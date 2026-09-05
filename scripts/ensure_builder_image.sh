#!/usr/bin/env bash
set -euo pipefail
exec 1>&2
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

force=false
for arg in "$@"; do
    case "${arg}" in --force) force=true ;; *) die "Unknown argument: ${arg}" ;; esac
done
if [[ "${force}" == false ]] && docker image inspect "${FLS_IMAGE_REF}" >/dev/null 2>&1; then
    log INFO "Reusing '${FLS_IMAGE_REF}' builder image"
    exit 0
fi
build_args=()
if [[ "${force}" == true ]]; then build_args+=(--pull --no-cache); fi
log INFO "Building '${FLS_IMAGE_REF}' builder image"
# '-' is a text Dockerfile on stdin, with no filesystem build context.
docker image build "${build_args[@]}" -t "${FLS_IMAGE_REF}" - <<EOF
FROM ${FLS_BASE_IMAGE}
ENV PATH="/opt/venv/bin:\$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
EOF
