import html
import json
import os
import subprocess
from collections import Counter

from locust import LoadTestShape, events
from locust.stats import calculate_response_time_percentile

def _load_endpoint_slo_ms() -> tuple[dict[str, int], dict[str, int]]:
    """Per-endpoint p95/p99 (ms) from env.sh ENDPOINT_SLOS.

    Each line is ``<locust name> <p95_ms> <p99_ms>``. A run only stats the
    endpoints it fires, so the shape checks that subset against each name's
    own pair -- no shared class SLO.
    """
    p95_mapping: dict[str, int] = {}
    p99_mapping: dict[str, int] = {}
    for raw_line in os.environ.get("ENDPOINT_SLOS", "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        p95_mapping[name] = int(parts[1])
        if len(parts) >= 3:
            p99_mapping[name] = int(parts[2])
    return p95_mapping, p99_mapping


ENDPOINT_SLO_P95_MS, ENDPOINT_SLO_P99_MS = _load_endpoint_slo_ms()

# Farmer staff-api CPU snapshot for the separate /farmer-cpu page (not mixed
# into Locust Statistics). Freeze logic still uses CPU_BREACH_CORES in tick().
STAFF_API_CPU: dict = {
    "hottest_cores": None,
    "total_cores": None,
    "limit_cores": float(os.environ.get("CPU_BREACH_CORES", "1.8")),
    "over_limit": 0,
    "need_over_limit": 1,
    "polls": 0,
    "error": None,
    "pods": [],
}

_POD_CPU_EXTREMA: dict[str, tuple[float, float]] = {}


def _short_pod_name(name: str) -> str:
    suffix = name.rsplit("-", 1)[-1]
    return suffix if suffix else name


def _farmer_cpu_live_html() -> str:
    snapshot = json.dumps(STAFF_API_CPU, indent=2)
    snapshot_safe = html.escape(snapshot)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Farmer staff-api CPU</title>
  <style>
    body {{ font-family: sans-serif; margin: 16px; }}
    table {{ border-collapse: collapse; }}
    td, th {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
    pre {{ background: #111; color: #eee; padding: 12px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Farmer staff-portal-api CPU</h1>
  <p>Live (polls every 2s). Locust Statistics is unchanged. Raw JSON:
     <a href="/staff-api-cpu.json">/staff-api-cpu.json</a></p>
  <p id="summary"></p>
  <table>
    <thead><tr><th>pod</th><th>cpu cores</th><th>min</th><th>max</th></tr></thead>
    <tbody id="pods"></tbody>
  </table>
  <pre id="json">{snapshot_safe}</pre>
  <script>
    async function refresh() {{
      const res = await fetch("/staff-api-cpu.json", {{ cache: "no-store" }});
      const data = await res.json();
      document.getElementById("json").textContent = JSON.stringify(data, null, 2);
      const pods = data.pods || [];
      document.getElementById("summary").textContent =
        "pods=" + pods.length +
        " total=" + data.total_cores +
        " hottest=" + data.hottest_cores +
        " over=" + data.over_limit + "/" + data.need_over_limit +
        " limit=" + data.limit_cores +
        " polls=" + data.polls +
        (data.error ? " error=" + data.error : "");
      const tbody = document.getElementById("pods");
      tbody.innerHTML = "";
      if (!pods.length) {{
        tbody.innerHTML = "<tr><td colspan='4'>no farmer staff-api pods yet</td></tr>";
        return;
      }}
      for (const pod of pods) {{
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + pod.name + "</td>" +
          "<td>" + Number(pod.cores).toFixed(3) + "</td>" +
          "<td>" + Number(pod.min_cores).toFixed(3) + "</td>" +
          "<td>" + Number(pod.max_cores).toFixed(3) + "</td>";
        tbody.appendChild(tr);
      }}
    }}
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


@events.init.add_listener
def _attach_farmer_cpu_page(environment, **kwargs):
    if not environment.web_ui:
        return
    try:
        from flask import jsonify, make_response
    except ImportError:
        return

    @environment.web_ui.app.route("/staff-api-cpu.json")
    def staff_api_cpu_json():
        response = jsonify(STAFF_API_CPU)
        response.headers["Cache-Control"] = "no-store"
        return response

    @environment.web_ui.app.route("/staff-api-cpu")
    def staff_api_cpu():
        response = make_response(_farmer_cpu_live_html())
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    @environment.web_ui.app.route("/farmer-cpu")
    def farmer_cpu_page():
        response = make_response(_farmer_cpu_live_html())
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response


def _parse_kubectl_cpu(token: str) -> float:
    """Convert `kubectl top` CPU tokens (e.g. 1999m, 1.80, 2) to cores."""
    token = token.strip()
    if token.endswith("m"):
        return float(token[:-1]) / 1000.0
    return float(token)


class SLOStepRampShape(LoadTestShape):
    """
    Step-1 isolated ramp, driven by ENDPOINT_SLO_P95_MS / ENDPOINT_SLO_P99_MS
    (env.sh ENDPOINT_SLOS). Checks only endpoints this run actually fires.

    Starts with warmup_seconds at warmup_users with NO SLO checks. Then ramps
    +step_users every step_seconds up to max_users. There is no per-user RPS
    cap; each user fires sequential HTTP as fast as the API answers. CPU
    freeze: if 3+ replicas, 2 pods must be at CPU_BREACH_CORES; if 1 or 2
    replicas, 1 pod is enough. A single SLO or CPU spike does not freeze:
    SLO needs SLO_BREACH_STEPS consecutive step windows, CPU needs
    CPU_BREACH_POLLS consecutive polls. On confirmed CPU or SLO, freeze
    that user count (no step-down) and soak sustain_seconds. Reaching
    max_users also soaks then stops.

    503s/other failures are logged but do not freeze the ramp and do not
    feed p95/p99. Only successful request times count.

    NOTE: -u/-r/-t are ignored once a custom shape is active.

    NOTE: register_read's get_record_history is omitted from ENDPOINT_SLOS
    (SYS-ERR-001).
    """

    # Base class, not meant to be run directly -- must NOT be auto-detected
    # by Locust as a runnable shape (only its per-scenario subclasses, e.g.
    # RegisterReadRampShape, should be). Locust's load_locustfile treats any
    # LoadTestShape subclass as runnable unless abstract=True is set
    # explicitly in that class's own body (see
    # locust/util/load_locustfile.py's is_shape_class) -- without this, a
    # locustfile that merely imports SLOStepRampShape (to subclass it) would
    # have Locust pick up two candidate shapes: this base class AND the
    # subclass, arbitrarily choosing one.
    abstract = True

    endpoint_slo_p95_ms: dict[str, int] = ENDPOINT_SLO_P95_MS
    endpoint_slo_p99_ms: dict[str, int] = ENDPOINT_SLO_P99_MS
    step_seconds = 30
    step_users = 4
    max_users = int(os.environ.get("MAX_USERS", "100"))
    # Docs §7: warm 1–5 min and discard. Hold at warmup_users with no SLO
    # checks so cold-start latency cannot freeze the ramp.
    warmup_seconds = 1 * 20
    warmup_users = 2
    # Need enough success samples for a meaningful percentile; 5 made p95
    # ≈ max-of-5 and let a single outlier freeze the ramp.
    min_requests_for_check = 100
    sustain_seconds = int(float(os.environ.get("SUSTAIN_MINUTES", "10")) * 60)
    cpu_breach_cores = float(os.environ.get("CPU_BREACH_CORES", "1.8"))
    cpu_breach_polls = int(os.environ.get("CPU_BREACH_POLLS", "2"))
    slo_breach_steps = int(os.environ.get("SLO_BREACH_STEPS", "2"))
    kube_namespace = os.environ.get("STAFF_API_KUBE_NAMESPACE", "perftest")
    pod_grep = os.environ.get("STAFF_API_POD_GREP", "farmer-registry-staff-portal-api")
    cpu_poll_seconds = 10

    @staticmethod
    def _cpu_over_need(replica_count: int) -> int:
        """How many staff-api pods must be at CPU_BREACH_CORES to freeze.

        3+ (POD_SCALE or observed replicas) → 2. Otherwise → 1.
        """
        raw = os.environ.get("POD_SCALE", "").strip()
        scale = int(raw) if raw.isdigit() else 0
        n = scale if scale > 0 else replica_count
        return 2 if n >= 3 else 1

    def __init__(self):
        super().__init__()
        self._step = -1
        self._step_start: dict[tuple[str, str], tuple[int, int]] = {}
        self._step_success_times: dict[tuple[str, str], list[int]] = {}
        self._listener_registered = False
        self._warmup_done_logged = False
        self._cpu_skip_logged = False
        self._last_cpu_poll_at = 0.0
        self._last_cpu_cores: float | None = None
        self._last_hot_cores: float | None = None
        self._last_cpu_over: int = 0
        self._last_cpu_need: int = 1
        self._cpu_hot_polls = 0
        self._cpu_streak_poll_at = 0.0
        self._slo_hot_steps = 0
        self._slo_counted_this_step = False
        self._hold_users: int | None = None
        self._hold_run_time: float | None = None
        self._hold_reason: str | None = None
        self._soak_started = False
        self._soak_last_report_at: float | None = None
        self._soak_slo_failed = False
        self._tracked_names = set(self.endpoint_slo_p95_ms) | set(self.endpoint_slo_p99_ms)

    def _on_request(self, request_type, name, response_time, response_length, exception=None, **_kwargs):
        # Only successful requests' latencies feed the SLO percentile check
        # -- a failed request's response_time isn't a real latency sample
        # (e.g. a connection reset returns almost instantly), and mixing it
        # in would let failures indirectly trigger/avoid a breach.
        if exception is not None:
            return
        if name not in self._tracked_names:
            return
        # Warmup traffic is discarded — do not accumulate samples that
        # would leak into the first ramp step's percentile check.
        if self.get_run_time() < self.warmup_seconds:
            return
        self._step_success_times.setdefault((name, request_type), []).append(response_time)

    def _percentile(self, name: str, method: str, percent: float) -> int | None:
        times = self._step_success_times.get((name, method))
        if not times:
            return None
        histogram = Counter(times)
        return calculate_response_time_percentile(histogram, len(times), percent)

    def _staff_api_cpu_cores(self, run_time: float) -> float | None:
        if not self.kube_namespace:
            return None
        if run_time - self._last_cpu_poll_at < self.cpu_poll_seconds and self._last_cpu_poll_at > 0:
            return self._last_hot_cores
        self._last_cpu_poll_at = run_time
        # Same kube context as locust-staff-api.sh — log in before starting Locust.
        try:
            result = subprocess.run(
                ["kubectl", "top", "pod", "-n", self.kube_namespace, "--no-headers"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            if not self._cpu_skip_logged:
                print(f"[shape] CPU check skipped (kubectl top failed: {exc})")
                self._cpu_skip_logged = True
            STAFF_API_CPU["error"] = str(exc)
            self._last_cpu_cores = None
            self._last_hot_cores = None
            self._last_cpu_over = 0
            self._last_cpu_need = 1
            return None
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "non-zero exit").strip()
            STAFF_API_CPU["error"] = err
            if not self._cpu_skip_logged:
                print(f"[shape] CPU check skipped (kubectl top: {err})")
                self._cpu_skip_logged = True
            self._last_cpu_cores = None
            self._last_hot_cores = None
            self._last_cpu_over = 0
            self._last_cpu_need = 1
            return None
        pods: list[dict] = []
        needle = self.pod_grep
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name, cpu_token = parts[0], parts[1]
            if needle not in name:
                continue
            if "locust" in name:
                continue
            try:
                cores = _parse_kubectl_cpu(cpu_token)
            except ValueError:
                continue
            prev = _POD_CPU_EXTREMA.get(name)
            min_c = cores if prev is None else min(prev[0], cores)
            max_c = cores if prev is None else max(prev[1], cores)
            _POD_CPU_EXTREMA[name] = (min_c, max_c)
            pods.append(
                {
                    "name": name,
                    "short_name": _short_pod_name(name),
                    "cores": cores,
                    "min_cores": min_c,
                    "max_cores": max_c,
                }
            )
        if not pods:
            STAFF_API_CPU["error"] = f"no pods matching {needle!r} in {self.kube_namespace}"
            if not self._cpu_skip_logged:
                print(
                    f"[shape] CPU check skipped (no pods matching "
                    f"{needle!r} in namespace {self.kube_namespace})"
                )
                self._cpu_skip_logged = True
            self._last_cpu_cores = None
            self._last_hot_cores = None
            self._last_cpu_over = 0
            self._last_cpu_need = 1
            return None
        pods.sort(key=lambda pod: pod["name"])
        total = sum(float(pod["cores"]) for pod in pods)
        hottest = max(float(pod["cores"]) for pod in pods)
        over = sum(
            1 for pod in pods if float(pod["cores"]) >= self.cpu_breach_cores
        )
        # POD_SCALE=3 (or 3+ pods in top): two replicas must be >= 1.85.
        # 1 or 2: one replica is enough. Prefer POD_SCALE so a 3-pod run
        # never freezes on a single hot pod if top briefly lists fewer.
        need = self._cpu_over_need(len(pods))
        self._last_cpu_cores = total
        self._last_hot_cores = hottest
        self._last_cpu_over = over
        self._last_cpu_need = need
        self._publish_cpu(pods, total, hottest, over, need)
        return hottest

    def _cpu_quorum_hot(self, run_time: float) -> bool:
        """True when enough replicas are at CPU_BREACH_CORES (2 of 3+, else 1)."""
        if self.cpu_breach_cores <= 0:
            return False
        cpu = self._staff_api_cpu_cores(run_time)
        if cpu is None:
            return False
        return self._last_cpu_over >= self._last_cpu_need

    def _publish_cpu(
        self, pods: list[dict], total: float, hottest: float, over: int, need: int
    ) -> None:
        STAFF_API_CPU["pods"] = pods
        STAFF_API_CPU["hottest_cores"] = hottest
        STAFF_API_CPU["total_cores"] = total
        STAFF_API_CPU["limit_cores"] = self.cpu_breach_cores
        STAFF_API_CPU["over_limit"] = over
        STAFF_API_CPU["need_over_limit"] = need
        STAFF_API_CPU["error"] = None
        STAFF_API_CPU["polls"] = int(STAFF_API_CPU.get("polls") or 0) + 1
        per_pod = " ".join(
            f"{pod['short_name']}={pod['cores']:.2f}c" for pod in pods
        )
        print(
            f"[shape] farmer staff-api cpu pods={len(pods)} total={total:.2f}c "
            f"over={over}/{need} | {per_pod}"
        )

    def _cpu_log_bit(self, hottest: float | None) -> str:
        if hottest is None:
            return ""
        pods = STAFF_API_CPU.get("pods") or []
        if not pods:
            return f" hottest={hottest:.2f}c"
        per_pod = " ".join(f"{pod['short_name']}={pod['cores']:.2f}c" for pod in pods)
        total = STAFF_API_CPU.get("total_cores")
        total_bit = f" total={total:.2f}c" if total is not None else ""
        over = STAFF_API_CPU.get("over_limit", 0)
        need = STAFF_API_CPU.get("need_over_limit", 1)
        return f" hottest={hottest:.2f}c{total_bit} over={over}/{need} | {per_pod}"

    def _print_latency_snapshot(self, label: str) -> tuple[bool, bool]:
        """Print p95/p99 vs SLO for every tracked endpoint with enough samples.

        Returns (had_enough_samples, any_endpoint_over_slo).
        """
        breached = False
        printed = False
        for (name, method), times in sorted(self._step_success_times.items()):
            if name not in self._tracked_names:
                continue
            if len(times) < self.min_requests_for_check:
                continue
            printed = True
            p95 = self._percentile(name, method, 0.95)
            p99 = self._percentile(name, method, 0.99)
            slo95 = self.endpoint_slo_p95_ms.get(name)
            slo99 = self.endpoint_slo_p99_ms.get(name)
            bits = [f"{name} n={len(times)}"]
            if p95 is not None and slo95 is not None:
                bits.append(f"p95={p95}ms/{slo95}ms")
                if p95 > slo95:
                    breached = True
            if p99 is not None and slo99 is not None:
                bits.append(f"p99={p99}ms/{slo99}ms")
                if p99 > slo99:
                    breached = True
            print(f"[shape] {label}: {' '.join(bits)}")
        if not printed:
            print(
                f"[shape] {label}: not enough successes yet "
                f"(need ≥{self.min_requests_for_check}/endpoint)"
            )
        return printed, breached

    def _slo_breached_this_window(self, user_count: int, entries) -> bool:
        for (name, method), entry in entries.items():
            if name not in self._tracked_names:
                continue
            start_requests, start_failures = self._step_start.get((name, method), (0, 0))
            requests_this_step = entry.num_requests - start_requests
            failures_this_step = entry.num_failures - start_failures
            success_times = self._step_success_times.get((name, method), [])
            if failures_this_step > 0 and requests_this_step >= self.min_requests_for_check:
                print(
                    f"[shape] {name} had {failures_this_step} failure(s) "
                    f"at {user_count} users (logged, not stopping)"
                )
            if len(success_times) < self.min_requests_for_check:
                continue

            slo95 = self.endpoint_slo_p95_ms.get(name)
            if slo95 is not None:
                p95 = self._percentile(name, method, 0.95)
                if p95 is not None and p95 > slo95:
                    print(
                        f"[shape] SLO breach: {name} p95={p95}ms > SLO-95={slo95}ms "
                        f"at {user_count} users (n={len(success_times)})"
                    )
                    return True
            slo99 = self.endpoint_slo_p99_ms.get(name)
            if slo99 is not None:
                p99 = self._percentile(name, method, 0.99)
                if p99 is not None and p99 > slo99:
                    print(
                        f"[shape] SLO breach: {name} p99={p99}ms > SLO-99={slo99}ms "
                        f"at {user_count} users (n={len(success_times)})"
                    )
                    return True
        return False

    def _slo_window_status(self, user_count: int, entries) -> str:
        """Return 'hot', 'ok', or 'wait' (not enough samples yet)."""
        if self._slo_breached_this_window(user_count, entries):
            return "hot"
        for (name, method), times in self._step_success_times.items():
            if name in self._tracked_names and len(times) >= self.min_requests_for_check:
                return "ok"
        return "wait"

    def _cpu_confirmed_hot(self, run_time: float) -> bool:
        """True only after CPU_BREACH_POLLS consecutive hot kubectl polls."""
        hot = self._cpu_quorum_hot(run_time)
        if self._last_cpu_poll_at != self._cpu_streak_poll_at:
            self._cpu_streak_poll_at = self._last_cpu_poll_at
            if hot:
                self._cpu_hot_polls += 1
                print(
                    f"[shape] CPU quorum {self._cpu_hot_polls}/{self.cpu_breach_polls} "
                    f"over={self._last_cpu_over}/{self._last_cpu_need} "
                    f"hottest={self._last_hot_cores}"
                )
            elif self._cpu_hot_polls:
                print("[shape] CPU spike ignored; poll recovered")
                self._cpu_hot_polls = 0
        return hot and self._cpu_hot_polls >= self.cpu_breach_polls

    def _note_slo_window(self, user_count: int, entries, run_time: float) -> bool:
        """Count at most one SLO verdict per step. Freeze after consecutive hots."""
        if self._slo_counted_this_step:
            return False
        status = self._slo_window_status(user_count, entries)
        if status == "wait":
            return False
        self._slo_counted_this_step = True
        if status == "hot":
            self._slo_hot_steps += 1
            print(
                f"[shape] SLO window {self._slo_hot_steps}/{self.slo_breach_steps} "
                f"at {user_count} users"
            )
            if self._slo_hot_steps >= self.slo_breach_steps:
                self._freeze_users(user_count, run_time, f"SLO at {user_count} users")
                return True
            print("[shape] SLO spike ignored; need another consecutive window")
            return False
        if self._slo_hot_steps:
            print("[shape] SLO spike ignored; window recovered")
        self._slo_hot_steps = 0
        return False

    def _freeze_users(self, user_count: int, run_time: float, reason: str) -> None:
        if self._hold_users is not None:
            return
        self._hold_users = user_count
        self._hold_run_time = run_time
        self._hold_reason = reason
        self._soak_started = False
        self._soak_last_report_at = None
        self._soak_slo_failed = False
        print(
            f"[shape] freeze at {user_count} users ({reason}); "
            f"soaking {self.sustain_seconds}s — same users, no RPS cap"
        )

    def _tick_soak(self, run_time: float, entries):
        self._staff_api_cpu_cores(run_time)
        if not self._soak_started:
            self._soak_started = True
            self._step_success_times = {}
            self._step_start = {
                key: (e.num_requests, e.num_failures) for key, e in entries.items()
            }
            self._soak_last_report_at = run_time
        soak_elapsed = run_time - (self._hold_run_time or run_time)
        if (
            self._soak_last_report_at is not None
            and run_time - self._soak_last_report_at >= self.step_seconds
        ):
            cpu = self._staff_api_cpu_cores(run_time)
            cpu_bit = self._cpu_log_bit(cpu)
            label = (
                f"soak {int(soak_elapsed)}s/{self.sustain_seconds}s "
                f"@ {self._hold_users} users{cpu_bit}"
            )
            _, slo_hot = self._print_latency_snapshot(label)
            if slo_hot:
                self._soak_slo_failed = True
                print("[shape] soak window is above SLO (continuing until sustain ends)")
            self._soak_last_report_at = run_time
        if soak_elapsed >= self.sustain_seconds:
            cpu = self._staff_api_cpu_cores(run_time)
            cpu_bit = self._cpu_log_bit(cpu)
            verdict = "FAIL" if self._soak_slo_failed else "PASS"
            print(
                f"[shape] soak complete: {verdict} {self.sustain_seconds}s at "
                f"{self._hold_users} users ({self._hold_reason}){cpu_bit}"
            )
            self._print_latency_snapshot("soak final")
            return None
        return (self._hold_users, self.step_users)

    def tick(self):
        if not self._listener_registered:
            # self.runner is only attached after __init__ (see
            # Environment._create_runner), so the listener is registered
            # lazily on first tick instead.
            self.runner.environment.events.request.add_listener(self._on_request)
            self._listener_registered = True

        run_time = self.get_run_time()
        entries = self.runner.stats.entries

        # Warmup: fixed low concurrency, no SLO checks, samples discarded
        # (see _on_request). Matches documentation/staff-api/test-scenarios.md
        # §7 prep ("warm up 3–5 min … discard this window").
        if run_time < self.warmup_seconds:
            self._staff_api_cpu_cores(run_time)
            return (self.warmup_users, self.step_users)

        if not self._warmup_done_logged:
            self._warmup_done_logged = True
            # Reset step bookkeeping so the first ramp step starts clean.
            self._step = -1
            self._step_start = {}
            self._step_success_times = {}
            print(
                f"[shape] warmup complete ({self.warmup_seconds}s at "
                f"{self.warmup_users} users); starting ramp "
                f"(SLO check needs ≥{self.min_requests_for_check} successes/endpoint/step; "
                f"no RPS cap; freeze users then soak; "
                f"CPU 2-of-3 or 1-of-1/2 at {self.cpu_breach_cores} cores "
                f"({self.cpu_breach_polls} polls); SLO {self.slo_breach_steps} windows)"
            )

        if self._hold_users is not None:
            return self._tick_soak(run_time, entries)

        # Ramp clock starts after warmup so step 0 is not mixed with cold traffic.
        ramp_time = run_time - self.warmup_seconds
        step = int(ramp_time // self.step_seconds)
        user_count = min(self.max_users, self.step_users * (step + 1))

        if self._cpu_confirmed_hot(run_time):
            hottest = self._last_hot_cores
            hottest_bit = f"{hottest:.2f}c" if hottest is not None else "?"
            self._freeze_users(
                user_count,
                run_time,
                f"CPU {self._last_cpu_over}/{self._last_cpu_need} pods "
                f">={self.cpu_breach_cores}c hottest={hottest_bit}",
            )
            return self._tick_soak(run_time, entries)

        if step != self._step:
            self._step = step
            self._slo_counted_this_step = False
            self._step_start = {key: (e.num_requests, e.num_failures) for key, e in entries.items()}
            self._step_success_times = {}
        elif self._note_slo_window(user_count, entries, run_time):
            return self._tick_soak(run_time, entries)

        if user_count >= self.max_users:
            self._freeze_users(user_count, run_time, f"max_users={self.max_users}")
            return self._tick_soak(run_time, entries)

        return (user_count, self.step_users)
