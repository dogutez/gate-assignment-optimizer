"""Run a built Pyomo model with HiGHS, Gurobi or CPLEX and read back the same fields.

Every solver gets the model through its Python API, in memory (no LP/MPS file):

- HiGHS and Gurobi: Pyomo's new solver interface (pyomo.contrib.solver), which has
  common options (time_limit, rel_gap, threads) and common results. For Gurobi the
  persistent class is used: its direct class needs scipy, the persistent one does not.
  "Persistent" means the solver keeps the model, so a changed model can be re-solved
  without sending it again; we solve each model once, so here it acts like direct.
- CPLEX: not in the new interface yet, so the older `cplex_direct` plugin, with
  CPLEX's own option names.

Gurobi and CPLEX are optional (`uv sync --extra solvers`) and need a license for
models above their free size limits. Both problems raise SolverUnavailableError
with a message that says what is missing.
"""

from dataclasses import dataclass

import highspy
import pyomo.environ as pyo
from pyomo.contrib.solver.common.base import Availability
from pyomo.contrib.solver.common.results import SolutionStatus
from pyomo.contrib.solver.solvers.gurobi.gurobi_persistent import GurobiPersistent
from pyomo.contrib.solver.solvers.highs import Highs

from app.schemas import SolverName

SOLVERS: tuple[SolverName, ...] = ("highs", "gurobi", "cplex")

INSTALL_HINT = "install it with `uv sync --extra solvers`"


class SolverUnavailableError(RuntimeError):
    """The solver is not installed, not licensed, or the model is above its size limit."""


def is_available(solver: SolverName) -> bool:
    """Installed and licensed. A size-limited free license counts (enough for small)."""
    if solver == "highs":
        return bool(Highs().available())
    if solver == "gurobi":
        return bool(GurobiPersistent().available())
    if solver == "cplex":
        return bool(pyo.SolverFactory("cplex_direct").available(exception_flag=False))
    raise ValueError(f"unknown solver: {solver}")


@dataclass
class RawResult:
    """What a solver reports, before the objective is scaled back."""

    has_solution: bool
    optimal: bool
    termination: str
    objective: float | None
    best_bound: float | None
    nodes: int | None
    version: str


def run(
    m: pyo.ConcreteModel,
    solver: SolverName,
    time_limit_s: float,
    mip_gap: float,
    threads: int | None = None,
) -> RawResult:
    """Solve m. On success the variable values are loaded into m."""
    if solver == "highs":
        # HiGHS starts one thread pool per process at its first solve and keeps that
        # size. A later solve with another `threads` value then stops without a
        # solution (termination "unknown"). Resetting the pool lets every job choose.
        highspy.Highs.resetGlobalScheduler(True)
        return run_new_interface(m, Highs(), "highs", time_limit_s, mip_gap, threads)
    if solver == "gurobi":
        return run_new_interface(m, GurobiPersistent(), "gurobi", time_limit_s, mip_gap, threads)
    if solver == "cplex":
        return run_cplex(m, time_limit_s, mip_gap, threads)
    raise ValueError(f"unknown solver: {solver}")


def run_new_interface(m, opt, name, time_limit_s, mip_gap, threads) -> RawResult:
    available = opt.available()
    if available == Availability.NotFound:
        raise SolverUnavailableError(f"{name} is not installed; {INSTALL_HINT}")
    if not available:  # BadLicense, BadVersion, ...
        raise SolverUnavailableError(f"{name} cannot run here: {available.name}")

    try:
        results = opt.solve(
            m,
            time_limit=time_limit_s,
            rel_gap=mip_gap,
            threads=threads,  # None keeps the solver's default (all cores)
            load_solutions=False,  # we load them ourselves, only if a solution exists
            raise_exception_on_nonoptimal_result=False,  # a time-limit stop is not an error
        )
    except Exception as e:
        if "size-limited license" in str(e):
            raise SolverUnavailableError(
                f"{name}: model is too large for the size-limited license "
                "(2,000 variables/constraints); an academic license is needed"
            ) from e
        raise

    has_solution = results.solution_status in (SolutionStatus.optimal, SolutionStatus.feasible)
    if has_solution:
        results.solution_loader.load_vars()
    nodes = None
    for key in ("mip_node_count", "NodeCount"):  # the HiGHS and the Gurobi name
        if key in results.extra_info:
            nodes = int(results.extra_info[key])
    return RawResult(
        has_solution=has_solution,
        optimal=results.solution_status == SolutionStatus.optimal,
        termination=results.termination_condition.name,
        objective=results.incumbent_objective if has_solution else None,
        best_bound=results.objective_bound,
        nodes=nodes,
        version=".".join(str(v) for v in opt.version()),
    )


# CPLEX solution status codes for "optimal" and "optimal within the gap tolerance".
CPLEX_OPTIMAL = {1, 101, 102}


def run_cplex(m, time_limit_s, mip_gap, threads) -> RawResult:
    opt = pyo.SolverFactory("cplex_direct")
    if not opt.available(exception_flag=False):
        raise SolverUnavailableError(f"cplex is not installed; {INSTALL_HINT}")

    # CPLEX's own parameter names (parameters.timelimit, parameters.mip.tolerances.mipgap).
    opt.options["timelimit"] = time_limit_s
    opt.options["mipgap"] = mip_gap
    if threads is not None:
        opt.options["threads"] = threads

    try:
        results = opt.solve(m, load_solutions=False, tee=False)
    except Exception as e:
        if "1016" in str(e):  # CPLEX error 1016: Community Edition size limit
            raise SolverUnavailableError(
                "cplex: model is too large for CPLEX Community Edition "
                "(1,000 variables and 1,000 constraints); a full license is needed"
            ) from e
        raise

    # The plugin keeps the cplex.Cplex object; bound and node count are read from it.
    cpx = opt._solver_model
    has_solution = cpx.solution.get_solution_type() != cpx.solution.type.none
    if has_solution:
        opt.load_vars()
    return RawResult(
        has_solution=has_solution,
        optimal=cpx.solution.get_status() in CPLEX_OPTIMAL,
        termination=cpx.solution.get_status_string(),
        objective=results.problem.upper_bound if has_solution else None,
        best_bound=results.problem.lower_bound if has_solution else None,
        nodes=cpx.solution.progress.get_num_nodes_processed(),
        version=cpx.get_version(),
    )
