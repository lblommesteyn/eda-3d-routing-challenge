import glob, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
cases = [c for c in sorted(glob.glob('lpbound/calib/small/s*_?.json')) if not c.endswith('s8n7_0.json')]
def go(c):
    with open(c.replace('.json', '.log'), 'w') as log:
        subprocess.run([sys.executable, '-m', 'lpbound.calib_small', 'solve', c, '2400', '2'], stdout=log, stderr=subprocess.STDOUT)
with ThreadPoolExecutor(3) as ex:
    list(ex.map(go, cases))
