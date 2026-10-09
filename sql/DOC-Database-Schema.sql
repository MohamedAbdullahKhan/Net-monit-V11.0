-- =============================================================================
-- Net-monit V8.6 — Complete Database Schema
-- =============================================================================
-- SQLite 3. Target file: data/monitor.db
--
-- HOW THIS FILE WAS PRODUCED: this is not hand-transcribed. It was generated
-- by actually running monitor/database.py's real init_db() (plus the three
-- lazily-guarded table-creation helpers it also ships, see the note in
-- Section 0 below) against a fresh SQLite file, then dumping the resulting
-- live schema straight back out of sqlite_master. What you're looking at
-- is exactly what the application itself creates — guaranteed to match
-- the code, not a description of it that could drift out of sync.
--
-- HOW TO USE THIS FILE:
--   - You do NOT need to run this manually to install Net-monit. The app
--     creates its own database automatically on first start (via
--     monitor/database.py::init_db()), including automatic migration of
--     existing databases when upgrading between versions.
--   - This file exists for reference, review, and for anyone who wants to
--     pre-create an empty database outside the app (DBA review, CI, a
--     documentation generator, etc). It IS valid, runnable SQL if you want
--     to do that:
--         sqlite3 monitor.db < DOC-Database-Schema-V8.sql
--   - Table order below is grouped by function, not alphabetical, to read
--     more like documentation.
--
-- Generated for: Net-monit V8.6 (2026)
-- =============================================================================


-- =============================================================================
-- SECTION 0 — A note on how tables actually get created in the real code
-- =============================================================================
-- Most tables below are created upfront, unconditionally, every time the app
-- starts (monitor/database.py::init_db(), called on every launch, every
-- statement uses CREATE TABLE IF NOT EXISTS so it's always safe to re-run).
--
-- THREE tables are created lazily instead — the very first time a function
-- that needs them is actually called, via a small internal guard function,
-- rather than upfront in init_db():
--   - system_settings      (guarded by _ensure_system_settings_table())
--   - speedtest_history     (guarded by _ensure_new_tables())
--   - network_scan_jobs     (guarded by _ensure_new_tables())
-- Every single function that touches these three tables calls its guard
-- function first, so this is safe in practice (unlike active_alerts used to
-- be — see below) — it just means an EMPTY, freshly-installed database will
-- not show these three tables until the corresponding feature (Speed Test,
-- Network Scan, or a system-wide default theme/color) has been used at least
-- once. This SQL file includes all of them regardless, since it documents
-- the COMPLETE schema the app can produce, not just what a 30-second-old
-- install happens to already contain.
--
-- One table used to follow this same lazy pattern and had a real bug because
-- of it: active_alerts (the table behind the Alert Event Log UI) was only
-- created the first time an alert ever fired, which meant deleting a device
-- before any alert had ever happened would crash with "no such table". Fixed
-- in V8.2 by moving it into the upfront/unconditional set below, where it
-- now lives permanently. See the Claude Reference Guide, Section 10/13, for
-- the full story — flagged here too since it's the kind of pattern worth
-- recognizing before adding a 4th lazily-created table to this codebase.
-- =============================================================================


-- =============================================================================
-- SECTION 1 — Accounts, sessions, licensing
-- =============================================================================

-- Login accounts. role = 'admin' | 'user'. Sessions themselves are NOT a
-- table -- they're an in-memory dict in monitor/auth.py and do not survive
-- a service restart (a known, deliberate limitation, not an oversight).
CREATE TABLE email_users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT UNIQUE NOT NULL,
    email_hash  TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    token_hash  TEXT NOT NULL,
    active      INTEGER DEFAULT 1,
    created_ts  REAL
);

-- Admin action trail, shown in the Admin panel.
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT,
    action      TEXT,
    target      TEXT,
    detail      TEXT,
    ts          REAL
);

-- Single-row table (id is CHECK'd to always be 1) holding this
-- installation's license/trial state. device_id/activation_code are
-- derived from this machine's own hardware fingerprint at runtime, not
-- stored here as input -- license_key is what the Product Owner's License
-- Key Generator produces and the customer pastes into the Activation screen.
CREATE TABLE license_record (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    device_id       TEXT,
    activation_code TEXT,
    license_key     TEXT DEFAULT '',
    trial_start     REAL,
    activated       INTEGER DEFAULT 0
);


-- =============================================================================
-- SECTION 2 — Device monitoring: current state + history
-- =============================================================================

-- Latest poll result per device. metrics_json is the FULL raw dict the
-- relevant checker returned (ping_check / url_check / service_check /
-- etc), stored as-is -- the dashboard tile reads straight from this, which
-- is why fields a checker returns are available to the frontend even for
-- metrics the alerting engine (Section 3) doesn't have a threshold for.
CREATE TABLE device_status (
    device_id      TEXT PRIMARY KEY,
    name           TEXT,
    type           TEXT,       -- 'server' | 'storage' | 'web'
    method         TEXT,       -- 'ping' | 'snmp' | 'powershell' | 'ssh' | 'disk_usage' | 'url'
    host           TEXT,
    status         TEXT DEFAULT 'unknown',
    last_checked   REAL,
    last_error     TEXT,
    metrics_json   TEXT
);

-- Time-series of individual metric values, one row per metric per poll --
-- what graphs/history views read from.
CREATE TABLE metric_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT,
    metric      TEXT,
    value       REAL,
    state       TEXT,
    ts          REAL
);


