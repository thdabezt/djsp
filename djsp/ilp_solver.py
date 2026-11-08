from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from . import config
from .model import Instance, SolveResult, factory_makespans
from .parser import read_instance
from .system import package_version

SOLVERS = {"ilp_cplex": "CPLEX", "ilp_gurobi": "Gurobi", "ilp_cplex_ub": "CPLEX", "ilp_gurobi_ub": "Gurobi"}
MODEL = "Naderi and Azab (2015)"
ABSOLUTE_GAP = 0.999
EPSILON = 1e-6
VALUE = re.compile(r"^([YC])(\d+)_(\d+)$")


def serial_horizon(instance: Instance) -> int:
    loads = [0] * instance.n_factories
    for length in sorted((sum(operation.duration for operation in job) for job in instance.jobs), reverse=True):
        loads[loads.index(min(loads))] += length
    return max(loads)


def _threads() -> int | None:
    return None if str(config.ILP_THREADS).lower() == "auto" else int(config.ILP_THREADS)


def write_lp(instance: Instance, path: Path, big_m: int, lower: int | None = None) -> Dict[str, int]:
    n, m, f = instance.n_jobs, instance.n_machines, instance.n_factories
    p = [[0] * m for _ in range(n)]
    for j, job in enumerate(instance.jobs):
        for operation in job:
            p[j][operation.machine] = operation.duration
    constraints = 0
    with Path(path).open("w", encoding="ascii", newline="\n") as out:
        write = out.write
        write(f"\\ {MODEL} MILP, {instance.name}, M = {big_m}\n")
        write("Minimize\n obj: Cmax\nSubject To\n")
        for j in range(n):
            write(f" a{j}: " + " + ".join(f"Y{j}_{r}" for r in range(f)) + " = 1\n")
        for j in range(n):
            for i in range(m):
                write(f" p{j}_{i}: C{j}_{i} >= {p[j][i]}\n")
        for j, job in enumerate(instance.jobs):
            for previous, operation in zip(job, job[1:]):
                i, l = operation.machine, previous.machine
                write(f" q{j}_{i}: C{j}_{i} - C{j}_{l} >= {p[j][i]}\n")
                constraints += 1
        for i in range(m):
            for k in range(n):
                for j in range(k + 1, n):
                    x = f"X{k}_{j}_{i}"
                    for r in range(f):
                        write(f" s{k}_{j}_{i}_{r}: C{j}_{i} - C{k}_{i} - {big_m} {x} - {big_m} Y{j}_{r}"
                              f" - {big_m} Y{k}_{r} >= {p[j][i] - 3 * big_m}\n")
                        write(f" t{k}_{j}_{i}_{r}: C{k}_{i} - C{j}_{i} + {big_m} {x} - {big_m} Y{j}_{r}"
                              f" - {big_m} Y{k}_{r} >= {p[k][i] - 2 * big_m}\n")
        for j in range(n):
            for i in range(m):
                write(f" c{j}_{i}: Cmax - C{j}_{i} >= 0\n")
        if lower is not None:
            write(f" lb: Cmax >= {lower}\n")
        write("Binaries\n")
        names = [f"Y{j}_{r}" for j in range(n) for r in range(f)]
        names += [f"X{k}_{j}_{i}" for i in range(m) for k in range(n) for j in range(k + 1, n)]
        for start in range(0, len(names), 10):
            write(" " + " ".join(names[start:start + 10]) + "\n")
        write("End\n")
    pairs = n * (n - 1) // 2
    constraints += n + n * m + 2 * f * m * pairs + n * m + (lower is not None)
    return {"binaries": n * f + m * pairs, "continuous": n * m + 1, "constraints": constraints}


def _prepare(path: str | Path, n_factories: int, engine: str):
    instance = read_instance(path, n_factories)
    if not engine.endswith("_ub"):
        return instance, {}, None
    from .heuristic import apply_horizon
    instance, heuristic = apply_horizon(instance)
    return instance, heuristic, instance.horizon


def dump_lp(path: str | Path, n_factories: int, out_dir: str | Path) -> Path:
    instance = read_instance(path, n_factories)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{instance.name}_naderi2015.lp"
    write_lp(instance, target, serial_horizon(instance), instance.lower_bound if config.LB_STOP else None)
    return target


