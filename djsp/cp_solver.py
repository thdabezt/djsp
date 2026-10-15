from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict

from . import config
from .model import Instance, SolveResult, factory_makespans
from .parser import read_instance
from .system import package_version


def _cp_executable() -> str:
    explicit = os.environ.get("CPLEX_CP_EXECFILE")
    if explicit:
        return explicit
    studio = config.CPLEX_STUDIO
    if studio is None:
        raise RuntimeError("CPLEX Optimization Studio not found: set DJSP_CPLEX_STUDIO or CPLEX_CP_EXECFILE")
    if os.name == "nt":
        dll = studio / "cplex" / "bin" / "x64_win64"
        if str(dll).lower() not in os.environ.get("PATH", "").lower():
            os.environ["PATH"] = str(dll) + os.pathsep + os.environ.get("PATH", "")
        return str(studio / "cpoptimizer" / "bin" / "x64_win64" / "cpoptimizer.exe")
    platform_dir = "x86-64_osx" if sys.platform == "darwin" else "x86-64_linux"
    return str(studio / "cpoptimizer" / "bin" / platform_dir / "cpoptimizer")


def build_model(instance: Instance):
    from docplex.cp.expression import interval_var
    from docplex.cp.model import CpoModel
    from docplex.cp.modeler import alternative, end_before_start, end_of, max_of, minimize, no_overlap, presence_of

    n_factories = instance.n_factories
    model = CpoModel(name=instance.name)
    master = {}
    optional = {}
    constraints = 0
    for operation in instance.operations:
        master[operation.id] = interval_var(size=operation.duration, name=f"I{operation.job}_{operation.index}")
        for factory in range(n_factories):
            optional[(operation.id, factory)] = interval_var(
                optional=True, size=operation.duration, name=f"A{operation.job}_{operation.index}_{factory}")
        model.add(alternative(master[operation.id], [optional[(operation.id, f)] for f in range(n_factories)]))
        constraints += 1

    for job in instance.jobs:
        first = job[0]
        for operation in job[1:]:
            for factory in range(n_factories):
                model.add(presence_of(optional[(operation.id, factory)]) == presence_of(optional[(first.id, factory)]))
                constraints += 1
        for operation, following in zip(job, job[1:]):
            model.add(end_before_start(master[operation.id], master[following.id]))
            constraints += 1

    machines: Dict[int, list] = {}
    for operation in instance.operations:
        machines.setdefault(operation.machine, []).append(operation)
    for operations in machines.values():
        for factory in range(n_factories):
            model.add(no_overlap([optional[(operation.id, factory)] for operation in operations]))
            constraints += 1

    model.add(minimize(max_of([end_of(master[job[-1].id]) for job in instance.jobs])))
    return model, master, optional, {"intervals": len(master) + len(optional), "constraints": constraints}


def dump_cpo(path: str | Path, n_factories: int, out_dir: str | Path) -> Path:
    instance = read_instance(path, n_factories)
    model = build_model(instance)[0]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{instance.name}_cp.cpo"
    model.export_as_cpo(out=str(target))
    return target


def solve_instance_cp(
    path: str | Path,
    n_factories: int,
    time_limit: float = config.TIME_LIMIT,
    checkpoint: Callable[[SolveResult], None] | None = None,
) -> SolveResult:
    total_started = time.perf_counter()
    instance = read_instance(path, n_factories)
    n_factories = instance.n_factories
    execfile = _cp_executable()

    base_data: Dict[str, object] = {
        "instance": instance.name,
        "source": str(instance.source),
        "engine": "cp",
        "solver": "CP Optimizer (docplex)",
        "docplex": package_version("docplex"),
        "cplex_studio": str(config.CPLEX_STUDIO),
        "workers": config.CP_WORKERS,
        "jobs": instance.n_jobs,
        "machines": instance.n_machines,
        "factories": instance.n_factories,
        "operations": len(instance.operations),
        "UB": None,
        "time_limit": time_limit,
        "trivial_LB": instance.lower_bound,
    }

    build_started = time.perf_counter()
    model, master, optional, size = build_model(instance)
    build_time = time.perf_counter() - build_started

    state = {"starts": None, "factories": None, "makespan": None, "first": None, "latest": None,
             "iterations": [], "bound": None}

    def decode(solution):
        starts = [[int(solution.get_var_solution(master[operation.id]).get_start()) for operation in job]
                  for job in instance.jobs]
        factories = []
        for job in instance.jobs:
            present = [f for f in range(n_factories)
                       if solution.get_var_solution(optional[(job[0].id, f)]).is_present()]
            if len(present) != 1:
                raise RuntimeError(f"solution places job {job[0].job} in {len(present)} factories")
            factories.append(present[0])
        return starts, factories

    def snapshot(status: str) -> SolveResult:
        data = dict(base_data)
        proven = status == "OPTIMAL"
        data.update({
            "status": status,
            "makespan": state["makespan"],
            "lower_bound": (state["makespan"] if proven
                            else max(instance.lower_bound, state["bound"] or 0)),
            "factory_makespans": (factory_makespans(instance, state["starts"], state["factories"])
                                  if state["starts"] is not None else None),
            "vars": size["intervals"],
            "clauses": size["constraints"],
            "build_time": build_time,
            "solve_time": time.perf_counter() - total_started,
            "time_first_sat": state["first"] if state["first"] is not None else "",
            "time_latest_sat": state["latest"] if state["latest"] is not None else "",
            "time_unsat": "",
            "iterations": state["iterations"],
        })
        return SolveResult(instance=instance, data=data, starts=state["starts"], factories=state["factories"])

    left = max(1.0, time_limit - (time.perf_counter() - total_started))
    terminal = None
    solver = model.start_search(TimeLimit=left, Workers=config.CP_WORKERS, LogVerbosity="Quiet",
                                execfile=execfile)
    try:
        for solution in solver:
            terminal = solution
            if solution is None or not solution.is_solution():
                continue
            starts, factories = decode(solution)
            makespan = max(factory_makespans(instance, starts, factories))
            elapsed = time.perf_counter() - total_started
            try:
                state["bound"] = int(solution.get_objective_bounds()[0])
            except Exception:
                pass
            if state["makespan"] is None or makespan < state["makespan"]:
                state.update({"starts": starts, "factories": factories, "makespan": makespan, "latest": elapsed})
                if state["first"] is None:
                    state["first"] = elapsed
                state["iterations"].append({"status": "SAT", "makespan": makespan, "elapsed": elapsed})
                print(f"[{instance.name}] CP makespan={makespan} elapsed={elapsed:.2f}s", flush=True)
                if checkpoint is not None:
                    checkpoint(snapshot("RUNNING"))
        try:
            last = solver.get_last_result()
            if last is not None:
                terminal = last
        except Exception:
            pass
    finally:
        try:
            version = solver.get_solver_version()
        except Exception:
            version = None
        try:
            solver.end()
        except Exception:
            pass

    solve_status = terminal.get_solve_status() if terminal is not None else "Unknown"
    try:
        state["bound"] = int(terminal.get_objective_bounds()[0])
    except Exception:
        pass
    if state["starts"] is not None:
        status = "OPTIMAL" if solve_status == "Optimal" else "FEASIBLE"
    else:
        status = "UNSAT" if solve_status == "Infeasible" else "TIMEOUT"
    result = snapshot(status)
    result.data["cp_status"] = solve_status
    if version:
        result.data["solver"] = f"CP Optimizer {version} (docplex)"
    return result
