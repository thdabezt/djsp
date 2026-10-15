from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional, Tuple

from .model import Instance

INF = float("inf")
TENURE, RESTART = 10, 60
BIAS_LEVELS = 9


class _Problem:
    def __init__(self, instance: Instance) -> None:
        self.n, self.m, self.F = instance.n_jobs, instance.n_machines, instance.n_factories
        self.ops = [[(operation.machine, operation.duration) for operation in job] for job in instance.jobs]
        self.rev = [ops[::-1] for ops in self.ops]
        self.tot = [sum(d for _, d in ops) for ops in self.ops]
        self.longest = max(self.tot)
        self.lb = instance.lower_bound
        self.cum = [self._cumulative(level) for level in range(BIAS_LEVELS)]

    def _cumulative(self, level: int) -> List[int]:
        out, total = [], 0
        for r in range(self.n):
            root, value = math.sqrt(r + 1), 1.0
            for _ in range(level):
                value /= root
            total += int(round(1_000_000 * value))
            out.append(total)
        return out


def _gt(P, jobs, ops, gen=None, delta=1.0, cum=None, bound=INF, pick=None):
    m = P.m
    prog = dict.fromkeys(jobs, 0)
    rem = {j: P.tot[j] for j in jobs}
    jr = dict.fromkeys(jobs, 0)
    mr = [0] * m
    starts = {j: [0] * m for j in jobs}
    prune = bound < INF
    if prune:
        mload = [0] * m
        for j in jobs:
            for machine, d in ops[j]:
                mload[machine] += d
        if max(mload) >= bound or max(rem.values()) >= bound:
            return None
    active = list(jobs)
    cmax = 0
    while active:
        cstar = mstar = None
        for j in active:
            machine, d = ops[j][prog[j]]
            e = (jr[j] if jr[j] > mr[machine] else mr[machine]) + d
            if cstar is None or e < cstar:
                cstar, mstar = e, machine
        ready = mr[mstar]
        cand = []
        sstar = cstar
        for j in active:
            if ops[j][prog[j]][0] == mstar:
                s = jr[j] if jr[j] > ready else ready
                if s < cstar:
                    cand.append((j, s))
                    if s < sstar:
                        sstar = s
        if delta < 1.0 and len(cand) > 1:
            threshold = sstar + delta * (cstar - sstar)
            cand = [c for c in cand if c[1] <= threshold]
        if len(cand) == 1:
            j, s = cand[0]
        elif pick is not None:
            j, s = pick(cand, prog, rem, ops)
        elif cum is not None:
            keyed = []
            for c, sc in cand:
                cc = sc + ops[c][prog[c]][1]
                lb = sc + rem[c]
                for k, _ in cand:
                    if k != c:
                        v = (jr[k] if jr[k] > cc else cc) + rem[k]
                        if v > lb:
                            lb = v
                keyed.append((lb, c, sc))
            keyed.sort()
            _, j, s = gen.choices(keyed, cum_weights=cum[:len(keyed)])[0]
        else:
            j, s = cand[gen.randrange(len(cand))]
        d = ops[j][prog[j]][1]
        starts[j][prog[j]] = s
        e = s + d
        jr[j] = mr[mstar] = e
        rem[j] -= d
        if prune:
            mload[mstar] -= d
            if e + rem[j] >= bound or e + mload[mstar] >= bound:
                return None
        prog[j] += 1
        if prog[j] == m:
            active.remove(j)
            if e > cmax:
                cmax = e
    return cmax, starts


def _nondelay(P, jobs, ops, pick):
    m = P.m
    prog = dict.fromkeys(jobs, 0)
    rem = {j: P.tot[j] for j in jobs}
    jr = dict.fromkeys(jobs, 0)
    mr = [0] * m
    starts = {j: [0] * m for j in jobs}
    active = list(jobs)
    cmax = 0
    while active:
        est, t = {}, None
        for j in active:
            machine = ops[j][prog[j]][0]
            s = jr[j] if jr[j] > mr[machine] else mr[machine]
            est[j] = s
            if t is None or s < t:
                t = s
        cand = [(j, t) for j in active if est[j] == t]
        j, s = cand[0] if len(cand) == 1 else pick(cand, prog, rem, ops)
        machine, d = ops[j][prog[j]]
        starts[j][prog[j]] = s
        e = s + d
        jr[j] = mr[machine] = e
        rem[j] -= d
        prog[j] += 1
        if prog[j] == m:
            active.remove(j)
            if e > cmax:
                cmax = e
    return cmax, starts


