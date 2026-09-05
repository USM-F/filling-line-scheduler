#!/usr/bin/env bash
# Sourced by host scripts. Host requirements: Bash, Docker, coreutils and flock.
set -euo pipefail

FLS_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FLS_PROJECT_ROOT="$(cd -- "${FLS_SCRIPT_DIR}/.." && pwd -P)"
FLS_BASE_IMAGE="${FLS_BASE_IMAGE:-python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3}"
FLS_IMAGE_KEY="$( { printf '%s\n' "${FLS_BASE_IMAGE}"; cat "${FLS_SCRIPT_DIR}/ensure_builder_image.sh"; } | sha256sum | cut -c1-16)"
FLS_IMAGE_REF="filling-scheduler-builder:${FLS_IMAGE_KEY}"
VENV_DIR="${VENV_DIR:-${FLS_PROJECT_ROOT}/.venv}"
export FLS_BASE_IMAGE VENV_DIR

log() { printf '%s %s\n' "$1" "$2" >&2; }
die() { log ERROR "$1"; exit 2; }

validate_venv_path() {
    [[ -n "${VENV_DIR}" && "${VENV_DIR}" == /* ]] || die 'VENV_DIR must be an absolute path'
    local part="${VENV_DIR}"
    while [[ "${part}" != / ]]; do
        [[ ! -L "${part}" ]] || die 'VENV_DIR must not traverse symlinks'
        part="$(dirname -- "${part}")"
    done
    local canonical
    canonical="$(realpath -m -- "${VENV_DIR}")"
    [[ "${VENV_DIR}" == "${canonical}" ]] || die 'VENV_DIR must be normalized (no .. or trailing slash)'
    [[ "${canonical}" != / && "${canonical}" != "${HOME}" && "${canonical}" != "${FLS_PROJECT_ROOT}" ]] || die 'Unsafe VENV_DIR'
    [[ "${FLS_PROJECT_ROOT}/" != "${canonical}/"* ]] || die 'VENV_DIR must not contain the project'
    case "$(basename -- "${canonical}")" in .venv|.venv-*) ;; *) die 'VENV_DIR basename must be .venv or .venv-*' ;; esac
    [[ ! -e "${VENV_DIR}" || -d "${VENV_DIR}" ]] || die 'VENV_DIR must be a directory'
    if [[ -d "${VENV_DIR}" && -n "$(ls -A -- "${VENV_DIR}")" ]]; then
        [[ -f "${VENV_DIR}/.fls-owner" && ! -L "${VENV_DIR}/.fls-owner" ]] || die 'Refusing an unmarked nonempty venv'
        [[ "$(cat -- "${VENV_DIR}/.fls-owner")" == "${FLS_PROJECT_ROOT}" ]] || die 'Venv belongs to another checkout'
        [[ ! -L "${VENV_DIR}/.fls-stamp" ]] || die 'Venv stamp must not be a symlink'
    fi
}

runtime_docker_args() {
    FLS_DOCKER_ARGS=(run --rm --network none --user "$(id -u):$(id -g)"
        -e PYTHONPATH=/workspace/src -e PYTHONDONTWRITEBYTECODE=1
        -e PYTHONUNBUFFERED=1 -e PYTHONUTF8=1
        -v "${FLS_PROJECT_ROOT}:/workspace:ro"
        -v "${VENV_DIR}:/opt/venv:ro" -w /workspace)
}
