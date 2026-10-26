#!/usr/bin/env python3
"""Reproducible from-scratch priced routing and refinement, with phase metrics."""
import argparse
import json
import math
from pathlib import Path
import time

from controller import solve
from improve import improve
from native import optimize
from route import Instance
from m3d.checker import check
from m3d.scorer import leaderboard, score_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("benchmarks"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dual-dir", type=Path,
                        help="lpbound case_NAME.alpha.npz files; seeds congestion routing and native refinement")
    parser.add_argument("--routing-seconds", type=float, default=600)
    parser.add_argument("--present", type=float, default=64)
    parser.add_argument("--history", type=float, default=4)
    parser.add_argument("--growth", type=float, default=1)
    parser.add_argument("--fanout-scale", action="store_true",
                        help="divide a net's congestion prices by its sink count, so high-fanout nets keep the fast layers")
    parser.add_argument("--max-iters", type=int, default=1000)
    parser.add_argument("--refinement-seconds", type=float, default=180)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--native-seconds", type=float, default=0)
    parser.add_argument("--native-iterations", type=int, default=100000)
    parser.add_argument("--native-temperature", type=float, default=60)
    parser.add_argument("--exact-prices", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if not math.isfinite(args.native_seconds) or args.native_seconds < 0:
        parser.error("--native-seconds must be finite and nonnegative")
    if args.native_iterations < 1:
        parser.error("--native-iterations must be positive")
    if not math.isfinite(args.native_temperature) or args.native_temperature < 0:
        parser.error("--native-temperature must be finite and nonnegative")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.suite/"suite.json").read_text())
    selected = [c for c in manifest["cases"] if args.cases is None or c["name"] in args.cases]
    if not selected or (args.cases and set(args.cases)-{c["name"] for c in selected}):
        parser.error("unknown or empty case selection")
    scores, rows, runtimes = [], [], {}
    for case in selected:
        start = time.perf_counter()
        inst = Instance.load(str(args.suite/case["instance_file"]))
        dual = None
        if args.dual_dir is not None:
            from dual import load
            dual = load(inst, args.dual_dir / (inst.name + ".alpha.npz"))
        def routing_progress(row):
            print(f"{inst.name} {row['stage']} {row['iteration']}: "
                  f"conflicts={row['conflict_vertices']} delay={row['diagnostic_delay']} "
                  f"elapsed={row['elapsed_seconds']:.2f}s", flush=True)
        sub, routing = solve(inst, strategy="steiner" if args.exact_prices else "spt", k=1,
            negotiation_k=1, present=args.present, history=args.history,
            growth=args.growth, max_iters=args.max_iters,
            refinement_passes=0, time_limit=args.routing_seconds, seed=args.seed,
            progress=routing_progress, dual=dual, fanout_scale=args.fanout_scale)
        refinement = None
        if sub is not None:
            def refinement_progress(row):
                print(f"{inst.name} {row['stage']} {row['iteration']}: "
                      f"best_delay={row['delay']} working_delay={row['working_delay']} "
                      f"elapsed={row['elapsed_seconds']:.2f}s", flush=True)
            sub, refinement = improve(inst, sub, passes=2, pair_rounds=2, triple_rounds=1,
                cbs_nodes=16, lns_rounds=args.rounds, group_limit=25, neighborhood_size=16,
                order_retries=4, local_iters=20, temperature=8, regional=True,
                guided_rounds=8, exact_prices=args.exact_prices, seed=args.seed,
                time_limit=args.refinement_seconds, progress=refinement_progress)
        native = None
        if sub is not None and args.native_seconds > 0:
            def native_progress(row):
                print(f"{inst.name} native {row['iteration']}: "
                      f"best_delay={row['best_delay']} working_delay={row['working_delay']} "
                      f"elapsed={row['seconds']:.2f}s", flush=True)
            sub, native = optimize(inst, sub, seconds=args.native_seconds,
                iterations=args.native_iterations, temperature=args.native_temperature,
                seed=args.seed, progress=native_progress, dual=dual)
        result = check(inst, sub) if sub is not None else None
        if result is not None and not result.legal:
            raise RuntimeError("pipeline produced illegal output")
        output = args.out_dir/(inst.name+".sol.json")
        if sub is not None:
            sub.save(str(output))
            scores.append(score_case(inst,sub,case["baseline_total"]))
        elif output.exists():
            output.unlink()  # No stale successful file on a failed rerun.
        seconds = time.perf_counter()-start
        runtimes[inst.name] = seconds
        row = dict(case=inst.name, legal=sub is not None, total_delay=result.total_delay if result else None,
                   seconds=seconds, routing=routing, refinement=refinement, native=native)
        rows.append(row)
        report = dict(config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                      completed_cases=len(rows), selected_cases=len(selected), cases=rows,
                      aggregate_score=leaderboard(scores).aggregate_score if len(scores)==len(selected) else 0,
                      complete=len(scores)==len(selected), full_tier=len(selected)==manifest["n_cases"],
                      runtime_note="Per-case input loading through final validation and route writing; report serialization excluded.")
        (args.out_dir/"report.json").write_text(json.dumps(report,indent=2)+"\n")
        (args.out_dir/"runtime.json").write_text(json.dumps(runtimes,indent=2)+"\n")
        print(f"FINISHED {inst.name}: legal={row['legal']} delay={row['total_delay']} "
              f"seconds={seconds:.2f} completed={len(rows)}/{len(selected)}", flush=True)
    return 0 if all(row["legal"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
