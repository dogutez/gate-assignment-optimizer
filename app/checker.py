"""Independent solution checker.

Verifies an assignment against the instance using plain Python only. It does not
import app.model on purpose: if the model has a bug (a missing constraint, a wrong
conflict test), the checker should not share it.
"""

from app.schemas import APRON, SIZE_RANK, Instance


def find_violations(instance: Instance, assignment: dict[str, str]) -> list[str]:
    """Return a list of human-readable problems. An empty list means the solution is valid."""
    flights = {f.id: f for f in instance.flights}
    gates = {g.id: g for g in instance.gates}
    problems = []

    # 1. Every flight appears exactly once (dict keys are unique, so check the key set).
    missing = set(flights) - set(assignment)
    extra = set(assignment) - set(flights)
    if missing:
        problems.append(f"flights without an assignment: {sorted(missing)}")
    if extra:
        problems.append(f"unknown flights in assignment: {sorted(extra)}")

    # 2. Every target is a known gate or the apron, and the aircraft fits.
    for fid, gid in assignment.items():
        if fid not in flights or gid == APRON:
            continue
        if gid not in gates:
            problems.append(f"{fid}: unknown gate {gid}")
        elif SIZE_RANK[flights[fid].aircraft_size] > SIZE_RANK[gates[gid].max_size]:
            problems.append(
                f"{fid}: size {flights[fid].aircraft_size} does not fit gate {gid} "
                f"(max {gates[gid].max_size})"
            )

    # 3. At each gate, sorted by arrival, the next flight arrives after the previous
    #    one has left and the buffer has passed.
    for gid in gates:
        at_gate = sorted(
            (flights[fid] for fid, g in assignment.items() if g == gid and fid in flights),
            key=lambda f: f.arrival,
        )
        for prev, nxt in zip(at_gate, at_gate[1:], strict=False):
            if nxt.arrival < prev.departure + instance.buffer:
                problems.append(
                    f"gate {gid}: {nxt.id} arrives at {nxt.arrival} but {prev.id} "
                    f"blocks the gate until {prev.departure + instance.buffer}"
                )

    return problems


def total_cost(instance: Instance, assignment: dict[str, str]) -> float:
    """Objective value of an assignment, in passenger-meters, computed from scratch."""
    walk = {g.id: g.walk_dist for g in instance.gates}
    cost = 0.0
    for f in instance.flights:
        gid = assignment[f.id]
        if gid == APRON:
            cost += instance.apron_penalty + f.pax * instance.apron_walk_dist
        else:
            cost += f.pax * walk[gid]
    return cost
