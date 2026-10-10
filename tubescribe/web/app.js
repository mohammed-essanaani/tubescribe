"use strict";

const $ = (sel) => document.querySelector(sel);

// text is always added as text nodes, never as HTML
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : (data.detail?.[0]?.msg ?? msg);
    } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(message, kind = "") {
  const el = h("div", { class: `toast ${kind}` }, message);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

const fmtDuration = (s) => {
  if (!s && s !== 0) return "";
  s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
};
const fmtNumber = (n) => new Intl.NumberFormat().format(n);
const KIND = {
  manual: ["Creator captions", "badge"],
  auto: ["Auto-generated", "badge badge-muted"],
  translated: ["Translated", "badge badge-info"],
};

const state = { config: null, speed: "balanced", job: null, source: null, waitTimer: null };

async function init() {
  initTheme();
  try {
    state.config = await api("/api/config");
  } catch (e) {
    toast(`Could not reach the TubeScribe server: ${e.message}`, "error");
    return;
  }
  $("#version").textContent = `v${state.config.version}`;
  buildLanguageSelect();
  buildSpeed();
  restoreOptions();
  bindForm();
  bindLibrary();
  bindReader();
  loadLibrary();
  if (state.config.active_job) followJob(state.config.active_job);
}

function initTheme() {
  $("#theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme
      ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("ts-theme", root.dataset.theme); } catch { /* ignore */ }
  });
}

function buildLanguageSelect() {
  const sel = $("#language");
  sel.append(h("option", { value: "orig" }, "Original (spoken language)"));
  const group = h("optgroup", { label: "Prefer a specific language" });
  for (const { code, name } of state.config.languages) group.append(h("option", { value: code }, name));
  sel.append(group, h("option", { value: "__custom" }, "Other language code..."));
  sel.addEventListener("change", () => {
    $("#language-custom").hidden = sel.value !== "__custom";
    if (sel.value === "__custom") $("#language-custom").focus();
    updateLanguageHint();
  });
}

function updateLanguageHint() {
  const v = $("#language").value;
  $("#language-hint").textContent = v === "orig"
    ? "Uses the language spoken in each video."
    : $("#allow-translation").checked
      ? "Falls back to YouTube's translation when needed."
      : "Falls back to the original language when unavailable.";
}

function buildSpeed() {
  const box = $("#speed");
  for (const s of state.config.speeds) {
    box.append(h("button", {
      type: "button", role: "radio", "data-speed": s.name, "aria-checked": "false",
      onclick: () => setSpeed(s.name),
    }, s.label));
  }
  setSpeed(state.config.default_speed);
}

function setSpeed(name) {
  state.speed = name;
  for (const b of $("#speed").children) b.setAttribute("aria-checked", String(b.dataset.speed === name));
  $("#speed-hint").textContent = state.config.speeds.find((s) => s.name === name)?.description ?? "";
  saveOptions();
}

function readOptions() {
  let lang = $("#language").value;
  if (lang === "__custom") lang = $("#language-custom").value.trim() || "orig";
  const formats = [...document.querySelectorAll("#formats input:checked")].map((i) => i.value);
  const limit = parseInt($("#limit").value, 10);
  return {
    url: $("#url").value.trim(),
    languages: lang === "orig" ? ["orig"] : [lang, "orig"],
    allow_translation: $("#allow-translation").checked,
    allow_auto: $("#allow-auto").checked,
    formats: formats.length ? formats : ["md"],
    timestamps: $("#timestamps").checked,
    limit: Number.isFinite(limit) && limit > 0 ? limit : null,
    speed: state.speed,
    force: $("#force").checked,
  };
}

function saveOptions() {
  if (!state.config) return;
  const o = readOptions();
  delete o.url;
  o.languageSelect = $("#language").value;
  o.languageCustom = $("#language-custom").value;
  store.set("ts-options", o);
}

function restoreOptions() {
  const o = store.get("ts-options", null);
  if (!o) return;
  if (o.languageSelect && [...$("#language").options].some((op) => op.value === o.languageSelect)) {
    $("#language").value = o.languageSelect;
    $("#language-custom").value = o.languageCustom || "";
    $("#language-custom").hidden = o.languageSelect !== "__custom";
  }
  for (const input of document.querySelectorAll("#formats input")) input.checked = o.formats?.includes(input.value) ?? input.checked;
  $("#allow-translation").checked = o.allow_translation ?? true;
  $("#allow-auto").checked = o.allow_auto ?? true;
  $("#timestamps").checked = !!o.timestamps;
  if (o.limit) $("#limit").value = o.limit;
  if (o.speed && state.config.speeds.some((s) => s.name === o.speed)) setSpeed(o.speed);
  updateLanguageHint();
}

