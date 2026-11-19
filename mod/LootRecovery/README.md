# Loot Recovery 1.0.0

Moves eligible, already-spawned loot from across the current Borderlands 2 map near your character. It does not create items, open containers, or automatically pick items up.

## Install or update

1. Exit the game. Back up `sdk_mods/LootRecovery/` and saved SDK settings such as `LootRecovery.json`.
2. Extract the matching **Player ZIP** into `Borderlands 2/sdk_mods/`, replacing the old folder and removing duplicate installations.
3. Confirm `sdk_mods/LootRecovery/__init__.py`, restart the game, and check your settings in the SDK Mods menu.

Restore the backed-up folder and settings to roll back.

## Usage and settings

Press **Gather All Loot** (default **Enter**) to bring eligible items into a semicircle nearby. Wait until you are grounded; airborne commands are deferred. The mod works on the solo player or co-op host, not a joining guest.

- **Recovery:** Reset to Defaults; optional on-screen feedback (Off).
- **Equipment:** All equipment categories On; minimum rarity Any. **Include Container Gear** and **Exclude My Dropped Gear** default to Off.
- **Consumables & Currency:** Ammo, money/currencies, and supported consumables On.

**Include Container Gear** controls equipment while it is still attached to its source, not all gear originally spawned by containers. Once detached, gear may be eligible even with the option Off. Ordinary resources use their own category filters regardless of attachment.

## Safety and compatibility

**Slot machines:** Gathering spawned prizes can disrupt active machines; Save/Quit may help but is not a guaranteed fix. Keep container gear Off and avoid Gather during payouts and mission handoffs.

**Placement:** Ground and obstacle clearance are not fully verified. Avoid cliffs, voids, and tight spaces when gathering valuable loot.

Windows BL2 solo/host is the primary Player target. TPS is experimental and untested in-game; co-op replication and Proton/Wine are unverified. Native Linux BL2 is unsupported.

For troubleshooting and full limitations, see the [Loot Recovery user guide](https://github.com/PeytonBlakeslee/LootRecovery/blob/main/docs/USER_GUIDE.md).
