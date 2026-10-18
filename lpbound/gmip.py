"""Global restricted re-route: every net at once, each kept within its bounding box + margin.

    python -m lpbound.gmip <case.json> <start.sol.json> <out.sol.json> <seconds> [margin] [threads]
"""
import sys
import time

from lpbound import wlns
from lpbound.cg import Instance

if __name__ == '__main__':
    case, start, out, secs = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
    margin = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    threads = int(sys.argv[6]) if len(sys.argv) > 6 else 8
    I = Instance(case)
    st = wlns.State(I, start)
    t = time.time()
    print('start', st.total(), flush=True)
    g, why = wlns.window(st, (0, I.W, 0, I.H), secs, threads, max_nets=10**9, max_sinks=10**9,
                         margin=margin, max_nodes=10**9, verbose=True)
    print('result', why, 'gain', g, 'total', st.total(), f'{time.time()-t:.0f}s', flush=True)
    if g > 0:
        st.save(out)
