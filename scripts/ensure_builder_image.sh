#!/usr/bin/env bash

set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "${script_dir}/utils.sh"
exec 1>&2

force='false'
build_args=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            force='true'
            build_args=(--pull --no-cache)
            shift
            ;;
        *)
            fail "Usage: $(basename "$0") [--force]"
            ;;
    esac
done

if [[ "${force}" == 'false' ]] && docker image inspect "${FLS_IMAGE_REF}" &>/dev/null; then
    log INFO "Builder image '${FLS_IMAGE_REF}' found -> skipping build"
    exit 0
fi

log INFO "Building '${FLS_IMAGE_REF}' builder image"

docker image build "${build_args[@]}" -t "${FLS_IMAGE_REF}" - <<EOF
FROM ${FLS_BASE_IMAGE}
ENV PATH="/opt/venv/bin:\$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
EOF
