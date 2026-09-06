#!/usr/bin/env bash
# Real host Docker integration, kept separate from tests inside the container.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
. "${script_dir}/utils.sh"

smoke_dir="$(mktemp -d "${TMPDIR:-/tmp}/filling smoke.XXXXXXXX")"
cleanup() { rm -rf -- "${smoke_dir}"; }
trap cleanup EXIT
export VENV_DIR="${smoke_dir}/.venv-smoke"
export FLS_WORK_DIR="${smoke_dir}"
source_input="${FLS_PROJECT_ROOT}/test/data/filling_only_20_product_assignment_input.json"
cp -- "${source_input}" "${smoke_dir}/input with spaces.json"

log INFO 'Smoke: first run, runtime install, clean JSON stdout and spaced paths'
"${script_dir}/run.sh" inspect --input "${smoke_dir}/input with spaces.json" \
    --report "${smoke_dir}/report with spaces.json" --log-file "${smoke_dir}/run log.jsonl" \
    >"${smoke_dir}/stdout.json"
[[ "$(head -n 1 "${VENV_DIR}/.fls-stamp")" == runtime ]]
cmp "${smoke_dir}/stdout.json" "${smoke_dir}/report with spaces.json"
runtime_stamp="$(cat "${VENV_DIR}/.fls-stamp")"
touch "${VENV_DIR}/reuse-sentinel"
"${script_dir}/ensure_venv.sh"
[[ -f "${VENV_DIR}/reuse-sentinel" && "$(cat "${VENV_DIR}/.fls-stamp")" == "${runtime_stamp}" ]]

log INFO 'Smoke: runtime to dev profile transition'
"${script_dir}/ensure_venv.sh" --profile dev
[[ ! -e "${VENV_DIR}/reuse-sentinel" && "$(head -n 1 "${VENV_DIR}/.fls-stamp")" == dev ]]
touch "${VENV_DIR}/dev-sentinel"
"${script_dir}/ensure_venv.sh" --profile runtime
[[ -e "${VENV_DIR}/dev-sentinel" ]]

log INFO 'Smoke: stale fingerprint rebuild'
printf 'dev\nstale-test-fingerprint\n' >"${VENV_DIR}/.fls-stamp"
"${script_dir}/ensure_venv.sh" --profile dev
[[ ! -e "${VENV_DIR}/dev-sentinel" ]]
touch "${VENV_DIR}/force-sentinel"

log INFO 'Smoke: repeat run replaces the report and reuses the venv'
printf 'old report\n' >"${smoke_dir}/report with spaces.json"
"${script_dir}/run.sh" inspect --input "${smoke_dir}/input with spaces.json" \
    --report "${smoke_dir}/report with spaces.json" >"${smoke_dir}/repeat-stdout.json"
[[ -e "${VENV_DIR}/force-sentinel" ]]
cmp "${smoke_dir}/repeat-stdout.json" "${smoke_dir}/report with spaces.json"

log INFO 'Smoke: force recreates only the owned venv and replaces the report'
printf 'old report\n' >"${smoke_dir}/report with spaces.json"
"${script_dir}/run.sh" inspect --input "${smoke_dir}/input with spaces.json" \
    --report "${smoke_dir}/report with spaces.json" --force >"${smoke_dir}/force-stdout.json"
[[ ! -e "${VENV_DIR}/force-sentinel" ]]
cmp "${smoke_dir}/force-stdout.json" "${smoke_dir}/report with spaces.json"
cmp "${source_input}" "${smoke_dir}/input with spaces.json"

log INFO 'Smoke: no network, read-only source/venv, expected UID and real dependencies'
docker container run --rm --network none \
    --user "$(id -u):$(id -g)" \
    -v "${FLS_PROJECT_ROOT}:/workspace:ro" \
    -v "${VENV_DIR}:/opt/venv:ro" \
    -v "${smoke_dir}:/smoke:ro" \
    -e "EXPECTED_UID=$(id -u)" \
    "${FLS_IMAGE_REF}" python -c '
import json, os, pathlib, highspy, pytest
assert os.getuid() == int(os.environ["EXPECTED_UID"])
assert {p.name for p in pathlib.Path("/sys/class/net").iterdir()} == {"lo"}
for directory in ("/workspace", "/opt/venv"):
    assert os.statvfs(directory).f_flag & os.ST_RDONLY, directory
report = json.loads(pathlib.Path("/smoke/report with spaces.json").read_text())
assert [report[key] for key in ("productCount", "activeProductCount", "lineCount", "eligiblePairCount", "totalDemandUnits")] == [20, 20, 13, 84, 1133892]
assert highspy.Highs().version() == "1.11.0"
'
log INFO 'Docker smoke passed'
