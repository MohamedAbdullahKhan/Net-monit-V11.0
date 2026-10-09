/* =============================================================================
 * Net-monit V11.0
 * Copyright (c) 2024-2026 Abdullah | Abdullah-InfoXtek.com
 * =============================================================================
 * theme.js — dark/light mode + custom accent colour, shared across every page.
 *
 * Storage model:
 *   - Applied instantly from localStorage (so there's no flash-of-wrong-theme)
 *   - Synced to the server per logged-in user via /api/prefs/theme and
 *     /api/prefs/accent-color so the preference follows the user across devices
 *   - Anonymous visitors: theme/colour only persist in this browser (localStorage)
 * ========================================================================== */

const THEME_KEY    = "netmon_theme";       // "dark" | "light" | "system"
const ACCENT_KEY   = "netmon_accent";      // hex colour string, e.g. "#0078d4"
const BG_COLOR_KEY = "netmon_bg_color";    // hex colour string, or "" for theme default
const BG_IMAGE_KEY = "netmon_bg_image";    // data: URL, or "" for none

const ACCENT_PRESETS = [
  { name: "Blue",   value: "#0078d4" },
  { name: "Indigo", value: "#6366f1" },
  { name: "Teal",   value: "#0d9488" },
  { name: "Green",  value: "#16a34a" },
  { name: "Amber",  value: "#d97706" },
  { name: "Rose",   value: "#e11d48" },
  { name: "Violet", value: "#7c3aed" },
];

const BG_COLOR_PRESETS = [
  { name: "Default", value: "" },
  { name: "Slate",   value: "#0f1520" },
  { name: "Navy",    value: "#0a1128" },
  { name: "Forest",  value: "#0d1f16" },
  { name: "Plum",    value: "#1a0f28" },
  { name: "Charcoal",value: "#161616" },
];

// ── Apply theme immediately (called at top of <head>, before paint) ────────
function applyTheme(mode) {
  const resolved = mode === "system"
    ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
    : (mode || "dark");
  document.documentElement.setAttribute("data-theme", resolved);
  return resolved;
}

function applyAccent(hex) {
  if (!hex) return;
  document.documentElement.style.setProperty("--accent", hex);
  // Derive a slightly darker hover shade
  try {
    const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    const darker = "#" + [r,g,b].map(c => Math.max(0, Math.round(c*0.8)).toString(16).padStart(2,'0')).join('');
    document.documentElement.style.setProperty("--accent-dim", hex + "33");
    document.documentElement.style.setProperty("--acc-hov", darker);
  } catch (_) {}
}

function applyBgColor(hex) {
  // Empty string means "use the theme's normal default" -- clear any override.
  if (hex) document.documentElement.style.setProperty("--bg-base", hex);
  else document.documentElement.style.removeProperty("--bg-base");
}

function applyBgImage(dataUrl) {
  // Sets a CSS custom property on <html> rather than touching
  // document.body directly -- this runs from the early-init IIFE below,
  // before <body> exists yet (script lives in <head>, same as
  // applyTheme/applyAccent above), so document.body would still be null
  // at that point. The actual background-image/size/position rules live
  // in style.css referencing var(--bg-image), which works the same
  // whether this is called early or after DOMContentLoaded.
  const root = document.documentElement;
  if (dataUrl) root.style.setProperty("--bg-image", `url("${dataUrl}")`);
  else root.style.removeProperty("--bg-image");
}

// ── Apply immediately on script load (before DOMContentLoaded) to avoid flash ──
// V10.1: falls back to window.__NETMON_DEFAULTS__ (the admin-set system
// default, injected by base.html) before the hardcoded value, so an
// anonymous visitor or a first-time user actually sees what the admin
// configured instead of always getting "dark" / no accent / no bg colour.
// bg-image isn't part of this -- see syncDefaultBgImage() below.
(function initThemeEarly() {
  const defaults = window.__NETMON_DEFAULTS__ || {};
  const savedTheme   = localStorage.getItem(THEME_KEY)    || defaults.theme    || "dark";
  const savedAccent  = localStorage.getItem(ACCENT_KEY)   || defaults.accent   || "";
  const savedBgColor = localStorage.getItem(BG_COLOR_KEY) || defaults.bg_color || "";
  const savedBgImage = localStorage.getItem(BG_IMAGE_KEY) || "";
  applyTheme(savedTheme);
  if (savedAccent) applyAccent(savedAccent);
  if (savedBgColor) applyBgColor(savedBgColor);
  if (savedBgImage) applyBgImage(savedBgImage);
})();

