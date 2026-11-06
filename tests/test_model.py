import pytest

from app.model import compute_kpis, conflict_cliques, conflict_pairs, solve
from app.schemas import APRON, SIZE_RANK, Instance
from data.generate import INSTANCES_DIR

FORMULATIONS = ["clique", "pairwise"]


def load(name: str) -> Instance:
    return Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())


def flight(fid, arrival, departure, size="M", pax=100):
    return {
        "id": fid,
        "arrival": arrival,
        "departure": departure,
        "aircraft_size": size,
        "pax": pax,
    }


def gate(gid, max_size="L", walk_dist=100):
    return {"id": gid, "max_size": max_size, "walk_dist": walk_dist}


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_small_instance_is_optimal_and_feasible(formulation):
    inst = load("small")
    result = solve(inst, time_limit_s=20, formulation=formulation)
    assert result.status == "optimal"

    flights = {f.id: f for f in inst.flights}
    gates = {g.id: g for g in inst.gates}

    # Each flight is assigned exactly once (to a gate or the apron).
    assert set(result.assignment) == set(flights)

    # All gate assignments are size-compatible.
    for fid, gid in result.assignment.items():
        if gid != APRON:
            assert SIZE_RANK[flights[fid].aircraft_size] <= SIZE_RANK[gates[gid].max_size]

    # No gate has two flights closer than the buffer.
    for gid in gates:
        at_gate = sorted(
            (flights[fid] for fid, g in result.assignment.items() if g == gid),
            key=lambda f: f.arrival,
        )
        for prev, nxt in zip(at_gate, at_gate[1:], strict=False):
            assert nxt.arrival >= prev.departure + inst.buffer


def test_formulations_give_same_optimum():
    inst = load("small")
    clique = solve(inst, formulation="clique")
    pairwise = solve(inst, formulation="pairwise")
    assert clique.objective == pytest.approx(pairwise.objective)


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_more_conflicting_flights_than_gates_uses_apron(formulation):
    inst = Instance(
        flights=[flight("F1", 100, 200), flight("F2", 110, 210), flight("F3", 120, 220)],
        gates=[gate("G1"), gate("G2")],
    )
    result = solve(inst, formulation=formulation)
    assert result.status == "optimal"
    assert list(result.assignment.values()).count(APRON) == 1


def test_single_flight_goes_to_nearest_compatible_gate():
    inst = Instance(
        flights=[flight("F1", 100, 200, size="M")],
        gates=[
            gate("G_SMALL_NEAR", max_size="S", walk_dist=50),
            gate("G_FAR", max_size="L", walk_dist=300),
            gate("G_NEAR", max_size="M", walk_dist=120),
        ],
    )
    assert solve(inst).assignment == {"F1": "G_NEAR"}


def test_no_compatible_gate_means_apron():
    inst = Instance(flights=[flight("F1", 100, 200, size="L")], gates=[gate("G1", max_size="M")])
    assert solve(inst).assignment == {"F1": APRON}


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize(("second_arrival", "expected_apron"), [(215, 0), (214, 1)])
def test_buffer_boundary(formulation, second_arrival, expected_apron):
    # F1 leaves at 200, buffer is 15: the gate is free again at 215.
    inst = Instance(
        buffer=15,
        flights=[flight("F1", 100, 200), flight("F2", second_arrival, 300)],
        gates=[gate("G1")],
    )
    result = solve(inst, formulation=formulation)
    assert list(result.assignment.values()).count(APRON) == expected_apron


def test_cliques_cover_every_conflicting_pair():
    inst = load("medium")
    cliques = [set(c) for c in conflict_cliques(inst)]
    for i, j in conflict_pairs(inst):
        assert any(i in c and j in c for c in cliques)


def test_kpis():
    inst = Instance(
        flights=[flight("F1", 0, 100, pax=10), flight("F2", 0, 100, pax=20)],
        gates=[gate("G1", walk_dist=200), gate("G2", walk_dist=500)],
    )
    kpis = compute_kpis(inst, {"F1": "G2", "F2": APRON})
    assert kpis.apron_count == 1
    assert kpis.total_walk_pax_m == 10 * 500
    assert kpis.avg_walk_per_pax_m == 500
    assert kpis.gate_utilization == pytest.approx(100 / (100 * 2))


def test_fuller_flight_gets_the_gate():
    # Two overlapping flights, one gate: the apron should take the emptier flight.
    inst = Instance(
        flights=[flight("BIG", 100, 200, pax=300), flight("SMALL", 110, 210, pax=50)],
        gates=[gate("G1", walk_dist=400)],
    )
    assert solve(inst).assignment == {"BIG": "G1", "SMALL": APRON}
