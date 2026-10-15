from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from .model import Instance, Operation


def _integers(text: str, label: str) -> List[int]:
    try:
        return [int(token) for token in text.split()]
    except ValueError as error:
        raise ValueError(f"{label} contains a non-integer value") from error


def instance_name(base: str, n_factories: int) -> str:
    return f"{base}_{n_factories}f"


def split_name(name: str) -> Tuple[str, int]:
    match = re.fullmatch(r"(ta\d\d)_(\d)f", name)
    if not match:
        raise ValueError(f"instance name {name!r} is not like ta01_2f")
    return match.group(1), int(match.group(2))


def read_instance(path: str | Path, n_factories: int) -> Instance:
    source = Path(path).resolve()
    base = _integers(source.read_text(encoding="utf-8"), "Taillard file")
    if len(base) < 2:
        raise ValueError("file must start with jobs and machines")
    n_jobs, n_machines = base[:2]
    if n_jobs <= 0 or n_machines <= 0:
        raise ValueError("jobs and machines must be positive")
    expected = 2 + 2 * n_jobs * n_machines
    if len(base) < expected:
        raise ValueError(f"file must contain {expected} integers, found {len(base)}")
    if n_factories <= 0:
        raise ValueError("the number of factories must be positive")

    jobs: List[Tuple[Operation, ...]] = []
    offset = 2
    operation_id = 0
    expected_machines = set(range(n_machines))
    for job in range(n_jobs):
        operations: List[Operation] = []
        machines = set()
        for index in range(n_machines):
            machine = base[offset]
            duration = base[offset + 1]
            offset += 2
            if machine not in expected_machines:
                raise ValueError(f"job {job} has invalid machine {machine}")
            if machine in machines:
                raise ValueError(f"job {job} repeats machine {machine}")
            if duration <= 0:
                raise ValueError(f"job {job}, machine {machine}, has a non-positive duration")
            machines.add(machine)
            operations.append(Operation(operation_id, job, index, machine, duration))
            operation_id += 1
        jobs.append(tuple(operations))

    return Instance(name=instance_name(source.stem, n_factories), source=source, jobs=tuple(jobs),
                    n_factories=n_factories)
