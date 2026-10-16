#!/usr/bin/env python3
"""JSON bridge for the native per-net router; no Python dependencies required."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from m3d.grid import Grid
from m3d.model import Instance, Submission

BINARY = Path(__file__).resolve().parent / "build" / "router"


def price_data(inst, data):
    """Validate sparse coordinate prices and hard blocks."""
    grid = Grid(inst)

    def vertex(value):
        if (not isinstance(value, list) or len(value) != 3 or
                any(type(v) is not int for v in value) or not inst.in_bounds(tuple(value))):
            raise ValueError(f"invalid vertex: {value}")
        return grid.vid(tuple(value))

    prices = {}
    for entry in data.get("vertices", []):
        v = vertex(entry["vertex"])
        cost = entry["price"]
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ValueError("prices must be finite and nonnegative")
        if v in prices:
            raise ValueError(f"duplicate price for {entry['vertex']}")
        prices[v] = cost
    blocked = {vertex(v) for v in data.get("blocked_vertices", [])}
    return prices, blocked


def generate(inst, *, net_ids=None, prices=None, blocked=None, occupancy=None,
             present_factor=5.0, hard_occupancy=False, k=4, attempts=None,
             diversity=2.0, binary=BINARY, strategy="greedy", seed=0):
    """Return candidates, not a globally conflict-free submission.

    Prices apply once per occupied vertex when reporting priced_cost. Search
    uses root-delay-seeded greedy growth with prices on newly added vertices.
    Candidate diversity is encouraged by a separate accumulated reuse penalty.
    """
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    if strategy not in ("greedy", "spt", "steiner", "compact") or type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("invalid strategy or seed")
    attempts = k * 4 if attempts is None else attempts
    if type(attempts) is not int or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    for value in (diversity, present_factor):
        if not math.isfinite(value) or value < 0:
            raise ValueError("penalty factors must be finite and nonnegative")
    grid = Grid(inst)
    prices, blocked = dict(prices or {}), set(blocked or ())
    for v, cost in prices.items():
        if type(v) is not int or not 0 <= v < grid.w * grid.h * grid.l:
            raise ValueError("price vertex out of bounds")
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("prices must be finite and nonnegative")
    if any(type(v) is not int or not 0 <= v < grid.w * grid.h * grid.l for v in blocked):
        raise ValueError("blocked vertex out of bounds")
    pin_vid = {p.id: grid.vid(p.vertex()) for p in inst.pins}
    owners = {pin_vid[p]: n.id for n in inst.nets for p in n.pins()}
    selected = set(net_ids) if net_ids is not None else {n.id for n in inst.nets}
    if selected - {n.id for n in inst.nets}:
        raise ValueError("unknown net id")
    nets = [n for n in inst.nets if n.id in selected]
    occupancy_vertices = {}
    if occupancy is not None:
        if occupancy.instance != inst.name:
            raise ValueError("occupancy instance does not match")
        for route in occupancy.routes:
            if any(not inst.in_bounds(v) for edge in route.edges for v in edge):
                raise ValueError("occupancy vertex out of bounds")
            # Count distinct NETS, not edge endpoints or repeated branches.
            vertices = {grid.vid(v) for edge in route.edges for v in edge}
            for v in vertices:
                occupancy_vertices.setdefault(v, set()).add(route.net)
    lines = [f"{inst.width} {inst.height} {inst.layers} {inst.via_delay}",
             " ".join(map(str, inst.layer_delay)), str(len(owners))]
    lines.extend(f"{v} {nid}" for v, nid in sorted(owners.items()))
    lines.append(str(len(nets)))
    for net in nets:
        net_prices, net_blocked = dict(prices), set(blocked)
        for v, occupants in occupancy_vertices.items():
            other_count = len(occupants - {net.id})
            if other_count:
                net_prices[v] = net_prices.get(v, 0) + present_factor * other_count
                if hard_occupancy:
                    net_blocked.add(v)
        mode = {"greedy":0, "spt":1, "steiner":2, "compact":3}[strategy]
        lines.append(f"{net.id} {len(net.pins())} {k} {attempts} {diversity} {mode} {seed}")
        lines.append(" ".join(str(pin_vid[p]) for p in net.pins()))
        lines.append(str(len(net_prices)))
        lines.extend(f"{v} {cost}" for v, cost in sorted(net_prices.items()))
        lines.append(str(len(net_blocked)))
        lines.extend(str(v) for v in sorted(net_blocked))
    start = time.perf_counter()
    process = subprocess.run([str(binary)], input="\n".join(lines) + "\n",
                             text=True, capture_output=True, check=True)
    elapsed = time.perf_counter() - start
    tokens = iter(process.stdout.split())
    results = []
    for net in nets:
        nid, count = int(next(tokens)), int(next(tokens))
        if nid != net.id:
            raise RuntimeError("native response has incorrect net id")
        candidates = []
        for _ in range(count):
            delay, price, edge_count = int(next(tokens)), float(next(tokens)), int(next(tokens))
            edges = [[list(grid.coord(int(next(tokens)))), list(grid.coord(int(next(tokens))))]
                     for _ in range(edge_count)]
            candidates.append({"delay": delay, "vertex_price": price,
                               "priced_cost": delay + price, "edges": edges})
        results.append({"net": nid, "candidates": candidates})
    if next(tokens, None) is not None:
        raise RuntimeError("unexpected native output")
    return {"format": "m3d-route-candidates", "version": 1, "instance": inst.name,
            "native_wall_seconds": elapsed, "nets": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--net", type=int, help="omit to generate candidates for every net")
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--occupancy", type=Path, help="current routes; own net is excluded from congestion")
    parser.add_argument("--hard-occupancy", action="store_true")
    parser.add_argument("--present-factor", type=float, default=5.0)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--attempts", type=int)
    parser.add_argument("--diversity", type=float, default=2.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    start = time.perf_counter()
    try:
        inst = Instance.load(str(args.case))
        data = json.loads(args.prices.read_text()) if args.prices else {}
        prices, blocked = price_data(inst, data)
        occupancy = Submission.load(str(args.occupancy)) if args.occupancy else None
        if args.hard_occupancy and occupancy is None:
            raise ValueError("--hard-occupancy requires --occupancy")
        result = generate(inst, net_ids=[args.net] if args.net is not None else None,
                          prices=prices, blocked=blocked, occupancy=occupancy,
                          hard_occupancy=args.hard_occupancy,
                          present_factor=args.present_factor, k=args.k,
                          attempts=args.attempts, diversity=args.diversity)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"Generated {sum(len(n['candidates']) for n in result['nets'])} candidates "
              f"for {len(result['nets'])} nets in {time.perf_counter() - start:.3f}s -> {args.out}")
        return 0 if all(n["candidates"] for n in result["nets"]) else 2
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"route: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
