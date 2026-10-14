"""Column-generation LP lower bound for an m3d instance.

Relaxation (every legal routing is feasible, so the optimum is a lower bound):

    min  sum_{s,p} c_p lam[s,p]
    s.t. sum_p lam[s,p] = 1                    for each sink s        (mu_s)
         sum_{p ∋ v} lam[s,p] - y[n(s),v] <= 0  for each sink s, vertex v  (-alpha_sv)
         sum_n y[n,v] <= 1                     for each vertex v      (-pi_v)
         paths avoid other nets' pins; lam, y >= 0

Rows/columns are generated lazily. Pricing for sink s is a Dijkstra with vertex
costs alpha_s (zero where no row exists, which is exactly the dual value of the
extended solution), so a round with no negative reduced cost proves LP
optimality. At every round  RMP + sum_s min(0, rc_s)  is a valid lower bound.
"""
import json
import sys
import time
from collections import defaultdict, deque

import highspy
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

INF = 1e9


class Instance:
    def __init__(self, path):
        d = json.load(open(path))
        self.name = d.get('name') or path
        g = d['grid']
        self.W, self.H, self.L = g['width'], g['height'], g['layers']
        self.ld = d['delay']['layer_delay']
        self.vd = d['delay']['via_delay']
        self.nv = self.W * self.H * self.L
        self.pins = {p['id']: self.vid(p['x'], p['y'], p['z']) for p in d['pins']}
        self.nets = [(n['id'], self.pins[n['driver']], [self.pins[s] for s in n['sinks']]) for n in d['nets']]
        self.pin_owner = np.full(self.nv, -1, dtype=np.int64)
        for k, (nid, drv, sinks) in enumerate(self.nets):
            for v in [drv] + sinks:
                self.pin_owner[v] = k
        # directed arcs; weight = delay of the edge
        src, dst, w = [], [], []
        W, H, L = self.W, self.H, self.L
        for z in range(L):
            for y in range(H):
                for x in range(W):
                    u = self.vid(x, y, z)
                    for dx, dy, dz in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                        X, Y, Z = x + dx, y + dy, z + dz
                        if X < W and Y < H and Z < L:
                            v = self.vid(X, Y, Z)
                            c = self.vd if dz else self.ld[z]
                            src += [u, v]; dst += [v, u]; w += [c, c]
        order = np.lexsort((np.array(dst), np.array(src)))
        self.asrc = np.array(src)[order]
        self.adst = np.array(dst)[order]
        self.aw = np.array(w, dtype=float)[order]
        self.indptr = np.searchsorted(self.asrc, np.arange(self.nv + 1))

    def vid(self, x, y, z):
        return (z * self.H + y) * self.W + x

    def edge_cost(self, u, v):
        zu, zv = u // (self.W * self.H), v // (self.W * self.H)
        return self.vd if zu != zv else self.ld[zu]

    def path_cost(self, p):
        return sum(self.edge_cost(a, b) for a, b in zip(p, p[1:]))

    def net_weights(self, k):
        """Arc weights for net k: arcs into foreign pins are blocked."""
        owner = self.pin_owner[self.adst]
        return np.where((owner >= 0) & (owner != k), INF, self.aw)


def solution_paths(inst, sol_path):
    """Driver->sink vertex paths of each sink in a submission's trees."""
    sol = json.load(open(sol_path))
    byid = {nid: k for k, (nid, _, _) in enumerate(inst.nets)}
    out = {}
    for r in sol['routes']:
        k = byid[r['net']]
        _, drv, sinks = inst.nets[k]
        adj = defaultdict(list)
        for a, b in r['edges']:
            u, v = inst.vid(*a), inst.vid(*b)
            adj[u].append(v); adj[v].append(u)
        par = {drv: None}; q = deque([drv])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in par:
                    par[v] = u; q.append(v)
        for j, s in enumerate(sinks):
            p = [s]
            while par[p[-1]] is not None:
                p.append(par[p[-1]])
            out[(k, j)] = p[::-1]
    return out


