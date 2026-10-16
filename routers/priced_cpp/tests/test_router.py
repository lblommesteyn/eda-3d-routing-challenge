import heapq
import random
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from route import generate, price_data
from m3d.checker import check
from m3d.grid import Grid
from m3d.model import Instance, Net, NetRoute, Pin, Submission


def fixture(width=4, height=3, layers=1, terminals=None):
    terminals = terminals or [(0, 1, 0), (width - 1, 1, layers - 1)]
    pins = [Pin(i, i, 0, *v) for i, v in enumerate(terminals)]
    return Instance("test", width, height, layers, [2] * layers, 3, [], pins,
                    [Net(0, 0, list(range(1, len(pins))))])


def route(candidate, nid=0):
    return NetRoute(nid, [(tuple(a), tuple(b)) for a, b in candidate["edges"]])


def vertices(candidate):
    return {tuple(v) for edge in candidate["edges"] for v in edge}


class RouterTests(unittest.TestCase):
    def assert_legal(self, inst, candidate):
        result = check(inst, Submission(inst.name, [route(candidate)]))
        net = next(n for n in result.nets if n.net == 0)
        self.assertTrue(net.legal, net.reasons)
        self.assertEqual(candidate["delay"], net.delay)

    def test_prices_change_route(self):
        inst = fixture()
        grid = Grid(inst)
        expensive = (1, 1, 0)
        normal = generate(inst, k=1)["nets"][0]["candidates"][0]
        priced = generate(inst, k=1, prices={grid.vid(expensive): 100})["nets"][0]["candidates"][0]
        self.assertIn(expensive, vertices(normal))
        self.assertNotIn(expensive, vertices(priced))
        self.assertGreater(priced["delay"], normal["delay"])
        self.assert_legal(inst, priced)

    def test_distinct_deterministic_candidates(self):
        inst = fixture()
        first = generate(inst, k=4)["nets"]
        self.assertEqual(first, generate(inst, k=4)["nets"])
        candidates = first[0]["candidates"]
        self.assertGreater(len(candidates), 1)
        self.assertLessEqual(len(candidates), 4)
        signatures = {tuple((tuple(a), tuple(b)) for a, b in c["edges"]) for c in candidates}
        self.assertEqual(len(signatures), len(candidates))
        self.assertEqual([c["priced_cost"] for c in candidates],
                         sorted(c["priced_cost"] for c in candidates))
        for candidate in candidates:
            self.assert_legal(inst, candidate)

    def test_multisink_delay_and_unique_vertex_price(self):
        inst = fixture(layers=3, terminals=[(0, 1, 0), (3, 0, 2), (3, 2, 2)])
        prices = {v: 0.5 for v in range(36)}
        for candidate in generate(inst, k=4, prices=prices)["nets"][0]["candidates"]:
            self.assert_legal(inst, candidate)
            self.assertEqual(candidate["vertex_price"], len(vertices(candidate)) * 0.5)
            self.assertEqual(candidate["priced_cost"], candidate["delay"] + candidate["vertex_price"])

    def test_other_nets_pins_are_forbidden(self):
        inst = fixture()
        inst.pins += [Pin(2, 2, 0, 1, 1, 0), Pin(3, 3, 0, 1, 2, 0)]
        inst.nets.append(Net(1, 2, [3]))
        candidates = generate(inst, net_ids=[0], k=3)["nets"][0]["candidates"]
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertNotIn((1, 1, 0), vertices(candidate))
            self.assertNotIn((1, 2, 0), vertices(candidate))
            self.assert_legal(inst, candidate)

    def test_fewer_candidates_when_only_one_path_exists(self):
        inst = fixture(width=4, height=1, terminals=[(0, 0, 0), (3, 0, 0)])
        candidates = generate(inst, k=4)["nets"][0]["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assert_legal(inst, candidates[0])
        blocked = {Grid(inst).vid((1, 0, 0))}
        self.assertEqual(generate(inst, blocked=blocked)["nets"][0]["candidates"], [])

    def test_two_pin_cost_matches_independent_dijkstra(self):
        rng = random.Random(48)
        for _ in range(25):
            inst = fixture(layers=2)
            grid = Grid(inst)
            prices = {v: rng.randrange(12) / 2 for v in range(24)}
            source, target = (grid.vid(p.vertex()) for p in inst.pins)
            blocked = {v for v in range(24) if v not in (source, target) and rng.random() < 0.12}
            distances = {source: prices[source]}
            heap = [(prices[source], source)]
            while heap:
                cost, u = heapq.heappop(heap)
                if cost != distances[u]:
                    continue
                for v, delay in grid.neighbors(u):
                    if v in blocked:
                        continue
                    next_cost = cost + delay + prices[v]
                    if next_cost < distances.get(v, float("inf")):
                        distances[v] = next_cost
                        heapq.heappush(heap, (next_cost, v))
            candidates = generate(inst, prices=prices, blocked=blocked, k=1)["nets"][0]["candidates"]
            if target not in distances:
                self.assertEqual(candidates, [])
            else:
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["priced_cost"], distances[target])
                self.assert_legal(inst, candidates[0])

    def test_occupancy_excludes_self_and_blocks_other_nets(self):
        root = HERE.parents[1]
        inst = Instance.load(str(root / "benchmarks/case_01.json"))
        original = Submission.load(str(root / "benchmarks/reference/case_01.sol.json"))
        # Including only the net being regenerated must add no congestion cost.
        own = Submission(inst.name, [r for r in original.routes if r.net == 0])
        with_self = generate(inst, net_ids=[0], occupancy=own, k=1)["nets"]
        without = generate(inst, net_ids=[0], k=1)["nets"]
        self.assertEqual(with_self, without)
        candidates = generate(inst, net_ids=[0], occupancy=original, hard_occupancy=True)["nets"][0]["candidates"]
        self.assertTrue(candidates)
        for candidate in candidates:
            combined = Submission(inst.name, [route(candidate)] + [r for r in original.routes if r.net != 0])
            result = check(inst, combined)
            self.assertTrue(result.legal, result.reasons)
            self.assertEqual(candidate["delay"], result.nets[0].delay)

    def test_bad_prices_and_parameters(self):
        inst = fixture()
        for price in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                price_data(inst, {"vertices": [{"vertex": [1, 1, 0], "price": price}]})
        with self.assertRaises(ValueError):
            price_data(inst, {"blocked_vertices": [[100, 0, 0]]})
        with self.assertRaises(ValueError):
            generate(inst, k=0)
        with self.assertRaises(ValueError):
            generate(inst, net_ids=[999])


if __name__ == "__main__":
    unittest.main()
