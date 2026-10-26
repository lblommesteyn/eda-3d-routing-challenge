#!/bin/bash
# bias.sh <case> <bias> <seed> <native_s>: one slow-anneal run with fanout-biased acceptance
cd "$(dirname "$0")/../.."
c=$1; b=$2; s=$3; secs=$4
d=lpbound/bias/${c}_g${b}_s$s; mkdir -p $d
PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite benchmarks_hard --out-dir $d --cases $c --seed $s \
  --native-seconds $secs --cycle 0 --temperature 20 --fanout-bias $b --binary routers/priced_cpp/build/optimizer_bias2 > $d/run.log 2>&1
