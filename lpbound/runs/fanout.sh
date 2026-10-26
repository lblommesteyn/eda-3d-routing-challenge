#!/bin/bash
# gentle negotiation with and without fanout scaling, 4 seeds, case_01
cd "$(dirname "$0")/../.."
mkdir -p lpbound/fan
for s in 0 1 2 3; do for f in on off; do
  extra=""; [ $f = on ] && extra=--fanout-scale
  PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/fan/${f}_s$s --cases case_01 \
    --routing-seconds 900 --refinement-seconds 1 --rounds 1 --native-seconds 0 --max-iters 5000 \
    --present 0.3 --history 0.5 --growth 1.05 --seed $s $extra > lpbound/fan/${f}_s$s.log 2>&1 &
done; done; wait
for f in lpbound/fan/*.log; do echo "$f $(grep FINISHED $f | cut -d: -f2)"; done
