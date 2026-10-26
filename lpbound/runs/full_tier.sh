#!/bin/bash
# one submit_run.py process per case of a tier, in parallel: <tier> <native_seconds> <out_dir> [seed]
cd "$(dirname "$0")/../.."
tier=$1; ns=$2; out=$3; seed=${4:-1}
suite=benchmarks_$tier; [ "$tier" = intro ] && suite=benchmarks
mkdir -p "$out"
for c in $(python3 -c "import json;print(' '.join(c['name'] for c in json.load(open('$suite/suite.json'))['cases']))"); do
  PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite $suite --out-dir "$out" --cases $c \
    --native-seconds $ns --seed $seed > "$out/$c.log" 2>&1 &
done; wait
PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/submit_run.py --suite $suite --out-dir "$out" --merge
grep -h FINISHED "$out"/*.log
