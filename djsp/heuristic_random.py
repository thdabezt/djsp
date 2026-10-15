from __future__ import annotations

import random
import time
from typing import List, Optional, Tuple

from .model import Instance


def _construct(instance: Instance, generator: random.Random) -> Tuple[int, List[List[int]], List[int]]:
    n_jobs, n_machines, n_factories = instance.n_jobs, instance.n_machines, instance.n_factories
    totals = [sum(operation.duration for operation in job) for job in instance.jobs]
    progress = [0] * n_jobs
    job_ready = [0] * n_jobs
    factory_of = [-1] * n_jobs
    load = [0] * n_factories
    machine_ready = [[0] * n_machines for _ in range(n_factories)]
    starts = [[0] * n_machines for _ in range(n_jobs)]
    ready = list(range(n_jobs))
    while ready:
        job = generator.choice(ready)
        if factory_of[job] < 0:
            lowest = min(load)
            factory_of[job] = generator.choice([f for f in range(n_factories) if load[f] == lowest])
            load[factory_of[job]] += totals[job]
        operation = instance.jobs[job][progress[job]]
        machines = machine_ready[factory_of[job]]
        start = max(job_ready[job], machines[operation.machine])
        starts[job][progress[job]] = start
        job_ready[job] = machines[operation.machine] = start + operation.duration
        progress[job] += 1
        if progress[job] == n_machines:
            ready.remove(job)
    return max(job_ready), starts, factory_of


def find_upper_bound_random(instance: Instance, time_limit: float, seed: int,
                            iterations: Optional[int] = None):
    generator = random.Random(seed)
    deadline = time.perf_counter() + max(0.0, time_limit)
    best = None
    done = 0
    while done == 0 or (done < iterations if iterations is not None else time.perf_counter() < deadline):
        done += 1
        makespan, starts, factories = _construct(instance, generator)
        if best is None or makespan < best[0]:
            best = (makespan, starts, factories)
    return best[0], best[1], best[2], {"iterations": done, "tabu_steps": None}
