#!/bin/bash
# 8 workers: 10 min native start screening, 20 min anneal, then 8 x 20 min continuation; vs port/w8 (8598, 21139)
cd "$(dirname "$0")/../.."
mkdir -p lpbound/screen
for c in case_01 case_07; do
  PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite benchmarks_hard --out-dir lpbound/screen/w8 --cases $c \
    --workers 8 --screen-seconds 600 --explore-seconds 1200 --native-seconds 1200 --seed 1 \
    --binary routers/priced_cpp/build/optimizer_scratch > lpbound/screen/w8_$c.log 2>&1 &
done
wait
grep -h "FINISHED\|Error" lpbound/screen/*.log
