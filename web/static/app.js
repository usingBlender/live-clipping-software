/* Live Clipper web UI - no build step, no dependencies. */
"use strict";

const S = {
  state: null, ov: null, hist: {}, hypes: {}, events: [], es: null, connected: false,
  clipsTab: "pending", settingsTab: "detection", ob: { step: 0 }, logLines: [], drawQueued: new Set(),
};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const root = () => $("#root");

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (opts.body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  let data = {};
  try { data = await r.json(); } catch (e) { /* non-json */ }
  if (r.status === 401 && !path.startsWith("/api/auth")) { S.state.authed = false; render(); throw new Error("login required"); }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(html, ms = 4500) {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = html;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}
const ago = (ts) => {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const num = (v, d = 1) => (v === null || v === undefined || isNaN(v) ? "—" : Number(v).toFixed(d));

const ICON = {
  dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>',
  clips: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 9l5 3-5 3z" fill="currentColor"/></svg>',
  streamers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
  help: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.5 9a2.5 2.5 0 015 0c0 2-2.5 2-2.5 4M12 17h.01"/></svg>',
};

/* ------------------------------------------------------------------ boot & routing */
async function boot() {
  try { S.state = await api("/api/state"); } catch (e) { root().innerHTML = `<div class="boot">Can't reach the app: ${esc(e.message)}</div>`; return; }
  window.addEventListener("hashchange", () => { $$(".modal-bg,.tour-hole,.tour-tip").forEach((e) => e.remove()); render(); });
  render();
}

function route() {
  const h = location.hash.replace(/^#\/?/, "");
  const [view, ...rest] = h.split("/");
  return { view: view || "dashboard", arg: rest.join("/") };
}

function render() {
  const st = S.state;
  if (!st.has_password) return renderOnboarding(true);
  if (!st.authed) return renderLogin();
  if (!st.onboarded) return renderOnboarding(false);
  startEvents();
  renderShell();
}

/* ------------------------------------------------------------------ login */
function renderLogin() {
  stopEvents();
  root().innerHTML = `
  <div class="ob"><div class="box login">
    <div class="row" style="justify-content:center;margin-bottom:18px"><img src="/icon.svg" width="54"></div>
    <div class="card">
      <h1>Welcome back</h1><p class="muted">Enter your Live Clipper password.</p>
      <form id="lf"><div class="field"><input type="password" id="pw" autocomplete="current-password" placeholder="Password" autofocus></div>
      <button class="btn pri" style="width:100%">Log in</button><p id="err" class="small" style="color:var(--red)"></p></form>
    </div></div></div>`;
  $("#lf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/auth/login", { method: "POST", body: { password: $("#pw").value } }); S.state = await api("/api/state"); render(); }
    catch (err) { $("#err").textContent = err.message; }
  };
}

/* ------------------------------------------------------------------ onboarding */
const OB_STEPS = ["Password", "Welcome", "System check", "First streamer", "Phone & alerts", "Done"];

function renderOnboarding(needPassword) {
  stopEvents();
  if (needPassword) S.ob.step = 0; else if (S.ob.step === 0) S.ob.step = 1;
  const step = S.ob.step;
  const bars = OB_STEPS.map((_, i) => `<i class="${i <= step ? "on" : ""}"></i>`).join("");
  root().innerHTML = `<div class="ob"><div class="box">
    <div class="row" style="margin-bottom:14px"><img src="/icon.svg" width="34"><b style="font-size:18px">Live Clipper</b><span class="sp" style="flex:1"></span><span class="dim small">Step ${step + 1} of ${OB_STEPS.length}</span></div>
    <div class="steps">${bars}</div><div class="card" id="obc"></div></div></div>`;
  const c = $("#obc");
  [obPassword, obWelcome, obSystem, obStreamer, obPhone, obDone][step](c);
}
const obNext = () => { S.ob.step++; renderOnboarding(false); };
const obBack = () => { S.ob.step = Math.max(1, S.ob.step - 1); renderOnboarding(false); };
const obNav = (nextLabel = "Continue", back = true) =>
  `<div class="row" style="margin-top:18px">${back ? '<button class="btn ghost" id="obBack">Back</button>' : ""}<span style="flex:1"></span><button class="btn pri" id="obNext">${nextLabel}</button></div>`;
function bindObNav(onNext) {
  const b = $("#obBack"); if (b) b.onclick = obBack;
  $("#obNext").onclick = async () => { try { if (onNext) await onNext(); obNext(); } catch (e) { toast(esc(e.message)); } };
}

function obPassword(c) {
  c.innerHTML = `<h1>Create your password</h1>
  <p class="muted">This password protects the app when you open it from your phone. You'll use it to log in on every new device.</p>
  <div class="field"><label>Password</label><input type="password" id="p1" autocomplete="new-password" placeholder="At least 8 characters"></div>
  <div class="field"><label>Repeat password</label><input type="password" id="p2" autocomplete="new-password"></div>
  <p id="err" class="small" style="color:var(--red)"></p>
  ${obNav("Create password", false)}`;
  $("#obNext").onclick = async () => {
    const a = $("#p1").value, b = $("#p2").value;
    if (a.length < 8) return ($("#err").textContent = "Use at least 8 characters.");
    if (a !== b) return ($("#err").textContent = "The passwords don't match.");
    try { await api("/api/auth/setup", { method: "POST", body: { password: a } }); S.state = await api("/api/state"); S.ob.step = 1; renderOnboarding(false); }
    catch (e) { $("#err").textContent = e.message; }
  };
}

function obWelcome(c) {
  c.innerHTML = `<h1>Welcome to <span class="grad-text">Live Clipper</span></h1>
  <p class="muted">It watches GTA 6 livestreams for you and turns the best moments into ready-to-post shorts.</p>
  <div class="grid" style="gap:10px;margin:16px 0">
    <div class="li"><span class="pill live">1</span><span><b>Watches</b> — records each stream and reads its chat, live.</span></div>
    <div class="li"><span class="pill live">2</span><span><b>Detects hype</b> — chat floods + the streamer yelling = a moment.</span></div>
    <div class="li"><span class="pill live">3</span><span><b>Edits</b> — AI picks the cut and writes a hook; the app renders a 9:16 clip with captions.</span></div>
    <div class="li"><span class="pill live">4</span><span><b>You approve</b> — from this page or your phone, then post it.</span></div>
  </div>
  <div class="field"><label>Language of your clips (captions, titles, hooks)</label>
    <div class="choice" id="lang"><button data-v="pt">Português (BR)<small>Brazilian streamers</small></button><button data-v="en">English<small>US / international</small></button></div></div>
  ${obNav("Continue", false)}`;
  let lang = "pt";
  api("/api/settings").then((s) => { lang = s.clipper.language; mark(); });
  const mark = () => $$("#lang button").forEach((b) => b.classList.toggle("on", b.dataset.v === lang));
  $$("#lang button").forEach((b) => (b.onclick = () => { lang = b.dataset.v; mark(); }));
  mark();
  bindObNav(() => api("/api/settings/clipper", { method: "PATCH", body: { language: lang } }));
}

async function obSystem(c) {
  c.innerHTML = `<h1>System check</h1><p class="muted">Checking the tools the app needs on this computer…</p><div id="chk" class="dim">Running checks…</div>
  <div class="card" style="margin-top:14px;background:var(--panel2)"><div class="row"><div style="flex:1"><b>Claude</b><div class="small muted">Used to judge each moment and write titles. Runs on your Claude Pro subscription.</div></div><button class="btn sm" id="ct">Test Claude</button></div><div id="ctr" class="small" style="margin-top:6px"></div></div>
  ${obNav("Continue")}`;
  bindObNav();
  $("#ct").onclick = async () => { $("#ctr").textContent = "Asking Claude… (up to a minute)"; const r = await api("/api/system/claude-test", { method: "POST" }); $("#ctr").innerHTML = r.ok ? '<span style="color:var(--green)">✓ Claude answered.</span>' : `<span style="color:var(--red)">✗ ${esc(r.detail)}</span> — run <span class="code">claude</span> once in a terminal and log in.`; };
  const r = await api("/api/system/check");
  $("#chk").innerHTML = checkList(r.items) + `<p class="small dim">Red items: the app still runs, but that feature is limited. Fix them anytime — Settings → System re-runs this check.</p>`;
}
const checkList = (items) => items.map((i) => `<div class="check"><span class="st ${i.ok ? "y" : "n"}">${i.ok ? "✓" : "!"}</span><div><b>${esc(i.label)}</b><div class="small muted">${esc(i.detail)}</div>${!i.ok && i.fix ? `<div class="small" style="margin-top:3px">Fix: <span class="code">${esc(i.fix)}</span></div>` : ""}</div></div>`).join("");

function streamerForm(s = {}, isNew = true) {
  const p = s.platform || "twitch";
  return `<div class="field"><label>Platform</label><div class="choice" id="plat">
      <button data-v="twitch">Twitch<small>recommended</small></button><button data-v="kick">Kick<small>experimental chat</small></button><button data-v="demo">Demo<small>fake stream to try the app</small></button></div></div>
    <div class="field" id="chf"><label>Channel</label><input type="text" id="ch" value="${esc(s.channel || "")}" placeholder="e.g. gaules or twitch.tv/gaules" ${isNew ? "" : "disabled"}>
      <span class="hint">The name in the stream URL. ${isNew ? "" : "To change the channel, add a new streamer."}</span></div>
    <div class="field" id="kf" style="display:none"><label>Kick chatroom ID</label><input type="text" id="kid" value="${esc(s.kick_chatroom_id || "")}" placeholder="numbers">
      <span class="hint">Open kick.com/api/v2/channels/&lt;channel&gt; in your browser and copy <span class="code">chatroom.id</span>.</span></div>
    <div class="field"><label>Layout</label><div class="choice" id="lay">
      <button data-v="cam_top">Facecam on top<small>cam + gameplay stacked</small></button><button data-v="fit_blur">No facecam<small>gameplay on blurred background</small></button></div></div>
    <input type="hidden" id="platv" value="${esc(p)}"><input type="hidden" id="layv" value="${esc(s.layout || "cam_top")}">`;
}
function bindStreamerForm() {
  const sync = () => {
    const p = $("#platv").value, l = $("#layv").value;
    $$("#plat button").forEach((b) => b.classList.toggle("on", b.dataset.v === p));
    $$("#lay button").forEach((b) => b.classList.toggle("on", b.dataset.v === l));
    $("#kf").style.display = p === "kick" ? "" : "none";
    $("#chf").style.display = p === "demo" ? "none" : "";
  };
  $$("#plat button").forEach((b) => (b.onclick = () => { if (!$("#ch").disabled || b.dataset.v === $("#platv").value) { $("#platv").value = b.dataset.v; sync(); } }));
  $$("#lay button").forEach((b) => (b.onclick = () => { $("#layv").value = b.dataset.v; sync(); }));
  sync();
}
function readStreamerForm() {
  const platform = $("#platv").value;
  const body = { platform, layout: $("#layv").value, channel: $("#ch").value.trim() };
  if (platform === "demo") { body.channel = "demo"; body.name = "demo"; body.layout = "fit_blur"; }
  if (platform === "kick") body.kick_chatroom_id = $("#kid").value.trim();
  return body;
}

function obStreamer(c) {
  c.innerHTML = `<h1>Add your first streamer</h1><p class="muted">You can add, pause or remove streamers anytime — even while others are being watched. Not sure yet? Pick <b>Demo</b> to see everything working with a fake stream.</p>
  ${streamerForm()}${obNav("Add streamer")}`;
  bindStreamerForm();
  bindObNav(async () => {
    const body = readStreamerForm();
    if (body.platform !== "demo" && !body.channel) throw new Error("Type the channel name");
    await api("/api/streamers", { method: "POST", body });
  });
}

async function obPhone(c) {
  c.innerHTML = `<h1>Phone access & alerts</h1>
  <h3>Open the app on your phone</h3><div id="ts" class="small muted">Checking Tailscale…</div>
  <h3 style="margin-top:18px">Telegram alerts <span class="dim small">(optional)</span></h3>
  <p class="small muted">Get a preview on your phone when a clip is ready. Create a bot with <b>@BotFather</b>, paste its token, send the bot any message, then paste your chat ID (open <span class="code">api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</span> and copy <span class="code">chat.id</span>).</p>
  <div class="two"><div class="field"><label>Bot token</label><input type="text" id="tgt" placeholder="123456:ABC…"></div><div class="field"><label>Chat ID</label><input type="text" id="tgc" placeholder="123456789"></div></div>
  <div class="row"><button class="btn sm" id="tgtest">Save & send test</button><span id="tgr" class="small"></span></div>
  ${obNav("Continue")}`;
  bindObNav(saveTelegram);
  $("#tgtest").onclick = async () => { await saveTelegram(); const r = await api("/api/notify/test", { method: "POST" }); $("#tgr").textContent = "Telegram: " + r.telegram; };
  renderTailscale($("#ts"));
}
async function saveTelegram() {
  const t = $("#tgt").value.trim(), c = $("#tgc").value.trim();
  if (t || c) await api("/api/settings/notify", { method: "PATCH", body: { telegram_token: t, telegram_chat_id: c } });
}
async function renderTailscale(el) {
  const ts = await api("/api/system/tailscale");
  if (!ts.installed) {
    el.innerHTML = `Install <b>Tailscale</b> on this computer and on your phone (same account). It creates a private tunnel only your devices can use — nothing is opened to the internet. Then come back to Settings → Phone access.`;
    return;
  }
  if (ts.serving && ts.url) {
    el.innerHTML = `<div class="banner" style="margin:6px 0">📱 Open <b><a href="${esc(ts.url)}" target="_blank">${esc(ts.url)}</a></b> on your phone (with Tailscale on). Tip: "Add to Home Screen" makes it feel like an app.</div>${ts.public ? '<p style="color:var(--red)">Warning: Funnel is ON — this is public. Run <span class="code">tailscale funnel reset</span>.</p>' : ""}`;
    return;
  }
  el.innerHTML = `Tailscale is installed${ts.online ? "" : " but not connected"}. Turn on phone access to make this app reachable from your devices only (tailnet-only HTTPS).
    <div class="row" style="margin-top:8px"><button class="btn sm pri" id="tsgo">Enable phone access</button><span id="tsr" class="small"></span></div>`;
  $("#tsgo").onclick = async () => {
    $("#tsr").textContent = "Enabling…";
    const r = await api("/api/system/tailscale-serve", { method: "POST" });
    if (r.ok) renderTailscale(el);
    else $("#tsr").innerHTML = `Couldn't enable automatically: ${esc(r.detail)}<br>Run <span class="code">tailscale serve --bg 8787</span> in an Administrator terminal.`;
  };
}

function obDone(c) {
  c.innerHTML = `<h1>You're all set 🎉</h1>
  <p class="muted">The app is now watching. When a streamer is live, the dashboard shows the hype score in real time. When it spikes, a clip is cut, edited and sent to <b>Clips</b> for your approval.</p>
  <ul class="muted"><li>The first ~3 minutes of each stream are a warm-up (the app learns that streamer's normal chat and voice level).</li><li>Approved clips land in your <span class="code">ready</span> folder with a caption, and on Telegram if you set it up.</li></ul>
  <div class="row" style="margin-top:18px"><button class="btn ghost" id="obBack">Back</button><span style="flex:1"></span><button class="btn" id="skip">Skip tour</button><button class="btn pri" id="tour">Show me around</button></div>`;
  $("#obBack").onclick = obBack;
  const finish = async (tour) => { await api("/api/onboarding/complete", { method: "POST" }); S.state.onboarded = true; location.hash = "#/dashboard"; render(); if (tour) setTimeout(startTour, 700); };
  $("#skip").onclick = () => finish(false);
  $("#tour").onclick = () => finish(true);
}

/* ------------------------------------------------------------------ shell */
function renderShell() {
  const { view, arg } = route();
  const pending = S.ov?.stats?.pending_total || 0;
  const nav = [["dashboard", "Dashboard", ICON.dash], ["clips", "Clips", ICON.clips], ["streamers", "Streamers", ICON.streamers], ["settings", "Settings", ICON.settings], ["help", "Help", ICON.help]];
  root().innerHTML = `<div class="app">
    <aside class="side"><div class="brand"><img src="/icon.svg">Live Clipper</div>
      <nav class="nav">${nav.map(([k, l, i]) => `<a href="#/${k}" data-tour="nav-${k}" class="${view === k ? "on" : ""}">${i}<span>${l}</span>${k === "clips" && pending ? `<span class="badge" id="pendBadge">${pending}</span>` : ""}</a>`).join("")}</nav>
      <div class="side-foot"><span class="conn"><span class="dot ${S.connected ? "ok" : "warn"}" id="connDot"></span><span id="connTxt">${S.connected ? "Live updates on" : "Connecting…"}</span></span><span>v${esc(S.state.version)}</span></div>
    </aside>
    <div><div class="mob-top"><img src="/icon.svg">Live Clipper<span class="sp"></span><span class="dot ${S.connected ? "ok" : "warn"}" id="connDot2"></span></div>
    <main class="main" id="view"></main></div></div>`;
  const v = $("#view");
  ({ dashboard: viewDashboard, clips: arg ? () => viewClip(v, arg) : viewClips, streamers: viewStreamers, settings: viewSettings, help: viewHelp }[view] || viewDashboard)(v);
}

function setConn(on) {
  S.connected = on;
  ["connDot", "connDot2"].forEach((id) => { const d = $("#" + id); if (d) d.className = "dot " + (on ? "ok" : "warn"); });
  const t = $("#connTxt"); if (t) t.textContent = on ? "Live updates on" : "Reconnecting…";
}

/* ------------------------------------------------------------------ live events */
function startEvents() {
  if (S.es) return;
  const es = new EventSource("/api/events");
  S.es = es;
  es.onopen = () => { setConn(true); refreshOverview(); };
  es.onerror = () => setConn(false);
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
}
function stopEvents() { if (S.es) { S.es.close(); S.es = null; } }

let ovTimer = null;
function refreshOverview(delay = 0) {
  clearTimeout(ovTimer);
  ovTimer = setTimeout(async () => {
    try { S.ov = await api("/api/overview"); } catch (e) { return; }
    S.events = S.ov.events.slice().reverse();
    updateDashboard(true);
    const b = $("#pendBadge"), n = S.ov.stats.pending_total;
    if (b) b.textContent = n; else if (n && $('[data-tour="nav-clips"]')) $('[data-tour="nav-clips"]').insertAdjacentHTML("beforeend", `<span class="badge" id="pendBadge">${n}</span>`);
  }, delay);
}

function onEvent(ev) {
  const d = ev.data;
  if (ev.type === "metrics") {
    const arr = (S.hist[d.streamer] ||= []);
    const lastT = arr.length ? arr[arr.length - 1].t : 0;
    for (const p of d.points) if (p.t > lastT) arr.push(p);
    if (arr.length > 900) arr.splice(0, arr.length - 900);
    if (S.ov) {
      const s = S.ov.streamers.find((x) => x.name === d.streamer);
      if (s) Object.assign(s, d.status);
      S.ov.threshold = d.threshold;
    }
    queueDraw(d.streamer);
    return;
  }
  if (ev.type === "log") { S.logLines.push(d.line); if (S.logLines.length > 500) S.logLines.shift(); const pre = $("#logpre"); if (pre) { pre.textContent += d.line + "\n"; pre.scrollTop = pre.scrollHeight; } return; }
  if (ev.type === "clip.stage") { const w = $("#kpiWork"); if (w) w.textContent = stageName(d.stage); return; }
  if (ev.type === "hype") (S.hypes[d.streamer] ||= []).push(d.peak);
  S.events.unshift(ev);
  if (S.events.length > 150) S.events.pop();
  const feed = $("#feed"); if (feed) feed.innerHTML = feedHtml();
  if (ev.type === "streamer.status" && S.ov) { const s = S.ov.streamers.find((x) => x.name === d.name); if (s) Object.assign(s, d); updateDashboard(); }
  if (ev.type === "clip.rendered") toast(`🎬 <b>Clip ready</b> from ${esc(d.streamer)}<br><span class="small">${esc(d.title)}</span><br><a href="#/clips/${encodeURIComponent(d.clip_id)}">Review now →</a>`, 9000);
  if (ev.type === "clip.error") toast(`⚠️ A clip failed: ${esc(d.reason)}`);
  if (ev.type === "update.available") toast(`⬆️ Version ${esc(d.latest)} is available — Settings → Updates`, 9000);
  if (ev.type.startsWith("clip.") || ev.type.startsWith("streamer.")) refreshOverview(800);
  if (ev.type.startsWith("clip.") && route().view === "clips" && !route().arg) viewClips($("#view"));
}

const stageName = (s) => ({ transcribing: "Transcribing…", judging: "AI judging…", rendering: "Rendering…" }[s] || s);
const EVMAP = {
  hype: (d) => ["🔥", `Hype on <b>${esc(d.streamer)}</b> — score ${num(d.score)}`],
  "clip.queued": (d) => ["⏳", `Moment from <b>${esc(d.streamer)}</b> sent for editing`],
  "clip.rendered": (d) => ["🎬", `Clip ready: <b>${esc(d.title)}</b>`, `#/clips/${encodeURIComponent(d.clip_id)}`],
  "clip.skipped": (d) => ["🙅", `AI skipped a <b>${esc(d.streamer)}</b> moment: ${esc(d.reason)}`],
  "clip.approved": (d) => ["✅", `Approved: <b>${esc(d.title)}</b>`, `#/clips/${encodeURIComponent(d.clip_id)}`],
  "clip.rejected": (d) => ["✖️", `Rejected a clip`],
  "clip.error": (d) => ["⚠️", `Clip failed: ${esc(d.reason)}`],
  "streamer.status": (d) => [d.state === "live" ? "🔴" : "⚪", `<b>${esc(d.name)}</b> is ${esc(d.state)}`],
  "streamer.removed": (d) => ["🗑️", `Removed <b>${esc(d.name)}</b>`],
  "chat.suppressed": (d) => ["🛡️", `Ignored a ${esc(d.type)} flood on <b>${esc(d.streamer)}</b>`],
  "update.available": (d) => ["⬆️", `Update ${esc(d.latest)} available`, "#/settings/updates"],
  "update.installing": (d) => ["⬇️", `Installing ${esc(d.latest)}…`],
};
function feedHtml() {
  const items = S.events.filter((e, i, a) => EVMAP[e.type] && !(e.type === "streamer.status" && a.slice(i + 1).find((x) => x.type === "streamer.status" && x.data.name === e.data.name)?.data.state === e.data.state)).slice(0, 60);
  if (!items.length) return `<div class="empty small"><div class="big">📡</div>Nothing yet. Events show up here as they happen.</div>`;
  return items.map((e) => { const [ic, txt, link] = EVMAP[e.type](e.data); return `<div class="ev ${link ? "clickable" : ""}" ${link ? `onclick="location.hash='${link}'"` : ""}><span class="ic">${ic}</span><div><div>${txt}</div><div class="t">${clock(e.ts)}</div></div></div>`; }).join("");
}

/* ------------------------------------------------------------------ dashboard */
async function viewDashboard(v) {
  v.innerHTML = `<div class="head"><h1>Dashboard</h1><span class="sp"></span><a class="btn sm" href="#/streamers">+ Streamer</a></div>
    <div id="upd"></div>
    <div class="grid kpis" data-tour="kpis">
      <div class="card kpi"><div class="v" id="kLive">—</div><div class="l">Live now</div></div>
      <div class="card kpi"><div class="v" id="kPend">—</div><div class="l">Waiting for review</div></div>
      <div class="card kpi"><div class="v" id="kAppr">—</div><div class="l">Approved today</div></div>
      <div class="card kpi"><div class="v" id="kpiWork" style="font-size:18px;padding-top:6px">Idle</div><div class="l" id="kQueue">Editing pipeline</div></div>
    </div>
    <div class="cols"><div><div class="grid streams" id="streams"></div></div>
      <div class="card" data-tour="activity"><h3>Activity</h3><div class="feed" id="feed">${feedHtml()}</div></div></div>`;
  if (!S.ov) S.ov = await api("/api/overview");
  S.events = S.ov.events.slice().reverse();
  $("#feed").innerHTML = feedHtml();
  await Promise.all(S.ov.streamers.map(async (s) => { try { const h = await api(`/api/streamers/${encodeURIComponent(s.name)}/history`); S.hist[s.name] = h.points; } catch (e) { /* ignore */ } }));
  updateDashboard(true);
}

function updateDashboard(full = false) {
  const ov = S.ov;
  if (!ov || !$("#streams")) return;
  const live = ov.streamers.filter((s) => s.state === "live").length;
  $("#kLive").textContent = `${live}/${ov.streamers.length}`;
  $("#kPend").textContent = ov.stats.pending_total ?? 0;
  $("#kAppr").textContent = ov.stats.today_approved ?? 0;
  const w = ov.worker;
  $("#kpiWork").textContent = w.current ? stageName(w.current.stage) : "Idle";
  $("#kQueue").textContent = w.queued ? `${w.queued} waiting in queue` : "Editing pipeline";
  const u = ov.update || {};
  $("#upd").innerHTML = u.available ? `<div class="banner">⬆️ <b>Version ${esc(u.latest)}</b> is available (you have ${esc(u.current)}). <a class="btn sm pri" href="#/settings/updates">Update</a></div>` : "";
  const box = $("#streams");
  const names = ov.streamers.map((s) => s.name).join("|");
  if (full || box.dataset.names !== names) {
    box.dataset.names = names;
    box.innerHTML = ov.streamers.length ? ov.streamers.map((s, i) => streamCard(s, i)).join("") :
      `<div class="card empty"><div class="big">🎮</div><b>No streamers yet</b><p>Add a Twitch or Kick channel — or the Demo stream to see how it works.</p><a class="btn pri" href="#/streamers">Add a streamer</a></div>`;
    $$("[data-act]", box).forEach((b) => (b.onclick = () => streamerAction(b.dataset.name, b.dataset.act)));
  }
  ov.streamers.forEach((s) => { updateCardStats(s); queueDraw(s.name); });
}

function streamCard(s, i) {
  const id = cssId(s.name);
  const paused = s.state === "paused";
  return `<div class="card scard" id="sc-${id}" ${i === 0 ? 'data-tour="stream-card"' : ""}>
    <div class="top"><span class="dot ${s.state === "live" ? "live" : paused ? "off" : "warn"}" id="sd-${id}"></span><span class="name">${esc(s.name)}</span>
      <span class="pill ${esc(s.platform)}">${esc(s.platform)}</span><span class="pill ${esc(s.state)}" id="sp-${id}">${esc(s.state)}</span><span style="flex:1"></span>
      <button class="btn sm ghost" data-name="${esc(s.name)}" data-act="${paused ? "resume" : "pause"}">${paused ? "▶ Resume" : "❚❚ Pause"}</button></div>
    <div class="meter" title="Hype score vs threshold"><i id="sm-${id}" style="width:0"></i><b style="left:50%"></b></div>
    <canvas id="cv-${id}"></canvas>
    <div class="legend"><span><i style="background:var(--pink)"></i>hype score</span><span><i style="background:var(--text);opacity:.6"></i>threshold</span><span><i style="background:var(--teal)"></i>chat</span><span><i style="background:var(--yellow)"></i>voice</span></div>
    <div class="stats" style="margin-top:8px" id="ss-${id}"></div></div>`;
}
const cssId = (n) => n.replace(/[^A-Za-z0-9_-]/g, "_");

function updateCardStats(s) {
  const id = cssId(s.name);
  const pill = $("#sp-" + id); if (!pill) return;
  pill.className = "pill " + s.state; pill.textContent = s.state;
  $("#sd-" + id).className = "dot " + (s.state === "live" ? "live" : s.state === "paused" ? "off" : "warn");
  const thr = s.threshold || S.ov.threshold || 3.5;
  $("#sm-" + id).style.width = `${Math.max(0, Math.min(100, ((s.score || 0) / (thr * 2)) * 100))}%`;
  const parts = [];
  if (s.state === "live") parts.push(`<span>live for <b>${ago(s.live_since).replace(" ago", "")}</b></span>`);
  parts.push(`<span>chat <b>${s.chat_per_min ?? 0}</b>/min</span>`, `<span>score <b>${num(s.score)}</b></span>`, `<span>clips today <b>${s.candidates_today ?? 0}</b></span>`);
  if (s.last_candidate) parts.push(`<span>last <b>${ago(s.last_candidate)}</b></span>`);
  if (s.state === "live" && s.warming_up) parts.push(`<span style="color:var(--orange)">warming up…</span>`);
  if (s.error) parts.push(`<span style="color:var(--red)">${esc(s.error)}</span>`);
  if (s.state === "offline" || s.state === "connecting") parts.push(`<span class="dim">waiting for the stream to go live</span>`);
  $("#ss-" + id).innerHTML = parts.join("");
}

async function streamerAction(name, act) {
  await api(`/api/streamers/${encodeURIComponent(name)}/${act}`, { method: "POST" });
  toast(`${act === "pause" ? "Paused" : "Resumed"} <b>${esc(name)}</b>`);
  refreshOverview(300);
}

function queueDraw(name) {
  if (S.drawQueued.has(name)) return;
  S.drawQueued.add(name);
  requestAnimationFrame(() => { S.drawQueued.delete(name); drawChart(name); const s = S.ov?.streamers.find((x) => x.name === name); if (s) updateCardStats(s); });
}

function drawChart(name) {
  const cv = $("#cv-" + cssId(name));
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const pts = S.hist[name] || [];
  const span = 300;
  const tEnd = pts.length ? pts[pts.length - 1].t : Date.now() / 1000;
  const t0 = tEnd - span;
  const vis = pts.filter((p) => p.t >= t0);
  const x = (t) => ((t - t0) / span) * W;
  g.strokeStyle = "rgba(255,255,255,.05)"; g.lineWidth = 1;
  for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(0, (H * i) / 4); g.lineTo(W, (H * i) / 4); g.stroke(); }
  if (!vis.length) { g.fillStyle = "#6c6480"; g.font = "12px Inter,system-ui"; g.fillText("waiting for data…", 10, H / 2); return; }
  const thr = S.ov?.streamers.find((x) => x.name === name)?.threshold || S.ov?.threshold || 3.5;
  const maxS = Math.max(thr * 1.6, ...vis.map((p) => p.score));
  const minS = Math.min(-1, ...vis.map((p) => p.score));
  const y = (v) => H - 4 - ((v - minS) / (maxS - minS)) * (H - 8);
  const maxC = Math.max(4, ...vis.map((p) => p.chat));
  const bw = Math.max(1, W / span - 0.5);
  g.fillStyle = "rgba(45,226,200,.32)";
  for (const p of vis) { const h = (p.chat / maxC) * (H * 0.55); g.fillRect(x(p.t), H - h, bw, h); }
  const vv = vis.filter((p) => p.voice !== null && p.voice !== undefined);
  if (vv.length > 2) {
    const vmin = Math.min(...vv.map((p) => p.voice)), vmax = Math.max(...vv.map((p) => p.voice));
    g.strokeStyle = "rgba(255,213,74,.55)"; g.lineWidth = 1; g.beginPath();
    vv.forEach((p, i) => { const yy = H - 6 - ((p.voice - vmin) / Math.max(1, vmax - vmin)) * (H * 0.7); i ? g.lineTo(x(p.t), yy) : g.moveTo(x(p.t), yy); });
    g.stroke();
  }
  g.setLineDash([5, 4]); g.strokeStyle = "rgba(239,234,247,.55)"; g.beginPath(); g.moveTo(0, y(thr)); g.lineTo(W, y(thr)); g.stroke(); g.setLineDash([]);
  for (const pk of S.hypes[name] || []) if (pk >= t0) { g.strokeStyle = "rgba(255,61,139,.7)"; g.setLineDash([2, 3]); g.beginPath(); g.moveTo(x(pk), 0); g.lineTo(x(pk), H); g.stroke(); g.setLineDash([]); g.fillStyle = "#ff3d8b"; g.font = "11px system-ui"; g.fillText("🔥", x(pk) - 6, 12); }
  const grd = g.createLinearGradient(0, 0, W, 0); grd.addColorStop(0, "#ff3d8b"); grd.addColorStop(1, "#ffb347");
  g.strokeStyle = grd; g.lineWidth = 2; g.beginPath();
  vis.forEach((p, i) => (i ? g.lineTo(x(p.t), y(p.score)) : g.moveTo(x(p.t), y(p.score))));
  g.stroke();
}
window.addEventListener("resize", () => (S.ov?.streamers || []).forEach((s) => queueDraw(s.name)));

