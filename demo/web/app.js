// 面向个人敏感数据的智能问答 — front-end
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
// The icon sprite carries the same ?v= as this script, so an update never leaves the browser on an old sprite.
const VERSION = new URL(import.meta.url).search;
const SPRITE = `/assets/icons.svg${VERSION}`;
const ICON = (id, cls = "i") => `<svg class="${cls}"><use href="${SPRITE}#${id}"/></svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const PARAMS = new URLSearchParams(location.search);
// A run still going after this long is stopped by the page itself (the server's own limit is 90 s).
const WATCHDOG_S = Number(PARAMS.get("watchdog")) || 100;
const MOTION = !matchMedia("(prefers-reduced-motion: reduce)").matches;
// Dev-only fixture: ?mock=photo|sport|car renders sample events for the new data kinds before the
// backend serves them. Nothing is loaded without the parameter.
if (PARAMS.get("mock")) (await import(`./mock.js${VERSION}`)).install(PARAMS.get("mock"));

const state = {
  status: null,
  presets: [],
  devices: new Map(),
  model: null,
  running: false,
  stopping: null,
  lanes: {},
  verify: null,
  plainTicker: "",
  kind: "mail", // data kind the lanes are set up for: the selected preset's, then the run's plan
  device: null, // device id, only when the server names devices
  run: null, // { kind, device, total } of the current or last run
  net: { edge: { gbps: 1, rtt_ms: 30 }, cloud: { gbps: 50, rtt_ms: 0.5 } },
};

// ------------------------------------------------------------------ format
function bytes(n) {
  if (n == null || isNaN(n)) return ["—", ""];
  const u = [["GB", 1e9], ["MB", 1e6], ["KB", 1e3]];
  for (const [unit, f] of u) if (n >= f) return [(n / f).toFixed(n / f >= 100 ? 0 : 1), unit];
  return [String(Math.round(n)), "B"];
}
const bytesText = (n) => bytes(n).join(" ");
function secs(ms) {
  if (ms == null || isNaN(ms)) return ["—", ""];
  if (ms < 1000) return [ms < 10 ? ms.toFixed(1) : Math.round(ms).toString(), "ms"];
  return [(ms / 1000).toFixed(2), "s"];
}
const secsText = (ms) => secs(ms).join(" ");
const intText = (n) => Number(n || 0).toLocaleString("en-US");
const wanText = (n) => (n >= 1e4 ? `${+(n / 1e4).toFixed(n % 1e4 ? 1 : 0)} 万` : `${intText(n)} `);
const dateText = (d) => String(d || "").slice(0, 10);
function placeText(p) {
  if (!p) return "";
  if (typeof p === "string") return p;
  const name = p.name || "";
  if (p.city && name && !name.includes(p.city)) return `${p.city} · ${name}`;
  return name || p.city || p.country || "";
}
async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

// ------------------------------------------------------------------ data kinds and devices
// PLAN-v3 §1: four devices, each with its own kind of data. A mail-only server names no kind or device,
// and every label then falls back to the mailbox exactly as before.
const KINDS = {
  mail: { label: "邮件", short: "邮件", unit: "封", noun: "邮件", whole: "整个邮箱", stage: "上传邮箱", link: "邮件与附件 · 分批上传", icon: "mail", view: "原文" },
  photo: { label: "照片", short: "照片", unit: "张", noun: "照片", whole: "整个相册", stage: "上传相册", link: "全部照片 · 分批上传", icon: "image", view: "照片" },
  sport: { label: "运动健康", short: "运动", unit: "条", noun: "运动记录", whole: "全部运动记录", stage: "上传运动记录", link: "全部运动记录 · 分批上传", icon: "activity", view: "记录" },
  car: { label: "用车出行", short: "出行", unit: "条", noun: "出行记录", whole: "全部出行记录", stage: "上传出行记录", link: "全部出行记录 · 分批上传", icon: "route", view: "记录" },
};
const DEVICES = {
  phone: { label: "手机", icon: "smartphone", kind: "photo" },
  pc: { label: "PC", icon: "laptop", kind: "mail" },
  car: { label: "车机", icon: "car", kind: "car" },
  watch: { label: "手表", icon: "watch", kind: "sport" },
};
const DEVICE_ORDER = ["phone", "pc", "car", "watch"];
const OWNER = "林舟"; // owner of the demo data (demo/data/bible.json); newer servers name the owner per row
const kindOf = (k) => KINDS[k] || KINDS.mail;
const deviceFor = (kind) => Object.keys(DEVICES).find((d) => DEVICES[d].kind === kind) || "pc";
function deviceOf(id) {
  const base = DEVICES[id] || { label: id || "终端", icon: "smartphone", kind: "mail" };
  return { ...base, ...(state.devices.get(id) || {}), id, icon: base.icon };
}
// "10 万封邮件", "412 张照片": what the device holds, for the terminal node of the plain lane
function holdings(kind, device) {
  const K = kindOf(kind);
  const n = kind === "mail" ? state.status?.mailbox?.emails : deviceOf(device || deviceFor(kind)).records;
  if (!n) return kind === "mail" ? "10 万封邮件" : K.noun;
  return `${wanText(n)}${K.unit}${K.noun}`;
}
function kindTotal(kind, device) {
  if (kind === "mail") return state.status?.mailbox?.emails;
  return deviceOf(device || deviceFor(kind)).records;
}
// plan fields may sit on the event or inside event.plan (PLAN-v3 §3); read both
function planInfo(ev) {
  const p = ev.plan || {};
  const pick = (k) => ev[k] ?? p[k];
  const device = pick("device") || null;
  const kind = KINDS[pick("kind")] ? pick("kind") : device && DEVICES[device] ? DEVICES[device].kind : "mail";
  return { kind, device, total: pick("total") ?? null, relaxed: pick("relaxed") || [] };
}
const photoUrl = (id, size) => window.HPIS_MOCK?.photoUrl?.(id, size) ?? `/api/photo/${id}?size=${size}`;

// ------------------------------------------------------------------ counters
const tweens = new Map();
function tweenTo(el, value, render, dur = 650) {
  const from = Number(el.dataset.v || 0);
  const t0 = performance.now();
  tweens.set(el, { from, to: value, t0, dur, render });
  el.dataset.v = value;
}
(function tick() {
  const now = performance.now();
  for (const [el, tw] of tweens) {
    const p = Math.min(1, (now - tw.t0) / tw.dur);
    const e = 1 - Math.pow(1 - p, 3);
    tw.render(el, tw.from + (tw.to - tw.from) * e);
    if (p >= 1) tweens.delete(el);
  }
  requestAnimationFrame(tick);
})();
const renderBytes = (el, v) => { const [a, b] = bytes(v); el.innerHTML = `${a}<small>${b}</small>`; };
const renderSecs = (el, v) => { const [a, b] = secs(v); el.innerHTML = `${a}<small>${b}</small>`; };
// Stage times: each boundary is rounded to 0.01 s once and a stage is the gap between two rounded
// boundaries, so the stages on screen add up exactly to the total.
const cs = (ms) => Math.round(ms / 10);
const csText = (c) => `${(c / 100).toFixed(2)} s`;

// ------------------------------------------------------------------ lane config
const LANES = {
  plain: {
    title: "端侧直传普通云", tag: "明文", icon: "unlock",
    ribbon: "明文方案 · 云端可读全部数据", ribbonIcon: "eye",
    desc: "每次提问把整个邮箱上传到普通云，由云端明文检索并回答",
    stages: [["upload", "上传邮箱"], ["retrieve", "云端检索"], ["generate", "模型作答"]],
    nodes: [["dev", 70, "smartphone", "终端", "10 万封邮件"], ["cloud", 486, "cloud", "普通云", "明文处理"]],
    links: [["edge", "dev", "cloud", "邮件与附件 · 分批上传"]],
  },
  v1: {
    title: "HPIS 1.0", tag: "全量", icon: "database",
    ribbon: "匿名存储 · 全量解密", ribbonIcon: "unlock",
    desc: "数据匿名加密存储，但每次检索都要把该用户全部密文搬到 HPIC 解密",
    stages: [["request", "密封请求"], ["fetch", "全量取回"], ["decrypt", "全部解密检索"], ["generate", "模型生成"]],
    nodes: [["dev", 56, "smartphone", "终端", "掌控密钥"], ["hpic", 290, "cpu", "HPIC", "可信计算"], ["hpis", 512, "database", "HPIS", "匿名密态存储"]],
    links: [["edge", "dev", "hpic", "推理请求"], ["cloudl", "hpic", "hpis", "全量数据密文"]],
  },
  v2: {
    title: "端侧+HPIC+HPIS", tag: "密态", icon: "shield-check",
    ribbon: "隐私保护方案 · 云端仅见密文", ribbonIcon: "eye-off",
    desc: "SSE 在密文索引上精确筛选，向量在可信计算区内精确排序，只取回命中的几封密文",
    stages: [["request", "加密上传"], ["search", "密态检索"], ["generate", "模型作答"]],
    nodes: [["dev", 56, "smartphone", "终端", "掌控密钥"], ["hpic", 290, "cpu", "HPIC", "可信计算"], ["hpis", 512, "database", "HPIS", "SSE + 密态向量"]],
    links: [["edge", "dev", "hpic", "推理请求 + 目标索引密文"], ["cloudl", "hpic", "hpis", "部分数据密文"]],
  },
};

// ------------------------------------------------------------------ flow diagram
const flows = new Set();
class Flow {
  constructor(host, cfg) {
    this.svg = document.createElementNS(NS, "svg");
    // the drawing spans y 16..125; cropping the empty margins draws it larger in the same height
    this.svg.setAttribute("viewBox", "0 12 568 116");
    this.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    host.appendChild(this.svg);
    this.nodes = {};
    this.links = {};
    this.packets = [];
    this.streams = {};
    const y = 58;
    for (const [id, a, b, label] of cfg.links) {
      const x1 = cfg.nodes.find((n) => n[0] === a)[1] + 40;
      const x2 = cfg.nodes.find((n) => n[0] === b)[1] - 40;
      const g = this.el("g", {});
      const base = this.el("path", { d: `M${x1} ${y} L${x2} ${y}`, class: "link" }, g);
      const dash = this.el("path", { d: `M${x1} ${y} L${x2} ${y}`, class: "link-dash" }, g);
      const lab = this.el("text", { x: (x1 + x2) / 2, y: y - 14, class: "llabel" }, g);
      lab.textContent = label;
      const val = this.el("text", { x: (x1 + x2) / 2, y: y + 24, class: "lbytes" }, g);
      this.links[id] = { x1, x2, y, base, dash, lab, val };
    }
    for (const [id, x, icon, label, sub] of cfg.nodes) {
      const g = this.el("g", { class: "node", transform: `translate(${x - 36} ${y - 28})` });
      this.el("rect", { width: 72, height: 56, rx: 16, class: "ripple" }, g);
      this.el("rect", { width: 72, height: 56, rx: 16, class: "box" }, g);
      const use = this.el("use", { href: `${SPRITE}#${icon}`, x: 22, y: 14, width: 28, height: 28 }, g);
      use.setAttribute("style", "fill:none;stroke:currentColor");
      const l = this.el("text", { x: 36, y: 76, class: "nlabel" }, g);
      l.textContent = label;
      const s = this.el("text", { x: 36, y: 92, class: "nsub" }, g);
      s.textContent = sub;
      this.nodes[id] = { g, s, sub, use, l };
    }
    flows.add(this);
  }
  el(tag, attrs, parent = this.svg) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    parent.appendChild(e);
    return e;
  }
  node(id, cls, text, subcls = "") {
    const n = this.nodes[id];
    if (!n) return;
    n.g.classList.remove("active", "alert");
    if (cls) n.g.classList.add(cls);
    if (text != null) { n.s.textContent = text; n.s.setAttribute("class", "nsub " + subcls); }
  }
  // the terminal node follows the device the question is about (plan.device)
  setTerminal({ icon, label }, sub) {
    const n = this.nodes.dev;
    if (!n) return;
    n.use.setAttribute("href", `${SPRITE}#${icon}`);
    n.l.textContent = label;
    n.sub = sub;
    if (!n.g.classList.contains("active")) { n.s.textContent = sub; n.s.setAttribute("class", "nsub"); }
  }
  linkLabel(id, text) {
    if (this.links[id]) this.links[id].lab.textContent = text;
  }
  linkValue(id, text, cls = "") {
    const l = this.links[id];
    if (!l) return;
    l.val.textContent = text;
    l.val.setAttribute("class", "lbytes " + cls);
  }
  hot(id, on) {
    const l = this.links[id];
    if (!l) return;
    l.base.classList.toggle("hot", on);
    l.dash.classList.toggle("on", on);
  }
  send(id, { reverse = false, kind = "", r = 3.4, dur = 650 } = {}) {
    const l = this.links[id];
    if (!l) return;
    const x0 = reverse ? l.x2 : l.x1;
    const tail = [0.78, 0.56].map((k) => this.el("circle", { r: r * k, class: `packet tail ${kind}`, cx: x0, cy: l.y, opacity: 0 }));
    const c = this.el("circle", { r, class: `packet ${kind}`, cx: x0, cy: l.y });
    this.packets.push({ c, tail, l, reverse, t0: performance.now(), dur });
  }
  stream(id, on, opts = {}) {
    clearInterval(this.streams[id]);
    delete this.streams[id];
    if (on) this.streams[id] = setInterval(() => this.send(id, opts), opts.every || 70);
  }
  quiet() {
    for (const k of Object.keys(this.streams)) this.stream(k, false);
    for (const id of Object.keys(this.links)) this.hot(id, false);
  }
  // a stopped run: nothing glows any more, and "…中" (in progress) labels say it stopped
  calm() {
    this.quiet();
    for (const n of Object.values(this.nodes)) {
      n.g.classList.remove("active", "alert");
      if (/中…?$/.test(n.s.textContent)) { n.s.textContent = "已停止"; n.s.setAttribute("class", "nsub"); }
    }
  }
  reset() {
    this.quiet();
    for (const p of this.packets) { p.c.remove(); p.tail.forEach((x) => x.remove()); }
    this.packets = [];
    for (const [id, n] of Object.entries(this.nodes)) this.node(id, null, n.sub);
    for (const id of Object.keys(this.links)) this.linkValue(id, "");
  }
  frame(now) {
    const at = (p, t) => {
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      return p.reverse ? p.l.x2 - (p.l.x2 - p.l.x1) * e : p.l.x1 + (p.l.x2 - p.l.x1) * e;
    };
    this.packets = this.packets.filter((p) => {
      const t = (now - p.t0) / p.dur;
      if (t >= 1) { p.c.remove(); p.tail.forEach((x) => x.remove()); return false; }
      const fade = t > 0.85 ? (1 - t) / 0.15 : 1;
      p.c.setAttribute("cx", at(p, t));
      p.c.setAttribute("opacity", fade);
      p.tail.forEach((x, i) => {
        x.setAttribute("cx", at(p, Math.max(0, t - 0.06 * (i + 1))));
        x.setAttribute("opacity", t < 0.06 * (i + 1) ? 0 : fade * (0.45 - i * 0.2));
      });
      return true;
    });
  }
}
(function animate(now) {
  for (const f of flows) f.frame(now);
  requestAnimationFrame(animate);
})(performance.now());

