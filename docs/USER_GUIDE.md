# Loot Recovery: User Guide

[Back to the repository overview](../README.md)

**Contents:** [Install, update or rollback](#install-update-or-rollback) · [Using Gather](#using-gather) · [Settings and defaults](#settings-and-defaults) · [Safety and limitations](#safety-and-limitations) · [Compatibility](#compatibility) · [Troubleshooting](#troubleshooting)

## Install, update or rollback

1. Confirm [PythonSDK and Willow2 Mod Manager](https://bl-sdk.github.io/willow2-mod-db/) work in Windows Borderlands 2.
2. Exit the game. Back up `sdk_mods/LootRecovery/` and saved SDK settings, including `LootRecovery.json`.
3. Remove any old numbered or duplicate LootRecovery folder.
4. Extract a verified **Player ZIP** so `sdk_mods/LootRecovery/__init__.py` exists. Do not use GitHub's source ZIP or an unrelated `.sdkmod` file.
5. Launch the game and check your filters and **Gather All Loot** keybind.

To **roll back**, exit the game and restore the backed-up mod folder and settings. The mod retains existing `LootRecovery.json` preferences; do not replace that file with an empty one.

## Using Gather

Press **Gather All Loot** (default **Enter**) to move eligible, already-spawned pickups into a semicircle near your character. Pick them up normally afterward. The mod does not create items, auto-collect them, or open containers.

If you trigger Gather while airborne, it waits until you return to walking, up to 12 seconds. An ongoing gather can pause during a jump and resume upon landing. The operation is host-authoritative: joining co-op guests cannot initiate it.

## Settings and defaults

The SDK Mods menu groups options as follows:

| Group | Options | Defaults |
| --- | --- | --- |
| **Recovery** | Reset to Defaults; Show Teleport Feedback | Feedback Off |
| **Equipment** | Minimum Gear Rarity; Weapons, Shields, Grenade Mods, Class Mods, Relics/Oz Kits; Include Container Gear; Exclude My Dropped Gear | Rarity Any; equipment categories On; container gear Off; self-drop exclusion Off |
| **Consumables & Currency** | Ammo, Money and Currencies, Health and Consumables | All On |
| **Keybinds** | Gather All Loot | Enter |

**Include Container Gear** controls equipment from opened containers, not ammo, cash, currencies, or consumables. Those resource types follow their own category settings and can still be eligible after containers open. **Exclude My Dropped Gear** is opt-in; it does not exclude your own dropped equipment unless enabled. Reset to Defaults also restores the Enter keybind.

## Safety and limitations

- **Slot machines:** Moving certain spawned prizes can interrupt a machine's animation or leave it unable to close or work normally. Leave **Include Container Gear** Off, and avoid Gather while machines or scripted rewards are resolving. Save/Quit and reload may restore an affected interaction, but recovery is not guaranteed. Resource categories may still include opened-container pickups even with container gear Off.
- **Placement:** Loot is moved to bounded positions near the player, but obstacle clearance, ground support, and pickup reachability are **not comprehensively verified**. Avoid cliffs, voids, steep drops, and confined spaces. Use expendable items when testing unfamiliar locations.
- **Missions and NPCs:** Identifiable mission items, ECHO recordings, and certain attached NPC handoffs are excluded. Not every scripted reward exposes reliable provenance, so avoid Gather during mission handoffs and reward sequences.
- **Inventory:** Gather moves world pickups, not items from your inventory, and does not guarantee every eligible pickup can be reached or collected afterward.

## Compatibility

| Environment | Status |
| --- | --- |
| Windows Borderlands 2, solo or listen host | Primary Player target |
| Joining co-op guest | Cannot initiate host-authoritative Gather; replication to guests unverified |
| The Pre-Sequel | Experimental, not verified in-game |
| Windows game through Proton/Wine | Unverified |
| Native Linux Borderlands 2 | Unsupported by this Windows package |

**TPS:** Only recognized settled loose gear, resources, Oz Kits, and selected health/oxygen items are considered. Unknown, attached, owned, instigated, priced, and unsettled pickups are excluded. The Grinder-menu guard cannot identify every scripted reward once it becomes ownerless. Avoid Grinder, chest, and mission-reward sequences. Low-gravity placement and multiplayer behavior remain unverified.

## Troubleshooting

- **Nothing gathered:** Check Enabled, the keybind, solo/host authority, category filters, rarity, and whether items are settled and eligible. A map with no eligible loot produces no gather.
- **Loot placed poorly:** Stop gathering in that location. Record the map, terrain, item type, and destination; do not risk valuable gear in unsafe spaces.
- **Slot-machine interaction disrupted:** Stop using container recovery near the machine. A Save/Quit and reload may help, but is not guaranteed.
- **Missing mod or settings:** Check `sdk_mods/LootRecovery/__init__.py`, remove duplicate folders, and restore your backed-up `LootRecovery.json`.

[Report an issue](https://github.com/PeytonBlakeslee/LootRecovery/issues) with your game/SDK/mod versions, map, filters, solo/host/client role, exact steps, and a short relevant `unrealsdk.log` excerpt. Redact private paths, credentials, and save data.