/* ------------------------------------------------------------------ clips */
const STATUS_TABS = [["pending", "To review"], ["approved", "Approved"], ["rejected", "Rejected"], ["skipped", "Skipped by AI"], ["error", "Errors"], ["", "All"]];
async function viewClips(v) {
  if (!v) return;
  const tab = S.clipsTab;
  v.innerHTML = `<div class="head"><h1>Clips</h1></div>
    <div class="tabs">${STATUS_TABS.map(([k, l]) => `<button class="tab ${k === tab ? "on" : ""}" data-k="${k}">${l}</button>`).join("")}</div><div id="cl" class="dim">Loading…</div>`;
  $$(".tab", v).forEach((b) => (b.onclick = () => { S.clipsTab = b.dataset.k; viewClips(v); }));
  const r = await api(`/api/clips?status=${tab}&limit=80`);
  const el = $("#cl");
  if (!el) return;
  if (!r.clips.length) { el.innerHTML = `<div class="card empty"><div class="big">🎬</div>${tab === "pending" ? "No clips waiting. New ones appear here (and on the dashboard) as soon as they're edited." : "Nothing here."}</div>`; return; }
  el.className = "grid clips";
  el.innerHTML = r.clips.map((c) => `<div class="card clip" onclick="location.hash='#/clips/${encodeURIComponent(c.id)}'">
    <div class="thumb">${c.has_video ? `<video src="/media/${encodeURIComponent(c.id)}/preview#t=1" muted playsinline preload="metadata"></video>` : `<div class="empty" style="padding-top:60%">${c.status === "skipped" ? "🙅" : c.status === "error" ? "⚠️" : "⏳"}</div>`}<span class="pill st ${c.status === "pending" ? "live" : ""}">${esc(c.status)}</span></div>
    <div class="body"><div class="title">${esc(c.title || c.reason || c.id)}</div><div class="small muted">${esc(c.streamer)} · ${ago(c.created)} · score ${num(c.score)}</div></div></div>`).join("");
}

