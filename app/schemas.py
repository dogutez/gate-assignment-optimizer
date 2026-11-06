"""Pydantic models for a gate assignment instance.

These models are the single data contract shared by the data generator,
the optimization model and the API. Times are integer minutes from midnight.
"""

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
    flights: list[Flight] = Field(min_length=1)
    gates: list[Gate] = Field(min_length=1)

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
