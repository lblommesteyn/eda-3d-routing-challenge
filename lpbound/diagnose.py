"""Split UB - L(alpha) into where a given legal solution loses to the dual.

For a legal solution with sink paths p_s:
    UB - L = sum_s [c(p_s) + alpha_s(p_s) - SP_s]                (path slack >= 0)
           + sum_v [maxA_v - sum_{s: v in p_s} alpha[s, v]]       (price slack >= 0)
Path slack: the sink's route is worse than its priced shortest path.
Price slack: a priced vertex is not (fully) held by the net bidding most for it.
Both terms are nonnegative, and they sum to the duality gap exactly.
"""
import json
import sys
from collections import defaultdict

import numpy as np

from lpbound.cg import Instance, solution_paths
from lpbound.lagr import sink_list, price_all


def main(case, sol, alpha_npz, top=15):
    inst = Instance(case)
    sinks = sink_list(inst)
    alpha = np.load(alpha_npz)['alpha']
    K = len(inst.nets)
    base_w = {k: inst.net_weights(k) for k in range(K)}
    sp, _ = price_all(inst, sinks, alpha, base_w)
    paths = solution_paths(inst, sol)
    net_of = np.array([k for k, _ in sinks])
    A = np.zeros((K, inst.nv))
    np.add.at(A, net_of, alpha)
    maxA = np.maximum(A.max(axis=0), 0)
    ub = 0.0
    path_slack = np.zeros(len(sinks))
    held = np.zeros(inst.nv)
    for i, s in enumerate(sinks):
        p = paths[s]
        c = inst.path_cost(p)
        ub += c
        path_slack[i] = c + alpha[i, p].sum() - sp[i]
        held[p] += alpha[i, p]
    price_slack = maxA - held
    L = sp.sum() - maxA.sum()
    out = dict(ub=ub, L=L, gap=ub - L, path_slack=path_slack.sum(), price_slack=price_slack.sum())
    by_net = defaultdict(float)
    for i, (k, _) in enumerate(sinks):
        by_net[k] += path_slack[i]
    out['top_nets_path_slack'] = sorted(((round(v, 1), inst.nets[k][0]) for k, v in by_net.items()), reverse=True)[:top]
    z = np.arange(inst.nv) // (inst.W * inst.H)
    out['price_slack_by_layer'] = [round(float(price_slack[z == l].sum()), 1) for l in range(inst.L)]
    out['path_slack_share_top10pct_nets'] = sum(sorted(by_net.values(), reverse=True)[:max(1, K // 10)]) / max(1e-9, path_slack.sum())
    return out


if __name__ == '__main__':
    print(json.dumps(main(*sys.argv[1:4]), indent=1))