async function viewClip(v, id) {
  v.innerHTML = `<div class="dim">Loading…</div>`;
  const c = await api(`/api/clips/${encodeURIComponent(id)}`);
  const enc = encodeURIComponent(id);
  const chat = (c.chat_sample || []).map((m) => `<div class="chatline"><span class="dim">${num(m.t, 0)}s</span> <b>${esc(m.user)}</b> ${esc(m.text)}</div>`).join("") || '<span class="dim small">no chat captured</span>';
  v.innerHTML = `<div class="head"><a class="btn sm ghost" href="#/clips">← Clips</a><span class="sp"></span><span class="pill ${c.status === "pending" ? "live" : ""}">${esc(c.status)}</span></div>
  <div class="detail">
    <div>${c.has_video ? `<video src="/media/${enc}/video" controls playsinline preload="metadata"></video>` : `<div class="card empty"><div class="big">${c.status === "skipped" ? "🙅" : "⏳"}</div>${esc(c.reason || "No video")}</div>`}</div>
    <div>
      <div class="field"><label>Title</label><input type="text" id="ti" value="${esc(c.title || "")}"></div>
      <div class="field"><label>Hashtags</label><textarea id="ht" rows="2">${esc(c.hashtags || "")}</textarea></div>
      <div class="row" style="margin-bottom:16px">
        ${c.has_video && c.status !== "approved" ? '<button class="btn ok" id="ap">✓ Approve</button>' : ""}
        ${c.has_video && c.status === "pending" ? '<button class="btn bad" id="rj">✕ Reject</button>' : ""}
        ${c.has_video ? '<button class="btn" id="cp">Copy caption</button>' : ""}
        ${c.has_video ? `<a class="btn" href="/media/${enc}/video?download=1">Download</a>` : ""}
        ${["skipped", "error", "rejected"].includes(c.status) ? '<button class="btn" id="rt">Re-run</button>' : ""}
      </div>
      <div class="card" style="margin-bottom:12px"><div class="stats">
        <span>streamer <b>${esc(c.streamer)}</b></span><span>hype <b>${num(c.score)}</b></span><span>AI confidence <b>${c.confidence != null ? Math.round(c.confidence * 100) + "%" : "—"}</b></span>
        <span>length <b>${c.duration ? c.duration + "s" : "—"}</b></span><span>judge <b>${esc(c.llm || "—")}</b></span><span>${ago(c.created)}</span></div>
        ${c.hook ? `<p style="margin:10px 0 0"><span class="dim small">HOOK</span><br><b>${esc(c.hook)}</b></p>` : ""}
        ${c.reason ? `<p style="margin:10px 0 0"><span class="dim small">WHY THE AI PICKED IT</span><br>${esc(c.reason)}</p>` : ""}</div>
      ${(c.edits || []).length ? `<div class="card" style="margin-bottom:12px"><h3>Edit</h3>${c.edits.map((e) => `<div class="chatline"><span class="dim">${num(e.t)}s</span> <b>${esc({ zoom: "Zoom", cam_focus: "Facecam takeover", text: "Callout", sfx: "Sound", shake: "Shake" }[e.type] || e.type)}</b> ${esc(e.text || e.name || (e.focus ? "on " + e.focus : ""))}${e.dur ? ` <span class="dim">(${num(e.dur)}s)</span>` : ""}</div>`).join("")}</div>` : ""}
      <div class="card"><h3>Chat at that moment</h3><div style="max-height:260px;overflow:auto">${chat}</div></div>
    </div></div>`;
  const caption = () => `${$("#ti").value}\n\n${$("#ht").value}`;
  const ap = $("#ap"); if (ap) ap.onclick = async () => { ap.disabled = true; const r = await api(`/api/clips/${enc}/approve`, { method: "POST", body: { title: $("#ti").value, hashtags: $("#ht").value } }); toast(`✅ Approved — saved to <span class="code">ready</span> (${r.size_mb} MB)`); try { await navigator.clipboard.writeText(r.caption); toast("Caption copied"); } catch (e) { /* clipboard needs https */ } nextPending(id); };
  const rj = $("#rj"); if (rj) rj.onclick = async () => { await api(`/api/clips/${enc}/reject`, { method: "POST" }); toast("Rejected"); nextPending(id); };
  const cp = $("#cp"); if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText(caption()); toast("Caption copied"); } catch (e) { prompt("Copy the caption:", caption()); } };
  const rt = $("#rt"); if (rt) rt.onclick = async () => { await api(`/api/clips/${enc}/retry`, { method: "POST" }); toast("Sent back to the editing queue"); location.hash = "#/dashboard"; };
}
async function nextPending(current) {
  const r = await api("/api/clips?status=pending&limit=5");
  const n = r.clips.find((c) => c.id !== current);
  location.hash = n ? `#/clips/${encodeURIComponent(n.id)}` : "#/clips";
}

