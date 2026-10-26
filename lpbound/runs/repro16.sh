#!/bin/bash
# try to reproduce the g16 verification failure with several seeds
cd "$(dirname "$0")/../.."
mkdir -p lpbound/repro
for s in 3 5 6 7 8 9 10 11; do
  PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_neg/hard \
    --out-dir lpbound/repro/s$s --cases case_01 --seconds 600 --iterations 100000000 --neighbor-polish --temperature 20 --max-group 16 --seed $s \
    > lpbound/repro/s$s.log 2>&1 &
done; wait
for s in 3 5 6 7 8 9 10 11; do echo "s$s $(grep -h 'FINISHED\|RuntimeError' lpbound/repro/s$s.log | cut -c1-400)"; done