// V10.1: the system default background image is deliberately not inlined
// into every page (see base.html) since it can be up to 4MB -- fetched
// here instead, after DOM ready, only when this browser has no bg-image
// of its own (personal or previously-applied) yet. A brief post-load
// pop-in for the default image specifically is an accepted, deliberate
// trade-off against bloating every single page load; theme/accent/bg-colour
// still apply with zero flash via initThemeEarly() above.
async function syncDefaultBgImage() {
  if (localStorage.getItem(BG_IMAGE_KEY)) return; // this browser already has one
  try {
    const r = await fetch("/api/appearance/defaults");
    const d = await r.json();
    if (d.bg_image && !localStorage.getItem(BG_IMAGE_KEY)) {
      applyBgImage(d.bg_image);
      updateBgImagePreviewUI(d.bg_image);
    }
  } catch (_) { /* no default reachable — leave as-is */ }
}

// ── Persist + sync helpers ──────────────────────────────────────────────────
async function setTheme(mode) {
  localStorage.setItem(THEME_KEY, mode);
  applyTheme(mode);
  updateThemeSwitcherUI(mode);
  const sess = (typeof getSession === "function") ? getSession() : "";
  if (sess) {
    try {
      await fetch("/api/prefs/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Token": sess },
        body: JSON.stringify({ theme: mode }),
      });
    } catch (_) { /* offline — local preference still applied */ }
  }
}

async function setAccent(hex) {
  localStorage.setItem(ACCENT_KEY, hex);
  applyAccent(hex);
  updateAccentSwatchUI(hex);
  const sess = (typeof getSession === "function") ? getSession() : "";
  if (sess) {
    try {
      await fetch("/api/prefs/accent-color", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Token": sess },
        body: JSON.stringify({ color: hex }),
      });
    } catch (_) { /* offline — local preference still applied */ }
  }
}

// V10.1: background colour now previews live but only persists on Save.
// The old version called this on every native <input type="color"> "input"
// event, which fires continuously while dragging inside the OS colour
// picker -- each tick fired its own POST, racing every other in-flight
// one with no ordering guarantee, so whichever request happened to land
// last (not necessarily the colour you actually released on) is what
// ended up saved. previewBgColor() below does the instant local apply;
// saveBgColor() is the only thing that touches localStorage or the
// network now, from the explicit Save button.
let _pendingBgColor = null;

function previewBgColor(hex) {
  _pendingBgColor = hex;
  applyBgColor(hex);
  updateBgColorSwatchUI(hex);
  _setAppearanceStatus("bg-color-status", "Not saved yet", null);
}

async function saveBgColor() {
  const hex = _pendingBgColor !== null ? _pendingBgColor : (localStorage.getItem(BG_COLOR_KEY) || "");
  localStorage.setItem(BG_COLOR_KEY, hex || "");
  applyBgColor(hex);
  updateBgColorSwatchUI(hex);
  const sess = (typeof getSession === "function") ? getSession() : "";
  if (!sess) { _setAppearanceStatus("bg-color-status", "Saved ✓", true); return; }
  _setAppearanceStatus("bg-color-status", "Saving…", null);
  try {
    const r = await fetch("/api/prefs/bg-color", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": sess },
      body: JSON.stringify({ color: hex || "" }),
    });
    _setAppearanceStatus("bg-color-status", r.ok ? "Saved ✓" : "Save failed", r.ok);
  } catch (_) { _setAppearanceStatus("bg-color-status", "Saved locally (offline)", true); }
}

function _setAppearanceStatus(elId, msg, ok) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = msg;
  el.style.color = ok === null ? "var(--text-muted)" : ok ? "var(--status-ok)" : "var(--status-critical)";
}

// V10.1: previewBgImage() applies instantly from the file the person just
// picked (no network call yet) so they can see it before committing.
// saveBgImage() is the explicit Save action that actually uploads it.
let _pendingBgImage = null;

function previewBgImage(dataUrl) {
  _pendingBgImage = dataUrl;
  applyBgImage(dataUrl);
  updateBgImagePreviewUI(dataUrl);
  _setAppearanceStatus("bg-image-status", "Not saved yet", null);
}

