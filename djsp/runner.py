from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from . import config
from .heuristic import cached_ub
from .output import write_error, write_summary
from .system import physical_cores, run_metadata, total_memory_mb
from .worker import ENGINES

FINAL = {"OPTIMAL", "FEASIBLE", "TIMEOUT", "UNSAT", "FAILED", "SKIPPED", "MEMOUT"}
OUT_OF_MEMORY = re.compile(r"MemoryError|bad_alloc|Cannot allocate memory|out of memory", re.IGNORECASE)


def _instances(names: Iterable[str] | None, factories: Tuple[int, ...]) -> List[Tuple[Path, int]]:
    dataset = Path(config.DATASET_DIR)
    tasks: List[Tuple[Path, int]] = []
    if not names:
        for path in sorted(dataset.glob("ta*.txt"), key=lambda p: p.name):
            tasks += [(path.resolve(), f) for f in factories]
        return tasks
    for name in names:
        match = re.fullmatch(r"ta(\d\d)(?:-(?:ta)?(\d\d))?(?:_(\d)f)?", name.lower())
        if match:
            first, last = int(match.group(1)), int(match.group(2) or match.group(1))
            counts = (int(match.group(3)),) if match.group(3) else factories
            for number in range(first, last + 1):
                path = dataset / f"ta{number:02d}.txt"
                if not path.is_file():
                    raise FileNotFoundError(path)
                tasks += [(path.resolve(), f) for f in counts]
        else:
            path = Path(name).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            tasks += [(path, f) for f in factories]
    return tasks


def _read_result(run_directory: Path, name: str) -> Dict[str, object]:
    path = run_directory / name / "result.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class CorePool:
    def __init__(self, cores: List[List[int]]) -> None:
        self.free = list(cores)
        self.condition = threading.Condition()

    def acquire(self) -> List[int]:
        with self.condition:
            while not self.free:
                self.condition.wait()
            return self.free.pop(0)

    def release(self, core: List[int]) -> None:
        with self.condition:
            self.free.append(core)
            self.free.sort(key=min)
            self.condition.notify_all()


class MemoryBudget:
    def __init__(self, megabytes: float) -> None:
        self.total = megabytes
        self.used = 0.0
        self.condition = threading.Condition()

    def acquire(self, megabytes: float) -> float:
        megabytes = min(megabytes, self.total)
        with self.condition:
            while self.used + megabytes > self.total:
                self.condition.wait()
            self.used += megabytes
        return megabytes

    def release(self, megabytes: float) -> None:
        with self.condition:
            self.used -= megabytes
            self.condition.notify_all()


def _memory_budget_mb() -> float:
    if config.MEMORY_BUDGET_MB:
        return float(config.MEMORY_BUDGET_MB)
    total = total_memory_mb()
    return 0.85 * total if total else 30000.0


def _sat_memory_mb(path: Path, n_factories: int) -> float:
    from .encoding import build_domains, count_clauses
    from .parser import read_instance
    instance = read_instance(path, n_factories)
    ub = cached_ub(instance.name)
    horizon = ub if ub is not None else int(math.ceil(config.HEURISTIC_HORIZON_ESTIMATE * instance.lower_bound))
    instance = dataclasses.replace(instance, horizon=horizon)
    clauses = count_clauses(instance, build_domains(instance))
    if ub is not None and clauses > config.MAX_CLAUSES:
        return 0.0
    return 300.0 + min(clauses, config.MAX_CLAUSES) * float(config.SAT_BYTES_PER_CLAUSE) / 2 ** 20


def stopped_record(path: Path, n_factories: int, engine: str, time_limit: float, wall: float,
                   status: str, note: str) -> Dict[str, object]:
    from .parser import read_instance
    instance = read_instance(path, n_factories)
    return {
        "instance": instance.name, "source": str(instance.source), "engine": engine,
        "jobs": instance.n_jobs, "machines": instance.n_machines, "factories": instance.n_factories,
        "operations": len(instance.operations), "UB": None if engine == "cp" else cached_ub(instance.name),
        "trivial_LB": instance.lower_bound, "time_limit": time_limit,
        "status": status, "makespan": None, "lower_bound": instance.lower_bound,
        "solve_time": wall, "note": note,
    }


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_worker(command: List[str], log_path: Path, timeout: float) -> Tuple[int | None, bool]:
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=config.PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   **options)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            return None, True
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        return code, False


