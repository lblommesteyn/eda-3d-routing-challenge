# lpbound: lower bounds and dual prices for m3d routing

Research tooling, not a submission. Nothing here touches `m3d/`, benchmarks or
`submissions/`.

## Relaxation

For each sink `s` of net `n(s)`, choose a driver-to-sink path `p_s` (foreign pins
forbidden). `y[n,v]` says net `n` occupies vertex `v`.

    min  sum_s c(p_s)
    s.t. v in p_s  =>  y[n(s), v] = 1
         sum_n y[n, v] <= 1

Every legal routing satisfies this (a tree's driver-to-sink paths are paths, and
vertex exclusivity is the checker's rule), so its LP value is a lower bound on
the optimal total delay. Tree acyclicity and edge exclusivity are dropped (edge
exclusivity is implied by vertex exclusivity anyway).

## Lagrangian dual (`lagr.py`)

Dualizing the linking rows with `alpha[s,v] >= 0` and eliminating the capacity
duals gives

    L(alpha) = sum_s SP_s(alpha_s) - sum_v max(0, max_n sum_{s in n} alpha[s,v])

`SP_s(alpha_s)` is a Dijkstra with vertex costs `alpha_s` on entry. `L` is
concave, and **every** `alpha >= 0` gives a valid bound (weak duality, short
proof in `verify.py`). `lagr.py` runs projected subgradient ascent with Polyak
steps toward the best known total. Delays are integers, so `ceil(L)` is the bound.

`cg.py` solves the same LP by column generation with HiGHS. It stalls on the
degenerate warm start and is kept only for reference; use `lagr.py`.

## Tools

    python -m lpbound.lagr_win <case.json> <sol.json> <seconds> <out_prefix> [warm.npz|-] [threads]
    python -m lpbound.verify   <case.json> <out_prefix>.alpha.npz          # independent recheck
    python -m lpbound.summarize                                           # tier ceilings from verify files
    python -m lpbound.diagnose <case.json> <sol.json> <alpha.npz>         # where the gap lives (dense alpha)
    python -m lpbound.best_public <tier>                                  # best checker total per case

`lagr_win.py` is the solver to use. It runs the same subgradient as `lagr.py`
but prices each sink with an exact numba A* (heuristic: cheapest layer delay
times xy distance plus via delay times z distance, which is admissible and
consistent) and stores alpha sparsely. Ascent from alpha = 0 only raises
alpha[s, v] where sink s's path has passed, so the support grows from the
paths and the iteration matches the dense version exactly. It is about 60x
faster per iteration than `lagr.py` on the hard tier (case_01: 8089 in 60 s vs
8069 in 30 min) and fits the 116x116 congested cases in about 1.5 GB. It writes
a checkpoint every 10 minutes and warm-starts from any of its own outputs.
`lagr.py` (dense) is kept for reference.

`verify.py` shares no code with the solver: its own graph, a heapq Dijkstra with
foreign pins removed outright, and a recomputation of `L`. It reads both the
dense and the sparse alpha formats.

`diagnose.py` splits `UB - L` exactly into **path slack**
(`c(p_s) + alpha_s(p_s) - SP_s`, which sinks route worse than their priced
shortest path) and **price slack** (priced vertices not held by the net bidding
most for them). These are the reroute targets for the primal side.

## Results (2026-10-14)

Certified bounds: `ceil(L)` as re-derived by `verify.py`, the best over all runs
of a case. "Best public" is the lowest checker total over all
`submissions/<tier>/*` entries at upstream `49b6443` (`best_public.py`). Max gain
is how far below today's best any legal routing could possibly go. Hard runs:
`lagr_win.py`, 15 min per case (`runs/hard3/`). Big tiers: 2 h per case, 2
threads (`runs/big/`), plus warm continuations where the bound was still
rising (`runs/big2/`).

**Hard: ceiling 1.5305** (best per case today 1.4138)

| case | baseline | best public | naive LB | certified bound | max gain |
|---|---:|---:|---:|---:|---:|
| case_01 | 11938 | 8540 | 7156 | 8089 | 5.3% |
| case_02 | 18910 | 12794 | 9976 | 11646 | 9.0% |
| case_03 | 14724 | 10922 | 8744 | 10207 | 6.5% |
| case_04 | 18077 | 12861 | 10253 | 11871 | 7.7% |
| case_05 | 21051 | 14987 | 12129 | 13863 | 7.5% |
| case_06 | 27134 | 19322 | 15332 | 17679 | 8.5% |
| case_07 | 31341 | 20887 | 16461 | 19008 | 9.0% |
| case_08 | 30886 | 20932 | 16576 | 19318 | 7.7% |
| case_09 | 27802 | 21062 | 17404 | 19519 | 7.3% |

"Naive LB" ignores other nets (cheapest single-layer detour per sink); the
naive max gain was 16 to 22%.

**Congested: ceiling 1.4753** (best per case today 1.3508)

| case | baseline | best public | certified bound | max gain |
|---|---:|---:|---:|---:|
| case_01 | 71049 | 52233 | 48146 | 7.8% |
| case_02 | 122534 | 92856 | 85582 | 7.8% |
| case_03 | 216835 | 159095 | 144375 | 9.3% |
| case_04 | 234127 | 172047 | 156851 | 8.8% |

case_03 and case_04 had a 2 h warm continuation (10.6% to 9.3%, 10.5% to 8.8%)
and were still rising slowly when it stopped, so their true max gain is a little
lower than shown. The other five cases had flattened out.

**Designs: ceiling 1.5921** (best per case today 1.4673)

| case | baseline | best public | certified bound | max gain |
|---|---:|---:|---:|---:|
| ctrl | 73148 | 48930 | 45328 | 7.4% |
| int2float | 152284 | 88064 | 80470 | 8.6% |
| router | 82491 | 67503 | 62429 | 7.5% |

No legal routing can exceed these ceilings. Every best-per-case entry today is
`coordinated_refinement` or `cuda-have-been-shorter`.

The bounds are close to the LP value: on a 14x14 hard-density instance
(`calib/h8_0`) subgradient reached 1774.55 against the exact LP of 1776.55. What
remains between a bound and the true optimum is the LP-to-integer gap, measured
next.

## Integrality gap: exact solves (2026-10-14)

`calib_small.py` makes hard-density instances (about 0.34 pins per grid cell,
the same generator settings as the hard tier) small enough for HiGHS to prove
optimality, then reports the LP, the true optimum and the negotiated router's
total. `gapsplit.py` re-solves with only some nets' `y` binary (each variant is
still a relaxation), to see which nets' integrality creates the gap.

