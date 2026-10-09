/* =============================================================================
 * Net-monit V11.0
 * Copyright (c) 2024-2026 Abdullah | Abdullah-InfoXtek.com
 * Contact: abuabdullah.be@outlook.com
 * =============================================================================
 * auth.js — shared authentication utilities (loaded on every page via base.html)
 *
 * Session model:
 *   - session token stored in sessionStorage (cleared when browser tab closes)
 *   - role ("admin" | "user") and email cached alongside the token
 *   - roles: "admin" (full access) | "user" (read-only dashboard)
 *
 * UI elements this file controls (must exist in base.html):
 *   #login-overlay, #login-close, #li-email, #li-token, #li-submit, #li-error
 *   #session-info, #session-email, #session-role-badge, #btn-signout, #btn-signin
 *   #sidebar-session
 *   #toast
 * ========================================================================== */

const SESS_KEY  = "netmon_session";
const ROLE_KEY  = "netmon_role";
const EMAIL_KEY = "netmon_email";

// ── Session storage helpers ─────────────────────────────────────────────────
function getSession() { return sessionStorage.getItem(SESS_KEY)  || ""; }
function getRole()    { return sessionStorage.getItem(ROLE_KEY)  || ""; }
function getEmail()   { return sessionStorage.getItem(EMAIL_KEY) || ""; }

function setSession(token, role, email) {
  sessionStorage.setItem(SESS_KEY,  token || "");
  sessionStorage.setItem(ROLE_KEY,  role  || "");
  sessionStorage.setItem(EMAIL_KEY, email || "");
}

function clearSession() {
  sessionStorage.removeItem(SESS_KEY);
  sessionStorage.removeItem(ROLE_KEY);
  sessionStorage.removeItem(EMAIL_KEY);
}

function authHeaders() {
  const s = getSession();
  return s
    ? { "Content-Type": "application/json", "X-Session-Token": s }
    : { "Content-Type": "application/json" };
}

// ── Toast notifications (used across all pages) ────────────────────────────
// V10.1: this looked for document.getElementById("toast"), an element that
// doesn't exist anywhere in this app -- only #toast-container does (see
// base.html). getElementById returned null, the `if (!t) return` guard
// caught it, so this silently did nothing on every page except the
// dashboard, where dashboard.js's own separate showToast (loaded after
// this file, so its declaration won) happened to work and mask the bug.
// That duplicate is removed; this is now the one copy every page uses.
function showToast(msg, type = "info", durationMs = 4000) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  container.appendChild(t);
  setTimeout(() => t.remove(), durationMs);
}

// ── Topbar UI sync — the SINGLE source of truth for the auth UI ────────────
function _updateTopbar(role, email) {
  const sessInfo  = document.getElementById("session-info");
  const sessEmail = document.getElementById("session-email");
  const sessBadge = document.getElementById("session-role-badge");
  const btnSignin = document.getElementById("btn-signin");
  const sidebarSess = document.getElementById("sidebar-session");
  const navLicense  = document.getElementById("nav-license");

  if (role && email) {
    if (sessInfo)  sessInfo.style.display = "";
    if (sessEmail) sessEmail.textContent  = email;
    if (sessBadge) {
      sessBadge.textContent = role === "admin" ? "ADMIN" : "VIEWER";
      sessBadge.className   = `role-badge ${role}`;
    }
    if (btnSignin) btnSignin.style.display = "none";
    if (navLicense && role === "admin") navLicense.style.display = "";
    if (sidebarSess) {
      sidebarSess.innerHTML =
        `<div style="font-size:12px;color:var(--text-muted);margin-bottom:3px;">Logged in as</div>
         <div style="font-size:12px;font-weight:600;word-break:break-all;">${email}</div>
         <span class="role-badge ${role}" style="display:inline-block;margin-top:4px;">
           ${role === "admin" ? "ADMIN" : "VIEWER"}
         </span>`;
    }
  } else {
    if (sessInfo)  sessInfo.style.display  = "none";
    if (btnSignin) btnSignin.style.display = "";
    if (navLicense) navLicense.style.display = "none";
    if (sidebarSess) sidebarSess.innerHTML = "";
  }
}

