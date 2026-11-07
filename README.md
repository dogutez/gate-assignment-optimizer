# Gate Assignment Optimizer

Assigns flights to airport gates with a Mixed-Integer Program (Pyomo + HiGHS) and serves
it through a REST API with background solve jobs.

> Work in progress: model, API, logging and Docker are done; CI and final docs come next.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if needed).

```bash
uv sync                      # create .venv with all dependencies
uv run python -m data.generate   # regenerate data/instances/{small,medium,large}.json
uv run pytest                # run tests
uv run ruff check . && uv run ruff format --check .
```

## Quick start with Docker

```bash
git clone https://github.com/dogutez/gate-assignment-optimizer.git
cd gate-assignment-optimizer
docker compose up --build          # API on http://127.0.0.1:8000, plus one worker
```

Submit the large instance and poll it:

```bash
python3 -c "import json; print(json.dumps({'instance': json.load(open('data/instances/large.json')), 'options': {'time_limit_s': 120}}))" > request.json
curl -s -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" -d @request.json
# {"job_id":"<id>","status":"queued"}
curl -s http://127.0.0.1:8000/jobs/<id>          # status, objective, gap
curl -s http://127.0.0.1:8000/jobs/<id>/result   # assignments + KPIs
```

- `api` and `worker` run from the same image and share one SQLite file on the `jobs-data` volume.
- The port is published on `127.0.0.1` only; use `API_PORT=8010 docker compose up` if 8000 is taken.
- `docker stop` sends SIGTERM: the worker finishes its current step and exits. If it is
  killed during a solve, the job is put back in the queue when the worker starts again.
- v1 runs a single worker (requeue-on-start assumes it); see "Possible extensions".

## Running the API locally

The API stores jobs in SQLite and returns immediately; a separate worker process solves them.

```bash
uv run python -m app.api --host 127.0.0.1 --port 8000   # terminal 1
uv run python -m app.worker                             # terminal 2
```

Both use `JOBS_DB_PATH` (default `jobs.db`). Interactive docs: http://127.0.0.1:8000/docs

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/jobs` | Submit `{"instance": ..., "options": {"time_limit_s": 60, "mip_gap": 0.0001}}`, returns `202` + `job_id` |
| `GET` | `/jobs/{job_id}` | Status (`queued` / `running` / `succeeded` / `failed`), timestamps, solver status, objective, gap |
| `GET` | `/jobs/{job_id}/result` | Assignments (`flight_id` -> `gate_id` or `APRON`) and KPIs; `409` until the job has succeeded |
| `GET` | `/jobs?limit=20` | Most recent jobs |

If the time limit is reached with a feasible solution, the job succeeds with
`solver_status: "feasible"` and the remaining `mip_gap`. If no solution was found, it fails
with the reason in `error`.

## Logging

API and worker write one JSON object per line to stdout (uvicorn's own lines included).
Every line written while a job is handled carries its `job_id`, also lines from the model
code, which knows nothing about jobs: the worker stores the id in a `ContextVar` and a log
filter adds it to each record. Level is set with `LOG_LEVEL` (default `INFO`).

```json
{"time": "...", "level": "INFO", "logger": "app.model", "event": "model built", "formulation": "clique", "n_variables": 2262, "n_constraints": 1358, "build_time_s": 0.059, "job_id": "1403136465a04a318758bd4761f8176c"}
{"time": "...", "level": "INFO", "logger": "app.model", "event": "solve finished", "status": "optimal", "termination": "convergenceCriteriaSatisfied", "objective": 14158944.0, "mip_gap": 0.0, "solve_time_s": 0.288, "time_limit_s": 60.0, "job_id": "1403136465a04a318758bd4761f8176c"}
```

Events: `job submitted`, `job started`, `model built`, `solve finished`, `job succeeded`,
`job failed` (with `error_type`, `error` and `traceback`).

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
