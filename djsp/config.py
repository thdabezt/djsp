from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def _find_cplex_studio() -> Path | None:
    candidates = [os.environ.get("DJSP_CPLEX_STUDIO"), os.environ.get("CPLEX_STUDIO_DIR222"),
                  "/opt/ibm/ILOG/CPLEX_Studio222", str(Path.home() / "IBM" / "ILOG" / "CPLEX_Studio222"),
                  r"C:\Program Files\IBM\ILOG\CPLEX_Studio222"]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


DATASET_DIR = _env_path("DJSP_DATASET_DIR", PROJECT_ROOT / "datasets")
RESULTS_DIR = _env_path("DJSP_RESULTS_DIR", PROJECT_ROOT / "results")
FACTORIES = (2, 3, 4)

HEURISTIC_METHOD = "assign"
HEURISTIC_TIME_LIMIT = 5.0
HEURISTIC_SEED = 0
HEURISTIC_CACHE = DATASET_DIR / "heuristic_ub.csv"
HEURISTIC_HORIZON_ESTIMATE = 1.5

TIME_LIMIT = 3600.0
KILL_GRACE = 120.0

PARALLEL = 4
MEMORY_BUDGET_MB = None
SAT_BYTES_PER_CLAUSE = 240
MEMORY_LIMIT_MB = None
MAX_CLAUSES = 140_000_000
CPU_PINNING = "core"

SAT_BINARY = _env_path("DJSP_SAT_BINARY", PROJECT_ROOT / "build" / f"djsp_sat{'.exe' if os.name == 'nt' else ''}")

CPLEX_STUDIO = _find_cplex_studio()
CP_WORKERS = 2


def apply_settings(settings: dict) -> None:
    module = globals()
    for name, value in settings.items():
        if not name.isupper() or name not in module or callable(module[name]):
            raise KeyError(f"unknown setting {name!r}")
        current = module[name]
        if (isinstance(current, Path) or name in ("CPLEX_STUDIO", "HEURISTIC_CACHE")) and value is not None:
            value = Path(value)
        elif isinstance(current, tuple) and isinstance(value, list):
            value = tuple(value)
        module[name] = value
    if "DATASET_DIR" in settings and "HEURISTIC_CACHE" not in settings:
        module["HEURISTIC_CACHE"] = module["DATASET_DIR"] / "heuristic_ub.csv"


if os.environ.get("DJSP_SETTINGS"):
    import json

    apply_settings(json.loads(os.environ["DJSP_SETTINGS"]))
