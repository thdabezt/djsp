from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List

from .model import SolveResult


SUMMARY_FIELDS = [
    "instance",
    "jobs",
    "machines",
    "factories",
    "operations",
    "engine",
    "solver",
    "vars",
    "clauses",
    "build_time",
    "solve_time",
    "time_first_sat",
    "time_latest_sat",
    "time_unsat",
    "UB",
    "makespan",
    "trivial_LB",
    "status",
]


def _write_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    for attempt in range(100):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def _operations(result: SolveResult) -> List[Dict[str, int]]:
    if result.starts is None:
        return []
    rows = []
    for job_index, job in enumerate(result.instance.jobs):
        for operation_index, operation in enumerate(job):
            start = result.starts[job_index][operation_index]
            rows.append(
                {
                    "id": operation.id,
                    "job": job_index,
                    "operation": operation_index,
                    "factory": result.factories[job_index],
                    "machine": operation.machine,
                    "start": start,
                    "duration": operation.duration,
                    "end": start + operation.duration,
                }
            )
    return rows


def write_result(run_directory: Path, result: SolveResult) -> Dict[str, object]:
    instance_directory = run_directory / result.instance.name
    instance_directory.mkdir(parents=True, exist_ok=True)

    data = dict(result.data)
    data["result_directory"] = str(instance_directory)
    _write_text(instance_directory / "result.json", json.dumps(data, indent=2))

    data_lines = [
        f"instance={data['instance']}",
        f"status={data['status']}",
        f"jobs={data['jobs']}",
        f"machines={data['machines']}",
        f"factories={data['factories']}",
        f"operations={data['operations']}",
        f"engine={data['engine']}",
        f"solver={data['solver']}",
        f"UB={data['UB']}",
        f"best_makespan={data['makespan'] if data.get('makespan') is not None else ''}",
        f"lower_bound={data.get('lower_bound') if data.get('lower_bound') is not None else ''}",
        f"vars={data['vars']}",
        f"clauses={data['clauses']}",
        f"time_build={data['build_time']}",
        f"time_total={data['solve_time']}",
        f"time_first_sat={data['time_first_sat']}",
        f"time_latest_sat={data['time_latest_sat']}",
        f"time_unsat={data['time_unsat']}",
    ]
    _write_text(instance_directory / "data.txt", "\n".join(data_lines) + "\n")

    log_lines = []
    for iteration in data.get("iterations", []):
        if iteration["status"] == "SAT":
            log_lines.append(
                f"SAT bound={iteration.get('bound', '')} "
                f"makespan={iteration['makespan']} "
                f"iteration_time={iteration.get('iteration_time', 0.0):.6f} "
                f"elapsed={iteration['elapsed']:.6f}"
            )
        elif iteration["status"] == "UNSAT":
            log_lines.append(
                f"UNSAT bound={iteration['bound']} "
                f"proof_time={iteration['iteration_time']:.6f} "
                f"elapsed={iteration['elapsed']:.6f}"
            )
        else:
            log_lines.append(
                f"TIMEOUT bound={iteration['bound']} "
                f"iteration_time={iteration['iteration_time']:.6f} "
                f"elapsed={iteration['elapsed']:.6f}"
            )
    _write_text(instance_directory / "log.txt", "\n".join(log_lines) + ("\n" if log_lines else ""))

    if result.starts is not None:
        raw = {
            "instance": result.instance.name,
            "horizon": result.data.get("horizon", result.instance.horizon),
            "makespan": result.data["makespan"],
            "factory_makespans": result.data.get("factory_makespans"),
            "factories": result.factories,
            "starts": result.starts,
            "operations": _operations(result),
        }
        _write_text(instance_directory / "raw_schedule.json", json.dumps(raw, indent=2))

        lines = []
        for job_index, job in enumerate(result.instance.jobs):
            entries = []
            for operation_index, operation in enumerate(job):
                start = result.starts[job_index][operation_index]
                entries.append(
                    f"O{operation_index}:M{operation.machine},"
                    f"start={start},duration={operation.duration},"
                    f"end={start + operation.duration}"
                )
            lines.append(f"Job {job_index} (F{result.factories[job_index]}): " + " | ".join(entries))
        _write_text(instance_directory / "schedule.txt", "\n".join(lines) + "\n")

    return data


def write_error(run_directory: Path, name: str, error: Exception) -> Dict[str, object]:
    instance_directory = run_directory / name
    instance_directory.mkdir(parents=True, exist_ok=True)
    data = {
        "instance": name,
        "status": "ERROR",
        "error": f"{type(error).__name__}: {error}",
        "result_directory": str(instance_directory),
    }
    _write_text(instance_directory / "result.json", json.dumps(data, indent=2))
    return data


def _key(row: Dict[str, object]):
    name = str(row.get("instance", ""))
    base, _, count = name.rpartition("_")
    try:
        return base, int(count.rstrip("f"))
    except ValueError:
        return name, None


def write_summary(run_directory: Path, rows: Iterable[Dict[str, object]]) -> List[Path]:
    groups: Dict[object, List[Dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(_key(row)[1], []).append(row)
    paths = []
    for factories, members in sorted(groups.items(), key=lambda item: str(item[0])):
        path = run_directory / f"summary_{factories}f.csv"
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in sorted(members, key=lambda r: str(r.get("instance", ""))):
                writer.writerow({field: row.get(field, "") for field in SUMMARY_FIELDS})
        os.replace(temporary, path)
        paths.append(path)
    return paths
