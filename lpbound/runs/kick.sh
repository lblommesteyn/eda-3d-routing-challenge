#!/bin/bash
# region kicks at every cycle restart, 30 min from hard_v1; compare plain continuation 8714-8718 / 21267-21287
cd "$(dirname "$0")/../.."
g++ -O3 -std=c++17 -Wall -Wextra routers/priced_cpp/optimize.cpp -o routers/priced_cpp/build/optimizer_kick || exit 1
mkdir -p lpbound/kick
run() { tag=$1; c=$2; shift 2; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/tier/hard_v1 \
  --out-dir lpbound/kick/$tag --cases $c --seconds 1800 --iterations 1000000000 --neighbor-polish --max-group 12 --binary routers/priced_cpp/build/optimizer_kick \
  --negotiate 30 20 0.5 1.3 0.5 --seed 11 --temperature 10 "$@" > lpbound/kick/${tag}_$c.log 2>&1; }
for c in case_01 case_07; do
  run k20 $c --cycle 20000 --kick 20 50 0.3 1.15 &
  run k40 $c --cycle 20000 --kick 40 50 0.3 1.15 &
  run k80 $c --cycle 20000 --kick 80 60 0.3 1.15 &
  run k40c5 $c --cycle 5000 --kick 40 50 0.3 1.15 &
  run k20c5 $c --cycle 5000 --kick 20 50 0.3 1.15 &
  run k40c50 $c --cycle 50000 --kick 40 50 0.3 1.15 &
done
wait
for f in lpbound/kick/*.log; do echo "$f $(grep -h 'FINISHED\|Error\|error' $f | cut -c1-200)"; done
