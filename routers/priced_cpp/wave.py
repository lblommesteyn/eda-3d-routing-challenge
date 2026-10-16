#!/usr/bin/env python3
"""Whole-instance price waves with legal primal recovery and best-state retention."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import time

from controller import NativeSession, as_submission
from improve import Refiner
from route import Instance, Submission
from m3d.checker import check


def optimize(inst, submission, *, cycles=20, repairs=12, seed=0, seconds=180):
    if any(type(v) is not int or v < 1 for v in (cycles,repairs)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("positive cycle/repair counts and time limit required")
    start, rng = time.perf_counter(), random.Random(seed)
    deadline, trace = start+seconds, []
    with NativeSession(inst) as worker:
        initial = Refiner(inst,submission,worker)
        best, best_delay = dict(initial.routes), initial.delay
        routes = dict(best)
        owners, prices = defaultdict(set), {}
        for nid,candidate in routes.items():
            for v in candidate["vertices"]:
                owners[v].add(nid)

        def remove(nid):
            old = routes[nid]
            for v in old["vertices"]:
                owners[v].remove(nid)
                if not owners[v]:
                    del owners[v]
            return old

        def add(nid,candidate):
            routes[nid] = candidate
            for v in candidate["vertices"]:
                owners[v].add(nid)

        def query(nid, penalty, iteration):
            options = worker.request(nid, prices=penalty, strategy="steiner" if penalty else "compact", k=1,
                                     diversity=0, seed=seed+iteration)
            if not options:
                raise RuntimeError("soft-priced net became unreachable")
            return options[0]

        for cycle in range(cycles):
            if time.perf_counter() >= deadline:
                break
            # Regenerate all nets, including currently unconflicted ones, so
            # they can reclaim cheap paths after earlier contention has moved.
            order = sorted(routes)
            rng.shuffle(order)
            for nid in order:
                if time.perf_counter() >= deadline:
                    break
                remove(nid)
                add(nid,query(nid,prices,cycle))
            overuse = {v:ns for v,ns in owners.items() if len(ns)>1}
            step = 1.0 / math.sqrt(1+cycle)
            for v in set(prices) | set(owners):
                value = max(0., prices.get(v,0)+step*(len(owners.get(v,()))-1))
                if value > 1e-8:
                    prices[v] = value
                else:
                    prices.pop(v,None)
            # Prices on unused resources can fall, unlike monotone history.
            # This is heuristic price guidance, not a certified dual bound.
            history, present = defaultdict(float), 1.0
            iterations = 0
            for iteration in range(repairs):
                if not overuse or time.perf_counter() >= deadline:
                    break
                affected = set().union(*overuse.values())
                for v,ns in overuse.items():
                    history[v] += .5*(len(ns)-1)
                order = sorted(affected)
                rng.shuffle(order)
                for nid in order:
                    if time.perf_counter() >= deadline:
                        break
                    old = remove(nid)
                    penalty = dict(prices)
                    for v,value in history.items():
                        penalty[v] = penalty.get(v,0)+value
                    for v,ns in owners.items():
                        penalty[v] = penalty.get(v,0)+present*len(ns)
                    candidate = query(nid,penalty,cycle+iteration)
                    old_cost = old["delay"]+sum(penalty.get(v,0) for v in old["vertices"])
                    add(nid,candidate if candidate["priced_cost"] <= old_cost else old)
                overuse = {v:ns for v,ns in owners.items() if len(ns)>1}
                present = min(10000,present*1.5)
                iterations += 1
            legal_delay = None
            if not overuse:
                result = check(inst,as_submission(inst,routes))
                if not result.legal:
                    raise RuntimeError("price wave produced an invalid primal solution")
                legal_delay = result.total_delay
                if legal_delay < best_delay:
                    best, best_delay = dict(routes), legal_delay
            trace.append(dict(cycle=cycle, legal_delay=legal_delay, best_delay=best_delay,
                              conflicts=len(overuse), repair_iterations=iterations,
                              elapsed_seconds=time.perf_counter()-start))
            print(f"{inst.name} wave {cycle}: legal_delay={legal_delay} best={best_delay} "
                  f"conflicts={len(overuse)} elapsed={trace[-1]['elapsed_seconds']:.2f}s",flush=True)
        # Polish the best state, never the potentially conflicting working state.
        final = Refiner(inst,as_submission(inst,best),worker,deadline,exact_prices=True)
        final.initialize_bounds()
        if time.perf_counter() < deadline:
            final.singles(seed,equal_moves=True)
        result = as_submission(inst,final.best_routes)
        verified = check(inst,result)
        if not verified.legal:
            raise RuntimeError("final price-wave result is illegal")
        return result,dict(initial_delay=initial.delay,total_delay=verified.total_delay,
                           seconds=time.perf_counter()-start,trace=trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite",type=Path,default=Path("benchmarks"))
    parser.add_argument("--start-dir",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--cases",nargs="+")
    parser.add_argument("--cycles",type=int,default=20)
    parser.add_argument("--repairs",type=int,default=12)
    parser.add_argument("--seconds",type=float,default=180)
    parser.add_argument("--seed",type=int,default=0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True,exist_ok=True)
    manifest = json.loads((args.suite/"suite.json").read_text())
    rows = []
    for case in manifest["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite/case["instance_file"]))
        sub = Submission.load(str(args.start_dir/(inst.name+".sol.json")))
        result,metrics = optimize(inst,sub,cycles=args.cycles,repairs=args.repairs,seed=args.seed,seconds=args.seconds)
        result.save(str(args.out_dir/(inst.name+".sol.json")))
        metrics.update(case=inst.name,score=case["baseline_total"]/metrics["total_delay"])
        rows.append(metrics)
        aggregate = math.exp(sum(math.log(r["score"]) for r in rows)/len(rows))
        (args.out_dir/"report.json").write_text(json.dumps(dict(cases=rows,selected_case_score=aggregate,
            aggregate_score=aggregate if len(rows)==manifest["n_cases"] else 0,
            config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}),indent=2)+"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
