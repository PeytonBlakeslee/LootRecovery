"""Source-derived LootRecovery eligibility safeguards (offline engine stand-ins)."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import importlib.util

ROOT = Path(__file__).resolve().parents[1] / "mod" / "LootRecovery"
SOURCE = ROOT / "__init__.py"
spec = importlib.util.spec_from_file_location("loot_rules_under_test", ROOT / "loot_rules.py")
loot_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loot_rules)


def make_runtime(*, tps=False, category="weapon", containers=False, own=False, minimum="Any", grinder=False):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LootRecoveryRuntime")
    selected = [node for node in original.body if isinstance(node, ast.FunctionDef)
                and node.name in {"eligible", "protected_scripted_source", "category_for"}]
    assert len(selected) == 3
    stub = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Runtime", bases=[], keywords=[], body=selected, decorator_list=[]
    )], type_ignores=[]))

    def addr(obj):
        return getattr(obj, "address", 0) if obj is not None else 0

    def clsname(obj):
        return getattr(getattr(obj, "Class", None), "Name", "").lower()

    def identity(obj):
        return getattr(obj, "path", "").lower()

    options = {name: NS(value=True) for name in ("ammo", "currency", "other", "weapon", "shield", "grenade", "classmod", "relic")}
    scope = dict(
        deleted=lambda obj: obj is None or bool(getattr(obj, "deleted", False)),
        addr=addr, clsname=clsname, identity=identity,
        CATEGORY_OPTIONS=options, GEAR=loot_rules.GEAR, RARITY_MIN=loot_rules.RARITY_MIN,
        Rarity=NS(value=minimum), SelfDropped=NS(value=own), Containers=NS(value=containers),
        IS_TPS=tps, get_pc=lambda: NS(bUsingGrinder=grinder),
        loot_rules=loot_rules,
        unrealsdk=NS(unreal=NS(WeakPointer=lambda obj: lambda: obj)),
    )
    exec(compile(stub, str(SOURCE), "exec"), scope)
    runtime = scope["Runtime"]()
    runtime.definition_cache = {}
    runtime.is_own_drop = lambda *args: False
    runtime.category_for = lambda definition: category
    return runtime, options


def pickup(*, category="weapon", attached=False, world=1, role=3, rarity=3,
           mission=False, inventory_class="WillowInventory", definition_class="WeaponTypeDefinition"):
    worldinfo = NS(address=world, NetMode=0)
    inventory = NS(Class=NS(Name=inventory_class), ItemLocation=1)
    definition = NS(Class=NS(Name=definition_class), path="gd_weapons.a_item.someweapon", address=90, bAutomaticallyPickup=False)
    result = NS(address=50, WorldInfo=worldinfo, Role=role, bDeleteMe=False,
                bIsMissionDirector=mission, bIsMissionItem=False,
                GetPickupableInventory=lambda: inventory,
                GetPickupableInventoryDefinition=lambda: definition,
                InventoryRarityLevel=rarity,
                bIsPickupAttachedToSomething=attached, bPickupAtRest=True,
                Base=None, Owner=None, Instigator=None, bCostsToPickUp=False)
    return result


class EligibilityTests(unittest.TestCase):
    def test_authority_and_world_prechecked(self):
        runtime, _ = make_runtime()
        self.assertFalse(runtime.eligible(pickup(world=2), 1, None))
        self.assertFalse(runtime.eligible(pickup(role=2), 1, None))

    def test_mission_pickups_excluded(self):
        runtime, _ = make_runtime()
        p = pickup(mission=True)
        self.assertFalse(runtime.eligible(p, 1, None))
        p.bIsMissionDirector = False
        p.bIsMissionItem = True
        self.assertFalse(runtime.eligible(p, 1, None))

    def test_mission_inventory_and_definition_excluded(self):
        runtime, _ = make_runtime()
        self.assertFalse(runtime.eligible(pickup(inventory_class="WillowMissionItem"), 1, None))
        self.assertFalse(runtime.eligible(pickup(definition_class="MissionItemDefinition"), 1, None))

    def test_missing_inventory_or_definition_excluded(self):
        runtime, _ = make_runtime()
        p = pickup()
        p.GetPickupableInventory = lambda: None
        self.assertFalse(runtime.eligible(p, 1, None))
        p = pickup()
        p.GetPickupableInventoryDefinition = lambda: None
        self.assertFalse(runtime.eligible(p, 1, None))

    def test_disabled_category_excluded(self):
        runtime, opts = make_runtime()
        opts["weapon"].value = False
        self.assertFalse(runtime.eligible(pickup(), 1, None))

    def test_gear_rarity_threshold_is_enforced(self):
        runtime, _ = make_runtime(minimum="Purple+")
        self.assertFalse(runtime.eligible(pickup(rarity=3), 1, None))
        self.assertTrue(runtime.eligible(pickup(rarity=4), 1, None))

    def test_attached_gear_requires_container_opt_in(self):
        runtime, _ = make_runtime(containers=False)
        self.assertFalse(runtime.eligible(pickup(attached=True), 1, None))
        runtime, _ = make_runtime(containers=True)
        self.assertTrue(runtime.eligible(pickup(attached=True), 1, None))

    def test_attached_pawn_handoff_excluded_with_container_opt_in(self):
        runtime, _ = make_runtime(containers=True)
        p = pickup(attached=True)
        p.Base = NS(address=12, Class=NS(Name="WillowAIPawn"), path="npc.hand_off")
        self.assertFalse(runtime.eligible(p, 1, None))

    def test_slot_machine_attachment_excluded_with_container_opt_in(self):
        runtime, _ = make_runtime(containers=True)
        p = pickup(attached=True)
        p.Base = NS(address=12, Class=NS(Name="InteractiveObject"), path="gamedata.slot_machine_0")
        self.assertFalse(runtime.eligible(p, 1, None))

    def test_attached_resource_not_gated_by_container_option(self):
        runtime, _ = make_runtime(category="ammo", containers=False)
        self.assertTrue(runtime.eligible(pickup(attached=True), 1, None))

    def test_self_drop_exclusion_applies_only_to_gear_when_enabled(self):
        runtime, _ = make_runtime(own=True)
        runtime.is_own_drop = lambda *args: True
        self.assertFalse(runtime.eligible(pickup(), 1, None))
        runtime, _ = make_runtime(category="ammo", own=True)
        runtime.is_own_drop = lambda *args: True
        self.assertTrue(runtime.eligible(pickup(), 1, None))

    def test_gear_remains_eligible_when_container_off_if_no_longer_attached(self):
        runtime, _ = make_runtime(containers=False)
        self.assertTrue(runtime.eligible(pickup(attached=False), 1, None))

    def test_tps_rejects_unsettled_and_attached_even_with_container_opt_in(self):
        runtime, _ = make_runtime(tps=True, containers=True)
        p = pickup()
        p.bPickupAtRest = False
        self.assertFalse(runtime.eligible(p, 1, None))
        p.bPickupAtRest = True
        p.bIsPickupAttachedToSomething = True
        self.assertFalse(runtime.eligible(p, 1, None))

    def test_tps_rejects_grinder_and_retained_owner(self):
        runtime, _ = make_runtime(tps=True, grinder=True)
        self.assertFalse(runtime.eligible(pickup(), 1, None))
        runtime, _ = make_runtime(tps=True)
        p = pickup()
        p.Owner = NS(address=22)
        self.assertFalse(runtime.eligible(p, 1, None))

    def test_echo_and_mission_definitions_classified_as_excluded(self):
        self.assertIsNone(loot_rules.classify_intrinsic("gd_echo.a_item.echorecorder", "UsableItemDefinition"))
        self.assertIsNone(loot_rules.classify_intrinsic("something.item", "MissionItemDefinition"))

    def test_definition_fallback_rechecks_mutable_autopickup(self):
        runtime, _ = make_runtime()
        runtime.category_for = type(runtime).category_for.__get__(runtime)
        definition = NS(address=100, Class=NS(Name="UnclassifiedDefinition"),
                        path="gd_custom.a_item.unknown", bAutomaticallyPickup=False)
        self.assertIsNone(runtime.category_for(definition))
        definition.bAutomaticallyPickup = True
        self.assertEqual(runtime.category_for(definition), "other")
        definition.bAutomaticallyPickup = False
        self.assertIsNone(runtime.category_for(definition))


if __name__ == "__main__":
    unittest.main()
