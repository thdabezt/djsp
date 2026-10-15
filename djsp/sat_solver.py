from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict

from . import config
from .encoding import build_domains, count_clauses
from .heuristic import apply_horizon
from .model import SolveResult, factory_makespans
from .parser import read_instance

SYMMETRY = {"sb_on": "on", "sb_off": "off"}


def _binary() -> Path:
    executable = Path(config.SAT_BINARY)
    if not executable.is_file():
        raise FileNotFoundError(f"{executable} not found: build it with 'make'")
    return executable


def dump_cnf(path: str | Path, n_factories: int, engine: str, out_dir: str | Path) -> Path:
    instance, _ = apply_horizon(read_instance(path, n_factories))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{instance.name}_{engine}_C{instance.horizon}.cnf"
    subprocess.run([str(_binary()), "--instance", str(instance.source), "--factories", str(n_factories),
                    "--horizon", str(instance.horizon), "--symmetry", SYMMETRY[engine], "--dump", str(target)],
                   check=True, stdout=subprocess.DEVNULL)
    return target


def solve_instance_sat(
    path: str | Path,
    n_factories: int,
    engine: str,
    time_limit: float = config.TIME_LIMIT,
    checkpoint: Callable[[SolveResult], None] | None = None,
    work_dir: str | Path | None = None,
) -> SolveResult:
    instance, heuristic = apply_horizon(read_instance(path, n_factories))
    started = time.perf_counter()
    domains = build_domains(instance)
    base: Dict[str, object] = {
        "instance": instance.name,
        "source": str(instance.source),
        "engine": engine,
        "solver": "CaDiCaL",
        "symmetry_breaking": SYMMETRY[engine],
        "jobs": instance.n_jobs,
        "machines": instance.n_machines,
        "factories": instance.n_factories,
        "operations": len(instance.operations),
        "UB": instance.horizon,
        "time_limit": time_limit,
        "trivial_LB": instance.lower_bound,
    }
    base.update(heuristic)

    def result(data: Dict[str, object], starts=None, factories=None) -> SolveResult:
        full = dict(base)
        full.update(data)
        return SolveResult(instance=instance, data=full, starts=starts, factories=factories)

    empty = {"makespan": None, "vars": 0, "clauses": 0, "build_time": 0.0,
             "time_first_sat": "", "time_latest_sat": "", "time_unsat": "", "iterations": []}
    estimated = count_clauses(instance, domains)
    base["estimated_clauses"] = estimated
    if estimated > config.MAX_CLAUSES:
        return result({**empty, "status": "SKIPPED", "lower_bound": instance.lower_bound,
                       "solve_time": time.perf_counter() - started,
                       "note": f"model would have {estimated} clauses > MAX_CLAUSES {config.MAX_CLAUSES}"})
    executable = _binary()

    folder = Path(work_dir) if work_dir else Path.cwd()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{instance.name}.sat.json"
    offset = time.perf_counter() - started
    command = [str(executable), "--instance", str(instance.source), "--factories", str(n_factories),
               "--horizon", str(instance.horizon), "--symmetry", SYMMETRY[engine],
               "--time-limit", f"{max(1.0, time_limit - offset):.3f}", "--out", str(out)]

    best = {"makespan": None, "starts": None, "factories": None}

    def read(final: bool) -> Dict[str, object] | None:
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        starts, factories = data.get("starts"), data.get("factories")
        if starts is not None and data.get("makespan") != best["makespan"]:
            best.update({"makespan": data["makespan"], "starts": starts, "factories": factories})
            if not final and checkpoint is not None:
                checkpoint(snapshot(data, "RUNNING"))
        return data

    def snapshot(data: Dict[str, object], status: str) -> SolveResult:
        def shifted(value):
            return value + offset if isinstance(value, (int, float)) else value

        proven = status == "OPTIMAL"
        iterations = [dict(item, elapsed=shifted(item.get("elapsed"))) for item in data.get("iterations", [])]
        return result({
            "status": status,
            "solver": data.get("solver", base["solver"]),
            "makespan": best["makespan"],
            "lower_bound": best["makespan"] if proven else instance.lower_bound,
            "factory_makespans": (factory_makespans(instance, best["starts"], best["factories"])
                                  if best["starts"] is not None else None),
            "vars": data.get("vars", 0),
            "clauses": data.get("clauses", 0),
            "build_time": shifted(data.get("build_time", 0.0)),
            "solve_time": time.perf_counter() - started,
            "time_first_sat": shifted(data.get("time_first_sat", "")),
            "time_latest_sat": shifted(data.get("time_latest_sat", "")),
            "time_unsat": data.get("time_unsat", ""),
            "iterations": iterations,
        }, best["starts"], best["factories"])

    process = subprocess.Popen(command)
    try:
        while True:
            try:
                process.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                read(final=False)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    data = read(final=True)
    try:
        out.unlink()
    except OSError:
        pass
    if process.returncode == 3:
        raise MemoryError("SAT core ran out of memory")
    if process.returncode != 0 or data is None:
        raise RuntimeError(f"SAT core failed with exit code {process.returncode}")
    return snapshot(data, str(data["status"]))
