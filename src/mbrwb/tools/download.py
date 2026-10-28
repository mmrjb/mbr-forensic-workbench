"""Operator-initiated HTTPS downloads from reviewed origins only.

* Only ``https`` URLs whose origin is in the reviewed allowlist are fetched.
* Redirects are followed only to allowlisted origins (max 3), never to http.
* Default TLS verification (system trust store); no verification bypass exists.
* Connect/read timeouts, a size cap, cancellation between chunks, and a ``.part`` file
  renamed only after a complete transfer. The SHA-256 is computed while streaming.
"""
from __future__ import annotations

import hashlib
import os
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .. import __version__


class DownloadError(Exception):
    pass


class DownloadCancelled(DownloadError):
    pass


@dataclass
class DownloadResult:
    url: str
    final_url: str
    path: Path
    size: int
    sha256: str
    etag: str | None
    last_modified: str | None


def origin_of(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")


class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def __init__(self, allowed: tuple[str, ...]):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if origin_of(newurl) not in self.allowed or not newurl.startswith("https://"):
            raise DownloadError(f"Redirect to a non-allowlisted location was refused: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, dest: Path, *, allowed_origins: tuple[str, ...], timeout: int = 60, max_bytes: int = 512 * 1024**2,
          cancel: threading.Event | None = None, progress=None) -> DownloadResult:
    if not url.startswith("https://"):
        raise DownloadError("Only HTTPS downloads are allowed.")
    if origin_of(url) not in allowed_origins:
        raise DownloadError(f"{origin_of(url)} is not a reviewed download origin. Allowed: {', '.join(allowed_origins)}")
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _AllowlistRedirect(allowed_origins))
    req = urllib.request.Request(url, headers={"User-Agent": f"MBR-Forensic-Workbench/{__version__}"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    size = 0
    try:
        with opener.open(req, timeout=timeout) as resp:
            final = resp.geturl()
            if origin_of(final) not in allowed_origins:
                raise DownloadError(f"Final location {final} is not allowlisted.")
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise DownloadError(f"Download is {int(declared)} bytes, above the {max_bytes} byte limit.")
            with open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled("Download cancelled.")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadError("Download exceeded the size limit.")
                    h.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(size, int(declared) if declared else None)
            if declared and size != int(declared):
                raise DownloadError(f"Incomplete download: {size} of {declared} bytes.")
            os.replace(part, dest)
            return DownloadResult(url, final, dest, size, h.hexdigest(), resp.headers.get("ETag"), resp.headers.get("Last-Modified"))
    except urllib.error.URLError as exc:
        raise DownloadError(f"Network error: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise DownloadError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
