#!/bin/bash
# start quality of native exact negotiation over present x growth x history, case_07, 3 seeds
cd "$(dirname "$0")/../.."
mkdir -p lpbound/scratch_sweep
for p in 0.1 0.3 1; do for gr in 1.02 1.05 1.1; do for h in 0.2 0.5 1; do echo "$p $gr $h"; done; done; done | \
  xargs -P 14 -L 1 bash -c 'python3 lpbound/scratch_probe.py benchmarks_hard/case_07.json 3000 $0 $1 $2 1 2 3 > lpbound/scratch_sweep/c07_$0_$1_$2.txt 2>&1'
cat lpbound/scratch_sweep/*.txt
