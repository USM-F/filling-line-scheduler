#!/usr/bin/env bash

set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "${script_dir}/utils.sh"

force_args=()
for arg in "$@"; do
    if [[ "${arg}" == '--force' ]]; then
        force_args=(--force)
    fi
done

"${script_dir}/ensure_venv.sh" "${force_args[@]}"

log INFO 'Running Filling-Line Scheduler'

# Input documents may be outside the working-directory bind mount. Resolve them
# on the host and mount just those files read-only at the same absolute paths.
prepare_input_mounts "$@"

# Output paths remain relative to FLS_WORK_DIR, as before.
docker container run --rm --network none \
    --user "$(id -u):$(id -g)" \
    -v "${FLS_PROJECT_ROOT}:/workspace:ro" \
    -v "${FLS_WORK_DIR}:${FLS_WORK_DIR}" \
    -v "${VENV_DIR}:/opt/venv:ro" \
    "${FLS_INPUT_MOUNTS[@]}" \
    -w "${FLS_WORK_DIR}" \
    -e PYTHONPATH=/workspace/src \
    "${FLS_IMAGE_REF}" \
    filling-scheduler "${FLS_CLI_ARGS[@]}"
