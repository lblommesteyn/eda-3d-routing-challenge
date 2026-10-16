"""Assemble submissions/<tier>/<name>/ from the window-LNS results.

Per case: the lower of lpbound/win/<tier>_<case>.sol.json and the pool best
(lpbound/runs/pool_<tier>.json), both re-checked with m3d/checker.py. runtime.json
is the summed wall time of the window-LNS runs on that case (from its log), and
meta.json credits every upstream entry a route came from.

    python -m lpbound.make_submission <tier> <name> [author]
"""
import json
import os
import re
import shutil
import sys
from datetime import date

from m3d.checker import check
from m3d.model import Instance, Submission


def run_seconds(log):
    """Sum over runs in the log of the last elapsed time each run printed."""
    if not os.path.exists(log):
        return 0.0
    total, last = 0.0, 0.0
    for line in open(log):
        if line.startswith('start '):
            total, last = total + last, 0.0
        m = re.search(r' t (\d+)s', line)
        if m:
            last = float(m.group(1))
    return total + last


def main(tier, name, author='lblommesteyn'):
    pool = json.load(open(f'lpbound/runs/pool_{tier}.json'))
    out = f'submissions/{tier}/{name}'
    os.makedirs(out, exist_ok=True)
    runtime, cases, upstream = {}, [], {}
    for c, v in pool.items():
        inst = Instance.load(v['instance'])
        src = v['best_file']
        parts = src.split('/')
        up = parts[5]
        meta_up = os.path.join(*parts[:6], 'meta.json')
        up_author = json.load(open(meta_up)).get('author', '') if os.path.exists(meta_up) else ''
        upstream[up] = up_author
        best, how = v['best'], 'retained public route'
        mine = f'lpbound/win/{tier}_{c}.sol.json'
        if os.path.exists(mine):
            r = check(inst, Submission.load(mine))
            if r.legal and r.total_delay < best:
                src, best, how = mine, r.total_delay, 'dual-guided exact window re-route (lpbound/wlns.py)'
        r = check(inst, Submission.load(src))
        assert r.legal and r.total_delay == best, (c, src)
        shutil.copy(src, f'{out}/{c}.sol.json')
        runtime[c] = round(run_seconds(f'lpbound/win/{tier}_{c}.log'), 1)
        cases.append(dict(case=c, total_delay=best, best_public=v['best'], method=how,
                          start=dict(submission=up, author=up_author, route=v['best_file'].split('/', 3)[3],
                                     source=parts[2])))
        print(c, v['best'], '->', best, how, flush=True)
    meta = dict(author=author, url='https://github.com/lblommesteyn/eda-3d-routing-challenge/tree/lpbound',
                method='Best public route per case, refined by exact joint re-routing (HiGHS MILP) of every net '
                       'touching a window, with windows aimed by Lagrangian dual path slack.',
                date=str(date.today()), hardware='AMD Ryzen 7 5800X (8 cores / 16 threads), 48 GB RAM, Windows 11; one case per worker, up to 16 workers at once', threads=1,
                runtime_note='Wall time of the refinement runs only; excludes the compute behind the public routes.',
                derived_from=dict(submission='; '.join(sorted(upstream)),
                                  author='; '.join(sorted({a for a in upstream.values() if a}))),
                cases=cases)
    json.dump(runtime, open(f'{out}/runtime.json', 'w'), indent=1)
    json.dump(meta, open(f'{out}/meta.json', 'w'), indent=1)


if __name__ == '__main__':
    main(*sys.argv[1:])
