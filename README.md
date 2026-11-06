# Gate Assignment Optimizer

Assigns flights to airport gates with a Mixed-Integer Program (Pyomo + HiGHS) and serves
it through a REST API with background solve jobs.

> Work in progress. Full documentation (model, architecture, benchmarks) comes in a later phase.

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
