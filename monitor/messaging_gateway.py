# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah. All rights reserved.
# Contact: abuabdullah.be@outlook.com
# =============================================================================
"""
messaging_gateway.py -- Net-monit V8.5 (new)

Sends SMS and voice-call escalation alerts through an admin-configured HTTP
gateway. Deliberately generic rather than hardcoded to one vendor's SDK --
"any SMS gateway or SMS API" was an explicit requirement, and third-party
telephony APIs vary too much in shape to give each one native code without
an ever-growing, ever-drifting integration list. Instead: ONE small, uniform
mechanism (configurable URL/method/headers/body, with {{phone}} and
{{message}} placeholders substituted in -- reusing the exact same {{...}}
syntax as monitor/notification_templates.py, deliberately, for consistency)
covers SMS and voice calls alike, and PRESETS below pre-fill that mechanism
for a few common providers so most admins never have to read a raw API spec.

IMPORTANT, read before "fixing" a preset that looks incomplete: third-party
API shapes were verified against each provider's current documentation as
of this session, not assumed from memory -- but they DO change over time,
and this module cannot detect that. Presets are a verified starting point,
not a guarantee; the module always surfaces the provider's raw HTTP error
back to the admin (via the Settings UI's "Send test" button and via
alert_log) specifically so a preset going stale is visible immediately
rather than failing silently.

Teltonika RUTOS specifically: Teltonika removed the simple GET-based
cgi-bin/sms_send method in RutOS 7.14+ "for security reasons" (confirmed via
their own community forum, mid-2025) in favor of an authenticated JSON REST
API that requires a login step to obtain a bearer token. This module's
single-templated-request model does not perform that login step for you --
the RUTOS 7.14+ preset expects the admin to obtain a token from their
router's own UI/API and paste it in as a static header value. Tokens may
expire depending on the router's own settings, in which case SMS sending
will start failing until the admin refreshes it -- this is a real, inherent
limitation of RUTOS's newer auth model, not a bug in this module. The
legacy pre-7.14 cgi-bin preset is also offered, clearly labeled deprecated,
for routers still on older firmware.
"""
import json
import ssl
import time
import urllib.request
import urllib.error

from monitor import config_manager

TIMEOUT_SEC = 15


# =============================================================================
# Presets -- each pre-fills the generic gateway fields. All are starting
# points the admin can (and, for Teltonika's token, must) edit before use.
# =============================================================================
PRESETS = {
    "custom": {
        "label": "Custom / generic HTTP gateway",
        "method": "POST",
        "url": "",
        "headers": {"Content-Type": "application/json"},
        "body_template": '{"to": "{{phone}}", "text": "{{message}}"}',
        "note": "Fill in your provider's URL, headers (e.g. an API key), and "
                "body shape yourself. {{phone}} and {{message}} are substituted "
                "in wherever they appear, in the URL, headers, or body.",
    },
    "twilio_sms": {
        "label": "Twilio -- SMS",
        "method": "POST",
        "url": "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "body_template": "To={{phone}}&From={your_twilio_number}&Body={{message}}",
        "auth": "basic",  # Account SID as username, Auth Token as password
        "note": "Replace {account_sid} in the URL and {your_twilio_number} in the "
                "body with your own values. Set Basic Auth below using your "
                "Account SID as the username and your Auth Token as the password.",
    },
    "twilio_voice": {
        "label": "Twilio -- Voice call (text-to-speech)",
        "method": "POST",
        "url": "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "body_template": "To={{phone}}&From={your_twilio_number}"
                          "&Twiml=<Response><Say>{{message}}</Say></Response>",
        "auth": "basic",
        "note": "Same Account SID / Auth Token as Twilio SMS above. The Twiml "
                "parameter's <Say> verb is what makes Twilio read the alert "
                "text aloud on the call.",
    },
    "teltonika_rutos_new": {
        "label": "Teltonika RUTOS 7.14+ (token-based API)",
        "method": "POST",
        "url": "https://{router_ip}/api/messages/actions/send",
        "headers": {"Content-Type": "application/json",
                    "Authorization": "Bearer {paste_your_token_here}"},
        "body_template": '{"data": {"number": "{{phone}}", "message": "{{message}}", "modem": "1-1"}}',
        "note": "RUTOS 7.14+ requires a bearer token obtained by logging into your "
                "router's API first (see your router's API documentation, usually "
                "at https://<router-ip>/api-docs) -- paste the token into the "
                "Authorization header above. Tokens can expire; if sending "
                "suddenly stops working, this is usually why.",
    },
    "teltonika_rutos_legacy": {
        "label": "Teltonika RUTOS (pre-7.14 legacy, deprecated)",
        "method": "GET",
        "url": "http://{router_ip}/cgi-bin/sms_send?username={router_user}"
               "&password={router_pass}&number={{phone}}&text={{message}}",
        "headers": {},
        "body_template": "",
        "note": "Teltonika removed this method in RutOS 7.14+. Only use this preset "
                "if your router is confirmed still running an older firmware version "
                "-- otherwise use the token-based preset above.",
    },
}


def _substitute(template: str, phone: str, message: str) -> str:
    return (template or "").replace("{{phone}}", phone).replace("{{message}}", message)


def _send_via_gateway(cfg: dict, phone: str, message: str):
    """Fires one HTTP request per the gateway's configured method/url/
    headers/body, substituting {{phone}}/{{message}} into all three.
    Returns (ok: bool, error: str|None). Never raises -- callers (alerts.py)
    treat this exactly like email_notifier.send_alert_email()'s (ok, error)
    contract, so it drops into the existing escalation/cooldown gating with
    no special-casing needed there."""
    if not cfg or not cfg.get("enabled"):
        return False, "gateway not configured"
    url = _substitute(cfg.get("url", ""), phone, message)
    if not url:
        return False, "gateway URL is empty"
    method = (cfg.get("method") or "POST").upper()
    headers = {k: _substitute(v, phone, message) for k, v in (cfg.get("headers") or {}).items()}
    body_str = _substitute(cfg.get("body_template", ""), phone, message)

    if cfg.get("auth") == "basic" and cfg.get("auth_user"):
        import base64
        token = base64.b64encode(
            f"{cfg['auth_user']}:{cfg.get('auth_pass','')}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"

    data = body_str.encode("utf-8") if (method != "GET" and body_str) else None
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        ctx = ssl.create_default_context()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=TIMEOUT_SEC) as resp:
            status = resp.getcode()
            if 200 <= status < 300:
                return True, None
            return False, f"Gateway returned HTTP {status}"
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(500).decode("utf-8", errors="ignore")
        except Exception:
            detail = ""
        return False, f"HTTP {e.code} {e.reason}" + (f" -- {detail}" if detail else "")
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def send_sms(phone: str, message: str):
    """Sends one SMS via the configured SMS gateway. Returns (ok, error)."""
    cfg = config_manager.get_sms_gateway()
    return _send_via_gateway(cfg, phone, message)


def send_call(phone: str, message: str):
    """Places one voice call via the configured call gateway, delivering
    `message` however that gateway's template is set up to (typically
    text-to-speech, e.g. Twilio's <Say> verb). Returns (ok, error)."""
    cfg = config_manager.get_call_gateway()
    return _send_via_gateway(cfg, phone, message)


def test_gateway(kind: str, phone: str):
    """Used by the Settings UI's "Send test" buttons. kind is 'sms' or 'call'."""
    message = "This is a test alert from Net-monit. If you received this, your gateway is configured correctly."
    if kind == "sms":
        return send_sms(phone, message)
    elif kind == "call":
        return send_call(phone, message)
    return False, f"Unknown gateway kind: {kind}"
