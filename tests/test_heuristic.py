import pytest

from app.checker import find_violations, total_cost
from app.heuristic import greedy, local_search
from app.model import solve
from app.schemas import APRON, Flight, Gate, Instance
from data.generate import INSTANCES_DIR


def load(name: str) -> Instance:
    return Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())


@pytest.mark.parametrize("name", ["small", "medium", "large"])
def test_greedy_and_local_search_are_feasible(name):
    instance = load(name)
    start = greedy(instance)
    improved = local_search(instance, start)

    assert find_violations(instance, start) == []
    assert find_violations(instance, improved) == []
    assert total_cost(instance, improved) <= total_cost(instance, start)


def test_mip_is_never_worse_than_the_heuristics():
    instance = load("medium")
    start = greedy(instance)
    improved = local_search(instance, start)
    mip = solve(instance)

    assert total_cost(instance, mip.assignment) <= total_cost(instance, improved)


def test_greedy_keeps_large_gate_free_for_large_aircraft():
    # The M flight arrives first. The L gate is closer, but the M flight should take the
    # M gate, so the overlapping L flight still finds a gate.
    instance = Instance(
        flights=[
            Flight(id="F1", arrival=480, departure=540, aircraft_size="M", pax=150),
            Flight(id="F2", arrival=500, departure=600, aircraft_size="L", pax=300),
        ],
        gates=[
            Gate(id="G_L", max_size="L", walk_dist=100),
            Gate(id="G_M", max_size="M", walk_dist=200),
        ],
    )
    assert greedy(instance) == {"F1": "G_M", "F2": "G_L"}


def test_local_search_swaps_flights_to_put_more_passengers_closer():
    # Greedy goes by arrival: the small flight F1 takes the near gate, the full flight F2
    # the far one. Swapping them lowers the walking.
    instance = Instance(
        flights=[
            Flight(id="F1", arrival=480, departure=540, aircraft_size="M", pax=120),
            Flight(id="F2", arrival=490, departure=560, aircraft_size="M", pax=180),
        ],
        gates=[
            Gate(id="NEAR", max_size="M", walk_dist=100),
            Gate(id="FAR", max_size="M", walk_dist=300),
        ],
    )
    start = greedy(instance)
    assert start == {"F1": "NEAR", "F2": "FAR"}
    assert local_search(instance, start) == {"F1": "FAR", "F2": "NEAR"}


def test_local_search_moves_apron_flight_to_a_gate_that_became_free():
    instance = Instance(
        flights=[Flight(id="F1", arrival=480, departure=540, aircraft_size="S", pax=60)],
        gates=[Gate(id="G1", max_size="S", walk_dist=100)],
    )
    assert local_search(instance, {"F1": APRON}) == {"F1": "G1"}
