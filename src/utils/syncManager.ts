import { addEventListener } from "@decky/api";
import type { SyncApplyData, SyncApplyUnitData, SyncStaleData, SyncChangedItem } from "../types";
import { getArtworkBase64, reportSyncResults, reportUnitResults, syncHeartbeat, logInfo, logError } from "../api/backend";
import { getExistingRomMShortcuts, addShortcut, removeShortcut } from "./steamShortcuts";
import { updateSyncProgress } from "./syncProgress";

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Delay between shortcut operations. 50ms is safe for CEF; 20ms caused crashes with large batches. */
const SHORTCUT_DELAY_MS = 50;

let _cancelRequested = false;
let _isSyncRunning = false;

/** Request cancellation of the frontend shortcut processing loop. */
export function requestSyncCancel(): void {
  _cancelRequested = true;
}

// ── Per-Unit Pipeline ──────────────────────────────────────

/**
 * Process a single unit's shortcuts: create/update shortcuts, fetch artwork,
 * then report results back to the backend.
 */
async function processUnit(data: SyncApplyUnitData): Promise<void> {
  const { unit_type, unit_name, unit_index, total_units, shortcuts } = data;
  logInfo(`sync_apply_unit: ${unit_type} "${unit_name}" (${unit_index + 1}/${total_units}), ${shortcuts.length} shortcuts`);

  if (!Array.isArray(shortcuts) || shortcuts.length === 0) {
    logInfo(`sync_apply_unit: no shortcuts for "${unit_name}", reporting empty results`);
    try {
      await reportUnitResults({});
    } catch (e) {
      logError(`Failed to report empty unit results for "${unit_name}": ${e}`);
    }
    return;
  }

  _cancelRequested = false;
  let lastHeartbeat = Date.now();
  const HEARTBEAT_INTERVAL_MS = 10_000;

  const existing = await getExistingRomMShortcuts();
  const romIdToAppId: Record<string, number> = {};
  const artworkTargets: Array<{ appId: number; romId: number; name: string }> = [];

  updateSyncProgress({
    running: true, phase: "applying",
    current: 0, total: shortcuts.length,
    message: `${unit_name}: Applying shortcuts 0/${shortcuts.length}`,
  });

  for (let i = 0; i < shortcuts.length; i++) {
    const item = shortcuts[i];
    try {
      updateSyncProgress({
        current: i + 1,
        message: `${unit_name}: Applying shortcuts ${i + 1}/${shortcuts.length}`,
      });

      const existingAppId = existing.get(item.rom_id);
      let appId: number | undefined;

      if (existingAppId) {
        SteamClient.Apps.SetShortcutName(existingAppId, item.name);
        SteamClient.Apps.SetShortcutExe(existingAppId, item.exe);
        SteamClient.Apps.SetShortcutStartDir(existingAppId, item.start_dir);
        SteamClient.Apps.SetAppLaunchOptions(existingAppId, item.launch_options);
        appId = existingAppId;
      } else {
        appId = await addShortcut(item) ?? undefined;
      }

      if (appId) {
        romIdToAppId[String(item.rom_id)] = appId;
        artworkTargets.push({ appId, romId: item.rom_id, name: item.name });
      }
    } catch (e) {
      logError(`Failed to process shortcut for rom ${item.rom_id}: ${e}`);
    }
    await delay(SHORTCUT_DELAY_MS);

    if (Date.now() - lastHeartbeat > HEARTBEAT_INTERVAL_MS) {
      syncHeartbeat().catch(() => {});
      lastHeartbeat = Date.now();
    }

    if (_cancelRequested) {
      logInfo(`Cancel requested during "${unit_name}" after ${i + 1}/${shortcuts.length}`);
      break;
    }
  }

  // Batch artwork fetch (parallel, up to 8 at a time)
  if (!_cancelRequested && artworkTargets.length > 0) {
    const ART_CONCURRENCY = 8;
    for (let i = 0; i < artworkTargets.length; i += ART_CONCURRENCY) {
      if (_cancelRequested) break;
      const batch = artworkTargets.slice(i, i + ART_CONCURRENCY);
      await Promise.all(batch.map(async ({ appId, romId, name }) => {
        try {
          const artResult = await getArtworkBase64(romId);
          if (artResult.base64) {
            await SteamClient.Apps.SetCustomArtworkForApp(appId, artResult.base64, "png", 0);
          }
        } catch (artErr) {
          logError(`Failed to fetch/set artwork for ${name}: ${artErr}`);
        }
      }));
    }
  }

  // Report unit results to backend (triggers registry save + signals next unit)
  try {
    await reportUnitResults(romIdToAppId);
  } catch (e) {
    logError(`Failed to report unit results for "${unit_name}": ${e}`);
  }

  logInfo(`sync_apply_unit complete: "${unit_name}" — ${Object.keys(romIdToAppId).length} shortcuts`);
}

/**
 * Process stale ROM removals emitted after all units complete.
 */
