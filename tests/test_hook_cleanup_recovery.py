"""Source-derived hook ownership tests; no game or SDK imports required.

Faults after a native hook mutation are simulated, not claimed to be observed
in BL2. The tests protect the bookkeeping contract if an SDK call throws.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mod" / "LootRecovery" / "__init__.py"
SELECTED = {"__init__", "_add_hook", "_sync_tick", "_set_state", "enable", "disable", "cancel", "queue_until_landing"}


class FakeHooks:
    def __init__(self):
        self.Type = NS(PRE="PRE", POST="POST", POST_UNCONDITIONAL="POST_UNCONDITIONAL")
        self.registered = {}
        self.add_calls = []
        self.remove_calls = []
        self.add_faults = {}
        self.remove_faults = {}

    def has_hook(self, *spec):
        return tuple(spec) in self.registered

    def add_hook(self, *args):
        spec, callback = tuple(args[:3]), args[3]
        self.add_calls.append(spec)
        mode = self.add_faults.pop(spec[2], None)
        if mode == "before":
            raise RuntimeError("registration failed before install")
        if spec in self.registered:
            return False
        self.registered[spec] = callback
        if mode == "after":
            raise RuntimeError("registration raised after install")
        return True

    def remove_hook(self, *spec):
        spec = tuple(spec)
        self.remove_calls.append(spec)
        mode = self.remove_faults.pop(spec[2], None)
        if mode == "before":
            raise RuntimeError("removal raised before uninstall")
        removed = self.registered.pop(spec, None) is not None
        if mode == "after":
            raise RuntimeError("removal raised after uninstall")
        return removed


def build():
    root = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
    cls = next(node for node in root.body if isinstance(node, ast.ClassDef) and node.name == "LootRecoveryRuntime")
    selected = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in SELECTED]
    assert len(selected) == len(SELECTED)
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Runtime", bases=[], keywords=[], body=selected, decorator_list=[]
    )], type_ignores=[]))
    hooks = FakeHooks()
    messages = []
    scope = {
        "unrealsdk": NS(hooks=hooks, unreal=NS(WeakPointer=lambda obj: lambda: obj)),
        "RecoveryState": __import__("enum").Enum("RecoveryState", ["IDLE", "WAITING_FOR_LANDING", "RECOVERING"]),
        "error": lambda msg: messages.append(("error", msg)),
        "log": lambda msg: messages.append(("info", msg)),
        "__version__": "1.0.0-beta.5",
        "time": NS(monotonic=lambda: 100.0),
        "LANDING_WAIT_TIMEOUT": 12.0,
        "addr": lambda obj: getattr(obj, "address", 0) if obj is not None else 0,
    }
    exec(compile(module, str(SOURCE), "exec"), scope)
    obj = scope["Runtime"]()
    obj.on_tick = lambda *a: None
    obj.on_travel = lambda *a: None
    obj.on_player_toss = lambda *a: None
    return obj, hooks, messages, scope["RecoveryState"]


class HookCleanupTests(unittest.TestCase):
    def test_normal_enable_and_disable_owns_four_hooks(self):
        rt, hooks, _, _ = build()
        rt.enable()
        self.assertEqual(len(hooks.registered), 4)
        self.assertEqual(len(rt.hooks), 4)
        rt.disable()
        self.assertEqual(len(hooks.registered), 0)
        self.assertEqual(rt.hooks, [])
        self.assertFalse(rt.enabled)

    def test_transient_tick_is_removed_when_gather_cancelled(self):
        rt, hooks, _, state = build()
        rt.enable()
        rt._set_state(state.RECOVERING)
        self.assertEqual(len(rt.hooks), 5)
        rt.cancel()
        self.assertEqual(len(rt.hooks), 4)
        self.assertFalse(any(x[2] == "loot_recovery_tick" for x in hooks.registered))

    def test_tick_removed_then_exception_does_not_leave_stale_ledger(self):
        rt, hooks, _, state = build()
        rt.enable()
        rt._set_state(state.RECOVERING)
        hooks.remove_faults["loot_recovery_tick"] = "after"
        rt.cancel()
        self.assertFalse(any(s[2] == "loot_recovery_tick" for s in hooks.registered))
        self.assertFalse(any(s[2] == "loot_recovery_tick" for s in rt.hooks))
        rt._set_state(state.RECOVERING)
        self.assertTrue(any(s[2] == "loot_recovery_tick" for s in hooks.registered))

    def test_disable_removed_then_exception_does_not_claim_stale_hooks(self):
        rt, hooks, messages, _ = build()
        rt.enable()
        hooks.remove_faults["loot_recovery_travel"] = "after"
        rt.disable()
        self.assertEqual(hooks.registered, {})
        self.assertEqual(rt.hooks, [])
        self.assertTrue(any(x[0] == "error" for x in messages))
        rt.enable()
        self.assertEqual(len(hooks.registered), 4)
        rt.disable()

    def test_genuine_tick_removal_failure_preserves_ledger_for_retry(self):
        rt, hooks, _, state = build()
        rt.enable()
        rt._set_state(state.RECOVERING)
        hooks.remove_faults["loot_recovery_tick"] = "before"
        rt.cancel()
        self.assertTrue(any(s[2] == "loot_recovery_tick" for s in rt.hooks))
        self.assertTrue(any(s[2] == "loot_recovery_tick" for s in hooks.registered))
        rt.disable()
        self.assertFalse(rt.hooks)
        self.assertFalse(hooks.registered)

    def test_genuine_disable_removal_failure_retains_only_owned_hook(self):
        rt, hooks, _, _ = build()
        rt.enable()
        hooks.remove_faults["loot_recovery_travel"] = "before"
        rt.disable()
        self.assertEqual(len(hooks.registered), 1)
        self.assertEqual([x[2] for x in rt.hooks], ["loot_recovery_travel"])
        rt.disable()
        self.assertEqual(rt.hooks, [])
        self.assertEqual(hooks.registered, {})

    def test_partial_registration_failure_is_cleaned(self):
        rt, hooks, _, _ = build()
        hooks.add_faults["loot_recovery_station_travel"] = "after"
        with self.assertRaisesRegex(RuntimeError, "registration raised after install"):
            rt.enable()
        self.assertFalse(rt.enabled)
        self.assertEqual(rt.hooks, [])
        self.assertEqual(hooks.registered, {})

    def test_foreign_registration_identifier_is_not_removed(self):
        rt, hooks, _, _ = build()
        spec = ("Engine.PlayerController:PreClientTravel", hooks.Type.PRE, "loot_recovery_travel")
        foreign = lambda *a: None
        hooks.registered[spec] = foreign
        with self.assertRaisesRegex(RuntimeError, "hook identifier already in use"):
            rt.enable()
        self.assertIs(hooks.registered[spec], foreign)
        self.assertFalse(rt.enabled)
        self.assertEqual(rt.hooks, [])

    def test_enable_twice_then_disable_is_idempotent(self):
        rt, hooks, _, _ = build()
        rt.enable()
        rt.enable()
        self.assertEqual(len(hooks.registered), 4)
        self.assertEqual(len(rt.hooks), 4)
        rt.disable()
        rt.disable()
        self.assertEqual(len(hooks.registered), 0)
        self.assertEqual(rt.hooks, [])

    def test_queue_for_landing_owns_tick_then_cancel_unhooks(self):
        rt, hooks, _, state = build()
        rt.enable()
        rt.queue_until_landing(NS(address=1), NS(address=2))
        self.assertEqual(rt._state, state.WAITING_FOR_LANDING)
        self.assertTrue(any(s[2] == "loot_recovery_tick" for s in hooks.registered))
        rt.cancel()
        self.assertEqual(rt._state, state.IDLE)
        self.assertEqual(len(hooks.registered), 4)


if __name__ == "__main__":
    unittest.main()
