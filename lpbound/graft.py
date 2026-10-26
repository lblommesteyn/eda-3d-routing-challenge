"""Recombine our own runs: graft a region from a donor solution into the incumbent.

A region is one net where the donor is cheaper, grown (up to a random cap of 1-10 nets)
by the incumbent nets its donor routes collide with, taken from the donor as well. The optimizer (mode bit
4096) paints the grafted routes, rips up colliding incumbent nets, renegotiates them,
then anneals briefly; the result replaces the incumbent only when it is strictly lower.

    python -m lpbound.graft <case.json> <incumbent.sol.json> <out.sol.json> <seconds> <donor.sol.json>...
"""
import random
import sys
import time

sys.path.insert(0, "routers/priced_cpp")
from m3d.checker import check  # noqa: E402
from m3d.model import Instance, Submission  # noqa: E402
from native import optimize  # noqa: E402

BINARY = "routers/priced_cpp/build/optimizer_graft"


def boxes(inst):
    pins = {p.id: p.vertex() for p in inst.pins}
    out = {}
    for n in inst.nets:
        xs = [pins[p][0] for p in n.pins()]; ys = [pins[p][1] for p in n.pins()]
        out[n.id] = (min(xs), max(xs), min(ys), max(ys))
    return out


def overlap(a, b, pad):
    return a[0] - pad <= b[1] and b[0] - pad <= a[1] and a[2] - pad <= b[3] and b[2] - pad <= a[3]


def main(case, incumbent, out, seconds, *donors, anneal=60.0, temperature=3.0, pad=2):
    inst = Instance.load(case)
    box = boxes(inst)
    cur = Submission.load(incumbent)
    cur_check = check(inst, cur)
    assert cur_check.legal
    pool = []
    for f in donors:
        d = Submission.load(f); dc = check(inst, d)
        if dc.legal:
            pool.append((f, {r.net: r for r in d.routes}, {r.net: r.delay for r in dc.nets}))
    rng = random.Random(1)
    deadline = time.time() + float(seconds)
    tries = wins = 0
    best = cur_check.total_delay
    print("start", best, "donors", len(pool), flush=True)
    while time.time() < deadline:
        routes = {r.net: r for r in cur.routes}
        delays = {r.net: r.delay for r in check(inst, cur).nets}
        f, droutes, ddelays = rng.choice(pool)
        better = [n for n in ddelays if ddelays[n] < delays[n]]
        if not better:
            continue
        seed_net = rng.choice(better)
        cap = rng.randint(1, 10)
        cells = {n: set(v for e in routes[n].edges for v in e) for n in routes}
        region, frontier = {seed_net}, [seed_net]
        while frontier and len(region) < cap:
            m = frontier.pop(0)
            mine = set(v for e in droutes[m].edges for v in e)
            for n in sorted(routes, key=lambda _: rng.random()):
                if n not in region and len(region) < cap and cells[n] & mine and droutes[n].edges != routes[n].edges:
                    region.add(n); frontier.append(n)
        hybrid = Submission(inst.name, [droutes[n.id] if n.id in region else routes[n.id] for n in inst.nets])
        hd = {n.id: (ddelays[n.id] if n.id in region else delays[n.id]) for n in inst.nets}
        tries += 1
        try:
            res, info = optimize(inst, hybrid, seconds=anneal, iterations=10**9, temperature=temperature,
                                 seed=rng.randrange(2**62), max_group=12, neighbor_polish=True,
                                 negotiate=(30, 80, 0.5, 1.15, 0.5), cycle=0, binary=BINARY,
                                 graft=sorted(region), graft_delays=hd)
        except RuntimeError as e:
            print("try", tries, "region", len(region), "failed", str(e)[:80], flush=True)
            continue
        total = check(inst, res).total_delay
        log = [l for l in info["native_log"].splitlines() if l.startswith("graft")]
        print("try", tries, "donor", f.split("/")[-2], "region", len(region), log[0] if log else "", "->", total,
              "best", best, flush=True)
        if total < best:
            best, cur = total, res; wins += 1
            cur.save(out)
            print("IMPROVED", best, flush=True)
    print("FINISHED tries", tries, "wins", wins, "final", best, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], *sys.argv[5:])
