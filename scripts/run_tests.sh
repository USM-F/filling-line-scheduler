#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
force_args=()
pytest_args=()
for arg in "$@"; do
    case "${arg}" in
        --force) force_args=(--force); pytest_args+=(--cache-clear) ;;
        *) pytest_args+=("${arg}") ;;
    esac
done
"${FLS_SCRIPT_DIR}/ensure_venv.sh" --profile dev "${force_args[@]}"
runtime_docker_args
mkdir -p "${FLS_PROJECT_ROOT}/.cache" "${FLS_PROJECT_ROOT}/.logs"
FLS_DOCKER_ARGS+=(-v "${FLS_PROJECT_ROOT}/.cache:/workspace/.cache"
    -v "${FLS_PROJECT_ROOT}/.logs:/workspace/.logs"
    -e COVERAGE_FILE=/workspace/.cache/.coverage
    -e HYPOTHESIS_STORAGE_DIRECTORY=/workspace/.cache/hypothesis)
docker "${FLS_DOCKER_ARGS[@]}" "${FLS_IMAGE_REF}" /opt/venv/bin/python -m pytest \
    -o cache_dir=/workspace/.cache/pytest --cov=filling_scheduler --cov-report=term-missing \
    --cov-report=xml:/workspace/.cache/coverage.xml "${pytest_args[@]}"
