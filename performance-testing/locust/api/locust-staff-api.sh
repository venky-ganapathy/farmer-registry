#!/bin/bash
# Fires one staff-api Locust run, driven entirely by env.sh's VOLUME_TIER /
# POD_SCALE / STEP / ISOLATED_SCENARIO exports -- edit env.sh (uncomment the
# one value you want per knob) rather than passing flags to this script.
#
# Replaces the old staff-intake-create.sh / staff-register-read.sh one-offs.
#
# Always runs with neither --headless nor --autostart: opens the web UI at
# localhost:8089 and waits for you to click "Start swarming". (-t is
# deliberately omitted -- Locust ignores --run-time unless --headless or
# --autostart is also set, so passing it here would silently do nothing.)
#
# For STEP=1-isolated, each scenario locustfile defines its own SLO-driven
# LoadTestShape (shared/slo_shape.py, config in env.sh's SLO sections) --
# Locust auto-detects it and ramps/stops concurrency itself once you click
# Start, ignoring the -u/-r passed below (see COMMON_OPTIONS in
# locust/main.py); those flags only do anything for the blended/soak shim
# locustfile.py below, which has no shape.
set -euo pipefail

# Must be *executed* (./locust-staff-api.sh), not sourced -- unlike env.sh.
# Sourcing runs set -e in your actual interactive shell, so a Ctrl-C-killed
# locust would exit your shell (and close the terminal) instead of just this
# script's subshell.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Run this script, don't source it: ./locust-staff-api.sh (not '. ./locust-staff-api.sh')" >&2
  return 1 2>/dev/null || exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# env.sh echoes $SEED_MANIFEST without exporting it (that default lives in
# shared/config.py instead) -- relax -u just for the source so that doesn't
# trip us up.
set +u
source ./env.sh
set -u

# In-cluster soak Job knobs (RPS cap, headless pod pin, Connection: close).
# Never leak them into laptop isolated/blended from a previous export.
unset IN_CLUSTER_SOAK SOAK_MAX_RPS STAFF_API_HEADLESS DISABLE_HTTP_KEEPALIVE

# Farmer CPU polls `kubectl top` in this process. Always the perftest
# kubeconfig (Rancher download), not ~/.kube/openg2p.yaml.
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/perftest.yaml}"
if [[ ! -f "$KUBECONFIG" ]]; then
  echo "Missing ${KUBECONFIG}. Download it from Rancher and:" >&2
  echo "  export KUBECONFIG=\$HOME/.kube/perftest.yaml" >&2
  exit 1
fi

KUBE_NS="${STAFF_API_KUBE_NAMESPACE:-perftest}"
POD_GREP="${STAFF_API_POD_GREP:-farmer-registry-staff-portal-api}"
if kubectl config get-contexts -o name 2>/dev/null | grep -qx perftest; then
  kubectl config use-context perftest >/dev/null
elif [[ "$(kubectl config current-context 2>/dev/null || true)" != "perftest" ]]; then
  kubectl config rename-context "$(kubectl config current-context)" perftest >/dev/null
fi

echo "=== kubectl (farmer CPU) ==="
echo "KUBECONFIG: ${KUBECONFIG:-<default>}"
echo "context: $(kubectl config current-context 2>/dev/null || echo '<none>')"
if ! kubectl get ns "$KUBE_NS" >/dev/null 2>&1; then
  echo "kubectl cannot see namespace ${KUBE_NS}." >&2
  echo "Download the perftest kubeconfig from Rancher and:" >&2
  echo "  export KUBECONFIG=\$HOME/.kube/perftest.yaml" >&2
  echo "  kubectl get ns ${KUBE_NS}" >&2
  echo "  kubectl top pod -n ${KUBE_NS} --no-headers | grep ${POD_GREP}" >&2
  exit 1
