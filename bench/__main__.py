"""Gnomonkit Bench command line.

    python -m bench dry-run                 validate scenarios and build every repo; no Claude
    python -m bench run --mode quick ...    1 rep, gnomonkit arm only
    python -m bench run --mode full --yes   every scenario x 3 reps x bare/written/gnomonkit
    python -m bench report RESULTS_JSON     HTML page + README block
    python -m bench compare OLD NEW         per scenario and arm, old k/n vs new k/n
    python -m bench gate RESULTS --family F --arm A    exit 0 = ship, 1 = hold

See bench/README.md for what each safety flag does.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from bench.aggregate import compare, gate
from bench.arms import build_arms
from bench.container import build_image, container_launcher
from bench.report import as_of, load_results, readme_block, render_html
from bench.runner import BenchAbort, estimate, plan_round, run_round, subprocess_launcher
from bench.scenario import load_all, validate
from bench.setup import check_out_dir, default_out_root, setup_run

CORE = Path(__file__).resolve().parents[1] / "plugins" / "core"
EXIT_NEEDS_YES = 3
IMAGE = "gnomonkit-bench:local"
LOCKDOWN = ["--rm", "--init", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "512"]


def _container_check() -> int:
    """Build the image, then prove without spending any usage that Claude Code starts and that a run
    is locked down: a non-root user with zero effective capabilities."""
    import subprocess
    build_image(IMAGE)
    ver = subprocess.run(["docker", "run", *LOCKDOWN, IMAGE, "claude", "--version"], capture_output=True, text=True)
    print(f"claude in container: rc {ver.returncode} {ver.stdout.strip() or ver.stderr.strip()[-200:]}")
    probe = subprocess.run(["docker", "run", *LOCKDOWN, IMAGE, "sh", "-c", "id -u; grep CapEff /proc/self/status"],
                           capture_output=True, text=True).stdout.split()
    uid, capeff = (probe[0], probe[-1]) if len(probe) >= 3 else ("?", "?")
    locked = uid != "0" and capeff.strip("0") == ""
    print(f"lockdown: uid {uid}, effective capabilities {capeff} -> {'ok' if locked else 'NOT LOCKED DOWN'}")
    return 0 if ver.returncode == 0 and locked else 1


def _dry_run(args) -> int:
    scenarios = load_all()
    problems = validate(scenarios)
    for p in problems:
        print("problem:", p)
    built = 0
    with tempfile.TemporaryDirectory(prefix="gnomonkit-bench-dry-") as tmp:
        arms = build_arms(CORE, Path(tmp) / "work")
        for sc in scenarios:
            for a in arms:
                if a in sc.arms:
                    setup_run(Path(tmp) / f"{sc.name}-{a}", sc, arms[a])
                    built += 1
    print(f"{len(scenarios)} scenarios, {built} repos built, {len(problems)} problems")
    if problems:
        return 1
    return _container_check() if args.container else 0


def _run(args, launcher) -> int:
    scenarios = load_all()
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s.name in wanted]
    custom = tuple(Path(p) for p in args.custom_plugin or ())
    out = Path(args.out) if args.out else default_out_root() / time.strftime("%Y%m%d-%H%M%S")
    try:
        check_out_dir(out)              # before anything is written anywhere
    except ValueError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    arms = build_arms(CORE, out / "_work", custom=custom)
    if args.mode == "quick":
        arm_names, reps = ("gnomonkit",), 1
    else:
        arm_names = ("bare", "written", "gnomonkit") + (("custom",) if custom else ())
        reps = args.reps
    jobs = plan_round(scenarios, arms, arm_names, reps)
    est = estimate(len(jobs), Path(args.last_round) if args.last_round else None)
    print(f"runs: {len(jobs)}  estimate: ${est:.2f}  out: {out}")
    if args.mode == "full" and not args.yes:
        print("A full round spends real usage. Re-run with --yes to start it.")
        return EXIT_NEEDS_YES
    max_cost = args.max_cost if args.max_cost is not None else round(1.5 * est, 2)
    if launcher is None:
        launcher = container_launcher(build_image(IMAGE)) if args.container else subprocess_launcher
    try:
        recs = run_round(jobs, arms, out, args.model, launcher=launcher, workers=args.workers,
                         max_cost=max_cost, allow_host=args.allow_host, container=args.container,
                         auth_dir=Path(args.auth_dir) if args.auth_dir else None)
    except BenchAbort as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    print(f"{len(recs)} runs recorded in {out / 'results.json'}")
    return 0


def _report(args) -> int:
    results = load_results(Path(args.results))
    when = as_of(results, Path(args.results))
    path = render_html(results, Path(args.out) if args.out else Path(args.results).parent, when)
    print(readme_block(results, when))
    print(f"report: {path}")
    return 0


def _compare(args) -> int:
    old = load_results(Path(args.old))["records"]
    new = load_results(Path(args.new))["records"]
    for row in compare(old, new):
        print(f"{row['scenario']:<34} {row['arm']:<10} {row['old']:>7} -> {row['new']:<7} {row['delta']:+.2f}")
    return 0


def _gate(args) -> int:
    g = gate(load_results(Path(args.results))["records"], args.family, args.arm, args.base)
    print(f"{args.family}: {args.arm} {g['k_new']}/{g['n_new']} vs {args.base} {g['k_base']}/{g['n_base']}, "
          f"p={g['p']:.4f}, controls {'ok' if g['controls_ok'] else 'REGRESSED'} -> {g['verdict']}")
    return 0 if g["verdict"] == "ship" else 1


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bench", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dry-run", help="validate scenarios and build every repo; no Claude")
    d.add_argument("--container", action="store_true", help="also build the image and check Claude + sandbox start")
    r = sub.add_parser("run", help="run a round")
    r.add_argument("--mode", choices=("quick", "full"), default="quick")
    r.add_argument("--out", help="round directory (default: user cache, outside any repo)")
    r.add_argument("--model", default="sonnet")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--only", help="comma-separated scenario names")
    r.add_argument("--custom-plugin", action="append", help="plugin dir for the custom arm (container mode only)")
    r.add_argument("--yes", action="store_true", help="confirm a full round")
    r.add_argument("--max-cost", type=float, help="stop scheduling at this spend (default 1.5x the estimate)")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--last-round", help="previous round dir, for the cost estimate")
    r.add_argument("--auth-dir", help="folder holding the subscription login (.credentials.json) to copy into each "
                                      "run while it runs; default <out-root>/auth")
    r.add_argument("--allow-host", action="store_true",
                   help="run on this machine with no sandbox (each run executes as you)")
    r.add_argument("--container", action="store_true",
                   help="run each session in a throwaway Linux container (recommended; required for --custom-plugin)")
    rep = sub.add_parser("report", help="HTML page + README block from results.json")
    rep.add_argument("results")
    rep.add_argument("--out")
    c = sub.add_parser("compare", help="old vs new round")
    c.add_argument("old")
    c.add_argument("new")
    g = sub.add_parser("gate", help="ship or hold for one family")
    g.add_argument("results")
    g.add_argument("--family", required=True)
    g.add_argument("--arm", required=True)
    g.add_argument("--base", default="written")
    return ap


def main(argv=None, launcher=None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "dry-run":
        return _dry_run(args)
    if args.cmd == "run":
        return _run(args, launcher)
    if args.cmd == "report":
        return _report(args)
    if args.cmd == "compare":
        return _compare(args)
    return _gate(args)


if __name__ == "__main__":
    sys.exit(main())
