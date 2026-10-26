#!/bin/bash
# 60 min tuned optimizer (negotiated moves) on three hard cases, two seeds each
cd "$(dirname "$0")/../.."
mkdir -p lpbound/long
for c in case_01 case_05 case_09; do for s in 4 5; do
  PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_neg/hard \
    --out-dir lpbound/long/s$s --cases $c --seconds 3600 --iterations 1000000000 --neighbor-polish --temperature 10 --max-group 12 \
    --seed $s --negotiate 30 20 0.5 1.3 0.5 > lpbound/long/${c}_s$s.log 2>&1 &
done; done; wait
for f in lpbound/long/*.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -c1-200)"; done
