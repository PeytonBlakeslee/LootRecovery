"""Loot Recovery v1.0 Beta (update 4): manual map-wide recovery of BL2's ordinary spawned pickups.

This module does not collect, clone, destroy or create inventory. All native movement
operations run on the owning world (solo/listen host) and must be validated in-game.
"""
from __future__ import annotations

import math
from enum import Enum
from . import loot_rules
from .loot_rules import GEAR, RARITY_MIN
from dataclasses import dataclass
import time
import traceback

import unrealsdk
from unrealsdk import logging
from mods_base import (
    SETTINGS_DIR, BoolOption, ButtonOption, CoopSupport, EInputEvent,
    Game, GroupedOption, KeybindOption, KeybindType, SpinnerOption, build_mod, get_pc,
)

__version__ = "1.0.0-beta.5"
__author__ = "Peyton"

PREFIX = "[LootRecovery] "
IS_TPS = Game.get_current() == Game.TPS
# Conservative identity check: UsableItemDefinitions containing ECHO recording names
# should never enter recovery. Mission markers are checked separately.
BATCH_PER_TICK = 2
SLOTS_PER_ARC = 9
DISTANCE_FIRST = 155.0
DISTANCE_STEP = 62.0
# Pilot build: verify actor relocation + stock pickup physics before
# reintroducing any geometry rejection. The old 8 traces rejected every slot.
HEIGHT_ABOVE_PLAYER = 40.0
MAX_TEST_DISTANCE = 5500.0
RESET_COOLDOWN = 0.5
GATHER_COOLDOWN = 0.45
FAILED_GATHER_COOLDOWN = 1.5
LANDING_WAIT_TIMEOUT = 12.0
# Mid-gather BL2 interruptions are often brief jumps, but should not keep
# PlayerTick and the pending pickup snapshot alive indefinitely.
BL2_MID_GATHER_WAIT_TIMEOUT = 30.0




# Pure slot geometry; actor movement and authority checks remain in the runtime.
@dataclass(frozen=True, slots=True)
class PlacementContext:
    origin: tuple[float, float, float]
    forward: tuple[float, float]

    @classmethod
    def from_values(cls, origin, yaw):
        angle = float(yaw) * (math.pi / 32768.0)
        return cls(tuple(float(v) for v in origin), (math.cos(angle), math.sin(angle)))