function detectKind(raw) {
  const v = raw.trim();
  if (!v) return null;
  if (/^[A-Za-z0-9_-]{11}$/.test(v)) return "Video";
  if (/^@[\w.-]+$/.test(v)) return "Channel";
  if (/^ytsearch\d*:/.test(v)) return "Search";
  if (!/youtu\.?be/.test(v)) return "Unknown link";
  if (/[?&]list=/.test(v) && !/[?&]v=/.test(v)) return "Playlist";
  if (/\/shorts\/[\w-]{6,}/.test(v)) return "Short";
  if (/[?&]v=|youtu\.be\/|\/live\/|\/embed\//.test(v)) return /[?&]list=/.test(v) ? "Video" : "Video";
  if (/\/(@[^/]+|channel\/|c\/|user\/)/.test(v)) {
    if (/\/shorts\/?$/.test(v)) return "Channel · Shorts";
    if (/\/videos\/?$/.test(v)) return "Channel · Videos";
    if (/\/streams\/?$/.test(v)) return "Channel · Lives";
    return "Channel";
  }
  return "Link";
}

function bindForm() {
  const url = $("#url");
  const chip = $("#url-kind");
  url.addEventListener("input", () => {
    const k = detectKind(url.value);
    chip.hidden = !k;
    chip.textContent = k || "";
    $("#probe-result").hidden = true;
  });
  $("#paste-btn").addEventListener("click", async () => {
    try {
      url.value = (await navigator.clipboard.readText()).trim();
      url.dispatchEvent(new Event("input"));
      url.focus();
    } catch { toast("Clipboard access was blocked, paste with Ctrl+V instead."); }
  });
  for (const el of document.querySelectorAll("#extract-form input, #extract-form select")) {
    if (el.id !== "url") el.addEventListener("change", () => { updateLanguageHint(); saveOptions(); });
  }
  $("#extract-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const opts = readOptions();
    if (!opts.url) return;
    $("#extract-btn").disabled = true;
    try {
      const { id } = await api("/api/jobs", { method: "POST", body: JSON.stringify(opts) });
      followJob(id);
    } catch (err) {
      toast(err.message, "error");
      $("#extract-btn").disabled = false;
    }
  });
  $("#cancel-btn").addEventListener("click", async () => {
    if (!state.job) return;
    $("#cancel-btn").disabled = true;
    $("#job-activity").textContent = "Stopping...";
    await api(`/api/jobs/${state.job}/cancel`, { method: "POST" }).catch(() => {});
  });
  $("#probe-btn").addEventListener("click", probeLanguages);
  $("#open-folder").addEventListener("click", () =>
    api("/api/open-folder", { method: "POST" }).catch((e) => toast(e.message, "error")));
}

async function probeLanguages() {
  const url = $("#url").value.trim();
  const box = $("#probe-result");
  if (!url) { toast("Paste a video link first."); return; }
  box.hidden = false;
  box.replaceChildren("Checking...");
  try {
    const data = await api(`/api/tracks?url=${encodeURIComponent(url)}`);
    const items = data.tracks.map((t) => h("span", { class: KIND[t.kind][1], title: KIND[t.kind][0] }, `${t.name}`));
    box.replaceChildren(...[
      h("strong", {}, "Spoken: ", data.original_language || "unknown", " · "),
      ...(items.length ? items : ["No captions on this video."]),
      data.translatable ? h("span", { class: "badge badge-info" }, "+ translations") : null,
    ].filter(Boolean));
  } catch (e) {
    box.replaceChildren(h("span", { class: "badge badge-warn" }, e.message));
  }
}

function resetProgress() {
  $("#progress").hidden = false;
  $("#job-alert").hidden = true;
  $("#log").replaceChildren();
  for (const id of ["saved", "skipped", "nocap", "failed"]) $(`#stat-${id}`).textContent = "0";
  $("#job-title").textContent = "Resolving link...";
  $("#job-kind").textContent = "Working";
  $("#job-count").textContent = "";
  $("#job-activity").textContent = "";
  $("#bar").classList.add("indeterminate");
  $("#bar-fill").style.width = "";
  $("#cancel-btn").disabled = false;
  $("#cancel-btn").hidden = false;
  $("#extract-btn").disabled = true;
}

function followJob(id) {
  state.job = id;
  resetProgress();
  $("#progress").scrollIntoView({ behavior: "smooth", block: "nearest" });
  const es = new EventSource(`/api/jobs/${id}/events`);
  es.onmessage = (msg) => handleEvent(JSON.parse(msg.data), es);
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) finishUi();
  };
}

