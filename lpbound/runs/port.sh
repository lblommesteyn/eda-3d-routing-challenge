#!/bin/bash
# portfolio: 8 pipelines x 20 min, then 8 chains x 20 min from the best
cd "$(dirname "$0")/../.."
mkdir -p lpbound/port
for c in case_01 case_07; do
  PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite benchmarks_hard --out-dir lpbound/port/w8 --cases $c \
    --workers 8 --explore-seconds 1200 --native-seconds 1200 --mix-fanout --seed 1 > lpbound/port/w8_$c.log 2>&1 &
done
wait
grep -h FINISHED lpbound/port/*.log