async function processStale(data: SyncStaleData): Promise<void> {
  const { remove_rom_ids } = data;
  if (!Array.isArray(remove_rom_ids) || remove_rom_ids.length === 0) {
    try { await reportUnitResults({}); } catch { /* empty */ }
    return;
  }

  logInfo(`sync_stale: removing ${remove_rom_ids.length} stale shortcuts`);
  const existing = await getExistingRomMShortcuts();
  const removedMap: Record<string, number> = {};

  for (let i = 0; i < remove_rom_ids.length; i++) {
    const romId = remove_rom_ids[i];
    const appId = existing.get(romId);
    if (appId) {
      removeShortcut(appId);
    }
    removedMap[String(romId)] = 0; // signal removal with appId=0
    updateSyncProgress({
      current: i + 1,
      message: `Removing stale shortcuts ${i + 1}/${remove_rom_ids.length}`,
    });
    await delay(SHORTCUT_DELAY_MS);

    if (_cancelRequested) {
      logInfo("Cancel requested during stale removals");
      break;
    }
  }

  try {
    await reportUnitResults(removedMap);
  } catch (e) {
    logError(`Failed to report stale removal results: ${e}`);
  }

  logInfo(`sync_stale complete: ${Object.keys(removedMap).length} removed`);
}

/**
 * Initialize the per-unit sync manager.
 * Listens for sync_apply_unit and sync_stale events.
 * Also retains the legacy sync_apply handler for delta mode.
 * Returns cleanup handles.
 */
export function initSyncManager(): { unitListener: ReturnType<typeof addEventListener>; staleListener: ReturnType<typeof addEventListener>; legacyListener: ReturnType<typeof addEventListener> } {
  const unitListener = addEventListener("sync_apply_unit", async (data: SyncApplyUnitData) => {
    if (_isSyncRunning) {
      logInfo("sync_apply_unit: already running, queuing will be handled by backend wait");
    }
    _isSyncRunning = true;
    try {
      await processUnit(data);
    } finally {
      _isSyncRunning = false;
    }
  });

  const staleListener = addEventListener("sync_stale", async (data: SyncStaleData) => {
    _isSyncRunning = true;
    try {
      await processStale(data);
    } finally {
      _isSyncRunning = false;
    }
  });

  // Legacy sync_apply handler for delta preview/apply mode
  const legacyListener = addEventListener("sync_apply", async (data: SyncApplyData) => {
    if (_isSyncRunning) {
      logInfo("sync_apply: already running, ignoring duplicate event");
      return;
    }
    _isSyncRunning = true;
    try {
      await processLegacySyncApply(data);
    } finally {
      _isSyncRunning = false;
    }
  });

  return { unitListener, staleListener, legacyListener };
}

// ── Legacy sync_apply handler (delta preview/apply) ────────

