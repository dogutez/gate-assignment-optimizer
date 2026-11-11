"""Solve one instance and print model size, solver status and KPIs.

Usage (run from the repo root):
    python -m scripts.solve data/instances/small.json --show
    python -m scripts.solve data/instances/large.json --time-limit 30 --formulation pairwise
    python -m scripts.solve data/instances/large.json --solver gurobi --threads 4
"""

import argparse
from pathlib import Path

from app.checker import find_violations, total_cost
from app.model import compute_kpis, solve
from app.schemas import APRON, Instance
from app.solvers import SOLVERS, SolverUnavailableError


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def print_schedule(instance: Instance, assignment: dict[str, str]) -> None:
    """One line per gate (plus the apron) with its flights in time order."""
    flights = sorted(instance.flights, key=lambda f: f.arrival)
    rows = [(g.id, f"{g.max_size} {g.walk_dist:>4} m") for g in instance.gates]
    rows.append((APRON, ""))
    for gate_id, label in rows:
        items = [
            f"{f.id}[{f.aircraft_size}] {hhmm(f.arrival)}-{hhmm(f.departure)}"
            for f in flights
            if assignment[f.id] == gate_id
        ]
        print(f"  {gate_id:<6} {label:<9} | " + ", ".join(items))


def main() -> None:
    parser = argparse.ArgumentParser(description="Solve a gate assignment instance.")
    parser.add_argument("instance", type=Path, help="instance JSON file")
    parser.add_argument("--time-limit", type=float, default=60.0, help="seconds")
    parser.add_argument("--gap", type=float, default=1e-4, help="relative MIP gap")
    parser.add_argument("--formulation", choices=["clique", "pairwise"], default="clique")
    parser.add_argument("--solver", choices=SOLVERS, default="highs")
    parser.add_argument("--threads", type=int, default=None, help="default: solver's choice")
    parser.add_argument("--show", action="store_true", help="print the gate schedule")
    args = parser.parse_args()

    instance = Instance.model_validate_json(args.instance.read_text())
    try:
        result = solve(
            instance,
            args.time_limit,
            args.gap,
            args.formulation,
            solver=args.solver,
            threads=args.threads,
        )
    except SolverUnavailableError as e:
        raise SystemExit(f"error: {e}") from None

    print(
        f"instance     : {instance.name} ({len(instance.flights)} flights, "
        f"{len(instance.gates)} gates)"
    )
    print(f"solver       : {result.solver} {result.solver_version}")
    print(f"formulation  : {args.formulation}")
    print(f"model size   : {result.n_variables} variables, {result.n_constraints} constraints")
    print(f"times        : build {result.build_time_s:.2f} s, solve {result.solve_time_s:.2f} s")
    print(f"status       : {result.status} ({result.termination})")
    if result.status == "no_solution":
        return

    kpis = compute_kpis(instance, result.assignment)
    print(f"objective    : {result.objective:,.0f}")
    print(f"best bound   : {result.best_bound:,.0f}")
    print(f"MIP gap      : {result.mip_gap:.4%}")
    print(f"B&B nodes    : {result.nodes}")
    print(f"apron        : {kpis.apron_count} of {kpis.n_flights} flights")
    print(
        f"walking      : {kpis.total_walk_pax_m:,.0f} pax-m "
        f"({kpis.avg_walk_per_pax_m:.0f} m per passenger)"
    )
    print(f"utilization  : {kpis.gate_utilization:.1%}")

    problems = find_violations(instance, result.assignment)
    recomputed = total_cost(instance, result.assignment)
    print(
        f"checker      : {'OK' if not problems else f'{len(problems)} violations'}, "
        f"recomputed cost {recomputed:,.0f}"
    )
    for problem in problems:
        print(f"  - {problem}")
    if args.show:
        print_schedule(instance, result.assignment)


if __name__ == "__main__":
    main()
