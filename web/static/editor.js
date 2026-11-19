/* Live Clipper - built-in clip editor (timeline, trims, splits, effects, texts, captions).
   All times in the editor are seconds in the source recording; the server converts them back. */
"use strict";

const ED_TYPES = {
  text: { label: "Text", track: "text", color: "#e8173f", dur: 2 },
  zoom: { label: "Zoom", track: "fx", color: "#7c5cff", dur: 1.2 },
  cam_focus: { label: "Facecam", track: "fx", color: "#2de2c8", dur: 2.5 },
  shake: { label: "Shake", track: "fx", color: "#ffb347", dur: 0.4 },
  sfx: { label: "Sound", track: "sfx", color: "#ffd54a", dur: 0 },
};
const ED_TRACKS = [["video", "Video"], ["cap", "Captions"], ["text", "Text"], ["fx", "Effects"], ["sfx", "Sounds"]];

async function viewEditor(v, id) {
  stopEditor();
  v.classList.add("wide");
  v.innerHTML = `<div class="dim">Opening the editor…</div>`;
  let m;
  for (;;) {
    try { m = await api(`/api/clips/${encodeURIComponent(id)}/editor`); }
    catch (e) { v.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
    if (m.ready) break;
    if (route().view !== "clips" || !route().arg.endsWith("/edit")) return;
    if (m.job.state === "error") {
      v.innerHTML = `<div class="card empty"><div class="big">⚠️</div>${esc(m.job.msg)}<br><button class="btn" id="rt" style="margin-top:12px">Try again</button></div>`;
      $("#rt").onclick = async () => { await api(`/api/clips/${encodeURIComponent(id)}/editor?retry=1`).catch(() => {}); viewEditor(v, id); };
      return;
    }
    v.innerHTML = `<div class="card empty"><div class="big">🎬</div>Preparing the editor… <span class="muted">${esc(m.job.msg || "")}</span></div>`;
    await new Promise((r) => setTimeout(r, 1500));
  }
  editorUI(v, m);
}

let ED = null;
function stopEditor() {
  if (!ED) return;
  cancelAnimationFrame(ED.raf);
  ED.video.pause();
  document.removeEventListener("keydown", ED.onKey);
  window.removeEventListener("beforeunload", ED.onLeave);
  ED = null;
}

function editorUI(v, m) {
  const g = m.geometry, st = m.style;
  const P = m.plan;
  let uid = 1;
  const E = (ED = {
    m, st: {
      start: P.start, end: P.end, cuts: P.cuts.map((c) => [...c]), splits: [...new Set(P.cuts.flat())], hook_text: P.hook_text,
      captions_on: P.captions_on, words: P.words.map((w) => ({ ...w })),
      edits: P.edits.map((e) => ({ ...e, _id: uid++ })),
    },
    sel: null, hist: [], fut: [], pps: 40, dirty: false, raf: 0, lastT: 0, audio: {},
  });
  const fmt = (t) => { t = Math.max(0, t); return `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`; };
  const clamp = (x, a, b) => Math.min(Math.max(x, a), b);
  const D = m.duration || 60;

  v.innerHTML = `<div class="ed">
    <div class="head ed-head"><a class="btn sm ghost" href="#/clips/${encodeURIComponent(m.id)}">← Back</a>
      <b class="ed-title">${esc(m.title || m.id)}</b><span class="sp"></span>
      <button class="btn sm" id="eUndo" title="Undo (Ctrl+Z)">↶</button><button class="btn sm" id="eRedo" title="Redo (Ctrl+Shift+Z)">↷</button>
      ${m.ai ? '<button class="btn sm" id="eReset" title="Go back to what the AI made">Reset to AI edit</button>' : ""}
      <button class="btn pri" id="eRender">Render</button></div>
    <div class="ed-main">
      <div class="ed-stage"><canvas id="eCv" width="540" height="960"></canvas>
        <div class="ed-transport"><button class="btn sm" id="ePlay">▶</button><span class="mono small" id="eTime"></span><span class="sp"></span>
          <button class="btn sm" id="eSplit" title="Split the video at the playhead (S)">✂ Split</button></div></div>
      <div class="ed-side card" id="eInsp"></div>
    </div>
    <div class="ed-add row"><span class="small muted">Add at playhead:</span>
      <button class="btn sm" data-add="text">+ Text</button><button class="btn sm" data-add="zoom">+ Zoom</button>
      ${g.layout === "cam_top" ? '<button class="btn sm" data-add="cam_focus">+ Facecam</button>' : ""}
      <button class="btn sm" data-add="sfx" ${m.sfx.length ? "" : 'disabled title="Install sounds in Settings → Sounds"'}>+ Sound</button>
      <button class="btn sm" data-add="shake">+ Shake</button><span class="sp"></span>
      <span class="small muted">Zoom</span><input type="range" id="eZoom" min="8" max="200" value="40" style="width:120px"></div>
    <div class="ed-tl" id="eTl"><div class="ed-inner" id="eIn"></div></div>
    <p class="small muted">Drag blocks to move them, drag their right edge to change length, drag the ends of the video to trim. Split the video and delete a part to cut it out. Space = play, S = split, Delete = remove.</p>
  </div>`;

  const video = (E.video = document.createElement("video"));
  video.src = m.proxy; video.preload = "auto"; video.playsInline = true;
  const cv = $("#eCv"), ctx = cv.getContext("2d");
  const K = cv.width / g.W;                 // output px -> canvas px
  const work = document.createElement("canvas"); work.width = cv.width; work.height = cv.height;
  const wctx = work.getContext("2d");

  function draw() { drawFrame(); }
  // ------------------------------------------------------------ state helpers
  const snap = () => JSON.stringify(E.st);
  const commit = (before) => { if (before !== snap()) { E.hist.push(before); E.fut = []; E.dirty = true; } draw(); };
  const change = (fn) => { const b = snap(); fn(); commit(b); };
  const undo = () => { if (!E.hist.length) return; E.fut.push(snap()); E.st = JSON.parse(E.hist.pop()); E.sel = null; draw(); };
  const redo = () => { if (!E.fut.length) return; E.hist.push(snap()); E.st = JSON.parse(E.fut.pop()); E.sel = null; draw(); };
  const inCut = (t) => E.st.cuts.find(([a, b]) => t >= a && t < b);
  const outLen = () => (E.st.end - E.st.start) - E.st.cuts.reduce((s, [a, b]) => s + Math.max(0, Math.min(b, E.st.end) - Math.max(a, E.st.start)), 0);
  const outTime = (t) => (t - E.st.start) - E.st.cuts.reduce((s, [a, b]) => s + Math.max(0, Math.min(b, t) - a), 0);
  const bounds = () => [E.st.start, ...E.st.splits.filter((x) => x > E.st.start && x < E.st.end).sort((a, b) => a - b), E.st.end];
  const segments = () => { const b = bounds(); return b.slice(1).map((z, i) => ({ a: b[i], b: z, cut: E.st.cuts.some(([x, y]) => x <= b[i] + 0.01 && y >= z - 0.01) })); };
  const groups = () => {               // same grouping as the renderer: max 3 words / 16 chars, break on pauses
    const out = []; let cur = [];
    E.st.words.forEach((w, i) => {
      if (!w.w) return;
      if (cur.length && (cur.length >= 3 || cur.reduce((s, x) => s + E.st.words[x].w.length, 0) + w.w.length > 16 || w.s - E.st.words[cur[cur.length - 1]].e > 0.5)) { out.push(cur); cur = []; }
      cur.push(i);
    });
    if (cur.length) out.push(cur);
    return out;
  };
  const byId = (i) => E.st.edits.find((e) => e._id === i);
  const seek = (t) => { video.currentTime = clamp(t, 0, D - 0.04); E.lastT = video.currentTime; draw(); };

  // ------------------------------------------------------------ preview drawing (mirrors the renderer)
  function coverRect(sx, sy, sw, sh, dw, dh) {          // centered crop of src to dest aspect (force_original_aspect_ratio=increase + crop)
    const a = sw / sh, A = dw / dh;
    return a > A ? [sx + (sw - sh * A) / 2, sy, sh * A, sh] : [sx, sy + (sh - sw / A) / 2, sw, sw / A];
  }
  function drawBase(c, t) {
    const vw = video.videoWidth || 1, sx = vw / g.sw;
    const W = cv.width, H = cv.height;
    c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
    if (!video.videoWidth) return;
    if (g.layout === "cam_top") {
      const chO = g.cam_h * K, [cx, cy, cw, ch] = g.cam.map((x) => x * sx), [gx, gy, gw, gh] = g.game.map((x) => x * sx);
      c.drawImage(video, ...coverRect(cx, cy, cw, ch, W, chO), 0, 0, W, chO);
      c.drawImage(video, gx, gy, gw, gh, 0, chO, W, H - chO);
      if (E.st.edits.some((e) => e.type === "cam_focus" && t >= e.t && t < e.t + e.dur)) c.drawImage(video, ...coverRect(cx, cy, cw, ch, W, H), 0, 0, W, H);
    } else {
      const [cw, ch] = g.crop.map((x) => x * sx), x0 = (video.videoWidth - cw) / 2, y0 = (video.videoHeight - ch) / 2;
      c.filter = "blur(12px)"; c.drawImage(video, ...coverRect(x0, y0, cw, ch, W, H), 0, 0, W, H); c.filter = "none";
      const fh = W * ch / cw; c.drawImage(video, x0, y0, cw, ch, 0, (H - fh) / 2, W, fh);
    }
  }
  function boxText(c, text, x, y, size, fg, bg, rot) {
    c.save(); c.translate(x, y); if (rot) c.rotate(rot);
    c.font = `800 ${size}px "${st.font}", Inter, Arial, sans-serif`; c.textAlign = "center"; c.textBaseline = "middle";
    const lines = wrap(c, text, 900 * K); const lh = size * 1.15, pad = size * 0.35;
    const w = Math.max(...lines.map((l) => c.measureText(l).width)) + pad * 2, h = lines.length * lh + pad;
    c.fillStyle = bg; c.fillRect(-w / 2, -h / 2, w, h);
    c.fillStyle = fg; lines.forEach((l, i) => c.fillText(l, 0, -h / 2 + pad / 2 + lh * (i + 0.5)));
    c.restore();
  }
  function wrap(c, text, maxW) {
    const out = []; let line = "";
    for (const w of String(text).split(/\s+/)) { const t = line ? line + " " + w : w; if (c.measureText(t).width > maxW && line) { out.push(line); line = w; } else line = t; }
    return out.concat(line ? [line] : []);
  }
  function drawOverlays(c, t) {
    const ct = t - E.st.start;
    if (st.hook !== false && E.st.hook_text && ct >= 0 && ct < 2.6) boxText(c, E.st.hook_text.toUpperCase(), cv.width / 2, g.text.hook * K, 70 * K, "#000", "#fff");
    for (const e of E.st.edits) if (e.type === "text" && t >= e.t && t < e.t + e.dur) boxText(c, e.text.toUpperCase(), cv.width / 2, g.text.callout * K, 64 * K, "#fff", st.callout_color || "#E8173F", -3 * Math.PI / 180);
    if (!E.st.captions_on) return;
    const gs = groups(), wi = E.st.words.findIndex((w) => t >= w.s && t < w.e + 0.3);
    const gr = gs.find((x) => x.includes(wi)) || gs.find((x) => t >= E.st.words[x[0]].s && t < E.st.words[x[x.length - 1]].e + 0.3);
    if (!gr) return;
    const size = 88 * K; c.font = `800 ${size}px "${st.font}", Inter, Arial, sans-serif`; c.textBaseline = "middle"; c.textAlign = "left";
    const parts = gr.map((i) => E.st.words[i].w.toUpperCase().replace(/[,.;:]+$/, ""));
    const widths = parts.map((p) => c.measureText(p).width), space = c.measureText(" ").width;
    let x = cv.width / 2 - (widths.reduce((a, b) => a + b, 0) + space * (parts.length - 1)) / 2;
    const y = g.text.caption * K;
    c.lineWidth = 7 * K * 2; c.strokeStyle = "#000"; c.lineJoin = "round";
    parts.forEach((p, k) => {
      const cur = gr[k] === wi || (t >= E.st.words[gr[k]].s && t < E.st.words[gr[k]].e);
      c.strokeText(p, x, y); c.fillStyle = cur ? st.highlight_color || "#FFE500" : st.caption_color || "#fff"; c.fillText(p, x, y);
      x += widths[k] + space;
    });
  }
  function drawFrame() {
    const t = video.currentTime;
    drawBase(wctx, t);
    // zooms: punch into the laid-out frame around the focus point
    for (const e of E.st.edits) {
      if (e.type !== "zoom" || t < e.t || t >= e.t + e.dur) continue;
      const z = clamp(+e.amount || 1.3, 1.05, 2), [fx, fy] = (g.focus[e.focus] || g.focus.center).map((q) => q * K);
      const cw = cv.width / z, ch = cv.height / z, x = clamp(fx - cw / 2, 0, cv.width - cw), y = clamp(fy - ch / 2, 0, cv.height - ch);
      ctx.drawImage(work, x, y, cw, ch, 0, 0, cv.width, cv.height); wctx.drawImage(cv, 0, 0);
    }
    ctx.save();
    if (E.st.edits.some((e) => e.type === "shake" && t >= e.t && t < e.t + e.dur)) {
      ctx.translate(22 * K * Math.sin(t * 61), 18 * K * Math.cos(t * 53)); ctx.scale(1.06, 1.06); ctx.translate(-cv.width * 0.03, -cv.height * 0.03);
    }
    ctx.drawImage(work, 0, 0); ctx.restore();
    if (t < E.st.start || t >= E.st.end || inCut(t)) { ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(0, 0, cv.width, cv.height); ctx.fillStyle = "#fff"; ctx.font = `600 ${15}px Inter, sans-serif`; ctx.textAlign = "center"; ctx.fillText("not in the clip", cv.width / 2, 24); }
    drawOverlays(ctx, t);
    $("#eTime").textContent = `${fmt(Math.max(0, outTime(clamp(t, E.st.start, E.st.end))))} / ${fmt(outLen())}`;
    const ph = $("#ePh"); if (ph) ph.style.left = `${t * E.pps}px`;
  }
  function tick() {
    if (!ED || ED !== E) return;
    let t = video.currentTime;
    if (!video.paused) {
      if (t < E.st.start - 0.05) { video.currentTime = t = E.st.start; }
      const c = inCut(t); if (c) { video.currentTime = t = c[1]; }
      if (t >= E.st.end) { video.pause(); video.currentTime = t = E.st.end - 0.02; $("#ePlay").textContent = "▶"; }
      for (const e of E.st.edits) if (e.type === "sfx" && e.t > E.lastT && e.t <= t && t - E.lastT < 0.5) playSfx(e.name, e.volume);
    }
    E.lastT = t;
    drawFrame();
    E.raf = requestAnimationFrame(tick);
  }
  function playSfx(name, vol = 1) {
    try { const a = new Audio(`/media/sfx/${encodeURIComponent(name)}`); a.volume = clamp((st.sfx_volume ?? 0.8) * vol, 0, 1); a.play().catch(() => {}); } catch (e) { /* ignore */ }
  }
  const play = () => {
    if (video.paused) { if (video.currentTime >= E.st.end - 0.05 || video.currentTime < E.st.start) video.currentTime = E.st.start; video.play().catch(() => {}); $("#ePlay").textContent = "❚❚"; }
    else { video.pause(); $("#ePlay").textContent = "▶"; }
  };

  // ------------------------------------------------------------ timeline
  function blockHTML(e) {
    const T = ED_TYPES[e.type], sel = E.sel && E.sel.k === "e" && E.sel.id === e._id ? " sel" : "";
    const out = e.t < E.st.start || e.t >= E.st.end ? " out" : "";
    const label = e.type === "text" ? e.text : e.type === "sfx" ? e.name : e.type === "zoom" ? `Zoom ${e.focus || ""}` : T.label;
    if (e.type === "sfx") return `<div class="ed-b ed-pt${sel}${out}" data-e="${e._id}" style="left:${e.t * E.pps - 7}px;background:${T.color}" title="${esc(label)}"><span>${esc(label)}</span></div>`;
    return `<div class="ed-b${sel}${out}" data-e="${e._id}" style="left:${e.t * E.pps}px;width:${Math.max(6, e.dur * E.pps)}px;background:${T.color}">
      <span>${esc(label)}</span><i class="ed-h" data-r="${e._id}"></i></div>`;
  }
  function drawTimeline() {
    const W = D * E.pps, step = E.pps > 80 ? 1 : E.pps > 25 ? 5 : E.pps > 12 ? 10 : 30;
    let ruler = "";
    for (let s = 0; s <= D; s += step) ruler += `<span style="left:${s * E.pps}px">${fmt(s).replace(/\.\d$/, "")}</span>`;
    const segs = segments().map((s, i) => `<div class="ed-seg${s.cut ? " cut" : ""}${E.sel && E.sel.k === "s" && E.sel.i === i ? " sel" : ""}" data-s="${i}" style="left:${s.a * E.pps}px;width:${(s.b - s.a) * E.pps}px">${s.cut ? "cut" : ""}</div>`).join("");
    const caps = E.st.captions_on ? groups().map((gr, i) => { const a = E.st.words[gr[0]].s, b = E.st.words[gr[gr.length - 1]].e; return `<div class="ed-b cap${E.sel && E.sel.k === "c" && E.sel.i === i ? " sel" : ""}" data-c="${i}" style="left:${a * E.pps}px;width:${Math.max(6, (b - a) * E.pps)}px"><span>${esc(gr.map((x) => E.st.words[x].w).join(" "))}</span></div>`; }).join("") : `<span class="ed-off">captions off</span>`;
    const hook = E.st.hook_text && st.hook !== false ? `<div class="ed-b hook${E.sel && E.sel.k === "h" ? " sel" : ""}" data-hook="1" style="left:${E.st.start * E.pps}px;width:${2.6 * E.pps}px"><span>${esc(E.st.hook_text)}</span></div>` : "";
    const rows = { video: `<div class="ed-shade" style="left:0;width:${E.st.start * E.pps}px"></div><div class="ed-shade" style="left:${E.st.end * E.pps}px;right:0"></div>${segs}<i class="ed-trim l" data-trim="start" style="left:${E.st.start * E.pps - 7}px"></i><i class="ed-trim r" data-trim="end" style="left:${E.st.end * E.pps - 7}px"></i>`,
      cap: caps, text: hook, fx: "", sfx: "" };
    for (const e of E.st.edits) rows[ED_TYPES[e.type].track] += blockHTML(e);
    $("#eIn").style.width = `${W + 40}px`;
    $("#eIn").innerHTML = `<div class="ed-ruler" id="eRuler">${ruler}</div>` +
      ED_TRACKS.map(([k, l]) => `<div class="ed-track" data-track="${k}"><b class="ed-tn">${l}</b>${rows[k]}</div>`).join("") +
      (m.peak != null ? `<i class="ed-peak" style="left:${m.peak * E.pps}px" title="Detected hype peak"></i>` : "") +
      `<i class="ed-ph" id="ePh" style="left:${video.currentTime * E.pps}px"></i>`;
    bindTimeline();
  }
  function timeAt(ev) { const r = $("#eIn").getBoundingClientRect(); return clamp((ev.clientX - r.left) / E.pps, 0, D); }
  function dragger(ev, onMove, onEnd) {
    ev.preventDefault(); ev.stopPropagation();
    const before = snap(), x0 = ev.clientX; let moved = false;
    const mv = (e) => { if (Math.abs(e.clientX - x0) > 2) moved = true; if (moved) onMove((e.clientX - x0) / E.pps, e); };
    const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); if (onEnd) onEnd(moved); commit(before); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  function bindTimeline() {
    const sc = $("#eIn");
    sc.onpointerdown = (ev) => { if (ev.target === sc || ev.target.closest(".ed-ruler") || ev.target.classList.contains("ed-track") || ev.target.classList.contains("ed-shade")) { E.sel = null; seek(timeAt(ev)); drawInspector(); dragger(ev, (_d, e) => seek(timeAt(e))); } };
    $$("[data-e]", sc).forEach((b) => (b.onpointerdown = (ev) => {
      if (ev.target.dataset.r) return;
      const e = byId(+b.dataset.e), t0 = e.t; E.sel = { k: "e", id: e._id }; drawInspector(); drawTimeline();
      dragger(ev, (d) => { e.t = +clamp(t0 + d, 0, D - (e.dur || 0)).toFixed(2); drawTimeline(); seek(e.t); }, (mv) => { if (!mv) seek(e.t); drawInspector(); });
    }));
    $$("[data-r]", sc).forEach((h) => (h.onpointerdown = (ev) => {
      const e = byId(+h.dataset.r), d0 = e.dur; E.sel = { k: "e", id: e._id };
      dragger(ev, (d) => { e.dur = +clamp(d0 + d, 0.1, D - e.t).toFixed(2); drawTimeline(); }, () => drawInspector());
    }));
    $$("[data-trim]", sc).forEach((h) => (h.onpointerdown = (ev) => {
      const k = h.dataset.trim, v0 = E.st[k];
      dragger(ev, (d) => {
        E.st[k] = +(k === "start" ? clamp(v0 + d, 0, E.st.end - 1) : clamp(v0 + d, E.st.start + 1, D)).toFixed(2);
        seek(E.st[k] - (k === "end" ? 0.05 : 0)); drawTimeline();
      }, () => drawInspector());
    }));
    $$("[data-s]", sc).forEach((b) => (b.onpointerdown = (ev) => { E.sel = { k: "s", i: +b.dataset.s }; seek(timeAt(ev)); drawTimeline(); drawInspector(); }));
    $$("[data-c]", sc).forEach((b) => (b.onpointerdown = (ev) => { E.sel = { k: "c", i: +b.dataset.c }; seek(E.st.words[groups()[+b.dataset.c][0]].s); drawTimeline(); drawInspector(); }));
    $$("[data-hook]", sc).forEach((b) => (b.onpointerdown = () => { E.sel = { k: "h" }; seek(E.st.start); drawTimeline(); drawInspector(); }));
  }

  // ------------------------------------------------------------ inspector
  function field(label, html) { return `<div class="field"><label>${label}</label>${html}</div>`; }
  function drawInspector() {
    const el = $("#eInsp"), s = E.sel;
    if (!s) {
      el.innerHTML = `<h3>Clip</h3>
        ${field("Hook text (first 2.6 s)", `<input type="text" id="iHook" value="${esc(E.st.hook_text)}" maxlength="60" placeholder="no hook">`)}
        <label class="row" style="gap:10px;margin:4px 0 12px"><span class="switch"><input type="checkbox" id="iCap" ${E.st.captions_on ? "checked" : ""}><span></span></span> Captions</label>
        <div class="stats small"><span>length <b>${fmt(outLen())}</b></span><span>effects <b>${E.st.edits.length}</b></span><span>cuts <b>${E.st.cuts.length}</b></span></div>
        <p class="small muted" style="margin-top:12px">Select anything on the timeline to change or delete it.${m.edited ? " This clip was already edited by you." : ""}</p>
        ${E.st.edits.some((e) => !e.user) ? '<button class="btn sm bad" id="iNoAi">Remove all AI effects</button>' : ""}`;
      $("#iHook").onchange = (ev) => change(() => (E.st.hook_text = ev.target.value.trim()));
      $("#iCap").onchange = (ev) => change(() => (E.st.captions_on = ev.target.checked));
      const na = $("#iNoAi"); if (na) na.onclick = () => change(() => { E.st.edits = E.st.edits.filter((e) => e.user); drawTimeline(); drawInspector(); });
      return;
    }
    if (s.k === "h") {
      el.innerHTML = `<h3>Hook</h3>${field("Text", `<input type="text" id="iHook" value="${esc(E.st.hook_text)}" maxlength="60">`)}<button class="btn sm bad" id="iDel">Remove hook</button>`;
      $("#iHook").oninput = (ev) => { E.st.hook_text = ev.target.value; drawTimeline(); };
      $("#iHook").onfocus = () => (E.fb = snap()); $("#iHook").onchange = () => commit(E.fb);
      $("#iDel").onclick = () => { change(() => (E.st.hook_text = "")); E.sel = null; drawTimeline(); drawInspector(); };
      return;
    }
    if (s.k === "s") {
      const sg = segments()[s.i]; if (!sg) { E.sel = null; return drawInspector(); }
      el.innerHTML = `<h3>Video part</h3><p class="small muted">${fmt(sg.a)} → ${fmt(sg.b)} (${(sg.b - sg.a).toFixed(1)} s)</p>
        ${sg.cut ? '<button class="btn" id="iRestore">Restore this part</button>' : '<button class="btn bad" id="iCut">Delete this part</button>'}
        <p class="small muted" style="margin-top:10px">Use ✂ Split (or S) at the playhead to make parts.</p>
        ${E.st.splits.length ? '<button class="btn sm" id="iJoin">Remove all splits</button>' : ""}`;
      const ct = $("#iCut"); if (ct) ct.onclick = () => change(() => { E.st.cuts.push([sg.a, sg.b]); drawTimeline(); drawInspector(); });
      const rs = $("#iRestore"); if (rs) rs.onclick = () => change(() => { E.st.cuts = E.st.cuts.filter(([a, b]) => !(a <= sg.a + 0.01 && b >= sg.b - 0.01)); drawTimeline(); drawInspector(); });
      const jn = $("#iJoin"); if (jn) jn.onclick = () => change(() => { E.st.splits = []; E.st.cuts = []; E.sel = null; drawTimeline(); drawInspector(); });
      return;
    }
    if (s.k === "c") {
      const gr = groups()[s.i]; if (!gr) { E.sel = null; return drawInspector(); }
      el.innerHTML = `<h3>Caption</h3>${field("Words", `<input type="text" id="iCw" value="${esc(gr.map((i) => E.st.words[i].w).join(" "))}">`)}
        <p class="small muted">Fix a misheard word, or delete the caption.</p><button class="btn sm bad" id="iDel">Delete caption</button>`;
      $("#iCw").onchange = (ev) => change(() => {
        const ws = ev.target.value.trim().split(/\s+/).filter(Boolean), a = E.st.words[gr[0]].s, b = E.st.words[gr[gr.length - 1]].e;
        let neu;
        if (ws.length === gr.length) neu = gr.map((i, k) => ({ ...E.st.words[i], w: ws[k] }));
        else neu = ws.map((w, k) => ({ w, s: +(a + (b - a) * k / ws.length).toFixed(3), e: +(a + (b - a) * (k + 1) / ws.length).toFixed(3) }));
        E.st.words.splice(gr[0], gr.length, ...neu); drawTimeline(); drawInspector();
      });
      $("#iDel").onclick = () => { change(() => E.st.words.splice(gr[0], gr.length)); E.sel = null; drawTimeline(); drawInspector(); };
      return;
    }
    const e = byId(s.id); if (!e) { E.sel = null; return drawInspector(); }
    const T = ED_TYPES[e.type];
    const num_ = (k, label, min, max, stp) => field(label, `<input type="number" data-k="${k}" value="${e[k]}" min="${min}" max="${max}" step="${stp}">`);
    let body = field("Starts at (s from the clip start)", `<input type="number" data-k="t" value="${(e.t - E.st.start).toFixed(2)}" step="0.05">`);
    if (e.type !== "sfx") body += num_("dur", "Length (s)", 0.1, 30, 0.1);
    if (e.type === "text") body = field("Text", `<input type="text" data-k="text" value="${esc(e.text)}" maxlength="60">`) + body;
    if (e.type === "zoom") body += field(`Strength <span class="dim" id="iAm">${(+e.amount || 1.3).toFixed(2)}×</span>`, `<input type="range" data-k="amount" min="1.05" max="2" step="0.05" value="${e.amount || 1.3}">`) +
      field("Zoom into", `<select data-k="focus">${(g.layout === "cam_top" ? ["cam", "game", "center"] : ["center"]).map((f) => `<option value="${f}" ${e.focus === f ? "selected" : ""}>${{ cam: "Facecam", game: "Game", center: "Center" }[f]}</option>`).join("")}</select>`);
    if (e.type === "sfx") body += field("Sound", `<div class="row" style="flex-wrap:nowrap"><select data-k="name" style="flex:1">${m.sfx.map((x) => `<option value="${esc(x.name)}" ${x.name === e.name ? "selected" : ""}>${esc(x.name)}</option>`).join("")}${m.sfx.some((x) => x.name === e.name) ? "" : `<option selected>${esc(e.name)} (missing)</option>`}</select><button class="btn sm" id="iPv">▶</button></div>`) +
      field(`Volume <span class="dim" id="iVo">${Math.round((e.volume ?? 1) * 100)}%</span>`, `<input type="range" data-k="volume" min="0" max="2" step="0.05" value="${e.volume ?? 1}">`);
    el.innerHTML = `<h3><span class="ed-dot" style="background:${T.color}"></span>${T.label}${e.user ? "" : ' <span class="pill">AI</span>'}</h3>${body}
      ${e.why ? `<p class="small muted">Based on ${esc(e.why)}</p>` : ""}<div class="row"><button class="btn sm" id="iDup">Duplicate</button><button class="btn sm bad" id="iDel">Delete</button></div>`;
    $$("[data-k]", el).forEach((inp) => {
      inp.onfocus = () => (E.fb = snap());
      inp.oninput = () => {
        const k = inp.dataset.k;
        e[k] = ["text", "focus", "name"].includes(k) ? inp.value : k === "t" ? clamp(+inp.value + E.st.start, 0, D) : +inp.value;
        if (k === "amount") $("#iAm").textContent = `${(+inp.value).toFixed(2)}×`;
        if (k === "volume") $("#iVo").textContent = `${Math.round(inp.value * 100)}%`;
        drawTimeline(); if (k === "t") seek(e.t);
      };
      inp.onchange = () => commit(E.fb || snap());
    });
    const pv = $("#iPv"); if (pv) pv.onclick = () => playSfx(e.name, e.volume ?? 1);
    $("#iDel").onclick = () => { change(() => (E.st.edits = E.st.edits.filter((x) => x !== e))); E.sel = null; drawTimeline(); drawInspector(); };
    $("#iDup").onclick = () => change(() => { const n = { ...e, _id: uid++, t: Math.min(D - 0.1, e.t + (e.dur || 0.5)), user: true }; delete n.why; E.st.edits.push(n); E.sel = { k: "e", id: n._id }; drawTimeline(); drawInspector(); });
  }

  // ------------------------------------------------------------ actions
  function split() {
    const t = +video.currentTime.toFixed(2);
    if (t <= E.st.start + 0.1 || t >= E.st.end - 0.1 || E.st.splits.some((x) => Math.abs(x - t) < 0.1)) return toast("Move the playhead inside the clip to split");
    change(() => {
      // a split inside a cut part splits the cut too, so each half can be restored separately
      E.st.splits.push(t);
      E.st.cuts = E.st.cuts.flatMap(([a, b]) => (t > a && t < b ? [[a, t], [t, b]] : [[a, b]]));
      E.sel = { k: "s", i: segments().findIndex((s) => t >= s.a && t < s.b) }; drawTimeline(); drawInspector();
    });
  }
  function add(type) {
    const t = +video.currentTime.toFixed(2), T = ED_TYPES[type];
    const e = { _id: uid++, type, t, user: true };
    if (T.dur) e.dur = T.dur;
    if (type === "text") e.text = "NEW TEXT";
    if (type === "zoom") { e.amount = 1.3; e.focus = g.layout === "cam_top" ? "cam" : "center"; }
    if (type === "sfx") { e.name = m.sfx[0].name; e.volume = 1; }
    change(() => { E.st.edits.push(e); E.sel = { k: "e", id: e._id }; drawTimeline(); drawInspector(); });
    if (type === "text") setTimeout(() => { const i = $('#eInsp [data-k="text"]'); if (i) { i.focus(); i.select(); } }, 0);
  }
  function del() {
    const s = E.sel; if (!s) return;
    if (s.k === "e") { change(() => (E.st.edits = E.st.edits.filter((x) => x._id !== s.id))); }
    else if (s.k === "c") { const gr = groups()[s.i]; if (gr) change(() => E.st.words.splice(gr[0], gr.length)); }
    else if (s.k === "h") change(() => (E.st.hook_text = ""));
    else if (s.k === "s") { const sg = segments()[s.i]; if (sg && !sg.cut) change(() => E.st.cuts.push([sg.a, sg.b])); drawTimeline(); drawInspector(); return; }
    E.sel = null; drawTimeline(); drawInspector();
  }
  async function renderEdit() {
    if (outLen() < 2) return toast("The clip must be at least 2 seconds long");
    const b = $("#eRender"); b.disabled = true; b.textContent = "Sending…";
    const body = { ...E.st, edits: E.st.edits.map(({ _id, ...e }) => e) };
    try { await api(`/api/clips/${encodeURIComponent(m.id)}/edit`, { method: "POST", body }); }
    catch (e) { b.disabled = false; b.textContent = "Render"; return toast(esc(e.message)); }
    E.dirty = false;
    toast("🎬 Rendering your edit — the clip page updates when it's done");
    location.hash = `#/clips/${encodeURIComponent(m.id)}`;
  }

  // ------------------------------------------------------------ wiring
  E.onKey = (ev) => {
    if (ev.target.closest("input,textarea,select")) return;
    const mod = ev.ctrlKey || ev.metaKey;
    if (ev.code === "Space") { ev.preventDefault(); play(); }
    else if (mod && ev.key.toLowerCase() === "z") { ev.preventDefault(); ev.shiftKey ? redo() : undo(); drawTimeline(); drawInspector(); }
    else if (mod && ev.key.toLowerCase() === "y") { ev.preventDefault(); redo(); drawTimeline(); drawInspector(); }
    else if (ev.key === "s" || ev.key === "S") split();
    else if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); del(); }
    else if (ev.key === "ArrowLeft") seek(video.currentTime - (ev.shiftKey ? 1 : 1 / (m.fps || 30)));
    else if (ev.key === "ArrowRight") seek(video.currentTime + (ev.shiftKey ? 1 : 1 / (m.fps || 30)));
    else if (ev.key === "Escape") { E.sel = null; drawTimeline(); drawInspector(); }
  };
  E.onLeave = (ev) => { if (E.dirty) { ev.preventDefault(); ev.returnValue = ""; } };
  document.addEventListener("keydown", E.onKey);
  window.addEventListener("beforeunload", E.onLeave);
  $("#ePlay").onclick = play;
  $("#eSplit").onclick = split;
  $("#eUndo").onclick = () => { undo(); drawTimeline(); drawInspector(); };
  $("#eRedo").onclick = () => { redo(); drawTimeline(); drawInspector(); };
  $("#eRender").onclick = renderEdit;
  const rs = $("#eReset");
  if (rs) rs.onclick = () => {
    if (!confirm("Go back to the AI's edit? (You can undo this.)")) return;
    change(() => { Object.assign(E.st, { start: +m.ai.start, end: +m.ai.end, cuts: [], splits: [], hook_text: m.ai.hook_text, edits: m.ai.edits.map((e) => ({ ...e, _id: uid++ })) }); });
    E.sel = null; seek(E.st.start); drawTimeline(); drawInspector();
  };
  $$("[data-add]").forEach((b) => (b.onclick = () => add(b.dataset.add)));
  $("#eZoom").oninput = (ev) => { E.pps = +ev.target.value; drawTimeline(); };
  cv.onclick = play;
  // initial zoom: fit the whole recording, then center on the clip
  const tl = $("#eTl");
  const fit = () => { E.pps = clamp(Math.floor((tl.clientWidth - 40) / (E.st.end - E.st.start + 8)), 8, 200); $("#eZoom").value = E.pps; drawTimeline(); tl.scrollLeft = Math.max(0, (E.st.start - 4) * E.pps); };
  video.addEventListener("loadeddata", () => { seek(E.st.start); });
  video.addEventListener("seeked", () => drawFrame());
  drawTimeline(); drawInspector();
  requestAnimationFrame(() => requestAnimationFrame(fit));
  E.raf = requestAnimationFrame(tick);
}