def _first_max(key):
    def pick(cand, prog, rem, ops):
        best = None
        for c in cand:
            v = key(c[0], prog, rem, ops)
            if best is None or v > best[0]:
                best = (v, c)
        return best[1]
    return pick


RULES = {
    "mwkr": _first_max(lambda j, p, r, o: r[j]),
    "tail_excl": _first_max(lambda j, p, r, o: r[j] - o[j][p[j]][1]),
    "mopnr": _first_max(lambda j, p, r, o: -p[j]),
}


def _mirror(P, jobs, st_rev, c_rev):
    m = P.m
    return {j: [c_rev - (st_rev[j][m - 1 - k] + P.ops[j][k][1]) for k in range(m)] for j in jobs}


def _assign_lpt(P):
    load = [0] * P.F
    fac = [0] * P.n
    for j in sorted(range(P.n), key=lambda j: (-P.tot[j], j)):
        f = min(range(P.F), key=lambda f: (load[f], f))
        fac[j] = f
        load[f] += P.tot[j]
    return fac


def _assign_load(P, gen=None, order=None):
    if order is None:
        order = list(range(P.n))
        gen.shuffle(order)
    load_by_machine = [[0] * P.m for _ in range(P.F)]
    peak = [0] * P.F
    load = [0] * P.F
    fac = [0] * P.n
    for j in order:
        keys = []
        for f in range(P.F):
            top = peak[f]
            for machine, d in P.ops[j]:
                v = load_by_machine[f][machine] + d
                if v > top:
                    top = v
            keys.append((max(top, P.longest), load[f]))
        low = min(keys)
        cands = [f for f in range(P.F) if keys[f] == low]
        f = cands[0] if gen is None else gen.choice(cands)
        fac[j] = f
        load[f] += P.tot[j]
        for machine, d in P.ops[j]:
            load_by_machine[f][machine] += d
            if load_by_machine[f][machine] > peak[f]:
                peak[f] = load_by_machine[f][machine]
    return fac


def _groups(P, fac):
    return [[j for j in range(P.n) if fac[j] == f] for f in range(P.F)]


def _fbi(P, jobs, st, c):
    last = P.m - 1
    for _ in range(6):
        end = {j: [st[j][k] + P.ops[j][k][1] for k in range(P.m)] for j in jobs}
        c_rev, st_rev = _gt(P, jobs, P.rev, pick=_first_max(lambda j, p, r, o: end[j][last - p[j]]))
        back = _mirror(P, jobs, st_rev, c_rev)
        c_fwd, st_fwd = _gt(P, jobs, P.ops, pick=_first_max(lambda j, p, r, o: -back[j][p[j]]))
        cand, cc = (st_fwd, c_fwd) if c_fwd <= c_rev else (back, c_rev)
        if cc < c:
            st, c = cand, cc
        else:
            break
    return st, c


