"""Fast, memory-light Lagrangian bound for the big tiers (congested, designs).

Same dual function as lagr.py,

    L(alpha) = sum_s SP_s(alpha_s) - sum_v max(0, max_n sum_{s in n} alpha[s,v]),

stored sparsely. Subgradient ascent from alpha = 0 can only raise alpha[s, v]
where sink s's priced path has passed through v (elsewhere the supergradient
is <= 0 and the projection keeps alpha at 0), so the support is grown on demand
from the paths. This is the same iteration as the dense lagr.py, without the
(n_sinks x n_vertices) arrays. Entries back at 0 that no path uses are pruned.

SP_s is an exact A* on the whole grid (numba). The heuristic
min_layer_delay * xy-distance + via_delay * z-distance never overestimates and
is consistent, and alpha >= 0, so A* returns the true shortest path.

Output npz is sparse: for sink i, vertices vert[off[i]:off[i+1]] carry alpha
val[off[i]:off[i+1]]. verify.py reads both this and the dense format.

    python -m lpbound.lagr_win <case.json> <sol.json> <seconds> <out_prefix> [warm.npz|-] [threads]
"""
import json
import os
import sys
import time

import numba as nb
import numpy as np

from lpbound.cg import Instance, solution_paths
from lpbound.lagr import sink_list