/* ------------------------------------------------------------------ streamers */
async function viewStreamers(v) {
  v.innerHTML = `<div class="head"><h1>Streamers</h1><span class="sp"></span><button class="btn pri" id="add">+ Add streamer</button></div>
    <p class="muted small">Changes apply immediately — no restart. Paused streamers aren't recorded.</p><div id="sl" class="grid"></div>`;
  $("#add").onclick = () => streamerModal(null);
  const r = await api("/api/streamers");
  const st = Object.fromEntries(r.status.map((s) => [s.name, s]));
  $("#sl").innerHTML = r.streamers.length ? r.streamers.map((s) => {
    const x = st[s.name] || {};
    return `<div class="card"><div class="row"><span class="dot ${x.state === "live" ? "live" : x.state === "paused" ? "off" : "warn"}"></span><b style="font-size:16px">${esc(s.name)}</b>
      <span class="pill ${esc(s.platform)}">${esc(s.platform)}</span><span class="pill ${esc(x.state)}">${esc(x.state || "")}</span>
      <span class="small muted">${s.platform === "demo" ? "fake test stream" : esc(s.channel)} · ${s.layout === "cam_top" ? "facecam on top" : "no facecam"}</span><span style="flex:1"></span>
      <label class="switch" title="Watching"><input type="checkbox" data-n="${esc(s.name)}" ${s.enabled ? "checked" : ""}><span></span></label>
      <button class="btn sm" data-edit="${esc(s.name)}">Edit</button><button class="btn sm bad" data-del="${esc(s.name)}">Remove</button></div></div>`;
  }).join("") : `<div class="card empty"><div class="big">🎮</div>No streamers yet.</div>`;
  $$("[data-n]").forEach((c) => (c.onchange = async () => { await api(`/api/streamers/${encodeURIComponent(c.dataset.n)}/${c.checked ? "resume" : "pause"}`, { method: "POST" }); toast(`${c.checked ? "Watching" : "Paused"} ${esc(c.dataset.n)}`); refreshOverview(300); setTimeout(() => viewStreamers(v), 500); }));
  $$("[data-edit]").forEach((b) => (b.onclick = () => streamerModal(r.streamers.find((s) => s.name === b.dataset.edit))));
  $$("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm(`Remove ${b.dataset.del}? Recordings stay on disk until cleanup.`)) return; await api(`/api/streamers/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); refreshOverview(200); viewStreamers(v); }));
}

function modal(html) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal"><button class="btn sm ghost x">✕</button>${html}</div>`;
  document.body.appendChild(bg);
  const close = () => bg.remove();
  $(".x", bg).onclick = close;
  bg.onclick = (e) => { if (e.target === bg) close(); };
  return { el: bg, close };
}

