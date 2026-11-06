import pytest
from pydantic import ValidationError

from app.schemas import Instance
from data.generate import INSTANCES_DIR, PRESETS, generate_instance


def make_instance(**overrides) -> dict:
    data = {
        "flights": [
            {"id": "F1", "arrival": 100, "departure": 160, "aircraft_size": "M", "pax": 150}
        ],
        "gates": [{"id": "G1", "max_size": "L", "walk_dist": 200}],
    }
    data.update(overrides)
    return data


def test_valid_instance_uses_defaults():
    inst = Instance.model_validate(make_instance())
    assert inst.buffer == 15
    assert inst.apron_penalty == 1_000_000


def test_departure_must_be_after_arrival():
    bad = make_instance(
        flights=[{"id": "F1", "arrival": 100, "departure": 100, "aircraft_size": "M", "pax": 1}]
    )
    with pytest.raises(ValidationError, match="departure"):
        Instance.model_validate(bad)


def test_unknown_aircraft_size_rejected():
    bad = make_instance(
        flights=[{"id": "F1", "arrival": 100, "departure": 160, "aircraft_size": "XL", "pax": 1}]
    )
    with pytest.raises(ValidationError):
        Instance.model_validate(bad)


def test_duplicate_flight_ids_rejected():
    flight = {"id": "F1", "arrival": 100, "departure": 160, "aircraft_size": "S", "pax": 1}
    with pytest.raises(ValidationError, match="duplicate flight ids"):
        Instance.model_validate(make_instance(flights=[flight, flight]))


def test_reserved_gate_id_rejected():
    bad = make_instance(gates=[{"id": "APRON", "max_size": "L", "walk_dist": 0}])
    with pytest.raises(ValidationError, match="reserved"):
        Instance.model_validate(bad)


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        Instance.model_validate(make_instance(gate_count=3))


def test_generator_is_deterministic():
    a = generate_instance(50, 12, seed=7, name="x")
    b = generate_instance(50, 12, seed=7, name="x")
    c = generate_instance(50, 12, seed=8, name="x")
    assert a == b
    assert a != c


@pytest.mark.parametrize("name", PRESETS)
def test_saved_instances_match_generator(name):
    """The committed JSON files must be reproducible from the generator."""
    n_flights, n_gates, seed = PRESETS[name]
    saved = Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())
    assert saved == generate_instance(n_flights, n_gates, seed, name)
