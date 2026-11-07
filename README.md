# Gate Assignment Optimizer

Assigns flights to airport gates with a Mixed-Integer Program (Pyomo + HiGHS) and serves
it through a REST API with background solve jobs.

> Work in progress: model and benchmark are done; the API, Docker setup and CI come next.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if needed).

```bash
uv sync                      # create .venv with all dependencies
uv run python -m data.generate   # regenerate data/instances/{small,medium,large}.json
uv run pytest                # run tests
uv run ruff check . && uv run ruff format --check .
```

## Instances

| Instance | Flights | Gates | Peak flights on ground |
|---|---|---|---|
| small | 20 | 10 | 4 |
| medium | 150 | 20 | 20 |
| large | 400 | 30 | 56 |

"Peak flights on ground" counts flights between arrival and departure + buffer at the same
minute. The large instance is deliberately congested: at peak times some flights must go
to the apron.

## Solution checking

`app/checker.py` verifies every solution without using the model code: each flight is
assigned exactly once, every aircraft fits its gate, consecutive flights at a gate are at
least `buffer` minutes apart, and the cost recomputed from scratch equals the solver's
objective. Tests run it on model output and on deliberately broken assignments.

```bash
uv run python -m scripts.solve data/instances/medium.json --show
```

## Benchmark

```bash
uv run python -m scripts.benchmark --repeat 3
```

Environment: HiGHS 1.15.1, Pyomo 6.10.1, Python 3.12.16, AMD Ryzen 7 3700U with Radeon Vega Mobile Gfx, time limit 120 s, median of 3 run(s)

| Instance | Flights | Gates | Formulation | Variables | Constraints | Build (s) | Solve (s) | Status | MIP gap | Apron | Checker |
|---|---|---|---|---|---|---|---|---|---|---|---|
| small | 20 | 10 | clique | 120 | 62 | 0.00 | 0.02 | optimal | 0.000% | 0 | OK |
| small | 20 | 10 | pairwise | 120 | 78 | 0.00 | 0.02 | optimal | 0.000% | 0 | OK |
| medium | 150 | 20 | clique | 2,262 | 1,358 | 0.07 | 0.18 | optimal | 0.000% | 5 | OK |
| medium | 150 | 20 | pairwise | 2,262 | 16,340 | 0.16 | 0.63 | optimal | 0.000% | 5 | OK |
| large | 400 | 30 | clique | 8,476 | 4,580 | 0.23 | 29.68 | optimal | 0.001% | 85 | OK |
| large | 400 | 30 | pairwise | 8,476 | 193,688 | 1.79 | 38.20 | optimal | 0.002% | 85 | OK |

Notes:

- `clique` (default) adds one constraint per gate and per maximal group of flights that are
  on the ground at the same moment; `pairwise` adds one per conflicting pair and gate. Both
  give the same optimum; clique needs about 40x fewer constraints on `large` and solves faster.
- Status `optimal` means within HiGHS's default relative gap of 0.01%.
- The objective is passed to HiGHS in passenger-kilometers. With the unscaled objective
  (~1e8 passenger-meters), HiGHS stayed at the root node and still had a 14% gap on `large`
  after 60 s.
- On `large`, wide-bodies are over-represented at the apron (33 of 85 apron flights, while
  only 22% of all flights are L): 87 L flights (avg. 89 min on ground) share 8 L-capable gates.
  That is a capacity limit of the gate mix, not a model artifact.