// ── Server-side session validation (called once per page load) ────────────
async function checkAdminStatus() {
  const sess = getSession();
  if (!sess) { _updateTopbar(null, null); return null; }
  try {
    const r = await fetch("/api/auth/check", { headers: { "X-Session-Token": sess } });
    const d = await r.json();
    if (d.logged_in) {
      setSession(sess, d.role, d.email);
      _updateTopbar(d.role, d.email);
      return d.role;
    }
  } catch (_) { /* network error — fall through to clear */ }
  // Session invalid or server unreachable with a stale token
  clearSession();
  _updateTopbar(null, null);
  return null;
}

// ── Login modal open / close ────────────────────────────────────────────────
function openLoginModal() {
  const overlay = document.getElementById("login-overlay");
  const errEl   = document.getElementById("li-error");
  if (errEl) errEl.textContent = "";
  overlay?.classList.remove("hidden");
  setTimeout(() => document.getElementById("li-email")?.focus(), 60);
}

function closeLoginModal() {
  document.getElementById("login-overlay")?.classList.add("hidden");
}

// Backwards-compatible aliases (some inline onclick= handlers may use these)
function openAdminModal()  { openLoginModal(); }
function closeAdminModal() { closeLoginModal(); }
window.openLogin = openLoginModal;

// ── Login submit ─────────────────────────────────────────────────────────
async function doLogin() {
  const emailEl = document.getElementById("li-email");
  const tokenEl = document.getElementById("li-token");
  const errEl   = document.getElementById("li-error");
  if (!emailEl || !tokenEl) return;

  const email = emailEl.value.trim().toLowerCase();
  const token = tokenEl.value.trim();
  if (errEl) errEl.textContent = "";

  if (!email || !token) {
    if (errEl) errEl.textContent = "Email and token are required.";
    return;
  }

  try {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, token }),
    });
    const d = await r.json();

    if (d.ok) {
      setSession(d.session, d.role, d.email || email);
      tokenEl.value = "";
      closeLoginModal();
      _updateTopbar(d.role, d.email || email);
      const roleLabel = d.role === "admin" ? "Admin" : "Viewer";
      showToast(`${roleLabel} session active — access unlocked.`, "success");
      if (typeof window.onAdminLogin === "function") window.onAdminLogin(d.role);
    } else {
      if (errEl) errEl.textContent = d.error || "Invalid email or token.";
    }
  } catch (_) {
    if (errEl) errEl.textContent = "Network error — is the server running?";
  }
}

// ── V10.0: Forgot token / reset flow, OTP-based ─────────────────────────
function _ftShowView(name) {
  ["ft-request-view", "ft-otp-view", "ft-newtoken-view", "ft-result-view"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", id !== name);
  });
}

let _ftEmail = "";
let _ftVerifiedToken = "";

function openForgotTokenModal() {
  closeLoginModal();
  document.getElementById("ft-title").textContent = "Reset your token";
  _ftEmail = "";
  _ftVerifiedToken = "";
  const otpEl = document.getElementById("ft-otp");
  if (otpEl) otpEl.value = "";
  const tokEl = document.getElementById("ft-new-token-input");
  if (tokEl) tokEl.value = "";
  _ftShowView("ft-request-view");
  document.getElementById("forgot-token-overlay")?.classList.remove("hidden");
  setTimeout(() => document.getElementById("ft-email")?.focus(), 60);
}

function closeForgotTokenModal() {
  document.getElementById("forgot-token-overlay")?.classList.add("hidden");
}

