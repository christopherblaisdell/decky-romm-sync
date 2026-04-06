/**
 * Steam collection management for RomM platforms.
 * Uses Steam's internal collectionStore API.
 *
 * Collection names are machine-scoped to prevent cross-device conflicts
 * when Steam Cloud syncs collections: "RomM: Platform (hostname)"
 */

import { logInfo, logWarn, logError } from "../api/backend";

/**
 * Steam system collection IDs that must NEVER be deleted or drained.
 * These are built-in Steam Library collections (Favorites, Hidden, etc.).
 * Deleting them corrupts the user's Steam Library state.
 */
const SYSTEM_COLLECTION_IDS = new Set([
  "favorite",
  "hidden",
  "uncategorized",
  "recent-activity",
  "all",
  "local-games",
  "remote-play",
]);

/**
 * Returns true if the collection name matches the RomM naming convention.
 * RomM collections always start with "RomM: " — system collections never do.
 */
export function isRomMCollection(displayName: string): boolean {
  return displayName.startsWith("RomM: ");
}

/**
 * Returns true if a collection is safe to delete — i.e., it's a RomM collection
 * and NOT a Steam system collection.
 */
export function isCollectionSafeToDelete(collection: { id: string; displayName: string }): boolean {
  if (SYSTEM_COLLECTION_IDS.has(collection.id)) return false;
  if (!isRomMCollection(collection.displayName)) return false;
  return true;
}

let _hostname = "";

export async function getHostname(): Promise<string> {
  if (_hostname) return _hostname;
  try {
    const info = await SteamClient.System.GetSystemInfo();
    _hostname = info.sHostname || "unknown";
  } catch {
    _hostname = "unknown";
  }
  return _hostname;
}

function getOverviews(appIds: number[]): AppStoreOverview[] {
  const overviews: AppStoreOverview[] = [];
  for (const appId of appIds) {
    if (typeof appStore !== "undefined") {
      const overview = appStore.GetAppOverviewByAppID(appId);
      if (overview) {
        overviews.push(overview);
        continue;
      }
    }
    // Fallback: construct a minimal overview
    overviews.push({ appid: appId, display_name: "", strDisplayName: "" });
  }
  return overviews;
}

export async function createOrUpdateCollections(
  platformAppIds: Record<string, number[]>,
  onProgress?: (current: number, total: number, name: string) => void,
): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, skipping collections");
      return;
    }

    const hostname = await getHostname();
    logInfo(`Creating/updating collections for platforms: ${Object.keys(platformAppIds).join(", ")} (hostname: ${hostname})`);

    const entries = Object.entries(platformAppIds);
    let idx = 0;
    for (const [platformName, appIds] of entries) {
      idx++;
      onProgress?.(idx, entries.length, platformName);
      const collectionName = `RomM: ${platformName} (${hostname})`;
      const overviews = getOverviews(appIds);

      try {
        const existing = collectionStore.userCollections.find(
          (c) => c.displayName === collectionName
        );

        if (existing) {
          logInfo(`Updating collection "${collectionName}" with ${appIds.length} apps`);
          const existingApps = existing.allApps;
          if (existingApps.length > 0) {
            existing.AsDragDropCollection().RemoveApps(existingApps);
          }
          existing.AsDragDropCollection().AddApps(overviews);
          await existing.Save();
        } else {
          logInfo(`Creating collection "${collectionName}" with ${appIds.length} apps`);
          const collection = collectionStore.NewUnsavedCollection(collectionName, undefined, []);
          collection.AsDragDropCollection().AddApps(overviews);
          await collection.Save();
        }
        logInfo(`Successfully saved collection "${collectionName}"`);
      } catch (colErr) {
        logError(`Failed to save collection "${collectionName}": ${colErr}`);
      }
    }
  } catch (e) {
    logError(`Failed to update collections: ${e}`);
  }
}