function setProgress(index, total) {
  $("#bar").classList.remove("indeterminate");
  const pct = total ? Math.round((index / total) * 100) : 100;
  $("#bar-fill").style.width = `${pct}%`;
  $("#bar").setAttribute("aria-valuenow", String(pct));
  $("#job-count").textContent = `${index} / ${total}`;
}

function bump(id) { const el = $(`#stat-${id}`); el.textContent = String(Number(el.textContent) + 1); }

function logLine(cls, icon, title, detail) {
  const log = $("#log");
  log.prepend(h("li", { class: cls }, h("span", { class: "icon" }, icon), h("span", { class: "t", title, dir: "auto" }, title), h("span", { class: "d" }, detail || "")));
}

function stopCountdown() { clearInterval(state.waitTimer); state.waitTimer = null; }

function handleEvent(ev, es) {
  switch (ev.type) {
    case "status":
      $("#job-activity").textContent = ev.message;
      break;
    case "resolved": {
      const kinds = { video: "Single video", playlist: "Playlist", channel: "Channel" };
      $("#job-kind").textContent = kinds[ev.kind] || ev.kind;
      $("#job-title").textContent = ev.title;
      $("#job-title").dir = "auto";
      setProgress(0, ev.total);
      if (!ev.total) $("#job-activity").textContent = "No videos found at this link.";
      break;
    }
    case "video_start":
      stopCountdown();
      $("#job-activity").textContent = `Fetching “${ev.title}”`;
      break;
    case "video_done": {
      const e = ev.entry;
      bump("saved");
      setProgress(ev.index, ev.total);
      logLine(e.note ? "warn" : "ok", "✓", ev.title,
        `${e.language_name} · ${fmtNumber(e.words)} words${e.note ? " · translation unavailable" : ""}`);
      addOrUpdateItem(e);
      break;
    }
    case "video_skipped":
      bump("skipped");
      setProgress(ev.index, ev.total);
      logLine("skip", "↷", ev.title, ev.reason);
      break;
    case "video_no_captions":
      bump("nocap");
      setProgress(ev.index, ev.total);
      logLine("warn", "○", ev.title, ev.reason);
      break;
    case "video_failed":
      bump("failed");
      setProgress(ev.index, ev.total);
      logLine("bad", "✗", ev.title, ev.reason);
      break;
    case "waiting": {
      stopCountdown();
      let left = Math.round(ev.seconds);
      const render = () => { $("#job-activity").textContent = `${ev.reason} · ${left}s`; };
      render();
      state.waitTimer = setInterval(() => { left -= 1; if (left <= 0) stopCountdown(); else render(); }, 1000);
      break;
    }
    case "error":
      $("#job-alert").hidden = false;
      $("#job-alert").textContent = ev.message;
      break;
    case "finished": {
      es.close();
      stopCountdown();
      const s = ev.summary;
      $("#job-activity").textContent = s.cancelled ? "Cancelled." : s.stopped_reason ? "Stopped." : "Finished.";
      if (!s.cancelled && !s.stopped_reason) {
        $("#bar").classList.remove("indeterminate");
        $("#bar-fill").style.width = "100%";
        if (s.saved) toast(`Saved ${s.saved} transcript${s.saved === 1 ? "" : "s"}.`);
      }
      finishUi();
      break;
    }
  }
}

function finishUi() {
  $("#cancel-btn").hidden = true;
  $("#extract-btn").disabled = false;
  $("#bar").classList.remove("indeterminate");
  state.job = null;
}

let searchTimer;
function bindLibrary() {
  $("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadLibrary, 200);
  });
  const syncZip = () => {
    const q = $("#search").value.trim();
    $("#zip-btn").href = `/api/export.zip?format=${$("#zip-format").value}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  };
  $("#zip-format").addEventListener("change", syncZip);
  $("#search").addEventListener("input", syncZip);
  syncZip();
}

async function loadLibrary() {
  try {
    const { items, count } = await api(`/api/library?q=${encodeURIComponent($("#search").value.trim())}`);
    $("#items").replaceChildren(...items.map(renderItem));
    $("#library-count").textContent = count ? `· ${count}` : "";
    $("#empty").hidden = count > 0 || !!$("#search").value;
  } catch (e) {
    toast(`Could not load the library: ${e.message}`, "error");
  }
}

function renderItem(e) {
  const [kindLabel, kindClass] = KIND[e.kind] || [e.kind, "badge"];
  const meta = [e.channel, e.upload_date].filter(Boolean).join(" · ");
  const thumb = `https://i.ytimg.com/vi/${encodeURIComponent(e.video_id)}/mqdefault.jpg`;
  return h("li", { "data-uid": e.uid },
    h("button", { class: "item", type: "button", onclick: () => openReader(e.uid) },
      h("div", { class: "thumb" },
        h("img", { src: thumb, alt: "", loading: "lazy" }),
        e.duration ? h("span", { class: "dur" }, fmtDuration(e.duration)) : null),
      h("div", { class: "item-body" },
        h("p", { class: "item-title", dir: "auto" }, e.title),
        h("p", { class: "item-meta", dir: "auto" }, meta),
        h("p", { class: "item-preview", dir: "auto", lang: e.language }, e.preview),
        h("div", { class: "badges" },
          h("span", { class: "badge" }, e.language_name || e.language),
          h("span", { class: kindClass }, kindLabel),
          h("span", { class: "badge badge-muted" }, `${fmtNumber(e.words)} words`),
          e.note ? h("span", { class: "badge badge-warn", title: e.note }, "Translation unavailable") : null))));
}

