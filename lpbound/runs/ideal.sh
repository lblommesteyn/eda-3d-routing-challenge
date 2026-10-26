#!/bin/bash
# ideal-tree moves, 30 min from hard_v1; compare island s11 (8718, 21287)
cd "$(dirname "$0")/../.."
g++ -O3 -std=c++17 -Wall -Wextra routers/priced_cpp/optimize.cpp -o routers/priced_cpp/build/optimizer_ideal || exit 1
mkdir -p lpbound/ideal
run() { tag=$1; c=$2; shift 2; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/tier/hard_v1 \
  --out-dir lpbound/ideal/$tag --cases $c --seconds 1800 --iterations 1000000000 --neighbor-polish --max-group 12 --binary routers/priced_cpp/build/optimizer_ideal \
  --negotiate 30 20 0.5 1.3 0.5 --cycle 20000 --seed 11 --temperature 10 "$@" > lpbound/ideal/${tag}_$c.log 2>&1; }
for c in case_01 case_07; do
  run i10c30 $c --ideal 10 30 &
  run i30c30 $c --ideal 30 30 &
  run i10c60 $c --ideal 10 60 &
done
wait
for f in lpbound/ideal/*.log; do echo "$f $(grep -h 'FINISHED\|Error\|error' $f | cut -c1-160)"; done
