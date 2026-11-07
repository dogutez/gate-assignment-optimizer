"""Pydantic models for a gate assignment instance and the job API.

These models are the single data contract shared by the data generator,
the optimization model and the API. Times are integer minutes from midnight.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AircraftSize = Literal["S", "M", "L"]

# Ordering used for the compatibility check: a flight fits a gate
# if SIZE_RANK[aircraft_size] <= SIZE_RANK[max_size].
SIZE_RANK: dict[str, int] = {"S": 1, "M": 2, "L": 3}

# Reserved label used in results for flights that do not get a gate.
APRON = "APRON"


class Flight(BaseModel):
    # Reject unknown fields so typos in input JSON surface as validation errors.
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    arrival: int = Field(ge=0, description="Arrival time, minutes from midnight")
    departure: int = Field(ge=0, description="Departure time, minutes from midnight")
    aircraft_size: AircraftSize
    pax: int = Field(ge=0, description="Passenger count")

    @model_validator(mode="after")
    def check_departure_after_arrival(self) -> "Flight":
        if self.departure <= self.arrival:
            raise ValueError(
                f"flight {self.id}: departure ({self.departure}) must be after "
                f"arrival ({self.arrival})"
            )
        return self


class Gate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    max_size: AircraftSize
    walk_dist: int = Field(ge=0, description="Walking distance to terminal center, meters")

    @model_validator(mode="after")
    def check_not_reserved(self) -> "Gate":
        if self.id == APRON:
            raise ValueError(f"gate id '{APRON}' is reserved")
        return self


class Instance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "unnamed"
    buffer: int = Field(default=15, ge=0, description="Minimum minutes between flights at a gate")
    # The objective is in passenger-meters. One flight at one gate can cost up to
    # ~350 pax * ~1,300 m = ~455,000, so the penalty must be larger than that,
    # otherwise the model prefers the apron over a free but distant gate.
    apron_penalty: float = Field(
        default=1_000_000, ge=0, description="Cost per apron assignment (passenger-meters)"
    )
    # Apron passengers take a bus; this is its cost expressed as an equivalent walk,
    # charged per passenger, so a full flight at the apron costs more than an empty one.
    apron_walk_dist: int = Field(
        default=1_500, ge=0, description="Walk-equivalent distance per apron passenger, meters"
    )
    # Upper limits keep a single request from tying up the worker or the database.
    flights: list[Flight] = Field(min_length=1, max_length=2000)
    gates: list[Gate] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_unique_ids(self) -> "Instance":
        for label, items in (("flight", self.flights), ("gate", self.gates)):
            seen: set[str] = set()
            duplicates: set[str] = set()
            for item in items:
                if item.id in seen:
                    duplicates.add(item.id)
                seen.add(item.id)
            if duplicates:
                raise ValueError(f"duplicate {label} ids: {sorted(duplicates)}")
        return self


# ---------------------------------------------------------------------------
# Results and the job API
# ---------------------------------------------------------------------------

JobStatus = Literal["queued", "running", "succeeded", "failed"]


class Kpis(BaseModel):
    n_flights: int
    apron_count: int
    total_walk_pax_m: float = Field(description="Sum of pax * walking distance at gates")
    avg_walk_per_pax_m: float
    gate_utilization: float = Field(description="Occupied / available gate-minutes")


class SolveOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_limit_s: float = Field(default=60, gt=0, le=3600)
    mip_gap: float = Field(default=1e-4, ge=0, le=1, description="Relative MIP gap tolerance")


class JobRequest(BaseModel):
    """Body of POST /jobs."""

    model_config = ConfigDict(extra="forbid")

    instance: Instance
    options: SolveOptions = SolveOptions()


class JobSubmitted(BaseModel):
    job_id: str
    status: JobStatus


class JobInfo(BaseModel):
    """Response of GET /jobs/{job_id} and items of GET /jobs."""

    job_id: str
    status: JobStatus
    instance_name: str
    n_flights: int
    n_gates: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    solver_status: str | None = Field(description="optimal, feasible or no_solution")
    termination: str | None = Field(description="Raw solver termination condition")
    objective: float | None
    mip_gap: float | None
    solve_time_s: float | None
    error: str | None


class GateAssignment(BaseModel):
    flight_id: str
    gate_id: str = Field(description=f"Gate id or '{APRON}'")


class JobResult(BaseModel):
    """Response of GET /jobs/{job_id}/result."""

    job_id: str
    assignments: list[GateAssignment]
    kpis: Kpis