@nb.njit(cache=True)
def _astar(W, H, L, ld, vd, lmin, owner, k, src, t, buf, dist, stamp, pred, cur, hk, hv):
    WH = W * H
    zt, rt = t // WH, t % WH
    yt, xt = rt // W, rt % W
    n = 0
    stamp[src] = cur
    dist[src] = 0.0
    pred[src] = -1
    zs, rs = src // WH, src % WH
    hk[0] = lmin * (abs(rs % W - xt) + abs(rs // W - yt)) + vd * abs(zs - zt)
    hv[0] = src
    n = 1
    while n > 0:
        f, u = hk[0], hv[0]
        n -= 1
        if n > 0:                       # sift down the last element
            kk, vv = hk[n], hv[n]
            i = 0
            while True:
                c = 2 * i + 1
                if c >= n:
                    break
                if c + 1 < n and hk[c + 1] < hk[c]:
                    c += 1
                if hk[c] >= kk:
                    break
                hk[i] = hk[c]; hv[i] = hv[c]
                i = c
            hk[i] = kk; hv[i] = vv
        du = dist[u]
        zu, ru = u // WH, u % WH
        yu, xu = ru // W, ru % W
        if f > du + lmin * (abs(xu - xt) + abs(yu - yt)) + vd * abs(zu - zt) + 1e-9:
            continue                    # stale entry
        if u == t:
            return du
        for d in range(6):
            if d == 0:
                if xu == 0: continue
                v = u - 1; c = ld[zu]
            elif d == 1:
                if xu == W - 1: continue
                v = u + 1; c = ld[zu]
            elif d == 2:
                if yu == 0: continue
                v = u - W; c = ld[zu]
            elif d == 3:
                if yu == H - 1: continue
                v = u + W; c = ld[zu]
            elif d == 4:
                if zu == 0: continue
                v = u - WH; c = vd
            else:
                if zu == L - 1: continue
                v = u + WH; c = vd
            o = owner[v]
            if o >= 0 and o != k:
                continue
            nd = du + c + buf[v]
            if stamp[v] != cur or nd < dist[v]:
                stamp[v] = cur
                dist[v] = nd
                pred[v] = u
                zv, rv = v // WH, v % WH
                key = nd + lmin * (abs(rv % W - xt) + abs(rv // W - yt)) + vd * abs(zv - zt)
                i = n                   # sift up
                n += 1
                while i > 0:
                    p = (i - 1) // 2
                    if hk[p] <= key:
                        break
                    hk[i] = hk[p]; hv[i] = hv[p]
                    i = p
                hk[i] = key; hv[i] = v
    return np.inf


@nb.njit(parallel=True, cache=True)
def price(W, H, L, ld, vd, owner, drv, tgt, netk, soff, svert, a, nchunk, cap):
    """Shortest priced path per sink. Path i is pbuf[i, :plen[i]] (target first)."""
    S = len(drv)
    nv = W * H * L
    lmin = ld.min()
    sp = np.empty(S)
    pbuf = np.empty((S, cap), np.int64)
    plen = np.zeros(S, np.int64)
    for ch in nb.prange(nchunk):
        buf = np.zeros(nv)
        dist = np.empty(nv)
        stamp = np.zeros(nv, np.int64)
        pred = np.empty(nv, np.int64)
        hk = np.empty(8 * nv)
        hv = np.empty(8 * nv, np.int64)
        for i in range(ch, S, nchunk):
            for e in range(soff[i], soff[i + 1]):
                buf[svert[e]] = a[e]
            sp[i] = _astar(W, H, L, ld, vd, lmin, owner, netk[i], drv[i], tgt[i], buf, dist, stamp, pred, i + 1, hk, hv)
            v = tgt[i]
            n = 0
            while v != -1:
                if n < cap:
                    pbuf[i, n] = v
                n += 1
                v = pred[v]
            plen[i] = n
            for e in range(soff[i], soff[i + 1]):
                buf[svert[e]] = 0.0
    return sp, pbuf, plen


@nb.njit(cache=True)
def argmax_net(uv, uk, A, cnt, nv):
    maxA = np.zeros(nv)
    best = np.zeros(nv)
    nstar = np.full(nv, -1, np.int64)
    anyuse = np.zeros(nv, np.bool_)
    for e in range(len(uv)):
        v = uv[e]
        if A[e] > maxA[v]:
            maxA[v] = A[e]
        sc = A[e] + 1e-9 * cnt[e]
        if sc > best[v]:
            best[v] = sc
            nstar[v] = uk[e]
        if cnt[e] > 0:
            anyuse[v] = True
    return maxA, nstar, anyuse


def run(case, sol, time_limit, warm=None, theta=1.0, log=print, stall_window=50, threads=4, cap=8192,
        checkpoint=None, every=600):
    t0 = time.time()
    inst = Instance(case)
    sinks = sink_list(inst)
    S, nv = len(sinks), inst.nv
    netk = np.array([k for k, _ in sinks], np.int64)
    drv = np.array([inst.nets[k][1] for k, _ in sinks], np.int64)
    tgt = np.array([inst.nets[k][2][j] for k, j in sinks], np.int64)
    ld = np.array(inst.ld, float)
    owner = inst.pin_owner.astype(np.int64)
    contested = owner < 0
    inc = solution_paths(inst, sol)
    ub = float(sum(inst.path_cost(inc[s]) for s in sinks))
    # support: sorted keys sink * nv + v, values a
    keys, a = np.zeros(0, np.int64), np.zeros(0)
    if warm:
        z = np.load(warm)
        sid = np.repeat(np.arange(S), np.diff(z['off']))
        keys = sid * nv + z['vert'].astype(np.int64)
        o = np.argsort(keys)
        keys, a = keys[o], z['val'][o].astype(float)
        theta = 0.2
    nb.set_num_threads(threads)
    best, best_keys, best_a, stall, it, hist = -np.inf, keys.copy(), a.copy(), 0, 0, []

    def layout(keys):
        soff = np.searchsorted(keys, np.arange(S + 1, dtype=np.int64) * nv)
        sv = keys % nv
        uniq, inv = np.unique(netk[keys // nv] * nv + sv, return_inverse=True)
        return soff, sv, uniq, inv.astype(np.int64)

    def pack(bk, ba):
        keep = ba > 0
        k2 = bk[keep]
        off = np.searchsorted(k2, np.arange(S + 1, dtype=np.int64) * nv)
        return dict(vert=k2 % nv, val=ba[keep], off=off)

    soff, sv, uniq, inv = layout(keys)
    last_ck = time.time()
    while True:
        sp, pbuf, plen = price(inst.W, inst.H, inst.L, ld, float(inst.vd), owner, drv, tgt, netk, soff, sv, a,
                               threads * 4, cap)
        assert plen.max() <= cap, 'path longer than cap'
        pk = np.concatenate([i * nv + pbuf[i, :plen[i]] for i in range(S)])
        pk = np.unique(pk[contested[pk % nv]])
        pos = np.searchsorted(keys, pk)
        hit = (pos < len(keys)) & (keys[np.minimum(pos, max(len(keys) - 1, 0))] == pk) if len(keys) else np.zeros(len(pk), bool)
        if (~hit).any():                       # grow the support
            keys = np.concatenate([keys, pk[~hit]])
            a = np.concatenate([a, np.zeros((~hit).sum())])
            o = np.argsort(keys, kind='stable')
            keys, a = keys[o], a[o]
            soff, sv, uniq, inv = layout(keys)
        use = np.zeros(len(keys))
        use[np.searchsorted(keys, pk)] = 1.0
        uv, uk = uniq % nv, uniq // nv
        A = np.bincount(inv, weights=a, minlength=len(uniq))
        cnt = np.bincount(inv, weights=use, minlength=len(uniq))
        maxA, nstar, anyuse = argmax_net(uv, uk, A, cnt, nv)
        L = sp.sum() - maxA.sum()
        if L > best + 1e-9:
            best, best_keys, best_a, stall = L, keys.copy(), a.copy(), 0
        else:
            stall += 1
            if stall >= stall_window:
                theta *= 0.5; stall = 0
                if theta < 2e-3:
                    theta, keys, a = 0.2, best_keys.copy(), best_a.copy()
                    soff, sv, uniq, inv = layout(keys)
                    it += 1
                    continue
        active = anyuse | (maxA > 0)
        own = (nstar[uv] == uk) & active[uv]
        g = use - own[inv]
        g[(a <= 0) & (g < 0)] = 0.0
        gn = float(g @ g)
        if it % 10 == 0:
            el = time.time() - t0
            hist.append((it, el, L, best))
            log(f'  it {it:5d} t {el:7.1f}s  L {L:10.2f}  best {best:10.2f}  UB {ub}  gap {(ub-best)/ub:7.2%}  '
                f'theta {theta:.4f}  support {len(keys)/1e6:.2f}M', flush=True)
        if checkpoint and time.time() - last_ck > every:   # survive restarts
            save(checkpoint, dict(case=case, sol=sol, lb=best, ub=ub, iters=it + 1, seconds=time.time() - t0,
                                  partial=True), pack(best_keys, best_a))
            last_ck = time.time()
        if gn == 0 or time.time() - t0 > time_limit:
            break
        a = np.maximum(0.0, a + theta * (ub - L) / gn * g)
        if it % 200 == 199:                   # prune dead entries
            keep = (a > 0) | (use > 0)
            if keep.sum() < 0.8 * len(keys):
                keys, a = keys[keep], a[keep]
                soff, sv, uniq, inv = layout(keys)
        it += 1
    return (dict(case=case, sol=sol, lb=best, ub=ub, iters=it + 1, seconds=time.time() - t0, hist=hist),
            pack(best_keys, best_a))


def save(out, r, sa):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out + '.alpha.tmp.npz', **sa)
    os.replace(out + '.alpha.tmp.npz', out + '.alpha.npz')
    json.dump(r, open(out + '.json', 'w'), indent=1)


if __name__ == '__main__':
    case, sol, tl, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    warm = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != '-' else None
    threads = int(sys.argv[6]) if len(sys.argv) > 6 else 4
    r, sa = run(case, sol, tl, warm=warm, threads=threads, checkpoint=out)
    print(json.dumps({k: v for k, v in r.items() if k != 'hist'}))
    save(out, r, sa)
