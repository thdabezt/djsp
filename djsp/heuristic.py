from __future__ import annotations

import csv
import dataclasses
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config
from .heuristic_assign import find_upper_bound_assign
from .heuristic_random import find_upper_bound_random
from .model import Instance

METHODS = ("assign", "random")
CACHE_FIELDS = ["instance", "seed", "method", "time_limit", "iterations", "ub"]


def search(instance: Instance, method: str, time_limit: float, seed: int,
           counts: Dict[str, int] | None = None) -> Tuple[int, List[List[int]], List[int], Dict[str, int]]:
    if method == "random":
        return find_upper_bound_random(instance, time_limit, seed,
                                       None if counts is None else counts["iterations"])
    if method == "assign":
        return find_upper_bound_assign(instance, time_limit, seed,
                                       None if counts is None else counts["iterations"])
    raise ValueError(f"heuristic must be one of {METHODS}, not {method!r}")


def _cache_path() -> Optional[Path]:
    if not config.HEURISTIC_CACHE:
        return None
    path = Path(config.HEURISTIC_CACHE)
    return path if path.is_absolute() else config.PROJECT_ROOT / path


@contextmanager
def _locked(path: Path):
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + 120
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise RuntimeError(f"cache lock {lock} held for more than 120 s; remove it if no run is active")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        os.remove(lock)


def read_cache(path: Path) -> Dict[Tuple[str, int, str, float], Dict[str, object]]:
    entries: Dict[Tuple[str, int, str, float], Dict[str, object]] = {}
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                entries[(row["instance"], int(row["seed"]), row["method"], float(row["time_limit"]))] = {
                    "instance": row["instance"], "seed": int(row["seed"]), "method": row["method"],
                    "time_limit": float(row["time_limit"]), "iterations": int(row["iterations"]),
                    "ub": int(row["ub"])}
    return entries


def _key(name: str) -> Tuple[str, int, str, float]:
    return name, int(config.HEURISTIC_SEED), str(config.HEURISTIC_METHOD), float(config.HEURISTIC_TIME_LIMIT)


def cached_ub(name: str) -> Optional[int]:
    path = _cache_path()
    if path is None:
        return None
    entry = read_cache(path).get(_key(name))
    return None if entry is None else int(entry["ub"])


def store_in_cache(path: Path, entry: Dict[str, object]) -> Dict[str, object]:
    with _locked(path):
        existing = read_cache(path).get((entry["instance"], entry["seed"], entry["method"], entry["time_limit"]))
        if existing is not None:
            return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.is_file() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CACHE_FIELDS, lineterminator="\n")
            if new:
                writer.writeheader()
            writer.writerow(entry)
        return entry


def apply_horizon(instance: Instance) -> Tuple[Instance, Dict[str, object]]:
    seed, method = int(config.HEURISTIC_SEED), str(config.HEURISTIC_METHOD)
    if method not in METHODS:
        raise ValueError(f"heuristic must be one of {METHODS}, not {method!r}")
    cache = _cache_path()
    record = {"heuristic_method": method, "heuristic_seed": seed,
              "heuristic_time_limit": float(config.HEURISTIC_TIME_LIMIT)}
    if cache is not None:
        entry = read_cache(cache).get(_key(instance.name))
        if entry is not None:
            makespan = int(entry["ub"])
            print(f"[{instance.name}] heuristic UB={makespan} method={method} seed={seed} (cache)", flush=True)
            record.update({"heuristic_ub": makespan, "heuristic_iterations": entry["iterations"],
                           "heuristic_time": 0.0, "heuristic_source": "cache"})
            return dataclasses.replace(instance, horizon=makespan), record
    started = time.perf_counter()
    makespan, _, _, done = search(instance, method, float(config.HEURISTIC_TIME_LIMIT), seed)
    source = "search"
    if cache is not None:
        stored = store_in_cache(cache, {"instance": instance.name, "seed": seed, "method": method,
                                        "time_limit": float(config.HEURISTIC_TIME_LIMIT), **done, "ub": makespan})
        if (stored["ub"], stored["iterations"]) != (makespan, done["iterations"]):
            counts = {"iterations": stored["iterations"]}
            makespan, _, _, done = search(instance, method, 0.0, seed, counts)
            if makespan != stored["ub"]:
                raise RuntimeError(f"cached UB {stored['ub']} of {instance.name} not reproduced: {makespan}")
            source = "cache"
    elapsed = time.perf_counter() - started
    instance = dataclasses.replace(instance, horizon=makespan)
    print(f"[{instance.name}] heuristic UB={makespan} method={method} seed={seed} "
          f"iterations={done['iterations']} time={elapsed:.2f}s", flush=True)
    record.update({"heuristic_ub": makespan, "heuristic_iterations": done["iterations"],
                   "heuristic_time": elapsed, "heuristic_source": source})
    return instance, record
