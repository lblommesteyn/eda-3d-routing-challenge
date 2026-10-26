#!/bin/bash
# seed 3, group 16 at three time limits, per-net diagnostics on failure
cd "$(dirname "$0")/../.."
for t in 120 300 600; do
  PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_neg/hard \
    --out-dir lpbound/repro/r$t --cases case_01 --seconds $t --iterations 100000000 --neighbor-polish --temperature 20 --max-group 16 --seed 3 \
    > lpbound/repro/r$t.log 2>&1 &
done; wait
for t in 120 300 600; do echo "$t $(grep -h 'FINISHED\|RuntimeError' lpbound/repro/r$t.log | cut -c1-600)"; done
