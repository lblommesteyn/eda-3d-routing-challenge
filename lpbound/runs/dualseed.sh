#!/bin/bash
# gentle negotiation seeded with our own duals vs not, 4 seeds, case_01
cd "$(dirname "$0")/../.."
mkdir -p lpbound/ds
for s in 0 1 2 3; do
  PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/ds/dual_s$s --cases case_01 \
    --routing-seconds 900 --refinement-seconds 1 --rounds 1 --native-seconds 0 --max-iters 5000 \
    --present 0.3 --history 0.5 --growth 1.05 --seed $s --dual-dir lpbound/dual_indep > lpbound/ds/dual_s$s.log 2>&1 &
done; wait
for f in lpbound/ds/*.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -d: -f2 | cut -c1-200)"; done