async function processLegacySyncApply(data: SyncApplyData): Promise<void> {
  if (!Array.isArray(data.shortcuts)) {
    logError("sync_apply: data.shortcuts is not an array, aborting");
    return;
  }
  if (!Array.isArray(data.remove_rom_ids)) {
    logError("sync_apply: data.remove_rom_ids is not an array, aborting");
    return;
  }
  const isDelta = Array.isArray(data.changed_shortcuts);
  logInfo(`sync_apply received: ${data.shortcuts.length} new, ${isDelta ? data.changed_shortcuts!.length + " changed, " : ""}${data.remove_rom_ids.length} remove${isDelta ? " (delta)" : ""}`);

  _cancelRequested = false;
  let cancelled = false;
  let lastHeartbeat = Date.now();
  const HEARTBEAT_INTERVAL_MS = 10_000;

  const existing = await getExistingRomMShortcuts();
  const romIdToAppId: Record<string, number> = {};
  const removedRomIds: number[] = [];
  const artworkTargets: Array<{ appId: number; romId: number; name: string }> = [];

  let currentStep = data.next_step ?? 1;
  const totalSteps = data.total_steps ?? 3;

  const totalNew = data.shortcuts.length;
  const totalChanged = data.changed_shortcuts?.length ?? 0;
  const totalShortcuts = totalNew + totalChanged;

  if (totalShortcuts > 0) {
    updateSyncProgress({
      running: true, phase: "applying",
      current: 0, total: totalShortcuts,
      message: `Applying shortcuts 0/${totalShortcuts}`,
      step: currentStep, totalSteps,
    });

    for (let i = 0; i < data.shortcuts.length; i++) {
      const item = data.shortcuts[i];
      try {
        updateSyncProgress({
          current: i + 1,
          message: `Applying shortcuts ${i + 1}/${totalShortcuts}`,
        });
        let appId: number | undefined;

        if (isDelta) {
          const newAppId = await addShortcut(item);
          if (newAppId) {
            appId = newAppId;
            romIdToAppId[String(item.rom_id)] = newAppId;
          }
        } else {
          const existingAppId = existing.get(item.rom_id);
          if (existingAppId) {
            SteamClient.Apps.SetShortcutName(existingAppId, item.name);
            SteamClient.Apps.SetShortcutExe(existingAppId, item.exe);
            SteamClient.Apps.SetShortcutStartDir(existingAppId, item.start_dir);
            SteamClient.Apps.SetAppLaunchOptions(existingAppId, item.launch_options);
            appId = existingAppId;
            romIdToAppId[String(item.rom_id)] = existingAppId;
          } else {
            const newAppId = await addShortcut(item);
            if (newAppId) {
              appId = newAppId;
              romIdToAppId[String(item.rom_id)] = newAppId;
            }
          }
        }

        if (appId) {
          artworkTargets.push({ appId, romId: item.rom_id, name: item.name });
        }
      } catch (e) {
        logError(`Failed to process shortcut for rom ${item.rom_id}: ${e}`);
      }
      await delay(50);

      if (Date.now() - lastHeartbeat > HEARTBEAT_INTERVAL_MS) {
        syncHeartbeat().catch(() => {});
        lastHeartbeat = Date.now();
      }

      if (_cancelRequested) {
        logInfo(`Cancel requested after processing ${i + 1}/${totalShortcuts} shortcuts`);
        cancelled = true;
        break;
      }
    }

    if (!cancelled && isDelta && data.changed_shortcuts) {
      for (let i = 0; i < data.changed_shortcuts.length; i++) {
        const item: SyncChangedItem = data.changed_shortcuts[i];
        const idx = totalNew + i;
        try {
          updateSyncProgress({
            current: idx + 1,
            message: `Updating shortcuts ${idx + 1}/${totalShortcuts}`,
          });
          const appId = item.existing_app_id;

          SteamClient.Apps.SetShortcutName(appId, item.name);
          SteamClient.Apps.SetShortcutExe(appId, item.exe);
          SteamClient.Apps.SetShortcutStartDir(appId, item.start_dir);
          SteamClient.Apps.SetAppLaunchOptions(appId, item.launch_options);
          romIdToAppId[String(item.rom_id)] = appId;

          artworkTargets.push({ appId, romId: item.rom_id, name: item.name });
        } catch (e) {
          logError(`Failed to update shortcut for rom ${item.rom_id}: ${e}`);
        }
        await delay(50);

        if (Date.now() - lastHeartbeat > HEARTBEAT_INTERVAL_MS) {
          syncHeartbeat().catch(() => {});
          lastHeartbeat = Date.now();
        }

        if (_cancelRequested) {
          logInfo(`Cancel requested during changed shortcuts processing`);
          cancelled = true;
          break;
        }
      }
    }

    currentStep++;
  }

  // Batch artwork fetch
  if (!cancelled && artworkTargets.length > 0) {
    const ART_CONCURRENCY = 8;
    for (let i = 0; i < artworkTargets.length; i += ART_CONCURRENCY) {
      if (_cancelRequested) {
        cancelled = true;
        break;
      }
      const batch = artworkTargets.slice(i, i + ART_CONCURRENCY);
      await Promise.all(batch.map(async ({ appId, romId, name }) => {
        try {
          const artResult = await getArtworkBase64(romId);
          if (artResult.base64) {
            await SteamClient.Apps.SetCustomArtworkForApp(appId, artResult.base64, "png", 0);
            logInfo(`Set cover artwork for ${name} (appId=${appId})`);
          }
        } catch (artErr) {
          logError(`Failed to fetch/set artwork for ${name}: ${artErr}`);
        }
      }));
    }
  }

  // Remove shortcuts
  if (!cancelled && data.remove_rom_ids.length > 0) {
    const totalRemovals = data.remove_rom_ids.length;
    updateSyncProgress({
      phase: "applying", current: 0, total: totalRemovals,
      message: `Removing shortcuts 0/${totalRemovals}`,
      step: currentStep, totalSteps,
    });

    for (let i = 0; i < data.remove_rom_ids.length; i++) {
      const romId = data.remove_rom_ids[i];
      const appId = existing.get(romId);
      if (appId) {
        removeShortcut(appId);
      }
      removedRomIds.push(romId);
      updateSyncProgress({
        current: i + 1,
        message: `Removing shortcuts ${i + 1}/${totalRemovals}`,
      });
      await delay(50);

      if (_cancelRequested) {
        logInfo("Cancel requested during removals");
        cancelled = true;
        break;
      }
    }

    currentStep++;
  }

  try {
    await reportSyncResults(romIdToAppId, removedRomIds, cancelled);
  } catch (e) {
    logError(`Failed to report sync results: ${e}`);
  }

  const doneMsg = cancelled
    ? `Sync cancelled (${Object.keys(romIdToAppId).length} processed)`
    : "Sync complete";
  updateSyncProgress({ running: false, phase: "done", message: doneMsg });
  logInfo(`sync_apply ${cancelled ? "cancelled" : "complete"}: ${Object.keys(romIdToAppId).length} added/updated, ${removedRomIds.length} removed`);
}
