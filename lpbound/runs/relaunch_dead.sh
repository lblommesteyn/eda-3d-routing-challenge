#!/bin/bash
# relaunch the window-LNS workers whose log ends in a traceback
secs=${1:-2700}; box=${2:-8}; mip=${3:-20}
cd "$(dirname "$0")/../.."
for tier in hard congested designs; do
  while read c inst start; do
    log=lpbound/win/${tier}_$c.log
    tail -1 "$log" | grep -q Error || continue
    cur=lpbound/win/${tier}_$c.sol.json
    [ -f "$cur" ] && start=$cur
    python -m lpbound.wlns "$inst" "$start" "$cur" $secs $box $mip $RANDOM >> "$log" 2>&1 &
    sleep 5
  done < <(python -c "import json;d=json.load(open('lpbound/runs/pool_$tier.json'));[print(k,v['instance'],v['best_file']) for k,v in d.items()]" | tr -d '\r')
done
wait
