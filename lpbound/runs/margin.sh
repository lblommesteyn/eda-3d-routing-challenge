#!/bin/bash
# search margin vs none on congested case_01, 10 min each from the 53985 start
cd "$(dirname "$0")/../.."
mkdir -p lpbound/margin
run() { tag=$1; shift; PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_congested --start-dir lpbound/start_cong \
  --out-dir lpbound/margin/$tag --cases case_01 --seconds 600 --iterations 1000000000 --neighbor-polish --temperature 10 --max-group 12 \
  --seed 9 --negotiate 30 20 0.5 1.3 0.5 --cycle 20000 --binary routers/priced_cpp/build/optimizer_fast "$@" > lpbound/margin/$tag.log 2>&1; }
run none &
run m4 --margin 4 &
run m8 --margin 8 &
run m16 --margin 16 &
wait
for f in lpbound/margin/*.log; do echo "$f $(grep -h 'FINISHED\|Error' $f | cut -c1-160)"; done
