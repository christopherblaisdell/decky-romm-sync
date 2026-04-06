"""Unit tests for PerfCollector and ETAEstimator."""

import time
from unittest.mock import patch

import pytest

from lib.perf import ETAEstimator, PerfCollector


# ═══════════════════════════════════════════════════════════════
# PerfCollector
# ═══════════════════════════════════════════════════════════════


class TestPerfCollectorLifecycle:
    """start_sync / end_sync / wall_time basics."""

    def test_wall_time_zero_before_sync(self):
        perf = PerfCollector()
        assert perf.wall_time == 0.0

    def test_wall_time_zero_after_start_without_end(self):
        perf = PerfCollector()
        perf.start_sync()
        assert perf.wall_time == 0.0

    def test_wall_time_positive_after_end(self):
        perf = PerfCollector()
        with patch("lib.perf.time.monotonic", side_effect=[100.0, 105.0]):
            perf.start_sync()
            perf.end_sync()
        assert perf.wall_time == pytest.approx(5.0)

    def test_start_sync_clears_prior_data(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.increment("x")
        perf.set_gauge("y", 42)
        perf.record_http_request("GET", "/a", 0.1, 200, 100)
        with perf.time_phase("p"):
            pass
        perf.end_sync()

        # Second sync should clear everything
        perf.start_sync()
        report = perf.generate_report()
        assert report["counters"] == {}
        assert report["gauges"] == {}
        assert report["http"]["total_requests"] == 0
        assert report["phases"] == {}


class TestPerfCollectorPhases:
    """time_phase context manager."""

    def test_phase_records_duration(self):
        perf = PerfCollector()
        perf.start_sync()
        with perf.time_phase("test_phase"):
            time.sleep(0.05)
        perf.end_sync()
        report = perf.generate_report()
        assert "test_phase" in report["phases"]
        assert report["phases"]["test_phase"] >= 0.03

    def test_phase_is_cumulative(self):
        perf = PerfCollector()
        perf.start_sync()
        with perf.time_phase("reentrant"):
            time.sleep(0.01)
        with perf.time_phase("reentrant"):
            time.sleep(0.01)
        perf.end_sync()
        report = perf.generate_report()
        # Both entries combined; use loose threshold for CI timing variance
        assert report["phases"]["reentrant"] >= 0.015

    def test_multiple_phases(self):
        perf = PerfCollector()
        perf.start_sync()
        with perf.time_phase("alpha"):
            pass
        with perf.time_phase("beta"):
            pass
        perf.end_sync()
        report = perf.generate_report()
        assert "alpha" in report["phases"]
        assert "beta" in report["phases"]

    def test_phase_records_even_on_exception(self):
        perf = PerfCollector()
        perf.start_sync()
        with pytest.raises(ValueError):
            with perf.time_phase("failing"):
                raise ValueError("boom")
        perf.end_sync()
        assert "failing" in perf.generate_report()["phases"]


class TestPerfCollectorHttp:
    """HTTP request tracking."""

    def test_record_single_request(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.record_http_request("GET", "/api/platforms", 0.5, 200, 4096)
        perf.end_sync()
        report = perf.generate_report()
        assert report["http"]["total_requests"] == 1
        assert report["http"]["total_bytes"] == 4096
        assert report["http"]["total_time"] == 0.5
        assert report["http"]["errors"] == 0

    def test_record_multiple_methods(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.record_http_request("GET", "/a", 0.1, 200, 100)
        perf.record_http_request("POST", "/b", 0.2, 201, 200)
        perf.record_http_request("GET", "/c", 0.3, 200, 300)
        perf.end_sync()
        report = perf.generate_report()
        assert report["http"]["total_requests"] == 3
        assert report["http"]["total_bytes"] == 600
        by_method = report["http"]["by_method"]
        assert by_method["GET"]["count"] == 2
        assert by_method["POST"]["count"] == 1

    def test_error_counting(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.record_http_request("GET", "/ok", 0.1, 200, 100)
        perf.record_http_request("GET", "/fail", 0.1, 500, 0)
        perf.record_http_request("GET", "/auth", 0.1, 401, 0)
        perf.record_http_request("GET", "/ok2", 0.1, 204, 50)
        perf.end_sync()
        report = perf.generate_report()
        # 500 and 401 are errors (non-2xx)
        assert report["http"]["errors"] == 2

    def test_zero_status_counted_as_error(self):
        """Status 0 (connection failure) should count as error."""
        perf = PerfCollector()
        perf.start_sync()
        perf.record_http_request("GET", "/fail", 0.1, 0, 0)
        perf.end_sync()
        assert perf.generate_report()["http"]["errors"] == 1


class TestPerfCollectorCounters:
    """Named counters."""

    def test_increment_default(self):
        perf = PerfCollector()
        perf.increment("roms_fetched")
        assert perf.get_counter("roms_fetched") == 1

    def test_increment_custom_amount(self):
        perf = PerfCollector()
        perf.increment("bytes", 1024)
        perf.increment("bytes", 2048)
        assert perf.get_counter("bytes") == 3072

    def test_get_counter_default(self):
        perf = PerfCollector()
        assert perf.get_counter("nonexistent") == 0


class TestPerfCollectorGauges:
    """Named gauges."""

    def test_set_and_get_gauge(self):
        perf = PerfCollector()
        perf.set_gauge("concurrency", 4.0)
        assert perf.get_gauge("concurrency") == 4.0

    def test_gauge_overwrites(self):
        perf = PerfCollector()
        perf.set_gauge("x", 1.0)
        perf.set_gauge("x", 99.0)
        assert perf.get_gauge("x") == 99.0

    def test_get_gauge_default(self):
        perf = PerfCollector()
        assert perf.get_gauge("nonexistent") == 0.0


class TestPerfCollectorReport:
    """generate_report() and format_report()."""

    def test_generate_report_structure(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.end_sync()
        report = perf.generate_report()
        assert "wall_time" in report
        assert "phases" in report
        assert "http" in report
        assert "counters" in report
        assert "gauges" in report
        assert "total_requests" in report["http"]
        assert "total_bytes" in report["http"]
        assert "errors" in report["http"]
        assert "by_method" in report["http"]

    def test_format_report_readable(self):
        perf = PerfCollector()
        perf.start_sync()
        with perf.time_phase("fetch"):
            pass
        perf.record_http_request("GET", "/api/roms", 0.5, 200, 10240)
        perf.increment("platforms_fetched", 3)
        perf.set_gauge("concurrency", 4)
        perf.end_sync()

        text = perf.format_report()
        assert "Sync completed in" in text
        assert "fetch:" in text
        assert "HTTP:" in text
        assert "GET:" in text
        assert "platforms_fetched: 3" in text
        assert "concurrency: 4" in text

    def test_format_report_empty_sync(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.end_sync()
        text = perf.format_report()
        assert "Sync completed in" in text
        # No phases, HTTP, counters, or gauges sections
        assert "Phases:" not in text
        assert "HTTP:" not in text

    def test_format_report_shows_http_errors(self):
        perf = PerfCollector()
        perf.start_sync()
        perf.record_http_request("GET", "/fail", 0.1, 500, 0)
        perf.end_sync()
        text = perf.format_report()
        assert "HTTP errors: 1" in text


# ═══════════════════════════════════════════════════════════════
# ETAEstimator
# ═══════════════════════════════════════════════════════════════


class TestETAEstimatorBasics:
    """Core ETA behaviour."""

    def test_eta_none_before_start(self):
        eta = ETAEstimator()
        assert eta.eta_seconds(0, 100) is None

    def test_eta_none_with_few_samples(self):
        eta = ETAEstimator(min_samples=3)
        eta.start()
        eta.update(10)
        assert eta.eta_seconds(10, 100) is None  # only 1 sample

    def test_eta_returns_value_after_min_samples(self):
        eta = ETAEstimator(alpha=0.5, min_samples=2)
        eta.start()
        # Simulate two updates with controlled timing
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0

            mock_time.return_value = 101.0
            eta.update(10)  # 10 items in 1s = 10/s

            mock_time.return_value = 102.0
            eta.update(20)  # 10 items in 1s = 10/s

        result = eta.eta_seconds(20, 100)
        assert result is not None
        assert result > 0

    def test_eta_none_when_complete(self):
        eta = ETAEstimator(min_samples=1)
        eta.start()
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0
            mock_time.return_value = 101.0
            eta.update(100)
        assert eta.eta_seconds(100, 100) is None

    def test_elapsed_zero_before_start(self):
        eta = ETAEstimator()
        assert eta.elapsed == 0.0

    def test_elapsed_positive_after_start(self):
        eta = ETAEstimator()
        eta.start()
        assert eta.elapsed >= 0

    def test_items_per_sec_zero_before_update(self):
        eta = ETAEstimator()
        eta.start()
        assert eta.items_per_sec == 0.0

    def test_samples_count(self):
        eta = ETAEstimator()
        eta.start()
        assert eta.samples == 0
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0
            mock_time.return_value = 101.0
            eta.update(10)
            mock_time.return_value = 102.0
            eta.update(20)
        assert eta.samples == 2


class TestETAEstimatorEdgeCases:
    """Edge cases and invariants."""

    def test_backward_update_ignored(self):
        eta = ETAEstimator(min_samples=1)
        eta.start()
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0
            mock_time.return_value = 101.0
            eta.update(50)
            mock_time.return_value = 102.0
            eta.update(30)  # backward — should be ignored
        assert eta.samples == 1

    def test_duplicate_update_ignored(self):
        eta = ETAEstimator(min_samples=1)
        eta.start()
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0
            mock_time.return_value = 101.0
            eta.update(50)
            mock_time.return_value = 102.0
            eta.update(50)  # same value — should be ignored
        assert eta.samples == 1

    def test_start_resets_state(self):
        eta = ETAEstimator(min_samples=1)
        eta.start()
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0
            mock_time.return_value = 101.0
            eta.update(50)
        assert eta.samples == 1

        eta.start()
        assert eta.samples == 0
        assert eta.items_per_sec == 0.0

    def test_ema_smoothing(self):
        """With alpha=1.0, the EMA should equal the latest rate exactly."""
        eta = ETAEstimator(alpha=1.0, min_samples=1)
        eta.start()
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0

            mock_time.return_value = 101.0
            eta.update(10)  # 10 items/sec

            mock_time.return_value = 102.0
            eta.update(30)  # 20 items in 1s = 20 items/sec

        # alpha=1.0 means newest rate dominates
        assert eta.items_per_sec == pytest.approx(20.0)

    def test_eta_calculation_accuracy(self):
        """With constant rate of 10 items/sec, ETA for 80 remaining should be ~8s."""
        eta = ETAEstimator(alpha=1.0, min_samples=1)
        eta.start()
        with patch("lib.perf.time.monotonic") as mock_time:
            mock_time.return_value = 100.0
            eta._start = 100.0
            eta._last_update = 100.0

            mock_time.return_value = 101.0
            eta.update(10)  # 10 items/sec

            mock_time.return_value = 102.0
            eta.update(20)  # 10 items/sec

        result = eta.eta_seconds(20, 100)
        assert result == pytest.approx(8.0)
