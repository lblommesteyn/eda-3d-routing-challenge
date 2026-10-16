from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import numpy as np
    from conflict_repair import ConflictRouter
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False
from controller import NativeSession,as_submission
from improve import Refiner
from m3d.checker import check
from test_router import fixture
from test_joint import initial


@unittest.skipUnless(HAVE_NUMPY,"dual conflict search requires numpy")
class ConflictTests(unittest.TestCase):
    def setup_case(self,long_route):
        inst = fixture(width=3,height=3,layers=2,terminals=[(0,1,0),(2,1,0),(1,0,0),(1,2,0)])
        upper = ([(0,1,0),(0,1,1),(0,0,1),(1,0,1),(2,0,1),(2,1,1),(2,1,0)] if long_route
                 else [(0,1,0),(0,1,1),(1,1,1),(2,1,1),(2,1,0)])
        sub = initial(inst,[upper,[(1,0,0),(1,1,0),(1,2,0)]])
        cap = np.zeros(18);cap[4] = 6
        dual = SimpleNamespace(capacity=cap,bids={n:(np.array([4]),np.array([6.])) for n in (0,1)},
            offsets=np.array([0,1,2]),vertices=np.array([4,4]),values=np.array([6.,6.]))
        return inst,sub,dual

    def test_branches_to_a_legal_improvement(self):
        inst,sub,dual = self.setup_case(True)
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = ConflictRouter(ref,dual).solve([0,1],1)
            self.assertIsNotNone(replacement)
            ref.accept(replacement)
            checked = check(inst,as_submission(inst,ref.routes))
        self.assertTrue(checked.legal)
        self.assertEqual(checked.total_delay,14)
        self.assertEqual(metrics["lower_bound"],14)
        self.assertEqual(metrics["status"],"improved")

    def test_dual_bound_prunes_an_optimal_incumbent(self):
        inst,sub,dual = self.setup_case(False)
        with NativeSession(inst) as worker:
            replacement,metrics = ConflictRouter(Refiner(inst,sub,worker),dual).solve([0,1],1)
        self.assertIsNone(replacement)
        self.assertEqual(metrics["expanded"],0)
        self.assertEqual(metrics["total_delay"],14)

    def test_deadline_keeps_the_legal_start(self):
        inst,sub,dual = self.setup_case(True)
        with NativeSession(inst) as worker:
            replacement,metrics = ConflictRouter(Refiner(inst,sub,worker),dual).solve([0,1],.000001)
        self.assertIsNone(replacement)
        self.assertEqual(metrics["total_delay"],18)
        self.assertEqual(metrics["status"],"budget")
        self.assertTrue(check(inst,sub).legal)
