#!/bin/bash
# low present-cost sweep x seeds, routing + 1 s refinement: <case>
cd "$(dirname "$0")/../.."
c=$1; mkdir -p lpbound/neg3
run() { tag=$1; shift; PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/neg3/$tag --cases $c \
  --routing-seconds 900 --refinement-seconds 1 --rounds 1 --native-seconds 0 --max-iters 5000 "$@" > lpbound/neg3/${tag}_$c.log 2>&1; }
for p in 0.1 0.2 0.3; do for h in 0.5 1; do for s in 0 1; do
  run p${p}h${h}s$s --present $p --history $h --growth 1.05 --seed $s &
done; done; wait; done
for f in lpbound/neg3/*_$c.log; do echo "$f $(grep -h 'negotiation' $f | tail -n 1 | cut -d: -f2-) | $(grep FINISHED $f | cut -d: -f2)"; done