| instance | LP | OPT | LP gap | negotiated / OPT | multi-sink integral closes | 2-pin integral closes |
|---|---:|---:|---:|---:|---:|---:|
| s8n7_0 | 436.24 | 446 | 2.19% | 1.206 | 82% | 100% |
| s8n7_1 | 158.00 | 160 | 1.25% | 1.000 | (no multi-sink nets) | 100% |
| s8n7_2 | 454.80 | 468 | 2.82% | 1.141 | 100% | 44% |
| s8n7_3 | 280.14 | 289 | 3.07% | 1.097 | 100% | 77% |
| s8n7_4 | 419.26 | 430 | 2.50% | 1.167 | 100% | 19% |
| s8n7_5 | 377.00 | 379 | 0.53% | 1.121 | 100% | 100% |
| s10n11_0 | 707.56 | in [709.9, 734] | 0.3 to 3.6% | at most 1.166 | | |
| s10n11_1 | 785.41 | in [788.3, 853] | 0.4 to 7.9% | at most 1.227 | | |
| h8_0 (14x14) | 1776.55 | in [1777.6, 2049] | | at most 1.178 | | |

The s10 and h8_0 rows hit the time limit (40 min and 2 h). On the six
instances solved to optimality the LP sits **2.1% below the optimum on average
(0.5 to 3.1%)**. Read against the hard tier's 5.3 to 9.0% "max gain", roughly
2 points of each case's gap are probably LP looseness and the rest (about 3 to
7%) is room the leaders have not taken. The same reading applies to congested
and designs (7.4 to 9.3% max gain). The negotiated router lands 10 to 21%
above optimal on these instances, and the exact solver beat it by 15% on h8_0,
so the primal side is far from saturated at this density.

