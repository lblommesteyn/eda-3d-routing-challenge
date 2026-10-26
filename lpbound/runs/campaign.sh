#!/bin/bash
# best-of-N campaign: independent single-thread 2 h runs, round-robin over cases.
# even seeds: cycle 20000 T 10; odd seeds: one time-based anneal T 20. <tier> <first_seed> <last_seed> [parallel]
cd "$(dirname "$0")/../.."
tier=$1; s0=$2; s1=$3; par=${4:-16}
suite=benchmarks_$tier
out=lpbound/campaign/$tier
mkdir -p $out
cases=$(python3 -c "import json;print(' '.join(c['name'] for c in json.load(open('$suite/suite.json'))['cases']))")
for s in $(seq $s0 $s1); do for c in $cases; do echo "$c $s"; done; done | \
  xargs -P $par -L 1 bash -c '
    c=$0; s=$1; d='$out'/$c/s$s
    [ -f $d/$c.run.json ] && exit 0
    mkdir -p $d
    if [ $((s % 2)) -eq 0 ]; then sched="--cycle 20000 --temperature 10"; else sched="--cycle 0 --temperature 20"; fi
    PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite '$suite' --out-dir $d --cases $c --seed $s \
      --native-seconds 7200 $sched --binary routers/priced_cpp/build/optimizer_sched > $d/run.log 2>&1
    grep -h FINISHED $d/run.log | sed "s|^|s$s |"'
