import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
B = 'lpbound/runs/big/'
wave1 = ['congested_case_02', 'congested_case_03', 'congested_case_04', 'designs_router']
while not all(os.path.exists(B + n + '.json') for n in wave1):
    time.sleep(30)
# hard tier with the fast code, one at a time in the free slot
for name, r in json.load(open('lpbound/runs/best_hard.json')).items():
    out = f'lpbound/runs/hard3/{name}'
    with open(out + '.log', 'w') as log:
        subprocess.run([sys.executable, '-m', 'lpbound.lagr_win', r['instance'], r['best_file'].replace(os.sep, '/'),
                        '900', out, '-', '2'], stdout=log, stderr=subprocess.STDOUT)
allb = wave1 + ['congested_case_01', 'designs_ctrl', 'designs_int2float']
while not all(os.path.exists(B + n + '.json') for n in allb):
    time.sleep(30)
def warm(n):
    r = json.load(open(B + n + '.json'))
    case = r['case']
    with open(f'lpbound/runs/big2/{n}.log', 'w') as log:
        subprocess.run([sys.executable, '-m', 'lpbound.lagr_win', case, r['sol'], '7200', f'lpbound/runs/big2/{n}',
                        B + n + '.alpha.npz', '2'], stdout=log, stderr=subprocess.STDOUT)
with ThreadPoolExecutor(4) as ex:
    list(ex.map(warm, wave1))
