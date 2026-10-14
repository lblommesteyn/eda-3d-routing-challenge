"""Integrality-gap calibration on instances small enough to solve exactly.

For each small hard-density instance: the negotiated reference (what a decent
router finds), the LP relaxation value (what lagr.py bounds), and the exact MILP
optimum (HiGHS, warm-started from the reference). The ratio OPT / LP says how
much of the hard-tier "max gain" is LP looseness rather than real headroom.

    python -m lpbound.calib_small gen <side> <n_nets> <seeds>
    python -m lpbound.calib_small solve <instance.json> <seconds> <threads>
"""
import json
import sys
import time

import numpy as np

from lpbound.cg import Instance
from lpbound.exact import build, solve_highspy


def gen(side, n_nets, seeds):
    from m3d.generator import GenConfig, generate_feasible
    for seed in range(seeds):
        cfg = GenConfig(name=f's{side}n{n_nets}_{seed}', width=side, height=side, layers=6,
                        n_nets=n_nets, frac_cross=0.45, p_twopin=0.6, max_fanout=6,
                        frac_local=0.12, cell_min=2, cell_max=2, pins_per_cell=2, cell_gap=0,
                        seed=7000 + 1000 * side + seed, router='negotiated')
        try:
            r = generate_feasible(cfg)
        except Exception as e:
            print(cfg.name, 'failed', e); continue
        g = r.instance
        r.instance.save(f'lpbound/calib/small/{cfg.name}.json')
        r.reference.save(f'lpbound/calib/small/{cfg.name}.ref.sol.json')
        print(cfg.name, 'grid', g.width, 'nets', len(g.nets), 'negotiated', r.baseline_total, flush=True)


def lp_value(case, threads):
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
    h = highspy.Highs()
    h.setOptionValue('output_flag', False)
    h.setOptionValue('threads', threads)
    h.passModel(lp)
    h.run()
    return h.getInfo().objective_function_value


def solve(case, seconds, threads):
    t = time.time()
    lp = lp_value(case, threads)
    t_lp = time.time() - t
    ref = case.replace('.json', '.ref.sol.json')
    from m3d.checker import check
    from m3d.model import Instance as MI, Submission
    neg = check(MI.load(case), Submission.load(ref)).total_delay
    t = time.time()
    r = solve_highspy(case, seconds, threads=threads, start_sol=ref)
    out = dict(case=case, lp=lp, lp_s=t_lp, negotiated=neg, mip=r['ip'], mip_bound=r['bound'],
               status=r['status'], mip_s=time.time() - t,
               opt_over_lp=r['ip'] / lp, neg_over_opt=neg / r['ip'])
    json.dump(out, open(case.replace('.json', '.calib.json'), 'w'), indent=1)
    print(json.dumps(out), flush=True)


if __name__ == '__main__':
    if sys.argv[1] == 'gen':
        import os
        os.makedirs('lpbound/calib/small', exist_ok=True)
        gen(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    else:
        solve(sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))
