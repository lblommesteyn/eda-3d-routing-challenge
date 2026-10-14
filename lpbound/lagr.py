"""Lagrangian lower bound by projected subgradient ascent.

Same relaxation as cg.py (sink paths + per-net vertex occupancy y + capacity),
dualized. Eliminating pi, the dual function is

    L(alpha) = sum_s SP_s(alpha_s) - sum_v max(0, max_n sum_{s in n} alpha[s,v])

where SP_s is the min-cost driver->sink path with vertex costs alpha_s (foreign
pins blocked). L is concave and L(alpha) <= OPT for every alpha >= 0, so every
iterate is a valid lower bound. Supergradient:
    g[s,v] = [v in path_s] - [n(s) == n*(v)],  n*(v) an argmax net at v.
"""
import json
import sys
import time

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from lpbound.cg import Instance


def sink_list(inst):
    return [(k, j) for k, (_, _, ss) in enumerate(inst.nets) for j in range(len(ss))]


def price_all(inst, sinks, alpha, base_w):
    """Per sink: min cost (including alpha) and its vertex path."""
    costs = np.empty(len(sinks))
    paths = []
    for i, (k, j) in enumerate(sinks):
        drv = inst.nets[k][1]
        t = inst.nets[k][2][j]
        w = base_w[k] + alpha[i][inst.adst]
        g = csr_matrix((w, inst.adst, inst.indptr), shape=(inst.nv, inst.nv))
        dist, pred = dijkstra(g, directed=True, indices=drv, return_predecessors=True)
        p = [t]
        while p[-1] != drv:
            p.append(pred[p[-1]])
        costs[i] = dist[t]
        paths.append(np.array(p[::-1]))
    return costs, paths


def run(case, ub, iters=3000, time_limit=1800, theta=1.0, log=print, alpha0=None, stall_window=50):
    t0 = time.time()
    inst = Instance(case)
    sinks = sink_list(inst)
    S, nv, K = len(sinks), inst.nv, len(inst.nets)
    net_of = np.array([k for k, _ in sinks])
    base_w = {k: inst.net_weights(k) for k in range(K)}
    contested = inst.pin_owner < 0          # pins are never priced
    alpha = np.zeros((S, nv)) if alpha0 is None else alpha0.copy()
    best, best_alpha, stall = -np.inf, alpha.copy(), 0
    hist = []
    for it in range(iters):
        costs, paths = price_all(inst, sinks, alpha, base_w)
        A = np.zeros((K, nv))
        np.add.at(A, net_of, alpha)
        maxA = np.maximum(A.max(axis=0), 0.0)
        L = costs.sum() - maxA.sum()
        if L > best + 1e-9:
            best, best_alpha, stall = L, alpha.copy(), 0
        else:
            stall += 1
            if stall >= stall_window:
                theta *= 0.5; stall = 0
                if theta < 2e-3:          # restart from the best point instead of dying
                    theta, alpha = 0.2, best_alpha.copy()
                    continue
        # supergradient
        use = np.zeros((S, nv))
        for i, p in enumerate(paths):
            use[i, p] = 1.0
        use[:, ~contested] = 0.0
        cnt = np.zeros((K, nv))
        np.add.at(cnt, net_of, use)
        # argmax net per vertex: highest alpha mass, ties broken by usage count
        score = A + 1e-9 * cnt
        nstar = score.argmax(axis=0)
        anyuse = cnt.sum(axis=0) > 0
        g = use.copy()
        rows = (net_of[:, None] == nstar[None, :]) & (anyuse | (maxA > 0))[None, :]
        g -= rows
        g[:, ~contested] = 0.0
        # project direction: alpha already 0 and g < 0 -> no movement
        g[(alpha <= 0) & (g < 0)] = 0.0
        gn = (g * g).sum()
        if gn == 0:
            log(f'  it {it} zero subgradient: L is optimal at {L:.3f}')
            break
        step = theta * (ub - L) / gn
        alpha = np.maximum(0.0, alpha + step * g)
        if it % 10 == 0:
            el = time.time() - t0
            hist.append((it, el, L, best))
            log(f'  it {it:5d} t {el:7.1f}s  L {L:10.2f}  best {best:10.2f}  UB {ub}  gap {(ub-best)/ub:7.2%}  theta {theta:.4f}')
        if time.time() - t0 > time_limit:
            break
    return dict(case=case, lb=best, ub=ub, iters=it + 1, seconds=time.time() - t0, hist=hist), best_alpha


