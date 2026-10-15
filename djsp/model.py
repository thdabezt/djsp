from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Operation:
    id: int
    job: int
    index: int
    machine: int
    duration: int


@dataclass(frozen=True)
class Instance:
    name: str
    source: Path
    jobs: Tuple[Tuple[Operation, ...], ...]
    n_factories: int
    horizon: Optional[int] = None

    @property
    def n_jobs(self) -> int:
        return len(self.jobs)

    @property
    def n_machines(self) -> int:
        return len(self.jobs[0]) if self.jobs else 0

    @property
    def operations(self) -> Tuple[Operation, ...]:
        return tuple(operation for job in self.jobs for operation in job)

    @property
    def lower_bound(self) -> int:
        longest = max(sum(operation.duration for operation in job) for job in self.jobs)
        load = [0] * self.n_machines
        for operation in self.operations:
            load[operation.machine] += operation.duration
        return max(longest, max(-(-value // self.n_factories) for value in load))


@dataclass
class Domains:
    earliest: Dict[int, int]
    latest: Dict[int, int]


@dataclass
class SolveResult:
    instance: Instance
    data: Dict[str, object]
    starts: List[List[int]] | None
    factories: List[int] | None


def factory_makespans(instance: Instance, starts: List[List[int]], factories: List[int]) -> List[int]:
    values = [0] * instance.n_factories
    for job_index, job in enumerate(instance.jobs):
        end = starts[job_index][-1] + job[-1].duration
        values[factories[job_index]] = max(values[factories[job_index]], end)
    return values
