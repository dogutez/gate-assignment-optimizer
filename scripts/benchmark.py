"""Benchmark both formulations on the preset instances and print a Markdown table.

Usage (run from the repo root):
    python -m scripts.benchmark                  # one run per case
    python -m scripts.benchmark --repeat 3       # median solve time over 3 runs

Every solution is verified with the independent checker before it is reported.
"""

import argparse
import platform
import statistics
from importlib.metadata import version

from app.checker import find_violations, total_cost
from app.model import compute_kpis, solve
from app.schemas import Instance
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the gate assignment model.")
    parser.add_argument("--time-limit", type=float, default=120.0, help="seconds per solve")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (median time)")
    args = parser.parse_args()

    print(
        f"HiGHS {version('highspy')}, Pyomo {version('pyomo')}, Python "
        f"{platform.python_version()}, {cpu_name()}, time limit {args.time_limit:.0f} s, "
        f"median of {args.repeat} run(s)\n"
    )
    print(
        "| Instance | Flights | Gates | Formulation | Variables | Constraints "
        "| Build (s) | Solve (s) | Status | MIP gap | Apron | Checker |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")

    for name in PRESETS:
        instance = Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())
        for formulation in FORMULATIONS:
            runs = [
                solve(instance, args.time_limit, formulation=formulation)
                for _ in range(args.repeat)
            ]
            result = runs[-1]
            solve_time = statistics.median(r.solve_time_s for r in runs)

            if result.status == "no_solution":
                gap = apron = checker = "-"
            else:
                problems = find_violations(instance, result.assignment)
                cost_matches = abs(total_cost(instance, result.assignment) - result.objective) <= (
                    1e-6 * abs(result.objective)
                )
                checker = "OK" if not problems and cost_matches else "FAIL"
                gap = f"{result.mip_gap:.3%}"
                apron = str(compute_kpis(instance, result.assignment).apron_count)

            print(
                f"| {name} | {len(instance.flights)} | {len(instance.gates)} | {formulation} "
                f"| {result.n_variables:,} | {result.n_constraints:,} "
                f"| {result.build_time_s:.2f} | {solve_time:.2f} | {result.status} "
                f"| {gap} | {apron} | {checker} |",
                flush=True,
            )


if __name__ == "__main__":
    main()
