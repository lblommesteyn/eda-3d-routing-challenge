#!/bin/bash
# graft.sh <case> <seconds> [log]: recombine all our runs of <case> into the best one
cd "$(dirname "$0")/../.."
c=$1; secs=$2
best=$(for f in lpbound/campaign/*/$c/*/run.log; do v=$(grep -o 'final=[0-9]*' $f | cut -d= -f2); [ -n "$v" ] && echo "$v $(dirname $f)"; done | sort -n | head -1 | cut -d' ' -f2)
inc=lpbound/graft/$c.sol.json
mkdir -p lpbound/graft
[ -f $inc ] || cp $best/$c.sol.json $inc
donors=$(ls lpbound/campaign/*/$c/*/$c.sol.json | grep -v "^$best/")
PYTHONPATH=. python3 -m lpbound.graft benchmarks_hard/$c.json $inc $inc $secs $donors