export async function createOrUpdateRomMCollections(
  collectionAppIds: Record<string, number[]>,
  onProgress?: (current: number, total: number, name: string) => void,
): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, skipping RomM collections");
      return;
    }

    const hostname = await getHostname();
    logInfo(`Creating/updating RomM collections: ${Object.keys(collectionAppIds).join(", ")} (hostname: ${hostname})`);

    const entries = Object.entries(collectionAppIds);
    let idx = 0;
    for (const [collName, appIds] of entries) {
      idx++;
      onProgress?.(idx, entries.length, collName);
      const collectionName = `RomM: [${collName}] (${hostname})`;
      const overviews = getOverviews(appIds);

      try {
        const existing = collectionStore.userCollections.find(
          (c) => c.displayName === collectionName
        );

        if (existing) {
          logInfo(`Updating RomM collection "${collectionName}" with ${appIds.length} apps`);
          const existingApps = existing.allApps;
          if (existingApps.length > 0) {
            existing.AsDragDropCollection().RemoveApps(existingApps);
          }
          existing.AsDragDropCollection().AddApps(overviews);
          await existing.Save();
        } else {
          logInfo(`Creating RomM collection "${collectionName}" with ${appIds.length} apps`);
          const collection = collectionStore.NewUnsavedCollection(collectionName, undefined, []);
          collection.AsDragDropCollection().AddApps(overviews);
          await collection.Save();
        }
        logInfo(`Successfully saved RomM collection "${collectionName}"`);
      } catch (colErr) {
        logError(`Failed to save RomM collection "${collectionName}": ${colErr}`);
      }
    }
  } catch (e) {
    logError(`Failed to update RomM collections: ${e}`);
  }
}

export async function clearPlatformCollection(platformName: string): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, cannot clear platform collection");
      return;
    }
    const hostname = await getHostname();
    const scopedName = `RomM: ${platformName} (${hostname})`;
    const legacyName = `RomM: ${platformName}`;

    // Delete the machine-scoped collection
    const scoped = collectionStore.userCollections.find(
      (c) => c.displayName === scopedName
    );
    if (scoped) {
      if (isCollectionSafeToDelete(scoped)) {
        logInfo(`Deleting collection "${scopedName}" (id=${scoped.id})`);
        await scoped.Delete();
      } else {
        logWarn(`Refusing to delete collection "${scopedName}" (id=${scoped.id}) — system or non-RomM collection`);
      }
    }

    // Also clean up legacy collection (without hostname suffix) if it exists
    const legacy = collectionStore.userCollections.find(
      (c) => c.displayName === legacyName
    );
    if (legacy) {
      if (isCollectionSafeToDelete(legacy)) {
        logInfo(`Deleting legacy collection "${legacyName}" (id=${legacy.id})`);
        await legacy.Delete();
      } else {
        logWarn(`Refusing to delete collection "${legacyName}" (id=${legacy.id}) — system or non-RomM collection`);
      }
    }

    if (!scoped && !legacy) {
      logInfo(`Collection "${scopedName}" not found, nothing to clear`);
    }
  } catch (e) {
    logError(`Failed to clear platform collection: ${e}`);
  }
}

export async function clearAllRomMCollections(): Promise<void> {
  try {
    if (typeof collectionStore === "undefined") {
      logWarn("collectionStore not available, cannot clear collections");
      return;
    }
    const hostname = await getHostname();
    const suffix = ` (${hostname})`;

    // Match collections belonging to this machine OR legacy ones without any hostname suffix.
    // Covers both platform collections ("RomM: PlatformName (hostname)") and
    // RomM collection-based collections ("RomM: [CollectionName] (hostname)").
    // Legacy collections match "RomM: ..." but do NOT have a parenthesized suffix.
    // This avoids deleting collections from other devices like "RomM: N64 (othermachine)".
    const rommCollections = collectionStore.userCollections.filter((c) => {
      if (!c.displayName.startsWith("RomM: ")) return false;
      // This machine's scoped collections (both platform and RomM collection style)
      if (c.displayName.endsWith(suffix)) return true;
      // Legacy collections: start with "RomM: " but have no " (...)" suffix at all
      if (!/\s\([^)]+\)$/.test(c.displayName)) return true;
      return false;
    });

    logInfo(`Deleting ${rommCollections.length} RomM collections (hostname: ${hostname})`);
    for (const c of rommCollections) {
      if (!isCollectionSafeToDelete(c)) {
        logWarn(`Refusing to delete collection "${c.displayName}" (id=${c.id}) — system or non-RomM collection`);
        continue;
      }
      logInfo(`Deleting collection "${c.displayName}" (id=${c.id})`);
      await c.Delete();
    }
  } catch (e) {
    logError(`Failed to clear collections: ${e}`);
  }
}
