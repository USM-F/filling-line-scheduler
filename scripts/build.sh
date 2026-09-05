#!/usr/bin/env bash

set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "${script_dir}/utils.sh"

force_args=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            force_args=(--force)
            shift
            ;;
        *)
            fail "Usage: $(basename "$0") [--force]"
            ;;
    esac
done

"${script_dir}/ensure_builder_image.sh" "${force_args[@]}"
"${script_dir}/ensure_venv.sh" "${force_args[@]}"

log INFO 'Checking Filling-Line Scheduler installation'

docker container run --rm --network none \
    --user "$(id -u):$(id -g)" \
    -v "${FLS_PROJECT_ROOT}:/workspace:ro" \
    -v "${VENV_DIR}:/opt/venv:ro" \
    -e PYTHONPATH=/workspace/src \
    "${FLS_IMAGE_REF}" \
    python -c 'import filling_scheduler, pydantic, highspy; print("Ready:", filling_scheduler.__version__, "Pydantic", pydantic.__version__, "HiGHS", highspy.Highs().version())'

log INFO 'Filling-Line Scheduler built successfully'
