#!/bin/bash
# A/B: negotiated group repair vs plain moves: <case> <secs> <start_dir>
cd "$(dirname "$0")/../.."
c=$1; s=$2; st=$3; mkdir -p lpbound/ab
run() { tag=$1; shift; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir $st \
  --out-dir lpbound/ab/$tag --cases $c --seconds $s --iterations 100000000 --neighbor-polish --temperature 10 "$@" > lpbound/ab/${tag}_$c.log 2>&1; }
run base_s3 --max-group 12 --seed 3 &
run base_s4 --max-group 12 --seed 4 &
run n30_s3 --max-group 12 --seed 3 --negotiate 30 20 0.5 1.3 0.5 &
run n30_s4 --max-group 12 --seed 4 --negotiate 30 20 0.5 1.3 0.5 &
run n30g24 --max-group 24 --seed 3 --negotiate 30 20 0.5 1.3 0.5 &
run n60 --max-group 12 --seed 3 --negotiate 60 20 0.5 1.3 0.5 &
run n30hot --max-group 12 --seed 3 --negotiate 30 20 1 1.5 1 &
run n30g40 --max-group 40 --seed 3 --negotiate 30 30 0.5 1.3 0.5 &
wait
for f in lpbound/ab/*_$c.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -c1-200)"; done