def _write_record(run_directory: Path, name: str, data: Dict[str, object]) -> None:
    (run_directory / name).mkdir(parents=True, exist_ok=True)
    (run_directory / name / "result.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_task(engine: str, path: Path, n_factories: int, run_directory: Path, time_limit: float,
              memory: MemoryBudget, cores: CorePool | None = None) -> Dict[str, object]:
    from .parser import instance_name
    name = instance_name(path.stem, n_factories)
    previous = _read_result(run_directory, name)
    if previous.get("status") in FINAL:
        return previous
    reserved = memory.acquire(_sat_memory_mb(path, n_factories)) if engine != "cp" else 0.0
    heuristic = 0.0 if engine == "cp" or cached_ub(name) is not None else float(config.HEURISTIC_TIME_LIMIT)
    command = [sys.executable, "-m", "djsp.worker", "--engine", engine, "--instance", str(path),
               "--factories", str(n_factories), "--run-dir", str(run_directory),
               "--time-limit", str(time_limit)]
    if config.MEMORY_LIMIT_MB:
        command += ["--memory-limit-mb", str(config.MEMORY_LIMIT_MB)]
    log_path = run_directory / f"{name}.log"
    core = cores.acquire() if cores else None
    if core:
        command += ["--cpus", ",".join(str(cpu) for cpu in core)]
    started = time.perf_counter()
    try:
        code, killed = _run_worker(command, log_path, time_limit + heuristic + config.KILL_GRACE)
    finally:
        if core:
            cores.release(core)
        if reserved:
            memory.release(reserved)
    wall = time.perf_counter() - started
    data = _read_result(run_directory, name)
    if data.get("heuristic_time") is not None:
        heuristic = float(data["heuristic_time"])
    wall = max(0.0, wall - heuristic)
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    out_of_memory = bool(OUT_OF_MEMORY.search(log_text)) or (code is not None and code in (-9, 137))
    if (not data or data.get("status") == "ERROR") and out_of_memory and not killed:
        data = stopped_record(path, n_factories, engine, time_limit, wall, "MEMOUT",
                              f"worker ran out of memory (exit code {code})")
        _write_record(run_directory, name, data)
    elif not data and killed:
        data = stopped_record(path, n_factories, engine, time_limit, wall, "TIMEOUT",
                              "worker stopped by the runner after the time limit, no schedule found")
        _write_record(run_directory, name, data)
    elif not data:
        data = write_error(run_directory, name, RuntimeError(f"worker wrote no result (exit code {code})"))
    elif killed or data.get("status") == "RUNNING":
        stopped = "TIMEOUT" if killed or not out_of_memory else "MEMOUT"
        data["status"] = "FEASIBLE" if data.get("makespan") is not None else stopped
        data["note"] = ("worker stopped by the runner after the time limit" if killed else
                        f"worker ended early (exit code {code}"
                        f"{', out of memory' if out_of_memory else ''}) after its last checkpoint")
        data["solve_time"] = wall
        _write_record(run_directory, name, data)
    print(f"[{name}] {engine} {data.get('status')} makespan={data.get('makespan')} "
          f"UB={data.get('UB')} time={float(data.get('solve_time') or wall):.1f}s", flush=True)
    return data


def _settings() -> Dict[str, object]:
    names = ["HEURISTIC_METHOD", "HEURISTIC_TIME_LIMIT", "HEURISTIC_SEED", "HEURISTIC_CACHE", "TIME_LIMIT",
             "KILL_GRACE", "PARALLEL", "MEMORY_BUDGET_MB", "SAT_BYTES_PER_CLAUSE", "MEMORY_LIMIT_MB", "MAX_CLAUSES",
             "CPU_PINNING", "SAT_BINARY", "CPLEX_STUDIO", "CP_WORKERS", "DATASET_DIR"]
    return {name: str(getattr(config, name)) if isinstance(getattr(config, name), Path) else getattr(config, name)
            for name in names}


