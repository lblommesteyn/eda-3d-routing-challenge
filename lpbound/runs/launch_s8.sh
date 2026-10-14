for i in 1 2 3 4 5; do
  c=lpbound/calib/small/s8n7_$i.json
  python -m lpbound.calib_small solve $c 1200 2 > ${c%.json}.log 2>&1
  python -m lpbound.gapsplit $c 600 2 > ${c%.json}.gapsplit.log 2>&1
done
python -m lpbound.gapsplit lpbound/calib/small/s10n11_0.json 900 2 > lpbound/calib/small/s10n11_0.gapsplit.log 2>&1
