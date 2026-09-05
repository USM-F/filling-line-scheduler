#!/usr/bin/env bash

set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "${script_dir}/utils.sh"
exec 1>&2

profile='runtime'
force='false'
while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            force='true'
            shift
            ;;
        --profile)
            if [[ $# -lt 2 ]]; then
                fail '--profile needs runtime or dev'
            fi
            profile=$2
            shift 2
            ;;
        *)
            fail "Usage: $(basename "$0") [--force] [--profile runtime|dev]"
            ;;
    esac
done

if [[ "${profile}" != 'runtime' && "${profile}" != 'dev' ]]; then
    fail 'Profile must be runtime or dev'
fi

validate_venv_path
mkdir -p -- "$(dirname -- "${VENV_DIR}")"
if [[ -L "${VENV_DIR}.lock" ]]; then
    fail 'Venv lock must not be a symlink'
fi
exec 9>"${VENV_DIR}.lock"
flock 9
validate_venv_path

"${script_dir}/ensure_builder_image.sh"

# The image ID fixes the Python ABI and architecture. A dev venv also serves runtime.
image_id=$(docker image inspect "${FLS_IMAGE_REF}" --format '{{.Id}}')
stamp_file="${VENV_DIR}/.fls-stamp"
if [[ -f "${stamp_file}" && "$(head -n 1 "${stamp_file}")" == 'dev' ]]; then
    profile='dev'
fi

requirements='requirements.txt'
if [[ "${profile}" == 'dev' ]]; then
    requirements='requirements-dev.txt'
fi
fingerprint=$(
    {
        printf '%s\n' "${image_id}" "${profile}"
        cat "${FLS_PROJECT_ROOT}/requirements.txt" "${FLS_PROJECT_ROOT}/${requirements}"
        cat "${FLS_PROJECT_ROOT}/pyproject.toml" "${script_dir}/ensure_venv.sh"
    } | sha256sum | cut -d ' ' -f 1
)
stamp=$(printf '%s\n%s' "${profile}" "${fingerprint}")

if [[ "${force}" == 'false' && -f "${stamp_file}" && "$(cat "${stamp_file}")" == "${stamp}" ]]; then
    # The venv interpreter exists inside the image; do not test its host symlink.
    if docker container run --rm --network none \
        --user "$(id -u):$(id -g)" \
        -v "${VENV_DIR}:/opt/venv:ro" \
        "${FLS_IMAGE_REF}" /opt/venv/bin/python -m pip check &>/dev/null; then
        log INFO "Venv '${VENV_DIR}' (${profile}) found -> skipping installation"
        exit 0
    fi
fi

if [[ -d "${VENV_DIR}" ]]; then
    validate_venv_path
    log INFO "Recreating venv '${VENV_DIR}'"
    rm -rf -- "${VENV_DIR}"
fi
mkdir -p -- "${VENV_DIR}"
printf '%s\n' "${FLS_PROJECT_ROOT}" >"${VENV_DIR}/.fls-owner"

log INFO "Installing '${requirements}' into '${VENV_DIR}'"

docker container run --rm -i \
    --user "$(id -u):$(id -g)" \
    -v "${FLS_PROJECT_ROOT}:/workspace:ro" \
    -v "${VENV_DIR}:/opt/venv" \
    -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -e PIP_NO_CACHE_DIR=1 \
    "${FLS_IMAGE_REF}" bash -se -- "${requirements}" <<'EOF'
python -m venv /opt/venv
/opt/venv/bin/python -m pip install -r "/workspace/$1"

# Build package metadata in /tmp, keeping the source checkout untouched.
mkdir /tmp/package
cp /workspace/pyproject.toml /workspace/README.md /tmp/package/
cp -R /workspace/src /tmp/package/src
/opt/venv/bin/python -m pip install --no-deps --no-build-isolation /tmp/package
/opt/venv/bin/python -m pip check
EOF

printf '%s\n' "${stamp}" >"${stamp_file}.tmp"
mv -- "${stamp_file}.tmp" "${stamp_file}"
log INFO "Venv '${VENV_DIR}' is ready (${profile})"
