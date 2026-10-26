#!/bin/bash
# his native optimizer move variants on our independent routes: <case> <secs>
cd "$(dirname "$0")/../.."
c=$1; s=$2; mkdir -p lpbound/nv
run() { tag=$1; shift; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_indep/hard \
  --out-dir lpbound/nv/$tag --cases $c --seconds $s --iterations 100000000 --seed 3 "$@" > lpbound/nv/${tag}_$c.log 2>&1; }
run warm20 --temperature 20 &
run nowarm20 --temperature 20 --no-warm &
run nowarm60 --temperature 60 --no-warm &
run warm20np --temperature 20 --neighbor-polish &
run nowarm20g48 --temperature 20 --no-warm --max-group 48 &
wait
for f in lpbound/nv/*_$c.log; do echo "$f $(grep FINISHED $f)"; done
