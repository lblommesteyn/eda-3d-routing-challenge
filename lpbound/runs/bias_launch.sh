#!/bin/bash
# launch fanout-bias test runs detached: bias_launch.sh <case> <native_s> <seed,...> <bias> [<bias> ...]
cd "$(dirname "$0")/../.."
c=$1; secs=$2; seeds=$3; shift 3
for b in "$@"; do for s in ${seeds//,/ }; do
  setsid nohup bash lpbound/runs/bias.sh $c $b $s $secs >/dev/null 2>&1 < /dev/null &
done; done
sleep 15
