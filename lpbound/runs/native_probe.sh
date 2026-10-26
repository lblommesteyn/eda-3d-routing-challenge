#!/bin/bash
# his native optimizer as a refiner of the pool best: <tier> <case> <secs> <temp> <tag>
cd "$(dirname "$0")/../.."
PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_$1 --start-dir lpbound/start/$1 \
  --out-dir lpbound/native/$5/$1 --cases $2 --seconds $3 --iterations 100000000 --temperature $4 --seed 7 > lpbound/native/$5_$1_$2.log 2>&1
