from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from mip import solve_mip
    import numpy as np
    from scipy.sparse import csc_matrix
    HAVE_SOLVER = True
except ImportError:
    HAVE_SOLVER = False


@unittest.skipUnless(HAVE_SOLVER,"optional warm-start solver is not installed")
class WarmStartTests(unittest.TestCase):
    def test_improves_legal_incumbent_without_fixing_its_choices(self):
        matrix = csc_matrix([[1,1,0,0],[0,0,1,1],[1,0,1,0]],dtype=float)
        result = solve_mip([1,5,1,6],matrix,[1,1,-np.inf],[1,1,1],
                              [0,1,0,1],[1]*4,1)
        self.assertIsNotNone(result.x)
        self.assertEqual(result.fun,6)
        self.assertTrue(np.all(matrix @ result.x <= [1,1,1]))
        self.assertEqual(list((matrix @ result.x)[:2]),[1,1])

    def test_rejects_overlapping_incumbent(self):
        matrix = csc_matrix([[1,1]],dtype=float)
        with self.assertRaisesRegex(ValueError,"incumbent"):
            solve_mip([1,1],matrix,[-np.inf],[1],[1,1],[1,1],1)

    def test_objective_offset_preserves_continuous_cost(self):
        from mip import LinearModel
        model = LinearModel()
        x = model.variable(cost=1,initial=3,upper=5,integer=False)
        model.constraint([(x,1)],lower=2.5)
        model.offset = 100
        result = model.solve(1)
        self.assertAlmostEqual(result.fun,102.5)
        self.assertAlmostEqual(result.x[x],2.5)
