"""Greedy construction and local search: a baseline to compare the MIP against.

Neither method can prove how far it is from the optimum; the MIP can.
"""

from app.model import is_compatible, overlaps
from app.schemas import APRON, SIZE_RANK, Flight, Instance


def flight_cost(instance: Instance, flight: Flight, gate_id: str, walk: dict[str, int]) -> float:
    """Cost of one flight at one place, same terms as the MIP objective."""
    if gate_id == APRON:
        return instance.apron_penalty + flight.pax * instance.apron_walk_dist
    return flight.pax * walk[gate_id]


def greedy(instance: Instance) -> dict[str, str]:
    """Flights in arrival order, each to the smallest free gate it fits, nearest first.

    Using the smallest fitting gate keeps large gates free for large aircraft.
    A flight that fits no free gate goes to the apron. Decisions are never revised.

    Two simpler rules did much worse on the large instance: "fullest flight first"
    (29% above the optimum, 121 apron flights) and "arrival order, nearest gate"
    (16%).
    """
    gate_order = sorted(instance.gates, key=lambda g: (SIZE_RANK[g.max_size], g.walk_dist))
    at_gate: dict[str, list[Flight]] = {g.id: [] for g in instance.gates}
    assignment = {}

    for flight in sorted(instance.flights, key=lambda f: f.arrival):
        assignment[flight.id] = APRON
        for gate in gate_order:
            if not is_compatible(flight, gate):
                continue
            if any(overlaps(flight, other, instance.buffer) for other in at_gate[gate.id]):
                continue
            assignment[flight.id] = gate.id
            at_gate[gate.id].append(flight)
            break
    return assignment


def local_search(instance: Instance, assignment: dict[str, str]) -> dict[str, str]:
    """Improve an assignment with two moves until neither finds an improvement.

    - move: put one flight at another gate or at the apron
    - swap: two flights exchange places (a gate flight and an apron flight included)

    Only feasible changes that lower the total cost are accepted, so the result is
    never worse than the start and stops at a local optimum.
    """
    flights = {f.id: f for f in instance.flights}
    gates = {g.id: g for g in instance.gates}
    walk = {g.id: g.walk_dist for g in instance.gates}
    assignment = dict(assignment)
    at_gate: dict[str, set[str]] = {k: set() for k in gates}
    for i, k in assignment.items():
        if k != APRON:
            at_gate[k].add(i)

    def cost(i: str, k: str) -> float:
        return flight_cost(instance, flights[i], k, walk)

    def fits(i: str, k: str, ignore: str | None = None) -> bool:
        """Can flight i use place k, if flight `ignore` were not at k?"""
        if k == APRON:
            return True
        if not is_compatible(flights[i], gates[k]):
            return False
        return not any(
            overlaps(flights[i], flights[j], instance.buffer)
            for j in at_gate[k]
            if j != i and j != ignore
        )

    def place(i: str, k: str) -> None:
        old = assignment[i]
        if old != APRON:
            at_gate[old].discard(i)
        if k != APRON:
            at_gate[k].add(i)
        assignment[i] = k

    places = [*gates, APRON]
    improved = True
    while improved:
        improved = False

        for i in flights:
            current = assignment[i]
            for k in places:
                if k != current and cost(i, k) < cost(i, current) and fits(i, k):
                    place(i, k)
                    current = k
                    improved = True

        ids = list(flights)
        for idx, i in enumerate(ids):
            for j in ids[idx + 1 :]:
                ki, kj = assignment[i], assignment[j]
                if ki == kj:
                    continue
                delta = cost(i, kj) + cost(j, ki) - cost(i, ki) - cost(j, kj)
                if delta < 0 and fits(i, kj, ignore=j) and fits(j, ki, ignore=i):
                    place(i, kj)
                    place(j, ki)
                    improved = True

    return assignment
