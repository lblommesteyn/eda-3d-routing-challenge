#!/usr/bin/env python3
"""Negotiated congestion and legal refinement around the C++ per-net router."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import subprocess
import sys
import time

from route import BINARY, Grid, Instance, Submission
from m3d.checker import check
from m3d.model import NetRoute


class NativeSession:
    """One native process per instance; requests use current prices sequentially."""
    def __init__(self, inst, binary=BINARY):
        self.inst, self.grid = inst, Grid(inst)
        self.pins = {p.id: self.grid.vid(p.vertex()) for p in inst.pins}
        self.nets = {n.id: n for n in inst.nets}
        owners = {self.pins[p]: n.id for n in inst.nets for p in n.pins()}
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
        header = [f"{inst.width} {inst.height} {inst.layers} {inst.via_delay}",
                  " ".join(map(str, inst.layer_delay)), str(len(owners))]
        header += [f"{v} {nid}" for v, nid in sorted(owners.items())] + ["-1"]
        self.process.stdin.write("\n".join(header) + "\n")
        self.process.stdin.flush()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        self.process.stderr.close()

    def request(self, nid, prices=None, blocked=(), k=4, diversity=2.0,
                strategy="greedy", seed=0, pins=None):
        prices = prices or {}
        net = self.nets[nid]
        pin_ids = net.pins() if pins is None else pins
        if (len(pin_ids) < 2 or pin_ids[0] != net.driver or
                len(set(pin_ids)) != len(pin_ids) or not set(pin_ids) <= set(net.pins())):
            raise ValueError("request pins must be this net's driver and distinct sinks")
        mode = {"greedy":0, "spt":1, "steiner":2, "compact":3}[strategy]
        lines = [f"{nid} {len(pin_ids)} {k} {4*k} {diversity} {mode} {seed}",
                 " ".join(str(self.pins[p]) for p in pin_ids), str(len(prices))]
        lines += [f"{v} {cost}" for v, cost in sorted(prices.items())]
        lines += [str(len(blocked))] + [str(v) for v in sorted(blocked)]
        self.process.stdin.write("\n".join(lines) + "\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if not header:
            raise RuntimeError("native worker stopped: " + self.process.stderr.read())
        response_id, count = map(int, header)
        if response_id != nid:
            raise RuntimeError("native response net mismatch")
        candidates = []
        for _ in range(count):
            delay, price, count_edges = self.process.stdout.readline().split()
            edges = [tuple(map(int, self.process.stdout.readline().split())) for _ in range(int(count_edges))]
            vertices = {self.pins[p] for p in pin_ids}
            vertices.update(v for edge in edges for v in edge)
            candidates.append({"delay": int(delay), "edges": edges, "vertices": vertices,
                               "priced_cost": int(delay) + float(price)})
        return candidates


def as_submission(inst, routes):
    grid = Grid(inst)
    return Submission(inst.name, [NetRoute(n.id, [(grid.coord(a), grid.coord(b))
                      for a, b in routes[n.id]["edges"]]) for n in inst.nets if n.id in routes])


def solve(inst, *, k=4, max_iters=60, present=2.0, growth=1.35,
          history=2.0, refinement_passes=1, time_limit=30.0, progress=None,
          negotiation_k=None, strategy="greedy", seed=0, fanout_scale=False,
          history_decay=1.0, dual=None):
    """Route from scratch, then accept only legal, strictly lower-delay refinements.

    Returns (legal submission or None, metrics). No reference-route fallback.
    The time budget is checked between net searches; one search can overrun it.
    """
    if type(k) is not int or k < 1 or type(max_iters) is not int or max_iters < 0:
        raise ValueError("k must be positive and max_iters nonnegative")
    if strategy not in ("greedy", "spt", "steiner", "compact") or type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("invalid strategy or seed")
    if not math.isfinite(history_decay) or not 0 <= history_decay <= 1:
        raise ValueError("history_decay must be between zero and one")
    negotiation_k = k if negotiation_k is None else negotiation_k
    if type(negotiation_k) is not int or negotiation_k < 1:
        raise ValueError("negotiation_k must be positive")
    if type(refinement_passes) is not int or refinement_passes < 0:
        raise ValueError("refinement_passes must be nonnegative")
    if any(not math.isfinite(v) or v < 0 for v in (present, history)):
        raise ValueError("prices must be finite and nonnegative")
    if not math.isfinite(growth) or growth < 1 or not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("growth must be >=1 and time_limit positive")
    start = time.perf_counter()
    routes, owners, history_prices = {}, defaultdict(set), defaultdict(float)
    trace, queries, candidates_total, accepted = [], 0, 0, 0
    refinement_evaluations, refinement_passes_completed = 0, 0
    candidate_overlaps = []
    grid = Grid(inst)
    seed_prices = {}
    if dual is not None:
        if (len(dual.capacity) != inst.width * inst.height * inst.layers or
                set(dual.bids) != {n.id for n in inst.nets}):
            raise ValueError("dual prices do not match instance grid/nets")
        seed_prices = {v: float(cost) for v, cost in enumerate(dual.capacity) if cost > 0}
    pin_vid = {p.id: grid.vid(p.vertex()) for p in inst.pins}

    def bbox(net):
        coords = [grid.coord(pin_vid[p]) for p in net.pins()]
        return sum(max(c[d] for c in coords) - min(c[d] for c in coords) for d in range(3))

    order = [n.id for n in sorted(inst.nets, key=lambda n: (-bbox(n), n.id))]

    def add(nid, candidate):
        routes[nid] = candidate
        for v in candidate["vertices"]:
            owners[v].add(nid)

    def remove(nid):
        old = routes.pop(nid)
        for v in old["vertices"]:
            owners[v].remove(nid)
            if not owners[v]:
                del owners[v]
        return old

    def snapshot(stage, iteration, rerouted=0, changed=0):
        conflicts = {v: occupants for v, occupants in owners.items() if len(occupants) > 1}
        edge_owners = defaultdict(set)
        for nid, candidate in routes.items():
            for edge in candidate["edges"]:
                edge_owners[edge].add(nid)
        record = {"stage": stage, "iteration": iteration,
                  "elapsed_seconds": time.perf_counter() - start,
                  "routed_nets": len(routes), "total_nets": len(order),
                  "conflict_vertices": len(conflicts),
                  "vertex_overuse": sum(len(s) - 1 for s in conflicts.values()),
                  "conflict_edges": sum(len(s) > 1 for s in edge_owners.values()),
                  "affected_nets": len(set().union(*conflicts.values())) if conflicts else 0,
                  "diagnostic_delay": sum(c["delay"] for c in routes.values()),
                  "rerouted_nets": rerouted, "accepted_changes": changed, "candidate_queries": queries,
                  "generated_candidates": candidates_total}
        record["occupied_vertex_uses"] = sum(len(c["vertices"]) for c in routes.values())
        record["unique_occupied_vertices"] = len(owners)
        trace.append(record)
        if progress:
            progress(record)
        return conflicts

    def request(worker, nid, prices=None, blocked=(), count=None, seeded=True):
        nonlocal queries, candidates_total
        prices = dict(prices or {})
        if seeded:
            for v, cost in seed_prices.items():
                prices[v] = prices.get(v, 0) + cost
        divisor = max(1, len(worker.nets[nid].sinks)) if fanout_scale else 1
        search_prices = {v: cost / divisor for v, cost in (prices or {}).items()}
        native_strategy = "compact" if strategy == "steiner" and not prices else strategy
        options = worker.request(nid, search_prices, blocked, k if count is None else count,
                                 strategy=native_strategy, seed=seed)
        for option in options:
            option["priced_cost"] = option["delay"] + sum((prices or {}).get(v, 0) for v in option["vertices"])
        options.sort(key=lambda c: (c["priced_cost"], c["edges"]))
        queries += 1
        candidates_total += len(options)
        if len(options) > 1:
            first = options[0]["vertices"]
            for option in options[1:]:
                other = option["vertices"]
                candidate_overlaps.append(len(first & other) / len(first | other))
        return options

    termination = "iteration_limit"
    negotiation_delay = None
    with NativeSession(inst) as worker:
        for nid in order:
            if time.perf_counter() - start >= time_limit:
                termination = "time_limit"
                break
            options = request(worker, nid, count=negotiation_k)
            if not options:
                termination = "unroutable_net"
                break
            add(nid, options[0])
        conflicts = snapshot("independent", 0)
        for iteration in range(1, max_iters + 1):
            if len(routes) != len(order) or not conflicts:
                break
            if time.perf_counter() - start >= time_limit:
                termination = "time_limit"
                break
            affected = set().union(*conflicts.values())
            if history_decay != 1:
                history_prices = defaultdict(float, {v: cost * history_decay for v, cost in history_prices.items()})
            for v, occupants in conflicts.items():
                history_prices[v] += history * (len(occupants) - 1)
            rerouted = 0
            for nid in order:
                if nid not in affected:
                    continue
                if time.perf_counter() - start >= time_limit:
                    termination = "time_limit"
                    break
                old = remove(nid)
                prices = dict(history_prices)
                for v, occupants in owners.items():
                    prices[v] = prices.get(v, 0) + present * len(occupants)
                options = request(worker, nid, prices, count=negotiation_k)
                if not options:
                    add(nid, old)
                    termination = "unroutable_net"
                    break
                # During negotiation, accept the oracle's replacement. For
                # multi-sink nets its search cost is a surrogate for the tree
                # cost; comparing that tree against the old one can reject
                # every move and permanently freeze an illegal conflict.
                # Legal refinement below still requires a delay improvement.
                chosen = options[0]
                add(nid, chosen)
                rerouted += 1
            conflicts = snapshot("negotiation", iteration, rerouted)
            present = min(present * growth, 1e6)
            if termination in ("time_limit", "unroutable_net"):
                break
        legal = check(inst, as_submission(inst, routes))
        if legal.legal:
            termination = "legal"
            negotiation_delay = legal.total_delay
            snapshot("legal", len([r for r in trace if r["stage"] == "negotiation"]))
            for pass_id in range(1, refinement_passes + 1):
                changes, evaluated = 0, 0
                for nid in sorted(order, key=lambda n: (-routes[n]["delay"], n)):
                    if time.perf_counter() - start >= time_limit:
                        break
                    old = remove(nid)
                    options = request(worker, nid, blocked=set(owners), seeded=False)
                    evaluated += 1
                    refinement_evaluations += 1
                    chosen = options[0] if options and options[0]["delay"] < old["delay"] else old
                    add(nid, chosen)
                    if chosen is not old:
                        # Verify the whole assembled solution before accepting.
                        validated = check(inst, as_submission(inst, routes))
                        if not validated.legal:
                            remove(nid)
                            add(nid, old)
                            raise RuntimeError("native refinement produced an illegal solution")
                        changes += 1
                        accepted += 1
                if evaluated == len(order):
                    refinement_passes_completed += 1
                snapshot("refinement", pass_id, evaluated, changes)
                if not changes or time.perf_counter() - start >= time_limit:
                    break
    submission = as_submission(inst, routes)
    result = check(inst, submission)
    metrics = {"instance": inst.name, "legal": result.legal,
               "dual_sha256": dual.sha256 if dual is not None else None,
               "dual_seed_vertices": len(seed_prices),
               "negotiation_k": negotiation_k, "refinement_k": k,
               "strategy": strategy, "seed": seed,
               "fanout_scale": fanout_scale, "history_decay": history_decay,
               "total_delay": result.total_delay if result.legal else None,
               "negotiation_delay": negotiation_delay,
               "termination": termination, "controller_seconds": time.perf_counter() - start,
               "iterations": sum(r["stage"] == "negotiation" for r in trace),
               "refinement_improvements": accepted,
               "refinement_nets_evaluated": refinement_evaluations,
               "refinement_passes_completed": refinement_passes_completed,
               "budget_exhausted": time.perf_counter() - start >= time_limit,
               "candidate_queries": queries, "generated_candidates": candidates_total,
               "mean_alternative_vertex_jaccard": (sum(candidate_overlaps) / len(candidate_overlaps)
                                                    if candidate_overlaps else None),
               "checker_reasons": result.reasons[:10], "trace": trace}
    return (submission if result.legal else None), metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--negotiation-k", type=int, default=1,
                        help="candidates per negotiation query; --k controls refinement")
    parser.add_argument("--max-iters", type=int, default=60)
    parser.add_argument("--history", type=float, default=2.0)
    parser.add_argument("--present", type=float, default=2.0)
    parser.add_argument("--growth", type=float, default=1.35)
    parser.add_argument("--refinement-passes", type=int, default=1)
    parser.add_argument("--time-limit", type=float, default=30.0)
    args = parser.parse_args()
    start = time.perf_counter()
    def progress(r):
        print(f"{r['stage']} {r['iteration']}: conflicts={r['conflict_vertices']} "
              f"overuse={r['vertex_overuse']} delay={r['diagnostic_delay']} "
              f"elapsed={r['elapsed_seconds']:.3f}s", flush=True)
    try:
        inst = Instance.load(str(args.case))
        submission, metrics = solve(inst, k=args.k, max_iters=args.max_iters,
            present=args.present, growth=args.growth, history=args.history,
            refinement_passes=args.refinement_passes, time_limit=args.time_limit, progress=progress,
            negotiation_k=args.negotiation_k)
        if submission is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            submission.save(str(args.out))
        metrics["end_to_end_seconds"] = time.perf_counter() - start
        args.metrics.parent.mkdir(parents=True, exist_ok=True)
        args.metrics.write_text(json.dumps(metrics, indent=2) + "\n")
        print(f"legal={metrics['legal']} delay={metrics['total_delay']} "
              f"runtime={metrics['end_to_end_seconds']:.3f}s")
        return 0 if submission else 2
    except (ValueError, OSError, RuntimeError) as error:
        print(f"controller: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