class Master:
    def __init__(self, inst):
        self.inst = inst
        h = self.h = highspy.Highs()
        h.setOptionValue('output_flag', False)
        h.setOptionValue('solver', 'simplex')
        self.sinks = [(k, j) for k, (_, _, ss) in enumerate(inst.nets) for j in range(len(ss))]
        self.conv = {}
        for s in self.sinks:
            self.conv[s] = h.getNumRow()
            h.addRow(1.0, 1.0, 0, np.array([], np.int32), np.array([], float))
        self.cap = {}        # v -> row
        self.yvar = {}       # (k, v) -> col
        self.link = {}       # (s, v) -> row
        self.cols = defaultdict(set)   # s -> set of path tuples
        self.colinfo = []    # col index -> (s, path) or None for y

    def _cap_row(self, v):
        if v not in self.cap:
            self.cap[v] = self.h.getNumRow()
            self.h.addRow(-highspy.kHighsInf, 1.0, 0, np.array([], np.int32), np.array([], float))
        return self.cap[v]

    def _y(self, k, v):
        if (k, v) not in self.yvar:
            r = self._cap_row(v)
            self.yvar[(k, v)] = self.h.getNumCol()
            self.colinfo.append(None)
            self.h.addCol(0.0, 0.0, highspy.kHighsInf, 1, np.array([r], np.int32), np.array([1.0]))
        return self.yvar[(k, v)]

    def _link_row(self, s, v):
        if (s, v) not in self.link:
            c = self._y(s[0], v)
            self.link[(s, v)] = self.h.getNumRow()
            self.h.addRow(-highspy.kHighsInf, 0.0, 1, np.array([c], np.int32), np.array([-1.0]))
        return self.link[(s, v)]

    def add_path(self, s, path):
        path = tuple(path)
        if path in self.cols[s]:
            return False
        self.cols[s].add(path)
        k = s[0]
        own = self.inst.pin_owner
        idx = [self.conv[s]] + [self._link_row(s, v) for v in path if own[v] != k]
        self.colinfo.append((s, path))
        self.h.addCol(float(self.inst.path_cost(list(path))), 0.0, highspy.kHighsInf,
                      len(idx), np.array(idx, np.int32), np.ones(len(idx)))
        return True

    def solve(self):
        self.h.run()
        sol = self.h.getSolution()
        obj = self.h.getInfo().objective_function_value
        return obj, np.array(sol.row_dual), np.array(sol.col_value)


def rmp_duals(m, row_dual):
    alpha = defaultdict(dict)
    for (s, v), r in m.link.items():
        a = -row_dual[r]
        if a > 1e-12:
            alpha[s][v] = a
    pi = {v: max(0.0, -row_dual[r]) for v, r in m.cap.items()}
    mu = {s: row_dual[r] for s, r in m.conv.items()}
    # Extend to rows not in the RMP: spread each vertex's unused price over the
    # net's sinks that have no link row there. Keeps sum_{s in n} alpha <= pi,
    # so the extended dual is feasible for the full LP and still optimal for
    # the RMP (whose rows are unchanged).
    sinks_of = defaultdict(list)
    for s in m.sinks:
        sinks_of[s[0]].append(s)
    own = m.inst.pin_owner
    for v, p in pi.items():
        if p <= 1e-12:
            continue
        for k, ss in sinks_of.items():
            if own[v] >= 0:
                continue   # pins: only the owner may enter, no capacity contest
            missing = [s for s in ss if (s, v) not in m.link]
            if not missing:
                continue
            used = sum(alpha[s].get(v, 0.0) for s in ss)
            rest = (p - used) / len(missing)
            if rest > 1e-12:
                for s in missing:
                    alpha[s][v] = rest
    return alpha, pi, mu


def blend(c, x, beta):
    """beta*c + (1-beta)*x for alpha / pi dictionaries."""
    if c is None or beta <= 0:
        return x
    if isinstance(next(iter(x.values()), None), dict) or isinstance(next(iter(c.values()), None), dict):
        out = defaultdict(dict)
        for s in set(c) | set(x):
            cs, xs = c.get(s, {}), x.get(s, {})
            for v in set(cs) | set(xs):
                out[s][v] = beta * cs.get(v, 0.0) + (1 - beta) * xs.get(v, 0.0)
        return out
    return {v: beta * c.get(v, 0.0) + (1 - beta) * x.get(v, 0.0) for v in set(c) | set(x)}


