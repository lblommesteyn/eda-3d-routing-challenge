#!/bin/bash
# best-of-N campaign, slow schedule only: one time-based anneal at T 20 over <native_s> seconds.
# <tier> <first_seed> <last_seed> <native_s> [parallel]; output lpbound/campaign/<tier>_t20_<native_s>
cd "$(dirname "$0")/../.."
tier=$1; s0=$2; s1=$3; secs=$4; par=${5:-16}
suite=benchmarks_$tier
out=lpbound/campaign/${tier}_t20_$secs
mkdir -p $out
cases=${CASES:-$(python3 -c "import json;print(' '.join(c['name'] for c in json.load(open('$suite/suite.json'))['cases']))")}
for s in $(seq $s0 $s1); do for c in $cases; do echo "$c $s"; done; done | \
  xargs -P $par -L 1 bash -c '
    c=$0; s=$1; d='$out'/$c/s$s
    [ -f $d/$c.run.json ] && exit 0
    mkdir -p $d
    PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite '$suite' --out-dir $d --cases $c --seed $s \
      --native-seconds '$secs' --cycle 0 --temperature 20 --binary routers/priced_cpp/build/optimizer_sched > $d/run.log 2>&1
    grep -h FINISHED $d/run.log | sed "s|^|s$s |"'
