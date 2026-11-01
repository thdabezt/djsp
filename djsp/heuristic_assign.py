from __future__ import annotations

import random
import time
from typing import Optional

from .model import Instance

FRESH = 0.1
SWAP = 0.5
DELTA = 0.5
FAILS = 3


def _assign(n, m, F, ops, gen):
    order = list(range(n))
    gen.shuffle(order)
    load = [[0] * m for _ in range(F)]
    fac = [0] * n
    for j in order:
        keys = [(max(load[f][mach] + d for mach, d in ops[j]), sum(load[f])) for f in range(F)]
        fac[j] = f = gen.choice([f for f in range(F) if keys[f] == min(keys)])
        for mach, d in ops[j]:
            load[f][mach] += d
    return fac


def _schedule(jobs, ops, tails, m, gen):
    delta = DELTA * gen.random()
    route, tail = [ops[j] for j in jobs], [tails[j] for j in jobs]
    nxt, ready, mready = [0] * len(jobs), [0] * len(jobs), [0] * m
    starts = [[0] * m for _ in jobs]
    active = list(range(len(jobs)))
    while active:
        cstar = None
        for i in active:
            mach, d = route[i][nxt[i]]
            end = (ready[i] if ready[i] > mready[mach] else mready[mach]) + d
            if cstar is None or end < cstar:
                cstar, mstar = end, mach
        cand = [(max(ready[i], mready[mstar]), i) for i in active if route[i][nxt[i]][0] == mstar]
        smin = min(cand)[0]
        cand = [c for c in cand if c[0] <= smin + delta * (cstar - smin)]
        s, i = max(cand, key=lambda c: tail[c[1]][nxt[c[1]]])
        starts[i][nxt[i]] = s
        ready[i] = mready[mstar] = s + route[i][nxt[i]][1]
        nxt[i] += 1
        if nxt[i] == m:
            active.remove(i)
    return max(ready, default=0), dict(zip(jobs, starts))


def find_upper_bound_assign(instance: Instance, time_limit: float, seed: int, iterations: Optional[int] = None):
    gen = random.Random(seed)
    n, m, F = instance.n_jobs, instance.n_machines, instance.n_factories
    ops = [[(o.machine, o.duration) for o in job] for job in instance.jobs]
    tails = [[sum(d for _, d in job[k + 1:]) for k in range(m)] for job in ops]
    deadline = time.perf_counter() + max(0.0, time_limit)
    best, done = None, 0
    while done == 0 or (done < iterations if iterations is not None else time.perf_counter() < deadline):
        done += 1
        if best is None or F == 1 or gen.random() < FRESH:
            fac, cms, sts = _assign(n, m, F, ops, gen), [float("inf")] * F, [{} for _ in range(F)]
        else:
            fac, cms, sts = list(best[0]), list(best[1]), list(best[2])
            crit = cms.index(max(cms))
            j = gen.choice([j for j in range(n) if fac[j] == crit])
            fac[j] = other = gen.choice([f for f in range(F) if f != crit])
            mates = [k for k in range(n) if fac[k] == other and k != j]
            if mates and gen.random() < SWAP:
                fac[gen.choice(mates)] = crit
            cms[crit] = cms[other] = float("inf")
        groups = [[j for j in range(n) if fac[j] == f] for f in range(F)]
        fails = 0
        while fails < FAILS:
            f = cms.index(max(cms))
            c, st = _schedule(groups[f], ops, tails, m, gen)
            fails = 0 if c < cms[f] else fails + 1
            if c < cms[f]:
                cms[f], sts[f] = c, st
        if best is None or max(cms) <= max(best[1]):
            best = (fac, cms, sts)
    starts = {j: s for st in best[2] for j, s in st.items()}
    return max(best[1]), [starts[j] for j in range(n)], best[0], {"iterations": done}
