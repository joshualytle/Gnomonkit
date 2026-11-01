"""Run a round: one sealed `claude -p` session per (scenario, arm, rep), scored and recorded.

Safety rails, all on by default:
  * refuses an output dir inside a git work tree or a forbidden root (setup.check_out_dir);
  * refuses to run on the bare host unless allow_host=True, and refuses the `custom` arm (third-party
    plugin code) outside container mode;
  * the child environment is an allowlist (setup.round_env), never a copy of the caller's;
  * a timed-out run is killed with its whole process tree, by PID, never by program name;
  * stops scheduling new runs once spent cost reaches max_cost;
  * stops the round when the first run fails without producing a result (CLI missing, not logged in);
  * a run whose repo setup fails (git init/add/commit, disk contention) is retried once, then recorded
    as an errored run with git's stderr; the round goes on, since nothing about the CLI is broken.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import date
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from bench import proctree
from bench.arms import Arm
from bench.metrics import metrics
from bench.scenario import Scenario
from bench.setup import build_cmd, check_out_dir, empty_mcp_config, round_env, setup_run
from bench.stream import parse_stream

SCHEMA = "gnomonkit-bench/v1"
DEFAULT_COST_PER_RUN = 0.25

Launcher = Callable[[list, Path, dict, int, Path], int]
Job = tuple[Scenario, str, int]


class BenchAbort(RuntimeError):
    """The round cannot or must not continue."""


LIVE: dict = {}                 # pid -> proctree handle, for runs still going
_LIVE_LOCK = threading.Lock()


def kill_all() -> None:
    """End every live run and everything it started (Ctrl+C, or the round failing)."""
    with _LIVE_LOCK:
        handles = list(LIVE.items())
    for pid, handle in handles:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        proctree.release(handle)


def subprocess_launcher(cmd: list, cwd: Path, env: dict, timeout: int, stream_path: Path) -> int:
    """Run cmd bound to its own job / process group. Whatever it leaves running is killed when it
    ends, on timeout, or by kill_all(); never by program name."""
    err_path = Path(stream_path).with_suffix(".err")
    with open(stream_path, "w", encoding="utf-8") as out, open(err_path, "w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=err, **proctree.popen_kwargs())
        handle = proctree.attach(proc)
        with _LIVE_LOCK:
            LIVE[proc.pid] = handle
        try:
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
                proctree.release(handle)
                handle = None
                proc.wait()
                return -9
        finally:
            with _LIVE_LOCK:
                LIVE.pop(proc.pid, None)
            proctree.release(handle)


def scrub(text: str, run_dir: Path) -> str:
    """Replace the run dir and the user's home dir with placeholders, in either slash style."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    for raw, label in ((str(Path(run_dir)), "<run>"), (home, "<home>")):
        if not raw:
            continue
        for variant in {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}:
            text = re.sub(re.escape(variant), label, text, flags=re.I if sys.platform == "win32" else 0)
    return text


def plan_round(scenarios: list[Scenario], arms: dict[str, Arm], arm_names: tuple[str, ...], reps: int) -> list[Job]:
    jobs = []
    for sc in scenarios:
        for a in arm_names:
            if a in arms and a in sc.arms:
                jobs += [(sc, a, rep) for rep in range(reps)]
    return jobs


def estimate(n_runs: int, last_round: Path | None) -> float:
    if last_round is not None:
        try:
            recs = json.loads((Path(last_round) / "results.json").read_text(encoding="utf-8"))["records"]
            costs = [r["cost_usd"] for r in recs if isinstance(r.get("cost_usd"), (int, float))]
            if costs:
                return statistics.median(costs) * n_runs
        except (OSError, ValueError, KeyError):
            pass
    return DEFAULT_COST_PER_RUN * n_runs


def _error_kind(error: str | None) -> str | None:
    """'behaviour': the agent itself timed out or ran out of turns; it counts as a failed run.
    'infra': the run never really happened (launch, login, crash); it is excluded from k/n."""
    if not error:
        return None
    return "behaviour" if error == "timeout" or error.startswith("error_max_turns") else "infra"


