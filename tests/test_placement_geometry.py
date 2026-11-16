"""Offline placement geometry regression without importing UnrealSDK or the game."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import math
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'mod' / 'LootRecovery' / '__init__.py'


def load_geometry():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'PlacementContext', 'slot_position'}
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    if {node.name for node in nodes} != names:
        raise AssertionError('Placement helpers not available in runtime')
    env = {'dataclass': dataclass, 'math': math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), env)
    return env['PlacementContext'], env['slot_position']


class PlacementGeometryTests(unittest.TestCase):
    def test_forward_slot_and_next_layer(self):
        ctx_type, slot_position = load_geometry()
        ctx = ctx_type.from_values((10, 20, 30), 0)
        self.assertEqual(ctx.origin, (10., 20., 30.))
        self.assertEqual(slot_position(ctx, 0, 9, 155, 62), (165., 20., 30.))
        self.assertEqual(slot_position(ctx, 9, 9, 155, 62), (227., 20., 30.))

    def test_angular_symmetry_and_yaw(self):
        ctx_type, slot_position = load_geometry()
        ctx = ctx_type.from_values((0, 0, 0), 0)
        left = slot_position(ctx, 1, 9, 155, 62)
        right = slot_position(ctx, 2, 9, 155, 62)
        self.assertAlmostEqual(left[0], right[0])
        self.assertAlmostEqual(left[1], -right[1])
        yawed = ctx_type.from_values((0, 0, 5), 16384)
        x, y, z = slot_position(yawed, 0, 9, 155, 62)
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(y, 155, places=6)
        self.assertEqual(z, 5)


if __name__ == '__main__':
    unittest.main()
