#!/usr/bin/env python3
"""Install, update or remove the Gnomonkit portal as a background service. Standard library only.

  python install_portal.py                               # local: this machine only, starts at logon
  python install_portal.py --mode server --host 0.0.0.0  # server: a systemd unit (run with sudo)
  python install_portal.py --dry-run [...]               # print every file and command, change nothing
  python install_portal.py --uninstall [--purge]         # remove what was installed (--purge: data too)

LOCAL MODE (per user, no admin rights):
  Windows  two Scheduled Tasks run by pythonw.exe (no console window): the server at logon, and the
           refresh nightly. Registered through PowerShell's ScheduledTasks cmdlets with the script
           passed as -EncodedCommand, so no path or argument goes through command-line quoting.
  macOS    two launchd agents in ~/Library/LaunchAgents: the server (RunAtLoad, KeepAlive) and the
           refresh (StartCalendarInterval).
  Linux    systemd --user units: the server, plus a oneshot refresh service and its timer.

SERVER MODE (Linux, root): system units in /etc/systemd/system that run as --user with a hardened
sandbox, a token in a 0600 file (unless --behind-proxy, which needs --allowed-host), and Caddy and
nginx snippets to put an authenticating reverse proxy in front.

Root never creates, chowns or chmods anything inside a folder the service account controls: a
symlink planted there would turn that into a write to any file on the machine. The token and the
data folder are created by a child process that runs as the service account (setuid, no extra
groups, umask 077). Root itself creates the data folder only when --data-dir names a folder whose
parent root controls; it then hands it over with fchown on the opened folder, never by path. The
path is read as written, one folder at a time with lstat, never resolved: a symlink on the way, or a
folder of a third account, is refused. --purge deletes as root only under folders that are all
root's own, and otherwise as the service account.

--snapshot FILE adds a static snapshot (run_portal.py snapshot) to the nightly refresh; --hide-titles
applies to that snapshot as well as to the server.

Re-running with the same options changes nothing but a restart; a changed option rewrites the units.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
RUN = PLUGIN / "run_portal.py"
DEFAULT_PORT = 7451              # the server's default too; 8765 is taken by common desktop software
HEALTH_BODY = "gnomonkit-portal"  # what /healthz answers, so another program on the port is not mistaken for it
HEALTH_HEADER = "X-Gnomonkit-Portal"
VERIFY_SECONDS = 10
DEFAULT_REFRESH = "03:30"
TASK = "Gnomonkit Portal"
TASK_REFRESH = "Gnomonkit Portal Refresh"
LABEL = "gnomonkit.portal"
LABEL_REFRESH = "gnomonkit.portal-refresh"
UNIT = "gnomonkit-portal.service"
UNIT_REFRESH = "gnomonkit-portal-refresh.service"
TIMER = "gnomonkit-portal-refresh.timer"


@dataclass
class Env:
    """Everything the plan reads from the machine, so tests can describe any OS."""
    system: str                      # Windows | Darwin | Linux
    home: Path
    python: str
    pythonw: str | None = None
    uid: int = 0
    is_root: bool = False
    user: str = ""
    user_home: Path | None = None    # the service user's home (server mode)
    etc: Path = Path("/etc")
    user_uid: int | None = None      # the service user's ids (server mode), for the steps run as that user
    user_gid: int | None = None

    @classmethod
    def detect(cls, service_user: str | None = None) -> "Env":
        system = platform.system()
        py = sys.executable
        pyw = None
        if system == "Windows":
            cand = Path(py).with_name("pythonw.exe")
            pyw = str(cand) if cand.is_file() else None
        uid = os.getuid() if hasattr(os, "getuid") else 0
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        user = service_user or os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("USERNAME") or ""
        user_home = user_uid = user_gid = None
        if system != "Windows" and user:
            try:
                import pwd
                pw = pwd.getpwnam(user)
                user_home, user_uid, user_gid = Path(pw.pw_dir), pw.pw_uid, pw.pw_gid
            except (ImportError, KeyError):
                pass
        return cls(system=system, home=Path.home(), python=py, pythonw=pyw, uid=uid, is_root=is_root,
                   user=user, user_home=user_home, user_uid=user_uid, user_gid=user_gid)


@dataclass
class Step:
    kind: str                        # write | remove | run | token | datadir | rmtree | note
    desc: str
    path: Path | None = None
    content: str = ""
    cmd: list = field(default_factory=list)
    may_fail: bool = False
    owner: str = ""                  # datadir: the service user it is for
    as_user: dict = field(default_factory=dict)   # run, rmtree: the service account (uid, gid, name, home)
    show: str = ""                   # what a dry run prints for a run step (decoded script)
    host: str = ""                   # check_port / verify
    port: int = 0
    diag: list = field(default_factory=list)   # verify: a command whose output explains a failure


# ---------------------------------------------------------------- helpers
def is_loopback(host: str) -> bool:
    h = host.strip("[]")
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def ps_quote(s: str) -> str:
    """A PowerShell single-quoted literal: nothing inside expands; a quote is doubled."""
    return "'" + str(s).replace("'", "''") + "'"


def win_arg(s: str, force: bool = False) -> str:
    """One argument in a Windows command line (CommandLineToArgvW rules, which pythonw uses to split the
    Scheduled Task's -Argument string). force: always quote; every path goes in quoted, so a folder name
    with a space (a home folder named "First Last") can never split one argument into two."""
    s = str(s)
    if not force and s and not any(c in s for c in ' \t"'):
        return s
    out, bs = '"', 0
    for c in s:
        if c == "\\":
            bs += 1
        elif c == '"':
            out += "\\" * (2 * bs + 1) + '"'
            bs = 0
        else:
            out += "\\" * bs + c
            bs = 0
    return out + "\\" * (2 * bs) + '"'


def sd_quote(s: str) -> str:
    """One word in a systemd ExecStart line."""
    s = str(s)
    if s and not any(c in s for c in ' \t"\\\'$%;'):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$").replace("%", "%%") + '"'


def xml(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def encoded_ps(script: str) -> list:
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", b64]


def parse_time(t: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in t.split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    raise SystemExit("--refresh-at must be HH:MM (24-hour)")


def serve_args(a) -> list:
    args = ["serve", "--host", a.host, "--port", str(a.port), "--quiet"]
    if a.behind_proxy:
        args.append("--behind-proxy")
    for h in a.allowed_host or []:
        args += ["--allowed-host", h]
    if a.secure_cookie:
        args.append("--secure-cookie")
    if a.hide_titles:
        args.append("--hide-titles")
    return args


def refresh_args(a) -> list:
    args = ["refresh", "--trigger", "nightly"]
    if a.snapshot:
        args += ["--snapshot", os.path.abspath(a.snapshot)]
    if a.hide_titles:
        args.append("--hide-titles")        # the snapshot the refresh writes leaves titles out too
    return args


def needs_token(a) -> bool:
    return not a.behind_proxy and (a.mode == "server" or not is_loopback(a.host))


# ---------------------------------------------------------------- per platform
def plan_windows(a, env: Env, data: Path) -> list:
    exe = env.pythonw or env.python
    steps = []
    if not env.pythonw:
        steps.append(Step("note", "pythonw.exe was not found beside this Python, so the tasks use python.exe "
                                  "and a console window may open at each start"))
    # Paths are always quoted; the data dir travels as an argument, not an environment variable, so the task
    # carries everything it needs.
    head = win_arg(str(RUN), force=True) + ((" --data-dir " + win_arg(str(data), force=True)) if a.data_dir else "")
    serve = head + " " + " ".join(win_arg(x) for x in serve_args(a))
    ref = head + " " + " ".join(win_arg(x, force=i >= 4) for i, x in enumerate(refresh_args(a)))   # the path quoted
    h, m = parse_time(a.refresh_at)
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name\n"
        "$p = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited\n"
        # The server runs until logoff: no time limit, one instance, restart if it dies.
        "$s1 = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew "
        "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)\n"
        "$s2 = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew\n"
        "$a1 = New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s\n"
        "$a2 = New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s\n"
        "if (Get-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName %s }\n"
        "Register-ScheduledTask -TaskName %s -Action $a1 -Principal $p -Settings $s1 "
        "-Trigger (New-ScheduledTaskTrigger -AtLogOn -User $me) -Force | Out-Null\n"
        "Register-ScheduledTask -TaskName %s -Action $a2 -Principal $p -Settings $s2 "
        "-Trigger (New-ScheduledTaskTrigger -Daily -At %s) -Force | Out-Null\n"
        "Start-ScheduledTask -TaskName %s\n"
    ) % (ps_quote(win_arg(exe, force=True)), ps_quote(serve), ps_quote(str(PLUGIN)),
         ps_quote(win_arg(exe, force=True)), ps_quote(ref), ps_quote(str(PLUGIN)),
         ps_quote(TASK), ps_quote(TASK), ps_quote(TASK), ps_quote(TASK_REFRESH), ps_quote("%02d:%02d" % (h, m)),
         ps_quote(TASK))
    steps.append(Step("run", "register the Scheduled Tasks %r (at logon) and %r (daily %02d:%02d), then start the "
                             "server" % (TASK, TASK_REFRESH, h, m), cmd=encoded_ps(script), show=script))
    return steps


def unplan_windows(a, env: Env) -> list:
    script = ("foreach ($n in @(%s, %s)) {\n"
              "  if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {\n"
              "    Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue\n"
              "    Unregister-ScheduledTask -TaskName $n -Confirm:$false\n  }\n}\n") % (ps_quote(TASK), ps_quote(TASK_REFRESH))
    return [Step("run", "remove the Scheduled Tasks %r and %r" % (TASK, TASK_REFRESH), cmd=encoded_ps(script),
                 show=script)]


def _plist(label: str, args: list, data: Path, extra: str) -> str:
    items = "\n".join("    <string>%s</string>" % xml(x) for x in args)
    envs = "  <key>EnvironmentVariables</key>\n  <dict>\n    <key>GNOMONKIT_PORTAL_DIR</key>\n" \
           "    <string>%s</string>\n  </dict>\n" % xml(str(data))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            "  <key>Label</key>\n  <string>%s</string>\n"
            "  <key>ProgramArguments</key>\n  <array>\n%s\n  </array>\n%s"
            "  <key>WorkingDirectory</key>\n  <string>%s</string>\n"
            "  <key>StandardOutPath</key>\n  <string>%s</string>\n"
            "  <key>StandardErrorPath</key>\n  <string>%s</string>\n"
            # 63 is 0o077: every file the agent creates is private to the user, whatever their login umask.
            "  <key>Umask</key>\n  <integer>63</integer>\n%s"
            "</dict>\n</plist>\n") % (xml(label), items, envs, xml(str(PLUGIN)), xml(str(data / "launchd-stdio.txt")),
                                     xml(str(data / "launchd-stdio.txt")), extra)


def _agents(env: Env) -> Path:
    return env.home / "Library" / "LaunchAgents"


def plan_macos(a, env: Env, data: Path) -> list:
    h, m = parse_time(a.refresh_at)
    d = _agents(env)
    p1, p2 = d / (LABEL + ".plist"), d / (LABEL_REFRESH + ".plist")
    s1 = _plist(LABEL, [env.python, str(RUN)] + serve_args(a), data,
                "  <key>RunAtLoad</key>\n  <true/>\n  <key>KeepAlive</key>\n  <true/>\n")
    s2 = _plist(LABEL_REFRESH, [env.python, str(RUN)] + refresh_args(a), data,
                "  <key>StartCalendarInterval</key>\n  <dict>\n    <key>Hour</key>\n    <integer>%d</integer>\n"
                "    <key>Minute</key>\n    <integer>%d</integer>\n  </dict>\n" % (h, m))
    dom = "gui/%d" % env.uid
    steps = [Step("write", "launchd agent for the server", p1, s1), Step("write", "launchd agent for the nightly refresh", p2, s2)]
    for lab, p in ((LABEL, p1), (LABEL_REFRESH, p2)):
        steps.append(Step("run", "unload %s if loaded" % lab, cmd=["launchctl", "bootout", "%s/%s" % (dom, lab)], may_fail=True))
        steps.append(Step("run", "load %s" % lab, cmd=["launchctl", "bootstrap", dom, str(p)]))
    return steps


def unplan_macos(a, env: Env) -> list:
    d = _agents(env)
    steps = []
    for lab in (LABEL, LABEL_REFRESH):
        steps.append(Step("run", "unload %s" % lab, cmd=["launchctl", "bootout", "gui/%d/%s" % (env.uid, lab)], may_fail=True))
        steps.append(Step("remove", "remove the %s agent" % lab, d / (lab + ".plist")))
    return steps


def _units(a, env: Env, data: Path, system: bool) -> dict:
    h, m = parse_time(a.refresh_at)
    py = env.python
    exec_serve = " ".join(sd_quote(x) for x in [py, str(RUN)] + serve_args(a))
    exec_ref = " ".join(sd_quote(x) for x in [py, str(RUN)] + refresh_args(a))
    envline = "Environment=%s\n" % sd_quote("GNOMONKIT_PORTAL_DIR=%s" % data)
    if system:
        home = env.user_home or Path("/home") / env.user
        writable = {str(home / ".gnomonkit"), str(data)}
        if a.snapshot:
            writable.add(os.path.dirname(os.path.abspath(a.snapshot)))    # the snapshot is replaced in its folder
        writable = sorted(writable)
        sandbox = ("User=%s\n" % env.user +
                   "NoNewPrivileges=yes\nPrivateTmp=yes\nPrivateDevices=yes\nProtectSystem=strict\n"
                   "ProtectHome=read-only\nReadWritePaths=%s\nProtectKernelTunables=yes\nProtectKernelModules=yes\n"
                   "ProtectControlGroups=yes\nRestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n"
                   "RestrictNamespaces=yes\nLockPersonality=yes\nUMask=0077\n") % " ".join(sd_quote(w) for w in writable)
        wanted, after = "multi-user.target", "After=network-online.target\nWants=network-online.target\n"
    else:
        sandbox, wanted, after = "UMask=0077\n", "default.target", ""
    return {
        UNIT: ("[Unit]\nDescription=Gnomonkit portal (dashboard)\n%s\n[Service]\n%s%sExecStart=%s\n"
               "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=%s\n") % (after, envline, sandbox, exec_serve, wanted),
        UNIT_REFRESH: ("[Unit]\nDescription=Gnomonkit portal nightly refresh\n\n[Service]\nType=oneshot\n%s%s"
                       "ExecStart=%s\nTimeoutStartSec=2h\n") % (envline, sandbox, exec_ref),
        TIMER: ("[Unit]\nDescription=Gnomonkit portal nightly refresh\n\n[Timer]\nOnCalendar=*-*-* %02d:%02d:00\n"
                "Persistent=true\nRandomizedDelaySec=300\n\n[Install]\nWantedBy=timers.target\n") % (h, m),
    }


def _unit_dir(env: Env, system: bool) -> Path:
    return env.etc / "systemd" / "system" if system else env.home / ".config" / "systemd" / "user"


def plan_systemd(a, env: Env, data: Path, system: bool) -> list:
    d = _unit_dir(env, system)
    sc = ["systemctl"] if system else ["systemctl", "--user"]
    steps = [Step("write", "systemd unit %s" % name, d / name, body) for name, body in _units(a, env, data, system).items()]
    steps += [Step("run", "reload systemd", cmd=sc + ["daemon-reload"]),
              Step("run", "enable and start the server and the refresh timer", cmd=sc + ["enable", "--now", UNIT, TIMER]),
              Step("run", "restart the server so changed options apply", cmd=sc + ["restart", UNIT])]
    if not system:
        steps.append(Step("note", "user units stop at logout; to keep the portal running without a login session, "
                                  "run once: loginctl enable-linger %s" % (env.user or "$USER")))
    return steps


def unplan_systemd(a, env: Env, system: bool) -> list:
    d = _unit_dir(env, system)
    sc = ["systemctl"] if system else ["systemctl", "--user"]
    steps = [Step("run", "stop and disable the portal units", cmd=sc + ["disable", "--now", UNIT, TIMER], may_fail=True)]
    steps += [Step("remove", "remove %s" % n, d / n) for n in (UNIT, UNIT_REFRESH, TIMER)]
    steps.append(Step("run", "reload systemd", cmd=sc + ["daemon-reload"], may_fail=True))
    return steps


CADDY = """# Caddyfile: replace portal.example.com and the auth line with your own.
portal.example.com {
    # Authenticate first: forward_auth to your identity provider, or basic_auth with a hashed password.
    # forward_auth <auth-host:port> {
    #     uri <verify-path>
    # }
    reverse_proxy 127.0.0.1:%(port)d {
        # Only when the portal runs WITH a token: hand it over here so users never see it.
        # header_up X-Gnomonkit-Token <contents of %(token)s>
    }
}
"""

NGINX = """# nginx: replace portal.example.com, the certificate paths and the auth lines with your own.
server {
    listen 443 ssl;
    server_name portal.example.com;
    ssl_certificate     /etc/ssl/<your-certificate>.pem;
    ssl_certificate_key /etc/ssl/<your-key>.pem;

    # Authenticate first: auth_request to your identity provider, or auth_basic.
    # auth_request <verify-location>;

    location / {
        proxy_pass http://127.0.0.1:%(port)d;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Only when the portal runs WITH a token: hand it over here so users never see it.
        # proxy_set_header X-Gnomonkit-Token "<contents of %(token)s>";
    }
}
"""


def proxy_snippets(a, data: Path) -> str:
    v = {"port": a.port, "token": data / "token"}
    return (CADDY % v) + "\n" + (NGINX % v)


# ---------------------------------------------------------------- plan + run
def data_dir_for(a, env: Env) -> Path:
    if a.data_dir:
        return Path(a.data_dir)
    if a.mode == "server" and env.system != "Windows":
        home = env.user_home or (Path("/home") / env.user if env.user else env.home)
        return home / ".gnomonkit" / "portal"
    if os.environ.get("GNOMONKIT_PORTAL_DIR"):
        return Path(os.environ["GNOMONKIT_PORTAL_DIR"])
    gk = os.environ.get("GNOMONKIT_HOME")
    return (Path(gk) if gk else env.home / ".gnomonkit") / "portal"


def plan(a, env: Env) -> list:
    data = data_dir_for(a, env)
    system = a.mode == "server"
    if system and env.system != "Linux" and not a.dry_run:
        raise SystemExit("server mode installs systemd units and runs on Linux only. On this machine use "
                         "--mode local (add --host and a token to reach it from other devices).")
    if system and not a.dry_run and not env.is_root:
        raise SystemExit("server mode writes to /etc/systemd/system: run it with sudo (add --user <account> "
                         "for the account the portal runs as).")
    if system and not env.user and not a.uninstall:
        raise SystemExit("server mode needs --user <account>: the portal never runs as root")
    if system and env.user == "root" and not a.uninstall:
        raise SystemExit("refusing to run the portal as root: pass --user <unprivileged account>")
    if a.behind_proxy and not a.uninstall and not any((h or "").strip() for h in a.allowed_host or []):
        raise SystemExit("--behind-proxy needs --allowed-host <the public name the proxy serves>: without it the "
                         "portal accepts any Host header, so a page that rebinds its DNS name could read the API.")
    if system and not a.uninstall and needs_token(a) and not a.dry_run and env.user_uid is None:
        raise SystemExit("cannot look up the account %r: create it first, or pass --user <account>" % env.user)
    if a.uninstall:
        if system:
            steps = unplan_systemd(a, env, True)
        elif env.system == "Windows":
            steps = unplan_windows(a, env)
        elif env.system == "Darwin":
            steps = unplan_macos(a, env)
        else:
            steps = unplan_systemd(a, env, False)
        if a.purge:
            who = ({"uid": env.user_uid, "gid": env.user_gid, "name": env.user,
                    "home": str(env.user_home or Path("/home") / env.user)}
                   if system and env.user and env.user_uid is not None else {})
            steps.append(Step("rmtree", "delete the portal data dir (metrics, token, logs)", data, as_user=who))
        else:
            steps.append(Step("note", "kept the data dir %s (metrics, token, logs); --purge deletes it" % data))
        return steps
    steps = []
    if "cache" in PLUGIN.parts:
        steps.append(Step("note", "the service points at this plugin version's folder; after updating the plugin, "
                                  "re-run this installer from the new version so the service follows it"))
    if needs_token(a) and system:
        # Root prepares nothing inside the service account's folders; that account makes its own token.
        steps.append(Step("datadir", "the data folder for %s" % env.user, data, owner=env.user))
        steps.append(Step("run", "create the access token as %s (kept when one exists)" % env.user,
                          cmd=[env.python, str(RUN), "--data-dir", str(data), "token", "init"],
                          as_user={"uid": env.user_uid, "gid": env.user_gid, "name": env.user,
                                   "home": str(env.user_home or Path("/home") / env.user)}))
    elif needs_token(a):
        steps.append(Step("token", "access token (kept when one exists)", data / "token"))
    if not is_loopback(a.host) and not system:
        steps.append(Step("note", "binding %s makes the portal reachable from other machines; it requires the token, "
                                  "and the login link is printed by: run_portal.py login-url" % a.host))
    # Before anything is registered: the port must be free, or held by an earlier portal (which the
    # re-registration replaces). Anything else answering there would make the service fail at every start.
    steps.append(Step("check_port", "port %d is free" % a.port, host=a.host, port=a.port))
    if system:
        steps += plan_systemd(a, env, data, True)
        diag = ["systemctl", "status", UNIT, "--no-pager", "-n", "20"]
    elif env.system == "Windows":
        steps += plan_windows(a, env, data)
        diag = encoded_ps("$i = Get-ScheduledTaskInfo -TaskName %s\n"
                          "'LastTaskResult=' + $i.LastTaskResult + '  LastRunTime=' + $i.LastRunTime\n" % ps_quote(TASK))
    elif env.system == "Darwin":
        steps += plan_macos(a, env, data)
        diag = ["launchctl", "print", "gui/%d/%s" % (env.uid, LABEL)]
    else:
        steps += plan_systemd(a, env, data, False)
        diag = ["systemctl", "--user", "status", UNIT, "--no-pager", "-n", "20"]
    # After starting: the portal itself has to answer, not merely something on the port.
    steps.append(Step("verify", "the portal answers on port %d" % a.port, host=a.host, port=a.port, diag=diag,
                      path=data / "portal.log"))
    return steps


# ---------------------------------------------------------------- port and health probes
def _connect_host(host: str) -> str:
    h = host.strip("[]")
    return {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1", "localhost": "127.0.0.1"}.get(h, h)


def is_portal(host: str, port: int, timeout: float = 1.0) -> bool:
    """True only when the Gnomonkit portal answers /healthz: its header and its body, not any 200."""
    import http.client
    try:
        c = http.client.HTTPConnection(_connect_host(host), port, timeout=timeout)
        c.request("GET", "/healthz")
        r = c.getresponse()
        body = r.read(200)
        c.close()
    except (OSError, http.client.HTTPException):
        return False
    return r.status == 200 and bool(r.getheader(HEALTH_HEADER)) and body.startswith(HEALTH_BODY.encode())


def probe_port(host: str, port: int) -> str:
    """free | portal | busy (another program listens) | reserved (cannot bind, nothing listens: a Windows
    excluded port range, or a privileged port)."""
    import socket
    h = host.strip("[]")
    s = socket.socket(socket.AF_INET6 if ":" in h else socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((h, port))
        return "free"
    except OSError:
        pass
    finally:
        s.close()
    if is_portal(host, port):
        return "portal"
    try:
        c = socket.create_connection((_connect_host(host), port), timeout=1)
        c.close()
        return "busy"
    except OSError:
        return "reserved"


def wait_portal(host: str, port: int, seconds: float = VERIFY_SECONDS) -> bool:
    import time
    deadline = time.monotonic() + seconds
    while True:
        if is_portal(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _write(p: Path, content: str) -> bool:
    """True when the file changed. Writes atomically."""
    try:
        if p.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    # A fresh name from mkstemp (O_EXCL), never a fixed ".tmp" someone could plant a symlink at.
    fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)          # unit files and launchd agents are read by the service manager
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


class Unsafe(SystemExit):
    pass


def _ensure_token(p: Path) -> bool:
    """Local mode: this account makes its own token (the server-mode token is made as the service account).
    A token path or data folder the portal refuses (a symlink, another account's) becomes Unsafe, so the
    installer prints the reason instead of a traceback."""
    sys.path.insert(0, str(PLUGIN))
    from gnomonkit_portal import paths as P, token as T
    try:
        _, created = T.ensure(p)
    except P.UnsafePath as e:
        raise Unsafe(str(e)) from e
    return created


def _lstat(p):
    """A path's own status, never through a symlink at its last part. The one place the path checks below
    read the file system, so a test can describe what root would see."""
    return os.lstat(p)


def _parts(p: Path) -> list:
    """p and every folder above it, from / down, exactly as written: made absolute, never resolved."""
    p = Path(os.path.abspath(p))
    return [*reversed(p.parents), p]


def _walk(p: Path, uid: int) -> list:
    """The parts of p that exist, from / down, as (path, lstat). Each part is read on its own with lstat,
    in order, so no symlink is ever followed on the way: the path is never resolved first. Raises Unsafe at
    a symlink, at a part that is not a folder, or at a folder that belongs to neither root nor the service
    account (uid), because whoever owns a folder on the way can swap what lies below it."""
    target, found = Path(os.path.abspath(p)), []
    for q in _parts(target):
        try:
            st = _lstat(q)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(st.st_mode):
            raise Unsafe("refusing the data folder %s: %s is a symlink, and root never follows one on the way to "
                         "the data folder. Pass the folder's real path with --data-dir." % (target, q))
        if not stat.S_ISDIR(st.st_mode):
            raise Unsafe("refusing the data folder %s: %s is not a folder" % (target, q))
        if st.st_uid not in (0, uid):
            raise Unsafe("refusing the data folder %s: %s belongs to another account; pick another --data-dir"
                         % (target, q))
        found.append((q, st))
    return found


def _root_controlled(p: Path) -> bool:
    """p and every folder above it, each read with lstat as written (never resolved first), are real folders
    owned by root that no other account can write to, so no other account can swap anything in that path
    while root works there."""
    for q in _parts(p):
        try:
            st = _lstat(q)
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            return False
    return True


def _fd_hand_over(d: Path, uid: int, gid: int) -> None:
    """chown and chmod d through a descriptor opened without following a symlink, so the change lands on the
    folder that was checked and nowhere else."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(d), flags)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise Unsafe("refusing %s: not a folder" % d)
        os.fchown(fd, uid, gid)
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def hand_over_dir(d: Path, uid: int, gid: int) -> str:
    """Make sure the service account can create its data folder d, without root ever acting inside a folder
    that account (or any other) controls. Returns what was done:

      ok       d is already a folder of that account
      user     the nearest existing folder above d is that account's own, so it makes d itself (as itself)
      created  root made d (its parent is root-controlled) and handed it over through the open descriptor
      handed   d was a folder of root's (made beforehand) under a root-controlled parent; handed over the same way

    Raises Unsafe for a symlink anywhere on the path as written (d or any folder above it), a folder of a
    third account on the way, or a folder above d that others can write to."""
    d = Path(os.path.abspath(d))
    found = _walk(d, uid)
    a, st = found[-1]                      # the nearest part that exists ("/" always does)
    if a == d:
        if st.st_uid == uid:
            return "ok"
        if not _root_controlled(d.parent):
            raise Unsafe("refusing the data folder %s: it is root's, inside a folder another account can change; "
                         "pick another --data-dir" % d)
        _fd_hand_over(d, uid, gid)
        return "handed"
    if st.st_uid == uid and not st.st_mode & 0o022:
        return "user"
    if a == d.parent and _root_controlled(a):
        os.mkdir(d, 0o700)
        _fd_hand_over(d, uid, gid)
        return "created"
    raise Unsafe("cannot prepare the data folder %s safely: %s belongs to another account or others can write "
                 "to it. Create %s as root first (owned by root, mode 755), or pick a --data-dir inside the "
                 "service account's home." % (d, a, d.parent))


def _as_user_kw(u: dict) -> dict:
    """subprocess.run arguments that drop to the service account before the child runs: its uid and gid, no
    supplementary groups (root's would otherwise be kept), a private umask, and its own HOME."""
    env = {"HOME": u["home"], "USER": u["name"], "LOGNAME": u["name"], "PATH": "/usr/local/bin:/usr/bin:/bin",
           "LANG": os.environ.get("LANG") or "C.UTF-8"}
    return {"user": u["uid"], "group": u["gid"], "extra_groups": [], "umask": 0o077, "cwd": "/", "env": env}


RMTREE = "import shutil, sys; shutil.rmtree(sys.argv[1])"


def purge_dir(d: Path, as_user: dict | None = None, runner=subprocess.run, is_root: bool | None = None) -> str:
    """Delete the data folder d (--uninstall --purge). Returns "gone", "deleted" or "deleted as <account>".

    Root never deletes through a path another account controls, because a symlink swapped in on the way
    would point the delete at any folder on the machine. The path is walked as written (_walk: no symlink,
    no third account's folder), and root deletes d itself only when every folder above it is root's own,
    so nothing on the way can change (rmtree does not follow links inside d). Otherwise the service account
    deletes its own folder, as itself, with nothing but its own rights."""
    d = Path(os.path.abspath(d))
    if is_root is None:
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if not is_root:                        # local mode: the user's own folder, deleted with the user's rights
        try:
            st = os.lstat(d)
        except FileNotFoundError:
            return "gone"
        if not stat.S_ISDIR(st.st_mode):
            raise Unsafe("not deleting %s: it is a symlink or not a folder" % d)
        shutil.rmtree(d)
        return "deleted"
    uid = (as_user or {}).get("uid")
    found = _walk(d, 0 if uid is None else uid)
    if found[-1][0] != d:
        return "gone"
    if _root_controlled(d.parent) and getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        shutil.rmtree(d)
        return "deleted"
    if uid is None:
        raise Unsafe("not deleting %s as root: a folder above it belongs to another account. Pass --user <account> "
                     "so that account deletes its own data." % d)
    r = runner([sys.executable, "-I", "-c", RMTREE, str(d)], capture_output=True, text=True,
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **_as_user_kw(as_user))
    if r.returncode != 0:
        raise Unsafe("could not delete %s as %s (exit %s)" % (d, as_user["name"], r.returncode))
    return "deleted as %s" % as_user["name"]


PORT_TAKEN = {
    "busy": "port %d on %s is already used by another program, so the portal could not start there. "
            "Pick a free one with --port%s.",
    "reserved": "port %d on %s cannot be bound (reserved by the system, or privileged). "
                "Pick another with --port%s.",
}


def _suggest(host: str, port: int, probe) -> str:
    """' (for example --port N)' with an N the probe found free, or nothing."""
    cands = ([DEFAULT_PORT] if port != DEFAULT_PORT else []) + [p for p in range(port + 1, port + 21) if p < 65536]
    for c in cands:
        if probe(host, c) == "free":
            return " (for example --port %d, which is free)" % c
    return ""


def execute(steps: list, dry_run: bool, runner=subprocess.run, out=print, probe=None, wait=None) -> int:
    probe = probe or probe_port
    wait = wait or wait_portal
    failed = 0
    for s in steps:
        if s.kind == "note":
            out("note: " + s.desc)
            continue
        if dry_run:
            if s.kind == "check_port":
                out("would check that %s:%d is free (or held by an earlier portal) before registering anything"
                    % (s.host, s.port))
            elif s.kind == "verify":
                out("would wait up to %d s for the portal's own /healthz answer on port %d" % (VERIFY_SECONDS, s.port))
            elif s.kind == "write":
                out("would write %s  (%s)\n%s" % (s.path, s.desc, _indent(s.content)))
            elif s.kind == "run":
                shown = s.cmd if not s.show else s.cmd[:-1] + ["<script below>"]
                out("would run%s: %s  (%s)" % ((" as " + s.as_user["name"]) if s.as_user else "", " ".join(shown), s.desc))
                if s.show:
                    out(_indent(s.show))
                if s.as_user:
                    out("    (it creates <data dir>/token with mode 0600 in a 0700 folder, unless a valid token is "
                        "already there)")
            elif s.kind == "datadir":
                out("would check %s for %s: the account makes it itself when the folder above is its own; root "
                    "creates it only under a root-controlled parent, and never follows a symlink" % (s.path, s.owner))
            elif s.kind == "token":
                out("would create %s with mode 0600 unless a valid token is already there" % s.path)
            elif s.kind == "rmtree":
                out("would delete %s  (%s)%s" % (s.path, s.desc, (", as %s unless every folder above it is root's "
                                                                  "own" % s.as_user["name"]) if s.as_user else ""))
            else:
                out("would remove %s  (%s)" % (s.path, s.desc))
            continue
        if s.kind == "check_port":
            state = probe(s.host, s.port)
            if state in PORT_TAKEN:
                out("FAILED: " + PORT_TAKEN[state] % (s.port, s.host, _suggest(s.host, s.port, probe)))
                out("Nothing was installed.")
                return failed + 1                 # stop before registering a service that cannot start
            out("ok: port %d is free" % s.port if state == "free" else
                "ok: port %d is held by an earlier portal, which this install replaces" % s.port)
        elif s.kind == "verify":
            if wait(s.host, s.port, VERIFY_SECONDS):
                out("ok: the portal answers on port %d" % s.port)
                continue
            failed += 1
            out("FAILED: the portal did not answer on port %d within %d s." % (s.port, VERIFY_SECONDS))
            if s.diag:
                try:
                    r = runner(s.diag, capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    detail = ((r.stdout or "") + (r.stderr or "")).strip()
                except OSError:
                    detail = ""
                if detail:
                    out(_indent("\n".join(detail.splitlines()[-20:])))
            out("    log: %s" % s.path)
        elif s.kind == "write":
            out(("wrote " if _write(s.path, s.content) else "unchanged ") + str(s.path))
        elif s.kind == "remove":
            try:
                s.path.unlink()
                out("removed %s" % s.path)
            except FileNotFoundError:
                out("already gone: %s" % s.path)
        elif s.kind == "rmtree":
            try:
                done = purge_dir(s.path, s.as_user, runner)
            except (Unsafe, OSError) as e:
                failed += 1
                out("FAILED: %s" % (e.code if isinstance(e, Unsafe) else "%s: %s" % (s.desc, type(e).__name__)))
                continue
            out("already gone: %s" % s.path if done == "gone" else "deleted %s%s" % (s.path, done[len("deleted"):]))
        elif s.kind == "token":
            try:
                created = _ensure_token(s.path)
            except (Unsafe, OSError) as e:
                out("FAILED: %s" % (e.code if isinstance(e, Unsafe) else "%s: %s" % (s.desc, type(e).__name__)))
                out("Nothing more was installed.")
                return failed + 1
            out(("created token " if created else "kept the existing token ") + str(s.path))
        elif s.kind == "datadir":
            if os.name != "posix":
                out("skipped: %s (POSIX only)" % s.desc)
                continue
            u = next((x.as_user for x in steps if x.as_user), {})
            try:
                done = hand_over_dir(s.path, u["uid"], u["gid"])
            except (Unsafe, OSError) as e:
                out("FAILED: %s" % (e.code if isinstance(e, Unsafe) else "%s: %s" % (s.desc, type(e).__name__)))
                out("Nothing more was installed.")
                return failed + 1
            out("ok: %s (%s)" % (s.desc, {"ok": "already the account's own", "user": "the account creates it",
                                          "created": "created and handed over", "handed": "handed over"}[done]))
        elif s.kind == "run":
            kw = _as_user_kw(s.as_user) if s.as_user else {}
            try:
                r = runner(s.cmd, capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kw)
                code = r.returncode
            except OSError as e:
                code, r = 127, None
                out("could not start %s (%s)" % (s.cmd[0], type(e).__name__))
            if code == 0:
                out("ok: " + s.desc)
            elif s.may_fail:
                out("skipped: %s (exit %s)" % (s.desc, code))
            else:
                failed += 1
                tail = ((r.stderr or r.stdout or "") if r else "").strip().splitlines()[-3:]
                out("FAILED: %s (exit %s)%s" % (s.desc, code, ("\n" + _indent("\n".join(tail))) if tail else ""))
    return failed


def _indent(s: str) -> str:
    return "\n".join("    " + x for x in s.rstrip("\n").splitlines())


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Install the Gnomonkit portal as a background service.")
    ap.add_argument("--mode", choices=("local", "server"), default="local")
    ap.add_argument("--host", help="IP to bind (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--refresh-at", default=DEFAULT_REFRESH, help="nightly refresh time, HH:MM local (default %s)" % DEFAULT_REFRESH)
    ap.add_argument("--behind-proxy", action="store_true", help="server: no token; an AUTHENTICATING reverse proxy "
                    "in front controls access (needs --allowed-host)")
    ap.add_argument("--allowed-host", action="append", help="accepted Host header, e.g. the proxy's public name")
    ap.add_argument("--snapshot", metavar="FILE", help="also write a static snapshot of the page to FILE after each "
                    "nightly refresh (FILE's folder must exist)")
    ap.add_argument("--secure-cookie", action="store_true", help="mark the login cookie Secure (served over HTTPS)")
    ap.add_argument("--hide-titles", action="store_true", help="never show session titles")
    ap.add_argument("--user", help="server: the account the service runs as (default: the sudo caller)")
    ap.add_argument("--data-dir", help="portal data dir (default ~/.gnomonkit/portal, or $GNOMONKIT_HOME/portal)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--purge", action="store_true", help="with --uninstall: also delete the data dir")
    ap.add_argument("--dry-run", action="store_true", help="print every file and command; change nothing")
    return ap


def main(argv=None, env: Env | None = None, runner=subprocess.run, out=print, probe=None, wait=None) -> int:
    a = build_parser().parse_args(argv)
    if a.host is None:
        a.host = "127.0.0.1"
    try:
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    env = env or Env.detect(a.user)
    if a.user:
        env.user = a.user
    steps = plan(a, env)
    if a.dry_run:
        out("dry run: nothing is changed\n")
    failed = execute(steps, a.dry_run, runner, out, probe, wait)
    if failed and not a.uninstall:
        out("\n%d step(s) failed; see above. The portal is not running." % failed)
        return 1
    if a.mode == "server" and not a.uninstall:
        out("\nReverse proxy (put authentication in front; the snippets use placeholders):\n")
        out(proxy_snippets(a, data_dir_for(a, env)))
    if not a.uninstall:
        host = a.host if ":" not in a.host.strip("[]") else "[%s]" % a.host.strip("[]")
        url = "http://%s:%d/" % ("127.0.0.1" if a.host in ("0.0.0.0", "::") else host, a.port)
        out("\nPortal: %s" % url)
        if needs_token(a):
            out("Sign in once with the link from: %s %s login-url --port %d" % (Path(env.python).name, RUN, a.port))
        out("Data refreshes nightly at %s; the page has a Refresh button. First data now: %s %s refresh"
            % (a.refresh_at, Path(env.python).name, RUN))
        if a.snapshot:
            out("Each nightly refresh also writes a static snapshot to %s (readable by its owner only until you "
                "grant more; a later snapshot keeps that access)." % os.path.abspath(a.snapshot))
    if failed:
        out("\n%d step(s) failed; see above." % failed)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
