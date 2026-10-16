from pathlib import Path
import random
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from route import generate
from m3d.grid import Grid
from m3d.checker import check
from m3d.model import Submission
from test_router import fixture, route


def enumerate_trees(inst, blocked):
    """Independent exhaustive oracle on the eight-vertex test grid."""
    grid = Grid(inst)
    pins = [grid.vid(p.vertex()) for p in inst.pins]
    edges = [(u,v,w) for u in range(8) for v,w in grid.neighbors(u) if u < v and u not in blocked and v not in blocked]
    trees = []
    for mask in range(1 << len(edges)):
        chosen = [edge for i,edge in enumerate(edges) if mask & (1 << i)]
        vertices = set(pins) | {v for u,v,w in chosen} | {u for u,v,w in chosen}
        if len(chosen) != len(vertices)-1:
            continue
        adjacency = {v:[] for v in vertices}
        for u,v,w in chosen:
            adjacency[u].append((v,w)); adjacency[v].append((u,w))
        distances = {pins[0]:0}
        stack = [pins[0]]
        while stack:
            u = stack.pop()
            for v,w in adjacency[u]:
                if v not in distances:
                    distances[v] = distances[u]+w
                    stack.append(v)
        if len(distances) == len(vertices):
            trees.append((sum(distances[p] for p in pins[1:]), vertices))
    return trees


class PricedTreeTests(unittest.TestCase):
    def test_compact_initial_tree_shares_more_without_extra_delay(self):
        inst = fixture(width=4,height=4,terminals=[(0,0,0),(3,1,0),(1,3,0)])
        ordinary = generate(inst,strategy="spt",k=1)["nets"][0]["candidates"][0]
        compact = generate(inst,strategy="compact",k=1)["nets"][0]["candidates"][0]
        self.assertEqual(compact["delay"],16)
        self.assertEqual(compact["delay"],ordinary["delay"])
        self.assertEqual(len(compact["edges"]),6)  # XY spans require at least six wires.
        self.assertLess(len(compact["edges"]),len(ordinary["edges"]))
        self.assertEqual(compact["vertex_price"],0)
        self.assertTrue(check(inst,Submission(inst.name,[route(compact)])).legal)

    def test_large_fanout_corridor_preserves_proxy_upper_bound(self):
        rng = random.Random(97)
        positions = [(v%2,(v%4)//2,v//4) for v in range(8)]
        for sinks in (4,5,6,7):
            inst = fixture(width=2,height=2,layers=2,terminals=positions[:sinks+1])
            inst.layer_delay, inst.via_delay = [3,1], 2
            prices = {v:rng.randrange(12)/2 for v in range(8)}
            candidate = generate(inst, strategy="steiner", k=1, prices=prices)["nets"][0]["candidates"][0]
            proxies = [generate(inst, strategy=strategy, k=1, prices=prices)["nets"][0]["candidates"][0]
                       for strategy in ("spt","greedy")]
            self.assertLessEqual(candidate["priced_cost"], min(c["priced_cost"] for c in proxies))
            self.assertTrue(check(inst, Submission(inst.name,[route(candidate)])).legal)

    def test_two_and_three_sink_prices_match_exhaustive_optimum(self):
        rng = random.Random(32)
        for sinks in (2,3):
            points = [(0,0,0),(1,1,0),(1,1,1),(0,1,1)][:sinks+1]
            inst = fixture(width=2,height=2,layers=2,terminals=points)
            inst.layer_delay, inst.via_delay = [3,1], 2
            for blocked in (set(), {4}):
                trees = enumerate_trees(inst, blocked)
                for _ in range(5):
                    prices = {v:rng.randrange(12)/2 for v in range(8)}
                    options = generate(inst, strategy="steiner", k=1, prices=prices, blocked=blocked)["nets"][0]["candidates"]
                    self.assertTrue(options)
                    expected = min(delay+sum(prices[v] for v in vertices) for delay,vertices in trees)
                    self.assertEqual(options[0]["priced_cost"], expected)
                    result = check(inst, Submission(inst.name,[route(options[0])]))
                    self.assertTrue(result.legal)
                    self.assertEqual(options[0]["delay"], result.total_delay)


if __name__ == "__main__":
    unittest.main()
