from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import numpy as np
    from dual import load
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False
from test_router import fixture
from m3d.model import Net,Pin,Submission
from m3d.checker import check


@unittest.skipUnless(HAVE_NUMPY,"dual adapter requires optional numpy")
class DualTests(unittest.TestCase):
    def setUp(self):
        self.inst = fixture()
        self.inst.pins += [Pin(2,2,0,1,0,0),Pin(3,3,0,0,2,0),Pin(4,4,0,3,2,0)]
        self.inst.nets = [Net(0,0,[1,2]),Net(1,3,[4])]

    def test_capacity_uses_maximum_net_sum_and_dense_matches_sparse(self):
        with tempfile.TemporaryDirectory() as folder:
            sparse,dense = Path(folder)/"sparse.npz",Path(folder)/"dense.npz"
            np.savez(sparse,off=[0,2,3,5],vert=[4,5,4,4,6],val=[1.,2.,3.,7.,1.])
            alpha = np.zeros((3,12))
            alpha[0,[4,5]] = [1,2]; alpha[1,4] = 3; alpha[2,[4,6]] = [7,1]
            np.savez(dense,alpha=alpha)
            a,b = load(self.inst,sparse),load(self.inst,dense)
            self.assertEqual(a.capacity[4],7)
            self.assertEqual(dict(zip(*a.bids[0])),{4:4,5:2})
            np.testing.assert_array_equal(a.capacity,b.capacity)
            np.testing.assert_array_equal(a.values,b.values)

    def test_rejects_invalid_sparse_data(self):
        for fields in (dict(off=[0,1],vert=[1],val=[1.]),
                       dict(off=[0,1,1,1],vert=[12],val=[1.]),
                       dict(off=[0,1,1,1],vert=[1],val=[-1.]),
                       dict(off=[0,2,2,2],vert=[1,1],val=[1.,2.])):
            with self.subTest(fields=fields),tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/"bad.npz"
                np.savez(path,**fields)
                with self.assertRaises(ValueError):
                    load(self.inst,path)

    def test_native_dual_mode_preserves_legal_incumbent(self):
        from native import optimize
        from route import Instance
        root = Path(__file__).resolve().parents[3]
        inst = Instance.load(str(root/"benchmarks/case_02.json"))
        sub = Submission.load(str(root/"benchmarks/reference/case_02.sol.json"))
        count = sum(len(n.sinks) for n in inst.nets)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"alpha.npz"
            np.savez(path,off=np.arange(count+1),vert=np.ones(count,dtype=int),val=np.full(count,.2))
            result,metrics = optimize(inst,sub,dual=load(inst,path),iterations=100,seconds=2,seed=47)
        self.assertTrue(check(inst,result).legal)
        self.assertLessEqual(metrics["total_delay"],metrics["initial_delay"])
        self.assertEqual(len(metrics["dual_sha256"]),64)

    def test_scratch_controller_uses_capacity_prices_and_polishes_physical_delay(self):
        from controller import solve
        from m3d.grid import Grid
        inst = fixture()
        grid = Grid(inst)
        expensive = (1, 1, 0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"alpha.npz"
            np.savez(path,off=[0,1],vert=[grid.vid(expensive)],val=[100.])
            prices = load(inst,path)
            seeded,metrics = solve(inst,dual=prices,strategy="spt",k=1,refinement_passes=0)
            polished,polish_metrics = solve(inst,dual=prices,strategy="spt",k=1,refinement_passes=1)
        self.assertTrue(check(inst,seeded).legal)
        self.assertFalse(any(expensive in edge for edge in seeded.routes[0].edges))
        self.assertEqual(metrics["total_delay"],10)
        self.assertEqual(metrics["dual_sha256"],prices.sha256)
        self.assertEqual(metrics["dual_seed_vertices"],1)
        self.assertTrue(check(inst,polished).legal)
        self.assertEqual(polish_metrics["total_delay"],6)

    def test_diagnosis_accounts_for_full_duality_gap(self):
        from dual import diagnose
        from controller import NativeSession,as_submission
        routes,blocked = {},set()
        with NativeSession(self.inst) as worker:
            for net in self.inst.nets:
                routes[net.id] = worker.request(net.id,blocked=blocked,k=1,strategy="spt")[0]
                blocked.update(routes[net.id]["vertices"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"alpha.npz"
            np.savez(path,off=[0,2,3,5],vert=[4,5,4,4,6],val=[1.,2.,3.,7.,1.])
            result = diagnose(self.inst,as_submission(self.inst,routes),load(self.inst,path))
        self.assertAlmostEqual(result["dual_value"],12.)
        self.assertAlmostEqual(result["total_delay"]-result["dual_value"],
                               result["path_slack"]+result["price_slack"])
        self.assertGreaterEqual(result["path_slack"],-1e-8)
        self.assertGreaterEqual(result["price_slack"],-1e-8)

    def test_per_sink_union_candidate_is_a_legal_tree(self):
        from priced_repair import candidate
        from controller import NativeSession,as_submission
        inst = fixture(width=6,height=4,layers=2,terminals=[(0,1,0),(5,3,1),(1,0,0)])
        with tempfile.TemporaryDirectory() as folder,NativeSession(inst) as worker:
            path = Path(folder)/"alpha.npz"
            np.savez(path,off=[0,1,2],vert=[5,6],val=[3.,2.])
            route = candidate(worker,inst.nets[0],load(inst,path),0,1,53)
            self.assertIsNotNone(route)
            result = check(inst,as_submission(inst,{0:route}))
            self.assertTrue(result.legal,result.reasons)
            self.assertEqual(result.total_delay,route["delay"])
            # Switching the same worker between sink subsets must refresh A*.
            self.assertEqual(worker.request(0,pins=[0,1],k=1,strategy="spt")[0]["delay"],17)
            self.assertEqual(worker.request(0,pins=[0,2],k=1,strategy="spt")[0]["delay"],4)
            self.assertEqual(worker.request(0,k=1,strategy="spt")[0]["delay"],21)