SETUP_ERRORS = (subprocess.CalledProcessError, OSError)


def _setup_with_retry(run_dir: Path, sc: Scenario, arm: Arm) -> tuple[Path, dict[str, str]]:
    """One retry: setup_run clears the run dir itself, so a second call starts from nothing. A second
    failure is the caller's to record."""
    try:
        return setup_run(run_dir, sc, arm)
    except SETUP_ERRORS:
        return setup_run(run_dir, sc, arm)


def _setup_failure(e: BaseException, run_dir: Path) -> tuple[str, int | None, str]:
    """(short reason, rc, scrubbed stderr tail) for a setup_run failure."""
    if isinstance(e, subprocess.CalledProcessError):
        cmd = e.cmd if isinstance(e.cmd, (list, tuple)) else [str(e.cmd)]
        verb = next((a for a in cmd[1:] if not a.startswith("-") and "=" not in a), "?")
        raw = e.stderr or b""
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return f"setup: git {verb} failed (rc {e.returncode})", e.returncode, scrub(text[-300:], run_dir)
    strerror, filename = getattr(e, "strerror", None), getattr(e, "filename", None)
    reason = f"setup: {type(e).__name__}" + (f": {strerror}" if strerror else "")
    # str(e) would repr the path (doubled backslashes on Windows), which scrub would not recognise.
    text = f"{strerror}: {filename}" if strerror and filename else str(e)
    return reason, None, scrub(text[-300:], run_dir)


def _run_one(job: Job, arms: dict[str, Arm], out: Path, model: str, auth_dir: Path | None, launcher: Launcher,
             max_turns: int, timeout: int, billing: str) -> dict:
    sc, arm_name, rep = job
    run_dir = out / f"{sc.name}-{arm_name}-{rep}"
    t0 = time.time()
    try:
        repo, additions = _setup_with_retry(run_dir, sc, arms[arm_name])
    except SETUP_ERRORS as e:
        # The run never started, so it is an infra error; `setup` marks it as this run's problem, not
        # the CLI's, so run_round's first-run check lets the round continue.
        error, rc, err_tail = _setup_failure(e, run_dir)
        rec = {"scenario": sc.name, "family": sc.family, "polarity": sc.polarity, "arm": arm_name, "rep": rep,
               "rc": rc, "secs": round(time.time() - t0, 1), "billing": billing, "version": None,
               "error": error, "error_kind": _error_kind(error), "setup": True, "stderr": err_tail,
               "applied": None, "fired_before_action": None, "noise": 0.0, "cost_usd": 0.0, "turns": 0}
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "record.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        except OSError:
            pass                        # the record still reaches results.json
        return rec
    env = {**round_env(run_dir / "env", billing, auth_dir=auth_dir), **additions}
    cmd = build_cmd(sc, arms[arm_name], empty_mcp_config(run_dir), model, max_turns)
    stream_path = run_dir / "stream.jsonl"
    t0 = time.time()
    login_copy = run_dir / "env" / "claude-config" / ".credentials.json"
    try:
        rc = launcher(cmd, repo, env, timeout, stream_path)
    finally:
        # The run's copy of the user's login is only needed while it runs; never leave one per run on disk.
        try:
            login_copy.unlink()
        except FileNotFoundError:
            pass
    trace = parse_stream(stream_path)
    err_tail = ""
    try:
        err_tail = stream_path.with_suffix(".err").read_text(encoding="utf-8", errors="replace")[-600:]
    except OSError:
        pass
    error = "timeout" if rc == -9 else trace.error
    rec = {"scenario": sc.name, "family": sc.family, "polarity": sc.polarity, "arm": arm_name, "rep": rep,
           "rc": rc, "secs": round(time.time() - t0, 1), "billing": billing, "version": trace.version,
           "error": error, "error_kind": _error_kind(error),
           "stderr": scrub(err_tail, run_dir) if rc else ""}
    rec.update(metrics(trace, sc, sc.score(repo, trace)))
    (run_dir / "record.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def run_round(jobs: list[Job], arms: dict[str, Arm], out: Path, model: str, *,
              launcher: Launcher = subprocess_launcher, workers: int = 4, max_turns: int = 12,
              timeout: int = 600, max_cost: float | None = None, allow_host: bool = False,
              container: bool = False, billing: str | None = None, auth_dir: Path | None = None) -> list[dict]:
    out = Path(out)
    try:
        check_out_dir(out)
    except ValueError as e:
        raise BenchAbort(str(e)) from None
    if not container and not allow_host:
        raise BenchAbort("refusing to run on the bare host without --allow-host: native Windows has no Bash "
                         "sandbox, so each run executes as you. Use container mode, or pass --allow-host.")
    if not container and any(a == "custom" for _, a, _ in jobs):
        raise BenchAbort("the custom arm runs third-party plugin code; it is only allowed in container mode")
    billing = billing or ("api" if os.environ.get("ANTHROPIC_API_KEY") else "subscription")
    if billing != "api" and any(a == "custom" for _, a, _ in jobs):
        raise BenchAbort("the custom arm runs third-party plugin code, which would see the login it runs "
                         "under; use a dedicated, low-limit API key (ANTHROPIC_API_KEY) for custom-arm rounds")
    out.mkdir(parents=True, exist_ok=True)
    if auth_dir is None:
        auth_dir = out.parent / "auth"

    records: list[dict] = []
    pending = list(jobs)

    def check_first(rec: dict) -> None:
        # A broken setup (CLI missing, not logged in, bad key) fails before any model work, so it costs
        # nothing. A first run that timed out or ran out of turns is a result, not a broken setup; a
        # failed repo setup (`setup` flag) is one run's disk trouble, not the CLI's, so the round goes on.
        if rec.get("setup"):
            return
        if rec["error_kind"] == "infra" and not rec.get("cost_usd"):
            raise BenchAbort(f"first run failed before doing any work (rc {rec['rc']}, {rec['error']}): "
                             f"{rec['stderr'][-300:]}")

    _schedule(pending, records, arms, out, model, auth_dir, launcher, max_turns, timeout, billing,
              workers, max_cost, check_first)
    spent = sum(r.get("cost_usd") or 0.0 for r in records)
    skipped = len(pending)
    (out / "results.json").write_text(json.dumps(
        {"schema": SCHEMA, "started": date.today().isoformat(), "model": model, "billing": billing,
         "spent_usd": round(spent, 4),
         "skipped_for_cost": skipped, "records": records}, indent=1), encoding="utf-8")
    return records


