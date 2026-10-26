#!/usr/bin/env python3
"""Validated JSON bridge to the native neighborhood optimizer."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

from route import Grid, Instance, Submission
from m3d.model import NetRoute
from m3d.checker import check

BINARY = Path(__file__).resolve().parent/"build"/"optimizer"


def optimize(inst, submission, *, iterations=10000, seconds=120, temperature=20,
             seed=0, max_group=24, accurate_prices=False, progress=None, warm=True,
             binary=None, compact_growth=False, neighbor_polish=False, dual=None, negotiate=None, cycle=None, margin=None, fanout_order=None, ideal=None, kick=None, scratch=None, exact_negotiation=False, graft=None, graft_delays=None, fanout_bias=None):
    if type(iterations) is not int or iterations < 1 or type(max_group) is not int or max_group < 2:
        raise ValueError("positive iteration count and group size >=2 required")
    if not math.isfinite(seconds) or seconds <= 0 or not math.isfinite(temperature) or temperature < 0:
        raise ValueError("invalid time limit or temperature")
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("seed must be a nonnegative 63-bit integer")
    if accurate_prices and compact_growth:
        raise ValueError("choose either exact prices or compact growth")
    start = time.perf_counter()
    executable = Path(binary) if binary is not None else BINARY
    binary_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    if submission is None:
        # from-scratch construction: empty routes, the binary negotiates every net
        if scratch is None:
            raise ValueError("a submission or scratch settings are required")
        submission = Submission(inst.name,[NetRoute(n.id,[]) for n in inst.nets])
        delays = {n.id:0 for n in inst.nets}
        initial_delay = None
    elif graft is not None:
        # hybrid input: routes of the nets in `graft` come from another solution, so
        # the whole may conflict; per-net delays are supplied by the caller
        delays = dict(graft_delays)
        initial_delay = None
    else:
        checked = check(inst,submission)
        if not checked.legal:
            raise ValueError("native refinement requires a fully legal input")
        delays = {r.net:r.delay for r in checked.nets}
        initial_delay = checked.total_delay
    grid = Grid(inst)
    pins = {p.id:grid.vid(p.vertex()) for p in inst.pins}
    owners = {pins[p]:n.id for n in inst.nets for p in n.pins()}
    initial_routes = {r.net:r for r in submission.routes}
    lines = [f"{inst.width} {inst.height} {inst.layers} {inst.via_delay}",
             " ".join(map(str,inst.layer_delay)),str(len(owners))]
    lines.extend(f"{v} {nid}" for v,nid in sorted(owners.items()))
    mode = 2 if compact_growth else int(accurate_prices)
    if neighbor_polish:
        mode |= 4  # Optional polishing bit; lower two bits select the oracle.
    if dual is not None:
        if len(dual.capacity) != inst.width*inst.height*inst.layers or set(dual.bids) != {n.id for n in inst.nets}:
            raise ValueError("dual prices do not match instance grid/nets")
        mode |= 8
    if negotiate is not None:
        # (percent of moves, rounds, initial present, present growth, history step)
        mode |= 16
    if cycle is not None:
        mode |= 32
    if margin is not None:
        mode |= 64
    if fanout_order is not None:
        mode |= 128
    if ideal is not None:
        mode |= 256
    if kick is not None:
        mode |= 512
    if scratch is not None:
        mode |= 1024
    if exact_negotiation:
        mode |= 2048
    if graft is not None:
        mode |= 4096
    if fanout_bias is not None:
        mode |= 8192
    header = f"{len(inst.nets)} {iterations} {seconds} {temperature} {seed} {max_group} {mode} {int(warm)}"
    if negotiate is not None:
        pct, rounds, present, growth, history = negotiate
        header += f" {int(pct)} {int(rounds)} {float(present)!r} {float(growth)!r} {float(history)!r}"
    if cycle is not None:
        header += f" {int(cycle)}"
    if margin is not None:
        header += f" {int(margin)}"
    if fanout_order is not None:
        header += f" {int(fanout_order)}"
    if ideal is not None:
        header += f" {int(ideal[0])} {int(ideal[1])}"
    if kick is not None:
        header += f" {int(kick[0])} {int(kick[1])} {float(kick[2])!r} {float(kick[3])!r}"
    if scratch is not None:
        rounds, present, growth, history = scratch
        header += f" {int(rounds)} {float(present)!r} {float(growth)!r} {float(history)!r}"
    if fanout_bias is not None:
        header += f" {float(fanout_bias)!r}"
    if graft is not None:
        index = {n.id:i for i,n in enumerate(inst.nets)}
        header += f" {len(graft)} " + " ".join(str(index[n]) for n in graft)
    lines.append(header)
    if dual is not None:
        occupied = [v for v,cost in enumerate(dual.capacity) if cost > 0]
        lines.append(str(len(occupied)))
        lines.extend(f"{v} {dual.capacity[v]:.17g}" for v in occupied)
    for net in inst.nets:
        route = initial_routes[net.id]
        lines.append(f"{net.id} {len(net.pins())} {delays[net.id]} {len(route.edges)}")
        lines.append(" ".join(str(pins[p]) for p in net.pins()))
        lines.extend(f"{grid.vid(a)} {grid.vid(b)}" for a,b in route.edges)
        if dual is not None:
            vv,aa = dual.bids[net.id]
            lines.append(str(len(vv)))
            lines.extend(f"{v} {cost:.17g}" for v,cost in zip(vv,aa))
    trace = []
    process = subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,text=True,bufsize=1)
    try:
        process.stdin.write("\n".join(lines)+"\n")
        process.stdin.close()
        while True:
            line = process.stdout.readline().split()
            if not line:
                raise RuntimeError("native optimizer stopped: "+process.stderr.read())
            if line[0] == "R":
                _, count, best, performed, accepted, improvements, elapsed = line[:7]
                repair_metrics = dict(failed_repairs=int(line[7]),uphill_proposals=int(line[8])) if len(line)==9 else {}
                break
            if line[0] != "P":
                raise RuntimeError("unexpected native optimizer response")
            _, iteration, accepted_moves, best_delay, working_delay, elapsed = line
            row = dict(iteration=int(iteration),accepted=int(accepted_moves),best_delay=int(best_delay),
                       working_delay=int(working_delay),seconds=float(elapsed))
            trace.append(row)
            if progress:
                progress(row)
        routes, reported = [], {}
        for _ in range(int(count)):
            nid,delay,edges = map(int,process.stdout.readline().split())
            reported[nid] = delay
            decoded = []
            for _ in range(edges):
                a,b = map(int,process.stdout.readline().split())
                decoded.append((grid.coord(a),grid.coord(b)))
            routes.append(NetRoute(nid,decoded))
        process.wait(timeout=10)
        native_log = process.stderr.read().strip()
        if process.returncode:
            raise RuntimeError(native_log)
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()
    result = Submission(inst.name,routes)
    verified = check(inst,result)
    if not verified.legal or verified.total_delay != int(best):
        bad = [(n.net, n.reasons) for n in verified.nets if not n.legal][:5]
        bad += [(n.net, f"native {reported.get(n.net)} checker {n.delay}") for n in verified.nets
                if reported.get(n.net) != n.delay][:5]
        dump = Path(f"native_failure_{inst.name}_{seed}.sol.json")
        result.save(str(dump))
        raise RuntimeError(f"native result failed official legality/delay verification: legal={verified.legal} "
                           f"total={verified.total_delay} reported={best} reasons={list(verified.reasons)[:5]} "
                           f"nets={bad} (saved {dump})")
    if any(reported.get(r.net) != r.delay for r in verified.nets):
        raise RuntimeError("native per-net delay mismatch")
    if initial_delay is not None and verified.total_delay > initial_delay:
        raise RuntimeError("native optimizer lost its legal incumbent")
    return result,dict(initial_delay=initial_delay,total_delay=verified.total_delay,native_log=native_log,
                      iterations=int(performed),accepted=int(accepted),improvements=int(improvements),
                      native_seconds=float(elapsed),seconds=time.perf_counter()-start,trace=trace,
                      binary=str(executable.resolve()),binary_sha256=binary_sha256,
                      dual_sha256=dual.sha256 if dual is not None else None,**repair_metrics)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite",type=Path,default=Path("benchmarks"))
    parser.add_argument("--start-dir",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--cases",nargs="+")
    parser.add_argument("--iterations",type=int,default=10000)
    parser.add_argument("--seconds",type=float,default=120)
    parser.add_argument("--temperature",type=float,default=20)
    parser.add_argument("--seed",type=int,default=0)
    parser.add_argument("--max-group",type=int,default=24)
    strategies = parser.add_mutually_exclusive_group()
    strategies.add_argument("--accurate-prices",action="store_true")
    strategies.add_argument("--compact-growth",action="store_true")
    parser.add_argument("--neighbor-polish",action="store_true")
    parser.add_argument("--warm",action=argparse.BooleanOptionalAction,default=True)
    parser.add_argument("--binary",type=Path,default=BINARY)
    parser.add_argument("--dual-dir",type=Path)
    parser.add_argument("--negotiate",type=float,nargs=5,metavar=("PCT","ROUNDS","PRESENT","GROWTH","HISTORY"),
                        help="share of moves that re-route the group with a short joint negotiation")
    parser.add_argument("--cycle",type=int,help="moves per annealing cycle before restarting from the best (default 1000)")
    parser.add_argument("--margin",type=int,help="confine searches to the pins' bounding box grown by this many cells")
    parser.add_argument("--kick",type=float,nargs=4,metavar=("NETS","ROUNDS","PRESENT","GROWTH"),
                        help="renegotiate a region of NETS nets at every cycle restart")
    parser.add_argument("--ideal",type=int,nargs=2,metavar=("PCT","CAP"),help="percent of ideal-tree moves and their group cap")
    parser.add_argument("--fanout-order",type=int,metavar="PCT",help="percent of repairs that re-plan the largest nets first")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True,exist_ok=True)
    manifest = json.loads((args.suite/"suite.json").read_text())
    rows = []
    for case in manifest["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite/case["instance_file"]))
        sub = Submission.load(str(args.start_dir/(inst.name+".sol.json")))
        dual = None
        if args.dual_dir:
            from dual import load
            dual = load(inst,args.dual_dir/(inst.name+".alpha.npz"))
        def progress(row):
            print(f"{inst.name} native {row['iteration']}: best={row['best_delay']} "
                  f"working={row['working_delay']} elapsed={row['seconds']:.2f}s",flush=True)
        result,metrics = optimize(inst,sub,iterations=args.iterations,seconds=args.seconds,
            temperature=args.temperature,seed=args.seed,max_group=args.max_group,
            accurate_prices=args.accurate_prices,progress=progress,warm=args.warm,
            binary=args.binary,compact_growth=args.compact_growth,
            neighbor_polish=args.neighbor_polish,dual=dual,negotiate=args.negotiate,cycle=args.cycle,margin=args.margin,fanout_order=args.fanout_order,ideal=args.ideal,kick=args.kick)
        result.save(str(args.out_dir/(inst.name+".sol.json")))
        metrics.update(case=inst.name,score=case["baseline_total"]/metrics["total_delay"])
        rows.append(metrics)
        aggregate = math.exp(sum(math.log(r["score"]) for r in rows)/len(rows))
        (args.out_dir/"report.json").write_text(json.dumps(dict(cases=rows,selected_case_score=aggregate,
            aggregate_score=aggregate if len(rows)==manifest["n_cases"] else 0,
            config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
            runtime_note="Refinement only; starting-solution generation excluded."),indent=2)+"\n")
        print(f"FINISHED {inst.name}: {metrics['initial_delay']}->{metrics['total_delay']} {metrics['native_log']} "
              f"iterations={metrics['iterations']} seconds={metrics['seconds']:.2f}",flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
