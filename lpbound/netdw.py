"""Net-level Dantzig-Wolfe bound (small instances): how much do net cuts buy?

The sink LP (lagr.py, exact.py relaxation) lets each net occupy vertices
fractionally. Replacing each net's polytope by the convex hull of its integral
routings gives

    min  sum_{n,c} cost_c lam[n,c]
    s.t. sum_c lam[n,c] = 1                       (mu_n)
         sum_{n,c: v in c} lam[n,c] <= 1          (-pi_v)

Pricing for net n is the integral single-net problem
    min  sum_s path delay_s + sum_{v used} pi_v
solved here as a small MILP (per-sink flows, binary y). The value lies between
the sink LP and the true optimum. Every net-level valid inequality is implied
by it, so if it does not close the gap, net cuts cannot either.

    python -m lpbound.netdw <case.json> [seconds] [lagr]
"""
import json
import sys
import time

import highspy
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from lpbound.cg import Instance, solution_paths


def net_model(inst, k):
    """Single-net MILP pieces: flows per sink + y over this net's allowed vertices."""
    _, drv, ss = inst.nets[k]
    nv, na = inst.nv, len(inst.adst)
    own = inst.pin_owner
    S = len(ss)
    nf = S * na
    bad = ((own[inst.asrc] >= 0) & (own[inst.asrc] != k)) | ((own[inst.adst] >= 0) & (own[inst.adst] != k))
    lo = np.zeros(nf + nv); hi = np.ones(nf + nv)
    for i in range(S):
        hi[i * na:(i + 1) * na][bad] = 0
    hi[nf:][(own >= 0) & (own != k)] = 0
    lo[nf:][own == k] = 1
    rows, cols, vals, rlo, rhi = [], [], [], [], []
    r = 0
    ai = np.arange(na)
    for i, t in enumerate(ss):
        rows += list(r + inst.asrc); cols += list(i * na + ai); vals += [1.0] * na
        rows += list(r + inst.adst); cols += list(i * na + ai); vals += [-1.0] * na
        b = np.zeros(nv); b[drv] = 1; b[t] = -1
        rlo += list(b); rhi += list(b); r += nv
        rows += list(r + inst.adst); cols += list(i * na + ai); vals += [1.0] * na
        rows += list(r + np.arange(nv)); cols += list(nf + np.arange(nv)); vals += [-1.0] * nv
        rlo += [-np.inf] * nv; rhi += [0.0] * nv; r += nv
    A = coo_matrix((vals, (rows, cols)), shape=(r, nf + nv)).tocsr()
    base = np.r_[np.tile(inst.aw, S), np.zeros(nv)]
    integ = np.r_[np.zeros(nf), np.ones(nv)]
    return dict(A=A, rlo=np.array(rlo), rhi=np.array(rhi), lo=lo, hi=hi, base=base, integ=integ, nf=nf)


def price_net(m, pi):
    c = m['base'].copy()
    c[m['nf']:] += pi
    r = milp(c, constraints=LinearConstraint(m['A'], m['rlo'], m['rhi']), integrality=m['integ'],
             bounds=Bounds(m['lo'], m['hi']), options={'mip_rel_gap': 0.0})
    x = r.x
    y = x[m['nf']:] > 0.5
    # delay part only (the y-price part is pi @ y)
    delay = float(m['base'][:m['nf']] @ x[:m['nf']])
    return r.fun, delay, frozenset(np.nonzero(y)[0].tolist())


