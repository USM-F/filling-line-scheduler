#!/usr/bin/env bash

FLS_PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
FLS_BUILDER_VERSION='0.1.0'
FLS_IMAGE_REF="filling-scheduler-builder:${FLS_BUILDER_VERSION}"
FLS_BASE_IMAGE='python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3'

export VENV_DIR="${VENV_DIR:-${FLS_PROJECT_ROOT}/.venv}"
export FLS_WORK_DIR="${FLS_WORK_DIR:-${PWD}}"

log() {
    local severity=$1
    shift
    printf '%s %s\n' "${severity}" "$*" >&2
}

fail() {
    log ERROR "$@"
    exit 2
}

# Only remove environments created by this checkout, never an arbitrary directory.
validate_venv_path() {
    if [[ "${VENV_DIR}" != /* ]]; then
        fail 'VENV_DIR must be an absolute path'
    fi

    local canonical
    canonical=$(realpath -m -- "${VENV_DIR}")
    if [[ "${VENV_DIR}" != "${canonical}" ]]; then
        fail 'VENV_DIR must be normalized (no .. or trailing slash)'
    fi
    if [[ "${canonical}" == / || "${canonical}" == "${HOME}" || "${FLS_PROJECT_ROOT}/" == "${canonical}/"* ]]; then
        fail 'VENV_DIR must not be root, home, the project or its parent'
    fi

    case "$(basename -- "${VENV_DIR}")" in
        .venv|.venv-*) ;;
        *) fail 'VENV_DIR basename must be .venv or .venv-*' ;;
    esac

    local directory="${VENV_DIR}"
    while [[ "${directory}" != / ]]; do
        if [[ -L "${directory}" ]]; then
            fail 'VENV_DIR must not traverse symlinks'
        fi
        directory=$(dirname -- "${directory}")
    done

    if [[ -e "${VENV_DIR}" && ! -d "${VENV_DIR}" ]]; then
        fail 'VENV_DIR must be a directory'
    fi
    if [[ ! -d "${VENV_DIR}" || -z "$(ls -A -- "${VENV_DIR}")" ]]; then
        return
    fi
    if [[ ! -f "${VENV_DIR}/.fls-owner" || -L "${VENV_DIR}/.fls-owner" ]]; then
        fail 'Refusing an unmarked nonempty venv'
    fi
    if [[ "$(cat -- "${VENV_DIR}/.fls-owner")" != "${FLS_PROJECT_ROOT}" ]]; then
        fail 'Venv belongs to another checkout'
    fi
    if [[ -L "${VENV_DIR}/.fls-stamp" ]]; then
        fail 'Venv stamp must not be a symlink'
    fi
}
