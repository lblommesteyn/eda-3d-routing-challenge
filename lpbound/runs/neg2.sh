#!/bin/bash
# negotiation schedule sweep, routing + 1 s refinement: <case>
cd "$(dirname "$0")/../.."
c=$1; mkdir -p lpbound/neg2
run() { tag=$1; shift; PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/neg2/$tag --cases $c \
  --routing-seconds 900 --refinement-seconds 1 --rounds 1 --native-seconds 0 --max-iters 5000 "$@" > lpbound/neg2/${tag}_$c.log 2>&1; }
run p1g105s1 --present 1 --history 0.5 --growth 1.05 --seed 1 &
run p1g105s2 --present 1 --history 0.5 --growth 1.05 --seed 2 &
run p1g103 --present 1 --history 0.5 --growth 1.03 --seed 0 &
run p1g105h1 --present 1 --history 1 --growth 1.05 --seed 0 &
run p1g105h02 --present 1 --history 0.2 --growth 1.05 --seed 0 &
run p03g105 --present 0.3 --history 0.5 --growth 1.05 --seed 0 &
run p1g102h02 --present 1 --history 0.2 --growth 1.02 --seed 0 &
run p1g105nx --present 1 --history 0.5 --growth 1.05 --seed 0 --no-exact-prices &
wait
for f in lpbound/neg2/*_$c.log; do echo "$f $(grep -h 'negotiation' $f | tail -n 1 | cut -d: -f2-) | $(grep FINISHED $f | cut -d: -f2)"; done