// ------------------------------------------------------------------ lanes
const IDLE = `<div class="ph ph--idle"><i></i><b>等待提问</b><span>点击「开始对比」，两条路径会同时检索并作答</span></div>`;
function buildLanes() {
  const host = $("#lanes");
  host.innerHTML = "";
  host.classList.add("two");
  for (const id of ["plain", "v2"]) {
    const cfg = LANES[id];
    const el = document.createElement("article");
    el.className = `lane card lane--${id}`;
    el.dataset.lane = id;
    el.dataset.kind = "mail";
    // the band on top says what the cloud gets, and its ticker shows it: plaintext on the left, ciphertext on the right
    el.innerHTML = `
      <div class="lane__ribbon">${ICON(cfg.ribbonIcon)}<span class="lane__id">${cfg.ribbon}</span><div class="ticker"><span class="ticker__track ${id === "plain" ? "bad" : "mono"}" data-ticker></span></div></div>
      <header class="lane__head">
        <div class="lane__icon">${ICON(cfg.icon)}</div>
        <div class="lane__title"><h3>${cfg.title}<small>${cfg.tag}</small></h3><p data-desc>${cfg.desc}</p></div>
        <span class="pill" data-pill>待命</span>
      </header>
      <div class="flow" data-flow></div>
      <ol class="rail">${cfg.stages.map(([k, l]) => `<li data-stage="${k}"><span>${l}</span><b>—</b></li>`).join("")}<li class="rail__total" data-total><span>完整回答</span><b>—</b></li></ol>
      <div class="metrics">
        <div class="metric ${id === "plain" ? "bad" : id === "v2" ? "good" : ""}"><label>${ICON("signal")}上传云端</label><output data-m="edge">—</output></div>
        <div class="metric ${id === "v1" ? "v1" : id === "v2" ? "good" : ""}"><label>${ICON("server")}云云传输</label><output data-m="cloud">—</output></div>
      </div>
      <div class="answer">
        <div class="answer__head"><span>${ICON("brain")} <b data-model></b> 的回答<em class="answer__mode" data-mode></em></span><span class="answer__q" data-quality></span><span data-evcount></span></div>
        <div class="answer__notice" data-notice hidden></div>
        <div class="answer__text" data-answer>${IDLE}</div>
        <div class="evlist" data-evlist></div>
      </div>`;
    host.appendChild(el);
    const flow = new Flow($("[data-flow]", el), cfg);
    state.lanes[id] = { id, el, cfg, flow, kind: "mail", text: "", evidence: [], stageT: {}, ends: {}, m: {} };
    setTicker(id);
  }
}

function lane(id) { return state.lanes[id]; }
function setTotal(id, ms) {
  const li = $("[data-total]", lane(id).el);
  const b = $("b", li);
  li.classList.add("done");
  b.classList.remove("ticking", "pop");
  b.textContent = csText(cs(ms));
  void b.offsetWidth;
  b.classList.add("pop");
}
function pill(id, text, cls = "") {
  const p = $("[data-pill]", lane(id).el);
  p.textContent = text;
  p.className = "pill " + cls;
}
function metric(id, key, value, kind) {
  const L = lane(id);
  if (!L) return;
  const el = $(`[data-m="${key}"]`, L.el);
  el.classList.toggle("ticking", false);
  L.m[key] = value;
  if (value == null) { el.textContent = "—"; el.dataset.v = 0; return; }
  if (kind === "bytes") tweenTo(el, value, renderBytes);
  else tweenTo(el, value, renderSecs, 400);
}
function stage(id, key, phase, t, label) {
  const L = lane(id);
  if (!L) return;
  const li = $(`[data-stage="${key}"]`, L.el);
  if (!li) return;
  if (phase === "start") {
    L.stageT[key] = t;
    li.classList.add("run");
    li.style.setProperty("--p", "8%");
    clearInterval(L["sp_" + key]);
    let p = 8;
    L["sp_" + key] = setInterval(() => { p = Math.min(92, p + (95 - p) * 0.08); li.style.setProperty("--p", p + "%"); }, 120);
  } else if (phase === "progress") {
    clearInterval(L["sp_" + key]);
    li.style.setProperty("--p", Math.round(label * 100) + "%");
  } else if (phase === "end") {
    clearInterval(L["sp_" + key]);
    li.classList.remove("run");
    li.classList.add("done");
    li.style.setProperty("--p", "100%");
    L.ends[key] = t;
    const keys = L.cfg.stages.map(([k]) => k);
    const prev = keys.slice(0, keys.indexOf(key)).reverse().map((k) => L.ends[k]).find((v) => v != null) ?? 0;
    $("b", li).textContent = csText(Math.max(0, cs(t) - cs(prev)));
  }
}
function setTicker(id, text) {
  const L = lane(id);
  if (!L) return;
  const tr = $("[data-ticker]", L.el);
  const defaults = {
    plain: plainExposure(),
    v1: "HPIS 只存密文；但 HPIC 每次要解密该用户的全部邮件，才能找出需要的那几封",
    v2: state.cipherTicker || "AID · HMAC-SHA256 检索令牌 · AES-256-GCM 正文密文 · 密封的目标向量",
  };
  tr.textContent = text || defaults[id];
  // Constant reading speed (~40 px/s) however long the text is.
  requestAnimationFrame(() => {
    tr.style.animation = "none";
    void tr.offsetWidth;
    tr.style.animation = "";
    tr.style.animationDuration = `${Math.max(18, tr.scrollWidth / 40)}s`;
  });
}
// what the ordinary cloud can read for the kind of data in play
function plainExposure() {
  if (state.kind === "mail") return state.plainTicker || "邮件原文、附件、体检报告、账单、合同金额、联系方式……全部以明文进入普通云";
  const K = kindOf(state.kind);
  const dev = deviceOf(state.device || deviceFor(state.kind));
  return dev.exposure || `普通云可读取：全部 ${dev.records ? intText(dev.records) + " " + K.unit : ""}${K.noun}`;
}

// Set the lanes up for one kind of data and device (from a preset before the run, from the plan during it).
function applyKind(kind, device) {
  kind = KINDS[kind] ? kind : "mail";
  state.kind = kind;
  state.device = device || null;
  const K = kindOf(kind);
  const dev = device ? deviceOf(device) : null;
  const term = dev ? { icon: dev.icon, label: dev.label } : { icon: "smartphone", label: "终端" };
  for (const L of Object.values(state.lanes)) {
    L.kind = kind;
    L.el.dataset.kind = kind;
    L.flow.setTerminal(term, L.id === "plain" ? holdings(kind, device) : "掌控密钥");
    if (L.id === "plain") {
      L.flow.linkLabel("edge", K.link);
      $('[data-stage="upload"] span', L.el).textContent = K.stage;
      $("[data-desc]", L.el).textContent = `每次提问把${K.whole}上传到普通云，由云端明文检索并回答`;
    } else {
      $("[data-desc]", L.el).textContent = kind === "mail" ? L.cfg.desc : L.cfg.desc.replace("几封", "几条");
    }
  }
  $("#device .device__tag use").setAttribute("href", `${SPRITE}#${term.icon}`);
  setTicker("plain");
  renderMeta();
}

function resetLanes() {
  for (const L of Object.values(state.lanes)) {
    L.flow.reset();
    L.text = "";
    L.evidence = [];
    L.stageT = {};
    L.ends = {};
    L.m = {};
    L.done = false;
    L.halted = null;
    L.final = null;
    L.llm = null;
    L.decryptRows = null;
    L.el.classList.remove("running", "done", "halted");
    for (const li of $$(".rail li", L.el)) {
      li.className = li.hasAttribute("data-total") ? "rail__total" : "";
      li.style.removeProperty("--p");
      const b = $("b", li);
      b.textContent = "—";
      b.classList.remove("ticking", "pop");
      clearInterval(L["sp_" + li.dataset.stage]);
    }
    for (const o of $$("output", L.el)) { o.textContent = "—"; o.dataset.v = 0; }
    $("[data-answer]", L.el).innerHTML = IDLE;
    $("[data-evlist]", L.el).innerHTML = "";
    $("[data-evlist]", L.el).className = "evlist";
    $("[data-quality]", L.el).innerHTML = "";
    $("[data-evcount]", L.el).textContent = "";
    $("[data-mode]", L.el).textContent = "";
    const note = $("[data-notice]", L.el);
    note.hidden = true;
    note.innerHTML = "";
    $("[data-model]", L.el).textContent = modelLabel();
    pill(L.id, "待命");
    setTicker(L.id);
  }
  $("#verdict").classList.remove("on");
  document.body.classList.remove("scoreboard", "results");
  state.verify = null;
}