function streamerModal(s) {
  if (!s) return addStreamerModal();
  const enc = encodeURIComponent(s.name);
  const tabs = [["src", "Source"], ["cam", "Facecam & crop"], ["cap", "Captions"], ["edit", "Editing"], ["det", "Detection"]];
  const colorField = (k, label, val) => `<div class="field"><label>${label}</label><input type="color" name="${k}" value="${esc(val)}" style="height:42px;padding:4px"></div>`;
  const m = modal(`<h2>${esc(s.name)}</h2><p class="small muted" style="margin-top:-4px">Every setting here applies only to this streamer and takes effect right away.</p>
    <div class="tabs" id="stabs">${tabs.map(([k, l], i) => `<button type="button" class="tab ${i ? "" : "on"}" data-t="${k}">${l}</button>`).join("")}</div>
    <form id="sf" onsubmit="return false">
    <div data-pane="src">${streamerForm(s, false)}
      ${textField("display_name", "Name used in titles", s.display_name, "Leave empty to use the channel name.")}
      ${selField("language", "Clip language", s.language || "", [["", "App default"], ["pt", "Português (BR)"], ["en", "English"]])}
      ${textField("quality", "Stream quality", s.quality, "First available is used, e.g. 1080p60,1080p,best")}</div>
    <div data-pane="cam" style="display:none"><p class="small muted">Drag the box over the streamer's camera (drag the corner to resize). Uses the latest recorded frame.</p>
      <div class="cam-pick" id="cp"><img id="fr" alt=""><div class="box" id="bx"><div class="h"></div></div></div>
      <div class="two" style="margin-top:12px">
        ${rangeField("cam_height", "Facecam height (share of the video)", s.cam_height, 0.25, 0.6, 0.01, "Bigger = more face, less gameplay.")}
        ${rangeField("game_center_x", "Gameplay horizontal position", s.game_center_x, 0, 1, 0.01)}
        ${rangeField("game_zoom", "Gameplay zoom (hide HUD edges)", s.game_zoom, 1, 1.6, 0.02)}
      </div>
      <div class="row"><img id="lp" class="layout-prev" alt=""><div class="small muted" style="flex:1">Preview of the vertical layout (updates after saving).</div></div></div>
    <div data-pane="cap" style="display:none">
      ${selField("captions", "Who gets captions", s.captions, [["streamer", "Only the streamer (recommended)"], ["all", "Everyone, including game dialogue"], ["off", "No captions"]], "The AI tells the streamer's voice apart from NPCs, radio and cutscenes.")}
      ${rangeField("caption_y", "Caption height on screen (0.5 = middle)", s.caption_y, 0.45, 0.8, 0.01)}
      <div class="two">${colorField("caption_color", "Caption color", s.caption_color)}${colorField("highlight_color", "Spoken-word highlight", s.highlight_color)}</div>
      ${colorField("callout_color", "Callout box color", s.callout_color)}
      ${switchField("hook", "Show the hook text in the first seconds", s.hook)}</div>
    <div data-pane="edit" style="display:none">
      ${selField("edit_intensity", "Editing intensity", s.edit_intensity, [["off", "Off — clean cut"], ["light", "Light — up to 3 effects"], ["normal", "Normal — up to 7 effects"], ["heavy", "Heavy — up to 12 effects"]], "The AI places effects exactly on the moments that matter.")}
      ${switchField("edit_zoom", "Punch-in zooms (reaction / key action)", s.edit_zoom)}
      ${switchField("edit_cam_focus", "Facecam takes over the screen on big reactions", s.edit_cam_focus)}
      ${switchField("edit_text", "Callout texts (what happened / what went wrong)", s.edit_text)}
      ${switchField("edit_sfx", "Sound effects", s.edit_sfx)}
      ${switchField("edit_shake", "Screen shake on impacts", s.edit_shake)}
      ${rangeField("sfx_volume", "Sound effect volume", s.sfx_volume, 0, 1.5, 0.05)}
      ${areaField("hashtags", "Extra hashtags for this streamer", s.hashtags)}</div>
    <div data-pane="det" style="display:none">
      ${rangeField("sensitivity", "Sensitivity (higher = more clips)", s.sensitivity, 0.5, 2, 0.05, "Multiplies the global threshold for this streamer only. Big chats often need lower sensitivity.")}
      <div class="two">${textField("cooldown_s", "Min. seconds between clips", s.cooldown_s ?? "", "Empty = global setting")}${textField("chat_offset_s", "Chat delay (s)", s.chat_offset_s ?? "", "Empty = global setting")}</div>
      ${areaField("keywords_extra", "Extra hype words for this chat", s.keywords_extra, "Emotes or slang this community spams, comma separated.")}</div>
    </form>
    <div class="row" style="margin-top:16px"><span style="flex:1"></span><button class="btn" id="cancel">Close</button><button class="btn pri" id="save">Save</button></div>`);
  bindStreamerForm();
  $$('#plat button').forEach((b) => { if (b.dataset.v !== s.platform) b.style.display = "none"; });
  $$("#stabs .tab").forEach((t) => (t.onclick = () => {
    $$("#stabs .tab").forEach((x) => x.classList.toggle("on", x === t));
    $$("[data-pane]", m.el).forEach((p) => (p.style.display = p.dataset.pane === t.dataset.t ? "" : "none"));
  }));
  $$("input[type=range]", m.el).forEach((r) => (r.oninput = () => (r.nextElementSibling.textContent = r.value)));
  $("#cancel").onclick = m.close;
  let cam = { ...s.cam };
  const fr = $("#fr"), box = $("#bx"), wrap = $("#cp");
  fr.onerror = () => { $('[data-pane="cam"] p').innerHTML = `<span style="color:var(--orange)">No frame yet — this streamer needs to be live for a few seconds.</span>`; };
  fr.src = `/api/streamers/${enc}/frame.jpg?t=${Date.now()}`;
  const lp = $("#lp"); lp.src = `/api/streamers/${enc}/layout.jpg?t=${Date.now()}`; lp.onerror = () => (lp.style.display = "none");
  const place = () => { box.style.left = cam.x * 100 + "%"; box.style.top = cam.y * 100 + "%"; box.style.width = cam.w * 100 + "%"; box.style.height = cam.h * 100 + "%"; };
  place();
  let drag = null;
  box.onpointerdown = (e) => { e.preventDefault(); box.setPointerCapture(e.pointerId); drag = { mode: e.target.classList.contains("h") ? "size" : "move", x: e.clientX, y: e.clientY, c: { ...cam } }; };
  box.onpointermove = (e) => {
    if (!drag) return;
    const r = wrap.getBoundingClientRect(), dx = (e.clientX - drag.x) / r.width, dy = (e.clientY - drag.y) / r.height;
    if (drag.mode === "move") { cam.x = Math.min(1 - cam.w, Math.max(0, drag.c.x + dx)); cam.y = Math.min(1 - cam.h, Math.max(0, drag.c.y + dy)); }
    else { cam.w = Math.min(1 - cam.x, Math.max(0.05, drag.c.w + dx)); cam.h = Math.min(1 - cam.y, Math.max(0.05, drag.c.h + dy)); }
    place();
  };
  box.onpointerup = () => (drag = null);
  $("#save").onclick = async () => {
    try {
      const body = { ...formValues($("#sf")), layout: $("#layv").value, cam: roundCam(cam) };
      if (s.platform === "kick") body.kick_chatroom_id = $("#kid").value.trim();
      const r = await api(`/api/streamers/${enc}`, { method: "PATCH", body });
      toast(r.changed.length ? `Saved: ${r.changed.length} change(s)` : "Nothing changed");
      lp.style.display = ""; lp.src = `/api/streamers/${enc}/layout.jpg?t=${Date.now()}`;
      refreshOverview(300);
      if (route().view === "streamers") viewStreamers($("#view"));
    } catch (e) { toast(esc(e.message)); }
  };
}