def _schedule(pending, records, arms, out, model, auth_dir, launcher, max_turns, timeout, billing,
              workers, max_cost, check_first) -> None:
    spent = 0.0
    first_checked = False
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        running = set()
        try:
            _drive(pool, running, pending, records, arms, out, model, auth_dir, launcher, max_turns, timeout,
                   billing, workers, max_cost, check_first)
        except BaseException:
            # Inside the pool's `with`: its exit waits for running jobs, so kill them first.
            for f in running:
                f.cancel()
            kill_all()          # Ctrl+C or a failed round: end live runs now, not after their timeouts
            raise


def _drive(pool, running, pending, records, arms, out, model, auth_dir, launcher, max_turns, timeout, billing,
           workers, max_cost, check_first) -> None:
    spent = 0.0
    first_checked = False
    while pending or running:
        while pending and len(running) < max(1, workers) and (max_cost is None or spent < max_cost):
            job = pending.pop(0)
            running.add(pool.submit(_run_one, job, arms, out, model, auth_dir, launcher, max_turns,
                                    timeout, billing))
        if not running:
            break
        done, still = wait(running, return_when=FIRST_COMPLETED)
        running.clear()
        running.update(still)                 # in place: the caller cancels/kills from this same set
        for fut in done:
            try:
                rec = fut.result()
            except FileNotFoundError as e:
                raise BenchAbort(f"could not start claude: {e}") from None
            records.append(rec)
            spent += rec.get("cost_usd") or 0.0
            if not first_checked:
                first_checked = True
                check_first(rec)
