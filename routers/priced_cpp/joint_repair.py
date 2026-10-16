#!/usr/bin/env python3
"""Joint geometry repair with binary vertex ownership and continuous sink flows."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from controller import NativeSession,as_submission
from dual import load,diagnose
from improve import Refiner
from mip import LinearModel
from priced_repair import candidate
from route import Instance,Submission
from m3d.checker import check


class JointRouter:
    def __init__(self, refiner, dual=None, ownership_ties=False, fixed_xy=False,
                 xy_radius=0, sink_extra=None, aggregate=False):
        self.ref = refiner
        self.dual = dual
        self.ownership_ties = ownership_ties
        if type(xy_radius) is not int or xy_radius < 0 or (sink_extra is not None and
                (not math.isfinite(sink_extra) or sink_extra < 0)):
            raise ValueError("nonnegative XY radius and sink detour budget required")
        self.fixed_xy = fixed_xy or xy_radius > 0
        self.sink_extra = sink_extra
        if aggregate and sink_extra is not None:
            raise ValueError("aggregate flows do not support individual sink caps")
        self.aggregate = aggregate
        self.sink_offsets,offset = {},0
        for net in refiner.inst.nets:
            self.sink_offsets[net.id] = offset; offset += len(net.sinks)
        self.size = refiner.inst.width*refiner.inst.height*refiner.inst.layers
        edges = [(u,v,w) for u in range(self.size) for v,w in refiner.grid.neighbors(u)]
        self.u,self.v,self.w = np.asarray(edges,dtype=np.int64).T
        self.pin_owner = np.full(self.size,-1,dtype=np.int64)
        for net in refiner.inst.nets:
            for pin in net.pins():
                self.pin_owner[refiner.worker.pins[pin]] = net.id
        self.xy_masks = {}
        if self.fixed_xy:
            plane = refiner.inst.width*refiner.inst.height
            a,b = self.u%plane,self.v%plane
            codes = np.minimum(a,b)*plane+np.maximum(a,b)
            vertical = self.u//plane != self.v//plane
            for n,route in refiner.routes.items():
                points = {v%plane for v in route["vertices"]}
                for _ in range(xy_radius):
                    expanded = set(points)
                    for p in points:
                        x,y = p%refiner.inst.width,p//refiner.inst.width
                        if x: expanded.add(p-1)
                        if x+1<refiner.inst.width: expanded.add(p+1)
                        if y: expanded.add(p-refiner.inst.width)
                        if y+1<refiner.inst.height: expanded.add(p+refiner.inst.width)
                    points = expanded
                edges = {min(x%plane,y%plane)*plane+max(x%plane,y%plane)
                         for x,y in route["edges"] if x//plane == y//plane}
                self.xy_masks[n] = np.isin(a,list(points)) & np.isin(b,list(points))
                if not xy_radius:
                    self.xy_masks[n] &= vertical | np.isin(codes,list(edges))

    def solve(self, group, seconds):
        start = time.perf_counter()
        if not math.isfinite(seconds) or seconds <= 0 or not group:
            raise ValueError("nonempty group and positive solver time required")
        ref = self.ref
        incumbent = sum(ref.routes[n]["delay"] for n in group)
        outside = ref.blocked(set(group))
        model,commodities,vertex_nets = LinearModel(),[],defaultdict(set)
        lower = 0
        for nid in group:
            net = ref.worker.nets[nid]
            available = (self.pin_owner < 0) | (self.pin_owner == nid)
            available[list(outside)] = False
            mask = available[self.u] & available[self.v]
            if self.fixed_xy:
                mask &= self.xy_masks[nid]
            u,v,w = self.u[mask],self.v[mask],self.w[mask]
            graph = csr_matrix((w.astype(float),(u,v)),shape=(self.size,self.size))
            pins = [ref.worker.pins[p] for p in net.pins()]
            distances = dijkstra(graph,directed=True,indices=pins)
            adjacency = defaultdict(list)
            for a,b in ref.routes[nid]["edges"]:
                adjacency[a].append(b); adjacency[b].append(a)
            parent,order = {pins[0]:None},[pins[0]]
            for a in order:
                for b in adjacency[a]:
                    if b not in parent:
                        parent[b] = a; order.append(b)
            for j,sink in enumerate(pins[1:],1):
                lb = distances[0,sink]
                if not np.isfinite(lb):
                    raise RuntimeError("legal group contains unreachable sink")
                lower += lb
                path,here,path_cost = set(),sink,0
                while parent[here] is not None:
                    plane = ref.inst.width*ref.inst.height
                    path_cost += (ref.inst.via_delay if parent[here]//plane != here//plane
                                  else ref.inst.layer_delay[here//plane])
                    path.add((parent[here],here)); here = parent[here]
                commodities.append(dict(net=nid,driver=pins[0],sink=sink,u=u,v=v,w=w,
                    forward=distances[0],reverse=distances[j],lower=lb,path=path,current_cost=path_cost,
                    sink_index=self.sink_offsets[nid]+j-1))
        excess = incumbent-lower
        for item in commodities:
            u,v,w = item["u"],item["v"],item["w"]
            budget = item["lower"]+excess
            if self.sink_extra is not None:
                budget = min(budget,item["current_cost"]+self.sink_extra)
            mask = ((item["forward"][u]+w+item["reverse"][v] <= budget+1e-8)
                    & (v != item["driver"]) & (u != item["sink"]))
            item.update(u=u[mask],v=v[mask],w=w[mask],path_budget=budget)
        physical_arcs = sum(len(c["u"]) for c in commodities)
        dual_lower = None
        if self.dual is not None:
            bids = {n:np.zeros(self.size) for n in group}
            for item in commodities:
                i = item["sink_index"]
                a,b = self.dual.offsets[i:i+2]
                alpha = np.zeros(self.size)
                alpha[self.dual.vertices[a:b]] = self.dual.values[a:b]
                u,v,w = item["u"],item["v"],item["w"]
                active = np.zeros(self.size,dtype=bool)
                active[u] = True; active[v] = True
                bids[item["net"]] += alpha*active
                weighted = csr_matrix((w+alpha[v],(u,v)),shape=(self.size,self.size))
                forward = dijkstra(weighted,directed=True,indices=item["driver"])
                reverse = dijkstra(weighted.T.tocsr(),directed=True,indices=item["sink"])
                item.update(alpha=alpha,priced_forward=forward,priced_reverse=reverse,
                            priced_lower=forward[item["sink"]]+alpha[item["driver"]])
            capacity = np.zeros(self.size)
            for nid in group:
                capacity = np.maximum(capacity,bids[nid])
            dual_lower = sum(c["priced_lower"] for c in commodities)-float(capacity.sum())
            if dual_lower > incumbent+1e-5:
                raise RuntimeError("conditional dual exceeds legal incumbent")
        if not self.ownership_ties and (lower >= incumbent or
                (dual_lower is not None and dual_lower > incumbent-1+1e-6)):
            return None,dict(initial_delay=incumbent,total_delay=incumbent,lower_bound=lower,
                             dual_lower_bound=dual_lower,seconds=time.perf_counter()-start,status="bound proves optimal")
        # Every improving group has each sink cost <= its independent LB plus
        # the group's entire excess. These corridors retain all better routes.
        retained_arcs = 0
        for item in commodities:
            u,v,w = item["u"],item["v"],item["w"]
            path_budget = item["path_budget"]
            mask = np.ones(len(u),dtype=bool)
            if dual_lower is not None:
                # Local weak duality: every improving solution has each sink's
                # priced-path excess <= incumbent - conditional dual bound.
                priced = (item["priced_forward"][u]+w+item["alpha"][v]+
                          item["priced_reverse"][v]+item["alpha"][item["driver"]])
                mask &= priced <= item["priced_lower"]+max(0.,incumbent-dual_lower)+1e-6
            u,v,w = u[mask],v[mask],w[mask]
            retained_arcs += len(u)
            item.update(u=u,v=v,w=w)
        node_limits = defaultdict(int)
        flow_items = commodities
        if self.aggregate:
            grouped = defaultdict(dict)
            for item in commodities:
                nid = item["net"]
                for vertex in np.unique(np.r_[item["u"],item["v"]]):
                    node_limits[nid,int(vertex)] += 1
                for a,b,w in zip(item["u"],item["v"],item["w"]):
                    edge = int(a),int(b)
                    record = grouped[nid].setdefault(edge,[int(w),0,0])
                    record[1] += 1
                    record[2] += int(edge in item["path"])
            flow_items = []
            for nid,arcs in grouped.items():
                edges = list(arcs)
                net = ref.worker.nets[nid]
                demand = defaultdict(int)
                demand[ref.worker.pins[net.driver]] += len(net.sinks)
                for pin in net.sinks:
                    demand[ref.worker.pins[pin]] -= 1
                flow_items.append(dict(net=nid,u=[a for a,b in edges],v=[b for a,b in edges],
                    w=[arcs[e][0] for e in edges],upper=[arcs[e][1] for e in edges],
                    warm=[arcs[e][2] for e in edges],demand=demand))
        for item in flow_items:
            u,v,w = item["u"],item["v"],item["w"]
            upper = item.get("upper",[1]*len(u))
            warm = item.get("warm")
            if warm is None:
                warm = [int((int(a),int(b)) in item["path"]) for a,b in zip(u,v)]
            variables = [model.variable(int(cost),initial,upper=limit,integer=False)
                         for cost,initial,limit in zip(w,warm,upper)]
            if self.sink_extra is not None:
                model.constraint(zip(variables,w),upper=item["path_budget"])
            balance,incoming = defaultdict(list),defaultdict(list)
            for a,b,var in zip(u,v,variables):
                a,b = int(a),int(b)
                balance[a].append((var,1)); balance[b].append((var,-1))
                incoming[b].append((var,1))
                vertex_nets[a].add(item["net"]); vertex_nets[b].add(item["net"])
            for vertex,terms in balance.items():
                rhs = (item["demand"].get(vertex,0) if self.aggregate else
                       int(vertex == item["driver"])-int(vertex == item["sink"]))
                model.constraint(terms,rhs,rhs)
            item["incoming"] = incoming
        ownership = {}
        for vertex,nets in vertex_nets.items():
            if len(nets) > 1:
                variables = []
                for nid in sorted(nets):
                    var = model.variable(initial=int(ref.owners.get(vertex) == nid))
                    ownership[nid,vertex] = var
                    variables.append((var,1))
                model.constraint(variables,upper=1)
        for item in flow_items:
            for vertex,terms in item["incoming"].items():
                var = ownership.get((item["net"],vertex))
                limit = node_limits[item["net"],vertex] if self.aggregate else 1
                if var is not None:
                    model.constraint(terms+[(var,-limit)],upper=0)
                elif self.aggregate:
                    model.constraint(terms,upper=limit)
        if abs(np.dot(model.cost,model.initial)-incumbent) > 1e-6:
            raise RuntimeError("joint warm-start cost differs from checker")
        model.constraint(((i,c) for i,c in enumerate(model.cost) if c),upper=incumbent)
        # Expose the known integer bound even before the large root LP finishes.
        # C = floor + remainder is an exact objective reformulation; remainder
        # is nonnegative because the bound applies to every legal group route.
        floor = max(int(round(lower)),math.ceil(dual_lower-1e-6) if dual_lower is not None else 0)
        physical_cost = list(model.cost)
        remainder = model.variable(initial=incumbent-floor,upper=incumbent-floor,integer=False)
        model.constraint([(i,c) for i,c in enumerate(physical_cost) if c]+[(remainder,-1)],floor,floor)
        model.cost = [0.]*len(model.cost)
        model.cost[remainder] = 1.
        model.offset = floor
        if self.ownership_ties:
            # Integer physical delays remain primary: the total ownership tie
            # cost is <0.1 under capacity. Favor freeing contested resources
            # for the net bidding most without accepting a physical increase.
            scale = .1/(1+float(self.dual.capacity.sum()))
            for nid in group:
                bids = dict(zip(*self.dual.bids[nid]))
                for (owner,vertex),var in ownership.items():
                    if owner == nid:
                        model.cost[var] = scale*max(0.,self.dual.capacity[vertex]-bids.get(vertex,0.))
        solved = model.solve(seconds,mip_gap=0 if self.ownership_ties else 1e-4)
        replacement = None
        if solved.x is not None:
            allowed = {n:set() for n in group}
            for vertex,nets in vertex_nets.items():
                for nid in nets:
                    var = ownership.get((nid,vertex))
                    if var is None or solved.x[var] > .5:
                        allowed[nid].add(vertex)
            proposed = {}
            for nid in group:
                if self.fixed_xy:
                    available = np.zeros(self.size,dtype=bool)
                    available[list(allowed[nid])] = True
                    mask = self.xy_masks[nid] & available[self.u] & available[self.v]
                    graph = csr_matrix((self.w[mask].astype(float),(self.u[mask],self.v[mask])),
                                       shape=(self.size,self.size))
                    net = ref.worker.nets[nid]
                    driver = ref.worker.pins[net.driver]
                    distance,parent = dijkstra(graph,directed=True,indices=driver,return_predecessors=True)
                    used,edges,cost = {driver},set(),0
                    for pin in net.sinks:
                        vertex = ref.worker.pins[pin]
                        if not np.isfinite(distance[vertex]):
                            raise RuntimeError("fixed-XY flow cannot connect a sink")
                        cost += int(round(distance[vertex]))
                        while vertex not in used:
                            used.add(vertex)
                            previous = int(parent[vertex])
                            if previous < 0:
                                raise RuntimeError("broken fixed-XY predecessor")
                            edges.add(tuple(sorted((previous,vertex))))
                            vertex = previous
                    proposed[nid] = dict(edges=sorted(edges),vertices=used,delay=cost,priced_cost=cost)
                    continue
                blocked = set(range(self.size))-allowed[nid]
                options = ref.request(nid,blocked)
                if not options:
                    raise RuntimeError("joint flow solution has no legal path")
                proposed[nid] = options[0]
            cost = sum(c["delay"] for c in proposed.values())
            if cost > solved.fun+1e-5:
                raise RuntimeError("extracted tree costs more than joint flows")
            if cost < incumbent or (self.ownership_ties and cost == incumbent):
                replacement = proposed
        return replacement,dict(initial_delay=incumbent,
            total_delay=sum(c["delay"] for c in replacement.values()) if replacement else incumbent,
            lower_bound=lower,dual_lower_bound=dual_lower,variables=len(model.cost),binary_variables=sum(model.integrality),
            physical_arcs=physical_arcs,retained_arcs=retained_arcs,objective_floor=floor,
            constraints=len(model.lower),status=int(solved.status),gap=solved.mip_gap,
            model_objective=solved.fun,model_bound=solved.dual_bound,nodes=solved.nodes,
            seconds=time.perf_counter()-start)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("suite","start-dir","dual-dir","out-dir"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--cases",nargs="+")
    parser.add_argument("--group-seconds",type=float,default=5)
    parser.add_argument("--seconds",type=float,default=300)
    parser.add_argument("--groups",type=int,default=30)
    parser.add_argument("--size",type=int,default=3)
    parser.add_argument("--seed",type=int,default=0)
    parser.add_argument("--ownership-ties",action="store_true")
    parser.add_argument("--backend",choices=("mip","cbs"),default="mip")
    parser.add_argument("--nodes",type=int,default=5000)
    parser.add_argument("--fixed-xy",action="store_true",help="jointly assign all nets within their original XY edge graphs")
    parser.add_argument("--xy-corridor",type=int,help="jointly optimize all nets within this XY radius of their original wires")
    parser.add_argument("--sink-extra",type=float,help="heuristic cap on each sink's allowed delay increase")
    parser.add_argument("--aggregate",action="store_true",help="use one continuous multi-sink flow per net")
    args = parser.parse_args()
    if (args.size < 2 or args.groups < 1 or args.nodes < 1 or
            not math.isfinite(args.group_seconds) or args.group_seconds <= 0 or
            not math.isfinite(args.seconds) or args.seconds <= 0):
        parser.error("positive budgets/group counts and size >=2 required")
    if args.xy_corridor is not None and args.xy_corridor < 0:
        parser.error("--xy-corridor must be nonnegative")
    if args.sink_extra is not None and (not math.isfinite(args.sink_extra) or args.sink_extra < 0):
        parser.error("--sink-extra must be finite and nonnegative")
    if args.backend == "cbs" and args.ownership_ties:
        parser.error("--ownership-ties is only supported by the MIP backend")
    xy_mode = args.fixed_xy or args.xy_corridor is not None
    if args.fixed_xy and args.xy_corridor is not None:
        parser.error("choose --fixed-xy or --xy-corridor")
    if args.backend == "cbs" and xy_mode:
        parser.error("--fixed-xy requires the MIP backend")
    if args.backend == "cbs" and args.sink_extra is not None:
        parser.error("--sink-extra requires the MIP backend")
    if args.aggregate and (args.backend != "mip" or args.sink_extra is not None):
        parser.error("--aggregate requires MIP and no --sink-extra")
    args.out_dir.mkdir(parents=True,exist_ok=True)
    rows = []
    for case in json.loads((args.suite/"suite.json").read_text())["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        start = time.perf_counter()
        inst = Instance.load(str(args.suite/case["instance_file"]))
        sub = Submission.load(str(args.start_dir/(inst.name+".sol.json")))
        dual = load(inst,args.dual_dir/(inst.name+".alpha.npz"))
        diagnostic = diagnose(inst,sub,dual)
        offsets,offset = {},0
        for net in inst.nets:
            offsets[net.id] = offset; offset += len(net.sinks)
        trace,seen = [],set()
        with NativeSession(inst) as worker:
            ref = Refiner(inst,sub,worker,deadline=start+args.seconds)
            if args.backend == "cbs":
                from conflict_repair import ConflictRouter
                solver = ConflictRouter(ref,dual,node_limit=args.nodes)
            else:
                solver = JointRouter(ref,dual,ownership_ties=args.ownership_ties,fixed_xy=xy_mode,
                                     xy_radius=args.xy_corridor or 0,sink_extra=args.sink_extra,
                                     aggregate=args.aggregate)
            priorities = [r["net"] for r in diagnostic["nets"]]
            for iteration in range(args.groups):
                if time.perf_counter() >= ref.deadline:
                    break
                if xy_mode:
                    group = tuple(sorted(ref.routes))
                    replacement,metrics = solver.solve(group,min(args.group_seconds,max(.01,ref.deadline-time.perf_counter())))
                    if replacement:
                        ref.accept(replacement,allow_equal=args.ownership_ties)
                        ref.best_routes = dict(ref.routes)
                    trace.append(dict(group=group,**metrics))
                    print(f"{inst.name} XY radius {args.xy_corridor or 0}: {diagnostic['total_delay']}->{ref.best_delay} "
                          f"seconds={metrics['seconds']:.2f}",flush=True)
                    break
                target = priorities[iteration%len(priorities)]
                desired = candidate(worker,worker.nets[target],dual,offsets[target],
                                    (0,.5,1,2)[iteration%4],args.seed+iteration)
                if desired is None:
                    continue
                hits = defaultdict(int)
                for v in desired["vertices"]:
                    owner = ref.owners.get(v)
                    if owner is not None and owner != target:
                        hits[owner] += 1
                # Include nearby routes that may need to make room for blockers.
                # Direct path conflicts retain priority over these neighbors.
                for v in desired["vertices"]:
                    for neighbor,_ in ref.grid.neighbors(v):
                        owner = ref.owners.get(neighbor)
                        if owner is not None and owner != target:
                            hits[owner] += .05
                group = tuple(sorted([target]+sorted(hits,key=lambda n:-hits[n])[:args.size-1]))
                if len(group) < 2 or group in seen:
                    continue
                seen.add(group)
                replacement,metrics = solver.solve(group,min(args.group_seconds,max(.01,ref.deadline-time.perf_counter())))
                if replacement:
                    if ref.accept(replacement,allow_equal=True):
                        ref.best_routes = dict(ref.routes)
                trace.append(dict(group=group,**metrics))
                print(f"{inst.name} joint {group}: {metrics['initial_delay']}->{metrics['total_delay']} "
                      f"best={ref.best_delay} seconds={metrics['seconds']:.2f}",flush=True)
            result = as_submission(inst,ref.best_routes)
            if not check(inst,result).legal:
                raise RuntimeError("joint repair produced illegal result")
            result.save(str(args.out_dir/(inst.name+".sol.json")))
            rows.append(dict(case=inst.name,initial_delay=diagnostic["total_delay"],
                             total_delay=ref.best_delay,seconds=time.perf_counter()-start,trace=trace,
                             equal_moves=ref.equal_moves,improving_moves=ref.accepted))
            (args.out_dir/"report.json").write_text(json.dumps(dict(cases=rows,config={
                k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                runtime_note="Refinement only; source route generation excluded."),indent=2)+"\n")


if __name__ == "__main__":
    main()
