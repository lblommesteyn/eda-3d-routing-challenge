#!/usr/bin/env python3
"""Joint layer assignment preserving routing topology, with exact via cost."""
from collections import defaultdict, deque
import argparse
import json
import math
from pathlib import Path
import time

import numpy as np
from mip import LinearModel as Model

from route import Instance, Submission
from m3d.model import NetRoute
from m3d.checker import check



def topology(inst, net, route, chunk):
    # Contract existing vertical chains, rather than merging every vertex at an
    # XY coordinate. Separate visits to the same XY position must stay separate
    # logical nodes, otherwise the incumbent can become unrepresentable.
    pins = inst.pin_vertex()
    vertices = {v for edge in route.edges for v in edge} | {pins[p] for p in net.pins()}
    representative = {v:v for v in vertices}

    def find(v):
        while representative[v] != v:
            representative[v] = representative[representative[v]]
            v = representative[v]
        return v

    for a, b in route.edges:
        if a[:2] == b[:2]:
            ra, rb = find(a), find(b)
            representative[max(ra,rb)] = min(ra,rb)
    graph, original_layer, levels = defaultdict(set), {}, defaultdict(set)
    for v in vertices:
        levels[find(v)].add(v[2])
    for a, b in route.edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            graph[ra].add(rb); graph[rb].add(ra)
            original_layer[tuple(sorted((ra,rb)))] = a[2]
    pin_nodes = {pid:find(pins[pid]) for pid in net.pins()}
    root = pin_nodes[net.driver]
    parent, depth = {}, {root:0}
    queue = deque([root])
    while queue:
        v = queue.popleft()
        for other in sorted(graph[v]):
            if other not in depth:
                depth[other], parent[other] = depth[v]+1, v
                queue.append(other)
    active = {root}
    terminal_layers, sink_counts = defaultdict(list), defaultdict(int)
    for pid in net.pins():
        terminal_layers[pin_nodes[pid]].append(pins[pid][2])
    for pid in net.sinks:
        v = pin_nodes[pid]
        sink_counts[v] += 1
        while v not in active:
            active.add(v); v = parent[v]
    children = defaultdict(list)
    for v in active:
        if v != root:
            children[parent[v]].append(v)
    weights = dict(sink_counts)
    for v in sorted(active, key=lambda v:-depth[v]):
        weights[v] = weights.get(v,0) + sum(weights[c] for c in children[v])
    keys = set(terminal_layers) | {root}
    for v in active:
        if len(children[v]) != 1 or len(levels[v]) > 1 or depth[v] % chunk == 0:
            keys.add(v)
        elif v != root:
            p, c = parent[v], children[v][0]
            if (v[0]-p[0], v[1]-p[1]) != (c[0]-v[0], c[1]-v[1]):
                keys.add(v)
    segments, incoming, outgoing = [], {}, defaultdict(list)
    for start in sorted(keys):
        for child in sorted(children[start]):
            path = [start,child]
            while path[-1] not in keys:
                path.append(children[path[-1]][0])
            end, idx = path[-1], len(segments)
            labels = {original_layer[tuple(sorted((a,b)))] for a,b in zip(path,path[1:])}
            if len(labels) != 1:
                raise RuntimeError("compression crossed an existing via")
            segments.append(dict(start=start,end=end,path=path,weight=weights[end],original_layer=labels.pop()))
            incoming[end] = idx; outgoing[start].append(idx)
    return dict(root=root,keys=keys,terminals=terminal_layers,sinks=sink_counts,
                segments=segments,incoming=incoming,outgoing=outgoing,pin_nodes=pin_nodes)


