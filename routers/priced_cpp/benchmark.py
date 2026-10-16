#!/usr/bin/env python3
"""Controlled ablations with official checker/score and saved per-iteration metrics."""
import argparse
import csv
import json
import math
from pathlib import Path
import platform
import time

from controller import solve
from route import Instance
from m3d.checker import check
from m3d.negotiated import route_negotiated

METHODS = {
    "k1_history": dict(k=1, history=2.0, refinement_passes=0),
    "k4_present": dict(k=4, history=0.0, refinement_passes=0),
    "k4_history_refine": dict(k=4, history=2.0, refinement_passes=1),
    "adaptive_history_refine": dict(k=4, negotiation_k=1, history=2.0, refinement_passes=1),
}


def aggregate(rows, delay_key="total_delay", expected_cases=None):
    total = len(rows) if expected_cases is None else expected_cases
    legal = [r for r in rows if r["legal"] and r.get(delay_key)]
    complete = len(legal) == total and total > 0
    return {"legal_cases": len(legal), "total_cases": total,
            "completed_cases": len(rows), "complete": complete,
            "aggregate_score": math.exp(sum(math.log(r["baseline_delay"] / r[delay_key])
                                          for r in legal) / total) if complete else 0.0,
            "total_delay": sum(r[delay_key] for r in legal) if complete else None,
            "total_end_to_end_seconds": sum(r["end_to_end_seconds"] for r in rows),
            "max_iterations": max((r["iterations"] for r in rows), default=0),
            "initial_conflict_vertices": (sum(r["initial_conflict_vertices"] for r in rows)
                if all(r["initial_conflict_vertices"] is not None for r in rows) else None),
            "final_conflict_vertices": (sum(r["final_conflict_vertices"] for r in rows)
                if all(r["final_conflict_vertices"] is not None for r in rows) else None)}


