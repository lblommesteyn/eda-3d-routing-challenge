#!/bin/bash
# capture the optimizer input for congested case_01, then profile 300 iterations with gprof
cd "$(dirname "$0")/../.."
P=lpbound/prof; mkdir -p $P
cat > $P/tee_bin.sh <<'EOT'
#!/bin/bash
tee /tmp/m3d_opt_input.txt | "$(dirname "$0")/../../routers/priced_cpp/build/optimizer_fast"
EOT
chmod +x $P/tee_bin.sh
PYTHONPATH=.:routers/priced_cpp python3 routers/priced_cpp/native.py --suite benchmarks_congested --start-dir lpbound/start_cong \
  --out-dir $P/out --cases case_01 --seconds 100000 --iterations 300 --neighbor-polish --temperature 10 --max-group 12 \
  --seed 9 --negotiate 30 20 0.5 1.3 0.5 --cycle 20000 --binary $P/tee_bin.sh > /dev/null 2>&1
cp /tmp/m3d_opt_input.txt $P/input.txt
cd routers/priced_cpp && g++ -O2 -pg -std=c++17 optimize.cpp -o /tmp/optimizer_pg && cd ../..
cd /tmp && /tmp/optimizer_pg < /root/../$PWD/$P/input.txt > /dev/null 2>&1; cd - > /dev/null
gprof -b -p /tmp/optimizer_pg /tmp/gmon.out | head -25
