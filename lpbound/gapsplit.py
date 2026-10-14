"""Which nets' integrality makes the LP-to-integer gap? (small instances)

Solves the MILP with y binary only for a subset of nets (every other y
continuous). Each variant is still a relaxation of the true problem, so its
value is a valid lower bound between the LP and the optimum.

    python -m lpbound.gapsplit <case.json> <seconds> <threads>
"""
import json
import sys

from lpbound.cg import Instance
from lpbound.exact import solve_highspy


def main(case, seconds, threads):
    inst = Instance(case)
    multi = {k for k, (_, _, ss) in enumerate(inst.nets) if len(ss) > 1}
    two = set(range(len(inst.nets))) - multi
    ref = case.replace('.json', '.ref.sol.json')
    out = dict(case=case, n_multi=len(multi), n_two=len(two))
    for name, nets in (('multi_only', multi), ('twopin_only', two)):
        r = solve_highspy(case, seconds, threads=threads, start_sol=ref, int_nets=nets, quiet=True)
        out[name] = dict(value=r['bound'], status=r['status'], incumbent=r['ip'])
        print(name, out[name], flush=True)
    json.dump(out, open(case.replace('.json', '.gapsplit.json'), 'w'), indent=1)
    print(json.dumps(out))


if __name__ == '__main__':
    main(sys.argv[1], float(sys.argv[2]), int(sys.argv[3]))
