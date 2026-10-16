#!/usr/bin/env python3
"""Keep independently verified best case files and their local-run provenance."""
import argparse
import json
import math
from pathlib import Path
import shutil

from route import Instance, Submission
from m3d.checker import check
from m3d.scorer import leaderboard, score_case


def collect(suite, sources, out_dir, bounds=None, reference_dir=None):
    manifest = json.loads((suite / "suite.json").read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_path = out_dir / "report.json"
    previous = json.loads(previous_path.read_text()).get("provenance", {}) if previous_path.exists() else {}
    scores, provenance = [], {}
    for case in manifest["cases"]:
        inst = Instance.load(str(suite / case["instance_file"]))
        best = None
        for source in [out_dir] + sources:
            path = source / (inst.name + ".sol.json")
            if not path.exists():
                continue
            sub = Submission.load(str(path))
            result = check(inst, sub)
            if not result.legal:
                raise ValueError(f"illegal candidate: {path}")
            if best is None or result.total_delay < best[0]:
                best = (result.total_delay, path, sub)
        if best is None:
            raise ValueError(f"no legal solution for {inst.name}")
        delay, path, sub = best
        target = out_dir / path.name
        if path != target:
            shutil.copyfile(path, target)
        source_path = previous.get(inst.name, {}).get("source", str(path)) if path == target else str(path)
        provenance[inst.name] = dict(delay=delay, source=source_path)
        scores.append(score_case(inst, sub, case["baseline_total"]))
    result = leaderboard(scores)
    report = result.to_dict() | dict(provenance=provenance,
                                    runtime_note="Best-of-experiments snapshot; not an end-to-end runtime claim.")
    for row in report["cases"]:
        name = row["case"]
        if bounds is not None:
            bound = float(bounds[name])
            if not math.isfinite(bound) or bound <= 0 or bound > row["total_delay"]:
                raise ValueError(f"invalid lower bound for {name}")
            row.update(lower_bound=bound,gap_to_bound_percent=100*(row["total_delay"]-bound)/bound)
        if reference_dir is not None:
            case = next(c for c in manifest["cases"] if c["name"] == name)
            inst = Instance.load(str(suite/case["instance_file"]))
            ref = check(inst,Submission.load(str(reference_dir/(name+".sol.json"))))
            if not ref.legal:
                raise ValueError(f"illegal reference for {name}")
            row.update(reference_delay=ref.total_delay,
                       improvement_percent=100*(ref.total_delay-row["total_delay"])/ref.total_delay)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("benchmarks"))
    parser.add_argument("--sources", nargs="+", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("build/priced_cpp_best"))
    parser.add_argument("--bounds",type=Path,help="JSON mapping case names to certified lower bounds")
    parser.add_argument("--reference-dir",type=Path,help="comparison routes for per-case improvement metrics")
    args = parser.parse_args()
    bounds = json.loads(args.bounds.read_text()) if args.bounds else None
    result = collect(args.suite, args.sources, args.out_dir,bounds,args.reference_dir)
    print(f"legal={result.n_legal}/{result.n_cases} score={result.aggregate_score:.9f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