def slot_position(context, index, slots_per_arc, first_distance, distance_step):
    layer, offset = divmod(index, slots_per_arc)
    column = 0 if offset == 0 else ((offset + 1) // 2) * (1 if offset % 2 else -1)
    angle = column * (math.pi / 9.0)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    fx, fy = context.forward
    radius = first_distance + layer * distance_step
    px, py, pz = context.origin
    return (px + (fx * cos_a - fy * sin_a) * radius,
            py + (fy * cos_a + fx * sin_a) * radius, pz)

def log(msg):
    logging.info(PREFIX + str(msg))


def error(msg):
    logging.error(PREFIX + str(msg) + "\n" + traceback.format_exc())


def addr(obj):
    if obj is None:
        return 0
    try:
        return int(obj._get_address())
    except Exception:
        return 0


def deleted(obj):
    if obj is None:
        return True
    try:
        return bool(obj.bDeleteMe)
    except Exception:
        return True


def clsname(obj):
    try:
        return str(obj.Class.Name).lower()
    except Exception:
        return ""


def identity(obj):
    try:
        return str(obj._path_name()).lower()
    except Exception:
        return ""


def xyz(vec):
    return (float(vec.X), float(vec.Y), float(vec.Z))


def vec(x, y, z):
    return unrealsdk.make_struct("Vector", X=float(x), Y=float(y), Z=float(z))


def safe_world(pc):
    try:
        world = pc.WorldInfo
        pawn = pc.Pawn
        if not addr(world) or not addr(pawn) or deleted(pawn):
            return None, None
        mode = world.NetMode
        if getattr(mode, "name", "") == "NM_Client" or int(mode) not in (0, 1, 2):
            return None, None
        return world, pawn
    except Exception:
        return None, None


# These options are intentionally compact, all category toggles default On.
Ammo = BoolOption("Ammo", True, description="Include ordinary spawned ammo drops.")
Currency = BoolOption("Money and Currencies", True, display_name="Money and Moonstones" if IS_TPS else "Money and Currencies", description="Include loose cash and Moonstones." if IS_TPS else "Include cash, Eridium and DLC currencies.")
Other = BoolOption("Health and Consumables", True, description="Include supported ordinary health and oxygen consumables." if IS_TPS else "Include supported ordinary consumables.")
Weapons = BoolOption("Weapons", True, description="Include eligible dropped weapons.")
Shields = BoolOption("Shields", True, description="Include eligible dropped shields.")
Grenades = BoolOption("Grenade Mods", True, description="Include eligible dropped grenade mods.")
ClassMods = BoolOption("Class Mods", True, description="Include eligible dropped class mods.")
Relics = BoolOption("Relics", True, display_name="Oz Kits" if IS_TPS else "Relics", description="Include eligible dropped Oz Kits." if IS_TPS else "Include eligible dropped relics.")
Rarity = SpinnerOption(
    "Minimum Gear Rarity", "Any", ["Any", "Green+", "Blue+", "Purple+", "Legendary+"],
    description="Applies to equipment only. Ammo, currencies and consumables are unaffected.",
)
Containers = BoolOption(
    "Include Spawned Container Loot", False,
    display_name="Include Container Gear",
    description=(
        "Include gear spawned from opened containers. Off by default; may interfere with slot machines."
    ),
)
SelfDropped = BoolOption(
    "Exclude My Dropped Gear", False,
    description="Ignore gear you drop from your inventory. In co-op, tracking operates while the mod is enabled.",
)
Feedback = BoolOption(
    "Show Teleport Feedback", False,
    description="Show an on-screen message after gathering loot.",
)
CATEGORY_OPTIONS = {
    "ammo": Ammo, "currency": Currency, "other": Other,
    "weapon": Weapons, "shield": Shields, "grenade": Grenades,
    "classmod": ClassMods, "relic": Relics,
}


class RecoveryState(Enum):
    IDLE = "idle"
    WAITING_FOR_LANDING = "waiting-for-landing"
    RECOVERING = "recovering"


class LootRecoveryRuntime:
    def __init__(self):
        self.enabled = False
        self._state = RecoveryState.IDLE
        self.pending = []
        self.origin_world = 0
        self.pc_ptr = None
        self.pawn_ptr = None
        self.cursor = 0
        self.position_index = 0
        self.moved = 0
        self.skipped = 0
        self.fail_reasons = {}
        self.hooks = []
        self.reset_at = 0.0
        self.next_gather_at = 0.0
        # Map-lifetime provenance for gear thrown by the local player. Unlike
        # Instigator, this survives the subsequent physics/attachment changes.
        self.local_drop_refs = {}
        self.landing_deadline = 0.0
        self.landing_pawn = None
        self.landing_world = 0
        self.definition_cache = {}
        self.operation_generation = 0

    @property
    def active(self):
        return self._state is RecoveryState.RECOVERING

    @property
    def waiting_for_landing(self):
        return self._state is RecoveryState.WAITING_FOR_LANDING

    def _add_hook(self, spec, callback):
        if spec in self.hooks:
            return
        if unrealsdk.hooks.has_hook(*spec):
            raise RuntimeError("hook identifier already in use: " + spec[2])
        try:
            unrealsdk.hooks.add_hook(*spec, callback)
        finally:
            # A registration may raise after installing its callback. Keep it
            # owned so the existing failure cleanup can remove or retry it.
            if unrealsdk.hooks.has_hook(*spec):
                self.hooks.append(spec)
        if spec not in self.hooks:
            raise RuntimeError("hook registration failed: " + spec[2])

    def _sync_tick(self):
        spec = ("WillowGame.WillowPlayerController:PlayerTick", unrealsdk.hooks.Type.PRE,
                "loot_recovery_tick")
        needed = self.enabled and self._state is not RecoveryState.IDLE
        if needed and spec not in self.hooks:
            self._add_hook(spec, self.on_tick)
        elif not needed and spec in self.hooks:
            unrealsdk.hooks.remove_hook(*spec)
            if unrealsdk.hooks.has_hook(*spec):
                raise RuntimeError("recovery tick removal failed")
            self.hooks.remove(spec)

    def _set_state(self, state):
        self._state = state
        self._sync_tick()

    def enable(self):
        self.enabled = True
        self.cancel()
        self.local_drop_refs.clear()
        try:
            for path, phase, hook_id, cb in (
                ("Engine.PlayerController:PreClientTravel", unrealsdk.hooks.Type.PRE,
                 "loot_recovery_travel", self.on_travel),
                ("WillowGame.WillowPlayerController:ServerTeleportPlayerToStation", unrealsdk.hooks.Type.PRE,
                 "loot_recovery_station_travel", self.on_travel),
                ("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", unrealsdk.hooks.Type.POST_UNCONDITIONAL,
                 "loot_recovery_loaded", self.on_travel),
                ("WillowGame.WillowPawn:TossSpecificItem", unrealsdk.hooks.Type.POST,
                 "loot_recovery_player_toss", self.on_player_toss),
            ):
                spec = (path, phase, hook_id)
                self._add_hook(spec, cb)
        except Exception:
            error("hook setup failed")
            self.disable()
            raise
        log("v1.0 Beta.5 enabled; deferred landing gather; NPC handoff protection; host-authoritative")

    def disable(self):
        self.enabled = False
        self.cancel()
        self.local_drop_refs.clear()
        remaining = []
        for path, phase, hook_id in reversed(self.hooks):
            try:
                unrealsdk.hooks.remove_hook(path, phase, hook_id)
                if unrealsdk.hooks.has_hook(path, phase, hook_id):
                    raise RuntimeError("hook remains registered")
            except Exception:
                remaining.append((path, phase, hook_id))
                error("removing hook " + hook_id)
        self.hooks[:] = reversed(remaining)
        log("disabled")

    def cancel(self):
        self.operation_generation += 1
        self.definition_cache.clear()
        self._state = RecoveryState.IDLE
        self.pending.clear()
        self.pc_ptr = None
        self.pawn_ptr = None
        self.origin_world = 0
        self.cursor = 0
        self.position_index = 0
        self.moved = 0
        self.skipped = 0
        self.fail_reasons = {}
        self.landing_deadline = 0.0
        self.landing_pawn = None
        self.landing_world = 0
        try:
            self._sync_tick()
        except Exception:
            # Keep failed-removal bookkeeping so disable can retry it.
            error("removing recovery tick")

    def failure(self, reason, detail=None):
        self.fail_reasons[reason] = self.fail_reasons.get(reason, 0) + 1

    def on_travel(self, pc, _args, _ret, _fn):
        try:
            local = get_pc()
            if addr(pc) == addr(local):
                self.local_drop_refs.clear()
                self.cancel()
        except Exception:
            self.local_drop_refs.clear()
            self.cancel()

    def on_player_toss(self, source_pawn, _args, result_pickup, _fn):
        """Record real player-thrown pickups at creation, before provenance is lost.

        Called *after* WillowPawn.TossSpecificItem. Does not affect the drop.
        The local pawn identity keeps another co-op player's loot eligible.
        """
        if not self.enabled or deleted(result_pickup) or not addr(result_pickup):
            return
        try:
            pc = get_pc()
            world, pawn = safe_world(pc)
            if world is None or addr(source_pawn) != addr(pawn):
                return
            self.local_drop_refs[addr(result_pickup)] = unrealsdk.unreal.WeakPointer(result_pickup)
            # Do not let a prolonged session grow this map indefinitely.
            if len(self.local_drop_refs) > 1024:
                self.local_drop_refs = {
                    key: ref for key, ref in self.local_drop_refs.items()
                    if ref() is not None and not deleted(ref()) and addr(ref()) == key
                }
        except Exception:
            error("tracking local drop")

    def is_own_drop(self, pickup, inventory, pawn):
        pickup_id = addr(pickup)
        ref = self.local_drop_refs.get(pickup_id)
        if ref is not None:
            live = ref()
            if live is not None and addr(live) == pickup_id and not deleted(live):
                return True
            self.local_drop_refs.pop(pickup_id, None)
        try:
            if int(inventory.ItemLocation) != 5:
                return False
            # In standalone, any player-thrown inventory is ours. On a listen
            # host, ItemLocation=5 alone cannot identify the owner; use the
            # recorded event above or an explicit, retained instigator only.
            if int(pickup.WorldInfo.NetMode) == 0:
                return True
            source = getattr(pickup, "Instigator", None)
            return source is not None and addr(source) == addr(pawn)
        except Exception:
            return False

    @staticmethod
    def show_feedback(pc, msg):
        """Use BL2's visible HUD notification, not console/chat ClientMessage."""
        try:
            pc.ShowInGameNotification_Internal(msg, 2.5)
            return
        except Exception:
            pass
        try:
            pc.DisplayGameMessage(0, 2.5, msg, "")
        except Exception:
            try:
                pc.ClientMessage(msg)
            except Exception:
                error("displaying feedback")


    @staticmethod
    def protected_scripted_source(pickup):
        """High-confidence exclusions only; no wholesale interactive-object ban.

        Attached NPC handoffs can own their reward actor; detaching it may
        break mission completion. Slot machines often expose only generic
        attachment actor names, so their pickups cannot reliably be excluded.
        """
        if not bool(pickup.bIsPickupAttachedToSomething):
            return False
        base = getattr(pickup, "Base", None)
        owner = getattr(pickup, "Owner", None)
        # Inspect the original parent first, then only one ownership hop.
        candidates = (base, owner, getattr(base, "Owner", None) if base else None)
        for actor in candidates:
            if not addr(actor):
                continue
            kind = clsname(actor)
            # An item still based on a pawn is being held/presented, not a
            # released world drop. Avoid interrupting NPC scripted handoffs.
            if kind.endswith("pawn") or kind.endswith("aipawn"):
                return True
            name = identity(actor)
            if any(token in name or token in kind for token in (
                "slotmachine", "slot_machine", "slot-machine", "slotmach"
            )):
                return True
        return False

    def queue_until_landing(self, world, pawn):
        """Do not place pickups at airborne coordinates / over map voids."""
        self.landing_deadline = time.monotonic() + LANDING_WAIT_TIMEOUT
        self.landing_world = addr(world)
        self.landing_pawn = unrealsdk.unreal.WeakPointer(pawn)
        self._set_state(RecoveryState.WAITING_FOR_LANDING)

    def category_for(self, definition):
        address = addr(definition)
        cached = self.definition_cache.get(address) if address else None
        if cached is not None and cached[0]() is not None:
            kind = cached[1]
        else:
            name = identity(definition)
            kind = loot_rules.classify_path(name)
            if IS_TPS and clsname(definition) == "usableitemdefinition" and name in loot_rules.TPS_CONSUMABLES:
                kind = "other"
            if kind is loot_rules.UNCLASSIFIED:
                kind = loot_rules.classify_intrinsic(name, clsname(definition))
            if address:
                self.definition_cache[address] = (unrealsdk.unreal.WeakPointer(definition), kind)
        if kind is not loot_rules.UNCLASSIFIED:
            return kind
        # bAutomaticallyPickup can be changed by another mod. Cache the hint,
        # never the current mutable fallback decision.
        try:
            return "other" if bool(definition.bAutomaticallyPickup) else None
        except Exception:
            return None

    def placement_context(self, pawn):
        return PlacementContext.from_values(xyz(pawn.Location), pawn.Rotation.Yaw)

    def eligible(self, pickup, world_id, pawn):
        if deleted(pickup) or addr(getattr(pickup, "WorldInfo", None)) != world_id:
            return False
        if int(pickup.Role) != 3:  # ENetRole.ROLE_Authority
            return False
        if bool(pickup.bIsMissionDirector) or bool(pickup.bIsMissionItem):
            return False
        inventory = pickup.GetPickupableInventory()
        definition = pickup.GetPickupableInventoryDefinition()
        if inventory is None or definition is None or clsname(inventory) == "willowmissionitem":
            return False
        if clsname(definition) == "missionitemdefinition":
            return False
        kind = self.category_for(definition)
        if kind is None or not bool(CATEGORY_OPTIONS[kind].value):
            return False
        if IS_TPS:
            # The UI flag is only an active-interaction guard, not proof of origin.
            # Continue to require settled loose actors without retained owners.
            try:
                pc = get_pc()
                if (pc is None or bool(pc.bUsingGrinder)
                        or bool(pickup.bIsPickupAttachedToSomething)
                        or not bool(pickup.bPickupAtRest) or pickup.Owner is not None
                        or pickup.Base is not None or pickup.Instigator is not None
                        or bool(pickup.bCostsToPickUp)):
                    return False
                if kind == "other" and (clsname(definition) != "usableitemdefinition"
                                        or identity(definition) not in loot_rules.TPS_CONSUMABLES):
                    return False
            except (AttributeError, RuntimeError, TypeError):
                return False
        if kind in GEAR:
            if int(pickup.InventoryRarityLevel) < RARITY_MIN[Rarity.value]:
                return False
            if SelfDropped.value and self.is_own_drop(pickup, inventory, pawn):
                return False
        # The container toggle only gates equipment. Money, ammo, currencies,
        # and health/consumables use their own category options even while
        # their pickups are attached to an opened container.
        if kind in GEAR and bool(pickup.bIsPickupAttachedToSomething):
            if not Containers.value:
                return False
            if self.protected_scripted_source(pickup):
                return False
        return True

    def begin(self):
        if not self.enabled or self.active or time.monotonic() < self.next_gather_at:
            return
        try:
            pc = get_pc()
            if pc is None:
                return
            world, pawn = safe_world(pc)
            if world is None:
                return
            if int(pawn.Physics) != 1:  # Do not scatter loot from a jump or void.
                self.queue_until_landing(world, pawn)
                return
            self._set_state(RecoveryState.IDLE)
            self.landing_deadline = 0.0
            self.landing_pawn = None
            self.landing_world = 0
            globals_obj = pc.GetWillowGlobals()
            if globals_obj is None:
                return
            world_id = addr(world)
            self.definition_cache.clear()
            pending = []
            attached_pending = []
            for pickup in tuple(globals_obj.PickupList):
                try:
                    if self.eligible(pickup, world_id, pawn):
                        weak = unrealsdk.unreal.WeakPointer(pickup)
                        # Ground pickups are tested before attached container loot.
                        # This makes the first relocation a normal, reversible item.
                        if bool(pickup.bIsPickupAttachedToSomething):
                            attached_pending.append(weak)
                        else:
                            pending.append(weak)
                except Exception:
                    error("scan skipped candidate")
            pending.extend(attached_pending)
            if not pending:
                self.next_gather_at = time.monotonic() + GATHER_COOLDOWN
                if Feedback.value:
                    self.show_feedback(pc, "LootRecovery: No eligible loot found")
                return
            self.pending = pending
            self.pc_ptr = unrealsdk.unreal.WeakPointer(pc)
            self.pawn_ptr = unrealsdk.unreal.WeakPointer(pawn)
            self.origin_world = world_id
            self.cursor = 0
            self.position_index = 0
            self.moved = 0
            self.skipped = 0
            self.fail_reasons = {}
            self.next_gather_at = time.monotonic() + GATHER_COOLDOWN
            self._set_state(RecoveryState.RECOVERING)
            log("gather queued: {} eligible pickups".format(len(pending)))
        except Exception:
            error("starting gather")
            self.cancel()

    def on_tick(self, pc, _args, _ret, _fn):
        if not self.enabled:
            return
        if self.waiting_for_landing and not self.active:
            try:
                local = get_pc()
                if local is None:
                    self.cancel()
                    return
                if addr(local) != addr(pc):
                    return
                world, pawn = safe_world(pc)
                saved_pawn = self.landing_pawn() if self.landing_pawn else None
                if (world is None or addr(world) != self.landing_world
                        or addr(pawn) != addr(saved_pawn)
                        or time.monotonic() >= self.landing_deadline):
                    self.cancel()
                    return
                if int(pawn.Physics) == 1:
                    self.begin()
            except Exception:
                self.cancel()
                error("checking landing")
            return
        if not self.active:
            return
        try:
            live_pc = self.pc_ptr() if self.pc_ptr is not None else None
            pawn = self.pawn_ptr() if self.pawn_ptr is not None else None
            if live_pc is None or pawn is None:
                self.cancel()
                return
            if addr(pc) != addr(live_pc):
                return
            if deleted(pawn):
                self.cancel()
                return
            world, current_pawn = safe_world(live_pc)
            if world is None or addr(world) != self.origin_world or addr(current_pawn) != addr(pawn):
                self.cancel()
                return
            # Brief nonwalking physics can be a normal jump or skill transition.
            # Preserve moved actors while paused, but do not keep PlayerTick and
            # an old world's pending snapshot alive forever in BL2.
            if int(pawn.Physics) != 1:
                if not self.landing_deadline:
                    timeout = (LANDING_WAIT_TIMEOUT if IS_TPS
                               else BL2_MID_GATHER_WAIT_TIMEOUT)
                    self.landing_deadline = time.monotonic() + timeout
                elif time.monotonic() >= self.landing_deadline:
                    moved, remaining = self.moved, len(self.pending) - self.cursor
                    log("gather cancelled after prolonged nonwalking physics: "
                        "{} already moved, {} still pending; press Gather again "
                        "after returning to walking".format(moved, remaining))
                    # Cancel only our queued operation. Previously moved pickups
                    # retain their engine-owned location/physics without rollback.
                    self.cancel()
                return
            # Returning to walking resets the pause deadline even in BL2.
            self.landing_deadline = 0.0
            context = self.placement_context(pawn)
            generation = self.operation_generation
            for _ in range(BATCH_PER_TICK):
                if not self.active or generation != self.operation_generation:
                    return
                if self.cursor >= len(self.pending):
                    self.finish(live_pc)
                    return
                weak = self.pending[self.cursor]
                self.cursor += 1
                pickup = weak()
                if pickup is None:
                    self.skipped += 1
                    continue
                try:
                    eligible = self.eligible(pickup, self.origin_world, pawn)
                    if not self.active or generation != self.operation_generation:
                        return
                    if not eligible:
                        self.skipped += 1
                    else:
                        relocated = self.relocate(pickup, pawn, context)
                        if not self.active or generation != self.operation_generation:
                            return
                        if relocated:
                            self.moved += 1
                        else:
                            self.skipped += 1
                            # A failed relocation can represent a caught native
                            # error and best-effort rollback. Do not move more.
                            self.skipped += len(self.pending) - self.cursor
                            self.cursor = len(self.pending)
                            log("pickup relocation failed; stopped gather before touching remaining pickups")
                except Exception:
                    error("moving pickup")
                    if not self.active or generation != self.operation_generation:
                        return
                    self.skipped += 1
                    self.skipped += len(self.pending) - self.cursor
                    self.cursor = len(self.pending)
                    log("pickup error; stopped gather before touching remaining pickups")
            if self.cursor >= len(self.pending):
                self.finish(live_pc)
        except Exception:
            error("gather service")
            self.cancel()

    def finish(self, pc):
        count, skipped = self.moved, self.skipped
        if Feedback.value:
            self.show_feedback(pc, "LootRecovery: {} gathered, {} skipped".format(count, skipped))
        failures = ", ".join("{}={}".format(k, v) for k, v in sorted(self.fail_reasons.items()))
        if count == 0 and skipped:
            self.next_gather_at = max(self.next_gather_at, time.monotonic() + FAILED_GATHER_COOLDOWN)
        log("gather completed: {} moved, {} not moved{}".format(
            count, skipped, " (reasons: {})".format(failures) if failures else ""))
        self.cancel()

    def slot(self, pawn, index, context=None):
        if context is None:
            context = self.placement_context(pawn)
        return slot_position(context, index, SLOTS_PER_ARC, DISTANCE_FIRST, DISTANCE_STEP)

    def find_position(self, pawn, start_index, context=None):
        """Use the working stock-physics placement path on stable ground."""
        try:
            # Keep the established placement guard; on_tick defers when falling.
            if int(pawn.Physics) != 1:  # PHYS_Walking
                self.failure("player-not-walking")
                return None, start_index
            if context is None:
                context = self.placement_context(pawn)
            sx, sy, sz = self.slot(pawn, start_index, context)
            px, py, pz = context.origin
            if not all(map(math.isfinite, (sx, sy, sz, px, py, pz))):
                self.failure("invalid-player-position")
                return None, start_index
            if (sx - px) ** 2 + (sy - py) ** 2 > MAX_TEST_DISTANCE ** 2:
                self.failure("placement-outside-test-limit")
                return None, start_index
            target = (sx, sy, pz + HEIGHT_ABOVE_PLAYER)
            return target, start_index + 1
        except Exception as exc:
            self.failure("placement-error", "{}: {}".format(type(exc).__name__, exc))
            return None, start_index

    def relocate(self, pickup, pawn, context=None):
        target, next_index = self.find_position(pawn, self.position_index, context)
        if target is None:
            return False
        original = xyz(pickup.Location)
        old_base = getattr(pickup, "Base", None)
        attached = bool(pickup.bIsPickupAttachedToSomething)
        was_at_rest = bool(pickup.bPickupAtRest)
        if attached and old_base is None:
            self.failure("unsafe-attachment", "attached pickup has no restorable Base")
            return False
        old_bone = getattr(pickup, "BaseBoneName", None) if attached else None
        old_skel = getattr(pickup, "BaseSkelComponent", None) if attached else None
        old_relative = getattr(pickup, "RelativeLocation", None) if attached else None
        old_relative_rotation = getattr(pickup, "RelativeRotation", None) if attached else None
        # A new actor or inventory is NEVER created. Stage information is kept
        # for precise diagnosis and a best-effort rollback of the same pickup.
        stage = "start"
        try:
            if attached:
                stage = "detach"
                pickup.SetBase(None)
                pickup.bIsPickupAttachedToSomething = False

            # Older working BL2 implementations assign actor.Location directly.
            # Only after that do they ask the game to rebuild dropped physics.
            # Using modern Vector values avoids legacy tuple conversion guesses.
            stage = "write-actor-location"
            pickup.Location = vec(*target)
            observed = xyz(pickup.Location)
            if sum((observed[i] - target[i]) ** 2 for i in range(3)) > 5.0 ** 2:
                raise RuntimeError("actor Location property did not retain destination")

            stage = "drop-physics"
            pickup.AdjustPickupPhysicsAndCollisionForBeingDropped()
            stage = "initialize-rigid-body"
            pickup.InitializePickupForRBPhysics()

            # The pickup is not at rest at the new position. Replicate the
            # modified actor state from the solo/listen host.
            stage = "finalize"
            pickup.bPickupAtRest = False
            pickup.bForceNetUpdate = True
            pickup.bNetDirty = True
            actual = xyz(pickup.Location)
            if sum((actual[i] - target[i]) ** 2 for i in range(3)) > 24.0 ** 2:
                raise RuntimeError("actor Location changed during physics initialization")
            self.position_index = next_index
            return True
        except Exception as exc:
            self.failure("relocation-" + stage, "{}: {}".format(type(exc).__name__, exc))
            log("movement failed at {}: {}: {}; original={} destination={}".format(
                stage, type(exc).__name__, exc, original, target))
            # Rollback modifies only the same pickup. Avoid mesh RB overrides:
            # the game normally owns that representation via InitializePickupForRBPhysics.
            try:
                pickup.Location = vec(*original)
                if attached and old_base is not None:
                    pickup.SetBase(old_base, None, old_skel, old_bone)
                    if old_relative is not None:
                        pickup.SetRelativeLocation(old_relative)
                    if old_relative_rotation is not None:
                        pickup.SetRelativeRotation(old_relative_rotation)
                    pickup.AdjustPickupPhysicsAndCollisionForBeingAttached()
                else:
                    pickup.AdjustPickupPhysicsAndCollisionForBeingDropped()
                    pickup.InitializePickupForRBPhysics()
                    if was_at_rest:
                        pickup.ConvertRigidBodyToFixed()
                pickup.bPickupAtRest = was_at_rest
            except Exception:
                error("restoring failed relocation")
            return False


runtime = LootRecoveryRuntime()
MOD = None


def on_reset(_option):
    now = time.monotonic()
    if now - runtime.reset_at < RESET_COOLDOWN:
        return
    runtime.reset_at = now
    for option in (Ammo, Currency, Other, Weapons, Shields, Grenades, ClassMods,
                   Relics):
        option.value = True
    Containers.value = False
    for option in (SelfDropped, Feedback):
        option.value = False
    Rarity.value = "Any"
    TELEPORT.key = "Enter"
    if MOD is not None:
        try:
            MOD.save_settings()
        except Exception:
            error("saving reset options")
    # Older Willow2 Mod Menus don't automatically redraw a pressed button's page.
    try:
        from willow2_mod_menu import options_menu
        refresh = getattr(options_menu, "dangerous_reload_current_page", None)
        if callable(refresh):
            refresh()
        elif callable(getattr(options_menu, "latest_list", None)):
            page = options_menu.latest_list()
            if page is not None:
                page.Refresh()
    except Exception:
        pass
    log("settings reset to defaults")


RESET = ButtonOption("Reset to Defaults", on_press=on_reset,
                     description="Restore all filters and the Gather All Loot shortcut to their defaults.")
TELEPORT = KeybindType(
    "Gather All Loot", "Enter", runtime.begin,
    display_name="Gather All Loot", event_filter=EInputEvent.IE_Released,
    description="Solo or host only: move eligible spawned loot toward you. Gathering waits until landing if needed.",
)
MOD = build_mod(
    name="Loot Recovery", author=__author__, version=__version__,
    description="Gather eligible spawned loot on demand (solo or host only). Container gear is optional and may affect slot machines.",
    supported_games=Game.BL2 | Game.TPS, coop_support=CoopSupport.HostOnly,
    settings_file=SETTINGS_DIR / "LootRecovery.json", auto_enable=True,
    keybinds=[TELEPORT],
    options=[RESET, Containers, SelfDropped, Feedback, Rarity,
             Weapons, Shields, Grenades, ClassMods, Relics, Ammo, Currency, Other],
    on_enable=runtime.enable, on_disable=runtime.disable,
)


def _iter_player_options():
    """Presentation-only groups; keep MOD.options and saved identifiers flat."""
    yield GroupedOption("Recovery", [RESET, Feedback])
    yield GroupedOption("Equipment", [Rarity, Weapons, Shields, Grenades, ClassMods, Relics,
                                      Containers, SelfDropped])
    yield GroupedOption("Consumables & Currency", [Ammo, Currency, Other])
    yield GroupedOption("Keybinds", [KeybindOption.from_keybind(TELEPORT)])


MOD.iter_display_options = _iter_player_options
