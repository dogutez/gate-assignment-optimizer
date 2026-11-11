"""Benchmark solvers and formulations on the preset instances; print a Markdown table.

Usage (run from the repo root):
    python -m scripts.benchmark                          # HiGHS, one run per case
    python -m scripts.benchmark --solver all --repeat 3  # every solver, median of 3
    python -m scripts.benchmark --solver gurobi --objective-scale 1  # unscaled objective

A case the solver cannot run (not installed, or above its license size limit) is
reported as a row, not skipped. Every solution is verified with the independent
checker before it is reported.
"""

import argparse
import platform
import statistics
from importlib.metadata import version

from app.checker import find_violations, total_cost
from app.model import OBJECTIVE_SCALE, compute_kpis, solve
from app.schemas import Instance
from app.solvers import SOLVERS, SolverUnavailableError, is_available
from data.generate import INSTANCES_DIR, PRESETS

FORMULATIONS = ["clique", "pairwise"]


def cpu_name() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def unavailable_reason(error: SolverUnavailableError) -> str:
    text = str(error)
    if "too large" in text:
        return "license size limit"
    if "not installed" in text:
        return "not installed"
    return "unavailable"


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the gate assignment model.")
    parser.add_argument("--solver", choices=[*SOLVERS, "all"], default="highs")
    parser.add_argument("--time-limit", type=float, default=120.0, help="seconds per solve")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (median time)")
    parser.add_argument(
        "--objective-scale",
        type=float,
        default=OBJECTIVE_SCALE,
        help="factor on the objective as the solver sees it (default: pax-km)",
    )
    args = parser.parse_args()
    solvers = list(SOLVERS) if args.solver == "all" else [args.solver]

    print(
        f"Pyomo {version('pyomo')}, Python {platform.python_version()}, {cpu_name()}, "
        f"time limit {args.time_limit:.0f} s, median of {args.repeat} run(s), "
        f"objective scale {args.objective_scale:g}\n"
    )
    print(
        "| Solver | Instance | Formulation | Variables | Constraints | Build (s) "
        "| Solve (s) | Status | MIP gap | Nodes | Apron | Checker |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")

    for solver in solvers:
        if not is_available(solver):
            print(f"| {solver} | - | - | - | - | - | - | not installed | - | - | - | - |")
            continue
        for name in PRESETS:
            instance = Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())
            for formulation in FORMULATIONS:
                try:
                    runs = [
                        solve(
                            instance,
                            args.time_limit,
                            formulation=formulation,
                            solver=solver,
                            objective_scale=args.objective_scale,
                        )
                        for _ in range(args.repeat)
                    ]
                except SolverUnavailableError as e:
                    print(
                        f"| {solver} | {name} | {formulation} | - | - | - | - "
                        f"| {unavailable_reason(e)} | - | - | - | - |",
                        flush=True,
                    )
                    continue
                result = runs[-1]
                solve_time = statistics.median(r.solve_time_s for r in runs)

                if result.status == "no_solution":
                    gap = apron = checker = "-"
                else:
                    problems = find_violations(instance, result.assignment)
                    recomputed = total_cost(instance, result.assignment)
                    cost_matches = abs(recomputed - result.objective) <= 1e-6 * abs(
                        result.objective
                    )
                    checker = "OK" if not problems and cost_matches else "FAIL"
                    gap = f"{result.mip_gap:.3%}"
                    apron = str(compute_kpis(instance, result.assignment).apron_count)

                print(
                    f"| {result.solver} {result.solver_version} | {name} | {formulation} "
                    f"| {result.n_variables:,} | {result.n_constraints:,} "
                    f"| {result.build_time_s:.2f} | {solve_time:.2f} | {result.status} "
                    f"| {gap} | {result.nodes if result.nodes is not None else '-'} "
                    f"| {apron} | {checker} |",
                    flush=True,
                )


if __name__ == "__main__":
    main()
