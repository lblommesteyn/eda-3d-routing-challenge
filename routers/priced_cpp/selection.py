#!/usr/bin/env python3
"""Price-guided candidate generation and joint capacity-constrained selection.

The master is exact over its finite candidate pool, not over all possible routes.
SPT/greedy pricing are heuristic column generators for multi-sink nets.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import csc_matrix, vstack

from controller import NativeSession, as_submission
from candidates import planar_candidates, folded_candidates
from improve import Refiner
from route import Instance, Submission
from m3d.checker import check


def master(pool):
    nets = sorted(pool)
    columns = [(nid, c) for nid in nets for c in pool[nid].values()]
    net_row = {n: i for i, n in enumerate(nets)}
    vertex_nets = {}
    for nid, candidate in columns:
        for v in candidate["vertices"]:
            vertex_nets.setdefault(v, set()).add(nid)
    vertices = sorted(v for v, ns in vertex_nets.items() if len(ns) > 1)
    vertex_row = {v: i for i, v in enumerate(vertices)}
    eq_rows, eq_cols, ub_rows, ub_cols = [], [], [], []
    for column, (nid, candidate) in enumerate(columns):
        eq_rows.append(net_row[nid]); eq_cols.append(column)
        for v in candidate["vertices"]:
            if v in vertex_row:
                ub_rows.append(vertex_row[v]); ub_cols.append(column)
    eq = csc_matrix((np.ones(len(eq_rows)), (eq_rows, eq_cols)), shape=(len(nets), len(columns)))
    ub = csc_matrix((np.ones(len(ub_rows)), (ub_rows, ub_cols)), shape=(len(vertices), len(columns)))
    costs = np.array([candidate["delay"] for _, candidate in columns], dtype=float)
    return columns, costs, eq, ub, vertices


def polish_pool(inst, pool, best, *, seed=0, groups=20, seconds=1, deadline=float("inf")):
    """Solve small joint selections while the other incumbent nets stay fixed."""
    rng = random.Random(seed)
    targets = sorted(best, key=lambda n: (-(best[n]["delay"] - min(c["delay"] for c in pool[n].values())), n))
    for attempt in range(groups):
        if time.perf_counter() >= deadline:
            break
        target = targets[attempt % len(targets)]
        owners = {v: n for n, c in best.items() for v in c["vertices"]}
        choices = sorted(pool[target].values(), key=lambda c: (c["delay"], len(c["vertices"])))
        desired = choices[rng.randrange(min(16, len(choices)))]
        hits = {}
        for v in desired["vertices"]:
            n = owners.get(v)
            if n is not None and n != target:
                hits[n] = hits.get(n, 0) + 1
        group = {target} | set(sorted(hits, key=lambda n: (-hits[n], n))[:15])
        blocked = {v for n,c in best.items() if n not in group for v in c["vertices"]}
        local = {n: {key:c for key,c in pool[n].items() if not c["vertices"] & blocked} for n in group}
        if any(not candidates for candidates in local.values()):
            continue
        columns, costs, eq, ub, _ = master(local)
        incumbent_cost = sum(best[n]["delay"] for n in group)
        if sum(min(c["delay"] for c in local[n].values()) for n in group) >= incumbent_cost:
            continue
        solved = milp(costs, integrality=np.ones(len(columns)), bounds=Bounds(0, 1),
                      constraints=[LinearConstraint(eq, 1, 1), LinearConstraint(ub, -np.inf, 1)],
                      options={"time_limit": min(seconds, max(.01, deadline-time.perf_counter())), "mip_rel_gap": 1e-4})
        if solved.x is None:
            continue
        selected = {n:c for value,(n,c) in zip(solved.x, columns) if value > .5}
        if set(selected) == group and sum(c["delay"] for c in selected.values()) < incumbent_cost:
            proposed = best | selected
            result = check(inst, as_submission(inst, proposed))
            if not result.legal:
                raise RuntimeError("local joint selection is illegal")
            best = proposed
    return best


def select(inst, submission, *, rounds=20, initial_candidates=16, candidates=2,
           mip_seconds=15, lp_seconds=10, time_limit=300, seed=0, progress=None,
           pool_path=None, local_groups=20, warm_start=False, exact_prices=False):
    start = time.perf_counter()
    if any(type(value) is not int or value < 1 for value in (rounds, initial_candidates, candidates)):
        raise ValueError("rounds and candidate counts must be positive integers")
    if any(not math.isfinite(value) or value <= 0 for value in (time_limit, mip_seconds, lp_seconds)):
        raise ValueError("time limits must be finite and positive")
    if type(local_groups) is not int or local_groups < 0 or type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("invalid group count or seed")
    deadline = start + time_limit
    trace, queries = [], 0
    rng = random.Random(seed)
    with NativeSession(inst) as worker:
        initial = Refiner(inst, submission, worker)
        best, best_delay = dict(initial.routes), initial.delay
        pool = {nid: {} for nid in best}

        def add(nid, candidate):
            signature = frozenset(candidate["vertices"])
            if signature in pool[nid]:
                if candidate["delay"] < pool[nid][signature]["delay"]:
                    pool[nid][signature] = candidate
                    return True
                return False
            pool[nid][signature] = candidate
            return True

        def generate(nid, prices=None, strategy="spt", count=candidates, iteration=0, blocked=()):
            nonlocal queries
            options = worker.request(nid, prices=prices, blocked=blocked, k=count,
                                     diversity=.25 if count > 1 else 0,
                                     strategy=strategy, seed=seed+iteration)
            queries += 1
            return sum(add(nid, option) for option in options)

        for nid, candidate in best.items():
            add(nid, candidate)  # The starting legal routing is always representable.
            for projected in planar_candidates(worker, nid, candidate, seed) + folded_candidates(worker, nid, candidate):
                add(nid, projected)
            generate(nid, count=initial_candidates)
            generate(nid, blocked=initial.blocked({nid}), count=8)
        if pool_path is not None and pool_path.exists():
            cached = json.loads(pool_path.read_text())
            if cached["instance"] != inst.name:
                raise ValueError("candidate cache instance mismatch")
            for item in cached["candidates"]:
                nid = item["net"]
                edges = [tuple(e) for e in item["edges"]]
                vertices = {v for edge in edges for v in edge} | {worker.pins[p] for p in worker.nets[nid].pins()}
                add(nid, dict(edges=edges, vertices=vertices, delay=item["delay"]))

        for iteration in range(rounds):
            if time.perf_counter() >= deadline:
                break
            columns, costs, eq, ub, vertices = master(pool)
            relaxed = linprog(costs, A_ub=ub, b_ub=np.ones(ub.shape[0]),
                              A_eq=eq, b_eq=np.ones(eq.shape[0]), bounds=(0, None),
                              method="highs", options={"time_limit": min(lp_seconds, max(.01, deadline-time.perf_counter()))})
            if not relaxed.success:
                row = dict(iteration=iteration, delay=best_delay, columns=len(columns),
                           lp_status=int(relaxed.status), lp_message=relaxed.message,
                           elapsed_seconds=time.perf_counter()-start)
                trace.append(row)
                if progress:
                    progress(row)
                break
            best = polish_pool(inst, pool, best, seed=seed+iteration, groups=local_groups, deadline=deadline)
            best_delay = sum(c["delay"] for c in best.values())
            constraints = [LinearConstraint(eq, 1, 1), LinearConstraint(ub, -np.inf, 1),
                           LinearConstraint(costs.reshape(1, -1), -np.inf, best_delay)]
            solve_seconds = min(mip_seconds, max(.01, deadline-time.perf_counter()))
            if warm_start:
                from mip import solve_mip
                matrix = vstack((eq,ub,csc_matrix(costs.reshape(1,-1))),format="csc")
                initial_values = [int(c["vertices"] == best[n]["vertices"]) for n,c in columns]
                lower = np.concatenate((np.ones(eq.shape[0]),np.full(ub.shape[0]+1,-np.inf)))
                upper = np.concatenate((np.ones(eq.shape[0]+ub.shape[0]),[best_delay]))
                integer = solve_mip(costs,matrix,lower,upper,initial_values,
                                       np.ones(len(columns)),solve_seconds)
            else:
                integer = milp(costs, integrality=np.ones(len(columns)), bounds=Bounds(0, 1),
                               constraints=constraints, options={"time_limit": solve_seconds,"mip_rel_gap": 1e-4})
            if integer.x is not None:
                selected = {}
                for value, (nid, candidate) in zip(integer.x, columns):
                    if value > .5:
                        if nid in selected:
                            raise RuntimeError("master selected duplicate net")
                        selected[nid] = candidate
                if set(selected) != set(best):
                    raise RuntimeError("master returned incomplete selection")
                result = check(inst, as_submission(inst, selected))
                if not result.legal:
                    raise RuntimeError("master returned an illegal routing")
                if result.total_delay < best_delay:
                    best, best_delay = selected, result.total_delay
            prices = {v: max(0., -float(price)) for v, price in zip(vertices, relaxed.ineqlin.marginals)
                      if price < -1e-8}
            added = 0
            generation_order = sorted(pool)
            if exact_prices:
                rng.shuffle(generation_order)
            for nid in generation_order:
                if time.perf_counter() >= deadline:
                    break
                # Search both a path-cost and an incremental tree-cost proxy.
                added += generate(nid, prices, iteration=iteration+1)
                added += generate(nid, prices, strategy="greedy", count=1, iteration=iteration+1)
                if exact_prices and prices:
                    added += generate(nid,prices,strategy="steiner",count=1,iteration=iteration+1)
                others = [n for n in best if n != nid]
                subset = rng.sample(others, max(1, len(others)//2)) if others else []
                blocked = {v for n in subset for v in best[n]["vertices"]}
                added += generate(nid, prices, blocked=blocked, count=1, iteration=iteration+1)
                for projected in planar_candidates(worker, nid, best[nid], seed+iteration) + folded_candidates(worker, nid, best[nid]):
                    added += add(nid, projected)
            row = dict(iteration=iteration, delay=best_delay, pool_lp_bound=float(relaxed.fun),
                       columns=len(columns), vertex_constraints=len(vertices), added=added,
                       mip_status=int(integer.status), mip_gap=float(integer.get("mip_gap")) if integer.get("mip_gap") is not None else None,
                       queries=queries, elapsed_seconds=time.perf_counter()-start)
            trace.append(row)
            if progress:
                progress(row)
            if not added:
                break
        result = as_submission(inst, best)
        verified = check(inst, result)
        if not verified.legal:
            raise RuntimeError("final joint selection is illegal")
        if pool_path is not None:
            cached = dict(instance=inst.name, candidates=[dict(net=nid, edges=c["edges"], delay=c["delay"])
                         for nid in sorted(pool) for c in pool[nid].values()])
            pool_path.parent.mkdir(parents=True, exist_ok=True)
            pool_path.write_text(json.dumps(cached, separators=(",", ":"))+"\n")
        return result, dict(initial_delay=initial.delay, total_delay=verified.total_delay,
                            improvement=initial.delay-verified.total_delay, queries=queries,
                            seconds=time.perf_counter()-start, trace=trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("benchmarks"))
    parser.add_argument("--start-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--initial-candidates", type=int, default=16)
    parser.add_argument("--candidates", type=int, default=2)
    parser.add_argument("--mip-seconds", type=float, default=15)
    parser.add_argument("--lp-seconds", type=float, default=10)
    parser.add_argument("--time-limit", type=float, default=300)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pool-dir", type=Path, help="save/reuse generated columns")
    parser.add_argument("--local-groups", type=int, default=20)
    parser.add_argument("--warm-start",action="store_true")
    parser.add_argument("--exact-prices",action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.suite / "suite.json").read_text())
    rows = []
    for case in manifest["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite / case["instance_file"]))
        sub = Submission.load(str(args.start_dir / (inst.name + ".sol.json")))
        def progress(row):
            if "lp_status" in row:
                print(f"{inst.name} LP stopped: {row['lp_message']}", flush=True)
                return
            print(f"{inst.name} joint {row['iteration']}: delay={row['delay']} "
                  f"LP={row['pool_lp_bound']:.1f} columns={row['columns']} added={row['added']} "
                  f"elapsed={row['elapsed_seconds']:.1f}s", flush=True)
        result, metrics = select(inst, sub, rounds=args.rounds, initial_candidates=args.initial_candidates,
                                 candidates=args.candidates, mip_seconds=args.mip_seconds,
                                 lp_seconds=args.lp_seconds, time_limit=args.time_limit, seed=args.seed, progress=progress,
                                 pool_path=(args.pool_dir / (inst.name + ".pool.json")) if args.pool_dir else None,
                                 local_groups=args.local_groups,warm_start=args.warm_start,
                                 exact_prices=args.exact_prices)
        result.save(str(args.out_dir / (inst.name + ".sol.json")))
        metrics.update(case=inst.name, baseline=case["baseline_total"], score=case["baseline_total"]/metrics["total_delay"])
        rows.append(metrics)
        aggregate = math.exp(sum(math.log(r["score"]) for r in rows)/len(rows))
        report = dict(config={k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()},
                      cases=rows, selected_case_score=aggregate, complete=len(rows)==manifest["n_cases"],
                      aggregate_score=aggregate if len(rows)==manifest["n_cases"] else 0)
        (args.out_dir / "report.json").write_text(json.dumps(report, indent=2)+"\n")
        print(f"Completed {len(rows)} cases: selected-case score={aggregate:.6f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