async function submitForgotTokenRequest() {
  const emailEl = document.getElementById("ft-email");
  const errEl   = document.getElementById("ft-request-error");
  const email   = emailEl?.value.trim().toLowerCase();
  if (errEl) errEl.textContent = "";
  if (!email) return;
  _ftEmail = email;
  try {
    await fetch("/api/auth/forgot-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
  } catch (_) { /* network error -- still show the OTP entry view, don't leak state */ }
  document.getElementById("ft-otp-error").textContent = "";
  const otpEl = document.getElementById("ft-otp");
  if (otpEl) otpEl.value = "";
  _ftShowView("ft-otp-view");
  setTimeout(() => document.getElementById("ft-otp")?.focus(), 60);
}

async function submitVerifyOtp() {
  const otpEl = document.getElementById("ft-otp");
  const errEl = document.getElementById("ft-otp-error");
  const otp   = otpEl?.value.trim();
  if (errEl) errEl.textContent = "";
  if (!otp || otp.length !== 6) {
    if (errEl) errEl.textContent = "Enter the 6-digit code from your email.";
    return;
  }
  try {
    const r = await fetch("/api/auth/verify-reset-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: _ftEmail, otp }),
    });
    const d = await r.json();
    if (!d.ok) {
      if (errEl) errEl.textContent = d.error || "That code is incorrect or has expired.";
      return;
    }
    _ftVerifiedToken = d.verified_token;
    document.getElementById("ft-newtoken-error").textContent = "";
    document.getElementById("ft-new-token-input").value = "";
    document.getElementById("ft-title").textContent = "Choose your new token";
    _ftShowView("ft-newtoken-view");
    setTimeout(() => document.getElementById("ft-new-token-input")?.focus(), 60);
  } catch (_) {
    if (errEl) errEl.textContent = "Network error — is the server running?";
  }
}

function generateNewTokenField() {
  // Client-side convenience only -- the backend treats a generated token
  // exactly the same as one the person typed themselves (see
  // auth.complete_token_reset()'s docstring). 16 chars from a wide
  // alphabet, comparable entropy to the server-side generate_token().
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const token = Array.from(bytes, b => alphabet[b % alphabet.length]).join("");
  const el = document.getElementById("ft-new-token-input");
  if (el) { el.value = token; el.type = "text"; }
}

async function submitNewToken() {
  const tokEl = document.getElementById("ft-new-token-input");
  const errEl = document.getElementById("ft-newtoken-error");
  const newToken = tokEl?.value.trim();
  if (errEl) errEl.textContent = "";
  if (!newToken || newToken.length < 8) {
    if (errEl) errEl.textContent = "Token must be at least 8 characters.";
    return;
  }
  try {
    const r = await fetch("/api/auth/reset-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: _ftEmail, verified_token: _ftVerifiedToken, new_token: newToken }),
    });
    const d = await r.json();
    if (!d.ok) {
      if (errEl) errEl.textContent = d.error || "Could not set your new token.";
      return;
    }
    document.getElementById("ft-new-token").textContent = newToken;
    document.getElementById("ft-title").textContent = "Token reset";
    _ftShowView("ft-result-view");
  } catch (_) {
    if (errEl) errEl.textContent = "Network error — is the server running?";
  }
}

// ── Logout ───────────────────────────────────────────────────────────────
async function adminLogout() {
  const sess = getSession();
  if (sess) {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "X-Session-Token": sess },
      });
    } catch (_) { /* ignore — clearing local session regardless */ }
  }
  clearSession();
  closeLoginModal();
  // THE FIX: update the topbar UI immediately — this is the single function
  // that actually controls #session-info / #btn-signin visibility.
  _updateTopbar(null, null);
  showToast("Signed out.", "info");

  if (typeof window.onAdminLogout === "function") {
    window.onAdminLogout();
  }
}

