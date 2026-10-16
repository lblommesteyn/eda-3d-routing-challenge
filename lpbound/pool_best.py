"""Best legal total per case over merged entries and open upstream PRs (lpbound/pool/).

    python -m lpbound.pool_best <tier>     -> lpbound/runs/pool_<tier>.json
"""
import glob
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor

from lpbound.best_public import TIERS


def _score(args):
    inst_path, f = args
    from m3d.checker import check
    from m3d.model import Instance, Submission
    r = check(Instance.load(inst_path), Submission.load(f))
    return f, r.legal, r.total_delay


def best(tier, workers=8):
    bd = TIERS[tier]
    suite = json.load(open(f'{bd}/suite.json'))
    out = {}
    for c in suite['cases']:
        inst = f"{bd}/{c['instance_file']}"
        seen = {}
        for f in sorted(glob.glob(f"lpbound/pool/*/submissions/{tier}/*/{c['name']}.sol.json")):
            h = hashlib.md5(open(f, 'rb').read()).hexdigest()
            seen.setdefault(h, f.replace("\\", "/"))
        with ProcessPoolExecutor(workers) as ex:
            res = list(ex.map(_score, [(inst, f) for f in seen.values()]))
        legal = sorted((d, f) for f, ok, d in res if ok)
        out[c['name']] = dict(instance=inst, baseline=c['baseline_total'], best=legal[0][0], best_file=legal[0][1],
                              top=[(d, f.split('/')[2] + '/' + f.split('/')[5]) for d, f in legal[:5]])
        print(c['name'], out[c['name']]['best'], out[c['name']]['top'][:3], flush=True)
    return out


if __name__ == '__main__':
    tier = sys.argv[1]
    json.dump(best(tier), open(f'lpbound/runs/pool_{tier}.json', 'w'), indent=1)
