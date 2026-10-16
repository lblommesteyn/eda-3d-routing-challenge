from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
try:
    from layers import assign, topology
    HAVE_SOLVER = True
except ImportError:
    HAVE_SOLVER = False
from route import generate
from m3d.checker import check
from m3d.model import Instance, NetRoute, Submission
from test_router import fixture, route


@unittest.skipUnless(HAVE_SOLVER, "optional layer solver is not installed")
class LayerTests(unittest.TestCase):
    def test_multisink_root_column_cost_matches_checker(self):
        inst = fixture(layers=3, terminals=[(0,1,0), (0,1,2), (3,2,2)])
        inst.layer_delay = [6,2,5]
        sub = Submission(inst.name, [route(generate(inst, strategy="spt", k=1)["nets"][0]["candidates"][0])])
        result, metrics = assign(inst, sub, seconds=1)
        self.assertTrue(check(inst, result).legal)
        self.assertEqual(metrics["total_delay"], check(inst, result).total_delay)
        self.assertLessEqual(metrics["total_delay"], metrics["initial_delay"])

    def test_separate_visits_to_same_xy_remain_separate(self):
        inst = fixture(width=2, height=2, layers=3, terminals=[(0,0,0), (1,1,2)])
        vertices = [(0,0,0),(1,0,0),(1,0,1),(0,0,1),(0,1,1),(0,1,2),(1,1,2)]
        sub = Submission(inst.name, [NetRoute(0,list(zip(vertices,vertices[1:])))])
        self.assertTrue(check(inst, sub).legal)
        tree = topology(inst, inst.nets[0], sub.routes[0], 8)
        self.assertGreaterEqual(sum(v[:2] == (0,0) for v in tree["keys"]), 2)
        result, metrics = assign(inst, sub, seconds=1)
        self.assertTrue(check(inst, result).legal)
        self.assertLessEqual(metrics["total_delay"], metrics["initial_delay"])

    def test_short_budget_keeps_a_legal_solution(self):
        root = HERE.parents[1]
        inst = Instance.load(str(root/"benchmarks/case_01.json"))
        sub = Submission.load(str(root/"benchmarks/reference/case_01.sol.json"))
        result, metrics = assign(inst, sub, seconds=.001)
        self.assertTrue(check(inst, result).legal)
        self.assertLessEqual(metrics["total_delay"], metrics["initial_delay"])


if __name__ == "__main__":
    unittest.main()