if __name__ == '__main__' and sys.argv[1] != 'volume':
    import os
    case, ub = sys.argv[1], float(sys.argv[2])
    tl = float(sys.argv[3]) if len(sys.argv) > 3 else 1800
    out = sys.argv[4] if len(sys.argv) > 4 else None
    a0 = np.load(sys.argv[5])['alpha'] if len(sys.argv) > 5 else None
    r, a = run(case, ub, time_limit=tl, iters=10**9, alpha0=a0, theta=1.0 if a0 is None else 0.2)
    print(json.dumps({k: v for k, v in r.items() if k != 'hist'}))
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(r, open(out + '.json', 'w'), indent=1)
        np.savez_compressed(out + '.alpha.npz', alpha=a.astype(np.float64))


def subgradient(inst, sinks, net_of, alpha, base_w, contested, K):
    """L(alpha) and the supergradient (path use minus argmax-net assignment)."""
    costs, paths = price_all(inst, sinks, alpha, base_w)
    A = np.zeros((K, inst.nv))
    np.add.at(A, net_of, alpha)
    maxA = np.maximum(A.max(axis=0), 0.0)
    L = costs.sum() - maxA.sum()
    use = np.zeros_like(alpha)
    for i, p in enumerate(paths):
        use[i, p] = 1.0
    use[:, ~contested] = 0.0
    cnt = np.zeros((K, inst.nv))
    np.add.at(cnt, net_of, use)
    nstar = (A + 1e-9 * cnt).argmax(axis=0)
    active = (cnt.sum(axis=0) > 0) | (maxA > 0)
    g = use - ((net_of[:, None] == nstar[None, :]) & active[None, :])
    g[:, ~contested] = 0.0
    return L, g


def volume(case, ub, time_limit=1800, alpha0=None, a=0.1, f=0.1, log=print):
    """Volume algorithm: move along an averaged subgradient from a stability center."""
    t0 = time.time()
    inst = Instance(case)
    sinks = sink_list(inst)
    K = len(inst.nets)
    net_of = np.array([k for k, _ in sinks])
    base_w = {k: inst.net_weights(k) for k in range(K)}
    contested = inst.pin_owner < 0
    center = np.zeros((len(sinks), inst.nv)) if alpha0 is None else alpha0.copy()
    Lc, gbar = subgradient(inst, sinks, net_of, center, base_w, contested, K)
    red, it = 0, 0
    while time.time() - t0 < time_limit:
        it += 1
        d = gbar.copy()
        d[(center <= 0) & (d < 0)] = 0.0
        gn = (d * d).sum()
        if gn == 0:
            break
        trial = np.maximum(0.0, center + f * (ub - Lc) / gn * d)
        L, g = subgradient(inst, sinks, net_of, trial, base_w, contested, K)
        gbar = a * g + (1 - a) * gbar
        if L > Lc + 1e-9:
            center, Lc = trial, L
            # green step: enlarge if the new subgradient still points along d
            if (g * d).sum() > 0:
                f = min(2.0, f * 1.1)
            red = 0
        else:
            red += 1
            if red >= 20:
                f, red = max(f * 0.66, 1e-4), 0
        if it % 10 == 0:
            log(f'  it {it:5d} t {time.time()-t0:7.1f}s  L {L:10.2f}  center {Lc:10.2f}  gap {(ub-Lc)/ub:7.2%}  f {f:.4f}')
    return dict(case=case, lb=Lc, ub=ub, iters=it, seconds=time.time() - t0), center


def _volume_main():
    import os
    case, ub, tl, out = sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
    a0 = np.load(sys.argv[6])['alpha'] if len(sys.argv) > 6 else None
    r, c = volume(case, ub, time_limit=tl, alpha0=a0)
    print(json.dumps(r))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(r, open(out + '.json', 'w'), indent=1)
    np.savez_compressed(out + '.alpha.npz', alpha=c)


if __name__ == '__main__' and sys.argv[1] == 'volume':
    _volume_main()