async function saveBgImage() {
  if (_pendingBgImage === null) { _setAppearanceStatus("bg-image-status", "Choose an image first", false); return; }
  const dataUrl = _pendingBgImage;
  localStorage.setItem(BG_IMAGE_KEY, dataUrl || "");
  applyBgImage(dataUrl);
  const sess = (typeof getSession === "function") ? getSession() : "";
  if (!sess) { _setAppearanceStatus("bg-image-status", "Saved ✓", true); return; }
  _setAppearanceStatus("bg-image-status", "Uploading…", null);
  try {
    const r = await fetch("/api/prefs/bg-image", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": sess },
      body: JSON.stringify({ image_data: dataUrl }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      _setAppearanceStatus("bg-image-status", d.error || "Upload failed", false);
      return;
    }
    _setAppearanceStatus("bg-image-status", "Saved ✓", true);
  } catch (_) { _setAppearanceStatus("bg-image-status", "Network error", false); }
}

async function clearBgImage() {
  _pendingBgImage = null;
  localStorage.removeItem(BG_IMAGE_KEY);
  applyBgImage("");
  updateBgImagePreviewUI("");
  _setAppearanceStatus("bg-image-status", "Removed", true);
  const sess = (typeof getSession === "function") ? getSession() : "";
  if (sess) {
    try {
      await fetch("/api/prefs/bg-image", { method: "DELETE", headers: { "X-Session-Token": sess } });
    } catch (_) {}
  }
}

// ── Pull the server-stored preference once logged in (overrides local guess) ──
async function syncThemeFromServer() {
  const sess = (typeof getSession === "function") ? getSession() : "";
  if (!sess) return;
  try {
    const [tRes, aRes, bcRes, biRes] = await Promise.all([
      fetch("/api/prefs/theme",        { headers: { "X-Session-Token": sess } }),
      fetch("/api/prefs/accent-color", { headers: { "X-Session-Token": sess } }),
      fetch("/api/prefs/bg-color",     { headers: { "X-Session-Token": sess } }),
      fetch("/api/prefs/bg-image",     { headers: { "X-Session-Token": sess } }),
    ]);
    const tData  = await tRes.json();
    const aData  = await aRes.json();
    const bcData = await bcRes.json();
    const biData = await biRes.json();
    if (tData.theme) {
      localStorage.setItem(THEME_KEY, tData.theme);
      applyTheme(tData.theme);
      updateThemeSwitcherUI(tData.theme);
    }
    if (aData.color) {
      localStorage.setItem(ACCENT_KEY, aData.color);
      applyAccent(aData.color);
      updateAccentSwatchUI(aData.color);
    }
    // bg-color CAN be legitimately empty (means "use theme default"), so
    // sync it whenever the endpoint responds at all, not just when truthy.
    if (bcData.color !== undefined) {
      localStorage.setItem(BG_COLOR_KEY, bcData.color || "");
      applyBgColor(bcData.color);
      updateBgColorSwatchUI(bcData.color);
    }
    if (biData.image) {
      localStorage.setItem(BG_IMAGE_KEY, biData.image);
      applyBgImage(biData.image);
      updateBgImagePreviewUI(biData.image);
    }
  } catch (_) { /* keep local preference */ }
}

// ── UI wiring: theme switcher pill (dark/light/system) ──────────────────────
function updateThemeSwitcherUI(mode) {
  document.querySelectorAll(".theme-opt").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.theme === mode);
  });
}

function updateAccentSwatchUI(hex) {
  document.querySelectorAll(".accent-swatch").forEach(sw => {
    sw.classList.toggle("active", (sw.dataset.color || "").toLowerCase() === (hex||"").toLowerCase());
  });
}

function updateBgColorSwatchUI(hex) {
  document.querySelectorAll(".bg-color-swatch").forEach(sw => {
    sw.classList.toggle("active", (sw.dataset.color || "").toLowerCase() === (hex||"").toLowerCase());
  });
}

function updateBgImagePreviewUI(dataUrl) {
  const preview = document.getElementById("bg-image-preview");
  const img     = document.getElementById("bg-image-preview-img");
  if (!preview || !img) return;
  if (dataUrl) { img.src = dataUrl; preview.style.display = "block"; }
  else preview.style.display = "none";
}

