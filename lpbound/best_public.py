"""Best legal public total per case in a tier (scored with the repo checker)."""
import glob
import json
import os
import sys

from m3d.checker import check
from m3d.model import Instance, Submission

TIERS = {'intro': 'benchmarks', 'hard': 'benchmarks_hard', 'scale': 'benchmarks_scale',
         'congested': 'benchmarks_congested', 'designs': 'benchmarks_designs', 'stress': 'benchmarks_stress'}


def best(tier):
    bd = TIERS[tier]
    suite = json.load(open(f'{bd}/suite.json'))
    out = {}
    for c in suite['cases']:
        inst = Instance.load(f"{bd}/{c['instance_file']}")
        top = None
        for f in sorted(glob.glob(f"submissions/{tier}/*/{c['name']}.sol.json")):
            r = check(inst, Submission.load(f))
            if r.legal and (top is None or r.total_delay < top[0]):
                top = (r.total_delay, f)
        out[c['name']] = dict(instance=f"{bd}/{c['instance_file']}", baseline=c['baseline_total'],
                              best=top[0], best_file=top[1])
        print(c['name'], out[c['name']], flush=True)
    return out


if __name__ == '__main__':
    tier = sys.argv[1]
    json.dump(best(tier), open(f'lpbound/runs/best_{tier}.json', 'w'), indent=1)
