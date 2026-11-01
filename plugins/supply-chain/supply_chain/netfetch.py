"""HTTPS GETs to public registries and code hosts: timeouts, size caps, an optional disk cache.

Responses are untrusted. Callers parse JSON with the standard library only, and archives are
read in memory (archive.py); nothing fetched here is executed.

Fail-soft: every failure becomes FetchError with a short reason, which the review reports as
"could not be checked", never as "nothing new".
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

UA = "gnomonkit-supply-chain/%s (+https://github.com/joshualytle/Gnomonkit)" % __version__
DEFAULT_TIMEOUT = 30


class FetchError(RuntimeError):
    pass


def cache_dir() -> str:
    return (os.environ.get("CLAUDE_PLUGIN_OPTION_CACHE_DIR") or os.environ.get("GNOMONKIT_SUPPLY_CACHE")
            or os.path.join(os.path.expanduser("~"), ".cache", "gnomonkit-supply-chain"))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """urllib's own handler follows a redirect to any host and scheme and copies every header,
    Authorization included. This one refuses a redirect off https, refuses any redirect of a GitHub
    API call (the only requests that carry a token), and drops Authorization when the host changes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        if new.scheme != "https":
            raise urllib.error.HTTPError(req.full_url, code, "refused a redirect to a non-https URL", headers, fp)
        if old.hostname == "api.github.com":
            raise urllib.error.HTTPError(req.full_url, code, "refused a redirect of a GitHub API call", headers, fp)
        r = super().redirect_request(req, fp, code, msg, headers, newurl)
        if r is not None and new.hostname != old.hostname:
            for hdrs in (r.headers, r.unredirected_hdrs):
                for k in [k for k in hdrs if k.lower() == "authorization"]:
                    del hdrs[k]
        return r


_OPENER = urllib.request.build_opener(SafeRedirect())
DOWNLOAD_BUDGET = 300          # seconds for a whole response, not just per socket read


class HTTP:
    """`opener` replaces the safe opener (tests pass a fake; the network is never touched there)."""

    def __init__(self, opener=None, timeout: float = DEFAULT_TIMEOUT, cache: "str | None" = None,
                 github_token: "str | None" = None, budget: float = DOWNLOAD_BUDGET) -> None:
        self._open = opener or _OPENER.open
        self.budget = budget
        self.timeout = timeout
        self.cache = cache
        self.github_token = github_token
        self.calls = 0

    def _headers(self, url: str, extra: "dict | None") -> dict:
        h = {"User-Agent": UA}
        # The token goes to the GitHub API host only; SafeRedirect keeps it from following a redirect.
        if self.github_token and url.startswith("https://api.github.com/"):
            h["Authorization"] = "Bearer " + self.github_token
        h.update(extra or {})
        return h

    def get(self, url: str, headers: "dict | None" = None, max_bytes: int = 20_000_000):
        """(status, body). 404 and other HTTP errors come back as a status; transport errors raise."""
        status, body, _loc = self.get_full(url, headers, max_bytes)
        return status, body

    def get_full(self, url: str, headers: "dict | None" = None, max_bytes: int = 20_000_000):
        """(status, body, Location of a refused redirect or None)."""
        if not url.startswith("https://"):
            raise FetchError("only https URLs are fetched")
        self.calls += 1
        req = urllib.request.Request(url, headers=self._headers(url, headers))
        deadline = time.monotonic() + self.budget
        try:
            with self._open(req, timeout=self.timeout) as r:
                status = getattr(r, "status", 200)
                parts, size = [], 0
                # Read in chunks against a deadline: a server that drips one byte per timeout would
                # otherwise hold the review forever without ever tripping the socket timeout.
                while size <= max_bytes:
                    chunk = r.read(min(1 << 20, max_bytes + 1 - size))
                    if not chunk:
                        break
                    parts.append(chunk)
                    size += len(chunk)
                    if time.monotonic() > deadline:
                        raise FetchError("over the %ds time budget: %s" % (self.budget, url.split("?")[0][:120]))
                body = b"".join(parts)
        except urllib.error.HTTPError as e:
            loc = None
            try:
                loc = (e.headers or {}).get("Location")
            except AttributeError:
                loc = None
            return e.code, b"", loc
        except (OSError, ValueError) as e:
            raise FetchError("%s fetching %s" % (type(e).__name__, url.split("?")[0][:120])) from None
        if len(body) > max_bytes:
            raise FetchError("response over %d bytes: %s" % (max_bytes, url.split("?")[0][:120]))
        return status, body, None

    def json(self, url: str, headers: "dict | None" = None, max_bytes: int = 40_000_000):
        """Parsed JSON, or None on 404. Other failures raise FetchError."""
        status, body = self.get(url, headers, max_bytes)
        if status == 404:
            return None
        if status != 200:
            raise FetchError("HTTP %d from %s" % (status, url.split("?")[0][:120]))
        try:
            return json.loads(body)
        except ValueError:
            raise FetchError("not JSON: %s" % url.split("?")[0][:120]) from None

    def artifact(self, url: str, sha256: "str | None" = None, max_bytes: int = 60_000_000) -> bytes:
        """An archive's bytes. Cached on disk by sha256 when the registry published one, so a
        cached file is only ever reused when it still matches the digest."""
        path = None
        if self.cache and sha256 and len(sha256) == 64:
            path = os.path.join(self.cache, "artifacts", sha256 + ".blob")
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                if hashlib.sha256(data).hexdigest() == sha256:
                    return data
            except OSError:
                pass
        status, body = self.get(url, max_bytes=max_bytes)
        if status != 200:
            raise FetchError("HTTP %d downloading %s" % (status, url.split("?")[0][:120]))
        if path and hashlib.sha256(body).hexdigest() == sha256:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(body)
            except OSError:
                pass
        return body
