"""HiGHS, Gurobi and CPLEX on the same model.

Gurobi and CPLEX tests run only where the solver is installed and licensed; CI has
neither, so they are skipped there. The failure-path tests fake an unavailable
solver and run everywhere.
"""

import pytest
from pyomo.contrib.solver.common.base import Availability
from pyomo.contrib.solver.solvers.gurobi.gurobi_persistent import GurobiPersistent

from app import worker
from app.checker import find_violations, total_cost
from app.model import solve
from app.schemas import Instance
from app.solvers import SolverUnavailableError, is_available
from data.generate import INSTANCES_DIR
from tests.helpers import run_worker_once, small_request


def load(name: str) -> Instance:
    return Instance.model_validate_json((INSTANCES_DIR / f"{name}.json").read_text())


def needs(solver: str):
    return pytest.mark.skipif(not is_available(solver), reason=f"{solver} not available")


@pytest.fixture(scope="module")
def small_optimum() -> float:
    return solve(load("small"), solver="highs").objective


@pytest.mark.parametrize(
    "solver",
    [
        "highs",
        pytest.param("gurobi", marks=needs("gurobi")),
        pytest.param("cplex", marks=needs("cplex")),
    ],
)
@pytest.mark.parametrize("formulation", ["clique", "pairwise"])
def test_every_solver_reaches_the_same_optimum_on_small(solver, formulation, small_optimum):
    instance = load("small")
    result = solve(instance, solver=solver, formulation=formulation)

    assert result.solver == solver and result.solver_version
    assert result.status == "optimal"
    assert result.objective == pytest.approx(small_optimum, rel=1e-6)
    assert result.best_bound is not None and result.nodes is not None
    assert find_violations(instance, result.assignment) == []
    assert total_cost(instance, result.assignment) == pytest.approx(result.objective)


@pytest.mark.skipif(
    not is_available("gurobi") or GurobiPersistent().available() != Availability.FullLicense,
    reason="needs a full Gurobi license",
)
def test_gurobi_with_full_license_matches_highs_on_medium():
    instance = load("medium")
    gurobi = solve(instance, solver="gurobi", threads=2)
    highs = solve(instance, solver="highs")
    assert gurobi.status == "optimal"
    assert gurobi.objective == pytest.approx(highs.objective, rel=1e-4)


@needs("cplex")
def test_cplex_size_limit_gives_clear_error():
    try:
        solve(load("medium"), solver="cplex")
    except SolverUnavailableError as e:
        assert "Community Edition" in str(e)
    else:
        pytest.skip("CPLEX solved medium: full license, no size limit to test")


def test_missing_solver_raises_clear_error(monkeypatch):
    monkeypatch.setattr(GurobiPersistent, "available", lambda self: Availability.NotFound)
    with pytest.raises(SolverUnavailableError, match="gurobi is not installed"):
        solve(load("small"), solver="gurobi")


def test_job_with_unavailable_solver_fails_with_reason(client, db_path, monkeypatch):
    monkeypatch.setattr(GurobiPersistent, "available", lambda self: Availability.NotFound)
    job_id = client.post("/jobs", json=small_request(solver="gurobi")).json()["job_id"]
    assert client.get(f"/jobs/{job_id}").json()["solver"] == "gurobi"

    run_worker_once(db_path)
    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert status["error"].startswith("solver unavailable: gurobi is not installed")


def test_job_result_records_solver_and_nodes(client, db_path):
    job_id = client.post("/jobs", json=small_request(threads=1)).json()["job_id"]
    run_worker_once(db_path)
    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "succeeded"
    assert status["solver"] == "highs" and status["solver_version"]
    assert status["nodes"] is not None


def test_solution_rejected_by_checker_is_not_stored(client, db_path, monkeypatch):
    real_solve = worker.solve

    def broken_solve(*args, **kwargs):
        result = real_solve(*args, **kwargs)
        # Put every flight on the same gate: overlaps everywhere.
        result.assignment = {f: "G01" for f in result.assignment}
        return result

    monkeypatch.setattr(worker, "solve", broken_solve)
    job_id = client.post("/jobs", json=small_request()).json()["job_id"]
    run_worker_once(db_path)

    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert "independent check" in status["error"]
    assert client.get(f"/jobs/{job_id}/result").status_code == 409


def test_highs_thread_count_can_change_between_solves():
    # A long-running worker gets jobs with different `threads` options.
    instance = load("small")
    for threads in (None, 1, 2):
        assert solve(instance, threads=threads).status == "optimal"
