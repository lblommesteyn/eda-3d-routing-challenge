from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from joint_repair import JointRouter
    HAVE_SOLVER = True
except ImportError:
    HAVE_SOLVER = False
from controller import NativeSession,as_submission
from improve import Refiner
from m3d.model import Net,NetRoute,Submission
from m3d.checker import check
from test_router import fixture


def initial(inst, paths):
    inst.nets = [Net(0,0,[1]),Net(1,2,[3])]
    return Submission(inst.name,[NetRoute(n,list(zip(path,path[1:]))) for n,path in enumerate(paths)])


@unittest.skipUnless(HAVE_SOLVER,"joint solver requires optional optimization packages")
class JointGeometryTests(unittest.TestCase):
    def test_joint_flow_improves_legal_detour(self):
        inst = fixture(width=3,height=3,terminals=[(0,0,0),(2,0,0),(0,2,0),(2,2,0)])
        sub = initial(inst,[[(0,0,0),(0,1,0),(1,1,0),(2,1,0),(2,0,0)],
                            [(0,2,0),(1,2,0),(2,2,0)]])
        self.assertTrue(check(inst,sub).legal)
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = JointRouter(ref).solve([0,1],1)
            self.assertIsNotNone(replacement)
            ref.accept(replacement)
            result = check(inst,as_submission(inst,ref.routes))
        self.assertTrue(result.legal)
        self.assertEqual(result.total_delay,8)
        self.assertEqual(metrics["initial_delay"],12)

    def test_crossing_nets_cannot_both_take_the_cheapest_layer(self):
        inst = fixture(width=3,height=3,layers=2,terminals=[(0,1,0),(2,1,0),(1,0,0),(1,2,0)])
        sub = initial(inst,[[(0,1,0),(0,1,1),(1,1,1),(2,1,1),(2,1,0)],
                            [(1,0,0),(1,1,0),(1,2,0)]])
        self.assertTrue(check(inst,sub).legal)
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = JointRouter(ref).solve([0,1],1)
        self.assertIsNone(replacement)
        self.assertEqual(metrics["initial_delay"],14)
        self.assertEqual(metrics["lower_bound"],8)
        self.assertEqual(metrics["total_delay"],14)

    def test_dual_ties_preserve_physical_optimum_at_zero_excess(self):
        import numpy as np
        from types import SimpleNamespace
        inst = fixture(width=3,height=3,terminals=[(0,0,0),(2,0,0),(0,2,0),(2,2,0)])
        sub = initial(inst,[[(0,0,0),(1,0,0),(2,0,0)],[(0,2,0),(1,2,0),(2,2,0)]])
        dual = SimpleNamespace(capacity=np.zeros(9),bids={n:(np.array([],dtype=int),np.array([])) for n in (0,1)},
            offsets=np.zeros(3,dtype=int),vertices=np.array([],dtype=int),values=np.array([]))
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = JointRouter(ref,dual,ownership_ties=True).solve([0,1],1)
            self.assertIsNotNone(replacement)
            ref.accept(replacement,allow_equal=True)
            checked = check(inst,as_submission(inst,ref.routes))
        self.assertTrue(checked.legal)
        self.assertEqual(checked.total_delay,8)
        self.assertEqual(metrics["lower_bound"],8)

    def test_conditional_dual_proves_crossing_optimum(self):
        import numpy as np
        from types import SimpleNamespace
        inst = fixture(width=3,height=3,layers=2,terminals=[(0,1,0),(2,1,0),(1,0,0),(1,2,0)])
        sub = initial(inst,[[(0,1,0),(0,1,1),(1,1,1),(2,1,1),(2,1,0)],
                            [(1,0,0),(1,1,0),(1,2,0)]])
        capacity = np.zeros(18); capacity[4] = 6
        dual = SimpleNamespace(capacity=capacity,bids={n:(np.array([4]),np.array([6.])) for n in (0,1)},
            offsets=np.array([0,1,2]),vertices=np.array([4,4]),values=np.array([6.,6.]))
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = JointRouter(ref,dual).solve([0,1],1)
        self.assertIsNone(replacement)
        self.assertEqual(metrics["dual_lower_bound"],14)
        self.assertEqual(metrics["status"],"bound proves optimal")

    def test_fixed_xy_can_merge_projected_branches_without_new_edges(self):
        from layers import assign
        inst = fixture(width=3,height=2,layers=2,terminals=[(0,1,0),(2,1,0),(2,1,1)])
        lower = [(0,1,0),(0,0,0),(1,0,0),(2,0,0),(2,1,0)]
        upper = [(0,1,0),(0,1,1),(1,1,1),(2,1,1)]
        sub = Submission(inst.name,[NetRoute(0,list(zip(lower,lower[1:]))+list(zip(upper,upper[1:])))])
        self.assertTrue(check(inst,sub).legal)
        _,old = assign(inst,sub,chunk=1,seconds=1,all_layers=True)
        self.assertEqual(old["total_delay"],15)
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = JointRouter(ref,fixed_xy=True).solve([0],1)
            self.assertIsNotNone(replacement)
            ref.accept(replacement)
            result = as_submission(inst,ref.routes)
        self.assertTrue(check(inst,result).legal)
        self.assertEqual(check(inst,result).total_delay,11)
        def xy_edges(submission):
            return {tuple(sorted((a[:2],b[:2]))) for route in submission.routes for a,b in route.edges
                    if a[:2] != b[:2]}
        self.assertTrue(xy_edges(result) <= xy_edges(sub))

    def test_xy_expansion_can_shorten_a_detour_with_zero_sink_increase(self):
        inst = fixture(width=3,height=2,terminals=[(0,1,0),(2,1,0)])
        path = [(0,1,0),(0,0,0),(1,0,0),(2,0,0),(2,1,0)]
        sub = Submission(inst.name,[NetRoute(0,list(zip(path,path[1:])))])
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            unchanged,_ = JointRouter(ref,fixed_xy=True).solve([0],1)
            self.assertIsNone(unchanged)
            replacement,metrics = JointRouter(ref,xy_radius=1,sink_extra=0).solve([0],1)
            self.assertIsNotNone(replacement)
            ref.accept(replacement)
            result = check(inst,as_submission(inst,ref.routes))
        self.assertTrue(result.legal)
        self.assertEqual(result.total_delay,4)
        self.assertEqual(metrics["initial_delay"],8)

    def test_aggregate_flow_pays_for_every_sink_on_a_shared_stem(self):
        inst = fixture(width=3,height=2,terminals=[(0,0,0),(2,0,0),(2,1,0)])
        path = [(0,0,0),(0,1,0),(1,1,0),(2,1,0),(2,0,0)]
        sub = Submission(inst.name,[NetRoute(0,list(zip(path,path[1:])))])
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker)
            replacement,metrics = JointRouter(ref,aggregate=True).solve([0],1)
            self.assertIsNotNone(replacement)
            ref.accept(replacement)
            result = check(inst,as_submission(inst,ref.routes))
        self.assertTrue(result.legal)
        self.assertEqual(metrics["initial_delay"],14)
        self.assertEqual(result.total_delay,10)
