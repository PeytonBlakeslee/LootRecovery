"""BUG-15: bounded mid-gather nonwalking pause, hook release, and retry.

Executes the *production* LootRecoveryRuntime.begin/on_tick/cancel state machine
with mocked SDK callbacks and clock. No actual BL2/TPS physics are simulated.
"""
import ast
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mod" / "LootRecovery" / "__init__.py"


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Actor:
    def __init__(self, address):
        self.address = address
        self.bDeleteMe = False

    def _get_address(self):
        return self.address


class Pickup(Actor):
    def __init__(self, address):
        super().__init__(address)
        self.bIsPickupAttachedToSomething = False
        self.relocated = False


class HookRegistry:
    def __init__(self):
        self.active = {}
        self.adds = 0
        self.removes = 0
        self.Type = NS(PRE="PRE")

    def has_hook(self, *spec):
        return tuple(spec) in self.active

    def add_hook(self, *args):
        spec, callback = tuple(args[:3]), args[3]
        self.active[spec] = callback
        self.adds += 1
        return True

    def remove_hook(self, *spec):
        self.active.pop(tuple(spec), None)
        self.removes += 1
        return True


def load_runtime(tps=False):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
    wanted = {"RecoveryState", "LootRecoveryRuntime"}
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef)
             and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    clock = Clock()
    hooks = HookRegistry()
    log_lines = []
    loot = [Pickup(200 + n) for n in range(5)]
    world = Actor(10)
    pawn = Actor(20)
    pawn.Physics = 1
    pc = Actor(30)
    pc.Pawn = pawn
    pc.WorldInfo = world
    pc.GetWillowGlobals = lambda: NS(PickupList=loot)
    local = {"pc": pc}
    move_calls = []
    env = {
        "Enum": Enum,
        "time": NS(monotonic=clock.monotonic),
        "unrealsdk": NS(hooks=hooks,
                       unreal=NS(WeakPointer=lambda obj: (lambda: obj))),
        "addr": lambda obj: obj._get_address() if obj is not None else 0,
        "deleted": lambda obj: obj is None or bool(getattr(obj, "bDeleteMe", True)),
        "safe_world": lambda current: (
            (current.WorldInfo, current.Pawn)
            if current is not None and current.WorldInfo is not None
            and current.Pawn is not None and not current.Pawn.bDeleteMe
            else (None, None)
        ),
        "get_pc": lambda: local["pc"],
        "IS_TPS": tps,
        "LANDING_WAIT_TIMEOUT": 12.0,
        "BL2_MID_GATHER_WAIT_TIMEOUT": 30.0,
        "GATHER_COOLDOWN": 0.45,
        "FAILED_GATHER_COOLDOWN": 1.5,
        "BATCH_PER_TICK": 2,
        "Feedback": NS(value=False),
        "log": lambda message: log_lines.append(str(message)),
        "error": lambda message: log_lines.append("ERROR " + str(message)),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(SOURCE), "exec"), env)
    runtime = env["LootRecoveryRuntime"]()
    runtime.enabled = True

    def relocate(pickup, _pawn, _context):
        pickup.relocated = True
        move_calls.append((clock.monotonic(), pickup.address))
        return True

    runtime.eligible = lambda pickup, *_: not pickup.relocated
    runtime.relocate = relocate
    runtime.placement_context = lambda *_: "fixed-context"
    return NS(runtime=runtime, pc=pc, pawn=pawn, world=world, clock=clock,
              hooks=hooks, loot=loot, log_lines=log_lines, move_calls=move_calls,
              env=env, local=local)