// ------------------------------------------------------------------ answer rendering
function renderAnswer(id, final = false) {
  const L = lane(id);
  const box = $("[data-answer]", L.el);
  let html = esc(L.text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^#{1,4}\s*(.+)$/gm, "<strong>$1</strong>")
    .replace(/^\s*[-*]\s+/gm, "• ")
    .replace(/\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g, (_, nums) =>
      nums.split(/[,，、]/).map((n) => `<button class="cite" data-n="${n.trim()}">${n.trim()}</button>`).join(""))
    .replace(/\n/g, "<br>");
  if (!final) html += `<span class="caret"></span>`;
  box.innerHTML = html;
  box.scrollTop = final ? 0 : box.scrollHeight;
}

// Evidence: mail chips as before; photos as a thumbnail grid; watch and car records as a compact list.
function renderEvidence(id) {
  const L = lane(id);
  const kind = L.evidence.find((e) => e.kind)?.kind || L.kind || "mail";
  const K = kindOf(kind);
  const host = $("[data-evlist]", L.el);
  $("[data-evcount]", L.el).textContent = L.evidence.length ? `取证 ${L.evidence.length} ${K.unit}` : "";
  if (kind === "photo") {
    host.className = "evlist evlist--photos";
    host.innerHTML = L.evidence
      .map((e, i) => `<button class="thumb" data-i="${i}" title="${esc(e.title)}"><img src="${esc(e.thumb || photoUrl(e.id, "thumb"))}" alt="" loading="lazy" decoding="async"><b>${i + 1}</b><span>${esc(placeText(e.place) || e.title)}<em>${esc(dateText(e.date))}</em></span></button>`)
      .join("");
  } else if (kind !== "mail") {
    host.className = "evlist evlist--records";
    host.innerHTML = L.evidence
      .map((e, i) => `<button class="rec" data-i="${i}" title="${esc(e.title)}"><b>${i + 1}</b><time>${esc(dateText(e.date).slice(5) || "")}</time><strong>${esc(e.title)}</strong><span>${esc(e.summary || "")}</span></button>`)
      .join("");
  } else {
    host.className = "evlist";
    host.innerHTML = L.evidence
      .map((e, i) => `<span class="ev" data-i="${i}" title="${esc(e.subject)}"><b>${i + 1}</b><span>${esc(e.subject)}</span></span>`)
      .join("");
  }
}

function presetFor(question) {
  const clean = (s) => s.replace(/\s/g, "").replace(/[？?。.!！]+$/, "");
  return state.presets.find((p) => clean(p.question) === clean(question));
}

function quality(id, question) {
  const L = lane(id);
  const preset = presetFor(question);
  const host = $("[data-quality]", L.el);
  if (!preset || !preset.gold || !preset.gold.facts) { host.innerHTML = ""; return null; }
  const norm = (x) => String(x).replace(/\s/g, "").replace(/[￥¥]/g, "").replace(/[,，]/g, "").replace(/：/g, ":");
  const text = norm(L.text);
  const facts = preset.gold.facts;
  const hit = facts.filter((f) => [f.value, ...(f.match || [])].some((m) => m && text.includes(norm(m)))).length;
  const cited = new Set([...L.text.matchAll(/\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g)].flatMap((m) => m[1].split(/[,，、]/).map((n) => Number(n.trim()))));
  const citedIds = new Set([...cited].map((n) => L.evidence[n - 1]?.id).filter(Boolean));
  const gold = preset.gold.evidence_ids || preset.evidence_ids || [];
  const ev = gold.filter((g) => citedIds.has(g)).length;
  const retrieved = gold.filter((g) => L.evidence.some((e) => e.id === g)).length;
  const unit = kindOf(L.kind).unit;
  host.innerHTML = `
    <span class="qbadge ${hit === facts.length ? "ok" : "warn"}">${ICON("check-circle")}关键事实 ${hit}/${facts.length}</span>
    ${gold.length ? `<span class="qbadge ${retrieved === gold.length ? "ok" : "warn"}">${ICON("file-text")}检出证据 ${retrieved}/${gold.length}</span>` : ""}
    <span class="qbadge ${ev > 0 || !gold.length ? "ok" : "warn"}">${ICON("paperclip")}引用 ${citedIds.size} ${unit}</span>`;
  return { hit, facts: facts.length, ev, gold: gold.length };
}

// ------------------------------------------------------------------ run
function modelLabel() {
  const m = (state.status?.models || []).find((x) => x.id === state.model);
  return m ? m.label : state.model || "";
}
function modelVision() {
  const m = (state.status?.models || []).find((x) => x.id === state.model);
  return m && "vision" in m ? !!m.vision : null;
}

async function run() {
  if (state.running) return;
  const question = $("#question").value.trim();
  if (!question) return;
  if (state.status?.phase !== "ready") return;
  state.running = true;
  state.stopping = null;
  state.run = { kind: state.kind, device: state.device, total: null };
  const ctrl = new AbortController();
  state.abort = ctrl;
  $("#run").disabled = true;
  $("#run span").textContent = "对比中…";
  $("#stop").hidden = false;
  $("#stop").disabled = false;
  $("#stop span").textContent = "停止";
  resetLanes();
  const lanes = ["plain", "v2"];
  for (const id of lanes) {
    pill(id, "运行中", "run");
    lane(id).el.classList.add("running");
    $("[data-answer]", lane(id).el).innerHTML = `<div class="ph ph--idle ph--run"><i></i><b>检索中</b><span>找到证据后，模型开始作答</span></div>`;
  }
  $("#device-chips").innerHTML = "";
  const body = JSON.stringify({ question, model: state.model, lanes, net: state.net });
  const t0 = performance.now();
  clearInterval(state.clock);
  state.clock = setInterval(() => {
    const el = performance.now() - t0;
    for (const id of lanes) {
      const L = lane(id);
      if (!L || L.done) continue;
      const b = $("[data-total] b", L.el);
      b.textContent = csText(cs(el));
      b.classList.add("ticking");
    }
  }, 60);
  clearTimeout(state.watchdog);
  state.watchdog = setTimeout(() => {
    toast(`${ICON("clock")}<span>本次对比超过 ${WATCHDOG_S} 秒仍未完成，已自动停止</span>`, 8000);
    stopRun("timeout");
  }, WATCHDOG_S * 1000);
  try {
    const res = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body, signal: ctrl.signal });
    if (!res.ok) throw new Error(await res.text());
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i);
        buf = buf.slice(i + 1);
        if (line.trim()) handle(JSON.parse(line), question);
      }
    }
  } catch (err) {
    if (!state.stopping) {
      for (const id of lanes) fail(id, "连接出错，请重新开始对比");
      console.error(err);
    }
  } finally {
    clearInterval(state.clock);
    clearTimeout(state.watchdog);
    clearTimeout(state.abortTimer);
    // lanes the stream never finished (stopped, timed out, or cut off) stop where they are
    for (const id of lanes) if (!lane(id).done) halt(id, state.stopping || "broken");
    for (const o of $$(".ticking")) o.classList.toggle("ticking", false);
    state.running = false;
    state.abort = null;
    $("#run").disabled = false;
    $("#run span").textContent = "开始对比";
    $("#stop").hidden = true;
  }
}

// Stop the active run: ask the server first (POST /api/stop ends every lane with finish {stopped});
// a server without that endpoint, or one that does not end the stream soon, is cut off here.
async function stopRun(reason = "stopped") {
  if (!state.running || state.stopping) return;
  state.stopping = reason;
  $("#stop").disabled = true;
  $("#stop span").textContent = "停止中…";
  for (const L of Object.values(state.lanes)) if (!L.done) pill(L.id, "停止中…", "warn");
  let accepted = false;
  try {
    const r = await fetch("/api/stop", { method: "POST" });
    accepted = r.ok;
  } catch {}
  clearTimeout(state.abortTimer);
  state.abortTimer = setTimeout(() => state.abort?.abort(), accepted ? 5000 : 0);
}

// A lane that ends without a complete answer: keep what it produced, freeze the stage it was in.
// reason: "stopped" (停止 button or the server), "timeout" (the page's watchdog), "broken" (the stream just ended)
const HALT = {
  stopped: { pill: "已停止", cls: "stop", total: "已停止", title: "已停止", sub: "本次对比已手动停止" },
  timeout: { pill: "超时", cls: "warn", total: "超时", title: "超时，已自动停止", sub: `超过 ${WATCHDOG_S} 秒未完成` },
  broken: { pill: "已中断", cls: "err", total: "—", title: "连接已中断", sub: "请重新开始对比" },
};
function freeze(L, totalText) {
  L.done = true;
  for (const li of $$(".rail li", L.el)) {
    clearInterval(L["sp_" + li.dataset.stage]);
    if (li.classList.contains("run")) { li.classList.remove("run"); li.classList.add("halted"); }
  }
  const tot = $("[data-total]", L.el);
  tot.classList.add("halted");
  const b = $("b", tot);
  b.classList.remove("ticking", "pop");
  b.textContent = totalText;
  L.flow.calm();
  L.el.classList.remove("running");
  L.el.classList.add("halted");
}
function halt(id, reason) {
  const L = lane(id);
  if (!L || L.done) return;
  const h = HALT[reason] || HALT.stopped;
  L.halted = reason;
  freeze(L, h.total);
  pill(id, h.pill, h.cls);
  if (L.text) renderAnswer(id, true);
  else $("[data-answer]", L.el).innerHTML = `<div class="ph ph--idle ph--halt"><i></i><b>${h.title}</b><span>${h.sub}</span></div>`;
}
function fail(id, message) {
  const L = lane(id);
  if (!L || L.done) return;
  L.halted = "error";
  freeze(L, "—");
  pill(id, "出错", "err");
  $("[data-answer]", L.el).innerHTML = `<span class="ph">${esc(message)}</span>`;
}

// the terminal strip: idle text until a run fills it with what the device computed; a new question clears it
const STRIP_IDLE = `<span class="device__idle">提问后，终端在本地生成查询向量、HMAC 检索令牌，并把目标向量密封给 HPIS —— 密钥从不离开终端</span>`;
function idleStrip() {
  if (!state.running && $("#device-chips .dchip")) $("#device-chips").innerHTML = STRIP_IDLE;
}
function dchip(label, value, code) {
  const d = document.createElement("span");
  d.className = "dchip";
  d.innerHTML = `<em>${esc(label)}</em>${code ? `<code>${esc(value)}</code>` : `<b>${esc(value)}</b>`}`;
  $("#device-chips").appendChild(d);
}
// notice events: a run-wide one goes to the terminal strip, a lane's own one above that lane's answer
function notice(laneId, text) {
  if (!text) return;
  const L = laneId ? lane(laneId) : null;
  if (L) {
    const box = $("[data-notice]", L.el);
    box.insertAdjacentHTML("beforeend", `<p>${ICON("info")}<span>${esc(text)}</span></p>`);
    box.hidden = false;
    return;
  }
  const d = document.createElement("span");
  d.className = "dchip dchip--notice";
  d.innerHTML = `${ICON("info")}<b>${esc(text)}</b>`;
  $("#device-chips").appendChild(d);
}

function handle(ev, question) {
  const id = ev.lane;
  const L = id ? lane(id) : null;
  const t = ev.t;
  if (id && !L) return;
  const unit = kindOf(state.run?.kind).unit;
  switch (ev.type) {
    case "plan": {
      const info = planInfo(ev);
      state.run = { ...state.run, ...info };
      if (info.kind !== state.kind || info.device !== state.device) applyKind(info.kind, info.device);
      if (info.kind === "photo") {
        const vision = modelVision();
        if (vision != null) for (const x of Object.values(state.lanes)) $("[data-mode]", x.el).textContent = vision ? "看图作答" : "依据照片描述作答";
      }
      if (ev.embed_ms != null) dchip("查询向量", `512 维 · ${ev.embed_ms.toFixed(0)} ms`);
      for (const c of (ev.chips || []).filter((c) => c.kind !== "语义")) dchip(c.kind, c.value);
      break;
    }
    case "notice": {
      notice(ev.lane, ev.text);
      break;
    }
    case "sealed": {
      const tokens = ev.tokens || [];
      dchip("SSE 检索令牌", tokens.length ? tokens[0].slice(0, 16) + "…" : "无", true);
      dchip("密封请求", bytesText(ev.bytes));
      setTicker("v2", `匿名标识 AID · 检索令牌 ${tokens.map((x) => x.slice(0, 12)).join(" ")} · 密封目标向量 ${ev.preview || ""} · 正文 AES-256-GCM 密文`);
      break;
    }
    case "stage": {
      if (!L || L.done) break;
      const f = L.flow;
      if (id === "plain" && ev.stage === "upload" && ev.state === "start") {
        stage(id, "upload", "start", t);
        f.node("dev", "active");
        f.node("cloud", "alert", "接收明文中", "bad");
        f.hot("edge", true);
        f.stream("edge", true, { kind: "plaintext", every: 45, dur: 700 });
      } else if (id === "plain" && ev.stage === "upload" && ev.state === "end") {
        f.stream("edge", false);
        f.hot("edge", false);
        stage(id, "upload", "end", t);
        stage(id, "retrieve", "start", t);
        f.node("cloud", "alert", "明文检索中", "bad");
      } else if (ev.stage === "request" && ev.state === "start") {
        stage(id, "request", "start", t);
        f.node("dev", "active");
        f.hot("edge", true);
        f.send("edge", { kind: "cipher", r: 4 });
        metric(id, "edge", ev.edge_up, "bytes");
        f.linkValue("edge", bytesText(ev.edge_up), "small");
      } else if (ev.stage === "generate" && ev.state === "first") {
        stage(id, "generate", "start", L.stageT.generate ?? t);
      }
      break;
    }
    case "upload": {
      if (L.done) break;
      metric(id, "edge", ev.edge_up, "bytes");
      L.flow.linkValue("edge", `${bytesText(ev.edge_up)} · ${ev.batch}/${ev.batches} 批`, "big");
      stage(id, "upload", "progress", t, ev.batch / ev.batches);
      break;
    }
    case "auth": {
      if (L.done) break;
      stage(id, "request", "end", t);
      L.flow.hot("edge", false);
      L.flow.node("hpic", "active", "票据有效 · 转发");
      const key = id === "v1" ? "fetch" : "search";
      stage(id, key, "start", t);
      L.flow.hot("cloudl", true);
      if (id === "v1") {
        L.flow.node("hpis", "active", "读取全部密文");
        L.flow.stream("cloudl", true, { reverse: true, kind: "cipher", every: 38, dur: 520 });
      } else {
        L.flow.node("hpis", "active", "SSE 筛选 + 密态向量");
        L.flow.send("cloudl", { kind: "cipher", r: 3.5, dur: 420 });
      }
      break;
    }
    case "hpis": {
      if (L.done) break;
      const key = id === "v1" ? "fetch" : "search";
      L.flow.stream("cloudl", false);
      L.flow.hot("cloudl", false);
      const cloud = ev.up_bytes + ev.down_bytes;
      metric(id, "cloud", cloud, "bytes");
      L.flow.linkValue("cloudl", `${bytesText(cloud)} · ${intText(ev.rows)} ${unit}`, id === "v1" ? "big" : "small");
      if (id === "v2") {
        L.flow.send("cloudl", { reverse: true, kind: "cipher", r: 3.5, dur: 420 });
        L.flow.node("hpis", "active", `命中 ${intText(ev.vector_rows)} → 取 ${ev.rows} ${unit}`, "hot");
      } else {
        L.flow.node("hpis", "active", `搬出 ${intText(ev.rows)} ${unit}密文`, "bad");
      }
      if (id === "v1") { stage(id, key, "end", t); stage(id, "decrypt", "start", t); }
      L.flow.node("hpic", "active", "解密中…");
      break;
    }
    case "decrypt": {
      if (L.done) break;
      const K = kindOf(state.run?.kind);
      L.decryptMs = ev.ms;
      L.decryptRows = ev.rows;
      L.flow.node("hpic", "active", `解密 ${intText(ev.rows)} ${K.unit}`, id === "v1" ? "bad" : "hot");
      setTicker(id, id === "v1"
        ? `HPIC 本次解密 ${intText(ev.rows)} 封邮件（${secsText(ev.ms)}），在明文中再检索；HPIS 侧仍只见密文`
        : `HPIC 本次仅解密 ${ev.rows} ${K.unit}命中${K.noun} · HPIS 只见匿名标识、HMAC 令牌与 AES-GCM 密文`);
      break;
    }
    case "search": { // plain cloud
      if (L.done) break;
      L.flow.node("cloud", "alert", `解析 ${intText(ev.scanned)} ${unit} · 现算向量 ${intText(ev.embedded ?? ev.candidates)} ${unit}`, "bad");
      break;
    }
    case "evidence": {
      if (L.done) break;
      L.evidence = ev.items || [];
      renderEvidence(id);
      // retrieval (whatever it took in this lane) ends when the evidence is ready; the model's part starts here
      const keys = L.cfg.stages.map(([k]) => k);
      stage(id, keys[keys.indexOf("generate") - 1], "end", t);
      stage(id, "generate", "start", t);
      $("[data-answer]", L.el).innerHTML = `<span class="ph">模型生成中…</span>`;
      break;
    }
    case "token": {
      if (L.done) break;
      L.text += ev.text;
      renderAnswer(id);
      break;
    }
    case "done": {
      L.llm = ev;
      break;
    }
    case "finish": {
      if (L.done) break;
      if (ev.stopped) { halt(id, state.stopping || "stopped"); break; }
      L.done = true;
      renderAnswer(id, true);
      const end = ev.total_ms ?? t;
      stage(id, "generate", "end", end);
      setTotal(id, end);
      metric(id, "edge", ev.edge_up, "bytes");
      if (L.m.cloud == null) metric(id, "cloud", id === "plain" ? 0 : null, "bytes");
      L.final = ev;
      L.q = quality(id, question);
      if (ev.timed_out) pill(id, "超时 · 已截断", "warn");
      else pill(id, L.llm?.replay ? "完成 · 回放" : L.llm?.fallback ? "完成 · 摘录" : "完成", "ok");
      L.el.classList.remove("running");
      L.el.classList.add("done", "flash");
      setTimeout(() => L.el.classList.remove("flash"), 1200);
      for (const o of $$(".metric output", L.el)) { o.classList.remove("pop"); void o.offsetWidth; o.classList.add("pop"); }
      if (id === "plain") L.flow.node("cloud", "alert", "已读取全部明文", "bad");
      break;
    }
    case "verify": {
      state.verify = ev;
      break;
    }
    case "error": {
      if (L) fail(id, ev.message);
      else toast(`${ICON("alert")}<span>${esc(ev.message)}</span>`, 8000);
      break;
    }
    case "end": {
      if (ev.stopped || state.stopping) {
        for (const x of Object.values(state.lanes)) if (!x.done) halt(x.id, state.stopping || "stopped");
        if (!state.stopping) toast(`${ICON("square")}<span>本次对比已停止</span>`);
        break;
      }
      verdict();
      break;
    }
  }
}