def decode(instance: Instance, values: Dict[str, float]) -> Tuple[List[List[int]], List[int]]:
    factory_score: Dict[int, Dict[int, float]] = {}
    completion: Dict[Tuple[int, int], float] = {}
    for name, value in values.items():
        match = VALUE.match(name)
        if not match:
            continue
        first, second = int(match.group(2)), int(match.group(3))
        if match.group(1) == "Y":
            factory_score.setdefault(first, {})[second] = value
        else:
            completion[(first, second)] = value
    factories = [max(range(instance.n_factories), key=lambda r: factory_score.get(j, {}).get(r, 0.0))
                 for j in range(instance.n_jobs)]
    order = sorted(((completion[(j, operation.machine)] - operation.duration, j, operation.index)
                    for j, job in enumerate(instance.jobs) for operation in job))
    starts = [[0] * instance.n_machines for _ in range(instance.n_jobs)]
    job_ready = [0] * instance.n_jobs
    next_index = [0] * instance.n_jobs
    machine_ready: Dict[Tuple[int, int], int] = {}
    for _, j, index in order:
        if index != next_index[j]:
            raise RuntimeError(f"completion times break the route of job {j}")
        operation = instance.jobs[j][index]
        key = (factories[j], operation.machine)
        start = max(job_ready[j], machine_ready.get(key, 0))
        starts[j][index] = start
        job_ready[j] = machine_ready[key] = start + operation.duration
        next_index[j] += 1
    return starts, factories


def _cplex_executable() -> str:
    explicit = os.environ.get("DJSP_CPLEX_EXE")
    if explicit:
        return explicit
    studio = config.CPLEX_STUDIO
    if studio is None:
        raise RuntimeError("CPLEX Optimization Studio not found: set DJSP_CPLEX_STUDIO or DJSP_CPLEX_EXE")
    if os.name == "nt":
        return str(studio / "cplex" / "bin" / "x64_win64" / "cplex.exe")
    platform_dir = "x86-64_osx" if sys.platform == "darwin" else "x86-64_linux"
    return str(studio / "cplex" / "bin" / platform_dir / "cplex")


def _read_sol(path: Path) -> Tuple[float | None, Dict[str, float]]:
    root = ElementTree.parse(path).getroot()
    header = root.find("header")
    objective = float(header.get("objectiveValue")) if header is not None and header.get("objectiveValue") else None
    values = {}
    variables = root.find("variables")
    if variables is not None:
        for variable in variables:
            name = variable.get("name", "")
            if name[:1] in ("Y", "C"):
                values[name] = float(variable.get("value"))
    return objective, values


def _incumbent_number(path: Path) -> int:
    digits = re.findall(r"\d+", path.stem)
    return int(digits[-1]) if digits else 0


def _solve_cplex(work: Path, left: float, on_incumbent: Callable, cutoff: int | None) -> Dict[str, object]:
    for stale in list(work.glob("incumbent*.sol")) + [work / "final.sol"]:
        stale.unlink(missing_ok=True)
    commands = ["set logfile *", "read model.lp", f"set timelimit {left:.3f}", "set parallel 1",
                "set mip tolerances mipgap 0", f"set mip tolerances absmipgap {ABSOLUTE_GAP}",
                "set output writelevel 3", "set output intsolfileprefix incumbent", "set workdir ."]
    threads = _threads()
    if threads:
        commands.append(f"set threads {threads}")
    if cutoff is not None:
        commands.append(f"set mip tolerances uppercutoff {cutoff}")
    commands += ["optimize", "write final.sol", "quit"]
    log_path = work / "cplex_run.log"
    seen = set()
    launched = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([_cplex_executable(), "-c", *commands], cwd=work, stdout=log,
                                   stderr=subprocess.STDOUT)
        try:
            while True:
                finished = process.poll() is not None
                for path in sorted(work.glob("incumbent*.sol"), key=_incumbent_number):
                    if path.name in seen:
                        continue
                    try:
                        objective, values = _read_sol(path)
                    except (ElementTree.ParseError, OSError):
                        if finished:
                            seen.add(path.name)
                            continue
                        break
                    seen.add(path.name)
                    on_incumbent(values, objective, None, path.stat().st_mtime - launched)
                    path.unlink(missing_ok=True)
                if finished:
                    break
                time.sleep(0.5)
        except BaseException:
            process.kill()
            process.wait()
            raise
    text = log_path.read_text(encoding="utf-8", errors="replace")
    final = work / "final.sol"
    if final.is_file():
        objective, values = _read_sol(final)
        on_incumbent(values, objective, None, None)
    for leftover in [final, work / "cplex.log"] + list(work.glob("incumbent*.sol")):
        leftover.unlink(missing_ok=True)
    version = re.search(r"Interactive Optimizer (\S+)", text)
    status = re.findall(r"^MIP - (.+?)(?::|\.\s*$)", text, re.MULTILINE)
    bound = re.findall(r"Current MIP best bound =\s*(\S+)", text)
    status_text = status[-1].strip() if status else "Unknown"
    return {"status": status_text, "optimal": status_text.startswith("Integer optimal"),
            "infeasible": status_text.startswith("Integer infeasible"),
            "bound": float(bound[-1]) if bound else None,
            "version": f"CPLEX {version.group(1)}" if version else "CPLEX", "exit_code": process.returncode}


