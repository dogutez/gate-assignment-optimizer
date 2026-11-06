"""Synthetic instance generator for the gate assignment problem.

Usage (run from the repo root):
    python -m data.generate                      # write small/medium/large presets
    python -m data.generate --flights 60 --gates 12 --seed 7 --out data/instances/x.json

Flight arrivals follow a mix of a morning wave, an evening wave and a flat
background, so the instance has realistic peaks where gates become scarce.
"""

import argparse
import random
from pathlib import Path

from app.schemas import Flight, Gate, Instance

INSTANCES_DIR = Path(__file__).parent / "instances"

# name -> (number of flights, number of gates, seed)
PRESETS: dict[str, tuple[int, int, int]] = {
    "small": (20, 10, 1),
    "medium": (150, 20, 2),
    "large": (400, 30, 3),
}

# Share of each aircraft size among flights.
SIZE_WEIGHTS = {"S": 0.30, "M": 0.50, "L": 0.20}

# Ground time (minutes) and passenger ranges per aircraft size.
GROUND_TIME = {"S": (30, 50), "M": (40, 70), "L": (60, 120)}
PAX_RANGE = {"S": (50, 90), "M": (120, 190), "L": (220, 350)}

# Arrival time model: (weight, mean, std dev) in minutes from midnight.
MORNING_WAVE = (0.3, 8 * 60, 90)  # around 08:00
EVENING_WAVE = (0.3, 18 * 60, 100)  # around 18:00
# The remaining probability (0.4) is a flat background between these two times.
FIRST_ARRIVAL = 5 * 60  # 05:00
LAST_ARRIVAL = 23 * 60  # 23:00


def round_to_5(minutes: float) -> int:
    return int(round(minutes / 5) * 5)


def sample_arrival(rng: random.Random) -> int:
    r = rng.random()
    if r < MORNING_WAVE[0]:
        t = rng.gauss(MORNING_WAVE[1], MORNING_WAVE[2])
    elif r < MORNING_WAVE[0] + EVENING_WAVE[0]:
        t = rng.gauss(EVENING_WAVE[1], EVENING_WAVE[2])
    else:
        t = rng.uniform(FIRST_ARRIVAL, LAST_ARRIVAL)
    t = min(max(t, FIRST_ARRIVAL), LAST_ARRIVAL)  # clip to operating hours
    return round_to_5(t)


def make_gates(n_gates: int, rng: random.Random) -> list[Gate]:
    """Gates along a pier: G01 is closest to the terminal center.

    About 25% of gates take large aircraft (at least 2), 40% medium, the rest small.
    Sizes are shuffled so large gates are not all at one end of the pier.
    """
    n_large = max(2, round(0.25 * n_gates))
    n_medium = round(0.40 * n_gates)
    n_small = n_gates - n_large - n_medium
    sizes = ["L"] * n_large + ["M"] * n_medium + ["S"] * n_small
    rng.shuffle(sizes)

    gates = []
    for k, size in enumerate(sizes):
        walk_dist = 100 + 40 * k + rng.randint(-15, 15)  # meters, grows along the pier
        gates.append(Gate(id=f"G{k + 1:02d}", max_size=size, walk_dist=walk_dist))
    return gates


def make_flights(n_flights: int, rng: random.Random) -> list[Flight]:
    raw = []
    sizes = list(SIZE_WEIGHTS)
    weights = list(SIZE_WEIGHTS.values())
    for _ in range(n_flights):
        size = rng.choices(sizes, weights=weights)[0]
        arrival = sample_arrival(rng)
        ground_time = round_to_5(rng.uniform(*GROUND_TIME[size]))
        pax = rng.randint(*PAX_RANGE[size])
        raw.append((arrival, arrival + ground_time, size, pax))

    # Sort by arrival so flight ids follow the time of day.
    raw.sort()
    return [
        Flight(id=f"F{i + 1:03d}", arrival=a, departure=d, aircraft_size=s, pax=p)
        for i, (a, d, s, p) in enumerate(raw)
    ]


def generate_instance(n_flights: int, n_gates: int, seed: int, name: str) -> Instance:
    rng = random.Random(seed)  # own generator: same seed -> same instance
    gates = make_gates(n_gates, rng)
    flights = make_flights(n_flights, rng)
    return Instance(name=name, flights=flights, gates=gates)


def peak_on_ground(instance: Instance) -> int:
    """Maximum number of flights on the ground at the same minute (buffer included)."""
    events = []
    for f in instance.flights:
        events.append((f.arrival, 1))
        events.append((f.departure + instance.buffer, -1))
    # At equal times process departures (-1) before arrivals (+1).
    events.sort()
    current = peak = 0
    for _, change in events:
        current += change
        peak = max(peak, current)
    return peak


def save(instance: Instance, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(instance.model_dump_json(indent=2) + "\n")
    print(
        f"{path}: {len(instance.flights)} flights, {len(instance.gates)} gates, "
        f"peak on ground = {peak_on_ground(instance)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--flights", type=int, help="number of flights (custom instance)")
    parser.add_argument("--gates", type=int, help="number of gates (custom instance)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, help="output JSON path (custom instance)")
    args = parser.parse_args()

    if args.flights is None and args.gates is None and args.out is None:
        for name, (n_flights, n_gates, seed) in PRESETS.items():
            save(generate_instance(n_flights, n_gates, seed, name), INSTANCES_DIR / f"{name}.json")
        return

    if args.flights is None or args.gates is None or args.out is None:
        parser.error("custom instances need --flights, --gates and --out")
    instance = generate_instance(args.flights, args.gates, args.seed, args.out.stem)
    save(instance, args.out)


if __name__ == "__main__":
    main()
