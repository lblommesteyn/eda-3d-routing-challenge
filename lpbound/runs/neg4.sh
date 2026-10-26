#!/bin/bash
# gentle negotiation, 4 seeds, on given cases
cd "$(dirname "$0")/../.."
mkdir -p lpbound/neg4
for c in "$@"; do for s in 0 1 2 3; do
  PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/neg4/${c}_s$s --cases $c \
    --routing-seconds 900 --refinement-seconds 1 --rounds 1 --native-seconds 0 --max-iters 5000 \
    --present 0.3 --history 0.5 --growth 1.05 --seed $s > lpbound/neg4/${c}_s$s.log 2>&1 &
done; done; wait
for f in lpbound/neg4/*.log; do echo "$f $(grep FINISHED $f | cut -d: -f2)"; done