def shortest_paths(inst, m, alpha, cache):
    """Per sink: (cost, path) of the min-cost path with vertex costs alpha_s."""
    out = {}
    by_net = defaultdict(list)
    for s in m.sinks:
        by_net[s[0]].append(s)
    for k, ss in by_net.items():
        if k not in cache:
            cache[k] = inst.net_weights(k)
        base = cache[k]
        drv = inst.nets[k][1]
        for s in ss:
            w = base
            al = alpha.get(s)
            if al:
                add = np.zeros(inst.nv)
                add[np.fromiter(al.keys(), np.int64)] = np.fromiter(al.values(), float)
                w = base + add[inst.adst]
            g = csr_matrix((w, inst.adst, inst.indptr), shape=(inst.nv, inst.nv))
            t = inst.nets[k][2][s[1]]
            dist, pred = dijkstra(g, directed=True, indices=drv, return_predecessors=True)
            p = [t]
            while p[-1] != drv:
                p.append(pred[p[-1]])
            out[s] = (dist[t], p[::-1])
    return out


def lagrangian_bound(inst, m, alpha, pi, sp):
    """Valid LB for any alpha>=0, pi>=0; pi is raised to restore feasibility."""
    need = defaultdict(float)
    per_net = defaultdict(float)
    for s, d in alpha.items():
        for v, a in d.items():
            per_net[(s[0], v)] += a
    for (k, v), a in per_net.items():
        need[v] = max(need[v], a)
    pis = sum(max(pi.get(v, 0.0), need[v]) for v in set(pi) | set(need))
    return sum(c for c, _ in sp.values()) - pis


def reduced_cost(inst, path, s, alpha, mu):
    al = alpha.get(s, {})
    return inst.path_cost(path) + sum(al.get(v, 0.0) for v in path) - mu[s]


def run(case, sols, max_rounds=1000, time_limit=3600, beta=0.8, log=print, center_npz=None):
    t0 = time.time()
    inst = Instance(case)
    m = Master(inst)
    for sp in sols:
        for s, p in solution_paths(inst, sp).items():
            m.add_path(s, p)
    cache = {}
    best_lb, center = -1e18, None
    if center_npz:
        A0 = np.load(center_npz)['alpha']
        a_c = defaultdict(dict)
        for i, s in enumerate(m.sinks):
            nz = np.nonzero(A0[i])[0]
            a_c[s] = {int(v): float(A0[i, v]) for v in nz}
        sp = shortest_paths(inst, m, a_c, cache)
        best_lb = lagrangian_bound(inst, m, a_c, {}, sp)
        center = (a_c, {})
        for s, (c, p) in sp.items():
            m.add_path(s, p)
        log(f'  center LB {best_lb:.2f}')
    hist = []
    obj = None
    for it in range(max_rounds):
        ta = time.time()
        obj, row_dual, _ = m.solve()
        tb = time.time()
        alpha, pi, mu = rmp_duals(m, row_dual)
        added = 0
        for b in (beta, 0.0):
            if center is None and b > 0:
                continue
            a_s = blend(center[0], alpha, b) if b > 0 else alpha
            p_s = blend(center[1], pi, b) if b > 0 else pi
            sp = shortest_paths(inst, m, a_s, cache)
            lb = lagrangian_bound(inst, m, a_s, p_s, sp)
            if lb > best_lb:
                best_lb, center = lb, (a_s, p_s)
            for s, (c, p) in sp.items():
                if reduced_cost(inst, p, s, alpha, mu) < -1e-7 and m.add_path(s, p):
                    added += 1
            if added:
                break
        tc = time.time()
        hist.append((it, tc - t0, obj, best_lb, added))
        log(f'  it {it:3d} t {tc-t0:7.1f}s (lp {tb-ta:5.1f} price {tc-tb:5.1f})  RMP {obj:10.2f}  '
            f'best LB {best_lb:10.2f}  gap {obj-best_lb:8.2f}  new {added:4d}  rows {m.h.getNumRow()} cols {m.h.getNumCol()}')
        if not added or obj - best_lb < 1e-4 * max(1.0, abs(obj)) or time.time() - t0 > time_limit:
            break
    return dict(case=case, rmp=obj, lb=best_lb, rounds=len(hist), seconds=time.time() - t0,
                converged=(not added), hist=hist)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('case'); ap.add_argument('sols', nargs='+')
    ap.add_argument('--center'); ap.add_argument('--seconds', type=float, default=3600)
    a = ap.parse_args()
    r = run(a.case, a.sols, center_npz=a.center, time_limit=a.seconds)
    print(json.dumps({k: v for k, v in r.items() if k != 'hist'}))