fi
if ! kubectl top pod -n "$KUBE_NS" --no-headers 2>/dev/null | grep -q "$POD_GREP"; then
  echo "kubectl top returned no pods matching ${POD_GREP} in ${KUBE_NS}." >&2
  echo "Check: kubectl top pod -n ${KUBE_NS} --no-headers | grep ${POD_GREP}" >&2
  exit 1
fi
echo "kubectl top ok for ${POD_GREP} in ${KUBE_NS}"
echo "================================="

# STEP is "<ordinal>-<name>", e.g. "1-isolated" -- strip the ordinal, it's
# just for readable ordering in env.sh, not part of any path.
STEP_NAME="${STEP#*-}"

# ISOLATED_SCENARIO uses hyphens (register-read); folder/file names use
# underscores (register_read).
SCENARIO_DIR="${ISOLATED_SCENARIO//-/_}"

# DEBUG: print the run configuration up front, before deriving anything or
# firing Locust, so a misconfigured env.sh is obvious immediately.
echo "=== DEBUG: run configuration ==="
echo "INGRESS:     ${INGRESS}"
echo "VOLUME_TIER: ${VOLUME_TIER}"
echo "POD_SCALE:   ${POD_SCALE}"
echo "STEP:        ${STEP}"
if [ "$STEP_NAME" = isolated ]; then
  echo "SCENARIO:    ${ISOLATED_SCENARIO}"
fi
echo "================================="

case "$STEP_NAME" in
  isolated)
    LOCUSTFILE="staff-api/${SCENARIO_DIR}/${SCENARIO_DIR}_locustfile.py"
    CSV_PREFIX="results/staff-api/${INGRESS}/${VOLUME_TIER}/pod-${POD_SCALE}/${STEP}/${SCENARIO_DIR}/${SCENARIO_DIR}"
    ;;
  blended)
    LOCUSTFILE="staff-api/blended/blended_locustfile.py"
    CSV_PREFIX="results/staff-api/${INGRESS}/${VOLUME_TIER}/pod-${POD_SCALE}/${STEP}/blended"
    ;;
  soak)
    LOCUSTFILE="staff-api/blended/soak_locustfile.py"
    CSV_PREFIX="results/staff-api/${INGRESS}/${VOLUME_TIER}/pod-${POD_SCALE}/${STEP}/soak"
    ;;
  db-sweep)
    echo "db-sweep is not a Locust-fired step -- it's a separate exercise" >&2
    echo "varying PostgreSQL tuning, not something this script drives. See" >&2
    echo "documentation/staff-api/test-scenarios.md §3/§7 \"db-sweep\"." >&2
    exit 1
    ;;
  *)
    echo "Unrecognized STEP='${STEP}' in env.sh (expected 1-isolated |" >&2
    echo "2-blended | 3-soak | 4-db-sweep)" >&2
    exit 1
    ;;
esac

mkdir -p "$(dirname "$CSV_PREFIX")"

if [ "$STEP_NAME" = soak ]; then
  SOAK_USERS="${SOAK_USERS:?set SOAK_USERS in env.sh to 80% of Step 2 freeze users}"
  SOAK_RUN_TIME="${SOAK_RUN_TIME:-8h}"
  echo "8h soak from this laptop will die if the machine sleeps."
  echo "Prefer the in-cluster Job: locust/api/k8s/soak-job.yaml"
  echo "locust -f ${LOCUSTFILE} --headless -u ${SOAK_USERS} -r ${SOAK_USERS} -t ${SOAK_RUN_TIME} --csv ${CSV_PREFIX}"
  locust -f "$LOCUSTFILE" --host "$STAFF_API_BASE" --headless \
    -u "$SOAK_USERS" -r "$SOAK_USERS" -t "$SOAK_RUN_TIME" --csv "$CSV_PREFIX"
else
  echo "locust -f ${LOCUSTFILE} --csv ${CSV_PREFIX}"
  locust -f "$LOCUSTFILE" --host "$STAFF_API_BASE" -u 1 -r 1 --csv "$CSV_PREFIX"
fi
