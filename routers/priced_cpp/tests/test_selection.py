from itertools import product
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
try:
    from selection import master, select
    from scipy.optimize import Bounds, LinearConstraint, OptimizeResult, milp
    import numpy as np
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False
from m3d.checker import check
from m3d.model import Submission
from route import generate
from test_router import fixture, route


@unittest.skipUnless(HAVE_SCIPY, "optional SciPy selector is not installed")
class SelectionTests(unittest.TestCase):
    def test_master_matches_exhaustive_capacity_constrained_selection(self):
        pool = {
            0: {0: dict(delay=2, vertices={1, 2}), 1: dict(delay=5, vertices={1, 4})},
            1: {0: dict(delay=3, vertices={2, 3}), 1: dict(delay=6, vertices={3, 5})},
            2: {0: dict(delay=1, vertices={4, 5}), 1: dict(delay=4, vertices={6, 7})},
        }
        optimum = float("inf")
        for choices in product(*[list(pool[n].values()) for n in sorted(pool)]):
            if all(not a["vertices"] & b["vertices"] for i,a in enumerate(choices) for b in choices[i+1:]):
                optimum = min(optimum, sum(c["delay"] for c in choices))
        columns, costs, eq, ub, _ = master(pool)
        result = milp(costs, integrality=np.ones(len(columns)), bounds=Bounds(0, 1),
                      constraints=[LinearConstraint(eq, 1, 1), LinearConstraint(ub, -np.inf, 1)])
        self.assertTrue(result.success)
        self.assertEqual(result.fun, optimum)

    def test_no_solver_incumbent_preserves_legal_start(self):
        inst = fixture()
        candidate = generate(inst, k=1)["nets"][0]["candidates"][0]
        sub = Submission(inst.name, [route(candidate)])
        with patch("selection.milp", return_value=OptimizeResult(x=None, status=1)):
            result, metrics = select(inst, sub, rounds=1, initial_candidates=2)
        self.assertTrue(check(inst, result).legal)
        self.assertEqual(metrics["total_delay"], candidate["delay"])


if __name__ == "__main__":
    unittest.main()
