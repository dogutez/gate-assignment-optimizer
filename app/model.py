"""Gate assignment MIP: Pyomo formulation and HiGHS solve.

    min  sum_{(i,k) in FG} pax_i * walk_k * x[i,k]
       + sum_i (apron_penalty + pax_i * apron_walk_dist) * a[i]
    s.t. sum_{k:(i,k) in FG} x[i,k] + a[i] = 1          for every flight i
         no two conflicting flights share a gate       (pairwise or clique form)
         x, a binary

FG holds only compatible (flight, gate) pairs, so incompatible assignments
have no variable at all instead of a constraint forcing them to zero.
"""

import time
from dataclasses import dataclass
from typing import Literal

import pyomo.environ as pyo
from pyomo.contrib.solver.common.results import SolutionStatus
from pyomo.contrib.solver.solvers.highs import Highs

from app.schemas import APRON, SIZE_RANK, Flight, Gate, Instance, Kpis

Formulation = Literal["pairwise", "clique"]

# The solver sees the objective in passenger-kilometers instead of passenger-meters.
# With values around 1e8 HiGHS stalls at the root node (large instance: 14% gap after
# 60 s); scaled down it reaches ~0.001% in 30 s. Results are reported in pax-meters again.
OBJECTIVE_SCALE = 1e-3


@dataclass
class SolveResult:
    status: str  # "optimal", "feasible" (stopped at a limit with a solution) or "no_solution"
    termination: str  # raw termination condition reported through Pyomo
    objective: float | None
    best_bound: float | None  # dual bound: no solution can be better than this
    mip_gap: float | None  # (objective - best_bound) / objective
    assignment: dict[str, str]  # flight id -> gate id or APRON
    n_variables: int
    n_constraints: int
    build_time_s: float
    solve_time_s: float


def is_compatible(flight: Flight, gate: Gate) -> bool:
    return SIZE_RANK[flight.aircraft_size] <= SIZE_RANK[gate.max_size]


def overlaps(f1: Flight, f2: Flight, buffer: int) -> bool:
    """True if f1 and f2 cannot use the same gate.

    A flight occupies its gate from arrival until departure + buffer.
    """
    return f1.arrival < f2.departure + buffer and f2.arrival < f1.departure + buffer


def conflict_pairs(instance: Instance) -> list[tuple[str, str]]:
    """All pairs of flights whose gate occupation overlaps."""
    flights = instance.flights
    pairs = []
    for idx, f1 in enumerate(flights):
        for f2 in flights[idx + 1 :]:
            if overlaps(f1, f2, instance.buffer):
                pairs.append((f1.id, f2.id))
    return pairs


def conflict_cliques(instance: Instance) -> list[list[str]]:
    """Groups of flights that all occupy a gate at the same moment.

    At the arrival time of flight j, every flight i with
    arrival_i <= arrival_j < departure_i + buffer is on the ground. They
    pairwise conflict, so at most one of them can use any given gate.

    For time intervals, two overlapping flights are always both on the ground
    at the later of their two arrival times, so these groups cover every
    conflicting pair. Groups contained in a larger group are dropped.
    """
    groups = set()
    for fj in instance.flights:
        group = frozenset(
            fi.id
            for fi in instance.flights
            if fi.arrival <= fj.arrival < fi.departure + instance.buffer
        )
        if len(group) >= 2:
            groups.add(group)
    maximal = [g for g in groups if not any(g < other for other in groups)]
    return sorted(sorted(g) for g in maximal)


