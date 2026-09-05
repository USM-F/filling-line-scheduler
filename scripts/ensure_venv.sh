#!/usr/bin/env bash
set -euo pipefail
exec 1>&2
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

profile=runtime
force=false
while (($#)); do
    case "$1" in
        --force) force=true; shift ;;
        --profile) [[ $# -ge 2 ]] || die '--profile needs runtime or dev'; profile="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[[ "${profile}" == runtime || "${profile}" == dev ]] || die 'Profile must be runtime or dev'
validate_venv_path
mkdir -p -- "$(dirname -- "${VENV_DIR}")"
[[ ! -L "${VENV_DIR}.lock" ]] || die 'Venv lock must not be a symlink'
exec 9>"${VENV_DIR}.lock"
flock 9
validate_venv_path
"${FLS_SCRIPT_DIR}/ensure_builder_image.sh"
image_id="$(docker image inspect "${FLS_IMAGE_REF}" --format '{{.Id}}')"
abi="$(docker run --rm --network none "${FLS_IMAGE_REF}" python -c 'import platform, sysconfig; print(sysconfig.get_config_var("SOABI"), platform.machine())')"

# A current dev environment is also sufficient for runtime; do not downgrade it.
if [[ "${profile}" == runtime && -f "${VENV_DIR}/.fls-stamp" ]] && [[ "$(head -n 1 "${VENV_DIR}/.fls-stamp")" == dev ]]; then
    profile=dev
fi
fingerprint="$( {
    printf '%s\n' "${image_id}" "${abi}" "${profile}"
    cat "${FLS_PROJECT_ROOT}/requirements.txt" "${FLS_PROJECT_ROOT}/pyproject.toml"
    cat "${FLS_SCRIPT_DIR}/ensure_venv.sh"
    if [[ "${profile}" == dev ]]; then cat "${FLS_PROJECT_ROOT}/requirements-dev.txt"; fi
} | sha256sum | cut -d ' ' -f1)"
expected_stamp="$(printf '%s\n%s' "${profile}" "${fingerprint}")"
if [[ "${force}" == false && -f "${VENV_DIR}/pyvenv.cfg" && -f "${VENV_DIR}/.fls-stamp" ]] && [[ "$(cat "${VENV_DIR}/.fls-stamp")" == "${expected_stamp}" ]]; then
    # Venv interpreter symlinks point inside the image, not into the host OS.
    if docker run --rm --network none --user "$(id -u):$(id -g)" \
        -v "${VENV_DIR}:/opt/venv:ro" "${FLS_IMAGE_REF}" \
        /opt/venv/bin/python -m pip check >/dev/null 2>&1; then
        log INFO "Reusing '${VENV_DIR}' (${profile})"
        exit 0
    fi
fi
if [[ -d "${VENV_DIR}" ]]; then
    validate_venv_path
    log INFO "Recreating guarded venv '${VENV_DIR}'"
    rm -rf -- "${VENV_DIR}"
fi
mkdir -p -- "${VENV_DIR}"
printf '%s\n' "${FLS_PROJECT_ROOT}" >"${VENV_DIR}/.fls-owner"
requirements=requirements.txt
if [[ "${profile}" == dev ]]; then requirements=requirements-dev.txt; fi
log INFO "Installing '${requirements}' into container-built venv"
docker run --rm --user "$(id -u):$(id -g)" \
    -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_NO_CACHE_DIR=1 \
    -v "${FLS_PROJECT_ROOT}:/workspace:ro" -v "${VENV_DIR}:/opt/venv" \
    "${FLS_IMAGE_REF}" sh -eu -c '
        python -m venv /opt/venv
        /opt/venv/bin/python -m pip install --no-cache-dir -r "/workspace/$1"
        mkdir /tmp/package
        cp /workspace/pyproject.toml /workspace/README.md /tmp/package/
        cp -R /workspace/src /tmp/package/src
        /opt/venv/bin/python -m pip install --no-deps --no-build-isolation /tmp/package
        /opt/venv/bin/python -m pip check
    ' sh "${requirements}"
printf '%s\n' "${expected_stamp}" >"${VENV_DIR}/.fls-stamp.tmp"
mv -- "${VENV_DIR}/.fls-stamp.tmp" "${VENV_DIR}/.fls-stamp"
log INFO "Venv ready (${profile})"
