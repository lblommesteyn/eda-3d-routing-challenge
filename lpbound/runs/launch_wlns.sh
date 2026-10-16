#!/bin/bash
# one dual-guided window-LNS worker per case, from the pool best (lpbound/runs/pool_<tier>.json)
# or from this directory's own improved file when it exists
secs=${1:-2700}; box=${2:-8}; mip=${3:-20}; only=${4:-}
cd "$(dirname "$0")/../.."
mkdir -p lpbound/win
alpha_of() {
  case $1 in
    hard) echo lpbound/runs/hard3/$2.alpha.npz ;;
    congested) [ -f lpbound/runs/big2/congested_$2.alpha.npz ] && echo lpbound/runs/big2/congested_$2.alpha.npz || echo lpbound/runs/big/congested_$2.alpha.npz ;;
    designs) echo lpbound/runs/big/designs_$2.alpha.npz ;;
  esac
}
for tier in hard congested designs; do
  while read c inst start; do
    [ -n "$only" ] && [[ " $only " != *" ${tier}_$c "* ]] && continue
    cur=lpbound/win/${tier}_$c.sol.json
    [ -f "$cur" ] && start=$cur
    python -m lpbound.wlns "$inst" "$start" "$cur" $secs $box $mip $RANDOM "$(alpha_of $tier $c)" >> lpbound/win/${tier}_$c.log 2>&1 &
    sleep 3
  done < <(python -c "import json;d=json.load(open('lpbound/runs/pool_$tier.json'));[print(k,v['instance'],v['best_file']) for k,v in d.items()]" | tr -d '\r')
done
wait
