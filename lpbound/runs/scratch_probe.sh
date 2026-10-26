#!/bin/bash
# Saksham's from-scratch pipeline on a few hard cases with a longer budget
cd "$(dirname "$0")/../.."
mkdir -p lpbound/native/scratch20
for c in case_01 case_05 case_07 case_09; do
  PYTHONPATH=. python3 routers/priced_cpp/pipeline.py --suite benchmarks_hard --out-dir lpbound/native/scratch20/$c --cases $c \
    --routing-seconds 120 --refinement-seconds 120 --native-seconds 900 --native-iterations 100000000 --rounds 2 --seed 0 \
    > lpbound/native/scratch20_$c.log 2>&1 &
done
wait
