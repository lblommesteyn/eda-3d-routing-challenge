#!/bin/bash
# old vs stamped binary: fixed iteration count, same seed, must match exactly
cd "$(dirname "$0")/../.."
mkdir -p lpbound/equiv
for b in optimizer optimizer_fast; do
  /usr/bin/time -f "$b %e s" env PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_hard --start-dir lpbound/start_neg/hard \
    --out-dir lpbound/equiv/$b --cases case_01 --seconds 100000 --iterations 20000 --neighbor-polish --temperature 10 --max-group 12 \
    --seed 9 --negotiate 30 20 0.5 1.3 0.5 --cycle 5000 --binary routers/priced_cpp/build/$b 2>&1 | grep -E "FINISHED| s$"
done
cmp lpbound/equiv/optimizer/case_01.sol.json lpbound/equiv/optimizer_fast/case_01.sol.json && echo IDENTICAL
