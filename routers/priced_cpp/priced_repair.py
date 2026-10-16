#!/usr/bin/env python3
"""Per-sink dual-priced C++ paths followed by legal neighborhood repair."""
import argparse
import json
import math
from pathlib import Path
import time

from controller import NativeSession,as_submission
from dual import load,diagnose
from improve import Refiner
from route import Instance,Submission
from m3d.checker import check


def candidate(worker, net, dual, sink_offset, scale, seed):
    vertices = set()
    for i,sink in enumerate(net.sinks,sink_offset):
        a,b = dual.offsets[i:i+2]
        prices = {int(v):float(x)*scale for v,x in zip(dual.vertices[a:b],dual.values[a:b])}
        options = worker.request(net.id,prices=prices,pins=[net.driver,sink],
                                 strategy="spt",k=1,diversity=0,seed=seed)
        if not options:
            return None
        vertices.update(options[0]["vertices"])
    # Extract a physical shortest-path tree from the path union's induced grid.
    # This removes cycles and preserves every terminal before conflict repair.
    blocked = set(range(worker.inst.width*worker.inst.height*worker.inst.layers))-vertices
    options = worker.request(net.id,blocked=blocked,strategy="spt",k=1,diversity=0,seed=seed)
    return options[0] if options else None


def refine(inst, submission, dual, *, seconds=120, rounds=30, seed=0, temperature=8, progress=None):
    if not math.isfinite(seconds) or seconds <= 0 or type(rounds) is not int or rounds < 1:
        raise ValueError("positive time and round count required")
    if (type(seed) is not int or not 0 <= seed < 2**63 or
            not math.isfinite(temperature) or temperature < 0):
        raise ValueError("invalid seed or temperature")
    start = time.perf_counter()
    initial = diagnose(inst,submission,dual)
    targets = [r["net"] for r in initial["nets"]]
    offsets,offset = {},0
    for net in inst.nets:
        offsets[net.id] = offset; offset += len(net.sinks)
    trace = []
    with NativeSession(inst) as worker:
        ref = Refiner(inst,submission,worker,deadline=start+seconds)
        ref.initialize_bounds()
        for iteration in range(rounds):
            if time.perf_counter() >= ref.deadline:
                break
            scale = (.25,.5,1.,2.,4.)[iteration%5]
            desired = {}
            # Rotate the priority list to avoid starving later targets at a deadline.
            shift = iteration%len(targets)
            order = targets[shift:]+targets[:shift]
            for nid in order:
                if time.perf_counter() >= ref.deadline:
                    break
                route = candidate(worker,worker.nets[nid],dual,offsets[nid],scale,seed+iteration)
                if route is not None:
                    desired[nid] = route
            changes,attempts = ref.neighborhoods(seed+iteration,limit=len(desired),max_size=16,
                retries=4,local_iters=10,temperature=temperature,
                desired_routes=desired,target_order=[n for n in order if n in desired])
            ref.singles(seed+iteration,equal_moves=True)
            row = dict(iteration=iteration,scale=scale,best_delay=ref.best_delay,working_delay=ref.delay,
                       candidates=len(desired),attempts=attempts,changes=changes,seconds=time.perf_counter()-start)
            trace.append(row)
            if progress:
                progress(row)
        result = as_submission(inst,ref.best_routes)
    checked = check(inst,result)
    if not checked.legal or checked.total_delay != ref.best_delay or checked.total_delay > initial["total_delay"]:
        raise RuntimeError("dual repair lost its legal incumbent")
    final = diagnose(inst,result,dual)
    return result,dict(initial=initial,final=final,total_delay=checked.total_delay,
                       seconds=time.perf_counter()-start,trace=trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("suite","start-dir","dual-dir","out-dir"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--cases",nargs="+")
    parser.add_argument("--seconds",type=float,default=120)
    parser.add_argument("--rounds",type=int,default=30)
    parser.add_argument("--seed",type=int,default=0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True,exist_ok=True)
    rows = []
    for case in json.loads((args.suite/"suite.json").read_text())["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite/case["instance_file"]))
        sub = Submission.load(str(args.start_dir/(inst.name+".sol.json")))
        def progress(row):
            print(f"{inst.name} dual {row['iteration']}: best={row['best_delay']} "
                  f"working={row['working_delay']} elapsed={row['seconds']:.2f}s",flush=True)
        result,metrics = refine(inst,sub,load(inst,args.dual_dir/(inst.name+".alpha.npz")),
                              seconds=args.seconds,rounds=args.rounds,seed=args.seed,progress=progress)
        result.save(str(args.out_dir/(inst.name+".sol.json")))
        rows.append(dict(case=inst.name,**metrics))
        (args.out_dir/"report.json").write_text(json.dumps(dict(cases=rows,config={
            k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
            runtime_note="Refinement and diagnosis; starting route generation excluded."),indent=2)+"\n")
        print(f"FINISHED {inst.name}: {metrics['initial']['total_delay']}->{metrics['total_delay']} "
              f"seconds={metrics['seconds']:.2f}",flush=True)


if __name__ == "__main__":
    main()
