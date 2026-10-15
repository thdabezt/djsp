from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from . import config
from .output import write_error, write_result
from .parser import instance_name
from .system import limit_memory, peak_rss_mb, pin_to

ENGINES = ("sb_on", "sb_off", "cp")


def _summary_lines(row) -> None:
    print(f"s {row.get('status')}", flush=True)
    for key in ("makespan", "lower_bound", "UB", "solve_time", "peak_rss_mb", "vars", "clauses"):
        print(f"r {key} {row.get(key)}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m djsp.worker")
    parser.add_argument("--engine", required=True, choices=ENGINES)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--factories", type=int, required=True)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--time-limit", type=float, default=config.TIME_LIMIT)
    parser.add_argument("--memory-limit-mb", type=float, default=None)
    parser.add_argument("--cpus", default=None)
    parser.add_argument("--dump-model", default=None, metavar="DIR")
    arguments = parser.parse_args(argv)

    if arguments.dump_model:
        if arguments.engine == "cp":
            from .cp_solver import dump_cpo
            target = dump_cpo(arguments.instance, arguments.factories, arguments.dump_model)
        else:
            from .sat_solver import dump_cnf
            target = dump_cnf(arguments.instance, arguments.factories, arguments.engine, arguments.dump_model)
        print(target)
        return 0
    if not arguments.run_dir:
        parser.error("--run-dir is required unless --dump-model is given")

    run_directory = Path(arguments.run_dir)
    name = instance_name(Path(arguments.instance).stem, arguments.factories)
    limited = limit_memory(arguments.memory_limit_mb)
    cpus = [int(cpu) for cpu in arguments.cpus.split(",")] if arguments.cpus else None
    pinned = bool(cpus) and pin_to(cpus)

    def checkpoint(result):
        result.data["peak_rss_mb"] = peak_rss_mb()
        write_result(run_directory, result)

    try:
        if arguments.engine == "cp":
            from .cp_solver import solve_instance_cp
            result = solve_instance_cp(arguments.instance, arguments.factories, arguments.time_limit, checkpoint)
        else:
            from .sat_solver import solve_instance_sat
            result = solve_instance_sat(arguments.instance, arguments.factories, arguments.engine,
                                        arguments.time_limit, checkpoint, work_dir=run_directory / name)
        result.data["peak_rss_mb"] = peak_rss_mb()
        result.data["memory_limit_mb"] = arguments.memory_limit_mb if limited else None
        result.data["cpus"] = cpus if pinned else None
        row = write_result(run_directory, result)
        print(f"[{name}] {row['status']} makespan={row.get('makespan')}", flush=True)
        _summary_lines(row)
        return 0
    except MemoryError as error:
        traceback.print_exc()
        row = write_error(run_directory, name, error)
        row["status"] = "MEMOUT"
        (run_directory / name / "result.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        _summary_lines(row)
        return 1
    except Exception as error:
        traceback.print_exc()
        row = write_error(run_directory, name, error)
        _summary_lines(row)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
