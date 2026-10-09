# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah. All rights reserved.
# Contact: abuabdullah.be@outlook.com
# Unauthorised copying, modification or distribution of this software
# is strictly prohibited without prior written permission from the author.
# =============================================================================
"""
alerts.py  —  Net-monit V11.0
Threshold evaluation + per-device alert routing with multi-level escalation.

Escalation levels:
  Level 1 — first escalation (escalation_emails_l1)
  Level 2 — second escalation (escalation_emails_l2)
  Level 3 — third escalation  (escalation_emails_l3)
  escalation_levels: 1 | 2 | 3   (how many levels active; default 1)
  Each level triggers after its own time threshold (escalation_after_sec_l1/l2/l3).

Default notifications:
  All alerts always CC admin_emails (global SMTP setting) regardless of
  per-device notify_emails, so admin is always in the loop.
  Support group is always CCd on warning+.
"""
import time
from . import database as db
from . import email_notifier
from . import messaging_gateway

STATE_RANK = {"ok": 0, "warning": 1, "critical": 2, "offline": 3}

DEFAULT_THRESHOLDS = {
    "latency_ms":      {"warning": 100,  "critical": 300},
    "packet_loss_pct": {"warning": 5,    "critical": 20},
    "cpu_pct":         {"warning": 75,   "critical": 90},
    "memory_pct":      {"warning": 80,   "critical": 95},
    "disk_pct":        {"warning": 80,   "critical": 95},
    "bandwidth_pct":   {"warning": 70,   "critical": 90},
    # V8.2: url_check.py reports response_ms, but nothing evaluated it
    # against any threshold before -- a URL/site could sit at 8 second
    # response times indefinitely and never move off "ok". Same units and
    # a similar shape to latency_ms, just a higher bar since an HTTP
    # round-trip (DNS + TLS + server processing) is naturally slower than
    # an ICMP ping.
    "response_ms":     {"warning": 1000, "critical": 5000},
}


def evaluate_metric_state(value, thresholds):
    if value is None or not thresholds:
        return "ok"
    if not thresholds.get("enabled", True):
        return "ok"
    c = thresholds.get("critical")
    w = thresholds.get("warning")
    if c is not None and value >= c:
        return "critical"
    if w is not None and value >= w:
        return "warning"
    return "ok"


def evaluate_device(device, metrics, global_thresholds, device_notify=None):
    """
    Returns (overall_status, metric_states)
    metric_states: {metric: (state, value, thresholds_used)}
    """
    if not metrics.get("reachable", False):
        return "offline", {"availability": ("offline", 0, None)}

    eff = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    for layer in (global_thresholds or {}, device.get("thresholds") or {},
                  (device_notify or {}).get("thresholds") or {}):
        for metric, override in (layer or {}).items():
            eff[metric] = {**eff.get(metric, {}), **(override or {})}

    metric_states = {}
    worst = "ok"
    KEYS = ["latency_ms", "packet_loss_pct", "cpu_pct", "memory_pct",
            "disk_pct", "bandwidth_pct", "response_ms"]

    for key in KEYS:
        if key not in metrics:
            continue
        thr = eff.get(key)
        # V8.2 fix: the per-metric "Enabled" checkbox in the thresholds
        # table was never actually consulted here -- unchecking a metric
        # had zero effect on alerting. Now it does.
        if thr and thr.get("enabled") is False:
            continue
        state = evaluate_metric_state(metrics[key], thr)
        metric_states[key] = (state, metrics[key], thr)
        if STATE_RANK.get(state, 0) > STATE_RANK.get(worst, 0):
            worst = state

    # V8.2: service/task sub-checks (service_check.py / task_check.py)
    # attached to a host. Each monitored service/task is pass/fail, not a
    # threshold value, so it's evaluated directly rather than through
    # evaluate_metric_state(): any stopped service or failed task marks the
    # WHOLE device critical (this mirrors how "offline" already works --
    # a host you depend on for a specific service being down is treated as
    # an urgent condition, not a gradual warning).
    services = metrics.get("services")
    if isinstance(services, list) and services:
        down = [s for s in services if not s.get("running", False)]
        state = "critical" if down else "ok"
        metric_states["services_down"] = (state, len(down), None)
        if STATE_RANK.get(state, 0) > STATE_RANK.get(worst, 0):
            worst = state

    tasks = metrics.get("tasks")
    if isinstance(tasks, list) and tasks:
        failed = [t for t in tasks if not t.get("ok", False)]
        state = "critical" if failed else "ok"
        metric_states["tasks_failed"] = (state, len(failed), None)
        if STATE_RANK.get(state, 0) > STATE_RANK.get(worst, 0):
            worst = state

    if not metric_states:
        worst = "ok"
        metric_states["availability"] = ("ok", 1, None)

    return worst, metric_states


