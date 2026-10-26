#!/usr/bin/env python3
"""One timed from-scratch run per case: the configuration used for submissions.

Per case, inside one wall-clock measurement (instance loading through the
checked, written route file):

1. Gentle negotiated routing (present 0.3, history 0.5, growth 1.05, exact
   priced trees) from `--starts` seeds, keeping the lowest legal delay.
2. A short Python refinement of that routing.
3. The native annealer with neighbor polish, max group 12, temperature 10,
   20000-move cycles, and negotiated group repair on 30% of moves.

No published routes or external prices are read. Writes <case>.sol.json and
<case>.run.json to --out-dir; `--merge` collects runtime.json and report.json
once every case is done (cases may run as separate processes).

    PYTHONPATH=. python3 routers/priced_cpp/submit_run.py --suite benchmarks_hard \\
        --out-dir build/submit_hard --native-seconds 7200 --seed 1
"""
import argparse
import json
import multiprocessing
from pathlib import Path
import time

from controller import solve
from improve import improve
from native import optimize
from route import Instance
from m3d.checker import check


# Native exact-negotiation settings (present, growth, history) cycled while screening
# starts; all gave similar means on hard case_07 with a wide per-seed spread.
SCREEN_SETTINGS = [(0.1, 1.1, 0.5), (0.3, 1.1, 1.0), (1.0, 1.02, 0.5), (0.3, 1.05, 0.5)]


def screen(inst, args, seed):
    """Native from-scratch negotiations for --screen-seconds; returns the lowest-delay start."""
    began, found, k = time.perf_counter(), [], 0
    while not found or time.perf_counter()-began < args.screen_seconds:
        present, growth, history = SCREEN_SETTINGS[k % len(SCREEN_SETTINGS)]
        try:
            sub, _ = optimize(inst, None, seconds=0.001, iterations=1, temperature=0, seed=seed*1000+k, max_group=12,
                              negotiate=(args.negotiate_pct, 20, 0.5, 1.3, 0.5),
                              scratch=(5000, present, growth, history), binary=args.binary)
            found.append((check(inst, sub).total_delay, k, sub))
        except RuntimeError:
            pass
        k += 1
        if k > 10000:
            raise RuntimeError("no native start converged")
    found.sort(key=lambda t: (t[0], t[1]))
    return found


def pipeline(inst, args, seed, fanout_scale, seconds):
    """Negotiated starts, Python refinement and the native annealer from one seed."""
    starts = []
    if args.screen_seconds > 0:
        found = screen(inst, args, seed)
        starts = [found[0]]
        screened = [d for d, _, _ in found]
    for k in range(args.starts if args.screen_seconds <= 0 else 0):
        sub, _ = solve(inst, strategy="steiner", k=1, negotiation_k=1, present=args.present, history=args.history,
                       growth=args.growth, max_iters=args.max_iters, refinement_passes=0, fanout_scale=fanout_scale,
                       time_limit=args.routing_seconds, seed=seed*1000+k)
        if sub is not None:
            starts.append((check(inst, sub).total_delay, k, sub))
    if not starts:
        return None
    negotiated = [d for d, _, _ in sorted(starts, key=lambda t: t[1])]
    starts.sort(key=lambda t: (t[0], t[1]))
    routed_delay, best_start, sub = starts[0]
    sub, _ = improve(inst, sub, passes=2, pair_rounds=1, triple_rounds=0, cbs_nodes=16, lns_rounds=1,
                     group_limit=25, neighborhood_size=16, order_retries=4, local_iters=20, temperature=8,
                     regional=True, guided_rounds=2, exact_prices=True, seed=seed,
                     time_limit=args.refinement_seconds)
    refined_delay = check(inst, sub).total_delay
    sub, native = anneal(inst, sub, args, seed, seconds)
    return dict(sub=sub, total_delay=check(inst, sub).total_delay, seed=seed, fanout_scale=fanout_scale,
                negotiated=negotiated if args.screen_seconds <= 0 else screened, best_start=best_start, routed_delay=routed_delay, refined_delay=refined_delay,
                native=native)


def anneal(inst, sub, args, seed, seconds):
    sub, native = optimize(inst, sub, seconds=seconds, iterations=10**9, temperature=args.temperature,
                           seed=seed, max_group=args.max_group, neighbor_polish=True,
                           negotiate=(args.negotiate_pct, 20, 0.5, 1.3, 0.5), cycle=args.cycle, binary=args.binary,
                           fanout_bias=args.fanout_bias)
    return sub, dict(iterations=native["iterations"], accepted=native["accepted"], log=native["native_log"])


