#!/bin/bash
# tuned native polish from the gentle-negotiation start: <case> <secs>
cd "$(dirname "$0")/../.."
c=$1; s=$2; mkdir -p lpbound/nv3
run() { tag=$1; shift; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_neg/hard \
  --out-dir lpbound/nv3/$tag --cases $c --seconds $s --iterations 100000000 --neighbor-polish --temperature 20 "$@" > lpbound/nv3/${tag}_$c.log 2>&1; }
run g12s3 --max-group 12 --seed 3 &
run g12s4 --max-group 12 --seed 4 &
run g8 --max-group 8 --seed 3 &
run g16 --max-group 16 --seed 3 &
run g12t10 --max-group 12 --seed 3 --temperature 10 &
wait
for f in lpbound/nv3/*_$c.log; do echo "$f $(grep FINISHED $f)"; done
