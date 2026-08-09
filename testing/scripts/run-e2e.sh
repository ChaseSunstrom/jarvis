#!/usr/bin/env bash
#
# run-e2e.sh — the end-to-end suites, with artifacts a failure can be read from.
#
# What CI calls. It runs pytest against `testing/e2e`, keeping every harness
# work directory (the generated config, all three process logs, and the audio
# the fake STT actually received) under testing/artifacts/ so a failed job
# uploads something you can actually diagnose from.
#
#   testing/scripts/run-e2e.sh                    # everything
#   testing/scripts/run-e2e.sh -k pipeline        # extra args go to pytest
#
# Exit status is pytest's.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
ARTIFACTS="${JARVIS_E2E_ARTIFACTS:-${REPO_ROOT}/testing/artifacts}"
WORK_DIR="${ARTIFACTS}/harness"

rm -rf "${ARTIFACTS}"
mkdir -p "${WORK_DIR}"

# Keep the work directory whatever happens: on a green run it is a few hundred
# KB of logs, and on a red one it is the whole story.
export JARVIS_HARNESS_KEEP=1
export JARVIS_HARNESS_WORK_DIR="${WORK_DIR}"

cd "${REPO_ROOT}" || exit 2

echo "== jarvis end-to-end =="
echo "repo       ${REPO_ROOT}"
echo "artifacts  ${ARTIFACTS}"
python3 -c 'import sys; print("python    ", sys.version.split()[0])'

python3 -m pytest testing/e2e -v --color=yes "$@"
status=$?

echo
if [[ ${status} -eq 0 ]]; then
    echo "END-TO-END PASSED"
else
    echo "END-TO-END FAILED (status ${status}) — logs follow"
    for log in "${WORK_DIR}"/logs/*.log; do
        [[ -f "${log}" ]] || continue
        echo
        echo "----- ${log} (last 60 lines) -----"
        tail -n 60 "${log}"
    done
fi

echo
echo "Artifacts kept in ${ARTIFACTS}:"
find "${ARTIFACTS}" -type f -printf '  %-70p %s bytes\n' 2>/dev/null | head -40

exit "${status}"