def _explore(job):
    inst, args, seed, fanout_scale = job
    return pipeline(inst, args, seed, fanout_scale, args.explore_seconds)


def _continue(job):
    inst, args, sub, seed = job
    sub, native = anneal(inst, sub, args, seed, args.native_seconds)
    return dict(sub=sub, total_delay=check(inst, sub).total_delay, seed=seed, native=native)


def run_case(suite, case, args):
    start = time.perf_counter()
    inst = Instance.load(str(suite/case["instance_file"]))
    if args.workers <= 1:
        found = pipeline(inst, args, args.seed, args.fanout_scale, args.native_seconds)
        if found is None:
            raise RuntimeError(f"{inst.name}: no legal negotiated routing")
        sub, phases = found["sub"], {"explore": [{k: v for k, v in found.items() if k != "sub"}]}
    else:
        # Portfolio: independent pipelines land in different basins, which matters far
        # more than annealing time; keep the best, then anneal it with fresh chains.
        seeds = [args.seed*100+w for w in range(args.workers)]
        with multiprocessing.get_context("fork").Pool(args.workers) as pool:
            explored = [r for r in pool.map(_explore, [(inst, args, s, bool(w % 2) and args.mix_fanout)
                                                       for w, s in enumerate(seeds)], chunksize=1) if r]
            if not explored:
                raise RuntimeError(f"{inst.name}: no legal negotiated routing")
            best = min(explored, key=lambda r: (r["total_delay"], r["seed"]))
            continued = pool.map(_continue, [(inst, args, best["sub"], s+50) for s in seeds], chunksize=1)
        final = min(continued + [best], key=lambda r: r["total_delay"])
        sub = final["sub"]
        phases = {"explore": [{k: v for k, v in r.items() if k != "sub"} for r in explored],
                  "continue": [{k: v for k, v in r.items() if k != "sub"} for r in continued]}
    result = check(inst, sub)
    if not result.legal:
        raise RuntimeError(f"{inst.name}: illegal output")
    sub.save(str(args.out_dir/(inst.name+".sol.json")))
    seconds = time.perf_counter()-start
    return dict(case=inst.name, total_delay=result.total_delay, seconds=seconds, workers=args.workers, phases=phases)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--starts", type=int, default=3)
    parser.add_argument("--routing-seconds", type=float, default=900)
    parser.add_argument("--max-iters", type=int, default=5000)
    parser.add_argument("--present", type=float, default=0.3)
    parser.add_argument("--history", type=float, default=0.5)
    parser.add_argument("--growth", type=float, default=1.05)
    parser.add_argument("--fanout-scale", action="store_true", help="divide congestion prices by sink count while negotiating")
    parser.add_argument("--refinement-seconds", type=float, default=30)
    parser.add_argument("--native-seconds", type=float, default=3600)
    parser.add_argument("--temperature", type=float, default=10)
    parser.add_argument("--max-group", type=int, default=12)
    parser.add_argument("--cycle", type=int, default=20000)
    parser.add_argument("--negotiate-pct", type=int, default=30)
    parser.add_argument("--workers", type=int, default=1, help="parallel pipelines per case (portfolio)")
    parser.add_argument("--explore-seconds", type=float, default=1200, help="annealing per portfolio pipeline")
    parser.add_argument("--mix-fanout", action="store_true", help="odd portfolio workers negotiate with --fanout-scale")
    parser.add_argument("--screen-seconds", type=float, default=0,
                        help="per pipeline, screen native from-scratch starts this long and keep the best (replaces --starts)")
    parser.add_argument("--fanout-bias", type=float, help="weight extra on 5+ pin nets while hot (fades out)")
    parser.add_argument("--binary", type=Path, help="native optimizer binary")
    parser.add_argument("--merge", action="store_true", help="only collect runtime.json and report.json")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.suite/"suite.json").read_text())
    if args.merge:
        rows = {c["name"]: json.loads((args.out_dir/(c["name"]+".run.json")).read_text()) for c in manifest["cases"]}
        (args.out_dir/"runtime.json").write_text(json.dumps({k: round(r["seconds"], 3) for k, r in rows.items()}, indent=1)+"\n")
        (args.out_dir/"report.json").write_text(json.dumps(rows, indent=1)+"\n")
        print("merged", len(rows), "cases")
        return 0
    for case in manifest["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        row = run_case(args.suite, case, args)
        row["config"] = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
        (args.out_dir/(row["case"]+".run.json")).write_text(json.dumps(row, indent=1)+"\n")
        explored = [r["total_delay"] for r in row["phases"]["explore"]]
        print(f"FINISHED {row['case']}: explored={explored} final={row['total_delay']} seconds={row['seconds']:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
