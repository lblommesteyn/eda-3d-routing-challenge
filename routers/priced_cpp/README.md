# C++ per-net router with vertex prices

A hackathon starting point: generate up to K distinct, low-cost 3D routing trees
for a net given a nonnegative vertex price map. The search runs in C++17; the
Python standard-library bridge reads the challenge JSON and writes candidates.
No external solver or JSON library is needed.

## Working hard baseline

The immediate goal is a reproducible run of our own router with all nine hard
cases legal. Run from the repository root; this uses no public route inputs or
external dual prices:

```bash
make -C routers/priced_cpp
PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard \
  --out-dir build/priced_cpp_hard_baseline_v2 --routing-seconds 60 \
  --refinement-seconds 15 --native-seconds 30 --rounds 2 --seed 0
```

The output includes `.sol.json` routes, `report.json` with legality, delay and
phase metrics, and `runtime.json` with per-case end-to-end seconds. The aggregate
is a full-tier score only when all nine cases finish legally. The same command
accepts other suites through `--suite`. Time budgets can change the final routes
across machines; the seed and configuration are recorded.

Measured baseline: **9/9 legal, aggregate 1.143400280, 178275 total delay,
510.08 seconds**. All saved routes were independently rescored. Per-case and
per-stage results are in [BASELINE_HARD.md](BASELINE_HARD.md). This is our own
from-scratch result; the 1.413809501 public-route collection below is separate.

## Seed from lpbound dual prices

Install NumPy for the NPZ adapter. If this checkout includes Luke's `lpbound`
branch, the price files are already available in `lpbound/runs/hard3`:

```bash
python3 -m pip install numpy
PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard \
  --dual-dir lpbound/runs/hard3 --out-dir build/priced_cpp_hard_seeded \
  --routing-seconds 60 --refinement-seconds 15 --native-seconds 30 \
  --rounds 2 --seed 0
```

For other checkouts, download verified files using `fetch_duals.py` below and
pass `--dual-dir build/dual_prices/hard`. The sparse adapter follows the sink
ordering and vertex indexing in `lpbound/README.md`; it also accepts dense NPZ.
Initial routes and congestion negotiation add `pi[v]=max_net sum_sink alpha[s,v]`
to live congestion prices. Legal physical-delay polish ignores those seed
penalties. Native refinement receives the full per-sink duals and net bids.
Both routing and native metrics record the NPZ SHA-256. The unseeded baseline
above remains the control; loading duals does not guarantee a score improvement.

Hard case 01 smoke check with these settings: **10060 delay, legal, 49.06 s**;
the unseeded control was **9760**. Both routing and native reports recorded the
actual NPZ hash. This confirms integration, not a quality gain. All **50 tests**
pass, including a fixture where a dual-priced vertex changes the initial route
and subsequent physical-delay polish removes the unnecessary detour.

## Hard-first optimization experiments

An aspirational research target is hard aggregate >=1.45. Inputs
and runners are suite-generic. Refinement of public routes records their source;
timing excludes producing those input routes. Optional optimization stages use:

```bash
.venv/bin/pip install -r routers/priced_cpp/requirements-selection.txt
make -C routers/priced_cpp
python3 routers/priced_cpp/fetch_duals.py --suite benchmarks_hard \
  --remote-dir lpbound/runs/hard3 --out-dir build/dual_prices/hard
python3 routers/priced_cpp/collect.py --suite benchmarks_hard \
  --sources submissions/hard/coordinated_refinement submissions/hard/leonid-popryho \
  submissions/hard/cuda-have-been-shorter --out-dir build/priced_cpp_hard_public
.venv/bin/python routers/priced_cpp/dual.py --suite benchmarks_hard \
  --route-dir build/priced_cpp_hard_public --dual-dir build/dual_prices/hard \
  --out-dir build/priced_cpp_hard_diagnostics
.venv/bin/python routers/priced_cpp/layers.py --suite benchmarks_hard \
  --start-dir build/priced_cpp_hard_public --out-dir build/priced_cpp_hard_layers \
  --cases case_01 --chunk 1 --all-layers --seconds 120
.venv/bin/python routers/priced_cpp/joint_repair.py --suite benchmarks_hard \
  --start-dir build/priced_cpp_hard_public --dual-dir build/dual_prices/hard \
  --out-dir build/priced_cpp_hard_joint --cases case_01 \
  --size 8 --groups 60 --group-seconds 8 --seconds 300 --ownership-ties
python3 routers/priced_cpp/collect.py --suite benchmarks_hard \
  --sources build/priced_cpp_hard_public build/priced_cpp_hard_layers \
  build/priced_cpp_hard_joint --out-dir build/priced_cpp_hard_best \
  --bounds build/dual_prices/hard/bounds.json --reference-dir build/priced_cpp_hard_public
```