def _solve_gurobi(work: Path, total_started: float, time_limit: float, on_incumbent: Callable,
                  cutoff: int | None) -> Dict[str, object]:
    import gurobipy as gp
    from gurobipy import GRB

    env = gp.Env(empty=True)
    env.setParam("LogToConsole", 0)
    env.setParam("LogFile", str(work / "gurobi.log"))
    env.start()
    try:
        model = gp.read(str(work / "model.lp"), env)
        model.Params.TimeLimit = max(1.0, time_limit - (time.perf_counter() - total_started))
        model.Params.MIPGap = 0.0
        model.Params.MIPGapAbs = ABSOLUTE_GAP
        threads = _threads()
        if threads:
            model.Params.Threads = threads
        if cutoff is not None:
            model.Params.Cutoff = cutoff
        variables = model.getVars()
        names = model.getAttr("VarName", variables)
        kept = [index for index, name in enumerate(names) if VALUE.match(name)]
        keys = [names[index] for index in kept]
        wanted = [variables[index] for index in kept]
        failure: List[BaseException] = []

        def callback(handle, where):
            if where != GRB.Callback.MIPSOL or failure:
                return
            try:
                values = handle.cbGetSolution(wanted)
                on_incumbent(dict(zip(keys, values)), handle.cbGet(GRB.Callback.MIPSOL_OBJ),
                             handle.cbGet(GRB.Callback.MIPSOL_OBJBND), None)
            except BaseException as error:
                failure.append(error)
                handle.terminate()

        model.optimize(callback)
        if failure:
            raise failure[0]
        if model.SolCount:
            on_incumbent(dict(zip(keys, model.getAttr("X", wanted))), model.ObjVal, None, None)
        try:
            bound = float(model.ObjBound)
        except gp.GurobiError:
            bound = None
        codes = {getattr(GRB, name): name for name in ("OPTIMAL", "INFEASIBLE", "TIME_LIMIT", "INTERRUPTED",
                                                        "MEM_LIMIT", "SUBOPTIMAL", "NUMERIC", "INF_OR_UNBD", "CUTOFF")}
        status = codes.get(model.Status, str(model.Status))
        result = {"status": status, "optimal": model.Status == GRB.OPTIMAL,
                  "infeasible": model.Status == GRB.INFEASIBLE, "bound": bound,
                  "version": "Gurobi " + ".".join(str(part) for part in gp.gurobi.version()),
                  "nodes": float(model.NodeCount)}
        model.dispose()
        return result
    finally:
        env.dispose()