// ------------------------------------------------------------------ verdict
function fold(r) {
  if (!isFinite(r) || r <= 0) return "—";
  if (r >= 1e4) return `${(r / 1e4).toFixed(r >= 1e5 ? 0 : 1)}<small>万×</small>`;
  if (r >= 100) return `${Math.round(r)}<small>×</small>`;
  return `${r.toFixed(1)}<small>×</small>`;
}
function verdict() {
  const P = lane("plain"), V2 = lane("v2");
  if (!P?.final || !V2?.final) return;
  const kind = state.run?.kind || "mail";
  const K = kindOf(kind);
  // records of this kind: the plan says how many when the server sends it
  const total = state.run?.total ?? kindTotal(kind, state.run?.device);
  const tiles = [];
  // Upload per question, the measure the cover, the flow diagram and the cloud view all use.
  const pe = P.final.edge_up, ve = V2.final.edge_up;
  tiles.push({ big: fold(pe / ve), title: "上传云端减少", sub: `普通云 ${bytesText(pe)} → ${bytesText(ve)}` });
  if (V2.decryptRows && total) {
    const share = (100 * V2.decryptRows) / total;
    tiles.push({ big: `${share < 0.1 ? share.toFixed(2) : share.toFixed(1)}<small>%</small>`, title: "HPIS 只读命中密文", sub: `${V2.decryptRows} / ${intText(total)} ${K.unit} · 云内 ${bytesText(V2.m.cloud)}` });
  }
  if (P.final.total_ms && V2.final.total_ms) {
    const pt = cs(P.final.total_ms), vt = cs(V2.final.total_ms);
    const ratio = pt / Math.max(1, vt), sub = `普通云 ${csText(pt)} → ${csText(vt)}`;
    // Small collections (a watch or car log) upload in a blink: then the two lanes take about as long,
    // and the tile says so instead of "1.0× faster".
    if (ratio >= 1.1) tiles.push({ big: fold(ratio), title: "完整回答更快", sub });
    else if (ratio > 0.9) tiles.push({ big: `≈1<small>×</small>`, title: "完整回答用时相近", sub });
    else tiles.push({ big: fold(1 / ratio), title: "完整回答较慢", sub });
  }
  const vf = state.verify;
  if (vf && vf.plain_ms) {
    const noise = vf.overhead_pct < 0.5;
    const big = noise ? `≈0<small>%</small>` : `+${vf.overhead_pct.toFixed(1)}<small>%</small>`;
    const same = vf.identical ? "Top-10 与明文完全一致" : "结果与明文不同";
    const sub = noise
      ? `同题 ${vf.pairs} 次明密文配对，时延差异在测量误差内 · ${same}`
      : `同题 ${vf.pairs} 次配对：明文 ${vf.plain_p25_ms.toFixed(2)} ms · 密文 ${vf.cipher_p25_ms.toFixed(2)} ms · ${same}`;
    tiles.push({ big, title: "密态检索代价", sub });
  }
  tiles.push({ big: `0<small>${K.unit}</small>`, title: "存储侧可见明文", sub: `HPIC 可信区仅解密 ${V2.decryptRows || "K"} ${K.unit} · 普通云可读 ${intText(total)} ${K.unit}` });
  $("#verdict-grid").innerHTML = tiles.map((x, i) => `<div class="vstat" style="animation-delay:${i * 90}ms"><b>${x.big}</b><span>${x.title}</span><em>${esc(x.sub)}</em></div>`).join("");
  $("#verdict").hidden = false;
  requestAnimationFrame(() => {
    $("#verdict").classList.add("on");
    document.body.classList.add("scoreboard", "results");
    for (const id of ["plain", "v2"]) { const b = lane(id) && $("[data-answer]", lane(id).el); if (b) b.scrollTop = 0; }
  });
}

