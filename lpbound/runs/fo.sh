#!/bin/bash
# fanout-first repair order, 30 min from hard_v1, compare to island s11/s12
cd "$(dirname "$0")/../.."
g++ -O3 -std=c++17 -Wall -Wextra routers/priced_cpp/optimize.cpp -o routers/priced_cpp/build/optimizer_fo || exit 1
mkdir -p lpbound/fo
run() { tag=$1; c=$2; shift 2; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/tier/hard_v1 \
  --out-dir lpbound/fo/$tag --cases $c --seconds 1800 --iterations 1000000000 --neighbor-polish --max-group 12 --binary routers/priced_cpp/build/optimizer_fo \
  --negotiate 30 20 0.5 1.3 0.5 --cycle 20000 --seed 11 --temperature 10 "$@" > lpbound/fo/${tag}_$c.log 2>&1; }
for c in case_01 case_07; do
  run fo50 $c --fanout-order 50 &
  run fo100 $c --fanout-order 100 &
done
wait
for f in lpbound/fo/*.log; do echo "$f $(grep -h 'FINISHED\|Error\|error' $f | cut -c1-100)"; done
