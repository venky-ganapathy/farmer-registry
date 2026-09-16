# Ingress -- which network path STAFF_API_BASE below actually reaches:
# in-cluster (Locust hitting the service's ClusterIP -- no RP hop, isolates
# the microservice) or end-to-end (through the public RP hostname, the real
# client path, RP can itself bottleneck). This only *labels* results --
# changing it does NOT change STAFF_API_BASE; you must point STAFF_API_BASE
# at the right host yourself. See documentation/environment-topology.md §5.
# Uncomment exactly one.
# export INGRESS=in-cluster
export INGRESS=end-to-end

export STAFF_API_BASE=https://staff-farmer-registry.perftest.openg2p.org
export KEYCLOAK_BASE=https://keycloak.perftest.openg2p.org
export KEYCLOAK_REALM=staff
export OIDC_CLIENT_ID=farmer-registry-staff-portal
export OIDC_CLIENT_SECRET=12yTrCklvC2obIdAV6DHPkva1P6r1LbP
export OIDC_USERNAME=nina.patel
export OIDC_PASSWORD=password
export SEARCH_PAGE_SIZE=10
export MAX_SEARCH_PAGE=10
export TAB_ITERATION_MIN_WAIT_SECONDS=0.0
export TAB_ITERATION_MAX_WAIT_SECONDS=0.0

# Volume-Tier -- see documentation/staff-api/test-scenarios.md §3. Uncomment exactly one.
# export VOLUME_TIER=smoke
export VOLUME_TIER=primary
# export VOLUME_TIER=stretch
# export VOLUME_TIER=stress

# Pod-Scale -- app replica count under test. Uncomment exactly one.
# export POD_SCALE=1
# export POD_SCALE=2
export POD_SCALE=3

# Step -- see documentation/staff-api/test-scenarios.md §3/§7. Uncomment exactly one.
# export STEP=1-isolated
export STEP=2-blended
# export STEP=3-soak
# export STEP=4-db-sweep

# Only used when STEP=1-isolated (picks which of the 5 scenarios to fire).
# Uncomment exactly one.
# export ISOLATED_SCENARIO=register-read
# export ISOLATED_SCENARIO=cr-create
# export ISOLATED_SCENARIO=cr-read-and-approve
# export ISOLATED_SCENARIO=intake-create
# export ISOLATED_SCENARIO=intake-read-and-approve

# =============================================================================
# SLOs -- documentation/staff-api/test-scenarios.md §5.
# One line per Locust `name=`: "<endpoint> <p95_ms> <p99_ms>".
# Each endpoint owns its own pair (no shared class SLO).
# Bands from primary isolated stats: read 800/1000, search 900/1000,
# write 1000/1200, write+AWE +200 → 1200/1400.
# shared/slo_shape.py parses ENDPOINT_SLOS at import time.
# =============================================================================

# name p95_ms p99_ms
export ENDPOINT_SLOS="
get_all_tabs 800 1000
get_all_sections 800 1000
get_tab_sections 800 1000
get_section_ui_schema 800 1000
get_attribute_values 800 1000
render_intake_form 800 1000
get_register_summary_data 800 1000
get_subject_record 800 1000
get_tab_records 800 1000
get_number_of_versions 800 1000
get_version_dates 800 1000
get_versions_for_a_date 800 1000
get_deduplication_register_results 800 1000
get_change_request 800 1000
check_change_request_sequence 800 1000
get_deduplication_change_request_results 800 1000
get_number_of_pending_change_requests 800 1000
get_change_requests 800 1000
get_register_change_request_summary_data 800 1000
get_intake_form_submission 800 1000
get_intake_form_submissions_summary 800 1000
get_deduplication_intake_form_register_results 800 1000
get_deduplication_intake_form_intake_form_results 800 1000
get_file_url 800 1000
get_change_request_documents 800 1000
get_intake_form_documents 800 1000
list_tasks_for_request 1000 1200
search_in_a_register 900 1000
search_in_change_request 1300 1400
search_in_intake_form_submissions 900 1000
save_intake_form_submission 1000 1200
upload_documents 1000 1200
create_change_request 1200 1400
create_change_request_for_core_data 1200 1400
finalize_intake_form_submission 1200 1400
submit_task_decision 1200 1400
"

# No per-user RPS cap — each user fires sequential HTTP as fast as the API
# answers. Ramp users until CPU/SLO, freeze that count, soak, then stop.
# Ignore one-off spikes: SLO needs 2 consecutive 30s windows, CPU 2 polls.
export MAX_USERS=100
export SUSTAIN_MINUTES=5
export SLO_BREACH_STEPS=2
export CPU_BREACH_POLLS=2

# Freeze user count when enough replicas hit this many cores:
# 3+ pods → 2 over limit; 1 or 2 pods → 1 over limit.
export CPU_BREACH_CORES=1.85
# Step 3 soak user count is documented here for the in-cluster Job only.
# Do not export SOAK_MAX_RPS / STAFF_API_HEADLESS / IN_CLUSTER_SOAK here —
# those exist only in k8s/soak-job.yaml so Step 1/2 laptop runs stay uncapped.
export SOAK_USERS=36
export SOAK_RUN_TIME=8h
export STAFF_API_KUBE_NAMESPACE=perftest
export STAFF_API_POD_GREP=farmer-registry-staff-portal-api

# get_record_history is omitted from ENDPOINT_SLOS -- known SYS-ERR-001
# (see ../seeding-design.md) would freeze the ramp on the first step.

echo "------------------"
echo "------ ENV -------"
echo "------------------"

echo "$INGRESS"
echo "$STAFF_API_BASE"
echo "$KEYCLOAK_BASE"
echo "$KEYCLOAK_REALM"
echo "$OIDC_CLIENT_ID"
echo "$OIDC_CLIENT_SECRET"
echo "$OIDC_USERNAME"
echo "$OIDC_PASSWORD"
echo "$SEED_MANIFEST"
echo "$SEARCH_PAGE_SIZE"
echo "$MAX_SEARCH_PAGE"
echo "$TAB_ITERATION_MIN_WAIT_SECONDS"
echo "$TAB_ITERATION_MAX_WAIT_SECONDS"
echo "$VOLUME_TIER"
echo "$POD_SCALE"
echo "$STEP"
echo "$ISOLATED_SCENARIO"
echo "$MAX_USERS"
echo "$SUSTAIN_MINUTES"
echo "$SLO_BREACH_STEPS"
echo "$CPU_BREACH_POLLS"
echo "$CPU_BREACH_CORES"
echo "$STAFF_API_KUBE_NAMESPACE"
echo "$STAFF_API_POD_GREP"