// ------------------------------------------------------------------ drawer
const drawer = { kind: "mail", id: null, plain: "", item: null };
function showDrawer(kind, id, html, item = null) {
  Object.assign(drawer, { kind, id, plain: html, item });
  $(".drawer__seg [data-view=plain]").textContent = kindOf(kind).view;
  const body = $("#drawer-body");
  body.innerHTML = html;
  body.scrollTop = 0;
  $$(".drawer__seg button").forEach((b) => b.classList.toggle("active", b.dataset.view === "plain"));
  $("#drawer").classList.add("on");
  $("#scrim").classList.add("on");
}
function openEvidence(laneId, index) {
  const L = lane(laneId);
  const e = L?.evidence[index];
  if (!e) return;
  const kind = e.kind || L.kind || "mail";
  if (kind === "mail") openMail(e.id, laneId);
  else if (kind === "photo") openPhoto(e);
  else openRecord(kind, e);
}
async function openMail(mailId, laneId) {
  const res = await fetch(`/api/mail/${mailId}`);
  if (!res.ok) return;
  const m = await res.json();
  const L = laneId ? lane(laneId) : null;
  const marks = L ? [...new Set((L.text.match(/[0-9][0-9.,:：%¥￥月日年\-–]*[0-9%日]|[¥￥][\d,.]+/g) || []).filter((x) => x.length >= 3))] : [];
  const mark = (s) => {
    let out = esc(s);
    for (const k of marks.sort((a, b) => b.length - a.length)) out = out.split(esc(k)).join(`<mark>${esc(k)}</mark>`);
    return out;
  };
  const people = (xs) => (xs || []).map((p) => `${esc(p.name)} &lt;${esc(p.email)}&gt;`).join("，");
  const plain = `
    <div class="mail">
      <h3>${esc(m.subject)}</h3>
      <div class="mail__meta"><span>发件</span><b>${people([m.from])}${m.from?.org ? `（${esc(m.from.org)}）` : ""}</b><span>收件</span><b>${people(m.to)}</b>${m.cc?.length ? `<span>抄送</span><b>${people(m.cc)}</b>` : ""}<span>时间</span><b>${esc(m.date.replace("T", " ").slice(0, 16))}</b></div>
      <div class="mail__body">${mark(m.body || "")}</div>
      ${(m.attachments || []).map((a) => `<div class="att"><div class="att__head">${ICON("paperclip")}${esc(a.name)}<span>${bytesText(a.size)}</span></div><div class="att__text ${/[|│┃]/.test(a.text || "") ? "tableish" : ""}">${mark(a.text || "")}</div></div>`).join("")}
    </div>`;
  showDrawer("mail", mailId, plain);
}
async function fetchRecord(kind, item) {
  try {
    return { ...item, ...(await getJSON(`/api/record/${kind}/${item.id}`)) };
  } catch {
    return item; // older servers: what the evidence event carried
  }
}
const meta = (rows) => `<div class="mail__meta">${rows.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<span>${k}</span><b>${v}</b>`).join("")}</div>`;
async function openPhoto(item) {
  const r = await fetchRecord("photo", item);
  const place = r.place && typeof r.place === "object" ? r.place : null;
  const where = [placeText(r.place), place && place.country && !placeText(r.place).includes(place.country) ? place.country : ""].filter(Boolean).join(" · ");
  const geo = place && place.lat != null ? `${Number(place.lat).toFixed(4)}, ${Number(place.lon).toFixed(4)}` : "";
  const dev = deviceOf(r.device || "phone");
  const src = r.source ? [r.source.photographer, r.source.dataset].filter(Boolean).join(" · ") : "";
  const html = `
    <div class="mail photo">
      <figure class="photo__img"><img src="${esc(photoUrl(r.id, "full"))}" alt="${esc(r.title)}"></figure>
      <h3>${esc(r.title)}</h3>
      ${meta([["时间", esc(String(r.date || "").replace("T", " ").slice(0, 16))], ["地点", esc(where)], ["坐标", esc(geo)], ["设备", `${ICON(dev.icon)} ${esc(dev.label)}`], ["画面", esc((r.tags || []).join("、"))], ["尺寸", r.width ? `${r.width} × ${r.height}${r.bytes ? ` · ${bytesText(r.bytes)}` : ""}` : ""], ["来源", esc(src)]])}
      <div class="mail__body">${esc(r.caption || r.summary || r.text || "")}</div>
    </div>`;
  showDrawer("photo", r.id, html, r);
}
const METRICS = {
  distance_km: ["距离", "km"], duration_min: ["时长", "分钟"], energy_kwh: ["耗电", "kWh"], avg_hr: ["平均心率", "次/分"],
  max_hr: ["最高心率", "次/分"], resting_hr: ["静息心率", "次/分"], calories: ["热量", "千卡"], kcal: ["热量", "千卡"], steps: ["步数", "步"],
  pace: ["配速", ""], pace_min_km: ["配速", "分/km"], speed_kmh: ["均速", "km/h"], avg_speed_kmh: ["均速", "km/h"], elevation_m: ["爬升", "m"],
  sleep_h: ["睡眠", "小时"], deep_sleep_h: ["深睡", "小时"], sessions: ["次数", "次"], count: ["次数", "次"], trips: ["行程", "次"],
  charges: ["充电", "次"], cost_yuan: ["费用", "元"], soc_from: ["起始电量", "%"], soc_to: ["结束电量", "%"], laps: ["趟数", "趟"],
};
const SKIP = new Set(["id", "kind", "device", "date", "title", "text", "summary", "fields", "metrics", "source", "file", "thumb", "width", "height", "bytes", "place", "caption", "tags", "from", "to", "activity", "event"]);
const fmtVal = (v) => (typeof v === "number" ? (Number.isInteger(v) ? intText(v) : String(+v.toFixed(2))) : esc(typeof v === "object" ? JSON.stringify(v) : v));
async function openRecord(kind, item) {
  const r = await fetchRecord(kind, item);
  const dev = deviceOf(r.device || deviceFor(kind));
  const nums = { ...(r.metrics || {}) };
  for (const [k, v] of Object.entries(r)) if (!SKIP.has(k) && (typeof v === "number" || typeof v === "string")) nums[k] = v;
  const kpis = Object.entries(nums)
    .map(([k, v]) => { const [label, u] = METRICS[k] || [k, ""]; return `<div class="kpi"><label>${esc(label)}</label><b>${fmtVal(v)}${u ? `<small>${esc(u)}</small>` : ""}</b></div>`; })
    .join("");
  const route = r.from || r.to ? `<div class="route">${ICON("map-pin")}<b>${esc(r.from || "—")}</b>${ICON("arrow-right")}<b>${esc(r.to || "—")}</b></div>` : "";
  const html = `
    <div class="mail recview">
      <h3>${esc(r.title)}</h3>
      ${meta([["时间", esc(String(r.date || "").replace("T", " ").slice(0, 16))], ["设备", `${ICON(dev.icon)} ${esc(dev.label)}`], ["类型", esc(r.activity || r.event || kindOf(kind).label)]])}
      ${route}
      ${kpis ? `<div class="kpis">${kpis}</div>` : ""}
      <div class="mail__body">${esc(r.text || r.summary || "")}</div>
    </div>`;
  showDrawer(kind, r.id, html, r);
}
// ciphertext bytes drawn as pixels: three bytes per pixel, nothing invented
function mosaic(hex) {
  const b = (String(hex).match(/[0-9a-f]{2}/gi) || []).map((h) => parseInt(h, 16));
  const side = Math.floor(Math.sqrt(Math.floor(b.length / 3)));
  if (side < 2) return null;
  const c = document.createElement("canvas");
  c.width = c.height = side;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(side, side);
  for (let i = 0; i < side * side; i++) img.data.set([b[i * 3], b[i * 3 + 1], b[i * 3 + 2], 255], i * 4);
  ctx.putImageData(img, 0, 0);
  return { url: c.toDataURL(), bytes: side * side * 3 };
}
async function cipherView() {
  const body = $("#drawer-body");
  const { kind, id } = drawer;
  const still = () => drawer.id === id && $(".drawer__seg .active")?.dataset.view === "cipher";
  body.innerHTML = `<div class="cipherview cipherview--wait">${ICON("database")}<span>正在读取 HPIS 存储文件…</span></div>`;
  let c;
  try {
    c = await getJSON(kind === "mail" ? `/api/cipher/${id}` : `/api/cipher/${id}?kind=${kind}`);
  } catch {
    if (still()) body.innerHTML = `<div class="cipherview"><p class="muted" style="font-family:var(--cn)">暂时无法读取 HPIS 中的这条记录。</p></div>`;
    return;
  }
  if (!still()) return;
  const K = kindOf(kind);
  let pic = "";
  if (kind === "photo") {
    const m = mosaic(c.image || c.body);
    pic = `<div class="cipherpic">
      <figure><img src="${esc(photoUrl(id, "thumb"))}" alt=""><figcaption>终端里的照片</figcaption></figure>
      ${ICON("arrow-right")}
      <figure>${m ? `<img class="mosaic" src="${m.url}" alt="">` : ""}<figcaption>HPIS 里的照片：${c.imageBytes ? bytesText(c.imageBytes) + " " : ""}AES-256-GCM 密文</figcaption></figure>
    </div>`;
  }
  body.innerHTML = `<div class="cipherview">${pic}
    <h5>匿名标识 AID</h5><div>${esc(c.aid)}</div>
    <h5>记录号</h5><div>${esc(c.rid)}</div>
    ${kind === "photo" && c.imageBytes ? `<h5>图像密文 AES-256-GCM</h5><div class="lbl">${bytesText(c.imageBytes)}</div>` : ""}
    <h5>${kind === "mail" ? "正文" : K.noun}密文 AES-256-GCM（${bytesText(c.bodyBytes)}，节选）</h5><div>${esc(c.body)}</div>
    <h5>SSE 检索令牌 HMAC-SHA256（${(c.tags || []).length} 个）</h5>${(c.tags || []).map((t) => `<div>${esc(t)}</div>`).join("")}
    <h5>向量密文 AES-256-GCM（${bytesText(c.vectorBytes)}，节选）</h5><div>${esc(c.vector)}</div>
    <p class="muted" style="font-family:var(--cn);margin-top:1.2rem">以上数据直接读取自 HPIS 的存储文件。HPIS 无法从中得知${kind === "mail" ? "发件人、主题、时间或内容" : kind === "photo" ? "拍摄地点、时间或画面" : "时间、地点或内容"}；只有持有密钥的终端，或终端授权的 HPIC 在本次请求内，才能打开命中的这几条。</p>
  </div>`;
}
function closeDrawer() { $("#drawer").classList.remove("on"); $("#scrim").classList.remove("on"); }

// ------------------------------------------------------------------ cloud view
function renderProv() {
  const b = state.status?.build;
  if (!b || !b.deployments) return;
  const tee = b.deployments.tee || {};
  const up = b.upload || {};
  // Upload per question: the whole mailbox versus one sealed request of about 3 KB.
  $("#prov").innerHTML = `
    <div class="prov__title">${ICON("shield-check")}<div>一次性加密预置<small>邮箱只在开通时加密上传一次</small></div></div>
    <div><b>${intText(b.emails)}</b><span>封邮件在终端加密后存入 HPIS</span></div>
    <div><b>${tee.seconds ? tee.seconds.toFixed(0) + " s" : "—"}</b><span>一次性预置用时：加密全部邮件，生成检索令牌与向量密文</span></div>
    <div><b>${bytesText(tee.store_bytes)}</b><span>HPIS 中的全部存储：邮件密文与密文索引</span></div>
    <div class="prov__x"><b>万倍+</b><div class="prov__cmp"><em>每次提问上传云端的数据量</em>
      <p><span>端侧直传普通云</span><i class="bad">${up.bytes ? bytesText(up.bytes) : "—"}</i><small>整个邮箱</small></p>
      <p><span>端侧+HPIC+HPIS</span><i class="good">约 3 KB</i><small>一次密封请求</small></p></div></div>`;
}
const cloud = { tenants: [], index: new Map(), hpis: null, busy: false };
const aidKey = (r) => r.aid_short || String(r.aid || "").slice(0, 8);
function tenantClass(key) {
  if (!cloud.index.has(key)) cloud.index.set(key, cloud.index.size);
  return `tc${cloud.index.get(key) % 10}`;
}
function plainRow(r) {
  const kind = KINDS[r.kind] ? r.kind : "mail";
  return { ...r, kind, device: r.device || deviceFor(kind), owner: r.owner || OWNER };
}
function loadCloudView() {
  cloud.loading = loadCloudTables();
  return cloud.loading;
}
async function loadCloudTables() {
  renderProv();
  const [plain, hp] = await Promise.all([getJSON("/api/cloudview?kind=plain&limit=60"), getJSON("/api/cloudview?kind=hpis&limit=60")]);
  state.plainRows = plain.rows.map(plainRow);
  renderPlainRows();
  const multi = state.devices.size > 0;
  const total = plain.total ?? state.status?.mailbox?.emails;
  $("#plain-count").textContent = multi ? `${intText(total)} 条 · 实名可读` : `${intText(total)} 封 · 全部可读`;
  renderPlainBar();
  cloud.hpis = hp;
  cloud.tenants = hp.tenants?.length ? hp.tenants : tenantsFromRows(hp.rows, hp);
  cloud.index = new Map(cloud.tenants.map((t, i) => [aidKey(t), i]));
  $("#hpis-count").textContent = `${intText(hp.docs)} 条密文 · ${bytesText(hp.bytes)} · ${intText(hp.postings)} 个令牌`;
  renderTenants();
  renderHpisRows(hp.rows);
  if (cloud.ownRows !== false) cloud.next = freshSample();
}
// another page of HPIS rows (offsets past a few thousand make the store page slow, so stay below)
function freshSample() {
  const offset = 60 + Math.floor(Math.random() * Math.max(1, Math.min(3000, (cloud.hpis?.docs || 3060) - 100)));
  return getJSON(`/api/cloudview?kind=hpis&limit=40&offset=${offset}`).then((x) => x.rows).catch(() => null);
}
function tenantsFromRows(rows, hp) {
  const seen = new Map();
  for (const r of rows) if (!seen.has(aidKey(r))) seen.set(aidKey(r), { aid: r.aid, aid_short: aidKey(r), records: 0 });
  if (seen.size === 1) [...seen.values()][0].records = hp.docs;
  else for (const r of rows) seen.get(aidKey(r)).records += 1;
  return [...seen.values()];
}
// what the ordinary cloud knows about the person behind the data: name, devices, kinds
function renderPlainBar() {
  const name = state.plainRows?.[0]?.owner || OWNER;
  const devs = state.devices.size ? DEVICE_ORDER.filter((d) => state.devices.has(d)) : ["pc"];
  $("#plain-bar").innerHTML = `<span class="vbar__who">${ICON("user")}<b>${esc(name)}</b></span>${devs
    .map((d) => {
      const dev = deviceOf(d), K = kindOf(dev.kind);
      const n = dev.kind === "mail" ? state.status?.mailbox?.emails : dev.records;
      return `<span class="vdev">${ICON(dev.icon)}${esc(dev.label)} · ${esc(K.label)}${n ? ` <b>${intText(n)}</b>` : ""}</span>`;
    })
    .join("")}<span class="vbar__tag">${ICON("eye")}实名可读</span>`;
}
// hits: rows the plain cloud's own search returned (the word may sit past the visible snippet)
function renderPlainRows(q = "", hits = null) {
  const rows = state.plainRows || [];
  const hl = (s) => (q ? esc(s).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(s));
  $("#plain-rows").innerHTML = rows
    .map((r) => {
      const dev = deviceOf(r.device), K = kindOf(r.kind);
      const hit = q && (hits?.has(r) || `${r.subject || ""}${r.snippet || ""}${r.from || ""}${r.title || ""}`.includes(q));
      return `<div class="row prow${hit ? " hit" : ""}">
        <div class="who"><b>${esc(r.owner)}</b><span>${ICON(dev.icon)}${esc(dev.label)} · ${esc(K.label)}</span><time>${esc(String(r.date || "").slice(0, 10))}</time></div>
        <div class="what"><div class="s">${hl(r.subject || r.title || "")}</div><div class="p">${r.from ? `<em>${hl(r.from)}</em>` : ""}${hl(r.snippet || r.summary || "")}</div></div>
        ${r.thumb ? `<img class="pthumb" src="${esc(r.thumb)}" alt="" loading="lazy">` : ""}
      </div>`;
    })
    .join("");
}
// which of the owner's devices a tenant is (status.devices[].aid against the tenant's AID or its short form)
function ownerOf(t) {
  const key = String(t.aid || aidKey(t)).replace(/[^0-9a-f]/gi, "");
  for (const d of DEVICE_ORDER) {
    const dev = deviceOf(d);
    if (dev.aid && key && (dev.aid.startsWith(key) || key.startsWith(dev.aid))) return `${OWNER} · ${dev.label}`;
  }
  return OWNER;
}
function renderTenants() {
  const T = cloud.tenants;
  const mine = T.filter((t) => t.mine);
  $("#hpis-bar").innerHTML = `
    <div class="tenants">${T.map((t) => `<span class="tchip ${tenantClass(aidKey(t))}${t.mine ? " mine" : ""}" title="${t.records != null ? `${intText(t.records)} 条密文` : ""}"><i></i>AID ${esc(aidKey(t))}${t.mine ? `<b class="own">${esc(ownerOf(t))}</b>` : ""}</span>`).join("")}</div>
    <span class="vbar__tag">${ICON("unlink")}匿名 · 不可关联</span>
    ${mine.length ? `<button class="reveal" id="reveal" type="button" title="揭晓" aria-label="揭晓">${ICON("fingerprint")}</button>` : ""}
    ${mine.length ? `<div class="reveal__note">${ICON("fingerprint")}<span>其中 ${mine.length} 个 AID 来自${OWNER}的 ${mine.length} 台设备，各用独立密钥 —— HPIS 无法把它们关联到同一个人</span></div>` : ""}`;
  $("#reveal")?.addEventListener("click", () => $("#pane-cloud").classList.toggle("revealed"));
}
function renderHpisRows(rows, fresh = false) {
  const mine = new Set(cloud.tenants.filter((t) => t.mine).map(aidKey));
  $("#hpis-rows").innerHTML = rows
    .map((r) => {
      const k = aidKey(r);
      return `<div class="row hrow ${tenantClass(k)}${mine.has(k) ? " mine" : ""}${fresh ? " fresh" : ""}">
        <div class="k aidc"><i></i>AID ${esc(k)}… #${esc(r.rid)}</div><div>${esc(r.body)}</div>
        ${r.tags ? `<div class="k">令牌</div><div>${esc(r.tags.join(" · "))}</div>` : ""}
        ${r.vector ? `<div class="k">向量</div><div>${esc(r.vector)}</div>` : ""}
        <span class="miss">${ICON("x")}未命中</span>
      </div>`;
    })
    .join("");
}
// a band of light down a table; rows it passes get `cls`
function sweep(host, ms, cls) {
  return new Promise((resolve) => {
    host.classList.add("scanning");
    const line = document.createElement("div");
    line.className = "scanline";
    host.appendChild(line);
    const rows = $$(".row", host);
    const top = host.scrollTop, H = host.clientHeight;
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / ms);
      const y = top + p * H;
      line.style.transform = `translateY(${y}px)`;
      if (cls) for (const r of rows) if (!r._swept && r.offsetTop + r.offsetHeight / 2 <= y) { r._swept = true; r.classList.add(cls); }
      if (p < 1) requestAnimationFrame(step);
      else { line.remove(); host.classList.remove("scanning"); resolve(); }
    };
    requestAnimationFrame(step);
  });
}
async function spy() {
  const q = $("#spy-q").value.trim();
  if (!q || cloud.busy) return;
  cloud.busy = true;
  $("#spy-go").disabled = true;
  const plainHost = $("#plain-rows"), hpisHost = $("#hpis-rows");
  $("#spy-result").innerHTML = `<span class="muted">两侧同时检索「${esc(q)}」…</span>`;
  $("#pane-cloud").classList.add("searching");
  $$(".vres", $("#pane-cloud")).forEach((x) => x.remove());
  try {
    await cloud.loading?.catch(() => {}); // the first page of both tables is on screen before a search replaces it
    let answered = false;
    const asked = (async () => {
      const r = await getJSON(`/api/spy?q=${encodeURIComponent(q)}`);
      // a fresh sample of ciphertext for every query, so the encrypted side visibly reacts;
      // servers whose /api/spy has no hpis_rows yet: another page of the store, fetched ahead
      let fresh = r.hpis_rows;
      cloud.ownRows = !fresh?.length;
      if (cloud.ownRows) fresh = (await (cloud.next || freshSample())) || cloud.hpis?.rows || [];
      cloud.next = cloud.ownRows ? freshSample() : null;
      return [r, fresh];
    })().finally(() => { answered = true; });
    // both sides keep scanning until the server answers (a new word means grepping hundreds of MB)
    const scan = async (host) => { do await sweep(host, 900); while (!answered); };
    const [[r, fresh]] = await Promise.all([asked, scan(plainHost), scan(hpisHost)]);
    // plain side: the hits come first, highlighted
    const samples = (r.samples || []).map(plainRow);
    const same = (a, b) => a.id === b.id && a.kind === b.kind;
    state.plainRows = samples.concat((state.plainRows || []).filter((x) => !samples.some((s) => same(s, x)))).slice(0, 80);
    renderPlainRows(q, new Set(state.plainRows.slice(0, samples.length)));
    plainHost.scrollTop = 0;
    const multi = state.devices.size > 0;
    const unit = multi ? "条" : "封";
    plainHost.insertAdjacentHTML("beforebegin", `<div class="vres vres--plain">${ICON("search")}<span>「${esc(q)}」命中 <b>${intText(r.plain_hits)}</b> ${unit}明文，原文与实名一览无余</span></div>`);
    // HPIS side: new rows, scanned one by one, nothing matches
    renderHpisRows(fresh, true);
    hpisHost.scrollTop = 0;
    await sweep(hpisHost, 1700, "missed");
    const scanned = r.hpis_scanned ?? cloud.hpis?.docs;
    hpisHost.insertAdjacentHTML("beforebegin", `<div class="vres vres--hpis"${r.note ? ` title="${esc(r.note)}"` : ""}><b class="zero">命中 ${intText(r.hpis_hits ?? 0)} 条</b><span>扫描 ${intText(scanned)} 条 · ${bytesText(r.hpis_bytes)} 密文</span><span class="why">${ICON("key")}没有终端密钥，无法生成检索令牌</span></div>`);
    $("#spy-result").innerHTML = `<span style="color:var(--risk)">普通云：命中 <b class="num">${intText(r.plain_hits)}</b> ${unit}明文</span> &nbsp;·&nbsp; <span style="color:var(--v2)">HPIS：命中 <b class="num">${intText(r.hpis_hits ?? 0)}</b> 条（扫描 ${bytesText(r.hpis_bytes)} 密文）</span>`;
  } catch (e) {
    $("#spy-result").innerHTML = `<span class="muted">检索失败，请重试</span>`;
    console.warn(e);
  } finally {
    $("#pane-cloud").classList.remove("searching");
    $("#spy-go").disabled = false;
    cloud.busy = false;
  }
}

// ------------------------------------------------------------------ benchmark
async function loadBench() {
  const data = await fetch("/api/acceptance").then((r) => r.json());
  const host = $("#bench");
  const sc = data.scenarios || [];
  if (!sc.length) {
    host.innerHTML = `<div class="bcard bcard--wide card"><h4>验收数据生成中</h4><p class="desc">Linux 验收基准尚未写入 acceptance/results/summary.json。</p></div>`;
    return;
  }
  const head = data.headline || {};
  const worst = Math.max(...sc.filter((s) => s.headline !== false).map((s) => s.overhead_pct));
  let html = `<div class="bhero card"><div class="bhero__big">${worst < 10 ? "&lt;10%" : worst.toFixed(1) + "%"}</div>
    <div class="bhero__txt"><h3>${esc(head.title || "全部密文检索场景的时延损耗均低于 10%")}</h3><p>${esc(head.subtitle || "")}</p></div></div>`;
  for (const s of sc.filter((x) => x.headline !== false)) {
    const ov = s.overhead_pct, ci = s.ci95 || [ov, ov];
    const scale = (v) => Math.max(0, Math.min(100, ((v + 2) / 14) * 100));
    html += `<div class="bcard card ${s.wide ? "bcard--wide" : ""}">
      <h4>${esc(s.name_zh)} ${s.tag ? `<span class="tag">${esc(s.tag)}</span>` : ""}</h4>
      <p class="desc">${esc(s.desc_zh || "")}</p>
      <div class="bover"><b class="${ov < 10 ? "good" : "warn"}">${ov >= 0 ? "+" : ""}${ov.toFixed(2)}%</b><span>95% 区间 ${ci[0].toFixed(1)} ~ ${ci[1].toFixed(1)}%</span></div>
      <div class="bbar"><div class="bbar__zero" style="left:${scale(0)}%"></div><div class="bbar__fill" data-w="${scale(ov)}"></div><div class="bbar__ci" style="left:${scale(ci[0])}%;width:${Math.max(0.5, scale(ci[1]) - scale(ci[0]))}%"></div><div class="bbar__limit" style="left:${scale(10)}%"><span>10% 上限</span></div></div>
      <div class="bnums"><div><label>明文均值</label><b>${s.plaintext_mean_ms.toFixed(3)} ms</b></div><div><label>密文均值</label><b>${s.cipher_mean_ms.toFixed(3)} ms</b></div><div><label>${esc(s.correctness_label || "正确性")}</label><b>${esc(s.correctness || "—")}</b></div></div>
    </div>`;
  }
  if (data.io) {
    const io = data.io;
    const mx = Math.log10(Math.max(...io.rows.map((r) => r.bytes)) + 1);
    const top = Math.max(...io.rows.map((r) => r.bytes));
    html += `<div class="bcard bcard--wide card"><h4>${esc(io.title || "每次查询读出的数据量")}<span class="tag">对数刻度</span></h4>${io.desc ? `<p class="desc">${esc(io.desc)}</p>` : ""}<div class="io">${io.rows
      .map((r) => `<div class="io__row"><span>${esc(r.label)}</span><div class="io__bar"><i data-w="${(Math.log10(r.bytes + 1) / mx) * 100}" style="background:${r.color || "var(--io-good)"}"></i></div><b>${bytesText(r.bytes)}${r.bytes < top ? `<em class="io__ratio">1/${intText(Math.round(top / Math.max(1, r.bytes)))}</em>` : ""}</b></div>`)
      .join("")}</div></div>`;
  }
  for (const n of data.notes || []) {
    const body = n.bullets ? `<ul class="notes">${n.bullets.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : `<div class="note">${n.html || esc(n.text)}</div>`;
    html += `<div class="bcard ${n.wide ? "bcard--wide" : ""} card"><h4>${esc(n.title)}</h4>${body}</div>`;
  }
  host.innerHTML = html;
  requestAnimationFrame(() => {
    for (const el of $$("[data-w]", host)) el.style.width = el.dataset.w + "%";
  });
}

