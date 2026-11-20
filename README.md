# SAT-DJSP for Solving Distributed Job Shop Scheduling

SAT-DJSP is a SAT-based exact solver for the **Distributed Job Shop Scheduling Problem (DJSP)** with the makespan objective, using **CaDiCaL** 1.9.5. **CP Optimizer**, **CPLEX** and **Gurobi** models are included for comparison.

---

## Datasets

The instances are located in [datasets](datasets): Taillard `ta01.txt` to `ta80.txt`. With `F` factories, an instance is named `ta01_2f`, `ta01_3f`, `ta01_4f`.

[heuristic_ub.csv](datasets/heuristic_ub.csv) stores the initial upper bound of every instance. A stored bound is used as is; an instance that is not in the file is searched once and added to it.

---

## Build

Requirements: Python 3.10 or newer, a C++17 compiler, `make` and `git`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
make
```

The executable `djsp_sat` will be generated in the `build/` directory. If you want a clean build, instead run:

```bash
make clean && make
```

The SAT engines need nothing else. The other engines are optional:

* **CP Optimizer and CPLEX MILP:** IBM ILOG CPLEX Optimization Studio 22.2. Set `DJSP_CPLEX_STUDIO` if it is not in a default installation directory.
* **Gurobi MILP:** `pip install gurobipy` plus a licence.

---

## Usage

```bash
python -m djsp [instances ...] [options]
```

Without instances, the whole dataset is solved with 2, 3 and 4 factories.

```bash
python -m djsp ta01_2f --engine sb_on --time-limit 3600
python -m djsp ta01-10 --factories 2,3 --engine sb_off,cp
```

---

## Options

### Instances

```
instances
    ta01_2f, ta01 (all factory counts), a range such as ta01-10,
    or paths of Taillard .txt files

--factories <list>
    Factory counts as a comma list (default: 2,3,4)
```

### Engines

```
--engine <engine>
    One engine or a comma list (default: sb_on)

sb_on
    SAT with factory symmetry breaking

sb_off
    SAT without symmetry breaking

sb_domain
    SAT with only the factory domain restriction:
    job j may use factories 0..min(j, F-1)

cp
    CP Optimizer, no initial upper bound

cp_ub
    CP Optimizer with the constraint makespan <= UB

ilp_cplex, ilp_gurobi
    MILP solved by CPLEX or Gurobi, no initial upper bound

ilp_cplex_ub, ilp_gurobi_ub
    The same MILP with the initial upper bound as the solver's cutoff
```

### Bounds

```
--heuristic <assign|random>
    Heuristic that computes the initial upper bound (default: assign)

--heuristic-time <seconds>
    Time given to the heuristic (default: 5)

--lb-stop
    Stop with OPTIMAL when the makespan reaches the trivial lower bound
```

### Resource Limits

```
--time-limit <seconds>
    Time limit per instance (default: 3600)

--memory-mb <int>
    Memory budget shared by the SAT workers (default: 85 % of the RAM)

--memory-limit-mb <int>
    Address-space limit of each worker
```

### Performance

```
--parallel <int>
    Instances solved at a time (default: 4)

--no-pinning
    Do not pin each worker to its own physical core

--seed <int>
    Clause-order seed of the SAT engines (default: 0)

--cp-workers <int|auto>
    CP Optimizer workers (default: 2)

--ilp-threads <int|auto>
    CPLEX and Gurobi MILP threads (default: 2)
```

### Output

```
--results-dir <directory>
    Parent of the run directory (default: results/)

--resume <run directory>
    Complete an interrupted run; finished instances are kept
```

### Print model

```
python -m djsp.worker --engine <engine> --instance <file> --factories <int> --dump-model <directory>
    Writes the model (DIMACS, .cpo or .lp) to the given directory without solving it
```

---

## Output

Each run creates `results/run_<timestamp>/<engine>/` with `summary_2f.csv`, `summary_3f.csv`, `summary_4f.csv` and one directory per instance holding `result.json`, `log.txt`, `schedule.txt` and `raw_schedule.json`.

```
status
    OPTIMAL, FEASIBLE, TIMEOUT (no schedule within the limit),
    SKIPPED (SAT model above 140 million clauses) or MEMOUT

optimal_by
    UNSAT, CP or ILP (the solver's proof), or trivial_LB (--lb-stop)

UB
    Initial upper bound

trivial_LB
    max(longest job, ceil(largest machine load / F))

makespan
    Makespan of the best schedule

solve_time, build_time
    Wall-clock time of building and solving the model

time_first_sat, time_latest_sat, time_unsat
    Elapsed time at the first and last schedule, and the time of the closing proof

vars, clauses
    Model size
```