// ── Wire up DOM once on every page ─────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  const btnSignin  = document.getElementById("btn-signin");
  const btnSignout = document.getElementById("btn-signout");
  const liClose    = document.getElementById("login-close");
  const liSubmit   = document.getElementById("li-submit");
  const overlay    = document.getElementById("login-overlay");
  const liEmail    = document.getElementById("li-email");
  const liToken    = document.getElementById("li-token");
  const liForgot   = document.getElementById("li-forgot-link");

  if (btnSignin)  btnSignin.addEventListener("click", openLoginModal);
  if (btnSignout) btnSignout.addEventListener("click", adminLogout);
  if (liClose)    liClose.addEventListener("click", closeLoginModal);
  if (liSubmit)   liSubmit.addEventListener("click", doLogin);
  if (overlay) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeLoginModal();
    });
  }
  if (liEmail) liEmail.addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("li-token")?.focus();
  });
  if (liToken) liToken.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
  if (liForgot) liForgot.addEventListener("click", (e) => {
    e.preventDefault();
    openForgotTokenModal();
  });

  // V10.0: forgot-token modal wiring (OTP flow)
  const ftOverlay         = document.getElementById("forgot-token-overlay");
  const ftClose           = document.getElementById("ft-close");
  const ftRequestSubmit   = document.getElementById("ft-request-submit");
  const ftOtpSubmit       = document.getElementById("ft-otp-submit");
  const ftOtpResend       = document.getElementById("ft-otp-resend");
  const ftGenerateToken   = document.getElementById("ft-generate-token");
  const ftNewTokenSubmit  = document.getElementById("ft-newtoken-submit");
  const ftResultLogin     = document.getElementById("ft-result-login");
  const ftCopyToken       = document.getElementById("ft-copy-token");
  const ftEmail           = document.getElementById("ft-email");
  const ftOtpInput        = document.getElementById("ft-otp");

  if (ftClose)         ftClose.addEventListener("click", closeForgotTokenModal);
  if (ftRequestSubmit) ftRequestSubmit.addEventListener("click", submitForgotTokenRequest);
  if (ftOtpSubmit)     ftOtpSubmit.addEventListener("click", submitVerifyOtp);
  if (ftOtpResend)     ftOtpResend.addEventListener("click", submitForgotTokenRequest);
  if (ftGenerateToken) ftGenerateToken.addEventListener("click", generateNewTokenField);
  if (ftNewTokenSubmit) ftNewTokenSubmit.addEventListener("click", submitNewToken);
  if (ftResultLogin) ftResultLogin.addEventListener("click", () => {
    closeForgotTokenModal();
    openLoginModal();
  });
  if (ftCopyToken) ftCopyToken.addEventListener("click", () => {
    const text = document.getElementById("ft-new-token")?.textContent || "";
    navigator.clipboard?.writeText(text).then(() => {
      ftCopyToken.textContent = "Copied!";
      setTimeout(() => { ftCopyToken.textContent = "Copy"; }, 1500);
    });
  });
  if (ftEmail) ftEmail.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitForgotTokenRequest();
  });
  if (ftOtpInput) {
    ftOtpInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitVerifyOtp();
    });
    // Digits only, and auto-submit once all 6 are in -- OTP fields are
    // faster to use when they don't require an explicit extra click.
    ftOtpInput.addEventListener("input", () => {
      ftOtpInput.value = ftOtpInput.value.replace(/\D/g, "").slice(0, 6);
      if (ftOtpInput.value.length === 6) submitVerifyOtp();
    });
  }
  if (ftOverlay) {
    ftOverlay.addEventListener("click", (e) => {
      // Don't allow click-outside to close the token-choice/result views --
      // those represent an in-progress or just-issued token the person
      // shouldn't accidentally dismiss before saving it.
      const inProgress = !document.getElementById("ft-newtoken-view").classList.contains("hidden")
                       || !document.getElementById("ft-result-view").classList.contains("hidden");
      if (e.target === ftOverlay && !inProgress) closeForgotTokenModal();
    });
  }

  // Validate any existing session against the server and sync the UI
  checkAdminStatus();
});