// ------------------------------------------------------------------ architecture
function renderArch() {
  $("#arch").innerHTML = `
  <div class="arch__diagram card">${archSvg()}</div>
  <div class="arch__side">
    <div class="tech card"><h4>${ICON("key")}SSE 密文索引检索</h4><p>终端用私有密钥把机构、时间、类别等检索条件变成 <b>HMAC-SHA256 令牌</b>；HPIS 在倒排索引上直接匹配令牌，精确命中且不知道令牌含义。</p></div>
    <div class="tech card"><h4>${ICON("layers")}密态向量检索（软硬协同）</h4><p>向量以 <b>AES-256-GCM</b> 加密存放；目标向量密封给 HPIS 可信计算区，只在 TEE 内解开并精确排序，结果与明文检索<b>完全一致</b>。无硬件信任时可切换纯密码学 PPE 模式。</p></div>
    <div class="tech card"><h4>${ICON("lock")}纯密码学向量检索（PPE）</h4><p>不依赖可信硬件：终端用私有的随机旋转加有界随机扰动（<b>RAP</b>）变换向量，HPIS 直接在变换后的向量上比较距离、近似排序，全程不解密。实测 Top-10 召回约 <b>89%</b>（β=0.25）；也可多取 40 条交 HPIC 解密后精排，结果与明文<b>完全一致</b>。</p></div>
    <div class="tech card"><h4>${ICON("fingerprint")}匿名与最小暴露</h4><ul><li>每台设备一个匿名 AID，HPIS 无法把它们关联到同一人</li><li>HPIS 只见匿名标识 AID、令牌与密文</li><li>HPIC 凭匿名票据转发，仅解密本次命中的几条</li><li>数据密钥、索引密钥、向量密钥始终由终端掌控</li></ul></div>
  </div>
`;
}
// Four devices, each uploading its own data under its own anonymous AID (bottom), and the question path
// device → HPIC ⇄ HPIS (top). Colours and lines come from CSS variables, so both themes work.
function archSvg() {
  const use = (id, x, y, s, cls = "") => `<use href="${SPRITE}#${id}" x="${x}" y="${y}" width="${s}" height="${s}" class="ax-ic ${cls}"/>`;
  const devices = DEVICE_ORDER.map((id, i) => {
    const d = deviceOf(id);
    const kind = DEVICES[id].kind;
    return { ...d, kind, i, aidText: d.aid ? `AID ${d.aid.slice(0, 6)}…` : `匿名 AID ${i + 1}` };
  });
  const rowY = (i) => 296 + i * 104; // device and vault rows
  const RH = 92;
  const hex = ["9f3a c2e1 77b0 5d4e", "e08b 1c6f a93d 2b70", "4c17 d2a8 0e5f b391", "7a6e 3f02 c8d4 61b9"];
  const motion = (path, dur, begin, cls, r = 4.2) =>
    MOTION ? `<circle r="${r}" class="ax-dot ${cls}"><animateMotion dur="${dur}s" begin="${begin}s" repeatCount="indefinite" path="${path}"/></circle>` : "";
  // one dot that crosses `path` during [a, b] of a `cycle`-second loop: the question path runs in order ②③④⑤
  const pulse = (path, cycle, a, b, cls) =>
    MOTION
      ? `<circle r="5" class="ax-dot ${cls}" opacity="0"><animateMotion dur="${cycle}s" repeatCount="indefinite" path="${path}" keyPoints="0;0;1;1" keyTimes="0;${a};${b};1" calcMode="linear"/><animate attributeName="opacity" dur="${cycle}s" repeatCount="indefinite" values="0;0;1;1;0;0" keyTimes="0;${a};${a + 0.02};${b - 0.02};${b};1"/></circle>`
      : "";
  const arrow = (d, cls = "") => `<path d="${d}" class="ax-arrow ${cls}" marker-end="url(#ax-head)"/>`;
  const pipes = devices
    .map((d) => {
      const y = rowY(d.i) + RH / 2;
      const p = `M236 ${y} H700`;
      return `<path d="${p}" class="ax-pipe a${d.i + 1}"/><path d="${p}" class="ax-pipe-dash a${d.i + 1}" style="animation-delay:${-d.i * 0.35}s"/>
        ${motion(p, 3.2, -d.i * 0.8, `a${d.i + 1}`)}
        <g class="ax-aid a${d.i + 1}"><rect x="404" y="${y - 15}" width="132" height="30" rx="15"/><text x="470" y="${y + 5}" text-anchor="middle">${esc(d.aidText)}</text></g>`;
    })
    .join("");
  const unlinks = [0, 1, 2]
    .map((i) => { const y = rowY(i) + RH + 6; return `<g class="ax-ne"><circle cx="470" cy="${y}" r="11"/><text x="470" y="${y + 5}" text-anchor="middle">≠</text></g>`; })
    .join("");
  const devCards = devices
    .map((d) => {
      const y = rowY(d.i);
      const K = kindOf(d.kind);
      const n = d.kind === "mail" ? state.status?.mailbox?.emails : d.records;
      return `<g class="ax-dev a${d.i + 1}">
        <rect x="20" y="${y}" width="216" height="${RH}" rx="18" class="ax-card"/>
        <circle cx="66" cy="${y + RH / 2}" r="27" class="ax-devring"/>${use(d.icon, 52, y + RH / 2 - 14, 28, "ax-devic")}
        <text x="106" y="${y + 34}" class="ax-t1">${esc(d.label)}</text>
        <text x="106" y="${y + 57}" class="ax-t2">${K.short}${n ? ` · ${wanText(n)}${K.unit}` : ""}</text>
        <text x="106" y="${y + 78}" class="ax-t3">本地计算 · 本地存储</text>
      </g>`;
    })
    .join("");
  const vaults = devices
    .map((d) => {
      const y = rowY(d.i);
      return `<g class="ax-vault a${d.i + 1}">
        <rect x="700" y="${y}" width="264" height="${RH}" rx="16" class="ax-vbox"/>
        <rect x="700" y="${y}" width="6" height="${RH}" rx="3" class="ax-vbar"/>
        ${use("lock", 722, y + 20, 24, "ax-vic")}
        <text x="758" y="${y + 38}" class="ax-t1">${kindOf(d.kind).short}</text>
        <text x="758" y="${y + 62}" class="ax-t3 ax-mono">AES-256-GCM</text>
        <text x="878" y="${y + 30}" class="ax-hex">${hex[d.i].slice(0, 9)}</text><text x="878" y="${y + 50}" class="ax-hex">${hex[d.i].slice(10)}</text><text x="878" y="${y + 70}" class="ax-hex">${hex[(d.i + 1) % 4].slice(0, 9)}</text>
      </g>`;
    })
    .join("");
  return `<svg viewBox="0 0 1000 750" xmlns="http://www.w3.org/2000/svg" font-family="HarmonyOS Sans SC, PingFang SC, Microsoft YaHei, sans-serif" role="img" aria-label="方案架构：四台设备各以匿名 AID 加密上传，HPIC 机密计算，HPIS 匿名密态存储">
  <defs>
    <marker id="ax-head" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ax-head"/></marker>
    <linearGradient id="ax-hpic" x1="0" y1="0" x2="1" y2="1"><stop offset="0" class="ax-g-hpic0"/><stop offset="1" class="ax-g-hpic1"/></linearGradient>
    <linearGradient id="ax-hpis" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="ax-g-hpis0"/><stop offset="1" class="ax-g-hpis1"/></linearGradient>
    <linearGradient id="ax-sdk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" class="ax-g-sdk0"/><stop offset="1" class="ax-g-sdk1"/></linearGradient>
    <pattern id="ax-grid" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1.1" class="ax-griddot"/></pattern>
  </defs>
  <rect x="268" y="8" width="724" height="734" rx="26" class="ax-cloudzone"/>
  <rect x="268" y="8" width="724" height="734" rx="26" fill="url(#ax-grid)" class="ax-cloudgrid"/>
  <text x="128" y="26" text-anchor="middle" class="ax-zone">端 侧</text>
  <text x="630" y="34" text-anchor="middle" class="ax-zone">云 侧</text>

  <!-- device side: the SDK every device runs -->
  <rect x="20" y="44" width="216" height="210" rx="20" fill="url(#ax-sdk)" class="ax-box ax-box--sdk"/>
  <rect x="38" y="62" width="40" height="40" rx="12" class="ax-badge ax-badge--sdk"/>${use("key", 47, 71, 22, "ax-on")}
  <text x="90" y="80" class="ax-h2">端侧 SDK</text><text x="90" y="100" class="ax-t3">每台设备内置</text>
  <g class="ax-key"><rect x="38" y="118" width="180" height="32" rx="10"/><text x="128" y="139" text-anchor="middle">数据密钥 DEK</text></g>
  <g class="ax-key"><rect x="38" y="158" width="180" height="32" rx="10"/><text x="128" y="179" text-anchor="middle">检索密钥 SSE</text></g>
  <g class="ax-key"><rect x="38" y="198" width="180" height="32" rx="10"/><text x="128" y="219" text-anchor="middle">向量密钥 VEK</text></g>

  <!-- HPIC -->
  <rect x="316" y="44" width="290" height="210" rx="20" fill="url(#ax-hpic)" class="ax-box ax-box--hpic"/>
  <rect x="334" y="62" width="40" height="40" rx="12" class="ax-badge ax-badge--hpic"/>${use("cpu", 343, 71, 22, "ax-on")}
  <text x="386" y="80" class="ax-h1">HPIC</text><text x="386" y="100" class="ax-t3">个人智能计算区 · 无状态</text>
  <g class="ax-mod ax-mod--tee"><rect x="334" y="120" width="124" height="116" rx="14"/>${use("shield-check", 350, 134, 24, "ax-modic")}<text x="350" y="182" class="ax-t1">机密计算</text><text x="350" y="204" class="ax-t3">只解密命中</text><text x="350" y="223" class="ax-t3">的几条记录</text></g>
  <g class="ax-mod ax-mod--llm"><rect x="466" y="120" width="124" height="116" rx="14"/>${use("brain", 482, 134, 24, "ax-modic")}<text x="482" y="182" class="ax-t1">业务大模型</text><text x="482" y="204" class="ax-t3">Qwen / Gemma</text><text x="482" y="223" class="ax-t3">可信区内作答</text></g>

  <!-- HPIS -->
  <rect x="682" y="44" width="300" height="666" rx="22" fill="url(#ax-hpis)" class="ax-box ax-box--hpis"/>
  <rect x="700" y="62" width="40" height="40" rx="12" class="ax-badge ax-badge--hpis"/>${use("database", 709, 71, 22, "ax-on")}
  <text x="752" y="80" class="ax-h1">HPIS</text><text x="752" y="100" class="ax-t3">匿名私人智能存储</text>
  <g class="ax-cap"><rect x="700" y="118" width="264" height="42" rx="12"/>${use("key", 714, 129, 20, "ax-capic")}<text x="746" y="145" class="ax-t2b">SSE 密文索引</text></g>
  <g class="ax-cap"><rect x="700" y="168" width="264" height="42" rx="12"/>${use("layers", 714, 179, 20, "ax-capic")}<text x="746" y="195" class="ax-t2b">密态向量 TEE / PPE</text></g>
  <text x="832" y="240" text-anchor="middle" class="ax-t3">只见匿名 AID、令牌与密文</text>

  <!-- question path: ② sealed request → ③ search → ④ hits → ⑤ answer -->
  ${arrow("M238 122 H310")}<text x="274" y="110" text-anchor="middle" class="ax-lab">② 提问</text><text x="274" y="142" text-anchor="middle" class="ax-lab ax-mono">≈3 KB</text>
  ${arrow("M314 204 H242", "back")}<text x="278" y="194" text-anchor="middle" class="ax-lab">⑤ 回答</text>
  ${arrow("M608 122 H676")}<text x="644" y="110" text-anchor="middle" class="ax-lab">③ 检索</text><text x="644" y="142" text-anchor="middle" class="ax-lab ax-small">令牌·向量</text>
  ${arrow("M680 204 H612", "back")}<text x="646" y="194" text-anchor="middle" class="ax-lab">④ 命中</text><text x="646" y="224" text-anchor="middle" class="ax-lab ax-small">只取几条</text>
  ${pulse("M238 122 H310", 5.6, 0.02, 0.16, "q")}${pulse("M608 122 H676", 5.6, 0.22, 0.34, "q")}${pulse("M680 204 H612", 5.6, 0.42, 0.54, "q")}${pulse("M314 204 H242", 5.6, 0.62, 0.76, "q")}

  <!-- data path: one-time encrypted upload, one anonymous AID per device -->
  <text x="470" y="280" text-anchor="middle" class="ax-lab ax-lab--data">① 各设备以独立匿名 AID 加密上传</text>
  ${pipes}${unlinks}
  ${devCards}
  ${vaults}
  <text x="470" y="728" text-anchor="middle" class="ax-lab ax-lab--data">互不关联 · HPIS 无法得知它们属于同一人</text>
</svg>`;
}

