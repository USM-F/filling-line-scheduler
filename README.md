# Filling-Line Scheduler

## Запуск приложения

Нужны Linux/WSL, Bash и Docker. Python 3.11 и зависимости устанавливаются в контейнере.

```bash
./scripts/build.sh
./scripts/run.sh inspect --input test/data/filling_only_20_product_assignment_input.json
./scripts/run.sh solve --input test/data/filling_only_20_product_assignment_input.json --output output/schedule.json
./scripts/run.sh validate --input test/data/filling_only_20_product_assignment_input.json --schedule output/schedule.json
./scripts/run.sh render --schedule output/schedule.json --html-output output/another-view.html
```

По умолчанию используется `--objective-mode lexicographic`. Для общей взвешенной цели:

```bash
./scripts/run.sh solve --input test/data/filling_only_20_product_assignment_input.json \
  --output output/weighted.json --objective-mode weighted \
  --objective-weights 1 30 0.1 0.01
```

Четыре веса относятся к переналадкам, дополнительным назначениям, makespan и сумме
стартов. Временные цели измеряются в дискретах `precisionMinutes`, назначения — в штуках;
автоматического нормирования нет. Веса обязательны только для `weighted`, конечны,
неотрицательны и не могут быть одновременно нулевыми. Режим, веса, четыре значения
целей и `weighted_value` сохраняются в metrics. `OPTIMAL` в режиме `weighted`
относится к общей сумме; при ненулевом оставшемся gap результат отмечается `FEASIBLE`.

`solve` сохраняет JSON, автономный HTML Gantt и metrics рядом с расписанием.
По умолчанию: один поток, seed 0, gap 0, общий лимит 300 секунд.
Проверенный результат по лимиту сохраняется со статусом `FEASIBLE`.
Логи — stderr и `.logs/`; `--debug` включает подробности.
`--force` разрешает перезапись; в `run.sh` он также пересоздаёт venv,
а `build.sh --force` пересобирает image.
Для внешних данных задайте `FLS_WORK_DIR=/путь/к/данным`; по умолчанию монтируется текущий каталог.

## Запуск тестов

```bash
./scripts/run_tests.sh
./scripts/run_tests.sh -m baseline --no-cov
./scripts/run_tests.sh --smoke
```

Тесты находятся в `test/`, данные — в `test/data/`, покрытие — `.cache/coverage.xml`.
Baseline запускается отдельно с лимитом 300 секунд, артефакты — `output/baseline.*`.
Docker smoke проверяет окружение, `--force`, пути с пробелами и запуск без сети.

## Описание модели

Входной JSON задаёт спрос, совместимость продуктов и линий, скорости, смены с перерывами
и матрицу переналадок. MILP через HiGHS распределяет целый объём SKU между одной или
несколькими линиями, выбирает порядок и время. На каждой линии SKU образует один run;
перерывы приостанавливают производство, переналадки могут идти в нерабочее время.

В лексикографическом режиме цели идут по приоритету: переналадки → дополнительные
назначения SKU на линии → makespan → сумма стартов. Следующая цель решается после
доказательства оптимума предыдущей. Во взвешенном режиме минимизируется их линейная
композиция за один проход, с возможностью компромиссов между целями.
`validate` независимо проверяет физические слоты; `inspect` проверяет только входную схему.

[Постановка задачи](docs/filling_only_20_product_assignment.md) ·
[Математическая постановка: переменные, ограничения и цели](docs/milp_model.md).
