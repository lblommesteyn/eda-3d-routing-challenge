#!/bin/bash
# continue the 2 h hard_v1 routes: do fresh chains from the incumbent still improve? 30 min
cd "$(dirname "$0")/../.."
mkdir -p lpbound/island
run() { tag=$1; c=$2; shift 2; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/tier/hard_v1 \
  --out-dir lpbound/island/$tag --cases $c --seconds 1800 --iterations 1000000000 --neighbor-polish --max-group 12 \
  --negotiate 30 20 0.5 1.3 0.5 --cycle 20000 "$@" > lpbound/island/${tag}_$c.log 2>&1; }
for c in case_01 case_04 case_07; do
  run s11 $c --seed 11 --temperature 10 &
  run s12 $c --seed 12 --temperature 10 &
  run t5 $c --seed 13 --temperature 5 &
  run t20 $c --seed 14 --temperature 20 &
done
wait
for f in lpbound/island/*.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -c1-100)"; done
