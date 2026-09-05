# Filling-Line Scheduler

CLI-приложение для недельного планирования разлива продуктов. Вход — JSON со спросом,
доступными линиями, скоростями производства, рабочим календарём и переналадками.
Цель полного MVP — выпуск всего спроса с минимальным суммарным временем переналадок.

**Текущий этап: скелет приложения.** Работают загрузка и проверка входной схемы,
`inspect`, логирование, Docker-окружение и тесты. Команды `solve`, `validate`, `render`
зарегистрированы и возвращают `NOT_IMPLEMENTED` (exit code `1`). Они не создают
расписания. Успешный `inspect` не означает, что задача выполнима.

## Задача

Будущий планировщик должен:

- Выпускать полный спрос каждого продукта только на совместимых линиях.
- Соблюдать производительность, рабочие смены и перерывы.
- Резервировать переналадку при смене продукта; она может идти в рабочее время,
  во время обеда или между сменами.
- Минимизировать суммарное время переналадок и избегать последовательностей A–B–A.
- В MVP выпускать каждый продукт на одной линии. Это конкретизирует исходное
  требование использовать как можно меньше линий на продукт.

Дальнейшая архитектура: одна интегрированная event-based MILP, HiGHS,
опциональные независимые компоненты, независимая проверка результата и
`schedule.json` вместе с автономным HTML/SVG Gantt.

## Входные данные

Пример: [test/data/filling_only_20_product_assignment_input.json](test/data/filling_only_20_product_assignment_input.json).

| Поле | Содержимое |
| --- | --- |
| `id`, `description` | Идентификатор и описание задачи |
| `planningHorizon` | Начало и конец горизонта с UTC offset, IANA time zone и точность в минутах |
| `demand` | Продукты и спрос `quantityUnits` |
| `lines` | Линии, `eligibleProducts` и скорости `capacityUnitsPerHour` |
| `calendar` | Общий календарь, рабочие дни, смены и перерывы |
| `changeoverMatrixMinutes` | Матрица времени переналадок между продуктами |

Приложенный сценарий: 20 активных продуктов, 13 линий, 84 допустимые пары
«продукт–линия», общий спрос 1 133 892 единицы. Горизонт —
17–24 августа 2026 года, `Asia/Bangkok`, точность — 1 минута.

Загрузчик принимает UTF-8 JSON, запрещает повторяющиеся ключи и неизвестные поля.
Количество и минуты должны быть целыми JSON-числами, скорости — положительными
JSON-числами; строки и boolean вместо чисел не допускаются. Дробные скорости
сохраняются как `Decimal`. Нулевой спрос допустим. Внутри Python используются
`sku_id`, `line_id` и `snake_case`, внешние имена сохраняются через aliases.
Модели используют Pydantic `strict=True`, `extra='forbid'`, `frozen=True`.

Пока проверяются схема и ограничения отдельных полей. Проверки ссылок, уникальности
идентификаторов, полноты матрицы, пересечений смен, календарных окон и выполнимости
будут добавлены позже. Счётчики `inspect` относятся к записям входного файла.

## Установка и запуск

Нужны Linux (или WSL), Bash, Docker с доступным daemon, GNU coreutils и `flock`.
Python на host не требуется. Первичная сборка и установка зависимостей требуют сети.

```bash
./scripts/build.sh
./scripts/run.sh inspect \
  --input test/data/filling_only_20_product_assignment_input.json

./scripts/run.sh inspect \
  --input test/data/filling_only_20_product_assignment_input.json \
  --report output/inspection.json --debug

./scripts/run_tests.sh
./test/docker_smoke.sh
```

`inspect` печатает JSON в stdout. `--report` сохраняет ту же сводку, атомарно и без
перезаписи существующего файла. Для замены отчёта требуется `--force`.
Ошибка публикации возвращает `8`, а stdout остаётся пустым.
Файл входа нельзя использовать как отчёт или лог, в том числе через symlink/hardlink.

