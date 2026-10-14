"""Tier ceilings from verified bounds: geomean(baseline / bound) vs best public.

For each case, takes the best certified integer bound among the listed verify
files (each produced by verify.py, independent of the solver).

    python -m lpbound.summarize
"""
import glob
import json
import math
import os

RUNS = 'lpbound/runs'
SOURCES = {'hard': ['verify_{c}.json', 'hard3/verify_{c}.json'],
           'congested': ['big/verify_congested_{c}.json', 'big2/verify_congested_{c}.json'],
           'designs': ['big/verify_designs_{c}.json', 'big2/verify_designs_{c}.json']}


def gm(xs):
    return math.exp(sum(map(math.log, xs)) / len(xs))


def main():
    for tier, pats in SOURCES.items():
        best = json.load(open(f'{RUNS}/best_{tier}.json'))
        rows, ceil, cur = [], [], []
        for c, r in best.items():
            bs = [json.load(open(f))['certified_int_bound'] for p in pats
                  for f in [os.path.join(RUNS, p.format(c=c))] if os.path.exists(f)]
            lb = max(bs)
            rows.append(f"| {c} | {r['baseline']} | {r['best']} | {lb} | {(r['best'] - lb) / r['best']:.1%} |")
            ceil.append(r['baseline'] / lb); cur.append(r['baseline'] / r['best'])
        print(f'\n{tier}: ceiling {gm(ceil):.4f}, best per case today {gm(cur):.4f}')
        print('| case | baseline | best public | certified bound | max gain |\n|---|---:|---:|---:|---:|')
        print('\n'.join(rows))


if __name__ == '__main__':
    main()
