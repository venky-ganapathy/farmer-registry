# Locust load-test assets (staff-api)

5 working Locust flows against `staff-portal-api`, one per test scenario (see
[`../../documentation/staff-api/test-scenarios.md`](../../documentation/staff-api/test-scenarios.md)
§4 for what each fires and why). `partner-api/` currently has only placeholder
locustfiles (`ingest-pipeline/`, `search/`) — not yet built out to real request
bodies.

## Files
- `shared/` — `base_user.py` (token cache + `_post`/`_post_multipart` helpers),
  `config.py` (env-var config, incl. `SEED_MANIFEST`/`SEARCH_TERMS`/
  `HOUSEHOLD_IDS` loaded from the bulk seeder's manifest), `document_helpers.py`
  (shared `/documents/upload_documents` helpers), `request_builder.py`,
  `response_utils.py`, `token_cache.py`.
- `staff-api/{register_read,cr_create,cr_read_and_approve,intake_create,
  intake_read_and_approve}/` — one locustfile + helpers module per scenario.
- `locustfile.py` — compatibility-only shim so `locust -f locustfile.py`
  still resolves; the real workloads are the per-scenario files above.
- `env.sh` — connection/auth config, plus the `INGRESS`/`VOLUME_TIER`/
  `POD_SCALE`/`STEP`/`ISOLATED_SCENARIO` knobs `locust-staff-api.sh` reads
  (one value active per knob, the rest commented out — edit which line is
  uncommented rather than passing flags). `INGRESS` only *labels* results —
  point `STAFF_API_BASE` at the right host yourself (ClusterIP vs public
  hostname); see [`../../documentation/environment-topology.md`](../../documentation/environment-topology.md) §5.
- `locust-staff-api.sh` — fires the one staff-api Locust run implied by
  `env.sh`'s current knob settings.

## Install

```bash
cd performance-testing/locust/api
python3 -m venv .venv && source .venv/bin/activate
pip install locust requests
```

## Configure (env vars)

```bash
export STAFF_API_BASE=https://staff-farmer-registry.perftest.openg2p.org
export KEYCLOAK_BASE=https://keycloak.perftest.openg2p.org
export KEYCLOAK_REALM=staff
export OIDC_CLIENT_ID=farmer-registry-staff-portal
export OIDC_CLIENT_SECRET=...              # if confidential client
export OIDC_USERNAME=...  OIDC_PASSWORD=...
export SEED_MANIFEST=...  # optional override; defaults to ../../seeding/seed_manifest.json
```

`shared/config.py` also loads `HOUSEHOLD_IDS`/`SEARCH_TERMS` from that
manifest at import time — `intake_create`/`cr_create` will fail loudly (empty
list) if it's missing, since those flows need real seeded ids/anchors. Run
the bulk seeder first (see
[`../../documentation/seeding-design.md`](../../documentation/seeding-design.md)).

## Run

Edit `env.sh` — uncomment the one `INGRESS`, one `VOLUME_TIER`, one
`POD_SCALE`, one `STEP`, and (for `STEP=1-isolated` only) one
`ISOLATED_SCENARIO` line you want — then:

```bash
./locust-staff-api.sh
```

This fires the corresponding locustfile with `--csv` pointed at
`results/staff-api/<ingress>/<volume_tier>/pod-<pod_scale>/<step>/[<scenario>/]`
(matching [`../../documentation/staff-api/test-scenarios.md`](../../documentation/staff-api/test-scenarios.md)
§3/§8's Volume-Tier × Pod-Scale × Step model — no date folder; a rerun
overwrites the same cell unless you edit the script's `CSV_PREFIX`). It never
passes `--headless`/`--autostart`, so it opens the web UI at
`localhost:8089` with `-u`/`-r` pre-filled and waits for you to click "Start
swarming" — `-t` is deliberately omitted since Locust ignores `--run-time`
without one of those two flags.

`STEP=2-blended` uses `staff-api/blended/blended_locustfile.py` (SLO ramp).
`STEP=3-soak` uses `soak_locustfile.py` (fixed users, headless; prefer the
in-cluster Job below). `STEP=4-db-sweep` isn't Locust-fired.

For an ad-hoc one-off outside the `env.sh` knobs, invoke `locust` directly:

```bash
locust -f staff-api/register_read/register_read_locustfile.py \
  --host "$STAFF_API_BASE" -u 50 -r 5 \
  --csv results/staff-api/in-cluster/primary/pod-1/1-isolated/register_read/register_read
```

## Running in-cluster (8h soak)

Do **not** leave Locust on the laptop for 8 hours. Run a Kubernetes Job in
`perftest` against the staff-api ClusterIP. Use
`staff-api/blended/soak_locustfile.py` (no ramp shape) at **80% of the Step 2
freeze user count**, `--headless --run-time 8h`.

1. Confirm the service name:

   `kubectl -n perftest get svc | grep staff-portal-api`

2. Set `SOAK_USERS` to `floor(0.8 * step2_users)` (example: freeze at 40 → 32).
   Label the run `INGRESS=in-cluster`. Point `STAFF_API_BASE` at ClusterIP
   (not the public hostname), e.g.
   `http://farmer-registry-staff-portal-api.perftest.svc.cluster.local`.
   Point `KEYCLOAK_BASE` at the in-cluster Keycloak service
   (`http://commons-keycloak.perftest.svc.cluster.local`). Locust shares one
   OIDC token for the whole process so Keycloak is not stampeded.

3. Build and push an image from this directory (cluster must be able to pull it):

```bash
cd performance-testing/locust/api
docker build -f k8s/Dockerfile -t YOUR_REGISTRY/locust-staff-soak:latest .
docker push YOUR_REGISTRY/locust-staff-soak:latest
```

4. Seed files + OIDC secret (do not put passwords in the Job YAML):

```bash
kubectl -n perftest create configmap locust-perf-seed \
  --from-file="$HOME/Desktop/openg2p/perf-seed" --dry-run=client -o yaml | kubectl apply -f -

kubectl -n perftest create secret generic locust-staff-soak \
  --from-literal=OIDC_CLIENT_SECRET='...' \
  --from-literal=OIDC_USERNAME='nina.patel' \
  --from-literal=OIDC_PASSWORD='...' \
  --dry-run=client -o yaml | kubectl apply -f -
```

5. Edit `k8s/soak-job.yaml`: image, `SOAK_USERS`, ClusterIP if the service
   name differs. Apply and detach:

```bash
kubectl -n perftest apply -f k8s/soak-job.yaml
kubectl -n perftest logs -f job/locust-staff-soak
```

You can close the laptop. Locust still writes CSVs while it runs, but
**do not wait for the Job to Complete** — a finished container cannot be
`kubectl cp`'d and `emptyDir` dies with the pod. The Job sleeps after 8h
and stays Running until you collect:

```bash
kubectl -n perftest logs job/locust-staff-soak | grep SOAK_FINISHED
POD=$(kubectl -n perftest get pod -l job-name=locust-staff-soak -o jsonpath='{.items[0].metadata.name}')
mkdir -p ./results/staff-api/in-cluster
kubectl -n perftest cp "$POD:/results/staff-api/in-cluster/." ./results/staff-api/in-cluster/
kubectl -n perftest delete job locust-staff-soak
```

Do not schedule this Job on the same node as `farmer-registry-staff-portal-api`
if you can avoid it. Report this soak as **in-cluster**; do not mix with
Step 2 end-to-end RPS.

## Notes

- Stats are grouped by logical endpoint via the `name=` argument, so URLs with
  ids don't explode the report — and so two different endpoints never share a
  stats row (see `cr_create`'s two differently-named `get_all_sections` calls).
- For an **open-model** (constant arrival rate) capacity measurement, swap
  `between()` think-time for a constant-pacing/throughput strategy and document
  which model produced each figure.
- Locust CSVs feed the reporting pipeline: `../../scripts/create_raw_report.py`
  (first output — every endpoint, verbatim, into
  `../../documentation/staff-api/raw-report.md`, using the column schemas in
  `templates/staff-api/raw_report_templates/`) then
  `../../scripts/synthesize_report.py` (second output — curated headline
  endpoints + SLO/PASS-FAIL, into `templates/staff-api/synthesize_templates/`).
  See [`../../documentation/staff-api/final-report.md`](../../documentation/staff-api/final-report.md).
