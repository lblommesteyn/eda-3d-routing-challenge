"""Cheap candidate families complementing the native graph searches."""
from collections import defaultdict, deque
import heapq
import random


def _tree(worker, nid, edges):
    grid, inst = worker.grid, worker.inst
    pins = [worker.pins[p] for p in worker.nets[nid].pins()]
    forbidden = {worker.pins[p] for n in inst.nets if n.id != nid for p in n.pins()}
    graph = defaultdict(list)
    for a, b in edges:
        if a in forbidden or b in forbidden:
            continue
        weight = inst.edge_delay(grid.coord(a), grid.coord(b))
        graph[a].append((b, weight)); graph[b].append((a, weight))
    distance, parent = {pins[0]: 0}, {}
    heap = [(0, pins[0])]
    while heap:
        cost, v = heapq.heappop(heap)
        if cost != distance[v]:
            continue
        for other, weight in graph[v]:
            if cost + weight < distance.get(other, float("inf")):
                distance[other], parent[other] = cost + weight, v
                heapq.heappush(heap, (cost+weight, other))
    if any(p not in distance for p in pins):
        return None
    used, chosen = {pins[0]}, set()
    for pin in pins[1:]:
        v = pin
        while v not in used:
            used.add(v)
            prev = parent[v]
            chosen.add((min(v, prev), max(v, prev)))
            v = prev
    return dict(edges=sorted(chosen), vertices=used, delay=sum(distance[p] for p in pins[1:]))


def folded_candidates(worker, nid, original):
    """Move horizontal routing toward a layer while preserving existing via sites.

    Original access stems stay available. A shortest-path tree in the union of
    old edges, lifted horizontal edges, and extended vias chooses legal pieces.
    """
    grid, inst = worker.grid, worker.inst
    results = []
    for layer in range(inst.layers):
        edges = set(original["edges"])
        for a, b in original["edges"]:
            ax, ay, az = grid.coord(a)
            bx, by, bz = grid.coord(b)
            if az == bz:
                u, v = grid.vid((ax, ay, layer)), grid.vid((bx, by, layer))
                edges.add((min(u,v), max(u,v)))
            else:
                for z in range(min(az, bz, layer), max(az, bz, layer)):
                    edges.add((grid.vid((ax,ay,z)), grid.vid((ax,ay,z+1))))
        tree = _tree(worker, nid, edges)
        if tree:
            results.append(tree)
    return results


def planar_candidates(worker, nid, original, seed=0):
    """Reuse a route's XY footprint on each layer, reconnecting fixed pins.

    The projection can contain cycles; BFS predecessor paths produce a tree.
    Vertical pin columns are merged, then actual driver-to-sink delay is measured.
    Other nets' pins remain forbidden. Global capacity is enforced by selection.
    """
    grid, inst = worker.grid, worker.inst
    pins = [worker.pins[p] for p in worker.nets[nid].pins()]
    coords = {v: grid.coord(v) for edge in original["edges"] for v in edge}
    adjacency = defaultdict(set)
    for a, b in original["edges"]:
        pa, pb = coords[a][:2], coords[b][:2]
        if pa != pb:
            adjacency[pa].add(pb)
            adjacency[pb].add(pa)
    root = grid.coord(pins[0])[:2]
    pin_coords = [grid.coord(p) for p in pins]
    parent, seen = {}, {root}
    queue = deque([root])
    rng = random.Random(seed)
    while queue:
        xy = queue.popleft()
        neighbors = sorted(adjacency[xy])
        if seed:
            rng.shuffle(neighbors)
        for other in neighbors:
            if other not in seen:
                parent[other] = xy
                seen.add(other)
                queue.append(other)
    if any(coord[:2] not in seen for coord in pin_coords):
        return []
    xy_edges = set()
    for x, y, _ in pin_coords[1:]:
        xy = (x, y)
        while xy != root:
            prev = parent[xy]
            xy_edges.add(tuple(sorted((xy, prev))))
            xy = prev
    forbidden = {worker.pins[p] for n in inst.nets if n.id != nid for p in n.pins()}
    results = []
    for layer in range(inst.layers):
        edges = set()
        for (ax, ay), (bx, by) in xy_edges:
            a, b = grid.vid((ax, ay, layer)), grid.vid((bx, by, layer))
            edges.add((min(a, b), max(a, b)))
        for x, y, z in pin_coords:
            for level in range(min(z, layer), max(z, layer)):
                a, b = grid.vid((x, y, level)), grid.vid((x, y, level+1))
                edges.add((a, b))
        vertices = set(pins) | {v for edge in edges for v in edge}
        if vertices & forbidden:
            continue
        graph = defaultdict(list)
        for a, b in edges:
            weight = inst.edge_delay(grid.coord(a), grid.coord(b))
            graph[a].append((b, weight)); graph[b].append((a, weight))
        distances = {pins[0]: 0}
        queue = deque([pins[0]])
        while queue:
            v = queue.popleft()
            for other, weight in graph[v]:
                if other not in distances:
                    distances[other] = distances[v] + weight
                    queue.append(other)
        if len(distances) != len(vertices) or len(edges) != len(vertices)-1:
            continue
        results.append(dict(edges=sorted(edges), vertices=vertices,
                            delay=sum(distances[p] for p in pins[1:])))
    return results
