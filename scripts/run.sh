#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
force_args=()
for arg in "$@"; do if [[ "${arg}" == --force ]]; then force_args=(--force); fi; done
"${FLS_SCRIPT_DIR}/ensure_venv.sh" "${force_args[@]}"
runtime_docker_args
mkdir -p "${FLS_PROJECT_ROOT}/.logs"
FLS_DOCKER_ARGS+=(-v "${FLS_PROJECT_ROOT}/.logs:/workspace/.logs")
cli_args=()
mount_index=0
while (($#)); do
    option="$1"
    case "${option%%=*}" in
        --input|--schedule|--report|--output|--html-output|--log-file|--work-dir)
            if [[ "${option}" == *=* ]]; then
                value="${option#*=}"; option="${option%%=*}"; shift
            elif [[ $# -ge 2 && "$2" != --* ]]; then
                value="$2"; shift 2
            else
                cli_args+=("$1"); shift; continue
            fi
            if [[ -z "${value}" ]]; then cli_args+=("${option}" "${value}"); continue; fi
            host_path="$(realpath -m -- "${value}")"
            [[ "${host_path}" != *:* ]] || die 'Docker-mounted paths must not contain a colon'
            mount_index=$((mount_index + 1))
            target="/cli-paths/${mount_index}"
            case "${option}" in
                --input|--schedule)
                    if [[ -e "${host_path}" ]]; then FLS_DOCKER_ARGS+=(-v "${host_path}:${target}:ro"); fi
                    ;;
                --work-dir)
                    mkdir -p -- "${host_path}"
                    FLS_DOCKER_ARGS+=(-v "${host_path}:${target}")
                    ;;
                *)
                    parent="$(dirname -- "${host_path}")"
                    mkdir -p -- "${parent}"
                    FLS_DOCKER_ARGS+=(-v "${parent}:${target}")
                    target="${target}/$(basename -- "${host_path}")"
                    ;;
            esac
            cli_args+=("${option}" "${target}")
            ;;
        *) cli_args+=("$1"); shift ;;
    esac
done
docker "${FLS_DOCKER_ARGS[@]}" "${FLS_IMAGE_REF}" /opt/venv/bin/filling-scheduler "${cli_args[@]}"