class NonwalkingGatherTests(unittest.TestCase):
    def begin_partial(self, *, tps=False):
        t = load_runtime(tps=tps)
        t.runtime.begin()
        self.assertTrue(t.runtime.active)
        self.assertEqual(len(t.runtime.pending), 5)
        self.assertEqual(len(t.hooks.active), 1)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertEqual(len(t.move_calls), 2)
        self.assertEqual(t.runtime.cursor, 2)
        self.assertEqual(t.runtime.moved, 2)
        return t

    def assert_cancelled(self, t):
        self.assertFalse(t.runtime.active)
        self.assertFalse(t.runtime.waiting_for_landing)
        self.assertEqual(t.runtime.pending, [])
        self.assertEqual(t.runtime.hooks, [])
        self.assertEqual(t.hooks.active, {})
        self.assertEqual(t.runtime.origin_world, 0)
        self.assertEqual(t.runtime.landing_deadline, 0.0)

    def test_bl2_indefinite_nonwalking_is_bounded_and_releases_tick_hook(self):
        t = self.begin_partial()
        gen = t.runtime.operation_generation
        t.pawn.Physics = 2
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertEqual(t.runtime.landing_deadline, t.clock.now + 30.0)
        t.clock.advance(29.99)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertTrue(t.runtime.active)
        self.assertEqual(len(t.move_calls), 2)
        t.clock.advance(0.02)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(t.runtime.operation_generation, gen + 1)
        self.assertEqual(len(t.move_calls), 2)
        self.assertTrue(t.loot[0].relocated)
        self.assertTrue(t.loot[1].relocated)
        self.assertEqual(len([line for line in t.log_lines
                              if "prolonged nonwalking" in line]), 1)

    def test_short_jump_resumes_remaining_pickups_without_repeating_moved(self):
        t = self.begin_partial()
        t.pawn.Physics = 2
        t.runtime.on_tick(t.pc, None, None, None)
        t.clock.advance(3.0)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertEqual(t.runtime.moved, 2)
        self.assertEqual(t.runtime.cursor, 2)
        t.pawn.Physics = 1
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertEqual(t.runtime.moved, 4)
        self.assertEqual(t.runtime.cursor, 4)
        self.assertEqual(t.runtime.landing_deadline, 0.0)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(len(t.move_calls), 5)
        self.assertEqual(len(set(address for _, address in t.move_calls)), 5)

    def test_return_to_walking_resets_budget_for_next_interruption(self):
        t = self.begin_partial()
        t.pawn.Physics = 2
        t.runtime.on_tick(t.pc, None, None, None)
        first_deadline = t.runtime.landing_deadline
        t.clock.advance(28.0)
        t.pawn.Physics = 1
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertTrue(t.runtime.active)
        self.assertEqual(t.runtime.landing_deadline, 0.0)
        self.assertEqual(t.runtime.moved, 4)
        t.clock.advance(1.0)
        t.pawn.Physics = 4
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertGreater(t.runtime.landing_deadline, first_deadline)
        t.clock.advance(25.0)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertTrue(t.runtime.active)
        t.clock.advance(5.1)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(t.runtime.moved, 0)
        self.assertEqual(len(t.move_calls), 4)

    def test_vehicle_or_nonwalking_death_eventually_releases_pending(self):
        for physics in (0, 3, 4, 5):
            with self.subTest(physics=physics):
                t = self.begin_partial()
                t.pawn.Physics = physics
                t.runtime.on_tick(t.pc, None, None, None)
                t.clock.advance(30.1)
                t.runtime.on_tick(t.pc, None, None, None)
                self.assert_cancelled(t)
                self.assertEqual(len(t.move_calls), 2)

    def test_deleted_pawn_cancels_immediately_without_relocating_more(self):
        t = self.begin_partial()
        t.pawn.bDeleteMe = True
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(len(t.move_calls), 2)

    def test_world_replacement_or_expired_pawn_cancels_before_timeout(self):
        for mode in ("world", "pawn", "weak"):
            with self.subTest(mode=mode):
                t = self.begin_partial()
                t.pawn.Physics = 2
                t.runtime.on_tick(t.pc, None, None, None)
                if mode == "world":
                    t.pc.WorldInfo = Actor(11)
                elif mode == "pawn":
                    replacement = Actor(21)
                    replacement.Physics = 1
                    t.pc.Pawn = replacement
                else:
                    t.runtime.pawn_ptr = lambda: None
                t.runtime.on_tick(t.pc, None, None, None)
                self.assert_cancelled(t)
                self.assertEqual(len(t.move_calls), 2)

    def test_travel_hook_cancels_active_gather_without_undoing_moved(self):
        t = self.begin_partial()
        t.runtime.on_travel(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(len(t.move_calls), 2)

    def test_local_only_tick_cannot_be_expired_by_foreign_controller(self):
        t = self.begin_partial()
        t.pawn.Physics = 2
        remote = Actor(300)
        t.runtime.on_tick(remote, None, None, None)
        self.assertEqual(t.runtime.landing_deadline, 0.0)
        t.clock.advance(55)
        t.runtime.on_tick(remote, None, None, None)
        self.assertTrue(t.runtime.active)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertEqual(t.runtime.landing_deadline, t.clock.now + 30)
        self.assertEqual(len(t.move_calls), 2)

    def test_new_gather_after_expiry_picks_up_only_remaining_eligible(self):
        t = self.begin_partial()
        t.pawn.Physics = 4
        t.runtime.on_tick(t.pc, None, None, None)
        t.clock.advance(30.1)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        t.pawn.Physics = 1
        t.runtime.begin()
        self.assertTrue(t.runtime.active)
        self.assertEqual(len(t.runtime.pending), 3)
        t.runtime.on_tick(t.pc, None, None, None)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(len(t.move_calls), 5)
        self.assertEqual(len(set(address for _, address in t.move_calls)), 5)
        self.assertEqual(t.hooks.adds, 2)
        self.assertEqual(t.hooks.removes, 2)

    def test_tps_mid_gather_still_uses_original_12_second_limit(self):
        t = self.begin_partial(tps=True)
        t.pawn.Physics = 2
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertEqual(t.runtime.landing_deadline, t.clock.now + 12)
        t.clock.advance(11.9)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assertTrue(t.runtime.active)
        t.clock.advance(0.2)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(len(t.move_calls), 2)

    def test_initial_airborne_gather_still_waits_for_landing_with_12_second_gate(self):
        t = load_runtime()
        t.pawn.Physics = 2
        t.runtime.begin()
        self.assertTrue(t.runtime.waiting_for_landing)
        self.assertFalse(t.runtime.active)
        self.assertEqual(t.runtime.landing_deadline, t.clock.now + 12)
        t.clock.advance(12.1)
        t.runtime.on_tick(t.pc, None, None, None)
        self.assert_cancelled(t)
        self.assertEqual(t.move_calls, [])


if __name__ == "__main__":
    unittest.main()
