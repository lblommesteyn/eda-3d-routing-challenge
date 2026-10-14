"""Exact MILP and its LP relaxation for small instances (calibration).

    y[n,v] in {0,1}   net n owns vertex v (own pins fixed 1, foreign pins 0)
    f[s,a] >= 0       flow of sink s on arc a, one unit driver -> sink
    sum_{a into v} f[s,a] <= y[n(s),v]        (link)
    sum_n y[n,v] <= 1                          (capacity)
    min sum_s sum_a c_a f[s,a]

With y integral, each net's optimum is a shortest-path tree inside its own
vertex set, which is a legal tree, so the MILP optimum is the true optimum.
Relaxing y gives exactly the relaxation bounded by lagr.py.
"""
import json
import sys
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from lpbound.cg import Instance
from lpbound.lagr import sink_list


def build(inst):
    sinks = sink_list(inst)
    S, K, nv = len(sinks), len(inst.nets), inst.nv
    na = len(inst.adst)
    own = inst.pin_owner
    nf = S * na
    ny = K * nv
    fcol = lambda i, a: i * na + a
    ycol = lambda k, v: nf + k * nv + v
    cost = np.r_[np.tile(inst.aw, S), np.zeros(ny)]
    lo = np.zeros(nf + ny)
    hi = np.ones(nf + ny)
    for i, (k, _) in enumerate(sinks):
        bad = ((own[inst.asrc] >= 0) & (own[inst.asrc] != k)) | ((own[inst.adst] >= 0) & (own[inst.adst] != k))
        hi[i * na:(i + 1) * na][bad] = 0.0
    for k in range(K):
        blk = slice(nf + k * nv, nf + (k + 1) * nv)
        hi[blk][(own >= 0) & (own != k)] = 0.0
        lo[blk][own == k] = 1.0
    rows, cols, vals, rlo, rhi = [], [], [], [], []
    r = 0
    a_idx = np.arange(na)
    for i, (k, j) in enumerate(sinks):
        drv, t = inst.nets[k][1], inst.nets[k][2][j]
        # conservation: out - in = b_v
        rows += list(r + inst.asrc); cols += list(i * na + a_idx); vals += [1.0] * na
        rows += list(r + inst.adst); cols += list(i * na + a_idx); vals += [-1.0] * na
        b = np.zeros(nv); b[drv] = 1.0; b[t] = -1.0
        rlo += list(b); rhi += list(b)
        r += nv
        # link: inflow(v) - y[k,v] <= 0
        rows += list(r + inst.adst); cols += list(i * na + a_idx); vals += [1.0] * na
        rows += list(r + np.arange(nv)); cols += [ycol(k, v) for v in range(nv)]; vals += [-1.0] * nv
        rlo += [-np.inf] * nv; rhi += [0.0] * nv
        r += nv
    for k in range(K):
        rows += list(r + np.arange(nv)); cols += [ycol(k, v) for v in range(nv)]; vals += [1.0] * nv
    rlo += [-np.inf] * nv; rhi += [1.0] * nv
    r += nv
    A = coo_matrix((vals, (rows, cols)), shape=(r, nf + ny)).tocsr()
    integ = np.r_[np.zeros(nf), np.ones(ny)]
    return cost, A, np.array(rlo), np.array(rhi), lo, hi, integ


def solve(case, seconds=600):
    inst = Instance(case)
    cost, A, rlo, rhi, lo, hi, integ = build(inst)
    cons = LinearConstraint(A, rlo, rhi)
    t = time.time()
    lp = milp(cost, constraints=cons, integrality=np.zeros_like(integ), bounds=Bounds(lo, hi))
    t_lp = time.time() - t
    print(f'LP {lp.fun} in {t_lp:.1f}s, vars {len(cost)}, rows {A.shape[0]}', flush=True)
    t = time.time()
    ip = milp(cost, constraints=cons, integrality=integ, bounds=Bounds(lo, hi),
              options={'time_limit': seconds, 'mip_rel_gap': 0.0, 'disp': True})
    return dict(case=case, vars=len(cost), rows=A.shape[0], lp=lp.fun, lp_s=t_lp,
                ip=ip.fun, ip_bound=getattr(ip, 'mip_dual_bound', None), ip_status=ip.message, ip_s=time.time() - t)


def _main():
    if len(sys.argv) > 3 and sys.argv[3] == 'highspy':
        print(json.dumps(solve_highspy(sys.argv[1], float(sys.argv[2]), threads=8,
                                       start_sol=sys.argv[4] if len(sys.argv) > 4 else None)))
    else:
        print(json.dumps(solve(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 600)))


def solve_highspy(case, seconds=1800, threads=4, start_sol=None, save=None, int_nets=None, quiet=False):
    """Same MILP through the current HiGHS release (scipy bundles an old one)."""
    import highspy
    inst = Instance(case)
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
    if int_nets is not None:          # partial integrality: only these nets' y binary (still a relaxation)
        nf = len(cost) - len(inst.nets) * inst.nv
        integ = integ.copy()
        for k in range(len(inst.nets)):
            if k not in int_nets:
                integ[nf + k * inst.nv: nf + (k + 1) * inst.nv] = 0
    lp.integrality_ = [highspy.HighsVarType.kInteger if x else highspy.HighsVarType.kContinuous for x in integ]
    h = highspy.Highs()
    h.passModel(lp)
    if quiet:
        h.setOptionValue('output_flag', False)
    h.setOptionValue('time_limit', float(seconds))
    h.setOptionValue('mip_rel_gap', 0.0)
    h.setOptionValue('threads', threads)
    if start_sol:
        from lpbound.cg import solution_paths
        sinks = sink_list(inst)
        na, nv = len(inst.adst), inst.nv
        arc = {(int(u), int(v)): a for a, (u, v) in enumerate(zip(inst.asrc, inst.adst))}
        x = np.zeros(len(cost))
        nf = len(sinks) * na
        for i, s_ in enumerate(sinks):
            p = solution_paths(inst, start_sol)[s_] if i == 0 else paths[s_]
            if i == 0:
                paths = solution_paths(inst, start_sol)
            for u, v in zip(p, p[1:]):
                x[i * na + arc[(u, v)]] = 1.0
                x[nf + s_[0] * nv + v] = 1.0
            x[nf + s_[0] * nv + p[0]] = 1.0
        x = np.maximum(x, lo)
        sol = highspy.HighsSolution()
        sol.col_value = list(x)
        sol.value_valid = True
        print('setSolution', h.setSolution(sol), 'start objective', float(cost @ x), flush=True)
    h.run()
    info = h.getInfo()
    if save:
        np.save(save, np.array(h.getSolution().col_value))
    return dict(case=case, status=h.modelStatusToString(h.getModelStatus()),
                ip=info.objective_function_value, bound=info.mip_dual_bound, gap=info.mip_gap,
                nodes=info.mip_node_count)


if __name__ == '__main__':
    _main()