-- =============================================================================
-- SECTION 3 — Alerting: state machine, audit trail, per-device notify config
-- =============================================================================

-- Per-device-per-metric CURRENT state machine -- what alerts.py actually
-- reads/writes on every poll to decide whether to email anyone. Not
-- customer-visible directly (active_alerts below is the UI-facing view).
CREATE TABLE alert_state (
    device_id           TEXT,
    metric              TEXT,
    current_state       TEXT,
    last_email_ts       REAL,
    worst_state         TEXT DEFAULT 'ok',
    alert_started_ts    REAL,
    escalated           INTEGER DEFAULT 0,   -- bitmask: bit0=L1, bit1=L2, bit2=L3 reached this incident
    PRIMARY KEY (device_id, metric)
);

-- Append-only audit trail of EVERY state transition, whether or not it was
-- emailed (emailed=0/1 per row). This is what proves "sustained" alert mode
-- (V8.2) is actually logging brief blips even when it correctly declines to
-- email anyone about them.
CREATE TABLE alert_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT,
    device_name TEXT,
    metric      TEXT,
    value       REAL,
    state       TEXT,
    message     TEXT,
    ts          REAL,
    emailed     INTEGER DEFAULT 0
);

-- Admin-editable email templates (Settings > Templates). A state with NO
-- row here uses the built-in default from notification_templates.py's
-- DEFAULT_TEMPLATES -- this table stores OVERRIDES only, so a fresh
-- install or an un-customized state behaves identically to older
-- versions that had no template-editing feature at all.
-- NOTE: this table existed in the real schema well before V8.4 but was
-- missing from earlier revisions of this hand-maintained doc -- added
-- here (sourced from an actual init_db() run, not hand-typed) as routine
-- housekeeping alongside the V8.4 rename.
CREATE TABLE notification_templates (
    state             TEXT PRIMARY KEY,   -- warning | critical | offline | recovered
    subject_template  TEXT,
    text_template     TEXT,
    html_template     TEXT,
    enabled           INTEGER DEFAULT 1,
    updated_ts        REAL,
    updated_by        TEXT
);

-- V8.4: admin-defined static text placeholders (e.g. {{company_name}} ->
-- "Acme Corp"), distinct from the built-in placeholders in
-- notification_templates.py which are populated from live alert data at
-- render time. Settings > Templates > "Custom placeholders".
CREATE TABLE custom_placeholders (
    key               TEXT PRIMARY KEY,
    value             TEXT DEFAULT '',
    description       TEXT DEFAULT '',
    created_ts        REAL,
    updated_ts        REAL,
    updated_by        TEXT
);

-- THE ALERT EVENT LOG the admin actually sees in the UI. One row per
-- open/acknowledged/resolved incident (not one row per poll, unlike
-- alert_log above). status: 'active' | 'acknowledged' | 'resolved'.
-- ack_until_ts is set to 23:59:59 on the day it's acknowledged; once "now"
-- passes that timestamp, the next poll that finds the same problem still
-- happening reopens it as a fresh incident automatically.
--
-- V8.2: moved from a lazily-created table into this upfront set -- see
-- SECTION 0 above for why that mattered.
CREATE TABLE active_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT NOT NULL,
    device_name     TEXT,
    device_host     TEXT,
    metric          TEXT,
    severity        TEXT,                    -- warning | critical
    message         TEXT,
    status          TEXT DEFAULT 'active',    -- active | acknowledged | resolved
    triggered_ts    REAL,
    acknowledged_ts REAL,
    acknowledged_by TEXT,
    ack_until_ts    REAL,
    resolved_ts     REAL,
    resolved_by     TEXT
);
CREATE INDEX idx_active_alerts_lookup
    ON active_alerts (device_id, metric, status);

