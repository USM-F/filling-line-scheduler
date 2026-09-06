#!/usr/bin/env bash

set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "${script_dir}/utils.sh"

force_args=()
pytest_args=()
smoke='false'
for arg in "$@"; do
    case "${arg}" in
        --smoke)
            smoke='true'
            ;;
        --force)
            force_args=(--force)
            pytest_args+=(--cache-clear)
            ;;
        *)
            pytest_args+=("${arg}")
            ;;
    esac
done

if [[ "${smoke}" == 'true' ]]; then
    if [[ ${#pytest_args[@]} -gt 0 ]]; then
        fail '--smoke runs Docker checks without pytest options or --force'
    fi
    exec "${script_dir}/run_smoke_tests.sh"
fi

"${script_dir}/ensure_venv.sh" --profile dev "${force_args[@]}"
mkdir -p "${FLS_PROJECT_ROOT}/.cache" "${FLS_PROJECT_ROOT}/.logs" "${FLS_PROJECT_ROOT}/output"

log INFO 'Running Filling-Line Scheduler tests'

docker container run --rm --network none \
    --user "$(id -u):$(id -g)" \
    -v "${FLS_PROJECT_ROOT}:/workspace:ro" \
    -v "${VENV_DIR}:/opt/venv:ro" \
    -v "${FLS_PROJECT_ROOT}/.cache:/workspace/.cache" \
    -v "${FLS_PROJECT_ROOT}/.logs:/workspace/.logs" \
    -v "${FLS_PROJECT_ROOT}/output:/workspace/output" \
    -w /workspace \
    -e PYTHONPATH=/workspace/src \
    -e COVERAGE_FILE=/workspace/.cache/.coverage \
    "${FLS_IMAGE_REF}" \
    python -m pytest \
        -o cache_dir=/workspace/.cache/pytest \
        --cov=filling_scheduler --cov-report=term-missing \
        --cov-report=xml:/workspace/.cache/coverage.xml \
        "${pytest_args[@]}"

log INFO 'Filling-Line Scheduler tests passed'