class _FactoryTabu:

    def __init__(self, P, jobs, starts, gen) -> None:
        self.P, self.jobs, self.gen, m = P, jobs, gen, P.m
        self.dur = [d for j in jobs for _, d in P.ops[j]]
        self.mach = [machine for j in jobs for machine, _ in P.ops[j]]
        self.N = len(jobs) * m
        seq = [[] for _ in range(m)]
        for lj, j in enumerate(jobs):
            for k in range(m):
                seq[P.ops[j][k][0]].append((starts[j][k], lj * m + k))
        self.seq = [[o for _, o in sorted(s)] for s in seq]
        self.cur = self._evaluate(self.seq)
        self.best_ms, self.best_seq = self.cur[0], [s[:] for s in self.seq]
        self.tabu: Dict[Tuple[int, int], int] = {}
        self.step, self.since = 0, 0

    def _evaluate(self, seq):
        m, N, dur = self.P.m, self.N, self.dur
        mpred, msucc = [-1] * N, [-1] * N
        for s in seq:
            for a, b in zip(s, s[1:]):
                mpred[b], msucc[a] = a, b
        indeg = [(1 if o % m else 0) + (mpred[o] >= 0) for o in range(N)]
        start = [0] * N
        stack = [o for o in range(N) if indeg[o] == 0]
        done = 0
        while stack:
            o = stack.pop()
            done += 1
            e = start[o] + dur[o]
            if (o + 1) % m:
                if e > start[o + 1]:
                    start[o + 1] = e
                indeg[o + 1] -= 1
                if not indeg[o + 1]:
                    stack.append(o + 1)
            nxt = msucc[o]
            if nxt >= 0:
                if e > start[nxt]:
                    start[nxt] = e
                indeg[nxt] -= 1
                if not indeg[nxt]:
                    stack.append(nxt)
        if done < N:
            return None
        return max(start[o] + dur[o] for o in range(m - 1, N, m)), start, mpred

    def _moves(self):
        _, start, mpred = self.cur
        dur = self.dur
        o = max(range(self.N), key=lambda o: start[o] + dur[o])
        path = [o]
        while start[o] > 0:
            p = mpred[o]
            o = p if p >= 0 and start[p] + dur[p] == start[o] else o - 1
            path.append(o)
        path.reverse()
        blocks, cur = [], [path[0]]
        for o in path[1:]:
            if self.mach[o] == self.mach[cur[-1]]:
                cur.append(o)
            else:
                blocks.append(cur)
                cur = [o]
        blocks.append(cur)
        moves, nb = [], len(blocks)
        for bi, b in enumerate(blocks):
            if len(b) < 2:
                continue
            if bi > 0 or nb == 1:
                moves.append((b[0], b[1]))
            if (bi < nb - 1 or nb == 1) and (len(b) > 2 or not moves or moves[-1] != (b[-2], b[-1])):
                moves.append((b[-2], b[-1]))
        return moves, blocks

    def _swapped(self, a, b):
        machine = self.mach[a]
        s = self.seq[machine]
        i = s.index(a)
        ns = s[:]
        ns[i], ns[i + 1] = b, a
        seq = self.seq[:]
        seq[machine] = ns
        return seq

    def step_once(self) -> bool:
        self.step += 1
        cands, _ = self._moves()
        if not cands:
            return False
        best = None
        for a, b in cands:
            seq = self._swapped(a, b)
            r = self._evaluate(seq)
            if r is None:
                continue
            if self.tabu.get((a, b), 0) > self.step and r[0] >= self.best_ms:
                continue
            if best is None or r[0] < best[0]:
                best = (r[0], seq, r, a, b)
        if best is None:
            self.tabu.clear()
            return False
        c, seq, r, a, b = best
        self.seq, self.cur = seq, r
        self.tabu[(b, a)] = self.step + TENURE
        improved = False
        if c < self.best_ms:
            self.best_ms, self.best_seq, self.since, improved = c, [s[:] for s in seq], 0, True
        else:
            self.since += 1
        if self.since > RESTART:
            self.seq = [s[:] for s in self.best_seq]
            self.cur = self._evaluate(self.seq)
            for _ in range(3):
                pairs = [(x, y) for blk in self._moves()[1] for x, y in zip(blk, blk[1:])]
                if pairs:
                    x, y = self.gen.choice(pairs)
                    seq = self._swapped(x, y)
                    r = self._evaluate(seq)
                    if r is not None:
                        self.seq, self.cur = seq, r
            self.tabu.clear()
            self.since = 0
        return improved

    def best_starts(self) -> Dict[int, List[int]]:
        m = self.P.m
        start = self._evaluate(self.best_seq)[1]
        return {j: [start[lj * m + k] for k in range(m)] for lj, j in enumerate(self.jobs)}


