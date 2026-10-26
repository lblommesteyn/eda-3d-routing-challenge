#!/bin/bash
# full pipeline, 20 min native: does a fanout-scaled start land in a better basin after annealing?
cd "$(dirname "$0")/../.."
mkdir -p lpbound/fanstart
run() { tag=$1; c=$2; s=$3; shift 3; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite benchmarks_hard \
  --out-dir lpbound/fanstart/${tag}_s$s --cases $c --seed $s --native-seconds 1200 "$@" > lpbound/fanstart/${tag}_s${s}_$c.log 2>&1; }
for c in case_01 case_07; do for s in 2 3; do
  run plain $c $s &
  run fan $c $s --fanout-scale &
done; done
wait
for f in lpbound/fanstart/*.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -c1-140)"; done