function addStreamerModal() {
  const m = modal(`<h2>Add a streamer</h2>${streamerForm({}, true)}
    <p class="small muted">You can fine-tune captions, editing and sensitivity for this streamer afterwards (Edit).</p>
    <div class="row" style="margin-top:16px"><span style="flex:1"></span><button class="btn" id="cancel">Cancel</button><button class="btn pri" id="save">Add & start watching</button></div>`);
  bindStreamerForm();
  $("#cancel").onclick = m.close;
  $("#save").onclick = async () => {
    try {
      const body = readStreamerForm();
      if (body.platform !== "demo" && !body.channel) throw new Error("Type the channel name");
      await api("/api/streamers", { method: "POST", body });
      toast(`Watching <b>${esc(body.name || body.channel)}</b>`); m.close();
      refreshOverview(300);
      if (route().view === "streamers") viewStreamers($("#view"));
    } catch (e) { toast(esc(e.message)); }
  };
}
const roundCam = (c) => ({ x: +c.x.toFixed(4), y: +c.y.toFixed(4), w: +c.w.toFixed(4), h: +c.h.toFixed(4) });

/* ------------------------------------------------------------------ settings */
const SET_TABS = [["detection", "Detection"], ["ai", "Clips & AI"], ["sounds", "Sounds"], ["notify", "Notifications"], ["phone", "Phone access"], ["updates", "Updates"], ["security", "Security"], ["system", "System"], ["logs", "Logs"]];
async function viewSettings(v) {
  const sub = route().arg || S.settingsTab;
  S.settingsTab = sub;
  v.innerHTML = `<div class="head"><h1>Settings</h1></div><div class="tabs">${SET_TABS.map(([k, l]) => `<a class="tab ${k === sub ? "on" : ""}" href="#/settings/${k}">${l}</a>`).join("")}</div><div id="sv" class="dim">Loading…</div>`;
  const s = await api("/api/settings");
  const el = $("#sv"); el.className = "";
  ({ detection: setDetection, ai: setAI, sounds: setSounds, notify: setNotify, phone: setPhone, updates: setUpdates, security: setSecurity, system: setSystem, logs: setLogs }[sub] || setDetection)(el, s);
}
const rangeField = (k, label, val, min, max, step, hint) => `<div class="field"><label>${label}</label><div class="range-row"><input type="range" name="${k}" min="${min}" max="${max}" step="${step}" value="${val}"><output>${val}</output></div>${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
const textField = (k, label, val, hint, type = "text") => `<div class="field"><label>${label}</label><input type="${type}" name="${k}" value="${esc(val ?? "")}">${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
const areaField = (k, label, val, hint) => `<div class="field"><label>${label}</label><textarea name="${k}" rows="3">${esc(Array.isArray(val) ? val.join(", ") : val || "")}</textarea>${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
const selField = (k, label, val, opts, hint) => `<div class="field"><label>${label}</label><select name="${k}">${opts.map(([v, l]) => `<option value="${v}" ${String(v) === String(val) ? "selected" : ""}>${l}</option>`).join("")}</select>${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
const switchField = (k, label, val, hint) => `<div class="field"><div class="row"><label class="switch"><input type="checkbox" name="${k}" ${val ? "checked" : ""}><span></span></label><b class="small">${label}</b></div>${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
function formValues(form) {
  const out = {};
  $$("[name]", form).forEach((i) => { out[i.name] = i.type === "checkbox" ? i.checked : i.type === "range" || i.type === "number" ? +i.value : i.value; });
  return out;
}
function bindForm(el, section, extra) {
  $$("input[type=range]", el).forEach((r) => (r.oninput = () => (r.nextElementSibling.textContent = r.value)));
  $("#save", el).onclick = async () => { try { await api(`/api/settings/${section}`, { method: "PATCH", body: formValues($("form", el)) }); if (extra) await extra(); toast("Saved — applied immediately"); } catch (e) { toast(esc(e.message)); } };
}

function setDetection(el, s) {
  const d = s.detector;
  el.innerHTML = `<form class="card" onsubmit="return false"><h2>How sensitive the hype detector is</h2>
    <p class="small muted">The <b>hype score</b> measures how unusual the current moment is compared with the last 10 minutes of the same stream (chat activity and the streamer's voice). Changes apply live to every streamer.</p>
    <div class="two"><div>
    ${rangeField("threshold", "Trigger threshold", d.threshold, 1.5, 8, 0.1, "Higher = fewer, bigger moments. Aim for 15–25 candidates a day.")}
    ${rangeField("cooldown_s", "Minimum gap between clips (s)", d.cooldown_s, 30, 600, 10, "Stops one long moment from producing many clips.")}
    ${rangeField("chat_offset_s", "Chat delay (s)", d.chat_offset_s, 0, 20, 1, "How many seconds chat reacts after the moment. Changing it restarts the baseline.")}
    ${rangeField("warmup_s", "Warm-up (s)", d.warmup_s, 60, 600, 10, "No clips until the app has learned the stream's normal level.")}
    </div><div>
    ${rangeField("w_chat", "Weight of chat", d.w_chat, 0, 1, 0.05)}
    ${rangeField("w_audio", "Weight of streamer's voice", d.w_audio, 0, 1, 0.05)}
    ${rangeField("combo_bonus", "Bonus when both spike together", d.combo_bonus, 0, 3, 0.1)}
    ${rangeField("pre_s", "Seconds kept before the peak", d.pre_s, 10, 60, 1, "The AI trims the final clip inside this window.")}
    ${rangeField("post_s", "Seconds kept after the peak", d.post_s, 5, 40, 1)}
    </div></div>
    ${areaField("keywords", "Hype words in chat (count extra)", d.keywords, "Comma separated, lower-case. e.g. kkkk, clipa, mds")}
    ${areaField("ignore_users", "Ignore these chat users (bots)", d.ignore_users)}
    <button class="btn pri" id="save">Save</button></form>`;
  bindForm(el, "detector");
}

function setAI(el, s) {
  const c = s.clipper;
  el.innerHTML = `<form class="card" onsubmit="return false"><h2>Clips & AI</h2>
    <div class="two"><div>
    ${selField("language", "Clip language", c.language, [["pt", "Português (BR)"], ["en", "English"]])}
    ${selField("llm", "Who judges the moments", c.llm, [["claude", "Claude (your Pro subscription)"], ["ollama", "Local model (Ollama)"], ["none", "No AI — simple cut around the peak"]])}
    ${textField("claude_model", "Claude model", c.claude_model, "sonnet (default) · opus for best judgement · haiku to save usage")}
    ${textField("ollama_model", "Ollama model (fallback)", c.ollama_model, "e.g. qwen2.5:7b-instruct — used if Claude fails. Leave empty to disable.")}
    </div><div>
    ${selField("encoder", "Video encoder", c.encoder, [["h264_nvenc", "NVIDIA GPU (NVENC) — fast"], ["libx264", "CPU (x264) — slower"]])}
    ${selField("whisper_model", "Speech-to-text model", c.whisper_model, [["large-v3-turbo", "large-v3-turbo (best)"], ["medium", "medium (lighter)"], ["small", "small (fastest)"]])}
    ${textField("font", "Caption font name", c.font, "Font file goes in the app's fonts folder.")}
    ${areaField("default_hashtags", "Default hashtags", c.default_hashtags)}
    </div></div><button class="btn pri" id="save">Save</button></form>
    <form class="card" style="margin-top:14px" onsubmit="return false" id="edf"><h2>Editing</h2>
    ${switchField("ai_sees_frames", "Let the AI look at frames from each clip", s.editing.ai_sees_frames, "Claude sees what happens on screen, so zooms, callouts and sound effects land on the right moments. Uses a bit more of your Claude usage.")}
    <p class="small muted">Intensity, zooms, callouts, sound effects and caption style are set per streamer: Streamers → Edit → Editing / Captions.</p>
    <button class="btn" id="save3">Save</button></form>
    <div class="card" style="margin-top:14px"><h2>Sound effects</h2><p class="small muted">Manage and add sounds in <a href="#/settings/sounds">Settings → Sounds</a>.</p></div>
    <form class="card" style="margin-top:14px" onsubmit="return false" id="gen"><h2>Storage</h2>
    <p class="small muted">Recordings are kept in <span class="code">${esc(s.general.data_dir)}</span>. Settings file: <span class="code">${esc(s.general.config_path)}</span></p>
    ${rangeField("retention_hours", "Keep raw recordings for (hours)", s.general.retention_hours, 2, 96, 1, "About 3.5 GB per hour per 1080p stream. Clips are kept forever.")}
    <button class="btn" id="save2">Save</button></form>`;
  bindForm(el, "clipper");
  $$("#gen input[type=range]").forEach((r) => (r.oninput = () => (r.nextElementSibling.textContent = r.value)));
  $("#save2").onclick = async () => { await api("/api/settings/general", { method: "PATCH", body: formValues($("#gen")) }); toast("Saved"); };
  $("#save3").onclick = async () => { await api("/api/settings/editing", { method: "PATCH", body: formValues($("#edf")) }); toast("Saved"); };

}

async function setSounds(el) {
  el.innerHTML = `<div class="card"><h2>Add sounds</h2>
    <p class="small muted">Upload sound files or a whole <b>.zip</b> pack (works from your phone too). Name a file after a sound in the list below — e.g. <span class="code">vine-boom.mp3</span> — and the AI knows exactly when to use it. Every sound is volume-matched automatically.</p>
    <div class="row"><input type="file" id="sfxf" multiple accept="audio/*,.zip" style="display:none"><button class="btn pri" id="sfxpick">Upload sounds…</button><span id="sfxst" class="small muted"></span></div></div>
    <div class="card" style="margin-top:14px"><h2>Most-used editing sounds</h2>
    <p class="small muted">The sounds short-form editors use most. Download the ones you like from the links (Pixabay, Mixkit and Freesound's CC0 filter allow free use in monetized videos without credit — always check the license on the sound's page), then upload them here. Very famous meme clips may come from copyrighted recordings: when in doubt, use a royalty-free version.</p>
    <div id="starter" class="dim small">Loading…</div></div>
    <div class="card" style="margin-top:14px"><h2>Your library</h2><p class="small muted">Only enabled sounds are offered to the AI. Edit the description to tell it when to use a sound.</p><div id="lib" class="dim small">Loading…</div></div>`;
  const pick = $("#sfxf");
  $("#sfxpick").onclick = () => pick.click();
  pick.onchange = async () => {
    const files = [...pick.files]; let added = 0;
    for (const [i, f] of files.entries()) {
      $("#sfxst").textContent = `Uploading ${i + 1}/${files.length}: ${f.name}…`;
      const r = await fetch(`/api/sfx/upload?filename=${encodeURIComponent(f.name)}`, { method: "POST", body: f, credentials: "same-origin", headers: { "Content-Type": "application/octet-stream" } });
      const j = await r.json().catch(() => ({}));
      if (r.ok) added += j.added.length; else toast(esc(j.error || "upload failed"));
    }
    $("#sfxst").textContent = `Added ${added} sound(s).`; pick.value = ""; load();
  };
  async function load() {
    const r = await api("/api/sfx");
    $("#starter").className = "";
    $("#starter").innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px">${r.starter.map((x) => `
      <div style="border:1px solid var(--line);border-radius:10px;padding:8px 10px">
        <div class="row" style="gap:6px"><span class="st" style="color:${x.have ? "var(--green)" : "var(--dim)"}">${x.have ? "✓" : "○"}</span><b class="mono" style="font-size:13px">${esc(x.name)}</b></div>
        <div class="small muted" style="margin:3px 0 5px">${esc(x.use)}</div>
        <div class="small"><a href="${x.pixabay}" target="_blank" rel="noopener">Search Pixabay</a> · <a href="${x.freesound}" target="_blank" rel="noopener">Search Freesound CC0</a></div></div>`).join("")}</div>
      <h3 style="margin-top:16px">Whole libraries and packs</h3>${r.sources.map((x) => `<div class="small" style="padding:3px 0"><a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.name)}</b></a> <span class="muted">— ${esc(x.note)}</span></div>`).join("")}`;
    $("#lib").className = "";
    const order = { yours: 0, "bundled (Kenney, CC0)": 1, synth: 2 };
    $("#lib").innerHTML = r.sounds.sort((a, b) => (order[a.source] ?? 3) - (order[b.source] ?? 3) || a.name.localeCompare(b.name)).map((x) => `
      <div class="row" style="padding:7px 0;border-bottom:1px solid var(--line);flex-wrap:nowrap">
        <button class="btn sm" data-play="${esc(x.name)}" title="Play">▶</button>
        <div style="flex:1;min-width:0"><div class="row" style="gap:6px"><b class="mono" style="font-size:13px">${esc(x.name)}</b><span class="pill ${x.source === "yours" ? "demo" : ""}">${esc(x.source === "synth" ? "basic" : x.source === "yours" ? "yours" : "bundled")}</span></div>
          <input type="text" data-desc="${esc(x.name)}" value="${esc(x.desc)}" style="margin-top:4px;padding:6px 8px;font-size:13px"></div>
        <label class="switch" title="Offer to the AI"><input type="checkbox" data-en="${esc(x.name)}" ${x.enabled ? "checked" : ""}><span></span></label>
        ${x.source === "yours" ? `<button class="btn sm bad" data-del="${esc(x.name)}" title="Delete">✕</button>` : ""}</div>`).join("");
    $$("[data-play]").forEach((b) => (b.onclick = () => new Audio(`/media/sfx/${encodeURIComponent(b.dataset.play)}`).play()));
    $$("[data-desc]").forEach((i) => (i.onchange = async () => { await api(`/api/sfx/${encodeURIComponent(i.dataset.desc)}`, { method: "PATCH", body: { desc: i.value } }); toast("Description saved"); }));
    $$("[data-en]").forEach((c) => (c.onchange = () => api(`/api/sfx/${encodeURIComponent(c.dataset.en)}`, { method: "PATCH", body: { enabled: c.checked } })));
    $$("#lib [data-del]").forEach((b) => (b.onclick = async () => { if (!confirm(`Delete ${b.dataset.del}?`)) return; await api(`/api/sfx/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); load(); }));
  }
  load();
}

function setNotify(el, s) {
  const n = s.notify;
  el.innerHTML = `<form class="card" onsubmit="return false"><h2>Telegram</h2>
    <p class="small muted">Create a bot with <b>@BotFather</b> and paste its token. Send your bot a message, open <span class="code">https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</span> and copy <span class="code">chat.id</span>.</p>
    <div class="two">${textField("telegram_token", "Bot token", n.telegram_token)}${textField("telegram_chat_id", "Chat ID", n.telegram_chat_id)}</div>
    ${switchField("telegram_preview", "Send a preview when a clip is ready to review", n.telegram_preview)}
    ${switchField("telegram_final", "Send the final file + caption when I approve", n.telegram_final, "Files over 50 MB are sent as a link instead.")}
    <h2 style="margin-top:10px">Webhooks (n8n, Zapier…)</h2>
    ${areaField("webhooks", "Webhook URLs", n.webhooks, "Every event (clip.rendered, clip.approved…) is POSTed as JSON. Use the n8n workflow in the app's n8n folder to auto-upload approved clips.")}
    <div class="row"><button class="btn pri" id="save">Save</button><button class="btn" id="test">Send test</button><span id="tr" class="small"></span></div></form>`;
  bindForm(el, "notify");
  $("#test").onclick = async () => { await api("/api/settings/notify", { method: "PATCH", body: formValues($("form", el)) }); const r = await api("/api/notify/test", { method: "POST" }); $("#tr").textContent = Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(" · "); };
}

async function setPhone(el, s) {
  el.innerHTML = `<div class="card"><h2>Use the app from your phone</h2><div id="ts" class="muted">Checking Tailscale…</div>
    <ol class="small muted" style="margin-top:14px"><li>Install Tailscale on this computer and on your phone, signed in with the same account.</li><li>Click <b>Enable phone access</b> (or run <span class="code">tailscale serve --bg ${s.server.port}</span>).</li><li>Open the link on your phone, log in, and use "Add to Home Screen".</li></ol>
    <p class="small muted">Only devices in your Tailscale network can open it, and the app still asks for your password. Never use <span class="code">tailscale funnel</span> — that makes it public.</p></div>
    <form class="card" style="margin-top:14px" onsubmit="return false"><h2>Link used in notifications</h2>${textField("public_url", "Public URL override", s.server.public_url, "Leave empty to detect the Tailscale address automatically.")}<button class="btn pri" id="save">Save</button></form>`;
  bindForm(el, "server");
  renderTailscale($("#ts"));
}

async function setUpdates(el, s) {
  const u = s.update;
  el.innerHTML = `<div class="card" id="ust"><h2>Version</h2><div class="dim">Checking…</div></div>
    <form class="card" style="margin-top:14px" onsubmit="return false"><h2>Update source</h2>
    ${textField("repo", "GitHub repository", u.repo, "owner/name of the repo that publishes releases, e.g. yourname/live-clipper")}
    ${switchField("auto_install", "Install updates automatically (Windows & git installs)", u.auto_install, "Waits until no clip is being edited. Linux packages update through dnf/apt.")}
    ${rangeField("check_hours", "Check every (hours)", u.check_hours, 1, 48, 1)}
    <button class="btn pri" id="save">Save</button></form>`;
  bindForm(el, "update", () => renderUpdate(true));
  renderUpdate(false);
  async function renderUpdate(force) {
    const box = $("#ust");
    const r = force ? await api("/api/update/check", { method: "POST" }) : await api("/api/update");
    box.innerHTML = `<h2>Version</h2><p>You have <b>v${esc(r.current)}</b> (${esc(s.install_kind)} install).${r.latest ? ` Latest release: <b>${esc(r.latest)}</b>.` : ""}</p>
      ${r.error ? `<p class="small" style="color:var(--orange)">${esc(r.error)}</p>` : ""}
      ${r.available ? `<div class="banner">⬆️ Update available${r.command ? `: run <span class="code">${esc(r.command)}</span>` : ""}</div>${r.notes ? `<pre class="log" style="max-height:180px">${esc(r.notes)}</pre>` : ""}` : r.checked ? `<p class="small muted">Up to date · checked ${ago(r.checked)}</p>` : ""}
      <div class="row" style="margin-top:10px"><button class="btn" id="chk">Check now</button>${r.available && !r.command ? '<button class="btn pri" id="inst">Install update</button>' : ""}</div>`;
    $("#chk").onclick = () => renderUpdate(true);
    const ib = $("#inst"); if (ib) ib.onclick = async () => { if (!confirm("Install now? The app restarts and live recordings pause for about a minute.")) return; const x = await api("/api/update/install", { method: "POST" }); toast(esc(x.detail), 10000); };
  }
}

function setSecurity(el) {
  el.innerHTML = `<form class="card" onsubmit="return false" id="pwf"><h2>Change password</h2>
    <div class="two"><div class="field"><label>Current password</label><input type="password" id="cur"></div><div class="field"><label>New password</label><input type="password" id="nw"></div></div>
    <p class="small muted">Changing it logs out every other device.</p><button class="btn pri" id="chg">Change password</button></form>
    <div class="card" style="margin-top:14px"><h2>This device</h2><button class="btn bad" id="lo">Log out</button></div>`;
  $("#chg").onclick = async () => { try { await api("/api/auth/password", { method: "POST", body: { current: $("#cur").value, new: $("#nw").value } }); toast("Password changed"); } catch (e) { toast(esc(e.message)); } };
  $("#lo").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); S.state.authed = false; render(); };
}

async function setSystem(el) {
  el.innerHTML = `<div class="card"><h2>System check</h2><div id="chk" class="dim">Running…</div>
    <div class="row" style="margin-top:10px"><button class="btn" id="re">Re-check</button><button class="btn" id="ct">Test Claude</button><span id="ctr" class="small"></span></div></div>
    <div class="card" style="margin-top:14px"><h2>App</h2><div class="row"><button class="btn" id="rs">Restart app</button><button class="btn" id="ob">Run onboarding again</button><button class="btn bad" id="quit">Quit Live Clipper</button></div>
    <p class="small muted">Quit stops recording and closes the app (also available from the tray icon). Open it again from the Start menu / app launcher.</p></div>`;
  const run = async () => { $("#chk").innerHTML = checkList((await api("/api/system/check")).items); };
  $("#re").onclick = run;
  $("#ct").onclick = async () => { $("#ctr").textContent = "Asking Claude…"; const r = await api("/api/system/claude-test", { method: "POST" }); $("#ctr").textContent = r.ok ? "✓ Claude works" : "✗ " + r.detail; };
  $("#rs").onclick = async () => { await api("/api/restart", { method: "POST" }); toast("Restarting… the page reconnects by itself", 8000); };
  $("#ob").onclick = () => { S.state.onboarded = false; S.ob.step = 1; render(); };
  $("#quit").onclick = async () => { if (!confirm("Quit Live Clipper? Recording and clip editing stop until you open it again.")) return; await api("/api/quit", { method: "POST" }); stopEvents(); root().innerHTML = `<div class="boot">Live Clipper was closed. Open it again from the Start menu / app launcher.</div>`; };
  run();
}

async function setLogs(el) {
  const r = await api("/api/logs?n=400");
  el.innerHTML = `<div class="card"><div class="row"><h2 style="flex:1">Live log</h2><span class="small dim">updates in real time</span></div><pre class="log" id="logpre"></pre></div>`;
  const pre = $("#logpre"); pre.textContent = r.lines.join("\n") + "\n"; pre.scrollTop = pre.scrollHeight;
}

/* ------------------------------------------------------------------ help + tour */
function viewHelp(v) {
  const qa = [
    ["How does it find a moment?", "Every second, two things are measured: how many different people are typing in chat (extra weight for KKKK, CLIPA, ???) and how loud the streamer's voice is (speech only, so explosions don't count). Each is compared with that stream's last 10 minutes. When the combined score crosses the threshold, the app cuts ~55 seconds around it."],
    ["What happens to a moment?", "It's transcribed, then Claude reads the transcript and chat and decides: is it worth posting? Where exactly should the clip start and end? What hook and title? If yes, the app renders a vertical 1080×1920 video with word-by-word captions and sends it to Clips."],
    ["What does the chart show?", "Pink line = hype score. Dashed line = the threshold. Teal bars = chat activity. Yellow line = the streamer's voice level. A 🔥 marks a detected moment."],
    ["Why is a streamer 'warming up'?", "For the first few minutes the app learns what's normal for that stream. Clips start after the warm-up."],
    ["Too many / too few clips?", "Settings → Detection → Trigger threshold. Raise it for fewer clips, lower it for more. If clips start too late, increase the chat delay."],
    ["Can I swap streamers anytime?", "Yes. Add, pause or remove streamers on the Streamers page — the others keep running."],
    ["How do I post a clip?", "Approve it. The file and a caption go to your ready folder (and Telegram if set up). Post it from your phone. Automatic uploads need the platforms' API approval first."],
    ["Is it safe to open from my phone?", "With Tailscale serve, only your own devices can reach it, and the app still requires your password. Never enable Tailscale Funnel."],
  ];
  v.innerHTML = `<div class="head"><h1>Help</h1></div>
    <div class="row" style="margin-bottom:14px"><button class="btn pri" id="tourBtn">▶ Take the tour</button><a class="btn" href="/docs/Live-Clipper-Guide.pdf" target="_blank">📘 Full guide (PDF)</a></div>
    <div class="grid">${qa.map(([q, a]) => `<details class="card"><summary style="cursor:pointer;font-weight:700">${q}</summary><p class="muted" style="margin:10px 0 0">${a}</p></details>`).join("")}</div>`;
  $("#tourBtn").onclick = () => { location.hash = "#/dashboard"; setTimeout(startTour, 600); };
}

const TOUR = [
  ["nav-dashboard", "Your dashboard", "Everything happening right now, updated live. It works the same on your phone."],
  ["kpis", "Quick numbers", "How many streams are live, clips waiting for you, approvals today, and what the editor is doing."],
  ["stream-card", "One card per streamer", "The pink line is the hype score; the dashed line is the threshold. When pink crosses it, a 🔥 appears and a clip is cut automatically. Pause a streamer here anytime."],
  ["activity", "Activity feed", "Every detection, edit, skip and approval appears here. Tap a finished clip to review it."],
  ["nav-clips", "Review clips", "Finished clips wait here. Watch, tweak the title, then Approve (saves the file + caption) or Reject."],
  ["nav-streamers", "Swap streamers live", "Add, pause or remove streamers without restarting. Set each one's facecam position by dragging a box."],
  ["nav-settings", "Tune it", "Detection sensitivity, AI, Telegram alerts, phone access, updates and logs."],
  ["nav-help", "Help is here", "Re-run this tour or open the full guide anytime."],
];
function startTour(i = 0) {
  $$(".tour-hole,.tour-tip").forEach((e) => e.remove());
  while (i < TOUR.length && !visibleTarget(TOUR[i][0])) i++;
  if (i >= TOUR.length) return;
  const [sel, title, text] = TOUR[i];
  const t = visibleTarget(sel);
  t.scrollIntoView({ block: "center", behavior: "smooth" });
  setTimeout(() => {
    const r = t.getBoundingClientRect(), pad = 6;
    const hole = document.createElement("div"); hole.className = "tour-hole";
    Object.assign(hole.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + pad * 2 + "px", height: r.height + pad * 2 + "px" });
    const tip = document.createElement("div"); tip.className = "tour-tip";
    tip.innerHTML = `<div class="tiny dim">${i + 1} / ${TOUR.length}</div><h3>${title}</h3><div class="small muted">${text}</div>
      <div class="row"><button class="btn sm ghost" id="tskip">Skip</button><span style="flex:1"></span>${i ? '<button class="btn sm" id="tprev">Back</button>' : ""}<button class="btn sm pri" id="tnext">${i === TOUR.length - 1 ? "Finish" : "Next"}</button></div>`;
    document.body.append(hole, tip);
    const tw = Math.min(330, window.innerWidth - 24), th = tip.offsetHeight;
    let left = Math.min(window.innerWidth - tw - 12, Math.max(12, r.left));
    let top = r.bottom + 14;
    if (top + th > window.innerHeight - 12) top = Math.max(12, r.top - th - 14);
    if (window.innerWidth > 860 && r.right < 260) { left = r.right + 18; top = Math.max(12, Math.min(r.top, window.innerHeight - th - 12)); }
    Object.assign(tip.style, { left: left + "px", top: top + "px", width: tw + "px" });
    $("#tskip").onclick = () => $$(".tour-hole,.tour-tip").forEach((e) => e.remove());
    $("#tnext").onclick = () => startTour(i + 1);
    const p = $("#tprev"); if (p) p.onclick = () => startTour(i - 1);
  }, 350);
}
function visibleTarget(name) {
  const el = $(`[data-tour="${name}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width && r.height ? el : null;
}

boot();
