"""BUG-14: fault-inject the production attached-pickup relocation rollback.

Offline fake native methods follow the *exported* WillowPickup script for
AdjustPickupPhysicsAndCollisionForBeingAttached/Dropped. They cannot emulate
every native UE3 SetBase, rigid-body, collision-component or replication effect.
Tests intentionally do NOT claim full engine restoration.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "mod" / "LootRecovery" / "__init__.py"
TARGET = (400.0, 500.0, 80.0)


@dataclass(frozen=True)
class Vec:
    X: float
    Y: float
    Z: float


def xyz(value):
    return (float(value.X), float(value.Y), float(value.Z))


def make_runtime():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "LootRecoveryRuntime")
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == "relocate")
    test_cls = ast.ClassDef(name="RelocateRuntime", bases=[], keywords=[],
                            body=[fn], decorator_list=[])
    messages = []
    scope = {
        "xyz": xyz,
        "vec": lambda x, y, z: Vec(x, y, z),
        "log": lambda message: messages.append(("log", message)),
        "error": lambda message: messages.append(("error", message)),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[test_cls],
                   type_ignores=[])), str(SOURCE), "exec"), scope)
    runtime = scope["RelocateRuntime"]()
    runtime.position_index = 7
    runtime.fail_reasons = {}
    runtime.failure = lambda reason, detail=None: runtime.fail_reasons.__setitem__(
        reason, runtime.fail_reasons.get(reason, 0) + 1)
    runtime.find_position = lambda pawn, index, context=None: (TARGET, index + 1)
    return runtime, messages


class FakeAttachedPickup:
    """Observable engine surface, not an assertion of native setter atomicity."""
    def __init__(self, *, fault=None, timing=None):
        object.__setattr__(self, "fault", fault)
        object.__setattr__(self, "timing", timing)
        object.__setattr__(self, "fault_spent", False)
        object.__setattr__(self, "armed", False)
        self.Location = Vec(20, 30, 40)
        self.Base = SimpleNamespace(name="opened-container")
        self.BaseBoneName = "ContainerSlot"
        self.BaseSkelComponent = SimpleNamespace(name="container-mesh")
        self.RelativeLocation = Vec(3, 4, 5)
        self.RelativeRotation = (10, 20, 30)
        self.bIsPickupAttachedToSomething = True
        self.bPickupAtRest = True
        self.bForceNetUpdate = True
        self.bNetDirty = True
        self.bCollideWorld = False
        self.bUseRBPhysics = False
        self.Physics = 0
        self.Collision = (False, False)
        self.bPickupable = True
        self.ident = 0xAF12
        self.events = []
        object.__setattr__(self, "armed", True)

    def _fault(self, event, phase):
        if (not self.fault_spent and self.fault == event
                and self.timing == phase):
            self.fault_spent = True
            raise RuntimeError("injected " + event + " " + phase)

    def _perform(self, event, update):
        self.events.append(event)
        self._fault(event, "before")
        update()
        self._fault(event, "after")

    def __setattr__(self, name, value):
        if self.__dict__.get("armed", False):
            event = None
            if name == "Location" and xyz(value) == TARGET:
                event = "location"
            elif name == "bIsPickupAttachedToSomething" and value is False:
                event = "clear-attachment-flag"
            elif name == "bPickupAtRest" and value is False:
                event = "finalize-rest"
            if event is not None:
                self._perform(event, lambda: object.__setattr__(self, name, value))
                return
        object.__setattr__(self, name, value)

    def SetBase(self, new_base, *_args):
        event = "detach" if new_base is None else "restore-base"
        self._perform(event, lambda: setattr(self, "Base", new_base))

    def SetRelativeLocation(self, value):
        self._perform("restore-relative-location",
                      lambda: setattr(self, "RelativeLocation", value))

    def SetRelativeRotation(self, value):
        self._perform("restore-relative-rotation",
                      lambda: setattr(self, "RelativeRotation", value))

    def AdjustPickupPhysicsAndCollisionForBeingDropped(self):
        # From exported WillowGame.WillowPickup body, not UE3 native internals.
        def apply():
            self.Physics = 2
            self.Collision = (True, False)
            self.bCollideWorld = True
            self.bUseRBPhysics = True
        self._perform("drop-physics", apply)

    def InitializePickupForRBPhysics(self):
        self._perform("initialize-rb", lambda: None)

    def AdjustPickupPhysicsAndCollisionForBeingAttached(self):
        # Exported body sets the flag explicitly:
        #   bIsPickupAttachedToSomething = true;
        #   SetPhysics(0); SetCollision(false,false); bCollideWorld=false;
        #   PickupAtRest();
        # This helper does NOT explicitly clear bUseRBPhysics.
        def apply():
            self.bIsPickupAttachedToSomething = True
            self.Physics = 0
            self.Collision = (False, False)
            self.bCollideWorld = False
            self.bPickupAtRest = True
            self.bForceNetUpdate = True
            self.bNetDirty = True
        self._perform("restore-attached", apply)

    def ConvertRigidBodyToFixed(self):
        self._perform("fixed-body", lambda: None)

    def snapshot(self):
        return {
            "identity": self.ident,
            "location": self.Location,
            "base": self.Base,
            "bone": self.BaseBoneName,
            "skeletal": self.BaseSkelComponent,
            "relative": self.RelativeLocation,
            "rotation": self.RelativeRotation,
            "attached": self.bIsPickupAttachedToSomething,
            "rest": self.bPickupAtRest,
            "physics": self.Physics,
            "collision": self.Collision,
            "collide_world": self.bCollideWorld,
            "pickupable": self.bPickupable,
            # bUseRBPhysics intentionally recorded separately: its actual
            # native reset behavior is not established by exported script.
            "rb_requested": self.bUseRBPhysics,
        }


class AttachedRollbackTests(unittest.TestCase):
    def assert_script_visible_restored(self, pickup, before):
        after = pickup.snapshot()
        for field in ("identity", "location", "base", "bone", "skeletal",
                      "relative", "rotation", "attached", "rest", "physics",
                      "collision", "collide_world", "pickupable"):
            with self.subTest(property=field):
                self.assertEqual(after[field], before[field])

    def test_success_moves_same_pickup_using_existing_drop_physics(self):
        runtime, _ = make_runtime()
        item = FakeAttachedPickup()
        before = item.snapshot()
        self.assertTrue(runtime.relocate(item, object()))
        self.assertEqual(item.ident, before["identity"])
        self.assertEqual(xyz(item.Location), TARGET)
        self.assertIsNone(item.Base)
        self.assertFalse(item.bIsPickupAttachedToSomething)
        self.assertEqual(item.Physics, 2)
        self.assertEqual(runtime.position_index, 8)

    def test_failures_before_or_after_each_stage_restore_attached_script_fields(self):
        for fault in ("detach", "clear-attachment-flag", "location",
                      "drop-physics", "initialize-rb", "finalize-rest"):
            for timing in ("before", "after"):
                with self.subTest(fault=fault, timing=timing):
                    runtime, _ = make_runtime()
                    item = FakeAttachedPickup(fault=fault, timing=timing)
                    before = item.snapshot()
                    self.assertFalse(runtime.relocate(item, object()))
                    self.assert_script_visible_restored(item, before)
                    self.assertEqual(runtime.position_index, 7)
                    self.assertTrue(any(key.startswith("relocation-")
                                        for key in runtime.fail_reasons))
                    self.assertIn("restore-attached", item.events)

    def test_missing_attached_base_rejected_before_mutating_pickup(self):
        runtime, _ = make_runtime()
        item = FakeAttachedPickup()
        item.Base = None
        before = item.snapshot()
        self.assertFalse(runtime.relocate(item, object()))
        self.assertEqual(item.snapshot(), before)
        self.assertEqual(item.events, [])
        self.assertIn("unsafe-attachment", runtime.fail_reasons)

    def test_helper_reasserts_attachment_flag_without_extra_python_flag_write(self):
        runtime, _ = make_runtime()
        item = FakeAttachedPickup(fault="location", timing="after")
        before = item.snapshot()
        self.assertFalse(runtime.relocate(item, object()))
        self.assertTrue(item.bIsPickupAttachedToSomething)
        self.assertIs(item.Base, before["base"])
        self.assertIn("restore-attached", item.events)
        # A new direct bIsPickupAttachedToSomething=True assignment is not
        # required for this exported script path.

    def test_secondary_rb_property_cannot_be_certified_from_exported_script(self):
        runtime, _ = make_runtime()
        item = FakeAttachedPickup(fault="initialize-rb", timing="before")
        before = item.snapshot()
        self.assertFalse(runtime.relocate(item, object()))
        self.assert_script_visible_restored(item, before)
        # The exported attached helper never sets bUseRBPhysics=false.
        # The native engine might alter it indirectly; only BL2 can decide.
        self.assertFalse(before["rb_requested"])
        self.assertTrue(item.bUseRBPhysics)

    def test_interrupted_rollback_helper_is_not_a_verified_recovery(self):
        runtime, messages = make_runtime()
        item = FakeAttachedPickup(fault="restore-attached", timing="before")
        # Force relocation failure at its location verification stage, then
        # inject a second failure in the restoration helper.
        class FailingLocation(FakeAttachedPickup):
            def __setattr__(self, name, value):
                if (name == "Location" and self.__dict__.get("armed")
                        and xyz(value) == TARGET):
                    raise RuntimeError("destination failed")
                super().__setattr__(name, value)
        item = FailingLocation(fault="restore-attached", timing="before")
        self.assertFalse(runtime.relocate(item, object()))
        self.assertTrue(any(kind == "error" for kind, _ in messages))
        self.assertEqual(item.Base.name, "opened-container")
        # Demonstrates an incomplete *modeled* rollback, not a reproduced UE3
        # engine failure. This must remain a real-game acceptance case.
        self.assertFalse(item.bIsPickupAttachedToSomething)


if __name__ == "__main__":
    unittest.main()
