#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
force_args=()
for arg in "$@"; do
    case "${arg}" in --force) force_args=(--force) ;; *) die "Unknown argument: ${arg}" ;; esac
done
"${FLS_SCRIPT_DIR}/ensure_builder_image.sh" "${force_args[@]}"
"${FLS_SCRIPT_DIR}/ensure_venv.sh" "${force_args[@]}"
runtime_docker_args
docker "${FLS_DOCKER_ARGS[@]}" "${FLS_IMAGE_REF}" /opt/venv/bin/python -c \
    'import highspy, pydantic, filling_scheduler; print("Ready:", filling_scheduler.__version__, "Pydantic", pydantic.__version__, "HiGHS", highspy.Highs().version())'