def stage_summary(rows, method_dir, expected_cases):
    """Derive phase totals from saved traces, preserving incomplete-run status."""
    summary = dict(independent_seconds=0, negotiation_seconds=0, refinement_seconds=0,
                   initial_diagnostic_delay=0, accepted_refinements=0, queries=0,
                   candidates=0, refinement_evaluations=0, complete_refinement_pass_cases=0,
                   budget_exhausted_cases=[])
    for row in rows:
        metrics = json.loads((method_dir / (row["case"] + ".metrics.json")).read_text())
        trace = metrics["trace"]
        if not trace:
            return None  # The existing Python comparator does not expose phase traces.
        initial, final = trace[0], trace[-1]
        legal = next((r for r in trace if r["stage"] == "legal"), None)
        boundary = legal or final
        summary["independent_seconds"] += initial["elapsed_seconds"]
        summary["negotiation_seconds"] += boundary["elapsed_seconds"] - initial["elapsed_seconds"]
        summary["refinement_seconds"] += final["elapsed_seconds"] - boundary["elapsed_seconds"]
        summary["initial_diagnostic_delay"] += initial["diagnostic_delay"]
        summary["accepted_refinements"] += metrics["refinement_improvements"]
        summary["queries"] += metrics["candidate_queries"]
        summary["candidates"] += metrics["generated_candidates"]
        summary["refinement_evaluations"] += metrics.get("refinement_nets_evaluated", 0)
        summary["complete_refinement_pass_cases"] += metrics.get("refinement_passes_completed", 0) > 0
        if metrics.get("budget_exhausted", False):
            summary["budget_exhausted_cases"].append(row["case"])
    before = aggregate(rows, "negotiation_delay", expected_cases)
    after = aggregate(rows, expected_cases=expected_cases)
    summary.update(legal_cases=after["legal_cases"], total_cases=expected_cases,
                   negotiation_delay=before["total_delay"], negotiation_score=before["aggregate_score"],
                   final_delay=after["total_delay"], final_score=after["aggregate_score"],
                   geometric_delay_reduction_percent=(100 * (1 - 1 / after["aggregate_score"])
                                                      if after["complete"] else None),
                   refinement_total_delay_reduction_percent=(100 * (1 - after["total_delay"] / before["total_delay"])
                                                             if after["complete"] and before["complete"] else None))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("benchmarks"))
    parser.add_argument("--cases", nargs="+", help="subset of manifest case names")
    parser.add_argument("--methods", nargs="+", choices=list(METHODS) + ["negotiated"],
                        default=list(METHODS))
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--max-iters", type=int, default=60)
    parser.add_argument("--out-dir", type=Path, default=Path("build/priced_cpp_benchmark"))
    args = parser.parse_args()
    manifest = json.loads((args.suite / "suite.json").read_text())
    cases = [c for c in manifest["cases"] if args.cases is None or c["name"] in args.cases]
    if not cases or (args.cases and set(args.cases) - {c["name"] for c in cases}):
        parser.error("unknown or empty case selection")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    report = {"suite": str(args.suite), "cases": [c["name"] for c in cases],
              "hardware": platform.platform() + " / " + platform.machine(),
              "threads": 1, "time_limit_per_controller_case": args.time_limit,
              "max_iters": args.max_iters, "configs": METHODS,
              "runtime_note": "Wall time includes input loading, routing, official validation, and solution-file writing; metrics/report serialization excluded. Negotiated is iteration-limited, not time-limited.",
              "rows": rows, "summaries": {}}

    def save():
        summaries = {method: aggregate([r for r in rows if r["method"] == method], expected_cases=len(cases))
                     for method in args.methods if any(r["method"] == method for r in rows)}
        for method in ("k4_history_refine", "adaptive_history_refine"):
            if any(r["method"] == method for r in rows):
                summaries[method.replace("_refine", "_before_refinement")] = aggregate(
                    [r for r in rows if r["method"] == method], "negotiation_delay", len(cases))
        report["summaries"] = summaries
        (args.out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if rows:
            with (args.out_dir / "cases.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

    for method in args.methods:
        method_dir = args.out_dir / method
        method_dir.mkdir(exist_ok=True)
        runtimes = {}
        for case in cases:
            start = time.perf_counter()
            inst = Instance.load(str(args.suite / case["instance_file"]))
            if method == "negotiated":
                submission, stats = route_negotiated(inst, max_iters=args.max_iters)
                metrics = {"iterations": stats.iterations, "trace": [],
                           "negotiation_delay": None, "generated_candidates": 0,
                           "candidate_queries": 0, "refinement_improvements": 0,
                           "mean_alternative_vertex_jaccard": None,
                           "termination": "legal" if stats.success else "iteration_limit"}
            else:
                submission, metrics = solve(inst, max_iters=args.max_iters,
                                            time_limit=args.time_limit, **METHODS[method])
            result = check(inst, submission) if submission is not None else None
            legal = result is not None and result.legal
            sol_path = method_dir / f"{inst.name}.sol.json"
            if legal:
                submission.save(str(sol_path))
            elif sol_path.exists():
                sol_path.unlink()  # Remove this harness's stale output from a previous run.
            runtime = time.perf_counter() - start
            runtimes[inst.name] = runtime
            trace = metrics["trace"]
            row = {"method": method, "case": inst.name, "legal": legal,
                   "baseline_delay": case["baseline_total"],
                   "total_delay": result.total_delay if legal else None,
                   "negotiation_delay": metrics["negotiation_delay"],
                   "case_score": case["baseline_total"] / result.total_delay if legal else None,
                   "end_to_end_seconds": runtime, "iterations": metrics["iterations"],
                   "initial_conflict_vertices": trace[0]["conflict_vertices"] if trace else None,
                   "final_conflict_vertices": trace[-1]["conflict_vertices"] if trace else (0 if legal else None),
                   "candidate_queries": metrics["candidate_queries"],
                   "generated_candidates": metrics["generated_candidates"],
                   "mean_alternative_vertex_jaccard": metrics["mean_alternative_vertex_jaccard"],
                   "refinement_improvements": metrics["refinement_improvements"],
                   "termination": metrics["termination"]}
            rows.append(row)
            metrics["end_to_end_seconds"] = runtime
            (method_dir / f"{inst.name}.metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
            (method_dir / "runtime.json").write_text(json.dumps(runtimes, indent=2) + "\n")
            save()
            print(f"{method} {inst.name}: legal={legal} delay={row['total_delay']} "
                  f"score={row['case_score']} runtime={runtime:.3f}s iters={row['iterations']} "
                  f"conflicts={row['initial_conflict_vertices']}->{row['final_conflict_vertices']}", flush=True)
        summary = report["summaries"][method]
        print(f"SUMMARY {method}: {summary}", flush=True)
    stages = {}
    for method in args.methods:
        summary = stage_summary([r for r in rows if r["method"] == method], args.out_dir / method, len(cases))
        if summary is not None:
            stages[method] = summary
    report["stage_summaries"] = stages
    save()
    (args.out_dir / "stage_summary.json").write_text(
        json.dumps(next(iter(stages.values())) if len(stages) == 1 else stages, indent=2) + "\n")
    print(f"Reports: {args.out_dir / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