def process_alerts(device, metric_states, smtp_cfg, resend_interval_minutes,
                   device_notify=None):
    """
    Multi-level escalation support:
      escalation_levels: 1 | 2 | 3
      escalation_after_sec_l1 / _l2 / _l3
      escalation_emails_l1 / _l2 / _l3

    Admin CC default: admin_emails from smtp_cfg always CC'd on every alert.
    Support CC default: always CC'd on warning+.
    """
    did   = device["id"]
    dname = device["name"]
    host  = device["host"]
    now   = time.time()
    resend_sec = resend_interval_minutes * 60

    dn = device_notify or {}
    notif_enabled   = bool(dn.get("notifications_enabled", 1))
    esc_levels      = int(dn.get("escalation_levels", 1))  # 1-3
    esc_sec_l1      = int(dn.get("escalation_after_sec_l1", dn.get("escalation_after_sec", 60)))
    esc_sec_l2      = int(dn.get("escalation_after_sec_l2", esc_sec_l1 * 2))
    esc_sec_l3      = int(dn.get("escalation_after_sec_l3", esc_sec_l1 * 3))
    # V8.2: alert trigger mode. Default changed from "immediate" to
    # "sustained" after real-world use showed immediate-by-default produced
    # too much noise from single-poll blips (a lone packet-loss spike, a
    # momentary CPU tick) -- most people want confirmation, not a page for
    # every micro-fluctuation.
    #   "sustained" (default): don't email on first detection. Wait for the
    #     L1 escalation timer (esc_sec_l1, defaults to 60s) to confirm the
    #     problem is *still* happening before sending anything. A blip that
    #     clears before esc_sec_l1 elapses is still recorded in the Alert
    #     Event Log and alert history -- it just never triggers an email.
    #     L2/L3 continue to fire on top exactly as before, counted from the
    #     same original detection time.
    #   "immediate": email the moment a device/metric leaves "ok", with no
    #     confirm delay -- opt-in per-device or globally in Settings ->
    #     Escalation for anyone who genuinely wants zero-delay paging.
    alert_trigger_mode = dn.get("alert_trigger_mode", "sustained")

    # V8.4: repeat-alert cooldown. Distinct from alert_trigger_mode above
    # (which only governs the delay before the FIRST alert of a new
    # episode) -- this governs the minimum gap between REPEAT
    # notifications once an episode is already open, regardless of how
    # many times the underlying metric flaps in and out of a bad state
    # within that window. "immediate" (default, unchanged prior
    # behavior): no extra gap beyond what trigger-mode/escalation timers
    # already impose. "cooldown": suppress any notification -- first
    # alert or repeat -- that would otherwise fire less than
    # notify_cooldown_sec after the last one actually sent for this
    # device+metric.
    alert_notify_mode = dn.get("alert_notify_mode", "immediate")
    cooldown_sec       = int(dn.get("notify_cooldown_sec", 300)) if alert_notify_mode == "cooldown" else 0

    for metric, (state, value, thresholds) in metric_states.items():
        # V7.2 + V8.4: if this device+metric currently has -- or recently
        # had, while still inside its suppression window -- an
        # Acknowledged alert, suppress everything for this metric this
        # poll: no email, no state-machine update at all. Checked for
        # EVERY state (not just warning/critical) so that a metric
        # flapping back to "ok" while suppressed doesn't send a premature
        # "recovered" email or reset the escalation clock -- see
        # database.py::get_ack_suppression's docstring for why the old
        # status=='acknowledged'-only check used to get defeated by
        # exactly that flap.
        if db.get_ack_suppression(did, metric):
            continue

        prev        = db.get_alert_state(did, metric) or {}
        prev_state  = prev.get("current_state", "ok")
        last_ts     = prev.get("last_email_ts", 0) or 0
        worst       = prev.get("worst_state", "ok")
        started_ts  = prev.get("alert_started_ts")
        esc_reached = int(prev.get("escalated", 0))  # bitmask: bit0=L1, bit1=L2, bit2=L3

        # V8.2: per-metric wait-seconds override. thresholds is this
        # metric's effective threshold dict from evaluate_device() (device
        # override > global > built-in default), which can now also carry
        # a "wait_sec" key set via the per-metric thresholds table in the
        # device Notify modal -- e.g. Packet Loss can confirm after 30s
        # while Disk waits a full 300s on the same device. Falls back to
        # the device-level L1 timer when this metric has no override set.
        metric_wait_sec = esc_sec_l1
        if thresholds and thresholds.get("wait_sec") not in (None, ""):
            try:
                metric_wait_sec = int(thresholds["wait_sec"])
            except (TypeError, ValueError):
                pass

        should_email = False
        email_state  = state
        new_esc_level = 0  # which level fires this round

        if state in ("warning", "critical", "offline"):
            if state != prev_state:
                started_ts   = now
                esc_reached  = 0
                if alert_trigger_mode == "immediate":
                    should_email  = True
                    new_esc_level = 1  # tag as L1 so esc_reached records
                                       # that an alert email went out this
                                       # incident (see recovery check below)
                # "sustained": should_email stays False here -- the L1
                # escalation check below fires the first email once the
                # condition has actually persisted for metric_wait_sec.
            elif last_ts and (now - last_ts) >= resend_sec:
                should_email = True

            if STATE_RANK.get(state, 0) > STATE_RANK.get(worst, 0):
                worst = state

            # Multi-level escalation checks. L1 uses this metric's own wait
            # time (metric_wait_sec, may be an override); L2/L3 stay
            # anchored to the device-level timers -- only the initial
            # confirm window is meant to be tunable per metric.
            if started_ts:
                elapsed = now - started_ts
                if esc_levels >= 3 and not (esc_reached & 4) and elapsed >= esc_sec_l3:
                    new_esc_level = 3
                    should_email  = True
                elif esc_levels >= 2 and not (esc_reached & 2) and elapsed >= esc_sec_l2:
                    new_esc_level = 2
                    should_email  = True
                elif esc_levels >= 1 and not (esc_reached & 1) and elapsed >= metric_wait_sec:
                    new_esc_level = 1
                    should_email  = True

        elif state == "ok" and prev_state in ("warning", "critical", "offline"):
            # V8.2: esc_reached is nonzero only if an alert email actually
            # fired at some point during this incident (immediate mode
            # tags its first email as L1; sustained mode only sets bits
            # once L1/L2/L3 actually fire). If nothing was ever emailed --
            # a sustained-mode blip that recovered inside the confirm
            # window -- skip the recovery email too; there's nothing to
            # recover FROM as far as the inbox is concerned. The state
            # change is still logged either way (see below).
            if esc_reached:
                should_email = True
                email_state  = "recovered"
            started_ts   = None
            esc_reached  = 0

        # Build notify context
        notify_ctx = dict(dn)
        notify_ctx["escalation_level_fired"] = new_esc_level
        notify_ctx["escalated"] = new_esc_level > 0 or bool(esc_reached)

        thr_display = None
        if thresholds:
            thr_display = (thresholds.get("critical") if state == "critical"
                           else thresholds.get("warning"))

        emailed = False
        # V8.4: cooldown gate. last_ts is truthy only once something has
        # actually been emailed for this device+metric before, so the
        # very first alert of an episode is never held back by cooldown --
        # only repeats within cooldown_sec of the last one actually sent.
        in_cooldown = bool(cooldown_sec and last_ts and (now - last_ts) < cooldown_sec)
        if should_email and notif_enabled and not in_cooldown:
            ok, _ = email_notifier.send_alert_email(
                smtp_cfg, dname, host, metric, value, email_state,
                threshold=thr_display,
                recovered_from=worst if email_state == "recovered" else None,
                device_notify=notify_ctx,
            )
            emailed = ok

        # V8.5: SMS / voice-call escalation, same trigger point and same
        # cooldown gate as email above -- cooldown is meant to throttle
        # ALL outbound notification for a flapping metric, not just the
        # inbox, or an SMS-primary contact would still get spammed during
        # exactly the scenario cooldown exists to prevent.
        sms_ok = call_ok = False
        if should_email and notif_enabled and not in_cooldown:
            level_for_numbers = new_esc_level or 1  # a "recovered" round has
                                                      # new_esc_level==0; use L1's
                                                      # numbers so recovery still
                                                      # reaches whoever was
                                                      # actually being escalated to
            sms_numbers  = dn.get(f"sms_numbers_l{level_for_numbers}")  or []
            call_numbers = dn.get(f"call_numbers_l{level_for_numbers}") or []
            if sms_numbers or call_numbers:
                sms_text = _build_short_alert_text(dname, metric, value, email_state, thr_display)
                for num in sms_numbers:
                    ok, err = messaging_gateway.send_sms(num, sms_text)
                    sms_ok = sms_ok or ok
                    if not ok:
                        db.audit_log("system", "sms_alert_failed", f"{did}/{metric}", f"{num}: {err}")
                for num in call_numbers:
                    ok, err = messaging_gateway.send_call(num, sms_text)
                    call_ok = call_ok or ok
                    if not ok:
                        db.audit_log("system", "call_alert_failed", f"{did}/{metric}", f"{num}: {err}")

        # Update escalation bitmask -- only for a level that actually sent.
        # V8.4: a level suppressed by cooldown must NOT be marked reached,
        # or it would silently never fire once cooldown lifts (the bitmask
        # would claim that level already happened when no one was ever
        # actually notified).
        # V8.5: gated on ANY channel succeeding (email OR sms OR call), not
        # email alone -- an SMS-only device (no email addresses configured)
        # would otherwise have email_notifier correctly report "No
        # recipients configured" every single poll forever, and the
        # bitmask would never advance, so the same level would re-attempt
        # every poll instead of properly escalating to L2/L3 over time.
        notified = emailed or sms_ok or call_ok
        if notified:
            if new_esc_level == 1:
                esc_reached |= 1
            elif new_esc_level == 2:
                esc_reached |= 3  # L1+L2
            elif new_esc_level == 3:
                esc_reached |= 7  # L1+L2+L3

        next_worst    = "ok"       if email_state == "recovered" else worst
        next_escalated= 0          if email_state == "recovered" else esc_reached
        next_started  = started_ts
        # V8.5: was `should_email and emailed` -- broadened to `notified`
        # (email OR sms OR call) for the same reason as the bitmask fix
        # above: an SMS-only device's cooldown timer must advance on a
        # successful SMS send, or in_cooldown would stay permanently False
        # (last_ts never leaves its initial falsy state) and cooldown mode
        # would silently never engage for that device.
        new_last_ts   = (now if (should_email and notified) else last_ts)

        if state != prev_state or should_email or new_esc_level:
            db.set_alert_state(did, metric, state, new_last_ts,
                               worst_state=next_worst,
                               alert_started_ts=next_started,
                               escalated=next_escalated)
            msg = _msg(dname, metric, value, email_state, thr_display)
            if (sms_ok or call_ok) and not emailed:
                msg += "  [notified via " + "+".join(
                    filter(None, ["SMS" if sms_ok else None, "call" if call_ok else None])
                ) + "]"
            # V8.5: was the email-only `emailed` -- alert_log.emailed now
            # means "was this incident actually notified via at least one
            # channel", matching the bitmask/cooldown-timer broadening above.
            db.log_alert(did, dname, metric,
                         value if isinstance(value, (int, float)) else 0,
                         email_state, msg, notified)


def _msg(device_name, metric, value, state, threshold):
    ml = metric.replace("_", " ")
    if state == "recovered":
        return f"{device_name}: {ml} back to normal ({value})"
    if state == "offline":
        return f"{device_name}: device is unreachable"
    ts = f" (threshold {threshold})" if threshold is not None else ""
    return f"{device_name}: {ml} is {state.upper()} at {value}{ts}"
