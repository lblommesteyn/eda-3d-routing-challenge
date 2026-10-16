from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from native import optimize
from route import Instance, Submission
from m3d.checker import check


class NativeOptimizerTests(unittest.TestCase):
    def test_atomic_moves_preserve_legality_and_incumbent(self):
        root = HERE.parents[1]
        inst = Instance.load(str(root/"benchmarks/case_02.json"))
        sub = Submission.load(str(root/"benchmarks/reference/case_02.sol.json"))
        for accurate, compact_growth, neighbor_polish in ((False,False,False),(True,False,False),
                                                         (False,True,False),(False,False,True)):
            result,metrics = optimize(inst,sub,iterations=60,seconds=2,seed=33,
                                      temperature=50,accurate_prices=accurate,
                                      compact_growth=compact_growth,neighbor_polish=neighbor_polish)
            checked = check(inst,result)
            self.assertTrue(checked.legal,checked.reasons)
            self.assertEqual(checked.total_delay,metrics["total_delay"])
            self.assertLessEqual(checked.total_delay,metrics["initial_delay"])

    def test_deadline_rolls_back_partial_group(self):
        root = HERE.parents[1]
        inst = Instance.load(str(root/"benchmarks/case_01.json"))
        sub = Submission.load(str(root/"benchmarks/reference/case_01.sol.json"))
        result,metrics = optimize(inst,sub,iterations=1000,seconds=.0001,seed=17)
        self.assertTrue(check(inst,result).legal)
        self.assertLessEqual(metrics["total_delay"],metrics["initial_delay"])


if __name__ == "__main__":
    unittest.main()