Скрипты можно вызывать из другого рабочего каталога: аргументы путей разрешаются
относительно текущего каталога вызывающего процесса. Пути с пробелами поддерживаются;
пути Docker mounts с двоеточием не поддерживаются. Входные файлы монтируются только
для чтения. Родительские каталоги выходных файлов монтируются для записи — для
результатов рекомендуется отдельный `output/`.

Внутри подготовленного контейнера доступны обе точки входа:

```bash
filling-scheduler inspect --input /workspace/test/data/filling_only_20_product_assignment_input.json
python -m filling_scheduler inspect --input /workspace/test/data/filling_only_20_product_assignment_input.json
```

## Docker и venv

`ensure_builder_image.sh` передаёт короткий рецепт image через heredoc. База —
Python 3.11.15 slim-bookworm, закреплённый digest. Файлы проекта не передаются в
контекст сборки и не копируются в image; Dockerfile и `.dockerignore` не нужны.
Tag builder image зависит от рецепта и базового image.

`ensure_venv.sh` создаёт `.venv` контейнером и устанавливает exact-pinned зависимости
обычным `python -m pip`. Для установки метаданных пакета используется временная копия
исходников внутри контейнера. При запуске `PYTHONPATH=/workspace/src` выбирает текущие
исходники checkout; изменения Python-кода не требуют пересборки image или venv.

И при создании, и при запуске venv находится в `/opt/venv`. В runtime venv и проект
монтируются read-only; логи, отчёты и тестовые кэши — отдельными writable mounts.
Используется UID/GID вызывающего пользователя. Host-home не монтируется.
`run.sh`, `run_tests.sh` и проверка установки в `build.sh` запускают Python с
`--network none`.

Stamp окружения учитывает image ID, Python ABI, архитектуру, requirements,
`pyproject.toml`, скрипт установки и профиль `runtime`/`dev`. Актуальный dev-профиль
подходит для runtime и не понижается автоматически. Конкурирующие установщики
сериализуются через `flock`.

```bash
./scripts/ensure_builder_image.sh       # создать или переиспользовать image
./scripts/ensure_venv.sh --profile dev   # подготовить зависимости разработки
./scripts/build.sh --force              # image: --pull --no-cache; venv: пересоздание
./scripts/run_tests.sh --force          # пересоздать dev-venv и очистить pytest cache
./scripts/run.sh inspect --input test/data/filling_only_20_product_assignment_input.json \
  --report output/inspection.json --force
```

В `run.sh` флаг `--force` пересоздаёт venv и передаётся приложению как разрешение
перезаписи отчёта. Он не пересобирает image. Прямой вызов CLI с `--force` влияет
только на выходной файл. `ensure_venv.sh --force` также не пересобирает image.

Можно задать `VENV_DIR=/absolute/path/.venv-custom`. Перед удалением проверяются
нормализованный абсолютный путь, имя `.venv`/`.venv-*`, marker владельца и отсутствие
симлинков в пути. Корень, домашний каталог, корень проекта, его предки и посторонние
непустые каталоги запрещены. Marker создаётся только для нового/пустого каталога.
`FLS_BASE_IMAGE` позволяет явно переопределить базу; для воспроизводимости задавайте
image с digest и Python 3.11.

## Команды и диагностика

```text
inspect  --input INPUT [--report REPORT] [--force]
solve    --input INPUT --output SCHEDULE [options]       # пока NOT_IMPLEMENTED
validate --input INPUT --schedule SCHEDULE              # пока NOT_IMPLEMENTED
render   --schedule SCHEDULE --html-output HTML         # пока NOT_IMPLEMENTED
```

Для `solve` зарезервированы `--decomposition`, `--workers` (1),
`--threads-per-worker` (1), `--time-limit-seconds` (300), `--mip-gap` (0), `--seed` (0),
`--html-output`, `--work-dir`, `--keep-work-dir`, `--force`.

Общие опции доступны перед подкомандой и после неё: `--log-file`, `--log-level`,
`--debug`. `--debug` включает DEBUG независимо от `--log-level`.

Логи приложения идут в stderr и `.logs/<run_id>.jsonl` либо заданный `--log-file`:

