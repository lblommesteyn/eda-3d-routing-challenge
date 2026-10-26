#!/bin/bash
# cooling schedule at 30 min: cycle 20000 resets vs one time-based anneal (cycle 0), same starts, 2 seeds
cd "$(dirname "$0")/../.."
g++ -O3 -std=c++17 -Wall -Wextra routers/priced_cpp/optimize.cpp -o routers/priced_cpp/build/optimizer_sched || exit 1
mkdir -p lpbound/sched
run() { tag=$1; c=$2; s=$3; shift 3; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_neg/hard \
  --out-dir lpbound/sched/${tag}_s$s --cases $c --seconds 1800 --iterations 1000000000 --neighbor-polish --max-group 12 --binary routers/priced_cpp/build/optimizer_sched \
  --negotiate 30 20 0.5 1.3 0.5 --seed $s "$@" > lpbound/sched/${tag}_s${s}_$c.log 2>&1; }
for c in case_01 case_05; do for s in 1 2; do
  run c20k_t10 $c $s --cycle 20000 --temperature 10 &
  run lin_t10 $c $s --cycle 0 --temperature 10 &
  run lin_t20 $c $s --cycle 0 --temperature 20 &
  run lin_t40 $c $s --cycle 0 --temperature 40 &
done; done
wait
for f in lpbound/sched/*.log; do echo "$f $(grep -h 'FINISHED\|Error\|error' $f | sed 's/negotiated moves.*iterations/iterations/' | cut -c1-120)"; done
