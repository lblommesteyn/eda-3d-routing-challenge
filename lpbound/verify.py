"""Independent re-check of a Lagrangian lower bound.

Shares no code with lagr.py/cg.py: reads the instance JSON, builds the grid
graph itself, runs a heapq Dijkstra per sink with foreign pins removed and
vertex costs alpha[s, v] charged on entry, and evaluates

    L = sum_s SP_s - sum_v max(0, max_n sum_{s in n} alpha[s, v]).

Any legal routing has total delay >= L (weak duality), and delays are
integers, so ceil(L - 1e-6) is a certified bound up to float rounding.
Sink order must match lagr.sink_list: nets in file order, sinks in list order.
"""
import heapq
import json
import math
import sys

import numpy as np


def main(case, alpha_npz):
    d = json.load(open(case))
    W, H, L = d['grid']['width'], d['grid']['height'], d['grid']['layers']
    ld, vd = d['delay']['layer_delay'], d['delay']['via_delay']
    vid = lambda x, y, z: (z * H + y) * W + x
    pin_v = {p['id']: vid(p['x'], p['y'], p['z']) for p in d['pins']}
    owner = {}
    for k, n in enumerate(d['nets']):
        for p in [n['driver']] + n['sinks']:
            owner[pin_v[p]] = k
    z = np.load(alpha_npz)
    if 'alpha' in z:        # dense (n_sinks, n_vertices)
        rows = [dict(zip(np.nonzero(r)[0].tolist(), r[np.nonzero(r)[0]].tolist())) for r in z['alpha']]
    else:                   # sparse rows from lagr_win.py
        off, vert, val = z['off'], z['vert'], z['val']
        rows = [dict(zip(vert[off[i]:off[i + 1]].tolist(), val[off[i]:off[i + 1]].tolist())) for i in range(len(off) - 1)]
    assert all(x >= 0 for r in rows for x in r.values()), 'alpha must be nonnegative'

    def nbrs(v):
        z, r = divmod(v, W * H)
        y, x = divmod(r, W)
        if x > 0: yield v - 1, ld[z]
        if x < W - 1: yield v + 1, ld[z]
        if y > 0: yield v - W, ld[z]
        if y < H - 1: yield v + W, ld[z]
        if z > 0: yield v - W * H, vd
        if z < L - 1: yield v + W * H, vd

    total_sp = 0.0
    A = {}
    i = 0
    for k, n in enumerate(d['nets']):
        src = pin_v[n['driver']]
        for s in n['sinks']:
            t = pin_v[s]
            a = rows[i]
            for v, x in a.items():
                if v in owner:
                    raise AssertionError('alpha on a pin vertex')
                A[(k, v)] = A.get((k, v), 0.0) + x
            dist = {src: 0.0}
            pq = [(0.0, src)]
            while pq:
                du, u = heapq.heappop(pq)
                if u == t:
                    break
                if du > dist[u]:
                    continue
                for v, c in nbrs(u):
                    if owner.get(v, k) != k:
                        continue
                    nd = du + c + a.get(v, 0.0)
                    if nd < dist.get(v, math.inf):
                        dist[v] = nd
                        heapq.heappush(pq, (nd, v))
            total_sp += dist[t]
            i += 1
    assert i == len(rows), 'sink count mismatch'
    pen = {}
    for (k, v), x in A.items():
        pen[v] = max(pen.get(v, 0.0), x)
    bound = total_sp - sum(pen.values())
    return bound


if __name__ == '__main__':
    b = main(sys.argv[1], sys.argv[2])
    print(json.dumps({'case': sys.argv[1], 'L': b, 'certified_int_bound': math.ceil(b - 1e-6)}))