function buildThemeSwitcher(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `
    <button class="theme-opt" data-theme="light" title="Light">&#9728;</button>
    <button class="theme-opt" data-theme="dark"  title="Dark">&#9790;</button>
    <button class="theme-opt" data-theme="system" title="Match system">&#128421;</button>
  `;
  el.querySelectorAll(".theme-opt").forEach(btn => {
    btn.addEventListener("click", () => setTheme(btn.dataset.theme));
  });
  updateThemeSwitcherUI(localStorage.getItem(THEME_KEY) || "dark");
}

function buildAccentPicker(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const current = localStorage.getItem(ACCENT_KEY) || "#0078d4";
  el.innerHTML = ACCENT_PRESETS.map(p =>
    `<div class="accent-swatch ${p.value.toLowerCase()===current.toLowerCase()?'active':''}"
          style="background:${p.value};" data-color="${p.value}" title="${p.name}"></div>`
  ).join('') +
  `<input type="color" class="accent-swatch-custom" id="accent-custom-input"
          value="${current}" title="Custom colour">`;

  el.querySelectorAll(".accent-swatch").forEach(sw => {
    sw.addEventListener("click", () => setAccent(sw.dataset.color));
  });
  const customInput = document.getElementById("accent-custom-input");
  if (customInput) {
    customInput.addEventListener("input", (e) => setAccent(e.target.value));
  }
}

function buildBgColorPicker(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const current = localStorage.getItem(BG_COLOR_KEY) || "";
  // V10.1: was always "#0f1520" (near-black) regardless of theme, so
  // opening the custom picker in light mode started you on a dark colour
  // with no connection to what you're looking at. Now falls back to a
  // colour appropriate for whichever theme is currently active.
  const themeDefault = document.documentElement.getAttribute("data-theme") === "light" ? "#F5F6F8" : "#0f1520";
  el.innerHTML = BG_COLOR_PRESETS.map(p =>
    `<div class="bg-color-swatch ${p.value.toLowerCase()===current.toLowerCase()?'active':''}"
          style="background:${p.value || 'var(--bg-base)'};${p.value?'':'border-style:dashed;'}"
          data-color="${p.value}" title="${p.name}"></div>`
  ).join('') +
  `<input type="color" class="bg-color-swatch-custom" id="bg-color-custom-input"
          value="${current || themeDefault}" title="Custom colour">`;

  el.querySelectorAll(".bg-color-swatch").forEach(sw => {
    sw.addEventListener("click", () => previewBgColor(sw.dataset.color));
  });
  const customInput = document.getElementById("bg-color-custom-input");
  if (customInput) {
    customInput.addEventListener("input", (e) => previewBgColor(e.target.value));
  }
  const saveBtn = document.getElementById("bg-color-save-btn");
  if (saveBtn) saveBtn.addEventListener("click", saveBgColor);
}

function wireBgImageUploader(fileInputId, statusId) {
  const fileInput = document.getElementById(fileInputId);
  if (!fileInput) return;
  updateBgImagePreviewUI(localStorage.getItem(BG_IMAGE_KEY) || "");
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    const statusEl = statusId ? document.getElementById(statusId) : null;
    if (file.size > 4 * 1024 * 1024) {
      if (statusEl) { statusEl.textContent = "Image must be under 4MB"; statusEl.style.color = "var(--status-critical)"; }
      fileInput.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => previewBgImage(reader.result);
    reader.readAsDataURL(file);
  });
  const saveBtn = document.getElementById("bg-image-save-btn");
  if (saveBtn) saveBtn.addEventListener("click", saveBgImage);
}

// ── Wire up on every page ───────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  buildThemeSwitcher("theme-switcher-topbar");
  buildThemeSwitcher("theme-switcher-settings");
  buildAccentPicker("accent-picker-settings");
  buildBgColorPicker("bg-color-picker-settings");
  wireBgImageUploader("bg-image-file", "bg-image-status");
  // V10.1: unlike syncThemeFromServer below, this runs for every visitor,
  // logged in or not -- the whole point of a system default is that it
  // doesn't require a session.
  syncDefaultBgImage();
  // Once auth.js has validated the session, pull any server-saved preference
  setTimeout(syncThemeFromServer, 300);
});