def build_model(instance: Instance, formulation: Formulation = "clique") -> pyo.ConcreteModel:
    flights = {f.id: f for f in instance.flights}
    gates = {g.id: g for g in instance.gates}
    compatible_pairs = [
        (i, k) for i in flights for k in gates if is_compatible(flights[i], gates[k])
    ]
    compatible = set(compatible_pairs)
    gates_of = {i: [k for k in gates if (i, k) in compatible] for i in flights}

    m = pyo.ConcreteModel(name=instance.name)

    # Sets
    m.F = pyo.Set(initialize=list(flights), doc="flights")
    m.G = pyo.Set(initialize=list(gates), doc="gates")
    m.FG = pyo.Set(dimen=2, initialize=compatible_pairs, doc="compatible (flight, gate) pairs")

    # Parameters
    m.pax = pyo.Param(m.F, initialize={i: f.pax for i, f in flights.items()})
    m.walk = pyo.Param(m.G, initialize={k: g.walk_dist for k, g in gates.items()})
    m.apron_penalty = pyo.Param(initialize=instance.apron_penalty)
    m.apron_walk = pyo.Param(initialize=instance.apron_walk_dist)

    # Variables
    m.x = pyo.Var(m.FG, domain=pyo.Binary, doc="flight i at gate k")
    m.a = pyo.Var(m.F, domain=pyo.Binary, doc="flight i at the apron")

    # Objective: walking at gates + fixed apron penalty + apron passengers' bus transfer.
    # Without the pax term, apron passengers would cost nothing and the model would
    # send the fullest flights to the apron.
    m.total_cost = pyo.Objective(
        expr=OBJECTIVE_SCALE
        * (
            sum(m.pax[i] * m.walk[k] * m.x[i, k] for (i, k) in m.FG)
            + sum((m.apron_penalty + m.pax[i] * m.apron_walk) * m.a[i] for i in m.F)
        ),
        sense=pyo.minimize,
    )

    # Each flight gets exactly one gate or the apron.
    def assign_rule(m, i):
        return sum(m.x[i, k] for k in gates_of[i]) + m.a[i] == 1

    m.assign = pyo.Constraint(m.F, rule=assign_rule)

    # No two conflicting flights at the same gate.
    if formulation == "pairwise":
        # x[i,k] + x[j,k] <= 1 for every conflicting pair (i, j) and shared gate k
        index = [
            (i, j, k)
            for (i, j) in conflict_pairs(instance)
            for k in gates
            if (i, k) in compatible and (j, k) in compatible
        ]
        m.NO_OVERLAP = pyo.Set(dimen=3, initialize=index)
        m.no_overlap = pyo.Constraint(
            m.NO_OVERLAP, rule=lambda m, i, j, k: m.x[i, k] + m.x[j, k] <= 1
        )
    elif formulation == "clique":
        # sum_{i in clique c} x[i,k] <= 1 for every clique c and gate k
        cliques = conflict_cliques(instance)
        members = {}  # (clique index, gate) -> compatible flights in that clique
        for c, group in enumerate(cliques):
            for k in gates:
                fits = [i for i in group if (i, k) in compatible]
                if len(fits) >= 2:
                    members[c, k] = fits
        m.NO_OVERLAP = pyo.Set(dimen=2, initialize=list(members))
        m.no_overlap = pyo.Constraint(
            m.NO_OVERLAP, rule=lambda m, c, k: sum(m.x[i, k] for i in members[c, k]) <= 1
        )
    else:
        raise ValueError(f"unknown formulation: {formulation}")

    return m


def count_variables(m: pyo.ConcreteModel) -> int:
    return sum(1 for _ in m.component_data_objects(pyo.Var, active=True))


def count_constraints(m: pyo.ConcreteModel) -> int:
    return sum(1 for _ in m.component_data_objects(pyo.Constraint, active=True))


def extract_assignment(m: pyo.ConcreteModel) -> dict[str, str]:
    """Read the solution values. Binaries come back as floats like 0.9999999."""
    assignment = {i: APRON for i in m.F}
    for i, k in m.FG:
        if pyo.value(m.x[i, k]) > 0.5:
            assignment[i] = k
    return assignment


def solve(
    instance: Instance,
    time_limit_s: float = 60.0,
    mip_gap: float = 1e-4,
    formulation: Formulation = "clique",
) -> SolveResult:
    start = time.perf_counter()
    m = build_model(instance, formulation)
    build_time = time.perf_counter() - start

    start = time.perf_counter()
    results = Highs().solve(
        m,
        time_limit=time_limit_s,
        rel_gap=mip_gap,
        load_solutions=False,  # we load them ourselves, only if a solution exists
        raise_exception_on_nonoptimal_result=False,  # a time-limit stop is not an error
    )
    solve_time = time.perf_counter() - start

    common = dict(
        termination=results.termination_condition.name,
        n_variables=count_variables(m),
        n_constraints=count_constraints(m),
        build_time_s=build_time,
        solve_time_s=solve_time,
    )

    if results.solution_status not in (SolutionStatus.optimal, SolutionStatus.feasible):
        return SolveResult(
            status="no_solution",
            objective=None,
            best_bound=None,
            mip_gap=None,
            assignment={},
            **common,
        )

    results.solution_loader.load_vars()
    objective = results.incumbent_objective / OBJECTIVE_SCALE
    bound = (
        results.objective_bound / OBJECTIVE_SCALE if results.objective_bound is not None else None
    )
    gap = abs(objective - bound) / max(abs(objective), 1e-9) if bound is not None else None
    return SolveResult(
        status="optimal" if results.solution_status == SolutionStatus.optimal else "feasible",
        objective=objective,
        best_bound=bound,
        mip_gap=gap,
        assignment=extract_assignment(m),
        **common,
    )


def compute_kpis(instance: Instance, assignment: dict[str, str]) -> Kpis:
    walk = {g.id: g.walk_dist for g in instance.gates}
    at_gate = [f for f in instance.flights if assignment[f.id] != APRON]

    total_walk = sum(f.pax * walk[assignment[f.id]] for f in at_gate)
    pax_at_gate = sum(f.pax for f in at_gate)

    # Available time: from the first arrival to the last departure of the day, on every gate.
    horizon = max(f.departure for f in instance.flights) - min(f.arrival for f in instance.flights)
    occupied = sum(f.departure - f.arrival for f in at_gate)

    return Kpis(
        n_flights=len(instance.flights),
        apron_count=len(instance.flights) - len(at_gate),
        total_walk_pax_m=total_walk,
        avg_walk_per_pax_m=total_walk / pax_at_gate if pax_at_gate else 0.0,
        gate_utilization=occupied / (horizon * len(instance.gates)),
    )
