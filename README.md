# Filling-Line Scheduler — decomposition experiment

Weekly filling-line scheduling from JSON: exact demand, eligible lines, shared shifts and breaks, sequence-dependent changeovers. [Assignment](docs/filling_only_20_product_assignment.md).

HiGHS solves one MILP in two lexicographic passes: minimize total changeover time, then additional product-to-line assignments. Each product has at most one run per line. A calendar-aware left shift removes avoidable delays while preserving assignments, quantities and order. An independent validator checks the generated schedule. See the [MILP model](docs/milp_model.md).

## Run

Requires Linux/WSL, Bash and Docker. The wrapper builds the Python environment on first use; subsequent runs execute offline.

```bash
./scripts/run.sh all \
  --input test/data/filling_only_20_product_assignment_input.json \
  --output output/schedule.json
```

`all` (alias: `solve`) checks the input, solves, validates and writes:

- `schedule.json`: ordered production and changeover slots.
- `schedule.html`: standalone Gantt chart.
- `schedule.metrics.json`: objectives, solver bounds, proof status and timings.

Existing outputs are replaced after validation, with backups for publication rollback. Logs go to `.logs/`.

HiGHS runs with one thread. Defaults: a shared 300-second solve budget, zero MIP gap, seed 0. Override with `--time-limit-seconds`, `--mip-gap`, `--seed`; use `--debug` for diagnostics. `OPTIMAL` means both objectives are proven; `FEASIBLE` means a validated incumbent without a complete proof. Optional exact timing adds makespan and start-sum objectives; see the model extension below.

```bash
./scripts/run.sh inspect --input input.json
./scripts/run.sh validate --input input.json --schedule output/schedule.json
./scripts/run.sh render --input input.json --schedule output/schedule.json --html-output output/schedule.html
./scripts/run.sh all --help
```

## Test

```bash
./scripts/run_tests.sh              # unit and integration tests
./scripts/run_tests.sh -m baseline  # supplied case: proven 210-minute changeovers, zero splits
./scripts/run_tests.sh --smoke      # Docker installation, reuse and offline execution
```

## Decomposition experiment

Use `--decomposition` to solve independent eligibility components sequentially with a shared budget. `--timing-mode exact` adds globally coordinated makespan and start-sum objectives; the default remains two objectives followed by a left shift. Weighted objectives are absent from this branch. See the [extension](docs/milp_model.md#experimental-extension).