## Cuts (2026-10-14)

- **Generic MIP cuts do nothing.** HiGHS's root separation (Gomory, MIR,
  clique and others; 6000 to 8000 cuts in the pool over 5 root rounds) leaves the root bound at
  exactly the LP value on s8n7_0 (436.24) and moves h8_0 from 1776.55 to 1777.65
  in 2 hours. Only branching closes the gap.
- **Net-level cuts do almost nothing.** `netdw.py` computes the bound with each
  net's own routing forced integral (convex hull per net, which implies every
  single-net valid inequality). It is a Lagrangian over vertex prices, started
  from the LP capacity duals, with an exact single-net MILP as the pricing step. On s8n7_4,
  where making the multi-sink nets integral closes 100% of the gap, the net hull
  gives 419.58 against LP 419.26 and OPT 430 (3% of the gap, 25 min). On s8n7_0 it
  stays at the LP. Plain DW column generation (`netdw.run`) tails off and is not
  usable.
- **What the gap is.** In the LP, 2-pin nets split across several cheap paths
  and share vertices fractionally with the multi-sink nets they cross (on
  s8n7_0, nets 0, 2, 5 and 6 each pay 1.5 to 3.5 more in the optimum than in
  the LP). Integrality on either side of an interaction removes it, while
  integrality inside a single net does not. So valid inequalities would have to
  couple two or more nets, such as a joint hull over small groups of crossing
  nets. Those need a multi-net pricing problem, which is a small MILP per group
  and does not scale to the hard tier in this form. Not pursued further.

Dead ends, recorded so they are not retried:
- Column generation (`cg.py`) from the leaders' integral solution stays at the
  incumbent. Even with priced columns at the best duals, an independent scipy
  build of the RMP is integral at 8540. LP re-solves grow to 20 s or more per round.
- The Volume algorithm from a warm start did not beat plain subgradient with
  restarts.
- Pooling all public routes per net and re-selecting with an ILP gains 0.00%
  on all 9 hard cases. The public solutions cannot be combined.

## Interface for the primal router

`lagr_win.py` writes `<out>.alpha.npz` sparsely: sink `i` holds prices `val[off[i]:off[i+1]]`
on vertices `vert[off[i]:off[i+1]]`, zero elsewhere. Sinks are in instance order
(nets in file order, sinks in list order), and vertices are `(z*H + y)*W + x`.
Per-vertex congestion prices are `pi_v = max_n sum_{s in n} alpha[s,v]`. The old
dense format (from `lagr.py`) holds `alpha` with shape `(n_sinks, W*H*L)`;
those runs are not committed. Use `runs/hard3/`, `runs/big/` and `runs/big2/` for the current bounds.

## Window LNS on the public pool (2026-10-16)

`pool_best.py <tier>` scores every distinct route in `lpbound/pool/` (merged
entries plus open upstream PRs, extracted locally; not committed) and keeps
the best legal file per case. `wlns.py` then rips up every net touching a
window and re-solves them jointly as an exact per-sink-flow MILP (HiGHS,
warm-started from the incumbent), with windows aimed at sinks of large dual
path slack. `gmip.py` is the same model over all nets, which is too large for
the hard tier (case_01 never left the root LP in 40 min).
`make_submission.py <tier> <name>` re-checks everything with `m3d/checker.py`
and writes `submissions/<tier>/<name>/`.

Gains over the pool best are small, and most windows prove optimal:

| case | pool best | after wlns |
|---|---:|---:|
| hard case_05 | 14963 | 14961 |
| congested case_01 | 52219 | 52217 |
| congested case_02 | 92376 | 92374 |
| designs int2float | 87334 | 87316 |

Everything else is unchanged. On 2026-10-18 congested case_04 took 170349 from jay-tau's upstream PR #58. The result is `submissions/*/lpbound_window_lns`
(hard 1.4157, congested 1.3591, designs 1.4784). It is a derivative entry that
credits coordinated_refinement, cuda-have-been-shorter and
warm_lns_refinement. The public routes are at a strong local optimum, and the
3 to 7% headroom left by the bounds needs larger restructuring than exact
windows can reach.
