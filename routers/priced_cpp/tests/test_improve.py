import heapq
from pathlib import Path
import random
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from controller import NativeSession
from improve import Refiner, improve
from candidates import planar_candidates, folded_candidates
from controller import as_submission
from route import generate
from m3d.checker import check
from m3d.grid import Grid
from m3d.model import Instance, Submission
from test_router import fixture, route


class ImprovementTests(unittest.TestCase):
    def test_layer_candidates_keep_pins_and_report_checked_delay(self):
        inst = fixture(layers=3, terminals=[(0, 1, 0), (3, 0, 2), (3, 2, 2)])
        inst.layer_delay = [6, 2, 5]
        sub = Submission(inst.name, [route(generate(inst, k=1)["nets"][0]["candidates"][0])])
        with NativeSession(inst) as worker:
            refiner = Refiner(inst, sub, worker)
            original = refiner.routes[0]
            options = planar_candidates(worker, 0, original) + folded_candidates(worker, 0, original)
            self.assertTrue(options)
            for candidate in options:
                result = check(inst, as_submission(inst, {0: candidate}))
                self.assertTrue(result.legal, result.reasons)
                self.assertEqual(result.total_delay, candidate["delay"])

    def test_multisink_spt_matches_independent_shortest_path_bound(self):
        rng = random.Random(11)
        for _ in range(12):
            inst = fixture(layers=3, terminals=[(0, 1, 0), (3, 0, 2), (3, 2, 2)])
            inst.layer_delay = [6, 2, 5]
            grid = Grid(inst)
            pins = [grid.vid(p.vertex()) for p in inst.pins]
            blocked = {v for v in range(36) if v not in pins and rng.random() < .15}
            distances = {pins[0]: 0}
            heap = [(0, pins[0])]
            while heap:
                cost, u = heapq.heappop(heap)
                if cost != distances[u]:
                    continue
                for v, weight in grid.neighbors(u):
                    if v in blocked:
                        continue
                    if cost + weight < distances.get(v, float("inf")):
                        distances[v] = cost + weight
                        heapq.heappush(heap, (cost + weight, v))
            options = generate(inst, blocked=blocked, strategy="spt", diversity=0, k=4)["nets"][0]["candidates"]
            if any(p not in distances for p in pins):
                self.assertEqual(options, [])
            else:
                optimum = sum(distances[p] for p in pins[1:])
                for candidate in options:
                    self.assertEqual(candidate["delay"], optimum)
                    self.assertTrue(check(inst, Submission(inst.name, [route(candidate)])).legal)

    def test_seeded_alternatives_are_distinct_optimal_trees(self):
        inst = fixture(width=5, height=4, terminals=[(0, 0, 0), (4, 3, 0)])
        options = generate(inst, strategy="spt", diversity=0, k=4)["nets"][0]["candidates"]
        self.assertEqual(len(options), 4)
        self.assertEqual({o["delay"] for o in options}, {14})

    def test_group_refinement_is_monotone_and_legal(self):
        root = HERE.parents[1]
        inst = Instance.load(str(root / "benchmarks/case_02.json"))
        sub = Submission.load(str(root / "benchmarks/reference/case_02.sol.json"))
        result, metrics = improve(inst, sub, passes=2, pair_rounds=1, group_limit=10,
                                  cbs_nodes=16, lns_rounds=1, local_iters=8)
        self.assertTrue(check(inst, result).legal)
        self.assertLessEqual(metrics["total_delay"], check(inst, sub).total_delay)
        self.assertGreater(metrics["queries"], len(inst.nets))
        self.assertEqual([r["delay"] for r in metrics["trace"]],
                         sorted([r["delay"] for r in metrics["trace"]], reverse=True))

    def test_worse_replacement_is_rejected_without_changing_occupancy(self):
        inst = fixture()
        candidate = generate(inst, k=1)["nets"][0]["candidates"][0]
        sub = Submission(inst.name, [route(candidate)])
        with NativeSession(inst) as worker:
            refiner = Refiner(inst, sub, worker)
            old = refiner.routes[0]
            owners = dict(refiner.owners)
            self.assertFalse(refiner.accept({0: dict(old, delay=old["delay"] + 1)}))
            self.assertEqual(refiner.owners, owners)
            self.assertIs(refiner.routes[0], old)


if __name__ == "__main__":
    unittest.main()
