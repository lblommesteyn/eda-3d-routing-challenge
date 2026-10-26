#!/bin/bash
# negotiation schedules, routing stage only: <case>
cd "$(dirname "$0")/../.."
c=$1; mkdir -p lpbound/neg
run() { tag=$1; shift; PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/neg/$tag --cases $c \
  --routing-seconds 600 --refinement-seconds 1 --rounds 1 --native-seconds 0 --max-iters 3000 --seed 0 "$@" > lpbound/neg/${tag}_$c.log 2>&1; }
run p64 --present 64 --history 4 --growth 1 &
run p1g105 --present 1 --history 0.5 --growth 1.05 &
run p05g103 --present 0.5 --history 0.3 --growth 1.03 &
run p2g11 --present 2 --history 1 --growth 1.1 &
wait
for f in lpbound/neg/*_$c.log; do echo "$f $(grep -h 'negotiation\|independent' $f | tail -n 1) | $(grep FINISHED $f)"; done