// ------------------------------------------------------------------ status / boot
async function poll() {
  try {
    const s = await fetch("/api/status").then((r) => r.json());
    const first = !state.status;
    state.status = s;
    const hadDevices = state.devices.size;
    state.devices = new Map((s.devices || []).map((d) => [d.id, d]));
    const st = $("#status");
    const phases = { starting: "启动中", unpacking: "解压数据", building: "加密预置中", "starting-services": "启动服务", warming: "预热密态索引", ready: "服务就绪", error: "出错" };
    st.className = "status " + (s.phase === "ready" ? "ready" : s.phase === "error" ? "error" : "");
    $("span", st).textContent = phases[s.phase] || s.phase;
    if (s.mailbox) {
      renderMeta();
      $("[data-cover=emails]").textContent = intText(s.mailbox.emails);
      if (s.build?.upload?.bytes) $("[data-cover=upload]").textContent = bytesText(s.build.upload.bytes);
    }
    if (first || (s.presets && s.presets.length !== state.presets.length)) initPresets(s);
    if (first) initModels(s);
    if (first || hadDevices !== state.devices.size) onDevices();
    bootOverlay(s);
    if (s.phase !== "ready") { setTimeout(poll, s.phase === "error" ? 5000 : 800); return; }
    if (!state.loadedSide) { state.loadedSide = true; loadPlainTicker(); loadCipherTicker(); setInterval(scrambleCipher, 110); }
    setTimeout(poll, 20000); // heartbeat: a tab left open for days reconnects after a server restart
  } catch (e) {
    const st = $("#status");
    st.className = "status error";
    $("span", st).textContent = "重新连接中";
    setTimeout(poll, 3000);
  }
}
// a server with devices: questions about photos, sport and car records too
function onDevices() {
  renderArch();
  if (!state.devices.size) return;
  $("#question").placeholder = "向你的照片、邮件、运动与出行数据提问…";
  $("#cloud-sub").textContent = "同一个人的邮件、照片、运动与出行数据，在两种云里的真实存储形态。右侧数据直接读取自 HPIS 的存储文件。";
  if (!state.running) applyKind(state.kind, state.device);
}
function bootOverlay(s) {
  const boot = $("#boot");
  if (s.phase === "ready") { boot.classList.add("off"); return; }
  boot.classList.remove("off");
  const total = s.mailbox?.emails || 100000;
  const titles = { unpacking: "首次运行：解压邮箱与向量数据", building: "首次运行：在本机加密预置邮箱", "starting-services": "启动 HPIS / HPIC / 普通云服务", warming: "预热密态索引", error: "启动失败" };
  $("#boot-title").textContent = titles[s.phase] || "正在启动密态服务…";
  if (s.phase === "error") $("#boot-sub").textContent = s.error || "";
  const bars = [["tee", "HPIS 密态租户（AES-GCM + HMAC + 向量密文）"], ["plain", "明文对照租户"], ["upload", "普通云上传批次"]];
  $("#boot-bars").innerHTML = s.phase === "building" ? bars.map(([k, l]) => {
    const n = (s.progress || {})[k] || 0;
    return `<div class="bootbar"><label><span>${l}</span><span class="num">${intText(n)} / ${intText(total)}</span></label><div><i style="width:${Math.min(100, (n / total) * 100)}%"></i></div></div>`;
  }).join("") : "";
}
// the data strip right of the presets: what the device in play holds
function renderMeta() {
  const s = state.status;
  if (!s?.mailbox) return;
  const dev = state.device ? deviceOf(state.device) : null;
  const tag = dev ? `<span class="mdev">${ICON(dev.icon)}${esc(dev.label)}</span>` : "";
  if (state.kind === "mail" || !dev) {
    $("#mailbox-meta").innerHTML = `${tag}<span>${ICON("mail")} <b>${intText(s.mailbox.emails)}</b> 封邮件</span><span>${ICON("paperclip")} <b>${intText(s.mailbox.attachments)}</b> 个附件</span><span>${ICON("clock")} ${s.mailbox.first.slice(0, 7)} ~ ${s.mailbox.last.slice(0, 7)}</span>`;
    return;
  }
  const K = kindOf(state.kind);
  $("#mailbox-meta").innerHTML = `${tag}<span>${ICON(K.icon)} <b>${intText(dev.records)}</b> ${K.unit}${K.noun}</span>${dev.bytes ? `<span>${ICON("hard-drive")} ${bytesText(dev.bytes)}</span>` : ""}`;
}
const keyLabel = (i) => (i < 9 ? String(i + 1) : i === 9 ? "0" : "");
function initPresets(s) {
  const list = s.presets || [];
  const grouped = list.some((p) => p.device || p.kind);
  const devOf = (p) => p.device || deviceFor(p.kind || "mail");
  // shown grouped by device; the numbering (and the number keys) follow the order on screen
  let order = list;
  if (grouped) {
    const groups = new Map();
    for (const p of list) { const d = devOf(p); if (!groups.has(d)) groups.set(d, []); groups.get(d).push(p); }
    order = [...groups.values()].flat();
  }
  state.presets = order;
  const sens = /体检|账单|合同|学校/;
  const chip = (p, i) => `<button class="preset ${sens.test(p.chip) ? "sensitive" : ""}" data-i="${i}">${keyLabel(i) ? `<span class="k">${keyLabel(i)}</span>` : `<span class="k k--none"></span>`}${esc(p.chip)}</button>`;
  if (!grouped) {
    $("#presets").innerHTML = order.map(chip).join("");
  } else {
    let html = "", last = null;
    order.forEach((p, i) => {
      const d = devOf(p);
      if (d !== last) {
        if (last) html += "</span>";
        const dev = deviceOf(d);
        html += `<span class="pgroup"><span class="pdev" title="${esc(dev.label)} · ${esc(kindOf(dev.kind || DEVICES[d]?.kind).label)}">${ICON(dev.icon)}${esc(dev.label)}</span>`;
        last = d;
      }
      html += chip(p, i);
    });
    $("#presets").innerHTML = html + "</span>";
  }
  $("#presets").classList.toggle("grouped", grouped);
  if (state.presets.length && !$("#question").value) selectPreset(0);
}
// Dark by default; the light theme is one click away for bright rooms. The page sets the theme before first paint.
function initTheme() {
  const btn = $("#theme-btn");
  const root = document.documentElement;
  const sync = () => {
    btn.title = root.dataset.theme === "dark" ? "切换到浅色" : "切换到深色";
    btn.setAttribute("aria-label", btn.title);
  };
  btn.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("hpis-theme", root.dataset.theme); } catch {}
    sync();
  });
  sync();
}
function selectPreset(i) {
  const p = state.presets[i];
  if (!p) return;
  $("#question").value = p.question;
  $$(".preset").forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === i));
  idleStrip();
  if (!state.running && (p.kind || p.device)) applyKind(p.kind || DEVICES[p.device]?.kind || "mail", p.device || deviceFor(p.kind));
}
function initModels(s) {
  const sel = $("#model");
  sel.innerHTML = (s.models || []).map((m) => `<option value="${m.id}">${esc(m.label)}</option>`).join("");
  state.model = s.defaultModel && s.models.some((m) => m.id === s.defaultModel) ? s.defaultModel : s.models?.[0]?.id;
  sel.value = state.model;
  for (const L of Object.values(state.lanes)) $("[data-model]", L.el).textContent = modelLabel();
}
async function loadCipherTicker() {
  try {
    const r = await fetch("/api/cloudview?kind=hpis&limit=6").then((x) => x.json());
    state.cipherTicker = "HPIS 中的真实存储：" + r.rows.map((x) => `AID ${String(x.aid || x.aid_short).slice(0, 10)} · 正文 ${x.body.slice(0, 40)} · 令牌 ${(x.tags?.[0] || "").slice(0, 24)}`).join("　｜　");
    if (!state.running) setTicker("v2");
  } catch {}
}
// Hex in the encrypted lane's ticker keeps changing, like ciphertext under a new key.
function scrambleCipher() {
  const L = lane("v2");
  if (!L || document.hidden) return;
  const tr = $("[data-ticker]", L.el);
  const text = tr.textContent;
  if (tr._src !== text) {
    tr._hex = [];
    for (const m of text.matchAll(/[0-9a-f]{8,}/g)) for (let i = 0; i < m[0].length; i++) tr._hex.push(m.index + i);
  }
  if (!tr._hex.length) { tr._src = text; return; }
  const chars = [...text];
  for (let k = 0; k < 8; k++) chars[tr._hex[(Math.random() * tr._hex.length) | 0]] = "0123456789abcdef"[(Math.random() * 16) | 0];
  tr.textContent = chars.join("");
  tr._src = tr.textContent;
}
async function loadPlainTicker() {
  try {
    const r = await fetch("/api/cloudview?kind=plain&limit=10&seed=3&sensitive=1").then((x) => x.json());
    const sens = state.status?.mailbox?.sensitive || {};
    const head = Object.values(sens).filter((x) => x.count).map((x) => `${x.label} ${intText(x.count)} 封`).join(" · ");
    const mails = r.rows.filter((x) => !x.kind || x.kind === "mail");
    state.plainTicker = (head ? `普通云可读取：${head}　｜　` : "") + mails.map((x) => `${x.from ? `【${x.from}】` : ""}${x.subject}：${String(x.snippet || "").slice(0, 40)}`).join("　·　");
    if (!state.running) setTicker("plain");
  } catch {}
}

