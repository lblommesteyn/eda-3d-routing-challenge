"""Best single run per case from lpbound/campaign/<tier>: python -m lpbound.campaign_best <tier> [out_dir]"""
import json, shutil, sys
from pathlib import Path
tier = sys.argv[1]
root = Path('lpbound/campaign')/tier
suite = json.load(open(f'benchmarks_{tier}/suite.json'))
best, counts = {}, {}
for c in suite['cases']:
    n = c['name']
    runs = [json.loads(p.read_text()) | {'dir': p.parent} for p in (root/n).glob('s*/'+n+'.run.json')]
    counts[n] = sorted(r['total_delay'] for r in runs)
    if runs:
        best[n] = min(runs, key=lambda r: r['total_delay'])
for n, v in counts.items():
    print(n, 'runs', len(v), 'best', v[:1], 'median', v[len(v)//2] if v else None)
if len(sys.argv) > 2 and len(best) == len(suite['cases']):
    out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    rt = {}
    for n, r in best.items():
        shutil.copy(r['dir']/(n+'.sol.json'), out/(n+'.sol.json'))
        rt[n] = round(r['seconds'], 3)
    (out/'runtime.json').write_text(json.dumps(rt, indent=1)+'\n')
    print('wrote', out)