def run(case, seconds=1800, log=print):
    t0 = time.time()
    inst = Instance(case)
    K, nv = len(inst.nets), inst.nv
    models = [net_model(inst, k) for k in range(K)]
    cols = [[] for _ in range(K)]        # (delay, vertex set)
    # seed with the reference solution's per-net trees
    ref = case.replace('.json', '.ref.sol.json')
    paths = solution_paths(inst, ref)
    for k in range(K):
        _, drv, ss = inst.nets[k]
        vs, d = set(), 0.0
        for j in range(len(ss)):
            p = paths[(k, j)]
            vs |= set(p); d += inst.path_cost(p)
        cols[k].append((d, frozenset(vs)))
    best_lb = -np.inf
    it = 0
    while time.time() - t0 < seconds:
        it += 1
        # restricted master LP
        h = highspy.Highs()
        h.setOptionValue('output_flag', False)
        ncol = sum(len(c) for c in cols)
        cost, rows, colsi, vals = [], [], [], []
        j = 0
        for k in range(K):
            for d, vs in cols[k]:
                cost.append(d)
                rows.append(k); colsi.append(j); vals.append(1.0)
                for v in vs:
                    if inst.pin_owner[v] < 0:
                        rows.append(K + v); colsi.append(j); vals.append(1.0)
                j += 1
        A = coo_matrix((vals, (rows, colsi)), shape=(K + nv, ncol)).tocsc()
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = ncol, K + nv
        lp.col_cost_ = np.array(cost); lp.col_lower_ = np.zeros(ncol); lp.col_upper_ = np.full(ncol, highspy.kHighsInf)
        lp.row_lower_ = np.r_[np.ones(K), np.full(nv, -highspy.kHighsInf)]
        lp.row_upper_ = np.r_[np.ones(K), np.ones(nv)]
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = A.indptr, A.indices, A.data
        h.passModel(lp)
        h.run()
        rmp = h.getInfo().objective_function_value
        dual = np.array(h.getSolution().row_dual)
        mu, pi = dual[:K], np.maximum(0.0, -dual[K:])
        # Lagrangian bound: sum_n min_c (cost + pi y) - sum_v pi_v
        lb = -pi.sum()
        new = 0
        for k in range(K):
            val, d, vs = price_net(models[k], pi)
            lb += val
            if val < mu[k] - 1e-6 and (d, vs) not in cols[k]:
                cols[k].append((d, vs)); new += 1
        best_lb = max(best_lb, lb)
        log(f'  it {it:3d} t {time.time()-t0:6.1f}s  RMP {rmp:9.3f}  LB {lb:9.3f}  best {best_lb:9.3f}  new cols {new}  total {ncol}', flush=True)
        if new == 0:
            break
    return dict(case=case, dw_lb=best_lb, rmp=rmp, converged=new == 0, iters=it, seconds=time.time() - t0)




def lp_capacity_duals(inst):
    import highspy
    from lpbound.exact import build
    cost, A, rlo, rhi, lo, hi, integ = build(inst)
    Ac = A.tocsc()
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = len(cost), A.shape[0]
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = cost, lo, hi
    inf = highspy.kHighsInf
    lp.row_lower_ = np.where(np.isinf(rlo), -inf, rlo)
    lp.row_upper_ = np.where(np.isinf(rhi), inf, rhi)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = Ac.indptr, Ac.indices, Ac.data
    h = highspy.Highs()
    h.setOptionValue('output_flag', False)
    h.setOptionValue('threads', 1)
    h.passModel(lp)
    h.run()
    d = np.array(h.getSolution().row_dual)[-inst.nv:]     # capacity rows are last
    print('LP', h.getInfo().objective_function_value, flush=True)
    return np.maximum(0.0, -d)


def lagr(case, seconds=900, ub=None, log=print):
    """Same net-hull bound by subgradient on vertex prices pi (stable, valid every step).

    L(pi) = sum_n min_{routing of n alone} [delay + pi(vertices used)] - sum_v pi_v
    """
    t0 = time.time()
    inst = Instance(case)
    K, nv = len(inst.nets), inst.nv
    models = [net_model(inst, k) for k in range(K)]
    cont = inst.pin_owner < 0
    if ub is None:
        from m3d.checker import check
        from m3d.model import Instance as MI, Submission
        ub = check(MI.load(case), Submission.load(case.replace('.json', '.ref.sol.json'))).total_delay
    pi = lp_capacity_duals(inst)          # L(pi_LP) >= LP value, so start there
    best, theta, stall, it = -np.inf, 0.1, 0, 0
    while time.time() - t0 < seconds:
        it += 1
        L = -pi.sum()
        use = np.zeros(nv)
        for k in range(K):
            val, d, vs = price_net(models[k], pi)
            L += val
            use[list(vs)] += 1
        if L > best + 1e-9:
            best, stall = L, 0
        else:
            stall += 1
            if stall >= 20:
                theta, stall = theta / 2, 0
        g = (use - 1.0) * cont
        g[(pi <= 0) & (g < 0)] = 0
        gn = g @ g
        if it % 10 == 0:
            log(f'  it {it} t {time.time()-t0:.0f}s L {L:.3f} best {best:.3f} theta {theta:.4f}', flush=True)
        if gn == 0 or theta < 1e-4:
            break
        pi = np.maximum(0, pi + theta * (ub - L) / gn * g)
    return dict(case=case, net_hull_lb=best, iters=it, seconds=time.time() - t0)


if __name__ == '__main__':
    fn = lagr if len(sys.argv) > 3 and sys.argv[3] == 'lagr' else run
    print(json.dumps(fn(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 1800)))
