#!/bin/bash
# portfolio tier run, two cases at a time: <tier> <workers> <explore_s> <native_s> <out_dir> [seed] [parallel_cases]
cd "$(dirname "$0")/../.."
tier=$1; w=$2; ex=$3; ns=$4; out=$5; seed=${6:-1}; par=${7:-2}
suite=benchmarks_$tier; [ "$tier" = intro ] && suite=benchmarks
mkdir -p "$out"
python3 -c "import json;print('\n'.join(c['name'] for c in json.load(open('$suite/suite.json'))['cases']))" | \
  xargs -P $par -I{} bash -c "PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite $suite --out-dir $out --cases {} \
    --workers $w --explore-seconds $ex --native-seconds $ns --mix-fanout --seed $seed > $out/{}.log 2>&1; grep -h FINISHED $out/{}.log"
PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite $suite --out-dir $out --merge
