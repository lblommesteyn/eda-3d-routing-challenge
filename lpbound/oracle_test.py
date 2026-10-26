import sys
sys.path.insert(0, "routers/priced_cpp")
from collections import defaultdict
from m3d.checker import check
from m3d.model import Instance, Submission
from controller import NativeSession
from improve import Refiner
case, sol = sys.argv[1:3]
inst = Instance.load(case); sub = Submission.load(sol)
with NativeSession(inst) as w:
    r = Refiner(inst, sub, w)
    worse = defaultdict(list); tot = defaultdict(int)
    for n in inst.nets:
        p = len(n.pins())
        if p < 3: continue
        blocked = r.blocked({n.id})
        best = None
        for strat in ("spt", "steiner", "compact", "greedy"):
            for seed in range(4):
                try:
                    opts = w.request(n.id, {}, blocked, 8, strategy=strat, seed=seed)
                except Exception as e:
                    continue
                for o in opts:
                    best = o["delay"] if best is None else min(best, o["delay"])
        have = r.routes[n.id]["delay"]
        tot[min(p,6)] += 1
        if best is None or best > have: worse[min(p,6)].append((n.id, have, best))
    for k in sorted(tot): print("pins", k, "nets", tot[k], "our oracle worse on", len(worse[k]), worse[k][:6])
