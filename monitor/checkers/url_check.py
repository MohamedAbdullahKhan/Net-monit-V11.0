# =============================================================================
# Net-monit V11.0 -- URL / Web Application (Sites) Monitor
# Copyright (c) 2024-2026 Net-monit V11.0 -- Developed by Abdullah -- InfoXtek.com
# =============================================================================
"""
url_check.py
Monitors web application URLs for HTTP status, response time, SSL certificate
expiry, and keyword presence. Designed to be defensive: a problem in the
secondary SSL-expiry probe can NEVER cause a healthy site to be reported
offline -- only a confirmed successful HTTP response determines reachability.
"""
import logging, time, ssl, socket
import urllib.request, urllib.error
from urllib.parse import urlparse
from datetime import datetime, timezone

log = logging.getLogger("monitor.checkers.url")


def _ssl_expiry_days(hostname: str, port: int = 443, timeout: float = 4.0):
    """Best-effort SSL expiry lookup. Never raises -- returns None on any failure."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
        exp_str = cert.get("notAfter", "")
        if not exp_str:
            return None
        exp_dt = datetime.strptime(exp_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        delta = exp_dt - datetime.now(timezone.utc)
        if delta.total_seconds() <= 0:
            return 0  # genuinely in the past
        # V10.1: was `max(0, delta.days)` -- a cert with, say, 6 hours left
        # (still valid) has delta.days == 0 too, which the caller below
        # treats identically to "0 = expired" and flips a working,
        # reachable site to offline over a certificate that hasn't
        # actually expired yet. max(1, ...) here keeps that distinct from
        # the true-expiry case returned just above.
        return max(1, delta.days)
    except Exception as e:
        log.debug("SSL expiry probe failed for %s: %s", hostname, e)
        return None


def check(device: dict) -> dict:
    """
    device = {
      "host": "https://sub.domain.com/path"  (or a bare domain/IP -- caller normalises),
      "url_config": {
        "expected_status": 200, "timeout_sec": 10, "keyword": "...",
        "check_ssl": True, "ssl_warn_days": 30, "max_response_ms": 2000
      }
    }
    """
    url = (device.get("host") or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    cfg             = device.get("url_config") or {}
    expected_status = int(cfg.get("expected_status") or 200)
    timeout_sec     = max(1, int(cfg.get("timeout_sec") or 10))
    keyword         = (cfg.get("keyword") or "").strip()
    check_ssl_cert  = bool(cfg.get("check_ssl", True))
    ssl_warn_days   = int(cfg.get("ssl_warn_days") or 30)
    max_resp_ms     = cfg.get("max_response_ms")

    result = {
        "reachable": False, "status_code": None, "response_ms": None,
        "content_size_kb": 0.0, "ssl_days_left": None, "ssl_status": "N/A",
        "error": None, "warnings": [],
    }

    hostname = None
    try:
        hostname = urlparse(url).hostname
    except Exception:
        pass

    # ── Single HTTP(S) request, redirects followed automatically ──────────
    req = urllib.request.Request(
        url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
    ctx = ssl.create_default_context()  # only used for the https:// opener
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            body = resp.read(2_000_000)  # cap at 2MB
            status = resp.getcode()

            result["status_code"]     = status
            result["response_ms"]     = elapsed_ms
            result["content_size_kb"] = round(len(body) / 1024.0, 2)

            # Accept an exact match, OR -- if the caller left expected_status
            # at the 200 default -- accept any 2xx (covers sites that
            # legitimately return 201/204/etc. without the user needing to
            # know that in advance).
            status_ok = (status == expected_status) or \
                        (expected_status == 200 and 200 <= status < 400)
            if status_ok:
                result["reachable"] = True
            else:
                result["error"] = f"HTTP {status} (expected {expected_status})"

            if keyword:
                try:
                    body_text = body.decode("utf-8", errors="ignore")
                except Exception:
                    body_text = ""
                if keyword.lower() not in body_text.lower():
                    result["reachable"] = False
                    kw_err = f"Keyword '{keyword[:30]}' not found in response"
                    result["error"] = f"{result['error']} | {kw_err}" if result["error"] else kw_err

            if max_resp_ms and elapsed_ms > int(max_resp_ms):
                result["warnings"].append(
                    f"Slow response: {elapsed_ms}ms > {max_resp_ms}ms threshold")

    except urllib.error.HTTPError as e:
        # Server responded, just with an error status -- still "reachable"
        # from a network standpoint if that status is what was expected.
        result["status_code"] = e.code
        result["response_ms"] = int((time.perf_counter() - t0) * 1000)
        result["reachable"]   = (e.code == expected_status)
        if not result["reachable"]:
            result["error"] = f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        result["error"] = f"Connection failed: {e.reason}"
    except socket.timeout:
        result["error"] = f"Timeout after {timeout_sec}s"
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"

    # ── SSL expiry: best-effort, isolated, can only downgrade a page that
    #    is ALREADY reachable if the cert is confirmed expired. A failed
    #    probe (timeout, rate limit, etc.) only adds a warning, never
    #    flips a working site to offline. ────────────────────────────────
    if check_ssl_cert and hostname and url.startswith("https://"):
        days = _ssl_expiry_days(hostname)
        result["ssl_days_left"] = days
        if days is None:
            result["ssl_status"] = "Unverified"
        elif days <= 0:
            result["ssl_status"] = "EXPIRED"
            if result["reachable"]:
                result["reachable"] = False
                result["error"] = "SSL certificate EXPIRED"
        elif days <= ssl_warn_days:
            result["ssl_status"] = f"Expiring ({days}d)"
            result["warnings"].append(f"SSL certificate expires in {days} days")
        else:
            result["ssl_status"] = "VALID"

    return result
