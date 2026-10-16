#!/usr/bin/env python3
"""Monotone legal refinement: exact per-net SPTs and coordinated small groups."""
from __future__ import annotations

import argparse
from collections import defaultdict
from itertools import permutations
import json
import heapq
import math
import random
from pathlib import Path
import time

from controller import NativeSession, as_submission
from route import Grid, Instance, Submission
from m3d.checker import check


class Refiner:
    def __init__(self, inst, submission, worker, deadline=float("inf"), exact_prices=False):
        result = check(inst, submission)
        if not result.legal:
            raise ValueError("refinement requires a fully legal starting solution")
        self.inst, self.worker, self.grid = inst, worker, Grid(inst)
        self.deadline = deadline
        self.exact_prices = exact_prices
        self.routes, self.owners = {}, {}
        self.queries, self.accepted, self.equal_moves = 0, 0, 0
        self.uphill_moves = 0
        self.lower_bounds, self.ideal, self.demand = {}, {}, defaultdict(int)
        delay = {n.net: n.delay for n in result.nets}
        for route in submission.routes:
            edges = sorted((min(self.grid.vid(a), self.grid.vid(b)),
                            max(self.grid.vid(a), self.grid.vid(b))) for a, b in route.edges)
            pins = {worker.pins[p] for p in worker.nets[route.net].pins()}
            vertices = pins | {v for edge in edges for v in edge}
            candidate = dict(edges=edges, vertices=vertices, delay=delay[route.net])
            self.routes[route.net] = candidate
            for v in vertices:
                self.owners[v] = route.net
        self.best_routes, self.best_delay = dict(self.routes), self.delay

    @property
    def delay(self):
        return sum(c["delay"] for c in self.routes.values())

    def request(self, nid, blocked=(), *, seed=0, k=1, prices=None):
        self.queries += 1
        return self.worker.request(nid, blocked=blocked, prices=prices, k=k,
                                   diversity=0, strategy="steiner" if self.exact_prices and prices else "spt", seed=seed)

    def initialize_bounds(self):
        for nid in sorted(self.routes):
            candidate = self.request(nid)[0]
            self.lower_bounds[nid] = candidate["delay"]
            self.ideal[nid] = candidate["vertices"]
            for v in candidate["vertices"]:
                self.demand[v] += 1

    def blocked(self, group):
        return {v for v, nid in self.owners.items() if nid not in group}

    def pressure(self, candidate):
        return sum(self.demand[v] for v in candidate["vertices"])

    def accept(self, replacement, *, allow_equal=False, max_increase=0):
        old = {nid: self.routes[nid] for nid in replacement}
        before = sum(c["delay"] for c in old.values())
        after = sum(c["delay"] for c in replacement.values())
        if after > before + max_increase or (after == before and not allow_equal):
            return False
        if all(replacement[nid]["edges"] == old[nid]["edges"] for nid in old):
            return False
        self.routes.update(replacement)
        result = check(self.inst, as_submission(self.inst, self.routes))
        if not result.legal:
            self.routes.update(old)
            raise RuntimeError("refinement proposed an illegal replacement")
        for nid, candidate in old.items():
            for v in candidate["vertices"]:
                del self.owners[v]
        for nid, candidate in replacement.items():
            for v in candidate["vertices"]:
                self.owners[v] = nid
        if after < before:
            self.accepted += 1
        elif after == before:
            self.equal_moves += 1
        else:
            self.uphill_moves += 1
        if self.delay < self.best_delay:
            self.best_delay, self.best_routes = self.delay, dict(self.routes)
        return True

    def singles(self, seed=0, *, equal_moves=False):
        changes = 0
        # Fractional prices only break ties in INTEGER true path delay. Their
        # total is < 1, so no more expensive true path can win a Dijkstra search.
        scale = 1e-4 / max(1, sum(self.demand.values()))
        prices = {v: value * scale for v, value in self.demand.items()}
        order = sorted(self.routes, key=lambda n: (-(self.routes[n]["delay"] - self.lower_bounds[n]), n))
        for nid in order:
            if time.perf_counter() >= self.deadline:
                break
            candidate = self.request(nid, self.blocked({nid}), seed=seed, prices=prices)[0]
            if self.accept({nid: candidate}, allow_equal=equal_moves):
                changes += 1
        return changes

    def conflict_search(self, group, blocked, incumbent, seed, node_limit):
        """Bounded CBS with exact SPT lower bounds and capacity-one branching.

        A conflict at v requires at least one of the two nets to avoid v. Both
        branches are explored; unfinished searches keep their legal incumbent.
        """
        best = incumbent
        best_cost = sum(c["delay"] for c in best.values())
        cache = {}

        def plan(nid, constraints):
            key = (nid, constraints)
            if key not in cache:
                options = self.request(nid, blocked | set(constraints), seed=seed)
                cache[key] = options[0] if options else None
            return cache[key]

        constraints = {nid: frozenset() for nid in group}
        routes = {nid: plan(nid, constraints[nid]) for nid in group}
        if any(c is None for c in routes.values()):
            return best
        cost = sum(c["delay"] for c in routes.values())
        heap = [(cost, 0, constraints, routes)]
        serial, expanded = 0, 0
        seen = set()
        while heap and expanded < node_limit and time.perf_counter() < self.deadline:
            cost, _, constraints, routes = heapq.heappop(heap)
            if cost >= best_cost:
                continue
            expanded += 1
            occupied = {}
            conflict = None
            for nid in group:
                for v in sorted(routes[nid]["vertices"]):
                    if v in occupied:
                        conflict = (v, occupied[v], nid)
                        break
                    occupied[v] = nid
                if conflict:
                    break
            if conflict is None:
                best, best_cost = routes, cost
                break  # Best-first with exact net costs proves the group optimum.
            v, a, b = conflict
            for nid in (a, b):
                child_constraints = dict(constraints)
                child_constraints[nid] = constraints[nid] | {v}
                signature = tuple((n, child_constraints[n]) for n in group)
                if signature in seen:
                    continue
                seen.add(signature)
                candidate = plan(nid, child_constraints[nid])
                if candidate is None:
                    continue
                child_routes = dict(routes)
                child_routes[nid] = candidate
                child_cost = sum(c["delay"] for c in child_routes.values())
                if child_cost < best_cost:
                    serial += 1
                    heapq.heappush(heap, (child_cost, serial, child_constraints, child_routes))
        return best

    def groups(self, seed=0, *, size=2, candidates=2, limit=100, cbs_nodes=0):
        """Prioritize nets occupying another net's ideal paths; try all orders."""
        proposals = []
        for nid in self.routes:
            gap = self.routes[nid]["delay"] - self.lower_bounds[nid]
            if gap <= 0:
                continue
            blockers = defaultdict(int)
            for v in self.ideal[nid]:
                owner = self.owners.get(v)
                if owner is not None and owner != nid:
                    blockers[owner] += 1
            for other in sorted(blockers, key=lambda b: (-blockers[b], b))[:6]:
                group = {nid, other}
                if size == 3:
                    extras = [b for b in sorted(blockers, key=lambda b: (-blockers[b], b)) if b != other]
                    if not extras:
                        continue
                    group.add(extras[0])
                proposals.append((gap * blockers[other], tuple(sorted(group))))
        seen, attempts, changes = set(), 0, 0
        for _, group in sorted(proposals, key=lambda x: (-x[0], x[1])):
            if time.perf_counter() >= self.deadline:
                break
            if group in seen:
                continue
            seen.add(group)
            attempts += 1
            if attempts > limit:
                break
            blocked = self.blocked(set(group))
            best = {nid: self.routes[nid] for nid in group}
            best_cost = sum(c["delay"] for c in best.values())
            for ordering in permutations(group):
                if time.perf_counter() >= self.deadline:
                    break
                first_options = self.request(ordering[0], blocked, seed=seed, k=candidates)
                for first in first_options:
                    trial = {ordering[0]: first}
                    occupied = blocked | first["vertices"]
                    for nid in ordering[1:]:
                        options = self.request(nid, occupied, seed=seed)
                        if not options:
                            break
                        trial[nid] = options[0]
                        occupied |= options[0]["vertices"]
                    if len(trial) == len(group):
                        cost = sum(c["delay"] for c in trial.values())
                        if cost < best_cost:
                            best, best_cost = trial, cost
            if cbs_nodes:
                best = self.conflict_search(group, blocked, best, seed, cbs_nodes)
            if self.accept(best):
                changes += 1
        return changes, min(attempts, limit)

    def negotiate_group(self, group, blocked, independent, incumbent, seed, iterations):
        routes = dict(independent)
        owners, history = defaultdict(set), defaultdict(float)
        for nid, candidate in routes.items():
            for v in candidate["vertices"]:
                owners[v].add(nid)
        best = incumbent
        best_cost = sum(c["delay"] for c in incumbent.values())
        present = .5
        for iteration in range(iterations):
            if time.perf_counter() >= self.deadline:
                break
            conflicts = {v: ns for v, ns in owners.items() if len(ns) > 1}
            if not conflicts:
                cost = sum(c["delay"] for c in routes.values())
                if cost < best_cost:
                    best, best_cost = dict(routes), cost
                break
            affected = set().union(*conflicts.values())
            for v, ns in conflicts.items():
                history[v] += .5 * (len(ns)-1)
            order = sorted(affected, key=lambda n: (-(routes[n]["delay"] - self.lower_bounds[n]), n))
            for nid in order:
                if time.perf_counter() >= self.deadline:
                    break
                old = routes[nid]
                for v in old["vertices"]:
                    owners[v].remove(nid)
                    if not owners[v]:
                        del owners[v]
                prices = dict(history)
                for v, ns in owners.items():
                    prices[v] = prices.get(v, 0) + present * len(ns)
                options = self.request(nid, blocked, seed=seed+iteration, prices=prices)
                candidate = options[0] if options else old
                routes[nid] = candidate
                for v in candidate["vertices"]:
                    owners[v].add(nid)
            present = min(1000, present * 1.4)
        if all(len(ns) == 1 for ns in owners.values()):
            cost = sum(c["delay"] for c in routes.values())
            if cost < best_cost:
                best = routes
        return best

    def guided_moves(self, seed=0, *, limit=25, temperature=8):
        """Let blockers take short legal detours to free a target's cheaper path.

        Unlike exact single-net refinement, these moves can temporarily raise
        cost. They must reduce overlap with the desired path and remain legal.
        The best complete routing is retained independently of the working state.
        """
        rng = random.Random(seed)
        targets = sorted(self.routes, key=lambda n: (-(self.routes[n]["delay"]-self.lower_bounds[n]), n))
        changes, attempts = 0, 0
        for target in targets:
            if attempts >= limit or time.perf_counter() >= self.deadline:
                break
            if self.routes[target]["delay"] <= self.lower_bounds[target]:
                continue
            attempts += 1
            desired = self.request(target, seed=seed)[0]
            blockers = defaultdict(int)
            for v in desired["vertices"]:
                other = self.owners.get(v)
                if other is not None and other != target:
                    blockers[other] += 1
            ordered = sorted(blockers, key=lambda n:(-blockers[n], n))[:6]
            rng.shuffle(ordered)
            for nid in ordered:
                if time.perf_counter() >= self.deadline:
                    break
                price = rng.choice((.5, 2., 5., 10.))
                options = self.request(nid, self.blocked({nid}), seed=seed,
                                       prices={v:price for v in desired["vertices"]})
                if not options:
                    continue
                candidate = options[0]
                overlap = len(candidate["vertices"] & desired["vertices"])
                previous = len(self.routes[nid]["vertices"] & desired["vertices"])
                if overlap < previous and self.accept({nid:candidate}, allow_equal=True, max_increase=temperature):
                    changes += 1
            if time.perf_counter() < self.deadline:
                candidate = self.request(target, self.blocked({target}), seed=seed)[0]
                if self.accept({target:candidate}, allow_equal=True):
                    changes += 1
        return changes, attempts

    def neighborhoods(self, seed=0, *, limit=30, max_size=10, retries=6, local_iters=0,
                      temperature=0, regional=False, desired_routes=None, target_order=None):
        """Rip up a target and its path blockers, then try deterministic orders.

        Large neighborhoods use bounded sequential planning rather than factorial
        permutations or unbounded CBS. Only fully legal, cheaper groups commit.
        """
        rng = random.Random(seed)
        targets = target_order if target_order is not None else sorted(
            self.routes, key=lambda n: (-(self.routes[n]["delay"] - self.lower_bounds[n]), n))
        changes, attempts = 0, 0
        for target in targets:
            if attempts >= limit or time.perf_counter() >= self.deadline:
                break
            if self.routes[target]["delay"] == self.lower_bounds[target] and target not in (desired_routes or {}):
                continue
            # Refresh the desired route instead of repeatedly choosing blockers
            # from one arbitrary shortest-path tie. Small soft occupancy prices
            # favor paths that can be freed by moving fewer other nets.
            soft_price = (0.0, .05, .2, .5, 1.0, 2.0)[seed % 6]
            prices = {v: soft_price for v, n in self.owners.items() if n != target}
            desired = (desired_routes or {}).get(target)
            if desired is None:
                desired = self.request(target, prices=prices, seed=seed)[0]
            counts = defaultdict(int)
            for v in desired["vertices"]:
                nid = self.owners.get(v)
                if nid is not None and nid != target:
                    counts[nid] += 1
            blockers = sorted(counts, key=lambda n: (-counts[n], n))[:max_size-1]
            if regional:
                # Release routes near a random point of the desired path, across
                # all layers. This allows coupled layer swaps and nearby detours.
                center = self.grid.coord(rng.choice(sorted(desired["vertices"])))
                radius = rng.randrange(2, 7)
                nearby = set()
                for y in range(max(0, center[1]-radius), min(self.inst.height, center[1]+radius+1)):
                    for x in range(max(0, center[0]-radius), min(self.inst.width, center[0]+radius+1)):
                        for z in range(self.inst.layers):
                            nid = self.owners.get(self.grid.vid((x, y, z)))
                            if nid is not None and nid != target and nid not in blockers:
                                nearby.add(nid)
                extras = sorted(nearby)
                rng.shuffle(extras)
                blockers += extras[:max(0, max_size-1-len(blockers))]
            group = [target] + blockers
            if len(group) < 2:
                continue
            attempts += 1
            blocked = self.blocked(set(group))
            best = {n: self.routes[n] for n in group}
            best_cost = sum(c["delay"] for c in best.values())
            original_cost = best_cost
            escape, escape_cost = None, float("inf")
            independent = {n: self.request(n, blocked, seed=seed)[0] for n in group}
            lower = {n: c["delay"] for n, c in independent.items()}
            if sum(lower.values()) >= best_cost + temperature:
                continue
            orders = [group, sorted(group, key=lambda n: (-(self.routes[n]["delay"] - lower[n]), n))]
            for _ in range(max(0, retries-2)):
                tail = blockers[:]
                rng.shuffle(tail)
                orders.append(([target] + tail) if rng.random() < .5 else tail + [target])
            for attempt, ordering in enumerate(orders):
                if time.perf_counter() >= self.deadline:
                    break
                trial, occupied, cost = {}, set(blocked), 0
                for i, nid in enumerate(ordering):
                    if cost + sum(lower[n] for n in ordering[i:]) >= best_cost + temperature:
                        break
                    # Preserve the route that defined this neighborhood. Replacing
                    # it with a different shortest-path tie can invalidate the
                    # blocker selection and miss the intended space exchange.
                    options = ([desired] if i == 0 and nid == target and not desired["vertices"] & occupied
                               else self.request(nid, occupied, seed=seed + attempt))
                    if not options:
                        break
                    candidate = options[0]
                    trial[nid] = candidate
                    occupied |= candidate["vertices"]
                    cost += candidate["delay"]
                if len(trial) == len(group) and cost < best_cost:
                    best, best_cost = trial, cost
                elif (len(trial) == len(group) and original_cost <= cost <= original_cost + temperature
                      and cost < escape_cost):
                    escape, escape_cost = trial, cost
            if local_iters and time.perf_counter() < self.deadline:
                best = self.negotiate_group(group, blocked, independent, best, seed, local_iters)
            if (sum(c["delay"] for c in best.values()) >= original_cost and escape is not None
                    and rng.random() < .25):
                best = escape
            if self.accept(best, allow_equal=temperature > 0, max_increase=temperature):
                changes += 1
        return changes, attempts