def run(
    names: Iterable[str] | None = None,
    engines: Tuple[str, ...] = ("sb_on",),
    factories: Tuple[int, ...] = config.FACTORIES,
    time_limit: float = config.TIME_LIMIT,
    results_directory: str | Path | None = None,
    resume: str | Path | None = None,
) -> int:
    unknown = [engine for engine in engines if engine not in ENGINES]
    if unknown:
        raise ValueError(f"unknown engine(s) {unknown}; choose from {list(ENGINES)}")
    tasks = _instances(names, factories)
    if not tasks:
        print(f"No instances found in {Path(config.DATASET_DIR)}")
        return 0

    from .parser import instance_name, read_instance
    sizes = {task: len(read_instance(task[0], task[1]).operations) for task in tasks}
    tasks.sort(key=lambda task: (sizes[task], task[1], task[0].name))

    root = Path(results_directory) if results_directory else Path(config.RESULTS_DIR)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_root = Path(resume).resolve() if resume else root.resolve() / f"run_{timestamp}"
    directories: Dict[str, Path] = {engine: run_root / engine for engine in engines}
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    meta_path = run_root / ("run_meta.json" if not (run_root / "run_meta.json").exists()
                            else f"run_meta_resume_{timestamp}.json")
    metadata = run_metadata(config.PROJECT_ROOT, _settings())
    metadata.update({"engines": list(engines), "factories": list(factories), "time_limit": time_limit,
                     "instances": len(tasks)})
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    memory = MemoryBudget(_memory_budget_mb())
    cores = None
    if config.CPU_PINNING == "core":
        found = physical_cores()
        if found:
            cores = CorePool(found)
            print(f"CPU pinning: {len(found)} physical cores, at most {len(found)} workers at a time", flush=True)
    elif config.CPU_PINNING:
        raise ValueError(f"CPU_PINNING must be core or None, not {config.CPU_PINNING!r}")
    rows: Dict[str, Dict[str, Dict[str, object]]] = {engine: {} for engine in engines}
    lock = threading.Lock()
    pools = {engine: ThreadPoolExecutor(max_workers=int(config.PARALLEL)) for engine in engines}

    def done(engine: str, name: str, future) -> None:
        try:
            data = future.result()
        except Exception as error:
            data = write_error(directories[engine], name, error)
        with lock:
            rows[engine][name] = data
            ordered = [rows[engine][key] for key in sorted(rows[engine])]
            write_summary(directories[engine], ordered)

    print(f"{len(tasks)} instances x {list(engines)} -> {run_root}", flush=True)
    for path, n_factories in tasks:
        name = instance_name(path.stem, n_factories)
        for engine in engines:
            future = pools[engine].submit(_run_task, engine, path, n_factories, directories[engine],
                                          time_limit, memory, cores)
            future.add_done_callback(lambda f, e=engine, n=name: done(e, n, f))
    for pool in pools.values():
        pool.shutdown(wait=True)
    for engine in engines:
        for path in sorted(directories[engine].glob("summary_*f.csv")):
            print(f"Summary: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m djsp")
    parser.add_argument("instances", nargs="*", help="ta01_2f, ta01, ta01-10 or .txt paths (default: all)")
    parser.add_argument("--engine", default="sb_on", help=f"{', '.join(ENGINES)} or a comma list")
    parser.add_argument("--heuristic", default=None, choices=("assign", "random"), help="initial UB (default: assign)")
    parser.add_argument("--heuristic-time", type=float, default=None, help="heuristic time limit (default: 5)")
    parser.add_argument("--factories", default=None, help="comma list (default: 2,3,4)")
    parser.add_argument("--time-limit", type=float, default=None)
    parser.add_argument("--results-dir", default=None, help="parent of the run directory")
    parser.add_argument("--resume", default=None, help="run directory to complete")
    parser.add_argument("--parallel", type=int, default=None)
    parser.add_argument("--memory-mb", type=float, default=None, help="memory budget of the SAT workers")
    parser.add_argument("--memory-limit-mb", type=float, default=None, help="address-space limit per worker")
    arguments = parser.parse_args()

    settings: Dict[str, object] = {}
    if arguments.heuristic:
        settings["HEURISTIC_METHOD"] = arguments.heuristic
    if arguments.heuristic_time is not None:
        if arguments.heuristic_time <= 0:
            parser.error("--heuristic-time must be positive")
        settings["HEURISTIC_TIME_LIMIT"] = arguments.heuristic_time
    if arguments.parallel:
        settings["PARALLEL"] = arguments.parallel
    if arguments.memory_mb:
        settings["MEMORY_BUDGET_MB"] = arguments.memory_mb
    if arguments.memory_limit_mb:
        settings["MEMORY_LIMIT_MB"] = arguments.memory_limit_mb
    if settings:
        config.apply_settings(settings)
        previous = json.loads(os.environ.get("DJSP_SETTINGS") or "{}")
        os.environ["DJSP_SETTINGS"] = json.dumps({**previous, **settings})

    engines = tuple(engine.strip() for engine in arguments.engine.split(",") if engine.strip())
    factories = (tuple(int(f) for f in arguments.factories.split(",")) if arguments.factories
                 else tuple(config.FACTORIES))
    time_limit = arguments.time_limit if arguments.time_limit is not None else float(config.TIME_LIMIT)
    return run(arguments.instances or None, engines, factories, time_limit,
               results_directory=arguments.results_dir, resume=arguments.resume)