def find_upper_bound_tabu(instance: Instance, time_limit: float, seed: int, tabu_share: float,
                          counts: Optional[Dict[str, int]] = None):
    P = _Problem(instance)
    gen = random.Random(seed)
    started = time.perf_counter()
    best: List[object] = [INF, None, None]

    def offer(ms, sts, fac):
        if ms < best[0]:
            starts = [None] * P.n
            for st in sts:
                for j, s in st.items():
                    starts[j] = list(s)
            best[:] = [ms, starts, list(fac)]

    def improve(sts, cms, groups):
        sts, cms = list(sts), list(cms)
        for _ in range(4 * P.F):
            f = max(range(P.F), key=lambda f: cms[f])
            st, c = _fbi(P, groups[f], sts[f], cms[f])
            if c < cms[f]:
                sts[f], cms[f] = st, c
            else:
                break
        return sts, cms

    for fac in (_assign_lpt(P), _assign_load(P, order=sorted(range(P.n), key=lambda j: (-P.tot[j], j)))):
        groups = _groups(P, fac)
        per_factory = [(INF, None)] * P.F
        for rule in RULES.values():
            for backward in (False, True):
                ops = P.rev if backward else P.ops
                for active in (True, False):
                    for f, g in enumerate(groups):
                        if not g:
                            per_factory[f] = (0, {})
                            continue
                        c, st = _gt(P, g, ops, pick=rule) if active else _nondelay(P, g, ops, rule)
                        if backward:
                            st = _mirror(P, g, st, c)
                        if c < per_factory[f][0]:
                            per_factory[f] = (c, st)
        cms = [c for c, _ in per_factory]
        sts = [st for _, st in per_factory]
        offer(max(cms), sts, fac)
        sts, cms = improve(sts, cms, groups)
        offer(max(cms), sts, fac)

    restarts = 0
    phase_b_end = started + tabu_share * time_limit
    while best[0] > P.lb and (restarts < counts["iterations"] if counts is not None
                              else time.perf_counter() < phase_b_end):
        restarts += 1
        cum = P.cum[gen.randrange(BIAS_LEVELS)]
        fac = _assign_load(P, gen)
        groups = _groups(P, fac)
        bound = best[0]
        delta = gen.random()
        cms, sts = [INF] * P.F, [None] * P.F
        for f, g in enumerate(groups):
            if not g:
                cms[f], sts[f] = 0, {}
                continue
            r = _gt(P, g, P.ops, gen, delta, cum, bound)
            if r is not None:
                cms[f], sts[f] = r
        stall = tries = 0
        while stall < P.F and tries < 2 * P.F:
            f = max(range(P.F), key=lambda f: cms[f])
            if max([cms[g] for g in range(P.F) if g != f], default=0) >= bound:
                break
            r = _gt(P, groups[f], P.ops, gen, gen.random(), cum, min(cms[f], bound))
            tries += 1
            if r is not None and r[0] < cms[f]:
                cms[f], sts[f] = r
                stall = 0
            else:
                stall += 1
        if max(cms) < best[0]:
            sts, cms = improve(sts, cms, groups)
            offer(max(cms), sts, fac)

    steps = 0
    deadline = started + time_limit
    if best[0] > P.lb and (counts["tabu_steps"] > 0 if counts is not None else time.perf_counter() < deadline):
        fac = best[2]
        searches = [_FactoryTabu(P, g, {j: best[1][j] for j in g}, gen) for g in _groups(P, fac) if g]
        while best[0] > P.lb and (steps < counts["tabu_steps"] if counts is not None
                                  else time.perf_counter() < deadline):
            steps += 1
            f = max(range(len(searches)), key=lambda f: searches[f].best_ms)
            if searches[f].step_once():
                ms = max(s.best_ms for s in searches)
                if ms < best[0]:
                    offer(ms, [s.best_starts() for s in searches], fac)
    return best[0], best[1], best[2], {"iterations": restarts, "tabu_steps": steps}