function addOrUpdateItem(entry) {
  if ($("#search").value.trim()) return; // don't disturb an active search
  const list = $("#items");
  list.querySelector(`[data-uid="${CSS.escape(entry.uid)}"]`)?.remove();
  list.prepend(renderItem(entry));
  $("#empty").hidden = true;
  $("#library-count").textContent = `· ${list.children.length}`;
}

let readerData = null;

function bindReader() {
  const dlg = $("#reader");
  $("#reader-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  $("#reader-ts").addEventListener("change", () => {
    $("#reader-body").classList.toggle("hide-ts", !$("#reader-ts").checked);
    updateDownloadLinks();
  });
  $("#reader-find").addEventListener("input", () => renderParagraphs($("#reader-find").value.trim()));
  $("#copy-btn").addEventListener("click", async () => {
    if (!readerData) return;
    try {
      await navigator.clipboard.writeText(readerData.paragraphs.map((p) => p.text).join("\n\n"));
      toast("Transcript copied.");
    } catch { toast("Clipboard access was blocked.", "error"); }
  });
}

function updateDownloadLinks() {
  if (!readerData) return;
  const ts = $("#reader-ts").checked;
  for (const f of ["md", "txt", "srt", "json"]) {
    $(`#dl-${f}`).href = `/api/library/${readerData.entry.uid}/download?format=${f}&timestamps=${ts}`;
  }
}

async function openReader(uid) {
  try {
    readerData = await api(`/api/library/${encodeURIComponent(uid)}`);
  } catch (e) { toast(e.message, "error"); return; }
  const e = readerData.entry;
  const [kindLabel, kindClass] = KIND[e.kind] || [e.kind, "badge"];
  $("#reader-title").textContent = e.title;
  $("#reader-title").dir = "auto";
  $("#reader-meta").textContent = [e.channel, e.upload_date, e.duration ? fmtDuration(e.duration) : null].filter(Boolean).join(" · ");
  $("#reader-badges").replaceChildren(...[
    h("span", { class: "badge" }, e.language_name || e.language),
    h("span", { class: kindClass }, kindLabel),
    h("span", { class: "badge badge-muted" }, `${fmtNumber(e.words)} words`),
    e.note ? h("span", { class: "badge badge-warn", title: e.note }, e.note) : null,
  ].filter(Boolean));
  $("#yt-link").href = e.url;
  $("#reader-find").value = "";
  const body = $("#reader-body");
  body.dir = readerData.rtl ? "rtl" : "auto";
  body.lang = e.language;
  body.classList.toggle("hide-ts", !$("#reader-ts").checked);
  updateDownloadLinks();
  renderParagraphs("");
  $("#reader").showModal();
  body.scrollTop = 0;
}

function highlight(text, query) {
  if (!query) return [text];
  const out = [];
  const lower = text.toLocaleLowerCase(), q = query.toLocaleLowerCase();
  let i = 0, j;
  while ((j = lower.indexOf(q, i)) !== -1) {
    out.push(text.slice(i, j), h("mark", {}, text.slice(j, j + q.length)));
    i = j + q.length;
  }
  out.push(text.slice(i));
  return out;
}

function renderParagraphs(query) {
  const { entry, paragraphs } = readerData;
  let hits = 0;
  const nodes = paragraphs.map((p) => {
    const parts = highlight(p.text, query);
    hits += parts.filter((x) => x instanceof Node).length;
    const t = Math.floor(p.start);
    return h("p", { dir: "auto" },
      h("a", { class: "ts", href: `https://youtu.be/${entry.video_id}?t=${t}`, target: "_blank", rel: "noopener", dir: "ltr" }, fmtDuration(t)),
      ...parts);
  });
  $("#reader-body").replaceChildren(...nodes);
  $("#find-count").textContent = query ? `${hits}` : "";
  if (query && hits) $("#reader-body mark")?.scrollIntoView({ block: "center" });
}

init();
