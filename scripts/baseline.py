"""Compare greedy, greedy + local search and the MIP on the preset instances.

Usage (run from the repo root):
    python -m scripts.baseline

Prints a Markdown table. Every solution is verified with the independent checker,
and every cost is recomputed by the checker, not taken from the method itself.
"""

import argparse
import time

from app.checker import find_violations, total_cost
from app.heuristic import greedy, local_search
from app.model import compute_kpis, solve
from app.schemas import Instance
from app.solvers import SOLVERS
from data.generate import INSTANCES_DIR, PRESETS


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare heuristics with the MIP.")
    parser.add_argument("--time-limit", type=float, default=120.0, help="MIP seconds per solve")
    parser.add_argument("--solver", choices=SOLVERS, default="highs", help="MIP solver")
    args = parser.parse_args()

    print(
        "| Instance | Method | Cost (pax·m) | Above MIP | Apron | Avg. walk (m) "
        "| Time (s) | Checker |"
    )
    print("|---|---|---|---|---|---|---|---|")

    for name in PRESETS:
        instance = Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())

        start = time.perf_counter()
        greedy_solution = greedy(instance)
        greedy_time = time.perf_counter() - start

        start = time.perf_counter()
        ls_solution = local_search(instance, greedy_solution)
        ls_time = greedy_time + time.perf_counter() - start  # includes the greedy start

        mip = solve(instance, args.time_limit, solver=args.solver)
        if mip.status == "no_solution":
            raise SystemExit(f"{name}: MIP found no solution within {args.time_limit} s")
        mip_cost = total_cost(instance, mip.assignment)

        rows = [
            ("greedy", greedy_solution, greedy_time),
            ("greedy + local search", ls_solution, ls_time),
            (
                f"MIP {args.solver} ({mip.status})",
                mip.assignment,
                mip.build_time_s + mip.solve_time_s,
            ),
        ]
        for method, assignment, seconds in rows:
            cost = total_cost(instance, assignment)
            kpis = compute_kpis(instance, assignment)
            checker = "OK" if not find_violations(instance, assignment) else "FAIL"
            print(
                f"| {name} | {method} | {cost:,.0f} | {cost / mip_cost - 1:.1%} "
                f"| {kpis.apron_count} | {kpis.avg_walk_per_pax_m:.0f} "
                f"| {seconds:.2f} | {checker} |",
                flush=True,
            )


if __name__ == "__main__":
    main()
