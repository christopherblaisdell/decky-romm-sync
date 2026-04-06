"""Performance instrumentation for sync lifecycle measurement.

Provides ``PerfCollector`` for phase timing, HTTP request tracking,
counters, and gauges — and ``ETAEstimator`` for throughput-based
remaining-time estimation.

Both classes are pure Python with no external dependencies.

Production usage
~~~~~~~~~~~~~~~~
Every sync automatically records perf data:
  - Formatted report logged to Decky logs
  - JSON written to ``<plugin_dir>/perf_report.json``
  - Available via ``get_perf_report()`` RPC

Ad-hoc baseline: ``python3 scripts/deck_perf_test.py``
See that script's docstring for the full test methodology and
representative platform selection rationale.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class _HttpRecord:
    """Single HTTP request measurement."""

    method: str
    path: str
    elapsed: float
    status: int
    nbytes: int


class PerfCollector:
    """Collects performance metrics for a single sync cycle.

    Usage::

        perf = PerfCollector()
        perf.start_sync()

        with perf.time_phase("fetch_platforms"):
            ...  # work

        perf.record_http_request("GET", "/api/platforms", 0.42, 200, 1234)
        perf.increment("platforms_fetched")
        perf.set_gauge("fetch_concurrency", 4)

        perf.end_sync()
        print(perf.format_report())
    """

    def __init__(self) -> None:
        self._sync_start: float = 0.0
        self._sync_end: float = 0.0
        self._phases: dict[str, float] = {}
        self._phase_start: dict[str, float] = {}
        self._http_requests: list[_HttpRecord] = []
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Sync lifecycle
    # ------------------------------------------------------------------

    def start_sync(self) -> None:
        """Begin a new sync cycle. Clears all prior data."""
        self._sync_start = time.monotonic()
        self._sync_end = 0.0
        self._phases.clear()
        self._phase_start.clear()
        self._http_requests.clear()
        self._counters.clear()
        self._gauges.clear()

    def end_sync(self) -> None:
        """Mark sync cycle as finished."""
        self._sync_end = time.monotonic()

    @property
    def wall_time(self) -> float:
        """Total wall-clock seconds for the sync (0.0 if not finished)."""
        if self._sync_end > 0 and self._sync_start > 0:
            return self._sync_end - self._sync_start
        return 0.0

    # ------------------------------------------------------------------
    # Phase timing
    # ------------------------------------------------------------------

    @contextmanager
    def time_phase(self, name: str):
        """Context manager that records the duration of a named phase.

        Phases are cumulative — entering the same phase twice adds to
        the previous total (useful for re-entrant phases).
        """
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - t0
            self._phases[name] = self._phases.get(name, 0.0) + elapsed

    # ------------------------------------------------------------------
    # HTTP request tracking
    # ------------------------------------------------------------------

    def record_http_request(
        self, method: str, path: str, elapsed: float, status: int, nbytes: int
    ) -> None:
        """Record a single HTTP request's measurements."""
        self._http_requests.append(
            _HttpRecord(method=method, path=path, elapsed=elapsed, status=status, nbytes=nbytes)
        )

    # ------------------------------------------------------------------
    # Counters & gauges
    # ------------------------------------------------------------------

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a named counter."""
        self._counters[name] = self._counters.get(name, 0) + amount

    def get_counter(self, name: str) -> int:
        """Return current value of a counter (0 if not set)."""
        return self._counters.get(name, 0)

    def set_gauge(self, name: str, value: float) -> None:
        """Set a named gauge to a point-in-time value."""
        self._gauges[name] = value

    def get_gauge(self, name: str) -> float:
        """Return current value of a gauge (0.0 if not set)."""
        return self._gauges.get(name, 0.0)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_report(self) -> dict:
        """Return structured performance data as a dict."""
        total_http = len(self._http_requests)
        total_bytes = sum(r.nbytes for r in self._http_requests)
        total_http_time = sum(r.elapsed for r in self._http_requests)

        # Per-method breakdown
        methods: dict[str, dict] = {}
        for r in self._http_requests:
            m = methods.setdefault(r.method, {"count": 0, "total_time": 0.0, "total_bytes": 0})
            m["count"] += 1
            m["total_time"] += r.elapsed
            m["total_bytes"] += r.nbytes

        # Error count (non-2xx)
        errors = sum(1 for r in self._http_requests if r.status < 200 or r.status >= 300)

        return {
            "wall_time": round(self.wall_time, 3),
            "phases": {k: round(v, 3) for k, v in self._phases.items()},
            "http": {
                "total_requests": total_http,
                "total_bytes": total_bytes,
                "total_time": round(total_http_time, 3),
                "errors": errors,
                "by_method": methods,
            },
            "counters": dict(self._counters),
            "gauges": {k: round(v, 3) for k, v in self._gauges.items()},
        }

    def format_report(self) -> str:
        """Return a human-readable performance summary."""
        data = self.generate_report()
        lines: list[str] = [f"Sync completed in {data['wall_time']:.1f}s"]
        self._format_phases(data, lines)
        self._format_http(data["http"], lines)
        self._format_map(data["counters"], "Counters", lines)
        self._format_map(data["gauges"], "Gauges", lines)
        return "\n".join(lines)

    @staticmethod
    def _format_phases(data: dict, lines: list[str]) -> None:
        if not data["phases"]:
            return
        lines.append("  Phases:")
        wall = data["wall_time"]
        for name, secs in data["phases"].items():
            pct = (secs / wall * 100) if wall > 0 else 0
            lines.append(f"    {name}: {secs:.1f}s ({pct:.0f}%)")

    @staticmethod
    def _format_http(h: dict, lines: list[str]) -> None:
        if h["total_requests"] == 0:
            return
        mb = h["total_bytes"] / (1024 * 1024)
        lines.append(
            f"  HTTP: {h['total_requests']} requests, "
            f"{mb:.1f} MB, {h['total_time']:.1f}s cumulative"
        )
        if h["errors"] > 0:
            lines.append(f"  HTTP errors: {h['errors']}")
        for method, stats in h["by_method"].items():
            lines.append(f"    {method}: {stats['count']} reqs, {stats['total_time']:.1f}s")

    @staticmethod
    def _format_map(mapping: dict, label: str, lines: list[str]) -> None:
        if not mapping:
            return
        lines.append(f"  {label}:")
        for name, val in mapping.items():
            lines.append(f"    {name}: {val}")


class ETAEstimator:
    """Throughput-based ETA estimator using exponential moving average.

    Parameters
    ----------
    alpha:
        Smoothing factor (0–1). Higher = more weight on recent samples.
        Default 0.3 balances responsiveness with stability.
    min_samples:
        Minimum number of ``update()`` calls before ``eta_seconds()``
        returns a value (avoids wild early estimates).
    """

    def __init__(self, alpha: float = 0.3, min_samples: int = 3) -> None:
        self._alpha = alpha
        self._min_samples = min_samples
        self._start: float = 0.0
        self._samples: int = 0
        self._ema_rate: float = 0.0  # items per second (smoothed)
        self._last_update: float = 0.0
        self._last_current: int = 0

    def start(self) -> None:
        """Reset and begin a new estimation cycle."""
        self._start = time.monotonic()
        self._samples = 0
        self._ema_rate = 0.0
        self._last_update = self._start
        self._last_current = 0

    def update(self, current: int) -> None:
        """Record progress — *current* is the cumulative count of items processed."""
        now = time.monotonic()
        dt = now - self._last_update
        if dt <= 0 or current <= self._last_current:
            return  # skip duplicate or backward updates

        dx = current - self._last_current
        rate = dx / dt

        if self._samples == 0:
            self._ema_rate = rate
        else:
            self._ema_rate = self._alpha * rate + (1 - self._alpha) * self._ema_rate

        self._last_update = now
        self._last_current = current
        self._samples += 1

    def eta_seconds(self, current: int, total: int) -> float | None:
        """Estimated seconds remaining, or ``None`` if too few samples."""
        if self._samples < self._min_samples or self._ema_rate <= 0 or current >= total:
            return None
        remaining = total - current
        return remaining / self._ema_rate

    @property
    def elapsed(self) -> float:
        """Seconds since ``start()`` was called."""
        if self._start <= 0:
            return 0.0
        return time.monotonic() - self._start

    @property
    def items_per_sec(self) -> float:
        """Current smoothed throughput (items/sec). 0.0 if no samples yet."""
        return self._ema_rate if self._samples > 0 else 0.0

    @property
    def samples(self) -> int:
        """Number of update() calls recorded."""
        return self._samples
