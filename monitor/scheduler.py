# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah. All rights reserved.
# Contact: abuabdullah.be@outlook.com
# Unauthorised copying, modification or distribution of this software
# is strictly prohibited without prior written permission from the author.
# =============================================================================
"""
scheduler.py  —  Net-monit V11.0
APScheduler background polling. Each device runs in its own interval job.
"""
import logging
import time
from apscheduler.schedulers.background import BackgroundScheduler
from . import database as db
from . import alerts
from . import crypto
from .checkers import ping_check, snmp_check, powershell_check, ssh_check, disk_usage_check, url_check, speedtest_check, service_check, task_check

log = logging.getLogger("monitor.scheduler")
_scheduler = None


def _poll_device(device, app_cfg):
    # Decrypt credentials before passing to checkers
    device = crypto.decrypt_device_creds(device)
    did    = device["id"]
    dname  = device["name"]
    host   = device["host"]
    method = device.get("method", "ping")

    try:
        if method == "ping":
            timeout_ms = device.get("ping_timeout_ms",
                         app_cfg.get("defaults", {}).get("ping_timeout_ms", 1000))
            metrics = ping_check.check(host, timeout_ms=timeout_ms)

        elif method == "snmp":
            sc = device.get("snmp", {})
            metrics = snmp_check.check(
                host,
                community=sc.get("community", "public"),
                version=sc.get("version", 2),
                interfaces=sc.get("interfaces"),
            )

        elif method == "powershell":
            pc = device.get("powershell", {})
            metrics = powershell_check.check(
                host,
                remote=pc.get("remote", False),
                use_winrm=pc.get("use_winrm", False),
                username=pc.get("username"),
                password=pc.get("password"),
            )

        elif method == "ssh":
            sc = device.get("ssh", {})
            metrics = ssh_check.check(
                host,
                port=sc.get("port", 22),
                username=sc.get("username", "monitor"),
                password=sc.get("password"),
                key_path=sc.get("key_path"),
            )

        elif method == "disk_usage":
            metrics = disk_usage_check.check(device)

        elif method == "url":
            metrics = url_check.check(device)
            # V10.1: evaluate_device() (monitor/alerts.py) has no
            # consecutive-failure confirmation anywhere -- ANY single
            # unreachable result flips status to "offline" and fires an
            # alert immediately, for every device method, by design
            # elsewhere in that file (out of scope to change here, and
            # CLAUDE.md specifically warns that file is the most
            # bug-prone -- trace the full call chain before touching it).
            # URL checks are the method most exposed to a one-off transient
            # failure: a momentary CDN/WAF hiccup, a DNS resolver retry, an
            # upstream restart that clears in a couple of seconds -- exactly
            # the "site's actually fine, Net-monit just caught it mid-blip"
            # complaint. One quick in-process retry before accepting
            # "unreachable" catches that case without touching alerts.py or
            # adding new schema/config. A genuinely-down site still fails
            # both checks and is still reported offline, still alerts.
            if not metrics.get("reachable"):
                time.sleep(2)
                metrics = url_check.check(device)

        elif method == "speedtest":
            metrics = speedtest_check.check(device)

        else:
            log.warning("Unknown method '%s' for device %s", method, did)
            return

    except Exception as exc:
        log.error("Poll error %s: %s", did, exc)
        metrics = {"reachable": False, "error": str(exc)}

    # V8.2: services/tasks watching is an optional EXTRA layered on top of
    # whatever primary check just ran above (ping/powershell/ssh/url/etc),
    # not a separate device method -- see service_check.py / task_check.py.
    # Merged into the same metrics dict so evaluate_device(), the Alert
    # Event Log, and the dashboard tile all pick them up through the exact
    # same pipeline as every other metric, with no special-casing needed.
    try:
        if (device.get("services_watch") or {}).get("enabled"):
            sres = service_check.check(device)
            if sres.get("services"):
                metrics["services"] = sres["services"]
            if sres.get("error") and not sres.get("reachable", True):
                metrics.setdefault("warnings", []).append(f"Service check: {sres['error']}")
        if (device.get("tasks_watch") or {}).get("enabled"):
            tres = task_check.check(device)
            if tres.get("tasks"):
                metrics["tasks"] = tres["tasks"]
            if tres.get("error") and not tres.get("reachable", True):
                metrics.setdefault("warnings", []).append(f"Task check: {tres['error']}")
    except Exception as exc:
        log.error("Service/task watch error %s: %s", did, exc)
        metrics.setdefault("warnings", []).append(f"Service/task watch error: {exc}")

    global_thr   = app_cfg.get("defaults", {}).get("thresholds", {})
    device_notify = db.get_device_notify(did)

    overall, metric_states = alerts.evaluate_device(
        device, metrics, global_thr, device_notify=device_notify)

    error_msg = metrics.get("error") or metrics.get("last_error")
    db.upsert_device_status(did, dname, device.get("type","network"),
                            method, host, overall, metrics, error=error_msg)

    for metric, (state, value, _) in metric_states.items():
        if isinstance(value, (int, float)):
            db.record_metric(did, metric, value, state)

        # V7.2: Active-alert lifecycle (Alert Event Logs page).
        # A metric in warning/critical opens (or refreshes) an active alert;
        # a metric back to ok silently auto-resolves any open alert for it.
        if state in ("warning", "critical"):
            db.upsert_active_alert(
                did, dname, host, metric, state,
                f"{dname} ({host}) — {metric.replace('_',' ')} is {state}"
                f"{': ' + str(round(value,1)) if isinstance(value,(int,float)) else ''}"
            )
        elif state == "ok":
            db.auto_resolve_active_alerts(did, metric)

    smtp_cfg  = app_cfg.get("smtp", {})
    resend    = smtp_cfg.get("resend_interval_minutes", 30)
    alerts.process_alerts(device, metric_states, smtp_cfg, resend,
                          device_notify=device_notify)


def start_scheduler(app_cfg):
    global _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _add_jobs(app_cfg)
    _scheduler.start()
    log.info("Scheduler started with %d device(s)", len(app_cfg.get("devices", [])))


def reload_scheduler(app_cfg):
    global _scheduler
    if _scheduler:
        _scheduler.remove_all_jobs()
        _add_jobs(app_cfg)
        log.info("Scheduler reloaded: %d device(s)", len(app_cfg.get("devices", [])))


def _add_jobs(app_cfg):
    default_interval = app_cfg.get("defaults", {}).get("poll_interval_seconds", 30)
    for device in app_cfg.get("devices", []):
        interval = device.get("poll_interval_seconds", default_interval)
        _scheduler.add_job(
            _poll_device,
            "interval",
            seconds=interval,
            args=[device, app_cfg],
            id=device["id"],
            next_run_time=__import__("datetime").datetime.now(),
            misfire_grace_time=60,
            coalesce=True,
        )