def improve(inst, submission, *, passes=8, pair_rounds=4, triple_rounds=0,
            group_limit=100, group_candidates=2, equal_moves=True, seed=0,
            time_limit=300, progress=None, cbs_nodes=0, lns_rounds=0,
            neighborhood_size=10, order_retries=6, local_iters=0,
            temperature=0, regional=False, guided_rounds=0, exact_prices=False):
    if any(type(v) is not int or v < 0 for v in (passes, pair_rounds, triple_rounds, group_limit, seed, cbs_nodes, lns_rounds, guided_rounds)):
        raise ValueError("pass counts, group limit, and seed must be nonnegative integers")
    if group_candidates < 1 or not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("candidate count and time limit must be positive")
    if (type(neighborhood_size) is not int or neighborhood_size < 2 or
            type(order_retries) is not int or order_retries < 1 or
            type(local_iters) is not int or local_iters < 0 or
            not math.isfinite(temperature) or temperature < 0):
        raise ValueError("invalid neighborhood or temperature parameters")
    start = time.perf_counter()
    trace = []
    with NativeSession(inst) as worker:
        refiner = Refiner(inst, submission, worker, start + time_limit, exact_prices=exact_prices)
        initial_delay = refiner.delay
        refiner.initialize_bounds()
        bound = sum(refiner.lower_bounds.values())

        def record(stage, iteration, changes, attempts=0):
            row = dict(stage=stage, iteration=iteration, delay=refiner.best_delay,
                       working_delay=refiner.delay,
                       excess=refiner.best_delay - sum(refiner.lower_bounds.values()),
                       changes=changes, group_attempts=attempts, queries=refiner.queries,
                       elapsed_seconds=time.perf_counter() - start)
            trace.append(row)
            if progress:
                progress(row)

        record("start", 0, 0)
        for iteration in range(passes):
            if time.perf_counter() - start >= time_limit or refiner.best_delay == bound:
                break
            changes = refiner.singles(seed + iteration, equal_moves=equal_moves)
            record("single", iteration + 1, changes)
            if not changes:
                break
        for size, rounds in ((2, pair_rounds), (3, triple_rounds)):
            for iteration in range(rounds):
                if time.perf_counter() - start >= time_limit or refiner.best_delay == bound:
                    break
                changes, attempts = refiner.groups(seed + iteration, size=size,
                                                   candidates=group_candidates, limit=group_limit,
                                                   cbs_nodes=cbs_nodes)
                record(f"group{size}", iteration + 1, changes, attempts)
                changes = refiner.singles(seed + passes + iteration, equal_moves=equal_moves)
                record("single_after_group", iteration + 1, changes)
        for iteration in range(lns_rounds):
            if time.perf_counter() - start >= time_limit or refiner.best_delay == bound:
                break
            changes, attempts = refiner.neighborhoods(seed + iteration, limit=group_limit,
                                                       max_size=neighborhood_size, retries=order_retries,
                                                       local_iters=local_iters, regional=regional,
                                                       temperature=temperature * (1 - iteration / max(1, lns_rounds)))
            record("neighborhood", iteration + 1, changes, attempts)
            changes = refiner.singles(seed + passes + iteration, equal_moves=equal_moves)
            record("single_after_neighborhood", iteration + 1, changes)
        for iteration in range(guided_rounds):
            if time.perf_counter() - start >= time_limit or refiner.best_delay == bound:
                break
            changes, attempts = refiner.guided_moves(seed+iteration, limit=group_limit,
                temperature=temperature * (1 - iteration/max(1,guided_rounds)))
            record("guided", iteration+1, changes, attempts)
            if not changes:
                break
        result = as_submission(inst, refiner.best_routes)
        verified = check(inst, result)
        if not verified.legal:
            raise RuntimeError("final refinement failed official validation")
        return result, dict(initial_delay=initial_delay, total_delay=verified.total_delay,
                            lower_bound=sum(refiner.lower_bounds.values()),
                            improvement=initial_delay - verified.total_delay,
                            accepted_improvements=refiner.accepted, equal_moves=refiner.equal_moves,
                            uphill_moves=refiner.uphill_moves,
                            queries=refiner.queries, seconds=time.perf_counter() - start, trace=trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("benchmarks"))
    parser.add_argument("--start-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--passes", type=int, default=8)
    parser.add_argument("--pair-rounds", type=int, default=4)
    parser.add_argument("--triple-rounds", type=int, default=0)
    parser.add_argument("--group-limit", type=int, default=100)
    parser.add_argument("--group-candidates", type=int, default=2)
    parser.add_argument("--cbs-nodes", type=int, default=0)
    parser.add_argument("--lns-rounds", type=int, default=0)
    parser.add_argument("--neighborhood-size", type=int, default=10)
    parser.add_argument("--order-retries", type=int, default=6)
    parser.add_argument("--local-iters", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--regional", action="store_true")
    parser.add_argument("--guided-rounds", type=int, default=0)
    parser.add_argument("--exact-prices", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--time-limit", type=float, default=300)
    parser.add_argument("--no-equal-moves", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.suite / "suite.json").read_text())
    rows = []
    for case in manifest["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite / case["instance_file"]))
        submission = Submission.load(str(args.start_dir / (inst.name + ".sol.json")))
        def progress(row):
            print(f"{inst.name} {row['stage']} {row['iteration']}: delay={row['delay']} "
                  f"excess={row['excess']} changes={row['changes']} elapsed={row['elapsed_seconds']:.2f}s", flush=True)
        result, metrics = improve(inst, submission, passes=args.passes, pair_rounds=args.pair_rounds,
                                  triple_rounds=args.triple_rounds, group_limit=args.group_limit,
                                  group_candidates=args.group_candidates, seed=args.seed,
                                  time_limit=args.time_limit, equal_moves=not args.no_equal_moves,
                                  progress=progress, cbs_nodes=args.cbs_nodes, lns_rounds=args.lns_rounds,
                                  neighborhood_size=args.neighborhood_size, order_retries=args.order_retries,
                                  local_iters=args.local_iters, temperature=args.temperature, regional=args.regional,
                                  guided_rounds=args.guided_rounds, exact_prices=args.exact_prices)
        result.save(str(args.out_dir / (inst.name + ".sol.json")))
        metrics.update(case=inst.name, baseline=case["baseline_total"], legal=True,
                       score=case["baseline_total"] / metrics["total_delay"])
        rows.append(metrics)
        aggregate = math.exp(sum(math.log(r["score"]) for r in rows) / len(rows))
        report = dict(config=vars(args) | {k: str(v) for k, v in vars(args).items() if isinstance(v, Path)},
                      cases=rows, completed_cases=len(rows), selected_case_score=aggregate,
                      complete=len(rows) == manifest["n_cases"],
                      aggregate_score=aggregate if len(rows) == manifest["n_cases"] else 0)
        (args.out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Completed {len(rows)} cases: selected-case score={aggregate:.6f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
