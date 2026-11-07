import pytest

from app.checker import find_violations, total_cost
from app.model import solve
from app.schemas import APRON, Instance
from data.generate import INSTANCES_DIR

INSTANCE = Instance(
    buffer=15,
    apron_penalty=1000,
    apron_walk_dist=10,
    flights=[
        {"id": "F1", "arrival": 100, "departure": 200, "aircraft_size": "M", "pax": 10},
        {"id": "F2", "arrival": 210, "departure": 300, "aircraft_size": "M", "pax": 20},
        {"id": "F3", "arrival": 100, "departure": 200, "aircraft_size": "L", "pax": 30},
    ],
    gates=[
        {"id": "G_M", "max_size": "M", "walk_dist": 100},
        {"id": "G_L", "max_size": "L", "walk_dist": 200},
    ],
)


def test_valid_solution_has_no_violations():
    assignment = {"F1": "G_M", "F2": "G_L", "F3": APRON}
    assert find_violations(INSTANCE, assignment) == []


def test_detects_buffer_overlap():
    # F2 arrives 10 min after F1 leaves, buffer is 15.
    problems = find_violations(INSTANCE, {"F1": "G_M", "F2": "G_M", "F3": "G_L"})
    assert len(problems) == 1
    assert "gate G_M" in problems[0]


def test_detects_size_mismatch():
    problems = find_violations(INSTANCE, {"F1": "G_L", "F2": APRON, "F3": "G_M"})
    assert problems == ["F3: size L does not fit gate G_M (max M)"]


def test_detects_missing_unknown_flights_and_gates():
    problems = find_violations(INSTANCE, {"F1": "G_X", "F2": APRON, "F9": APRON})
    assert any("without an assignment" in p and "F3" in p for p in problems)
    assert any("unknown flights" in p and "F9" in p for p in problems)
    assert any("unknown gate G_X" in p for p in problems)


def test_total_cost_by_hand():
    assignment = {"F1": "G_M", "F2": "G_L", "F3": APRON}
    # F1: 10 * 100, F2: 20 * 200, F3 at apron: 1000 + 30 * 10
    assert total_cost(INSTANCE, assignment) == 1000 + 4000 + 1300


@pytest.mark.parametrize("formulation", ["clique", "pairwise"])
def test_model_solution_passes_independent_check(formulation):
    inst = Instance.model_validate_json((INSTANCES_DIR / "medium.json").read_text())
    result = solve(inst, time_limit_s=20, formulation=formulation)
    assert result.status == "optimal"
    assert find_violations(inst, result.assignment) == []
    # The model's objective must match a cost computed without the model.
    assert total_cost(inst, result.assignment) == pytest.approx(result.objective, rel=1e-6)