The downloader pins the friend's lpbound commit and checks instance and NPZ Git
hashes. Sparse duals retain per-sink alpha and derive `pi[v]=max_net sum_sink alpha`.
Diagnostics recompute the dual with the C++ engine and split the official delay
gap into path slack and price slack. The nine hard values match the supplied
independent verifier within 7.3e-12. Format and research notes are in the
[lpbound README](https://github.com/lblommesteyn/eda-3d-routing-challenge/blob/eb8fae4602b059318c61ea53d887c34c67594f3d/lpbound/README.md).

`joint_repair.py` fixes other nets and jointly changes a small group's geometry.
It uses continuous sink flows and binary vertex ownership, cost-bounded corridors,
a conditional dual bound to prune impossible priced paths,
a legal warm start, and C++ tree extraction. The physical objective stays primary;
optional `--ownership-ties` adds a bounded dual-based tie cost to reallocate
resources among equal-delay layouts. Saved routes always pass the official checker. `native.py --dual-dir`
and `priced_repair.py` provide faster heuristic alternatives.

`joint_repair.py --backend cbs` branches on vertex conflicts and uses C++ tree
queries plus dual bounds instead of building a flow model. `--fixed-xy` solves
all nets within the original XY edge graphs, allowing branch mergers; case 01
proved optimum 8540 in 6.48 seconds in this broader model too. To permit geometry
changes, use `--xy-corridor 1` (one-cell expansion). `--sink-extra 4` additionally
caps each sink's delay increase; optimality then applies only to that restricted
search. `--aggregate` uses one flow per net with sink multiplicities, reducing
duplicated network variables; it cannot be combined with individual sink caps.
All variants retain the legal incumbent and report actual checker delay.

Current measurements: the public starting score is 1.413809501; early probes have
not improved it. Case 01's layer-only models proved optimum 8540 at both tested
segment resolutions. Read BASELINE_HARD.md for the measured from-scratch control.
The collected report's bound gap is `(delay-bound)/bound`; `improvement_percent`
compares with the supplied public-reference routes. No new full-runtime or
leaderboard-win claim follows from simply collecting public routes.

## Build and run

Run from the repository root:

```bash
make -C routers/priced_cpp
python3 routers/priced_cpp/route.py \
  --case benchmarks/case_01.json --net 0 --k 4 \
  --out build/case01-net0.candidates.json
make -C routers/priced_cpp test
```

Omit `--net` to generate candidates for every net in one native process.
Use `make -C routers/priced_cpp CXX=g++` if needed.

## Supply prices

Create a JSON file such as `build/prices.json`:

```json
{
  "vertices": [
    {"vertex": [5, 5, 2], "price": 20},
    {"vertex": [5, 6, 2], "price": 10}
  ],
  "blocked_vertices": [[6, 5, 2]]
}
```

Then add `--prices build/prices.json`. Omitted vertices have zero price.
Prices use the same scale as delay; negative and nonfinite prices are rejected.
Hard-blocked vertices cannot be used. Foreign pins are always forbidden.

For prices from current congestion:

```bash
python3 routers/priced_cpp/route.py \
  --case benchmarks/case_01.json --net 0 \
  --occupancy benchmarks/reference/case_01.sol.json \
  --present-factor 10 --k 4 --out build/congestion.candidates.json
```

For the requested net, each vertex gets `external_price + present_factor *
number_of_other_nets_using_vertex`. Its own previous route is excluded.
Shared branches count once per net. `--hard-occupancy` makes other nets' used
vertices unavailable, useful for replacing one net in an already legal solution.
Blocking shared vertices also prevents shared edges in this capacity-one graph.

## Algorithm and objective

1. Start a tree at the driver.
2. Seed Dijkstra at every tree vertex with its current true driver-to-vertex
   delay. Traversing a new edge costs its delay plus the destination's price.
3. Attach the first reached unconnected sink. Existing tree vertices cannot
   be traversed, so each attachment preserves connectivity and acyclicity.
4. Repeat until all sinks are connected.
5. Generate alternatives with an accumulated penalty on previously used
   vertices. Deduplicate whole trees and sort them by original priced cost.

Each candidate reports:

* `delay`: the sum of driver-to-sink delays, matching the official checker;
* `vertex_price`: sum of supplied prices on distinct used vertices, including
  pins; shared branches are charged once;
* `priced_cost`: `delay + vertex_price`;
* `edges`: the normal challenge coordinate edge format.

The reuse penalty encourages diversity but is excluded from reported costs.
`--diversity` controls its strength (default 2); `--attempts` limits searches
(default 4*K). Fewer than K candidates is valid if searches repeat or resources
limit the available paths. An unroutable net has an empty candidate list and the
CLI exits with status 2. Invalid requests exit with status 1.

For two-pin nets, the first candidate is a shortest path for delay plus vertex
prices. For multi-sink nets this is greedy tree growth: it does not guarantee
the globally minimum priced tree or the K cheapest trees. Existing trunk prices
are sunk costs during later attachments; true trunk delay still matters because
the challenge charges that delay again for every sink using the trunk.

## Integration with your friend's code

The Python API is `generate(inst, net_ids=[net_id], prices={vertex_id: price},
occupancy=current_submission, k=4)` in `route.py`. Vertex IDs use the existing
`Grid.vid`: `(z * height + y) * width + x`. The output contains a `nets` list,
each with a `net` ID and a `candidates` list. A candidate's `edges` can be put
into a normal submission route after selection.

Candidate output is **not a submission**: different nets' candidates can conflict.
A global controller must select routes, update occupancy/history prices, and
reroute conflicts. Check the assembled solution with `m3d.checker.check`.
The per-net component supplies a current-occupancy price adapter.
`controller.py` implements the outer negotiation loop described below.

`native_wall_seconds` includes native process startup and the batch search, but
excludes Python input loading and output writing. It is a diagnostic, not a
submission's required end-to-end `runtime.json` measurement.

## Next experiments

* Compare zero prices and congestion prices on identical cases; measure true
  delay, price cost, candidate overlap, and search time.
* Tune present/history price schedules and compare convergence iterations
  and legal delay under the same time budget.
* Try different sink orders or bounded local tree improvement for multi-sink
  nets; the greedy topology can limit quality.
* Profile larger tiers before optimizing heap operations or search buffers.

Existing toolkit files and benchmark inputs are unchanged. If you later warm-start
from published submissions, follow the attribution rules in `CONTRIBUTING.md`.


## Full routing controller and step metrics

```bash
python3 routers/priced_cpp/controller.py \
  --case benchmarks/case_10.json \
  --out build/controller/case_10.sol.json \
  --metrics build/controller/case_10.metrics.json
```

The controller starts from independent routes, maintains distinct-net vertex
occupancy, increases history prices on overused vertices, and sequentially
reroutes affected nets with fresh occupancy prices. It considers the old route
at its newly calculated cost as well as the generated alternatives. Once legal,
it tries replacements with all other nets hard-blocked and accepts only strictly
lower true delay, checking the assembled solution before accepting each change.
A persistent native worker avoids restarting C++ for every net.

The metrics JSON stores every iteration: shared vertices and edges, excess vertex
usage, affected/rerouted nets, diagnostic delay, elapsed time, query count, and
candidate count. `diagnostic_delay` on an illegal routing is not a valid score.
Final `total_delay` is populated only after the official checker passes.
Alternative vertex Jaccard overlap (0 to 1) measures candidate similarity; lower
means more diverse. Shared mandatory pins are included.

`--history 0` disables accumulated history. `--k 1` disables alternative
candidates. `--refinement-passes 0` isolates negotiation. The CLI defaults are one candidate
per negotiation query (`--negotiation-k 1`)
and four per refinement query (`--k 4`), 60 negotiation iterations, present
penalty 2, growth 1.35, history increment 2, one refinement pass, and a 30-second
soft budget. Use `--negotiation-k 4` for the fixed-four comparison. The Python
`solve` API keeps negotiation K equal to K unless explicitly overridden.
The budget is checked between native
queries; an in-flight query and validation can overrun it. A failure returns
status 2, metrics describing the failure, and no new solution. No baseline or
published submission is used as a fallback.

## Reproduce quantitative comparisons

The current from-scratch pipeline combines priced routing, Python neighborhood
refinement, and an optional C++ neighborhood stage:

```bash
make -C routers/priced_cpp
python3 routers/priced_cpp/pipeline.py --out-dir build/priced_cpp_pipeline \
  --routing-seconds 600 --refinement-seconds 180 --native-seconds 180 \
  --native-iterations 100000 --native-temperature 60 --seed 811
```

The native stage keeps unaffected neighboring routes, repairs actual conflicts,
and periodically polishes individual nets. Repeated cooling cycles restart from
the best legal state. The region repair follows the algorithm description in
[pathfinder_lns](https://github.com/partcleda/eda-3d-routing-challenge/pull/3);
this implementation generates its own routes. Every saved result passes the
official checker, including agreement with the native per-net delay values.
`report.json` records phase metrics; `runtime.json` includes all enabled phases.
Standalone `native.py` runs measure refinement only and exclude generating their
starting solutions. The collected best snapshot also excludes a reproducible
end-to-end timing claim.
The default routing budget is 600 seconds: the 120-second tier run failed to
legalize cases 18–20, while the longer retries legalized cases 18 and 20.

Native experiments can select `--binary PATH`; each result records the executable
SHA-256. `--compact-growth` enables cheaper root-delay seeded tree growth with a
tiny new-vertex cost, an A* potential, and randomized ties. It is optional:
measured gains have been small, and its repeated per-sink searches are slower
than the default single-search SPT. `--accurate-prices` enables the subset-DP
oracle instead; these two options are mutually exclusive. The sharing approach
is also described in [m3d-router](https://github.com/IrwinJam/m3d-router);
the implementation here extends this repository's own seeded tree-growth code.
`--neighbor-polish` optionally reroutes up to six retained neighboring nets
before rejecting a costly region move. Its first probe showed only a tiny gain
and fewer proposals per second, so it is disabled by default. Native reports
include failed repairs and uphill proposals when supported by the executable.

```bash
# Original adaptive pipeline on every intro case.
python3 routers/priced_cpp/benchmark.py \
  --methods adaptive_history_refine --out-dir build/priced_cpp_adaptive_intro

# Controlled comparisons on representative cases.
python3 routers/priced_cpp/benchmark.py \
  --cases case_01 case_05 case_10 case_15 case_20 \
  --methods k1_history k4_present k4_history_refine adaptive_history_refine negotiated \
  --out-dir build/priced_cpp_comparison
```

The harness saves `report.json`, `cases.csv`, per-method `runtime.json`, legal
solution files, and detailed per-case trace files. It checkpoints after each
case so failed runs remain visible. Any illegal case makes that run's aggregate
score zero; it never quietly scores just the successful subset. A subset run is
explicitly identified by its case list and does not establish full-tier success.
The `*_before_refinement` summaries are reported from the corresponding legal stage,
so candidate/history comparisons can exclude refinement's improvement.

Reported end-to-end wall time includes instance loading, worker startup,
routing, validation, and solution serialization; metrics/report writing is
excluded. These are single-run observations on the local machine, not repeated
performance estimates. The Python negotiated comparator uses the same iteration
cap but has no wall-time cap; do not interpret it as an equal-time-budget comparison.
It uses its existing default prices, ordering, and search algorithm.