def solve_instance_ilp(
    path: str | Path,
    n_factories: int,
    engine: str,
    time_limit: float = config.TIME_LIMIT,
    checkpoint: Callable[[SolveResult], None] | None = None,
    work_dir: str | Path | None = None,
) -> SolveResult:
    total_started = time.perf_counter()
    instance, heuristic, cutoff = _prepare(path, n_factories, engine)
    big_m = serial_horizon(instance)
    lower = instance.lower_bound if config.LB_STOP else None
    work = Path(work_dir) if work_dir else Path.cwd()
    work.mkdir(parents=True, exist_ok=True)

    build_started = time.perf_counter()
    size = write_lp(instance, work / "model.lp", big_m, lower)
    build_time = time.perf_counter() - build_started

    base_data: Dict[str, object] = {
        "instance": instance.name,
        "source": str(instance.source),
        "engine": engine,
        "solver": SOLVERS[engine],
        "model": MODEL,
        "big_M": big_m,
        "threads": config.ILP_THREADS,
        "cpu_count": os.cpu_count(),
        "jobs": instance.n_jobs,
        "machines": instance.n_machines,
        "factories": instance.n_factories,
        "operations": len(instance.operations),
        "UB": cutoff,
        "time_limit": time_limit,
        "trivial_LB": instance.lower_bound,
        "lb_stop": bool(config.LB_STOP),
        "binaries": size["binaries"],
        "continuous": size["continuous"],
    }
    if SOLVERS[engine] == "Gurobi":
        base_data["gurobipy"] = package_version("gurobipy")
    else:
        base_data["cplex_studio"] = str(config.CPLEX_STUDIO)
    base_data.update(heuristic)

    state = {"starts": None, "factories": None, "makespan": None, "first": None, "latest": None,
             "iterations": [], "bound": None}

    def snapshot(status: str, proven: bool = False) -> SolveResult:
        data = dict(base_data)
        bound = math.ceil(state["bound"] - EPSILON) if state["bound"] is not None else 0
        data.update({
            "status": status,
            "makespan": state["makespan"],
            "lower_bound": state["makespan"] if proven else max(instance.lower_bound, bound),
            "optimal_by": (("trivial_LB" if config.LB_STOP and state["makespan"] == instance.lower_bound else "ILP")
                           if proven else None),
            "factory_makespans": (factory_makespans(instance, state["starts"], state["factories"])
                                  if state["starts"] is not None else None),
            "vars": size["binaries"] + size["continuous"],
            "clauses": size["constraints"],
            "build_time": build_time,
            "solve_time": time.perf_counter() - total_started,
            "time_first_sat": state["first"] if state["first"] is not None else "",
            "time_latest_sat": state["latest"] if state["latest"] is not None else "",
            "time_unsat": "",
            "iterations": state["iterations"],
        })
        return SolveResult(instance=instance, data=data, starts=state["starts"], factories=state["factories"])

    solve_started = time.perf_counter()

    def on_incumbent(values: Dict[str, float], objective: float | None, bound: float | None,
                     offset: float | None) -> None:
        if bound is not None and abs(bound) < 1e99:
            state["bound"] = max(state["bound"] or bound, bound)
        starts, factories = decode(instance, values)
        makespan = max(factory_makespans(instance, starts, factories))
        if state["makespan"] is not None and makespan >= state["makespan"]:
            return
        now = time.perf_counter() - total_started
        elapsed = min(now, solve_started - total_started + offset) if offset is not None else now
        state.update({"starts": starts, "factories": factories, "makespan": makespan, "latest": elapsed})
        if state["first"] is None:
            state["first"] = elapsed
        state["iterations"].append({"status": "SAT", "makespan": makespan, "objective": objective,
                                    "bound": bound if bound is not None and abs(bound) < 1e99 else "",
                                    "elapsed": elapsed})
        print(f"[{instance.name}] {SOLVERS[engine]} makespan={makespan} elapsed={elapsed:.2f}s", flush=True)
        if checkpoint is not None:
            checkpoint(snapshot("RUNNING"))

    try:
        if SOLVERS[engine] == "Gurobi":
            outcome = _solve_gurobi(work, total_started, time_limit, on_incumbent, cutoff)
        else:
            left = max(1.0, time_limit - (time.perf_counter() - total_started))
            outcome = _solve_cplex(work, left, on_incumbent, cutoff)
    finally:
        (work / "model.lp").unlink(missing_ok=True)

    if outcome["bound"] is not None and abs(outcome["bound"]) < 1e99:
        state["bound"] = max(state["bound"] or outcome["bound"], outcome["bound"])
    if state["starts"] is not None:
        proven = bool(outcome["optimal"]) or (
            state["bound"] is not None and state["makespan"] <= math.ceil(state["bound"] - EPSILON))
        result = snapshot("OPTIMAL" if proven else "FEASIBLE", proven)
    else:
        result = snapshot("UNSAT" if outcome["infeasible"] else "TIMEOUT")
    result.data["solver"] = outcome["version"]
    result.data["solver_status"] = outcome["status"]
    if "nodes" in outcome:
        result.data["nodes"] = outcome["nodes"]
    return result