-- Per-device notification configuration: who gets emailed, the 3-level
-- escalation ladder (delay in seconds + recipient list per level), a
-- per-device threshold override (JSON), and (NEW in V8.2) whether this
-- device alerts immediately or waits to confirm first. A device with no
-- row here yet uses the application's built-in defaults (see
-- monitor/database.py::get_device_notify()) -- it is not required to have
-- a row for every device.
CREATE TABLE device_notify (
    device_id                   TEXT PRIMARY KEY,
    notifications_enabled       INTEGER DEFAULT 1,
    notify_emails_json          TEXT DEFAULT '[]',
    escalation_emails_l1_json   TEXT DEFAULT '[]',
    escalation_emails_l2_json   TEXT DEFAULT '[]',
    escalation_emails_l3_json   TEXT DEFAULT '[]',
    escalation_after_sec_l1     INTEGER DEFAULT 60,     -- V8.2: default changed from 300 to 60
    escalation_after_sec_l2     INTEGER DEFAULT 600,
    escalation_after_sec_l3     INTEGER DEFAULT 1200,
    escalation_levels           INTEGER DEFAULT 1,      -- how many of L1/L2/L3 are active: 1, 2, or 3
    thresholds_json             TEXT DEFAULT '{}',
    -- V8.2: 'sustained' (DEFAULT) waits for the L1 timer above (default
    -- 60s) to confirm a problem is still happening before emailing at all
    -- -- a single small up/down blip never pages anyone, only a problem
    -- that's still there after the confirm window. 'immediate' (the
    -- default before this refinement) emails on first detection with no
    -- confirm delay -- still available as an explicit opt-in.
    alert_trigger_mode          TEXT DEFAULT 'sustained',
    -- V8.4: repeat-alert cooldown. Distinct from alert_trigger_mode above,
    -- which only governs the delay before the FIRST alert of a new
    -- episode -- these two govern the minimum gap between REPEAT alerts
    -- once an episode is already open, so a metric flapping in and out
    -- of a bad state doesn't email on every flap. 'immediate' (DEFAULT,
    -- preserves pre-V8.4 behavior): no extra gap beyond trigger-mode/
    -- escalation timers. 'cooldown': suppress any notification -- first
    -- or repeat -- less than notify_cooldown_sec after the last one
    -- actually sent for this device+metric.
    alert_notify_mode           TEXT DEFAULT 'immediate',
    notify_cooldown_sec         INTEGER DEFAULT 300
);


-- =============================================================================
-- SECTION 4 — Per-user preferences (dashboard layout, view mode, theme)
-- =============================================================================

-- Generic per-user key/value store. This is the ONLY per-user-preference
-- storage in V8.2 -- keys currently in use: 'theme', 'accent_color',
-- 'view_mode' ('grid'|'list'), 'grid_cols' (0 = Auto), and
-- 'tile_layout_v2' (a JSON array describing this user's saved dashboard
-- tile positions/sizes/visibility).
--
-- V8.2 note: two OTHER dedicated tables for this same purpose
-- (user_tile_layout, user_dashboard_prefs) existed in V7.2 but were never
-- actually wired to anything -- dead code, removed in V8.2. If you're
-- extending per-user preferences in a future version, add a new key here
-- rather than building a third parallel table next to this one.
CREATE TABLE user_prefs_kv (
    email      TEXT NOT NULL,
    pref_key   TEXT NOT NULL,
    pref_value TEXT DEFAULT '',
    PRIMARY KEY (email, pref_key)
);

-- Global/legacy tile order+visibility, used as the fallback for a user who
-- has no personal layout saved yet in user_prefs_kv (e.g. a brand-new
-- account, or before they've ever entered Edit Mode and clicked Save Layout).
CREATE TABLE dashboard_tiles (
    device_id   TEXT PRIMARY KEY,
    position    INTEGER DEFAULT 0,
    hidden      INTEGER DEFAULT 0,
    size        TEXT DEFAULT 'normal',
    col         INTEGER DEFAULT 0,
    row         INTEGER DEFAULT 0
);

-- System-wide defaults (as opposed to per-user prefs above) -- currently
-- used for the installation's default theme/accent color shown to a user
-- before they've picked their own. Lazily created -- see SECTION 0.
CREATE TABLE system_settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT
);


-- =============================================================================
-- SECTION 5 — Feature-specific history (Speed Test, Network Scan)
-- =============================================================================
-- Both lazily created -- see SECTION 0.

CREATE TABLE speedtest_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL,
    download_mbps REAL,
    upload_mbps   REAL,
    latency_ms    REAL,
    server        TEXT,
    method_used   TEXT,
    elapsed_sec   REAL,
    jitter_ms     REAL,
    isp           TEXT,
    public_ip     TEXT
);

CREATE TABLE network_scan_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subnet      TEXT,
    status      TEXT DEFAULT 'pending',
    result_json TEXT,
    created_ts  REAL,
    updated_ts  REAL
);


-- =============================================================================
-- End of schema. 16 tables, 1 explicit secondary index (SQLite also
-- auto-creates its own internal indexes for every PRIMARY KEY/UNIQUE
-- constraint above, which don't need to be declared separately).
-- =============================================================================