- INFO — начало, сводка результата и завершение с кодом и временем.
- DEBUG — параметры, длительности и результат этапов.
- ERROR — машинный код ошибки и детали; неожиданные ошибки сохраняют traceback в JSONL.

Каждое событие получает один уровень. JSONL включает timestamp, level, run_id и event.
Ошибки argparse проходят тот же обработчик и логируются. События фильтруются
выбранным уровнем; stdout остаётся отдельным каналом JSON/справки.
Скрипты подготовки окружения печатают служебные INFO/ERROR в stderr.

| ExitCode | Значение |
| --- | --- |
| `0 SUCCESS` | Команда успешно выполнена |
| `1 NOT_IMPLEMENTED` | Команда ещё не реализована |
| `2 INPUT_ERROR` | Ошибка CLI, чтения или входной схемы |
| `3 INFEASIBLE` | Зарезервировано: доказанная невыполнимость |
| `4 NO_INCUMBENT` | Зарезервировано: solver не нашёл допустимое решение |
| `5 WORKER_FAILURE` | Зарезервировано: crash/timeout/неполные результаты workers |
| `6 MERGE_CONFLICT` | Зарезервировано: конфликт объединения |
| `7 SCHEDULE_INVALID` | Зарезервировано: независимая проверка отклонила расписание |
| `8 ARTIFACT_ERROR` | Конфликт или ошибка записи отчёта/лога; в будущем также HTML |
| `9 INTERNAL_ERROR` | Неожиданная внутренняя ошибка с traceback |

Коды завершения, машинные ошибки и имена событий определены enum, без свободных
строк в местах использования. Ошибки схемы содержат путь поля, тип и сообщение.

## Будущий формат расписания

Ниже **иллюстрация формата**, а не рассчитанное расписание или выполнение полного
спроса. Слоты упорядочены по линиям и времени; временная зона соответствует входу.

```json
{
  "scheduleId": "candidate-001",
  "timeZone": "Asia/Bangkok",
  "lines": [
    {
      "line": "L1",
      "slots": [
        {
          "type": "production",
          "start": "2026-08-17T08:30:00+07:00",
          "end": "2026-08-17T12:30:00+07:00",
          "product": "A",
          "quantityUnits": 38400
        },
        {
          "type": "changeover",
          "start": "2026-08-17T12:30:00+07:00",
          "end": "2026-08-17T13:00:00+07:00",
          "fromProduct": "A",
          "toProduct": "B",
          "durationMinutes": 30
        },
        {
          "type": "production",
          "start": "2026-08-17T13:30:00+07:00",
          "end": "2026-08-17T15:30:00+07:00",
          "product": "B",
          "quantityUnits": 19200
        }
      ]
    }
  ],
  "summary": {
    "producedByProduct": {"A": 38400, "B": 19200},
    "totalChangeoverMinutes": 30,
    "productsSplitAcrossMultipleLines": 0,
    "slotCount": 3
  }
}
```

## Структура и проверки

`src/filling_scheduler/` содержит входные контракты, CLI, IO, сводку и логирование.
Пакеты `validation`, `presolve`, `optimization`, `orchestration`, `solution` и
`reporting` описывают границы следующих этапов. Независимый валидатор не должен
импортировать оптимизатор, renderer — HiGHS; эти границы проверяются тестами.
Импорт CLI не загружает `highspy`.

Тесты находятся в `test/unit`, `test/integration`, `test/property`; исходные данные —
в `test/data`. `run_tests.sh` проверяет реальные CLI entrypoints, схемы, точность,
отчёты, логи и защиту удаления. Покрытие сохраняется в `.cache/coverage.xml`.
`test/docker_smoke.sh` проверяет отдельное окружение с пробелами в пути, переход
runtime → dev, повторное использование, stale stamp, `--force` и работу без сети.

GitHub Actions запускает сборку, тесты и Docker smoke при push/pull request,
сохраняя диагностику и coverage. Автоматического merge нет.

После приёмки скелета: семантическая валидация и календарь → presolve → монолитная
MILP → workers и декомпозиция → проверка/публикация расписания и Gantt → benchmark.
