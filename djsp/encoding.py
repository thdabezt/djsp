from __future__ import annotations

from typing import Dict, List, Tuple

from .model import Domains, Instance, Operation


def build_domains(instance: Instance) -> Domains:
    earliest: Dict[int, int] = {}
    latest: Dict[int, int] = {}
    for job in instance.jobs:
        elapsed = 0
        for operation in job:
            earliest[operation.id] = elapsed
            elapsed += operation.duration
        remaining = elapsed
        for operation in job:
            latest[operation.id] = instance.horizon - remaining
            remaining -= operation.duration
    return Domains(earliest=earliest, latest=latest)


def machine_groups(instance: Instance) -> Dict[int, List[Operation]]:
    groups: Dict[int, List[Operation]] = {machine: [] for machine in range(instance.n_machines)}
    for operation in instance.operations:
        groups[operation.machine].append(operation)
    return groups


def _order_range(domains: Domains, before: Operation, after: Operation) -> Tuple[int, int, bool]:
    eb, lb = domains.earliest[before.id], domains.latest[before.id]
    ea, la = domains.earliest[after.id], domains.latest[after.id]
    first = max(eb, ea - before.duration + 1)
    cut = la - before.duration + 1
    if cut <= lb:
        return first, max(first, cut) - 1, True
    return first, lb, False


def count_clauses(instance: Instance, domains: Domains) -> int:
    total = 0
    for operation in instance.operations:
        width = domains.latest[operation.id] - domains.earliest[operation.id]
        total += max(0, width - 1)
        if operation.index + 1 < instance.n_machines:
            total += max(0, width)
    for operations in machine_groups(instance).values():
        for left_index in range(len(operations)):
            left = operations[left_index]
            for right in operations[left_index + 1:]:
                total += 1
                for before, after in ((left, right), (right, left)):
                    first, last, cut = _order_range(domains, before, after)
                    total += max(0, last - first + 1) + (1 if cut else 0)
    n, f = instance.n_jobs, instance.n_factories
    total += n * (1 + f * (f - 1) // 2) + n * (n - 1) * f + 2 * n * f
    return total
