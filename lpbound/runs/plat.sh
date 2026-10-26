#!/bin/bash
# plateau breaking from the 60 min case_01 result (8722): cycle length x temperature, 20 min
cd "$(dirname "$0")/../.."
mkdir -p lpbound/plat
run() { tag=$1; shift; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_plat/hard \
  --out-dir lpbound/plat/$tag --cases case_01 --seconds 1200 --iterations 1000000000 --neighbor-polish --max-group 12 \
  --seed 6 --negotiate 30 20 0.5 1.3 0.5 "$@" > lpbound/plat/$tag.log 2>&1; }
run c1k_t10 --temperature 10 &
run c5k_t10 --temperature 10 --cycle 5000 &
run c20k_t10 --temperature 10 --cycle 20000 &
run c5k_t30 --temperature 30 --cycle 5000 &
run c20k_t30 --temperature 30 --cycle 20000 &
run c100k_t20 --temperature 20 --cycle 100000 &
wait
for f in lpbound/plat/*.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -c1-120)"; done
