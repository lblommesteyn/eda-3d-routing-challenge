#!/bin/bash
# one timed end-to-end refine_run per case (lpbound/refine_run.py), N at a time
budget=${1:-5400}; par=${2:-11}
cd "$(dirname "$0")/../.."
mkdir -p lpbound/final
for tier in hard congested designs; do
  python -c "import json;d=json.load(open('lpbound/runs/pool_$tier.json'));[print('$tier',k,v['instance'],v['best_file']) for k,v in d.items()]" | tr -d '\r'
done | xargs -P "$par" -L 1 bash -c 'd=600; [ "$0" != hard ] && d=1200; python -m lpbound.refine_run "$2" "$3" "lpbound/final/$0_$1" '"$budget"' $d 1 > "lpbound/final/$0_$1.log" 2>&1'
