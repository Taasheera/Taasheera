"""Find the most simultaneous users the whole app sustains: run the mixed workload in a loop.

    python loadtest/capacity.py --base-url https://<staging>.cloudfront.net --levels 10,25,50,75,100,150,200

Each level runs `k6 run -e PROFILE=step` for --step-duration. A level passes when server errors
(5xx, timeouts, dropped connections) stay under --max-error-rate and p95 latency of successful
requests stays under --p95-ms. The loop stops at the first failing level, then bisects once between
the last pass and the first failure. While each level runs, /admin-api/system-health is sampled every
15 s (CPU, memory, in-flight requests, server-side p95 and database p95), and CloudWatch CPU and CPU
credits are read afterwards when --instance-id is given, so every result carries its bottleneck.

Environment: LOADTEST_PASSWORD (synthetic users), LOADTEST_ADMIN_EMAIL and LOADTEST_ADMIN_PASSWORD
(the synthetic staging admin, for health samples). Standard library only.
"""
import argparse
import csv
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)  # nearest rank, as the API's own numbers
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


class HealthSampler(threading.Thread):
    """Polls the operator health endpoint as the synthetic admin while a level runs."""

    def __init__(self, base_url, email, password, interval=15):
        super().__init__(daemon=True)
        self.base_url, self.email, self.password, self.interval = base_url, email, password, interval
        self.samples, self.errors = [], 0
        self._halt = threading.Event()
        self._token = None

    def _call(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers={
            "Content-Type": "application/json", "Origin": self.base_url, "X-Request-ID": f"capacity-health-{int(time.time())}",
            **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(request, timeout=20) as reply:
            return json.loads(reply.read() or b"null")

    def run(self):
        if not (self.email and self.password):
            return
        while not self._halt.is_set():
            try:
                if self._token is None:
                    self._token = self._call("POST", "/auth/login", {"email": self.email, "password": self.password})["access_token"]
                health = self._call("GET", "/admin-api/system-health", token=self._token)
                self.samples.append({"at": time.time(), "cpu": health["host"]["cpu_percent"],
                    "memory_used": health["host"]["memory"]["used_percent"],
                    "memory_available_mb": health["host"]["memory"]["available_mb"],
                    "rss_mb": health["process"]["rss_mb"], "in_flight": health["api"]["in_flight"],
                    "server_p95_ms": health["api"]["last_1m"]["p95_ms"], "db_p95_ms": health["api"]["last_1m"]["db_p95_ms"],
                    "server_rps": health["api"]["last_1m"]["requests_per_second"], "db_probe_ms": health["database"]["probe_ms"],
                    "queue": health["queue"]["queued"], "load": health["host"]["load_average"], "status": health["status"]})
            except urllib.error.HTTPError as error:
                self.errors += 1
                if error.code == 401:
                    self._token = None
            except Exception:
                self.errors += 1
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()

    def summary(self):
        def values(key):
            return [sample[key] for sample in self.samples if isinstance(sample.get(key), (int, float))]
        return {"samples": len(self.samples), "sample_errors": self.errors,
                "cpu_max": max(values("cpu"), default=None), "cpu_mean": round(statistics.mean(values("cpu")), 1) if values("cpu") else None,
                "memory_used_max": max(values("memory_used"), default=None),
                "memory_available_min_mb": min(values("memory_available_mb"), default=None),
                "api_rss_max_mb": max(values("rss_mb"), default=None), "in_flight_max": max(values("in_flight"), default=None),
                "server_p95_max_ms": max(values("server_p95_ms"), default=None), "db_p95_max_ms": max(values("db_p95_ms"), default=None),
                "db_probe_max_ms": max(values("db_probe_ms"), default=None), "queue_max": max(values("queue"), default=None)}


def cloudwatch(instance_id, start, end):
    """Max CPU and min CPU credit balance for the window (5-minute basic monitoring)."""
    if not instance_id or not shutil.which("aws"):
        return {}
    result = {}
    for metric, stat in (("CPUUtilization", "Maximum"), ("CPUCreditBalance", "Minimum")):
        try:
            output = subprocess.run(["aws", "cloudwatch", "get-metric-statistics", "--namespace", "AWS/EC2",
                "--metric-name", metric, "--dimensions", f"Name=InstanceId,Value={instance_id}",
                "--start-time", datetime.fromtimestamp(start - 300, timezone.utc).isoformat(),
                "--end-time", datetime.fromtimestamp(end + 300, timezone.utc).isoformat(),
                "--period", "300", "--statistics", stat, "--output", "json"], capture_output=True, text=True, timeout=60, check=True)
            points = [point[stat] for point in json.loads(output.stdout)["Datapoints"]]
            if points:
                result[f"{metric}_{stat}".lower()] = round(max(points) if stat == "Maximum" else min(points), 1)
        except Exception:
            pass
    return result


def endpoint_table(csv_path):
    """Per-endpoint p95, count and server errors from k6's CSV output."""
    durations, errors = {}, {}
    with open(csv_path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("metric_name") != "http_req_duration":
                continue
            name = row.get("name") or "unnamed"
            durations.setdefault(name, []).append(float(row["metric_value"]))
            status = int(row.get("status") or 0)
            errors[name] = errors.get(name, 0) + (status == 0 or status >= 500)
    rows = [{"endpoint": name, "requests": len(values), "p50_ms": round(percentile(values, .5), 1),
             "p95_ms": round(percentile(values, .95), 1), "server_errors": errors.get(name, 0)}
            for name, values in durations.items()]
    return sorted(rows, key=lambda row: row["p95_ms"], reverse=True)


def run_level(args, vus, out_dir, profile="step", duration=None):
    tag = f"{profile}-{vus:04d}"
    summary_file, csv_file = out_dir / f"{tag}.json", out_dir / f"{tag}.csv"
    env = {**os.environ, "BASE_URL": args.base_url, "PROFILE": profile, "VUS": str(vus),
           "DURATION": duration or args.step_duration, "SUMMARY_FILE": str(summary_file),
           "RUN_ID": f"{args.run_id}-{vus}", "SLO_P95_MS": str(args.p95_ms), "SLO_ERROR_RATE": str(args.max_error_rate),
           "USER_COUNT": str(args.users), "THINK_SECONDS": str(args.think)}
    sampler = HealthSampler(args.base_url, os.environ.get("LOADTEST_ADMIN_EMAIL"), os.environ.get("LOADTEST_ADMIN_PASSWORD"))
    sampler.start()
    started = time.time()
    print(f"[{datetime.now():%H:%M:%S}] {profile} {vus} users for {duration or args.step_duration} ...", flush=True)
    completed = subprocess.run([args.k6, "run", "--quiet", "--out", f"csv={csv_file}", str(HERE / "mixed.js")],
                               env=env, cwd=HERE, capture_output=True, text=True)
    ended = time.time()
    sampler.stop()
    sampler.join(timeout=30)
    if not summary_file.exists():
        print(completed.stdout[-2000:], completed.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"k6 produced no summary at {vus} users (exit {completed.returncode}).")
    data = json.loads(summary_file.read_text())
    metric = lambda name, stat: data["metrics"].get(name, {}).get("values", {}).get(stat)
    p95 = metric("http_req_duration{expected_response:true}", "p(95)") or metric("http_req_duration", "p(95)")
    result = {
        "profile": profile, "vus": vus, "duration_s": round(ended - started), "requests": metric("http_reqs", "count"),
        "rps": round(metric("http_reqs", "rate") or 0, 2), "iterations": metric("iterations", "count"),
        "p50_ms": round(metric("http_req_duration", "med") or 0, 1), "p95_ms": round(p95 or 0, 1),
        "p99_ms": round(metric("http_req_duration", "p(99)") or 0, 1),
        "login_p95_ms": round(metric("login_duration", "p(95)") or 0, 1),
        "server_error_rate": round(metric("server_errors", "rate") or 0, 4),
        "http_failed_rate": round(metric("http_req_failed", "rate") or 0, 4),
        "throttled": metric("throttled_responses", "count") or 0,
        "business_rejections": metric("business_rejections", "count") or 0,
        "checks_rate": round(metric("checks", "rate") or 0, 4),
        "server": sampler.summary(), "cloudwatch": cloudwatch(args.instance_id, started, ended),
        "slowest_endpoints": endpoint_table(csv_file)[:8] if csv_file.exists() else [],
    }
    result["passed"] = (result["server_error_rate"] < args.max_error_rate and result["p95_ms"] < args.p95_ms
                        and result["checks_rate"] >= 0.95)
    if not args.keep_csv and csv_file.exists():
        csv_file.unlink()
    verdict = "PASS" if result["passed"] else "FAIL"
    print(f"    {verdict}: {result['rps']} req/s, p95 {result['p95_ms']} ms, server errors "
          f"{result['server_error_rate'] * 100:.2f}%, CPU max {result['server']['cpu_max']}%", flush=True)
    return result


def bottleneck(result):
    """A first reading of what ran out; the report explains it with the evidence."""
    server, cloud = result["server"], result["cloudwatch"]
    slow = result["slowest_endpoints"][0]["endpoint"] if result["slowest_endpoints"] else "unknown"
    if (cloud.get("cpucreditbalance_minimum") or 99) < 5:
        return "CPU credits exhausted: the burstable instance fell back to its baseline CPU share."
    if (server.get("cpu_max") or 0) >= 85:
        return f"Host CPU saturated ({server['cpu_max']}%); slowest endpoint {slow}."
    if (server.get("db_p95_max_ms") or 0) > 200 or (server.get("db_probe_max_ms") or 0) > 100:
        return f"Database time dominates (DB p95 {server.get('db_p95_max_ms')} ms): SQLite write locking."
    if (server.get("memory_available_min_mb") or 9999) < 100:
        return "Memory nearly exhausted."
    if (server.get("in_flight_max") or 0) >= 35:
        return f"Requests queue in the API's worker threads (in flight {server['in_flight_max']}); slowest endpoint {slow}."
    return f"Latency target missed first on {slow}."


def write_report(args, out_dir, levels, capacity, extra):
    report = {"base_url": args.base_url, "run_id": args.run_id, "started": args.started, "finished": datetime.now(timezone.utc).isoformat(),
              "targets": {"p95_ms": args.p95_ms, "max_server_error_rate": args.max_error_rate, "checks_rate": 0.95},
              "workload": {"script": "loadtest/mixed.js", "think_seconds": args.think, "users_seeded": args.users,
                           "mix": {k: os.environ.get(f"MIX_{k.upper()}") or v for k, v in
                                   {"browse": 30, "account": 25, "journey": 35, "documents": 6, "heavy": 4}.items()},
                           "step_duration": args.step_duration},
              "levels": levels, "capacity": capacity, **extra}
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    lines = [f"# Capacity run {args.run_id}", "",
             f"Target {args.base_url}; step {args.step_duration}; think time {args.think} s; p95 < {args.p95_ms} ms; server errors < {args.max_error_rate * 100:.1f}%.", "",
             "| Users | Req/s | p50 ms | p95 ms | p99 ms | Login p95 | 5xx/timeouts | CPU max | Mem used max | DB p95 max | In flight max | CPU credits min | Result |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for level in levels:
        server, cloud = level["server"], level["cloudwatch"]
        lines.append(f"| {level['vus']} | {level['rps']} | {level['p50_ms']} | {level['p95_ms']} | {level['p99_ms']} | {level['login_p95_ms']} | "
                     f"{level['server_error_rate'] * 100:.2f}% | {server.get('cpu_max')}% | {server.get('memory_used_max')}% | "
                     f"{server.get('db_p95_max_ms')} | {server.get('in_flight_max')} | {cloud.get('cpucreditbalance_minimum', '—')} | "
                     f"{'pass' if level['passed'] else 'FAIL'} |")
    lines += ["", f"**Maximum sustainable simultaneous users: {capacity.get('users')}** "
              f"({capacity.get('rps')} req/s at p95 {capacity.get('p95_ms')} ms).", "",
              f"First bottleneck: {capacity.get('bottleneck')}", ""]
    failing = next((level for level in levels if not level["passed"]), None)
    if failing:
        lines += [f"## Slowest endpoints at {failing['vus']} users", "", "| Endpoint | Requests | p50 ms | p95 ms | 5xx |", "|---|---:|---:|---:|---:|"]
        lines += [f"| {row['endpoint']} | {row['requests']} | {row['p50_ms']} | {row['p95_ms']} | {row['server_errors']} |"
                  for row in failing["slowest_endpoints"]]
    for name, result in extra.items():
        lines += ["", f"## {name.title()}", "", "```json", json.dumps(result, indent=1)[:4000], "```"]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")
    print(f"\nReport: {out_dir / 'report.md'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--levels", default="10,25,50,75,100,150,200,300")
    parser.add_argument("--step-duration", default="3m")
    parser.add_argument("--p95-ms", type=float, default=1500)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--users", type=int, default=300, help="synthetic accounts seeded on staging")
    parser.add_argument("--think", type=float, default=3, help="seconds between a user's actions")
    parser.add_argument("--instance-id", default="", help="staging EC2 instance, for CloudWatch CPU/credits")
    parser.add_argument("--no-bisect", action="store_true")
    parser.add_argument("--spike", action="store_true", help="after the loop, spike to the failing level")
    parser.add_argument("--soak", default="", help="after the loop, hold 70%% of capacity for this long, e.g. 30m")
    parser.add_argument("--keep-csv", action="store_true")
    parser.add_argument("--k6", default=shutil.which("k6") or "k6")
    parser.add_argument("--out", default=str(HERE / "results"))
    args = parser.parse_args()
    if not os.environ.get("LOADTEST_PASSWORD"):
        raise SystemExit("Set LOADTEST_PASSWORD (the synthetic staging accounts' password).")
    args.base_url = args.base_url.rstrip("/")
    args.started = datetime.now(timezone.utc).isoformat()
    args.run_id = datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(args.out) / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    levels, last_pass, first_fail = [], None, None
    for vus in [int(value) for value in args.levels.split(",") if value.strip()]:
        result = run_level(args, vus, out_dir)
        levels.append(result)
        if result["passed"]:
            last_pass = result
        else:
            first_fail = result
            break
    if first_fail and last_pass and not args.no_bisect and first_fail["vus"] - last_pass["vus"] > 5:
        middle = (first_fail["vus"] + last_pass["vus"]) // 2
        result = run_level(args, middle, out_dir)
        levels.append(result)
        if result["passed"]:
            last_pass = result
        else:
            first_fail = result
    levels.sort(key=lambda level: level["vus"])
    capacity = {"users": last_pass["vus"] if last_pass else 0, "rps": last_pass["rps"] if last_pass else 0,
                "p95_ms": last_pass["p95_ms"] if last_pass else None,
                "first_failure_users": first_fail["vus"] if first_fail else None,
                "bottleneck": bottleneck(first_fail) if first_fail else "No level failed; raise --levels."}
    extra = {}
    if args.spike and first_fail:
        extra["spike"] = run_level(args, first_fail["vus"], out_dir, profile="spike", duration="8m")
    if args.soak and last_pass:
        extra["soak"] = run_level(args, max(1, int(last_pass["vus"] * 0.7)), out_dir, profile="soak", duration=args.soak)
    write_report(args, out_dir, levels, capacity, extra)


if __name__ == "__main__":
    main()
