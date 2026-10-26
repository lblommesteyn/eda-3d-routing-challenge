#!/bin/bash
# profile 300 optimizer iterations on the captured congested input
cd "$(dirname "$0")/../.."
IN=$PWD/lpbound/prof/input.txt
ls -la $IN
cd /tmp && /tmp/optimizer_pg < $IN > /dev/null 2>&1
gprof -b -p /tmp/optimizer_pg /tmp/gmon.out | head -25