// ------------------------------------------------------------------ recording
// Records this tab to a video file with the browser's own screen capture. Browsers allow
// that only on a secure page (https or 127.0.0.1), so the demo also serves an https port.
const rec = { recorder: null, stream: null, chunks: [], t0: 0, timer: 0, mime: "" };
function toast(html, ms = 6000) {
  const t = $("#toast");
  t.innerHTML = html;
  t.classList.add("on");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("on"), ms);
}
function recMime() {
  const types = ["video/mp4;codecs=avc1.640033", "video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  return window.MediaRecorder ? types.find((t) => MediaRecorder.isTypeSupported(t)) || "" : "";
}
function recHelp() {
  const port = state.status?.httpsPort;
  const secureUrl = port ? `https://${location.hostname}:${port}/` : "";
  const alt = `<p class="alt">也可以用 Windows 自带的录屏：<kbd>Win</kbd> + <kbd>Alt</kbd> + <kbd>R</kbd>（Xbox Game Bar，录制当前窗口），或 <kbd>Win</kbd> + <kbd>Shift</kbd> + <kbd>R</kbd>（截图工具录屏）。</p>`;
  let body;
  if (window.isSecureContext) {
    body = `<p class="muted">这个浏览器不支持网页录屏，请用新版 Chrome 或 Edge 打开本页。</p>${alt}`;
  } else if (secureUrl) {
    body = `<p class="muted">浏览器只允许在安全地址（https）上录制网页。当前地址 <b>${esc(location.origin)}</b> 不是，请换到录制地址：</p>
    <a class="btn btn--primary" href="${secureUrl}">${ICON("external-link")}打开录制地址 ${esc(secureUrl)}</a>
    <ol><li>第一次打开会提示“您的连接不是私密连接”：点 <b>高级</b> → <b>继续前往</b>（证书由演示服务自己签发）。</li>
    <li>在新页面右上角点 <b>录制视频</b>，选择 <b>此标签页</b> → <b>共享</b>，倒数 3 秒后开始录制。</li>
    <li>演示结束点 <b>停止录制</b>，视频自动保存到“下载”文件夹。</li></ol>${alt}`;
  } else {
    body = `<p class="muted">浏览器只允许在安全地址（https）上录制网页，而演示服务没有开启 https 录制地址。</p>${alt}`;
  }
  $("#recpop-body").innerHTML = body;
  $("#recpop").hidden = false;
}
function countdown(n) {
  return new Promise((resolve) => {
    const box = $("#countdown");
    const num = $("#countdown-n");
    box.hidden = false;
    const step = (k) => {
      if (k === 0) { box.hidden = true; setTimeout(resolve, 250); return; }
      num.textContent = k;
      num.style.animation = "none";
      void num.offsetWidth; // restart the pop animation
      num.style.animation = "";
      setTimeout(() => step(k - 1), 1000);
    };
    step(n);
  });
}
async function startRecording() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getDisplayMedia || !window.MediaRecorder) { recHelp(); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: { ideal: 30 }, width: { max: 3840 }, height: { max: 2160 } },
      audio: false,
      preferCurrentTab: true,
      selfBrowserSurface: "include",
      surfaceSwitching: "exclude",
      monitorTypeSurfaces: "exclude",
    });
  } catch {
    return; // the share picker was cancelled
  }
  await countdown(3); // the countdown itself is not in the video
  rec.mime = recMime();
  rec.chunks = [];
  rec.stream = stream;
  rec.recorder = new MediaRecorder(stream, { mimeType: rec.mime || undefined, videoBitsPerSecond: 12000000 });
  rec.recorder.ondataavailable = (e) => { if (e.data.size) rec.chunks.push(e.data); };
  rec.recorder.onstop = saveRecording;
  stream.getVideoTracks()[0].addEventListener("ended", stopRecording); // 「停止共享」 in the browser bar
  rec.recorder.start(1000);
  rec.t0 = performance.now();
  $("#rec-btn").classList.add("on");
  const show = () => {
    const sec = Math.floor((performance.now() - rec.t0) / 1000);
    $("#rec-label").textContent = `停止录制 ${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
  };
  show();
  rec.timer = setInterval(show, 500);
}
function stopRecording() {
  clearInterval(rec.timer);
  if (rec.recorder && rec.recorder.state !== "inactive") rec.recorder.stop();
  rec.stream?.getTracks().forEach((t) => t.stop());
  $("#rec-btn").classList.remove("on");
  $("#rec-label").textContent = "录制视频";
}
function saveRecording() {
  const type = (rec.mime || "video/webm").split(";")[0];
  const blob = new Blob(rec.chunks, { type });
  rec.chunks = [];
  rec.recorder = null;
  rec.stream = null;
  if (!blob.size) return;
  const d = new Date();
  const p2 = (n) => String(n).padStart(2, "0");
  const name = `面向个人敏感数据的智能问答-${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}.${type.includes("mp4") ? "mp4" : "webm"}`;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  toast(`${ICON("download")}<span>视频已保存到“下载”文件夹：${esc(name)}（${bytesText(blob.size)}）</span>`, 8000);
}
function initRecording() {
  $("#rec-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    if (rec.recorder) stopRecording(); else startRecording();
  });
  document.addEventListener("click", (e) => { if (!$("#recpop").contains(e.target)) $("#recpop").hidden = true; });
}

// ------------------------------------------------------------------ wiring
function initNet() {
  const sync = () => {
    state.net = {
      edge: { gbps: Number($("#edge-gbps").value), rtt_ms: Number($("#edge-rtt").value) },
      cloud: { gbps: Number($("#cloud-gbps").value), rtt_ms: Number($("#cloud-rtt").value) },
    };
    const g = (v) => (v >= 1 ? `${v} Gbps` : `${v * 1000} Mbps`);
    $("#net-label").textContent = `端云 ${g(state.net.edge.gbps)} · 云云 ${g(state.net.cloud.gbps)}`;
  };
  for (const id of ["#edge-gbps", "#edge-rtt", "#cloud-gbps", "#cloud-rtt"]) $(id).addEventListener("change", sync);
  $("#net-btn").addEventListener("click", (e) => { e.stopPropagation(); $("#netpop").hidden = !$("#netpop").hidden; });
  document.addEventListener("click", (e) => { if (!$("#netpop").contains(e.target)) $("#netpop").hidden = true; });
  sync();
}
const coverOn = () => !$("#cover").classList.contains("off");
// 主页 shows the cover again, its entrance replayed
function showCover() {
  const cover = $("#cover");
  cover.classList.remove("off");
  for (const el of $$(".cover__kicker, .ln, .cover__sub, .cover__stats, #enter, .cover__visual, .cert, .cover__credit", cover)) {
    el.style.animation = "none";
    void el.offsetWidth;
    el.style.animation = "";
  }
}
function hideCover(tab) {
  $("#cover").classList.add("off");
  if (tab) state.showTab(tab);
}
function initTabs() {
  const show = (tab) => {
    if (tab === "home") { showCover(); return; }
    $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + tab));
    if (tab === "cloud" && !state.cloudLoaded) { state.cloudLoaded = true; loadCloudView().catch((e) => { state.cloudLoaded = false; console.warn(e); }); }
    if (tab === "bench") loadBench();
  };
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  state.showTab = show;
}
// The race fills the window below the top bar; these keep that true whatever the bar and the scoreboard measure.
function initLayout() {
  const root = document.documentElement.style;
  const ro = new ResizeObserver(() => {
    root.setProperty("--top-h", `${$(".topbar").offsetHeight}px`);
    root.setProperty("--verdict-h", `${$("#verdict").offsetHeight}px`);
  });
  ro.observe($(".topbar"));
  ro.observe($("#verdict"));
}

function init() {
  initTabs();
  initNet();
  initRecording();
  initLayout();
  buildLanes();
  renderArch();
  initTheme();
  $("#enter").addEventListener("click", () => hideCover("race"));
  $("#run").addEventListener("click", run);
  $("#stop").addEventListener("click", () => stopRun("stopped"));
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  $("#question").addEventListener("input", () => { $$(".preset").forEach((b) => b.classList.remove("active")); idleStrip(); });
  $("#presets").addEventListener("click", (e) => { const b = e.target.closest(".preset"); if (b) selectPreset(Number(b.dataset.i)); });
  $("#model").addEventListener("change", (e) => {
    state.model = e.target.value;
    // An answer on screen keeps the name of the model that wrote it; the new one shows from the next run.
    for (const L of Object.values(state.lanes)) {
      if (!L.el.classList.contains("running") && !L.el.classList.contains("done")) $("[data-model]", L.el).textContent = modelLabel();
    }
  });
  $("#lanes").addEventListener("click", (e) => {
    const laneEl = e.target.closest(".lane");
    if (!laneEl) return;
    const c = e.target.closest(".cite");
    if (c) { openEvidence(laneEl.dataset.lane, Number(c.dataset.n) - 1); return; }
    const item = e.target.closest("[data-evlist] [data-i]");
    if (item) openEvidence(laneEl.dataset.lane, Number(item.dataset.i));
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  $$(".drawer__seg button").forEach((b) => b.addEventListener("click", () => {
    $$(".drawer__seg button").forEach((x) => x.classList.toggle("active", x === b));
    if (b.dataset.view === "cipher") cipherView(); else $("#drawer-body").innerHTML = drawer.plain;
  }));
  $("#spy-go").addEventListener("click", spy);
  $("#verdict-close").addEventListener("click", () => { $("#verdict").classList.remove("on"); document.body.classList.remove("scoreboard"); });
  $("#lanes").addEventListener("dblclick", () => document.body.classList.toggle("results"));
  $("#spy-q").addEventListener("keydown", (e) => { if (e.key === "Enter") spy(); });
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.key === "Escape") { closeDrawer(); hideCover(); }
    if (/^[0-9]$/.test(e.key)) selectPreset(e.key === "0" ? 9 : Number(e.key) - 1);
    if (e.key === "Enter") { if (coverOn()) hideCover("race"); else run(); }
    if (e.key === "f") document.documentElement.requestFullscreen?.();
  });
  poll();
}
init();
