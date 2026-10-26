#!/bin/bash
# neighbor-polish variants on our independent routes: <case> <secs>
cd "$(dirname "$0")/../.."
c=$1; s=$2; mkdir -p lpbound/nv2
run() { tag=$1; shift; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_indep/hard \
  --out-dir lpbound/nv2/$tag --cases $c --seconds $s --iterations 100000000 --seed 3 --neighbor-polish "$@" > lpbound/nv2/${tag}_$c.log 2>&1; }
run t20 --temperature 20 &
run t60 --temperature 60 &
run t5 --temperature 5 &
run t20g12 --temperature 20 --max-group 12 &
run t20g40 --temperature 20 --max-group 40 &
wait
for f in lpbound/nv2/*_$c.log; do echo "$f $(grep FINISHED $f)"; done
