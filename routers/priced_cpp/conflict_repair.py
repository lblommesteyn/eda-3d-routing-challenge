"""Conflict-based group repair using C++ low-level trees and dual bounds."""
import heapq
import itertools
import math
import time

import numpy as np


class ConflictRouter:
    def __init__(self, refiner, dual, node_limit=5000):
        if node_limit < 1:
            raise ValueError("positive conflict-search node limit required")
        self.ref,self.dual,self.node_limit = refiner,dual,node_limit
        self.alpha,self.bids = {},{}
        sink = 0
        for net in refiner.inst.nets:
            self.alpha[net.id] = []
            for _ in net.sinks:
                a,b = dual.offsets[sink:sink+2]
                self.alpha[net.id].append(dict(zip(dual.vertices[a:b].tolist(),dual.values[a:b].tolist())))
                sink += 1
            self.bids[net.id] = dict(zip(*dual.bids[net.id]))

    def solve(self, group, seconds):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("positive conflict-search time required")
        start = time.perf_counter()
        deadline = start+seconds
        ref = self.ref
        incumbent = sum(ref.routes[n]["delay"] for n in group)
        outside = ref.blocked(set(group))
        capacity = np.zeros(len(self.dual.capacity))
        for n in group:
            vv,aa = self.dual.bids[n]
            capacity[vv] = np.maximum(capacity[vv],aa)
        capacity[list(outside)] = 0
        cap_sum = float(capacity.sum())
        queries,expanded,generated = 0,0,0
        cache = {}

        def route(n, bans):
            nonlocal queries
            key = n,bans
            if key in cache:
                return cache[key]
            blocked = outside | bans
            options = ref.request(n,blocked)
            queries += 1
            if not options:
                cache[key] = None
                return None
            net,priced = ref.worker.nets[n],0.
            for sink,alpha in zip(net.sinks,self.alpha[n]):
                options_s = ref.worker.request(n,prices=alpha,blocked=blocked,k=1,diversity=0,
                                              strategy="spt",pins=[net.driver,sink])
                queries += 1
                if not options_s:
                    raise RuntimeError("priced and unpriced reachability differ")
                priced += options_s[0]["priced_cost"]
            cache[key] = options[0],priced
            return cache[key]

        def clashes(routes):
            owners = {}
            for n,candidate in routes.items():
                for v in candidate["vertices"]:
                    owners.setdefault(v,[]).append(n)
            return {v:ns for v,ns in owners.items() if len(ns)>1}

        def bound(routes,prices,cap):
            return max(sum(r["delay"] for r in routes.values()),
                       math.ceil(sum(prices.values())-cap-1e-6))

        bans = {n:frozenset() for n in group}
        routes,prices = {},{}
        for n in group:
            result = route(n,bans[n])
            if result is None:
                raise RuntimeError("legal incumbent unreachable at conflict-search root")
            routes[n],prices[n] = result
        root_bound = bound(routes,prices,cap_sum)
        heap,counter,seen = [],itertools.count(),set()

        def push(routes,prices,bans,cap):
            nonlocal generated
            lower = bound(routes,prices,cap)
            if lower >= incumbent:
                return
            signature = tuple((n,tuple(sorted(bans[n]))) for n in sorted(group))
            if signature in seen:
                return
            seen.add(signature)
            conflicts = clashes(routes)
            count = sum(len(ns)-1 for ns in conflicts.values())
            # Ordering is deliberately eager to resolve conflicts. Pruning alone
            # uses an admissible bound; no weighted score can discard a solution.
            priority = lower+2*count
            heapq.heappush(heap,(priority,count,next(counter),routes,prices,bans,cap,conflicts))
            generated += 1

        push(routes,prices,bans,cap_sum)
        replacement = None
        while heap and expanded < self.node_limit and time.perf_counter()<deadline:
            _,_,_,routes,prices,bans,cap,conflicts = heapq.heappop(heap)
            expanded += 1
            if not conflicts:
                replacement = routes
                break
            vertex = max(conflicts,key=lambda v: (capacity[v]*(len(conflicts[v])-1),len(conflicts[v])))
            pair = sorted(conflicts[vertex],key=lambda n:-self.bids[n].get(vertex,0.))[:2]
            old_max = max(self.bids[n].get(vertex,0.) for n in group if vertex not in bans[n])
            for n in reversed(pair):
                if time.perf_counter() >= deadline:
                    break
                new_bans = dict(bans)
                new_bans[n] = bans[n] | {vertex}
                result = route(n,new_bans[n])
                if result is None:
                    continue
                new_routes,new_prices = dict(routes),dict(prices)
                new_routes[n],new_prices[n] = result
                new_max = max((self.bids[m].get(vertex,0.) for m in group if vertex not in new_bans[m]),default=0.)
                push(new_routes,new_prices,new_bans,cap-old_max+new_max)
        cost = sum(c["delay"] for c in replacement.values()) if replacement else incumbent
        limited = time.perf_counter() >= deadline or (expanded >= self.node_limit and bool(heap))
        return replacement,dict(initial_delay=incumbent,total_delay=cost,lower_bound=root_bound,
            expanded=expanded,generated=generated,queries=queries,
            status="improved" if replacement else "budget" if limited else "exhausted",
            seconds=time.perf_counter()-start)
