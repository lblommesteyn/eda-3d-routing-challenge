from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from controller import NativeSession, solve
from route import generate
from m3d.checker import check
from m3d.grid import Grid
from m3d.model import Instance
from test_router import fixture
from benchmark import aggregate


class ControllerTests(unittest.TestCase):
    def test_persistent_worker_matches_batch_and_reprices(self):
        inst = fixture()
        grid = Grid(inst)
        with NativeSession(inst) as worker:
            for prices in ({}, {grid.vid((1, 1, 0)): 100}, {}):
                actual = worker.request(0, prices=prices, k=3)
                expected = generate(inst, prices=prices, k=3)["nets"][0]["candidates"]
                self.assertEqual([c["delay"] for c in actual], [c["delay"] for c in expected])
                self.assertEqual([c["priced_cost"] for c in actual], [c["priced_cost"] for c in expected])
                self.assertEqual([c["edges"] for c in actual],
                    [[(grid.vid(tuple(a)), grid.vid(tuple(b))) for a, b in c["edges"]] for c in expected])

    def test_conflicts_converge_then_refinement_preserves_legality(self):
        inst = Instance.load(str(HERE.parents[1] / "benchmarks/case_01.json"))
        submission, metrics = solve(inst)
        self.assertIsNotNone(submission)
        result = check(inst, submission)
        self.assertTrue(result.legal)
        self.assertEqual(metrics["total_delay"], result.total_delay)
        self.assertGreater(metrics["trace"][0]["conflict_vertices"], 0)
        self.assertEqual(metrics["trace"][-1]["conflict_vertices"], 0)
        self.assertGreater(metrics["iterations"], 0)
        self.assertLessEqual(metrics["total_delay"], metrics["negotiation_delay"])
        self.assertGreater(metrics["refinement_improvements"], 0)

    def test_iteration_limit_never_returns_illegal_submission(self):
        inst = Instance.load(str(HERE.parents[1] / "benchmarks/case_01.json"))
        submission, metrics = solve(inst, max_iters=0, refinement_passes=0)
        self.assertIsNone(submission)
        self.assertFalse(metrics["legal"])
        self.assertIsNone(metrics["total_delay"])
        self.assertEqual(metrics["termination"], "iteration_limit")

    def test_adaptive_mode_reduces_candidate_work_and_stays_legal(self):
        inst = Instance.load(str(HERE.parents[1] / "benchmarks/case_01.json"))
        fixed, fixed_metrics = solve(inst, k=4)
        adaptive, metrics = solve(inst, k=4, negotiation_k=1)
        self.assertTrue(check(inst, adaptive).legal)
        self.assertEqual(metrics["total_delay"], fixed_metrics["total_delay"])
        self.assertLess(metrics["generated_candidates"], fixed_metrics["generated_candidates"])
        self.assertEqual(metrics["negotiation_k"], 1)

    def test_time_limit_never_returns_partial_submission(self):
        submission, metrics = solve(fixture(), time_limit=1e-12)
        self.assertIsNone(submission)
        self.assertFalse(metrics["legal"])
        self.assertEqual(metrics["termination"], "time_limit")
        self.assertEqual(metrics["trace"][0]["routed_nets"], 0)

    def test_already_legal_has_no_negotiation_iterations(self):
        submission, metrics = solve(fixture(), refinement_passes=0)
        self.assertTrue(check(fixture(), submission).legal)
        self.assertEqual(metrics["iterations"], 0)
        self.assertEqual(metrics["termination"], "legal")

    def test_invalid_controller_parameters(self):
        for kwargs in ({"k": 0}, {"max_iters": -1}, {"history": -1},
                       {"present": float("nan")}, {"growth": 0}, {"time_limit": 0}):
            with self.assertRaises(ValueError):
                solve(fixture(), **kwargs)

    def test_incomplete_runs_do_not_receive_ranked_aggregate(self):
        base = dict(legal=True, total_delay=100, baseline_delay=120,
                    end_to_end_seconds=1, iterations=2,
                    initial_conflict_vertices=3, final_conflict_vertices=0)
        result = aggregate([base, dict(base, legal=False, total_delay=None)])
        self.assertFalse(result["complete"])
        self.assertEqual(result["aggregate_score"], 0)
        self.assertIsNone(result["total_delay"])
        self.assertEqual(aggregate([base])["aggregate_score"], 1.2)
        checkpoint = aggregate([base], expected_cases=20)
        self.assertFalse(checkpoint["complete"])
        self.assertEqual(checkpoint["total_cases"], 20)
        self.assertEqual(checkpoint["aggregate_score"], 0)


if __name__ == "__main__":
    unittest.main()
