#!/bin/bash
# In-cluster isolated / blended entrypoint. Locustfiles are unchanged from
# laptop e2e; this only picks the file, labels CSV paths, and runs headless
# (no web UI in a Job). SLOs and ramp knobs come from env.sh.
set -euo pipefail
cd /app

TEST="${TEST:?set TEST on the Job}"
POD_SCALE="${POD_SCALE:?set POD_SCALE on the Job}"

# Kubernetes env is applied before this script. env.sh would overwrite
# ClusterIP hosts, POD_SCALE, and the OIDC secret — save, source, restore.
_SAVE_STAFF_API_BASE="${STAFF_API_BASE}"
_SAVE_KEYCLOAK_BASE="${KEYCLOAK_BASE}"
_SAVE_KEYCLOAK_REALM="${KEYCLOAK_REALM:-staff}"
_SAVE_OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-farmer-registry-staff-portal}"
_SAVE_OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET}"
_SAVE_OIDC_USERNAME="${OIDC_USERNAME}"
_SAVE_OIDC_PASSWORD="${OIDC_PASSWORD}"
_SAVE_INGRESS="${INGRESS:-in-cluster}"
_SAVE_VOLUME_TIER="${VOLUME_TIER:-primary}"
_SAVE_TEST="${TEST}"
_SAVE_POD_SCALE="${POD_SCALE}"
_SAVE_PERF_SEED_DIR="${PERF_SEED_DIR:-/perf-seed}"
_SAVE_CPU_BREACH_CORES="${CPU_BREACH_CORES:-1.85}"
_SAVE_CPU_BREACH_POLLS="${CPU_BREACH_POLLS:-2}"
_SAVE_STAFF_API_KUBE_NAMESPACE="${STAFF_API_KUBE_NAMESPACE:-perftest}"
_SAVE_STAFF_API_POD_GREP="${STAFF_API_POD_GREP:-farmer-registry-staff-portal-api}"

set +u
# shellcheck source=env.sh
source ./env.sh
set -u

export STAFF_API_BASE="${_SAVE_STAFF_API_BASE}"
export KEYCLOAK_BASE="${_SAVE_KEYCLOAK_BASE}"
export KEYCLOAK_REALM="${_SAVE_KEYCLOAK_REALM}"
export OIDC_CLIENT_ID="${_SAVE_OIDC_CLIENT_ID}"
export OIDC_CLIENT_SECRET="${_SAVE_OIDC_CLIENT_SECRET}"
export OIDC_USERNAME="${_SAVE_OIDC_USERNAME}"
export OIDC_PASSWORD="${_SAVE_OIDC_PASSWORD}"
export INGRESS="${_SAVE_INGRESS}"
export VOLUME_TIER="${_SAVE_VOLUME_TIER}"
export TEST="${_SAVE_TEST}"
export POD_SCALE="${_SAVE_POD_SCALE}"
export PERF_SEED_DIR="${_SAVE_PERF_SEED_DIR}"
export CPU_BREACH_CORES="${_SAVE_CPU_BREACH_CORES}"
export CPU_BREACH_POLLS="${_SAVE_CPU_BREACH_POLLS}"
export STAFF_API_KUBE_NAMESPACE="${_SAVE_STAFF_API_KUBE_NAMESPACE}"
export STAFF_API_POD_GREP="${_SAVE_STAFF_API_POD_GREP}"
# Soak-only: RPS cap, pod pin, Connection: close. Isolated/blended stay e2e.
# Drop any laptop kubeconfig so kubectl top uses this pod's ServiceAccount.
unset IN_CLUSTER_SOAK SOAK_MAX_RPS STAFF_API_HEADLESS DISABLE_HTTP_KEEPALIVE KUBECONFIG

case "${TEST}" in
  blended)
    STEP="2-blended"
    LOCUSTFILE="staff-api/blended/blended_locustfile.py"
    CSV_PREFIX="/results/staff-api/${INGRESS}/${VOLUME_TIER}/pod-${POD_SCALE}/${STEP}/blended"
    ;;
  register-read | cr-create | cr-read-and-approve | intake-create | intake-read-and-approve)
    STEP="1-isolated"
    SCENARIO_DIR="${TEST//-/_}"
    LOCUSTFILE="staff-api/${SCENARIO_DIR}/${SCENARIO_DIR}_locustfile.py"
    CSV_PREFIX="/results/staff-api/${INGRESS}/${VOLUME_TIER}/pod-${POD_SCALE}/${STEP}/${SCENARIO_DIR}/${SCENARIO_DIR}"
    ;;
  *)
    echo "TEST='${TEST}' is not valid. Use: register-read | cr-create |" >&2
    echo "cr-read-and-approve | intake-create | intake-read-and-approve | blended" >&2
    exit 1
    ;;
esac

mkdir -p "$(dirname "$CSV_PREFIX")"

echo "=== in-cluster run ==="
echo "INGRESS:     ${INGRESS}"
echo "VOLUME_TIER: ${VOLUME_TIER}"
echo "POD_SCALE:   ${POD_SCALE}"
echo "TEST:        ${TEST}"
echo "STEP:        ${STEP}"
echo "LOCUSTFILE:  ${LOCUSTFILE}"
echo "CSV_PREFIX:  ${CSV_PREFIX}"
echo "PERF_SEED:   ${PERF_SEED_DIR}"
echo "STAFF_API:   ${STAFF_API_BASE}"
echo "CPU_LIMIT:   ${CPU_BREACH_CORES} cores (polls=${CPU_BREACH_POLLS}, ns=${STAFF_API_KUBE_NAMESPACE})"
if [ "${POD_SCALE}" -ge 3 ] 2>/dev/null; then
  echo "CPU_QUORUM:  2 pods must be >= ${CPU_BREACH_CORES}c (POD_SCALE=${POD_SCALE})"
else
  echo "CPU_QUORUM:  1 pod must be >= ${CPU_BREACH_CORES}c (POD_SCALE=${POD_SCALE})"
fi
echo "======================"

# Same locustfiles as locust-staff-api.sh. --headless only so the Job starts
# without the web UI; the SLO shape still owns users and stop.
# Locust exits 1 if any request failed (e.g. one SYS-ERR-001). That must
# not abort this script: Kubernetes would mark the Job Failed and skip
# sleep, so kubectl cp cannot read /results.
set +e
locust -f "$LOCUSTFILE" --host "$STAFF_API_BASE" --headless \
  -u 1 -r 1 --csv "$CSV_PREFIX"
locust_rc=$?
set -e
echo "LOCUST_FINISHED rc=${locust_rc} csv=${CSV_PREFIX}"
echo "sleeping so kubectl cp can collect /results; delete the Job when done"
sleep infinity
