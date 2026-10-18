"""Window LNS: rip up every net touching a box and re-route them jointly, exactly.

For a box B (all layers) and the set R of nets whose trees touch B, net k in R
may use: free vertices of B, vertices of B held by any net in R (not foreign
pins), and its own current vertices outside B. Everything else is fixed. The
joint routing of R is the exact MILP of exact.py restricted to that domain
(per-sink flows, binary y[k,v] for v in B, capacity per vertex of B), warm
started from the incumbent, so the result is never worse. Each net is then
rebuilt as the shortest-path tree from its driver inside the vertices it was
given, whose sink delays are <= the MILP's, so legality and the improvement
both hold by construction (and the official checker re-confirms at the end).

    python -m lpbound.wlns <case.json> <start.sol.json> <out.sol.json> <seconds> [box] [mip_seconds] [seed]
"""
import heapq
import json
import random
import sys
import time

import highspy
import numpy as np
from scipy.sparse import coo_matrix

from lpbound.cg import Instance


class State:
    def __init__(self, inst, sol_path):
        self.inst = inst
        I = inst
        self.kof = {nid: k for k, (nid, _, _) in enumerate(I.nets)}
        self.owner = I.pin_owner.copy()
        self.verts = [set() for _ in I.nets]
        self.adj = [dict() for _ in I.nets]
        d = json.load(open(sol_path))
        for r in d['routes']:
            k = self.kof[r['net']]
            for a, b in r['edges']:
                u, v = I.vid(*a), I.vid(*b)
                self.adj[k].setdefault(u, []).append(v)
                self.adj[k].setdefault(v, []).append(u)
        for k, (_, drv, ss) in enumerate(I.nets):
            vs = set(self.adj[k]) | {drv} | set(ss)
            self.verts[k] = vs
            for v in vs:
                self.owner[v] = k
        self.cost = [self.tree_cost(k) for k in range(len(I.nets))]
        self.ver = [0] * len(I.nets)        # bumped when a net's tree changes
        self.proven = set()                 # (box, versions) windows proven optimal

    def tree_cost(self, k):
        I = self.inst
        _, drv, ss = I.nets[k]
        dist = {drv: 0}
        st = [drv]
        while st:
            u = st.pop()
            for v in self.adj[k].get(u, ()):
                if v not in dist:
                    dist[v] = dist[u] + I.edge_cost(u, v)
                    st.append(v)
        return sum(dist[t] for t in ss)

    def total(self):
        return sum(self.cost)

    def save(self, path):
        I = self.inst
        WH = I.W * I.H
        xyz = lambda v: [v % I.W, (v % WH) // I.W, v // WH]
        routes = []
        for k, (nid, _, _) in enumerate(I.nets):
            es = set()
            for u, nb in self.adj[k].items():
                for v in nb:
                    if u < v:
                        es.add((u, v))
            routes.append(dict(net=nid, edges=[[xyz(u), xyz(v)] for u, v in sorted(es)]))
        json.dump(dict(format='m3d-submission', version=1, instance=I.name, routes=routes), open(path, 'w'))


def sp_tree(inst, k, allowed, drv, ss):
    """Dijkstra from drv inside allowed; returns adjacency of the union of sink paths and the cost."""
    I = inst
    dist = {drv: 0}
    pred = {drv: -1}
    pq = [(0, drv)]
    left = set(ss)
    while pq and left:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        left.discard(u)
        for a in range(I.indptr[u], I.indptr[u + 1]):
            v = int(I.adst[a])
            if v not in allowed:
                continue
            nd = d + I.aw[a]
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                pred[v] = u
                heapq.heappush(pq, (nd, v))
    if left:
        return None, None
    keep = {drv}
    for t in ss:                           # walk up the SP tree until the kept part
        v = t
        while v not in keep:
            keep.add(v)
            v = pred[v]
    adj = {v: [] for v in keep}
    for v in keep:
        if v != drv:
            adj[v].append(pred[v])
            adj[pred[v]].append(v)
    return adj, int(sum(dist[t] for t in ss))


def window(st, box, mip_seconds=30.0, threads=4, max_nets=40, max_sinks=120, log=None, margin=None, max_nodes=20000, verbose=False):
    """Re-route all nets touching box exactly. Returns the gain (>= 0)."""
    I = st.inst
    x0, x1, y0, y1 = box
    WH = I.W * I.H
    B = np.array([(z * I.H + y) * I.W + x for z in range(I.L) for y in range(y0, y1) for x in range(x0, x1)])
    inB = np.zeros(I.nv, bool)
    inB[B] = True
    R = sorted({int(st.owner[v]) for v in B if st.owner[v] >= 0})
    if not R or len(R) > max_nets:
        return 0, 'skip'
    Rset = set(R)
    key = (tuple(box), tuple((k, st.ver[k]) for k in R))
    if key in st.proven:
        return 0, 'cached'
    nsink = sum(len(I.nets[k][2]) for k in R)
    if nsink > max_sinks:
        return 0, 'skip'
    old = sum(st.cost[k] for k in R)
    # domain per net
    freeB = inB & ((st.owner < 0) | np.isin(st.owner, R)) & (I.pin_owner < 0)
    ncol, nrow = 0, 0
    costs, rows, cols, vals, rlo, rhi = [], [], [], [], [], []
    ycol = {}                # (k, v) -> column, kept for warm start / extraction
    yarr = {}
    flow_index = []          # (k, sink idx, arc ids array, first col)
    doms = {}
    for k in R:
        dom = freeB.copy()
        if margin is not None:              # keep net k near its pins and current tree
            vs = np.fromiter(st.verts[k], np.int64)
            xs, ys = vs % I.W, (vs % WH) // I.W
            bx = np.zeros((I.H, I.W), bool)
            bx[max(0, ys.min() - margin):ys.max() + margin + 1, max(0, xs.min() - margin):xs.max() + margin + 1] = True
            dom &= np.tile(bx.ravel(), I.L)
        dom[np.fromiter(st.verts[k], np.int64)] = True
        _, drv, ss = I.nets[k]
        dom[drv] = True
        dom[ss] = True
        doms[k] = dom
        yv = np.nonzero(dom & inB & (I.pin_owner < 0))[0]
        ya = np.full(I.nv, -1, np.int64)
        ya[yv] = ncol + np.arange(len(yv))
        yarr[k] = (yv, ya)
        for v, c in zip(yv.tolist(), ya[yv].tolist()):
            ycol[(k, v)] = c
        costs.append(np.zeros(len(yv)))
        ncol += len(yv)
    for k in R:
        dom = doms[k]
        _, drv, ss = I.nets[k]
        yv, ya = yarr[k]
        arcs = np.nonzero(dom[I.asrc] & dom[I.adst])[0]
        dv = np.nonzero(dom)[0]
        vrow = np.full(I.nv, -1, np.int64)
        vrow[dv] = np.arange(len(dv))
        yidx = np.full(I.nv, -1, np.int64)
        yidx[yv] = np.arange(len(yv))
        na, src, dst = len(arcs), I.asrc[arcs], I.adst[arcs]
        lsel = yidx[dst] >= 0
        for j, t in enumerate(ss):
            c0 = ncol
            ncol += na
            costs.append(I.aw[arcs])
            fc = c0 + np.arange(na)
            rows += [nrow + vrow[src], nrow + vrow[dst]]; cols += [fc, fc]; vals += [np.ones(na), -np.ones(na)]
            b = np.zeros(len(dv)); b[vrow[drv]] = 1; b[vrow[t]] = -1
            rlo.append(b); rhi.append(b)
            nrow += len(dv)
            rows += [nrow + yidx[dst[lsel]], nrow + np.arange(len(yv))]
            cols += [fc[lsel], ya[yv]]
            vals += [np.ones(int(lsel.sum())), -np.ones(len(yv))]
            rlo.append(np.full(len(yv), -np.inf)); rhi.append(np.zeros(len(yv)))
            nrow += len(yv)
            flow_index.append((k, j, arcs, c0))
    # capacity rows
    vv = np.concatenate([yarr[k][0] for k in R])
    cc = np.concatenate([yarr[k][1][yarr[k][0]] for k in R])
    o = np.argsort(vv, kind='stable')
    vv, cc = vv[o], cc[o]
    u, start_, cnt = np.unique(vv, return_index=True, return_counts=True)
    multi = cnt > 1
    rid = np.full(len(u), -1, np.int64)
    rid[multi] = nrow + np.arange(int(multi.sum()))
    rr = np.repeat(rid, cnt)
    sel = rr >= 0
    rows.append(rr[sel]); cols.append(cc[sel]); vals.append(np.ones(int(sel.sum())))
    rlo.append(np.full(int(multi.sum()), -np.inf)); rhi.append(np.ones(int(multi.sum())))
    nrow += int(multi.sum())
    rows, cols, vals = np.concatenate(rows), np.concatenate(cols), np.concatenate(vals)
    cols_cost = np.concatenate(costs)
    rlo, rhi = np.concatenate(rlo), np.concatenate(rhi)
    A = coo_matrix((vals, (rows, cols)), shape=(nrow, ncol)).tocsc()
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = ncol, nrow
    lp.col_cost_ = cols_cost
    lp.col_lower_ = np.zeros(ncol)
    lp.col_upper_ = np.ones(ncol)
    inf = highspy.kHighsInf
    lp.row_lower_ = np.where(np.isinf(rlo), -inf, rlo)
    lp.row_upper_ = np.where(np.isinf(rhi), inf, rhi)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = A.indptr, A.indices, A.data
    integ = np.zeros(ncol, bool)
    integ[list(ycol.values())] = True
    lp.integrality_ = [highspy.HighsVarType.kInteger if x else highspy.HighsVarType.kContinuous for x in integ]
    h = highspy.Highs()
    h.setOptionValue('output_flag', bool(verbose))
    h.setOptionValue('time_limit', float(mip_seconds))
    h.setOptionValue('mip_rel_gap', 0.0)
    h.setOptionValue('threads', threads)
    h.setOptionValue('mip_max_nodes', max_nodes)
    h.passModel(lp)
    # warm start from incumbent tree paths
    x = np.zeros(ncol)
    for (k, v), c in ycol.items():
        if st.owner[v] == k:
            x[c] = 1.0
    for k, j, arcs, c0 in flow_index:
        _, drv, ss = I.nets[k]
        path = tree_path(st, k, drv, ss[j])
        apos = {(int(I.asrc[a]), int(I.adst[a])): i for i, a in enumerate(arcs)}
        for u, v in zip(path, path[1:]):
            x[c0 + apos[(u, v)]] = 1.0
    start = float(cols_cost @ x)
    sol = highspy.HighsSolution()
    sol.col_value = list(x)
    sol.value_valid = True
    h.setSolution(sol)
    try:
        h.run()
    except MemoryError:
        return 0, 'oom'
    info = h.getInfo()
    if info.primal_solution_status < 2:
        return 0, 'nosol'
    obj = info.objective_function_value
    if obj > start - 0.5:
        if h.getModelStatus() == highspy.HighsModelStatus.kOptimal:
            st.proven.add(key)
            return 0, 'opt'
        return 0, 'tl'
    xv = np.array(h.getSolution().col_value)
    # rebuild trees
    new_adj, new_cost = {}, {}
    for k in R:
        _, drv, ss = I.nets[k]
        allowed = {int(v) for v in np.nonzero(doms[k] & ~inB)[0]} | {drv, *ss}
        allowed |= {v for (kk, v), c in ycol.items() if kk == k and xv[c] > 0.5}
        adj, c = sp_tree(I, k, allowed, drv, ss)
        if adj is None:
            return 0, 'rebuild-fail'
        new_adj[k], new_cost[k] = adj, c
    new = sum(new_cost.values())
    if new >= old:
        return 0, 'nogain'
    # exclusivity check across R
    seen = {}
    for k in R:
        for v in new_adj[k]:
            if v in seen and seen[v] != k:
                return 0, 'conflict'
            seen[v] = k
    for k in R:
        for v in st.verts[k]:
            if st.owner[v] == k and I.pin_owner[v] < 0:
                st.owner[v] = -1
    for k in R:
        st.adj[k] = new_adj[k]
        st.verts[k] = set(new_adj[k])
        for v in st.verts[k]:
            if st.owner[v] >= 0 and st.owner[v] != k and st.owner[v] not in Rset:
                raise RuntimeError('took a fixed net vertex')
            st.owner[v] = k
        st.cost[k] = new_cost[k]
        st.ver[k] += 1
    return old - new, 'gain'


def tree_path(st, k, drv, t):
    I = st.inst
    pred = {drv: -1}
    q = [drv]
    while q:
        u = q.pop()
        if u == t:
            break
        for v in st.adj[k].get(u, ()):
            if v not in pred:
                pred[v] = u
                q.append(v)
    p = [t]
    while pred[p[-1]] != -1:
        p.append(pred[p[-1]])
    return p[::-1]


def slacks(st, alpha_path):
    """Per-sink dual path slack: incumbent priced cost minus priced shortest path (>= 0)."""
    from lpbound.lagr_win import price
    from lpbound.lagr import sink_list
    I = st.inst
    z = np.load(alpha_path)
    off, vert, val = z['off'].astype(np.int64), z['vert'].astype(np.int64), z['val'].astype(float)
    sinks = sink_list(I)
    netk = np.array([k for k, _ in sinks], np.int64)
    drv = np.array([I.nets[k][1] for k, _ in sinks], np.int64)
    tgt = np.array([I.nets[k][2][j] for k, j in sinks], np.int64)
    sp, pbuf, plen = price(I.W, I.H, I.L, np.array(I.ld, float), float(I.vd), I.pin_owner.astype(np.int64),
                     drv, tgt, netk, off, vert, val, 4, 8192)
    out = []
    for i, (k, j) in enumerate(sinks):
        p = tree_path(st, k, drv[i], tgt[i])
        al = dict(zip(vert[off[i]:off[i + 1]].tolist(), val[off[i]:off[i + 1]].tolist()))
        pc = I.path_cost(p) + sum(al.get(v, 0.0) for v in p[1:])
        out.append((max(0.0, pc - sp[i]), p + pbuf[i, :plen[i]].tolist()))   # incumbent + priced SP path
    return out


def run(case, start, out, seconds, box=8, mip_seconds=30, seed=0, threads=1, alpha=None, target=False, nmax=40, log=print):
    t0 = time.time()
    I = Instance(case)
    st = State(I, start)
    best = st.total()
    log(f'start {best}', flush=True)
    rng = random.Random(seed)
    sl = slacks(st, alpha) if alpha else None
    if sl:
        log(f'dual path slack total {sum(x for x, _ in sl):.1f} over {len(sl)} sinks', flush=True)
    it, gains, stats = 0, 0, {}
    while time.time() - t0 < seconds:
        it += 1
        bw = rng.randint(3, box + 2)
        bh = rng.randint(3, box + 2)
        margin = None
        if sl and target:
            w = [x * x + 1e-6 for x, _ in sl]
            _, p = rng.choices(sl, weights=w)[0]
            xs = [v % I.W for v in p]; ys = [(v % (I.W * I.H)) // I.W for v in p]
            pad = rng.randint(0, 2)
            x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
            bw, bh = min(I.W, max(xs) + pad + 1) - x0, min(I.H, max(ys) + pad + 1) - y0
            margin = 2
        elif sl and rng.random() < 0.8:
            w = [x + 1e-3 for x, _ in sl]
            _, p = rng.choices(sl, weights=w)[0]
            v = rng.choice(p)
            cx, cy = v % I.W, (v % (I.W * I.H)) // I.W
            x0 = min(max(0, cx - bw // 2), max(0, I.W - bw)); y0 = min(max(0, cy - bh // 2), max(0, I.H - bh))
        else:
            x0 = rng.randint(0, max(0, I.W - bw)); y0 = rng.randint(0, max(0, I.H - bh))
        bx = (x0, min(I.W, x0 + bw), y0, min(I.H, y0 + bh))
        g, why = window(st, bx, mip_seconds, threads, max_nets=nmax, max_sinks=3 * nmax, margin=margin)
        if why == 'tl' and rng.random() < 0.3:
            g, why = window(st, bx, 4 * mip_seconds, threads, max_nets=nmax, max_sinks=3 * nmax, margin=margin)
            why = why + '-retry'
        stats[why] = stats.get(why, 0) + 1
        if g > 0:
            gains += 1
            st.save(out)
            log(f'  it {it} t {time.time()-t0:.0f}s box {bw}x{bh}@({x0},{y0}) gain {g} total {st.total()}', flush=True)
            if sl:
                sl = slacks(st, alpha)
        elif it % 25 == 0:
            log(f'  it {it} t {time.time()-t0:.0f}s total {st.total()} {stats}', flush=True)
    st.save(out)
    return st.total()


if __name__ == '__main__':
    a = sys.argv
    run(a[1], a[2], a[3], float(a[4]), int(a[5]) if len(a) > 5 else 8, float(a[6]) if len(a) > 6 else 30,
        int(a[7]) if len(a) > 7 else 0, alpha=a[8] if len(a) > 8 and a[8] != '-' else None,
        target=len(a) > 9 and a[9] == 'target', nmax=int(a[10]) if len(a) > 10 else 40)
