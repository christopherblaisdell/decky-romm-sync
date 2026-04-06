"""Tests for the per-unit sync pipeline (Feature 2).

Tests the work queue builder, per-unit sync helpers, and report_unit_results.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.sync_state import SyncState
from services.library import LibraryService


@pytest.fixture
def svc(tmp_path):
    """Create a minimal LibraryService for unit tests."""
    import decky

    state = {
        "shortcut_registry": {},
        "installed_roms": {},
        "last_sync": None,
        "sync_stats": {},
    }
    settings = {
        "enabled_platforms": {},
        "enabled_collections": {},
    }

    romm_api = MagicMock()
    steam_config = MagicMock()
    steam_config.grid_dir.return_value = str(tmp_path / "grid")

    svc = LibraryService(
        romm_api=romm_api,
        steam_config=steam_config,
        state=state,
        settings=settings,
        metadata_cache={},
        loop=asyncio.get_event_loop(),
        logger=decky.logger,
        plugin_dir=str(tmp_path),
        emit=decky.emit,
        save_state=MagicMock(),
        save_settings_to_disk=MagicMock(),
        log_debug=MagicMock(),
    )
    return svc


@pytest.fixture(autouse=True)
async def _set_loop(svc):
    svc._loop = asyncio.get_event_loop()


class TestBuildWorkQueue:
    @pytest.mark.asyncio
    async def test_platforms_only(self, svc):
        svc._romm_api.list_platforms.return_value = [
            {"id": 4, "name": "Dreamcast", "slug": "dreamcast", "rom_count": 362},
            {"id": 19, "name": "PlayStation", "slug": "psx", "rom_count": 1978},
        ]
        svc._settings["enabled_platforms"] = {"4": True, "19": True}

        work_queue, platforms, collections_meta = await svc._build_work_queue()

        assert len(work_queue) == 2
        assert work_queue[0]["type"] == "platform"
        assert work_queue[0]["name"] == "Dreamcast"
        assert work_queue[1]["name"] == "PlayStation"
        assert len(platforms) == 2
        assert len(collections_meta) == 0

    @pytest.mark.asyncio
    async def test_platforms_and_collections(self, svc):
        svc._romm_api.list_platforms.return_value = [
            {"id": 4, "name": "Dreamcast", "slug": "dreamcast", "rom_count": 362},
        ]
        svc._settings["enabled_platforms"] = {"4": True}
        svc._settings["enabled_collections"] = {"90": True}

        svc._romm_api.list_collections.return_value = [
            {"id": 90, "name": "Metroid", "rom_count": 11},
        ]
        svc._romm_api.list_virtual_collections.return_value = []

        work_queue, platforms, collections_meta = await svc._build_work_queue()

        assert len(work_queue) == 2
        assert work_queue[0]["type"] == "platform"
        assert work_queue[1]["type"] == "collection"
        assert work_queue[1]["name"] == "Metroid"
        assert len(collections_meta) == 1

    @pytest.mark.asyncio
    async def test_empty_queue(self, svc):
        svc._romm_api.list_platforms.return_value = []

        work_queue, platforms, collections_meta = await svc._build_work_queue()

        assert len(work_queue) == 0
        assert len(platforms) == 0

    @pytest.mark.asyncio
    async def test_disabled_platforms_excluded(self, svc):
        svc._romm_api.list_platforms.return_value = [
            {"id": 4, "name": "Dreamcast", "slug": "dreamcast", "rom_count": 362},
            {"id": 19, "name": "PlayStation", "slug": "psx", "rom_count": 1978},
        ]
        svc._settings["enabled_platforms"] = {"4": True, "19": False}

        work_queue, platforms, _ = await svc._build_work_queue()

        assert len(work_queue) == 1
        assert work_queue[0]["name"] == "Dreamcast"


class TestReportUnitResults:
    def test_updates_registry(self, svc, tmp_path):
        svc._pending_sync = {
            1: {"name": "Game A", "platform_name": "DC", "platform_slug": "dreamcast",
                "cover_path": "", "rom_id": 1, "fs_name": "game_a.zip"},
            2: {"name": "Game B", "platform_name": "DC", "platform_slug": "dreamcast",
                "cover_path": "", "rom_id": 2, "fs_name": "game_b.zip"},
        }
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None

        result = svc.report_unit_results({"1": 100001, "2": 100002})

        assert result["success"] is True
        assert "1" in svc._state["shortcut_registry"]
        assert svc._state["shortcut_registry"]["1"]["app_id"] == 100001
        assert "2" in svc._state["shortcut_registry"]
        assert svc._state["shortcut_registry"]["2"]["app_id"] == 100002

    def test_saves_state(self, svc, tmp_path):
        svc._pending_sync = {
            1: {"name": "Game A", "platform_name": "DC", "platform_slug": "dreamcast",
                "cover_path": "", "rom_id": 1, "fs_name": "game_a.zip"},
        }
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None

        svc.report_unit_results({"1": 100001})

        svc._save_state.assert_called_once()

    def test_signals_event(self, svc, tmp_path):
        svc._pending_sync = {}
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None

        svc.report_unit_results({"1": 100001})

        assert svc._unit_result_event.is_set()
        assert svc._unit_result_data == {"1": 100001}

    def test_accumulates_across_units(self, svc, tmp_path):
        """Calling report_unit_results multiple times accumulates registry entries."""
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None

        svc._pending_sync = {
            1: {"name": "Game A", "platform_name": "DC", "platform_slug": "dreamcast",
                "cover_path": "", "rom_id": 1, "fs_name": "a.zip"},
        }
        svc.report_unit_results({"1": 100001})

        # Reset event for next unit
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None

        svc._pending_sync[2] = {
            "name": "Game B", "platform_name": "SNES", "platform_slug": "snes",
            "cover_path": "", "rom_id": 2, "fs_name": "b.zip",
        }
        svc.report_unit_results({"2": 100002})

        assert "1" in svc._state["shortcut_registry"]
        assert "2" in svc._state["shortcut_registry"]
        assert svc._state["shortcut_registry"]["1"]["platform_name"] == "DC"
        assert svc._state["shortcut_registry"]["2"]["platform_name"] == "SNES"


class TestSyncOnePlatform:
    @pytest.mark.asyncio
    async def test_fetches_and_emits(self, svc):
        """_sync_one_platform fetches ROMs, builds shortcuts, and emits sync_apply_unit."""
        import decky

        synced_rom_ids = set()

        # Mock the full fetch to return 2 ROMs
        async def mock_full_fetch(pid, pname, pslug, roms_list, pi, total):
            roms_list.extend([
                {"id": 1, "name": "Sonic Adventure", "platform_name": "Dreamcast",
                 "platform_slug": "dreamcast"},
                {"id": 2, "name": "Jet Set Radio", "platform_name": "Dreamcast",
                 "platform_slug": "dreamcast"},
            ])

        svc._full_fetch_platform_roms = mock_full_fetch
        svc._try_incremental_skip = AsyncMock(return_value=False)
        svc._artwork = None

        unit = {
            "type": "platform",
            "id": 4,
            "name": "Dreamcast",
            "slug": "dreamcast",
            "rom_count": 2,
            "_platform": {"id": 4, "name": "Dreamcast", "slug": "dreamcast", "rom_count": 2},
        }

        unit_roms, shortcuts_data = await svc._sync_one_platform(unit, synced_rom_ids, 0, 1)

        assert len(unit_roms) == 2
        assert len(shortcuts_data) == 2
        assert {1, 2} == synced_rom_ids

        # Verify sync_apply_unit was emitted
        emit_calls = [c for c in decky.emit.call_args_list if c[0][0] == "sync_apply_unit"]
        assert len(emit_calls) >= 1
        payload = emit_calls[-1][0][1]
        assert payload["unit_type"] == "platform"
        assert payload["unit_name"] == "Dreamcast"
        assert len(payload["shortcuts"]) == 2


class TestSyncOneCollection:
    @pytest.mark.asyncio
    async def test_deduplicates_against_synced(self, svc):
        """Collection sync skips ROMs already in synced_rom_ids."""
        import decky

        synced_rom_ids = {1, 2}  # Already synced from platform

        # Collection has ROM 1 (already synced) and ROM 3 (new)
        async def mock_fetch_single(coll, all_seen, coll_only_roms):
            all_rom_ids = [1, 3]
            for rid in all_rom_ids:
                if rid not in all_seen:
                    all_seen.add(rid)
                    coll_only_roms.append({
                        "id": rid, "name": f"ROM {rid}",
                        "platform_name": "GBA", "platform_slug": "gba",
                    })
            return all_rom_ids

        svc._fetch_single_collection_roms = mock_fetch_single
        svc._artwork = None

        unit = {
            "type": "collection",
            "id": "90",
            "name": "Metroid",
            "rom_count": 2,
            "_collection": {"id": 90, "name": "Metroid"},
        }

        coll_roms, shortcuts_data, coll_name, coll_rom_ids = await svc._sync_one_collection(
            unit, synced_rom_ids, 0, 1,
        )

        # Only ROM 3 should be in collection_only_roms (ROM 1 was deduped)
        assert len(coll_roms) == 1
        assert coll_roms[0]["id"] == 3
        assert len(shortcuts_data) == 1
        assert coll_rom_ids == [1, 3]
        assert 3 in synced_rom_ids


class TestWaitForUnitResults:
    @pytest.mark.asyncio
    async def test_returns_data_when_signaled(self, svc):
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None

        # Signal from another task after a short delay
        async def signal():
            await asyncio.sleep(0.05)
            svc._unit_result_data = {"1": 100001}
            svc._unit_result_event.set()

        asyncio.get_event_loop().create_task(signal())

        result = await svc._wait_for_unit_results(timeout_sec=5)
        assert result == {"1": 100001}

    @pytest.mark.asyncio
    async def test_returns_none_on_cancellation(self, svc):
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None
        svc._sync_state = SyncState.CANCELLING

        result = await svc._wait_for_unit_results(timeout_sec=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, svc):
        svc._unit_result_event = asyncio.Event()
        svc._unit_result_data = None
        svc._sync_last_heartbeat = 0  # Way in the past

        result = await svc._wait_for_unit_results(timeout_sec=0)
        assert result is None
