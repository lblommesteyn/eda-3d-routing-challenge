"""Small instances with the hard tier's density parameters (calibration only)."""
import json, sys
from m3d.generator import GenConfig, generate_feasible
out = []
for side in (8, 10, 12):
    for seed in range(3):
        cfg = GenConfig(name=f'h{side}_{seed}', width=side, height=side, layers=6,
                        n_nets=round(2.6 * side), frac_cross=0.45, p_twopin=0.6, max_fanout=6,
                        frac_local=0.12, cell_min=2, cell_max=2, pins_per_cell=2, cell_gap=0,
                        seed=1000 * side + seed, router='negotiated')
        try:
            r = generate_feasible(cfg)
        except Exception as e:
            print(cfg.name, 'failed', e); continue
        r.instance.save(f'lpbound/calib/{cfg.name}.json')
        r.reference.save(f'lpbound/calib/{cfg.name}.ref.sol.json')
        print(cfg.name, len(r.instance.nets), 'nets, negotiated total', r.baseline_total, flush=True)
