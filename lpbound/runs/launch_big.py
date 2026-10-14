import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
jobs = []
for tier in ('congested', 'designs'):
    for name, r in json.load(open(f'lpbound/runs/best_{tier}.json')).items():
        jobs.append((r['instance'], r['best_file'].replace(os.sep, '/'), f'lpbound/runs/big/{tier}_{name}'))
jobs.sort(key=lambda j: -os.path.getsize(j[0]))   # largest first
def go(j):
    inst, sol, out = j
    with open(out + '.log', 'w') as log:
        subprocess.run([sys.executable, '-m', 'lpbound.lagr_win', inst, sol, '7200', out, '-', '2'], stdout=log, stderr=subprocess.STDOUT)
with ThreadPoolExecutor(4) as ex:
    list(ex.map(go, jobs))