def assign(inst, submission, *, chunk=8, seconds=60, all_layers=False):
    start = time.perf_counter()
    initial = check(inst, submission)
    if not initial.legal or type(chunk) is not int or chunk < 1 or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("legal input, positive chunk size and time limit required")
    model, structures, resources = Model(), {}, defaultdict(list)
    resource_nets, constant = defaultdict(set), 0
    routes = {r.net:r for r in submission.routes}
    pin_vertices = inst.pin_vertex()

    def occupy(xy, z, variable, nid):
        vertex = (*xy, z)
        resources[vertex].append(variable)
        resource_nets[vertex].add(nid)

    for net in inst.nets:
        tree = topology(inst, net, routes[net.id], chunk)
        structures[net.id] = tree
        segments = tree["segments"]
        cheap = {z for z,cost in enumerate(inst.layer_delay) if cost == min(inst.layer_delay)}
        for segment in segments:
            segment["allowed"] = set(range(inst.layers)) if all_layers else cheap | {segment["original_layer"]}
        edge_vars = []
        for segment in segments:
            length, weight = len(segment["path"])-1, segment["weight"]
            variables = [model.variable(length * weight * inst.layer_delay[z], int(z == segment["original_layer"]),
                                        int(z in segment["allowed"])) for z in range(inst.layers)]
            model.constraint([(v,1) for v in variables], 1, 1)
            edge_vars.append(variables)
            for xy in segment["path"][1:-1]:
                for z, variable in enumerate(variables):
                    occupy(xy[:2], z, variable, (net.id, xy))
        tree["edge_vars"], tree["node_vars"] = edge_vars, {}
        for xy in sorted(tree["keys"]):
            pins = tree["terminals"].get(xy, [])
            out = tree["outgoing"][xy]
            incoming = tree["incoming"].get(xy)
            incident = out + ([incoming] if incoming is not None else [])
            simple = incoming is not None and len(out) == 1 and not pins
            original_ports = pins + [segments[e]["original_layer"] for e in incident]
            original_interval = (min(original_ports), max(original_ports))
            possible = set(pins) | set().union(*(segments[e]["allowed"] for e in incident))
            intervals = {}
            for low in range(inst.layers):
                for high in range(low, inst.layers):
                    if any(z < low or z > high for z in pins):
                        continue
                    if low not in possible or high not in possible:
                        continue
                    cost = (high-low) * inst.via_delay * segments[out[0]]["weight"] if simple else 0
                    q = model.variable(cost, int((low,high) == original_interval))
                    intervals[(low,high)] = q
                    for z in range(low, high+1):
                        occupy(xy[:2], z, q, (net.id, xy))
                    # Both ends must be used by a port/pin; forbid padded intervals.
                    for end in {low,high}:
                        if end not in pins:
                            model.constraint([(q,1)] + [(edge_vars[e][end],-1) for e in incident], upper=0)
            model.constraint([(v,1) for v in intervals.values()], 1, 1)
            tree["node_vars"][xy] = intervals
            for edge in incident:
                for z in range(inst.layers):
                    covered = [(q,-1) for (lo,hi),q in intervals.items() if lo <= z <= hi]
                    model.constraint([(edge_vars[edge][z],1)] + covered, upper=0)
            if xy == tree["root"]:
                driver_z = pin_vertices[net.driver][2]
                for edge in out:
                    for z in range(inst.layers):
                        model.cost[edge_vars[edge][z]] += segments[edge]["weight"] * inst.via_delay * abs(z-driver_z)
                for pid in net.sinks:
                    if tree["pin_nodes"][pid] == xy:
                        constant += inst.via_delay * abs(pin_vertices[pid][2]-driver_z)
            elif incoming is not None:
                for pid in net.sinks:
                    if tree["pin_nodes"][pid] == xy:
                        for z in range(inst.layers):
                            model.cost[edge_vars[incoming][z]] += inst.via_delay * abs(z-pin_vertices[pid][2])
                if not simple:
                    for edge in out:
                        pair = {(a,b):model.variable(segments[edge]["weight"] * inst.via_delay * abs(a-b),
                                                int(a == segments[incoming]["original_layer"] and b == segments[edge]["original_layer"]))
                                for a in segments[incoming]["allowed"] for b in segments[edge]["allowed"]}
                        for z in range(inst.layers):
                            model.constraint([(p,1) for (a,b),p in pair.items() if a == z] + [(edge_vars[incoming][z],-1)], 0, 0)
                            model.constraint([(p,1) for (a,b),p in pair.items() if b == z] + [(edge_vars[edge][z],-1)], 0, 0)
    for vertex, variables in resources.items():
        if len(resource_nets[vertex]) > 1:
            model.constraint([(v,1) for v in variables], upper=1)
    if abs(np.dot(model.cost, model.initial) + constant - initial.total_delay) > 1e-6:
        raise RuntimeError("incumbent layer cost differs from checker")
    built_seconds = time.perf_counter()-start
    solved = model.solve(seconds)
    result = submission
    if solved.x is not None:
        selected = []
        for net in inst.nets:
            tree = structures[net.id]
            edges = set()
            for segment, variables in zip(tree["segments"], tree["edge_vars"]):
                levels = [z for z,v in enumerate(variables) if solved.x[v] > .5]
                if len(levels) != 1:
                    raise RuntimeError("nonintegral segment assignment")
                z = levels[0]
                for a,b in zip(segment["path"], segment["path"][1:]):
                    edges.add(tuple(sorted(((*a[:2],z),(*b[:2],z)))))
            for xy, intervals in tree["node_vars"].items():
                chosen = [(lo,hi) for (lo,hi),v in intervals.items() if solved.x[v] > .5]
                if len(chosen) != 1:
                    raise RuntimeError("nonintegral via assignment")
                low, high = chosen[0]
                for z in range(low, high):
                    edges.add(((*xy[:2],z),(*xy[:2],z+1)))
            selected.append(NetRoute(net.id, sorted(edges)))
        proposed = Submission(inst.name, selected)
        checked = check(inst, proposed)
        if not checked.legal:
            raise RuntimeError(f"invalid layer assignment: {checked.reasons}")
        if abs(checked.total_delay - (solved.fun + constant)) > 1e-5:
            raise RuntimeError("layer objective differs from official checker")
        if checked.total_delay < initial.total_delay:
            result = proposed
    checked = check(inst, result)
    return result, dict(initial_delay=initial.total_delay, total_delay=checked.total_delay,
                        variables=len(model.cost), constraints=len(model.lower),
                        active_variables=sum(model.bounds), all_layers=all_layers,
                        build_seconds=built_seconds, seconds=time.perf_counter()-start,
                        status=int(solved.status), message=solved.message,
                        model_objective=float(solved.fun)+constant if solved.fun is not None else None,
                        model_gap=float(solved.get("mip_gap")) if solved.get("mip_gap") is not None else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("benchmarks"))
    parser.add_argument("--start-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--chunk", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--all-layers", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.suite/"suite.json").read_text())
    rows = []
    for case in manifest["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite/case["instance_file"]))
        sub = Submission.load(str(args.start_dir/(inst.name+".sol.json")))
        result, metrics = assign(inst, sub, chunk=args.chunk, seconds=args.seconds, all_layers=args.all_layers)
        result.save(str(args.out_dir/(inst.name+".sol.json")))
        metrics.update(case=inst.name, score=case["baseline_total"]/metrics["total_delay"])
        rows.append(metrics)
        aggregate = math.exp(sum(math.log(r["score"]) for r in rows)/len(rows))
        (args.out_dir/"report.json").write_text(json.dumps(dict(cases=rows, selected_case_score=aggregate,
            aggregate_score=aggregate if len(rows)==manifest["n_cases"] else 0,
            config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}),indent=2)+"\n")
        print(f"{inst.name}: {metrics['initial_delay']}->{metrics['total_delay']} "
              f"variables={metrics['variables']} status={metrics['status']} "
              f"seconds={metrics['seconds']:.2f} subset_score={aggregate:.6f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
