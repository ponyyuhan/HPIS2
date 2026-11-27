// 个人智能计算平台2.0 — front-end
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
  mode: "single", // 单端问答 | 多端协同 ("multi", when the server offers status.multi)
  multi: null, // status.multi: { presets, scenarios }
  single: [], // the 单端问答 presets in screen order; state.presets is the current mode's list
  mpreset: null, // the selected 多端协同 preset
  scen: "sync", // the HPIC 1.0 scenario of 多端协同: "sync" (场景一, via the phone) | "direct" (场景二, one device)
  pick: { sync: null, direct: null }, // its device choice: the devices synced to the phone / the one device that uploads
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
// Stage times: each boundary is rounded to 1 ms once and a stage is the gap between two rounded
// boundaries, so the stages on screen add up exactly to the total. Milliseconds, because the
// encrypted search takes only a few of them (at 0.01 s it would read 0.00 s).
const msr = (ms) => Math.round(ms);
const msText = (m) => `${(m / 1000).toFixed(3)} s`;

// ------------------------------------------------------------------ lane config
// Two generations of the same platform (SPEC §1-2): HPIC 1.0 keeps no data, so every question carries
// the whole collection; HPIC 2.0 stores each device's data once in HPIS and searches it encrypted.
const LANES = {
  plain: {
    title: "端侧直传机密计算平台HPIC1.0", tag: "1.0", icon: "cloud-upload",
    ribbon: "HPIC 1.0 · 每次提问上传全部数据", ribbonIcon: "cloud-upload",
    desc: "每次提问由手机把同步来的整个邮箱上传到 HPIC 1.0，在机密计算环境内现场检索并回答",
    stages: [["upload", "上传邮箱"], ["retrieve", "云端检索"], ["generate", "模型作答"]],
    nodes: [["dev", 70, "smartphone", "手机", "已同步 10 万封邮件"], ["cloud", 486, "cpu", "HPIC 1.0", "机密计算"]],
    links: [["edge", "dev", "cloud", "邮件与附件 · 分批上传"]],
  },
  v2: {
    title: "HPIC2.0：<wbr>端侧+<wbr>机密计算平台+<wbr>安全存储平台", tag: "2.0", icon: "shield-check", // <wbr>: wraps after a + only
    ribbon: "HPIC 2.0 · 一次加密存储，按需密态检索", ribbonIcon: "database",
    desc: "SSE 在密文索引上精确筛选，向量在可信计算区内精确排序，只取回命中的几封密文",
    stages: [["request", "加密上传"], ["search", "密态检索"], ["generate", "模型作答"]],
    nodes: [["dev", 56, "smartphone", "手机", "掌控密钥"], ["hpic", 290, "cpu", "HPIC", "机密计算平台"], ["hpis", 512, "database", "HPIS", "安全存储平台"]],
    links: [["edge", "dev", "hpic", "推理请求 + 目标索引密文"], ["cloudl", "hpic", "hpis", "部分数据密文"]],
  },
};
// 多端协同 (SPEC-scenarios): HPIC 1.0 in one of two scenarios (left), HPIC 2.0 as 场景三 (right).
// Each lane draws its devices instead of the two-node flow; texts that depend on the scenario are set in applyMulti.
const MULTI = {
  plain: { ...LANES.plain, stages: [["upload", "上传数据"], ["retrieve", "现场检索"], ["generate", "模型作答"]] },
  v2: {
    ...LANES.v2,
    ribbon: "场景三 · HPIC 2.0",
    desc: "各设备的数据已加密存入 HPIS。手机只发一次密封请求，HPIC 2.0 按设备逐个密态检索",
  },
};
// The two HPIC 1.0 scenarios (status.multi.scenarios; this copy only for a server that does not send them yet)
const SCENARIOS = [
  { id: "sync", label: "场景一 · 先同步到手机", issue: "性能差", route: "phone", choose: "many", devices: ["watch", "pc", "car"], default: ["watch", "pc", "car"] },
  { id: "direct", label: "场景二 · 单设备直传", issue: "准确性差", route: "direct", choose: "one", devices: ["watch", "pc", "car"], default: ["watch"] },
];
const scenarios = () => (state.multi?.scenarios?.length ? state.multi.scenarios : SCENARIOS);
const scenario = () => scenarios().find((s) => s.id === state.scen) || scenarios()[0];
// what HPIC 1.0 gets in the chosen scenario: route "phone" → the phone and the devices synced to it; "direct" → one device
function hpic1Pick() {
  const sc = scenario();
  const pick = state.pick[sc.id] || sc.default || [];
  const route = sc.route || (sc.choose === "one" ? "direct" : "phone");
  return { sc, route, devices: route === "direct" ? pick.slice(0, 1) : ["phone", ...pick] };
}
// the phone is node "dev" in every topology, so the run events that light up "dev" work in both modes
const nid = (d) => (d === "phone" ? "dev" : d);
// Topologies (one 480 x 160 drawing): the phone (主设备) always sits on top, apart from the column of watch, PC
// and car; HPIC top right. Device nodes are small with their text on the right, the others wide pills.
const TOPO_VIEW = "0 -2 480 162";
const COL = [["watch", 68], ["pc", 106], ["car", 144]];
const PHONE = ["dev", 214, "smartphone", "手机（主设备）", "", { y: 22, wide: 140 }];
// 场景一: watch, PC and car sync to the phone; the phone uploads everything to HPIC 1.0 for every question
function topoSync() {
  const nodes = [PHONE, ["cloud", 420, "cpu", "HPIC 1.0", "机密计算", { y: 22, wide: 120 }]];
  const links = [["edge", "dev", "cloud", "每次提问上传", { from: [286, 22], to: [358, 22], lpos: [322, 14], vpos: [322, 40] }]];
  for (const [d, y] of COL) {
    nodes.push([d, 20, DEVICES[d].icon, DEVICES[d].label, "", { y, small: true }]);
    links.push([`s_${d}`, d, "dev", "", { from: [122, y - 8], to: [168 + (y - 68) / 3, 44], cls: "sync" }]);
  }
  return { view: TOPO_VIEW, nodes, links };
}
// 场景二: one device uploads only its own data straight to HPIC 1.0; the phone and the other devices are not involved
function topoDirect(dev) {
  const nodes = [PHONE, ["cloud", 420, "cpu", "HPIC 1.0", "机密计算", { y: 22, wide: 120 }]];
  const links = [];
  for (const [d, y] of COL) {
    nodes.push([d, 20, DEVICES[d].icon, DEVICES[d].label, "", { y, small: true }]);
    if (d === dev) links.push(["edge", d, "cloud", "", { from: [122, y], to: [372, 44], vpos: [250, y - (y - 44) * 0.51 + 22] }]);
  }
  return { view: TOPO_VIEW, nodes, links };
}
// 场景三: every device stored its data once in HPIS under its own AID (the phone its photos too); the phone asks
// HPIC 2.0 with one sealed request, and HPIC 2.0 searches each device's data in HPIS
function topoV2() {
  const nodes = [PHONE, ["hpic", 420, "cpu", "HPIC 2.0", "机密计算平台", { y: 22, wide: 120 }], ["hpis", 420, "database", "HPIS", "安全存储平台", { y: 106, wide: 120 }]];
  const links = [
    ["edge", "dev", "hpic", "密封请求", { from: [286, 22], to: [358, 22], lpos: [322, 14], vpos: [322, 40] }],
    ["cloudl", "hpic", "hpis", "密态检索", { from: [420, 44], to: [420, 84], lpos: [452, 62], vpos: [452, 78] }],
    ["s_phone", "dev", "hpis", "", { from: [232, 44], to: [372, 84], cls: "store" }],
  ];
  for (const [d, y] of COL) {
    nodes.push([d, 20, DEVICES[d].icon, DEVICES[d].label, "", { y, small: true }]);
    links.push([`s_${d}`, d, "hpis", "", { from: [122, y], to: [358, 106 + (y - 106) / 3], cls: "store" }]);
  }
  return { view: TOPO_VIEW, nodes, links };
}

// ------------------------------------------------------------------ flow diagram
const flows = new Set();
class Flow {
  constructor(host, cfg) {
    this.svg = document.createElementNS(NS, "svg");
    // the drawing spans y 16..125; cropping the empty margins draws it larger in the same height
    this.svg.setAttribute("viewBox", cfg.view || "0 12 568 116");
    this.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    host.appendChild(this.svg);
    this.nodes = {};
    this.links = {};
    this.packets = [];
    this.streams = {};
    this.timers = [];
    this.glows = {};
    // a node: [id, x, icon, label, sub, {y, small}]; a big node is a 72 x 56 box, a small one 30 x 30 with its text on the right
    const pos = Object.fromEntries(cfg.nodes.map((n) => [n[0], { x: n[1], y: n[5]?.y ?? 58 }]));
    for (const [id, a, b, label, o = {}] of cfg.links) {
      const [x1, y1] = o.from || [pos[a].x + 40, pos[a].y];
      const [x2, y2] = o.to || [pos[b].x - 40, pos[b].y];
      const g = this.el("g", {});
      const base = this.el("path", { d: `M${x1} ${y1} L${x2} ${y2}`, class: `link${o.cls ? " link--" + o.cls : ""}` }, g);
      const dash = this.el("path", { d: `M${x1} ${y1} L${x2} ${y2}`, class: "link-dash" }, g);
      const [lx, ly] = o.lpos || [(x1 + x2) / 2, (y1 + y2) / 2 - 14];
      const lab = this.el("text", { x: lx, y: ly, class: "llabel" }, g);
      lab.textContent = label;
      const [vx, vy] = o.vpos || [(x1 + x2) / 2, (y1 + y2) / 2 + 24];
      const val = this.el("text", { x: vx, y: vy, class: "lbytes" }, g);
      this.links[id] = { x1, x2, y1, y2, base, dash, lab, val };
    }
    for (const [id, x, icon, label, sub, o = {}] of cfg.nodes) {
      const y = o.y ?? 58;
      if (o.wide) {
        // a wide pill (多端协同): icon on the left, label and status inside
        const [w, h] = [o.wide, 40];
        const g = this.el("g", { class: "node node--w", transform: `translate(${x - w / 2} ${y - h / 2})` });
        this.el("rect", { width: w, height: h, rx: 12, class: "ripple" }, g);
        this.el("rect", { width: w, height: h, rx: 12, class: "box" }, g);
        const use = this.el("use", { href: `${SPRITE}#${icon}`, x: 10, y: 10, width: 20, height: 20 }, g);
        use.setAttribute("style", "fill:none;stroke:currentColor");
        const l = this.el("text", { x: 38, y: 17, class: "nlabel nlabel--w" }, g);
        l.textContent = label;
        const s = this.el("text", { x: 38, y: 32, class: "nsub nsub--w" }, g);
        s.textContent = sub;
        this.nodes[id] = { g, s, sub, subcls: "", use, l, base: "nsub nsub--w" };
        continue;
      }
      const small = !!o.small;
      const [w, h, r, ic] = small ? [30, 30, 9, 18] : [72, 56, 16, 28];
      const g = this.el("g", { class: `node${small ? " node--s" : ""}`, transform: `translate(${x - w / 2} ${y - h / 2})` });
      this.el("rect", { width: w, height: h, rx: r, class: "ripple" }, g);
      this.el("rect", { width: w, height: h, rx: r, class: "box" }, g);
      const use = this.el("use", { href: `${SPRITE}#${icon}`, x: (w - ic) / 2, y: (h - ic) / 2, width: ic, height: ic }, g);
      use.setAttribute("style", "fill:none;stroke:currentColor");
      const l = this.el("text", small ? { x: 40, y: 12, class: "nlabel nlabel--s" } : { x: 36, y: 76, class: "nlabel" }, g);
      l.textContent = label;
      const s = this.el("text", small ? { x: 40, y: 27, class: "nsub nsub--s" } : { x: 36, y: 92, class: "nsub" }, g);
      s.textContent = sub;
      this.nodes[id] = { g, s, sub, subcls: "", use, l, base: small ? "nsub nsub--s" : "nsub" };
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
    if (text != null) { n.s.textContent = text; n.s.setAttribute("class", `${n.base} ${subcls}`); }
  }
  // the text a node shows while idle (and returns to on reset)
  idle(id, text, subcls = "") {
    const n = this.nodes[id];
    if (!n) return;
    n.sub = text;
    n.subcls = subcls;
    if (!n.g.classList.contains("active")) { n.s.textContent = text; n.s.setAttribute("class", `${n.base} ${subcls}`); }
  }
  // a device whose data is not there for this lane: greyed, its link dashed
  off(id, on) { this.nodes[id]?.g.classList.toggle("off", on); }
  // 多端协同, during a run: a device that sends nothing for the question is a shadow (device id, not node id);
  // its status text stays fully readable
  shade(d, on) {
    this.nodes[nid(d)]?.g.classList.toggle("shade", on);
    this.links[`s_${d}`]?.base.classList.toggle("shade", on);
  }
  // a link that fades out for the run (back on reset)
  fade(id, on) { this.links[id]?.base.classList.toggle("gone", on); }
  linkOff(id, on) { this.links[id]?.base.classList.toggle("off", on); }
  // the terminal node follows the device the question is about (plan.device)
  setTerminal({ icon, label }, sub) {
    const n = this.nodes.dev;
    if (!n) return;
    n.use.setAttribute("href", `${SPRITE}#${icon}`);
    n.l.textContent = label;
    n.sub = sub;
    if (!n.g.classList.contains("active")) { n.s.textContent = sub; n.s.setAttribute("class", n.base); }
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
    const [x0, y0] = reverse ? [l.x2, l.y2] : [l.x1, l.y1];
    const tail = [0.78, 0.56].map((k) => this.el("circle", { r: r * k, class: `packet tail ${kind}`, cx: x0, cy: y0, opacity: 0 }));
    const c = this.el("circle", { r, class: `packet ${kind}`, cx: x0, cy: y0 });
    this.packets.push({ c, tail, l, reverse, t0: performance.now(), dur });
  }
  stream(id, on, opts = {}) {
    clearInterval(this.streams[id]);
    delete this.streams[id];
    if (on) this.streams[id] = setInterval(() => this.send(id, opts), opts.every || 70);
  }
  // a link that glows for `ms`, then goes quiet: a step that took a few milliseconds still shows
  glow(id, ms = 900) {
    if (!this.links[id]) return;
    clearTimeout(this.glows[id]);
    this.hot(id, true);
    this.glows[id] = setTimeout(() => { delete this.glows[id]; this.hot(id, false); }, ms);
  }
  // a few packets on a link, one after another
  burst(id, n = 3, { every = 140, ...opts } = {}) {
    if (!this.links[id]) return;
    for (let i = 0; i < n; i++) this.timers.push(setTimeout(() => this.send(id, opts), i * every));
  }
  // a node lit for a moment (its data just moved), apart from the active state the run events set
  flash(id, ms = 900) {
    const n = this.nodes[id];
    if (!n) return;
    clearTimeout(n.flashT);
    n.g.classList.add("flash");
    n.flashT = setTimeout(() => n.g.classList.remove("flash"), ms);
  }
  quiet() {
    for (const k of Object.keys(this.streams)) this.stream(k, false);
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
    for (const k of Object.keys(this.glows)) clearTimeout(this.glows[k]);
    this.glows = {};
    for (const id of Object.keys(this.links)) this.hot(id, false);
  }
  // a stopped run: nothing glows any more, and "…中" (in progress) labels say it stopped
  calm() {
    this.quiet();
    for (const n of Object.values(this.nodes)) {
      n.g.classList.remove("active", "alert");
      if (/中…?$/.test(n.s.textContent)) { n.s.textContent = "已停止"; n.s.setAttribute("class", n.base); }
    }
  }
  reset() {
    this.quiet();
    for (const p of this.packets) { p.c.remove(); p.tail.forEach((x) => x.remove()); }
    this.packets = [];
    for (const [id, n] of Object.entries(this.nodes)) { this.node(id, null, n.sub, n.subcls); n.g.classList.remove("shade"); }
    for (const id of Object.keys(this.links)) { this.linkValue(id, ""); this.links[id].base.classList.remove("shade", "gone"); }
  }
  destroy() {
    this.quiet();
    flows.delete(this);
  }
  frame(now) {
    const at = (p, t) => {
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const k = p.reverse ? 1 - e : e;
      return [p.l.x1 + (p.l.x2 - p.l.x1) * k, p.l.y1 + (p.l.y2 - p.l.y1) * k];
    };
    this.packets = this.packets.filter((p) => {
      const t = (now - p.t0) / p.dur;
      if (t >= 1) { p.c.remove(); p.tail.forEach((x) => x.remove()); return false; }
      const fade = t > 0.85 ? (1 - t) / 0.15 : 1;
      const [x, y] = at(p, t);
      p.c.setAttribute("cx", x);
      p.c.setAttribute("cy", y);
      p.c.setAttribute("opacity", fade);
      p.tail.forEach((c, i) => {
        const [tx, ty] = at(p, Math.max(0, t - 0.06 * (i + 1)));
        c.setAttribute("cx", tx);
        c.setAttribute("cy", ty);
        c.setAttribute("opacity", t < 0.06 * (i + 1) ? 0 : fade * (0.45 - i * 0.2));
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
  for (const L of Object.values(state.lanes)) L.flow.destroy();
  state.lanes = {};
  host.innerHTML = "";
  host.classList.add("two");
  const multi = state.mode === "multi";
  host.classList.toggle("three", multi);
  for (const id of ["plain", "v2"]) {
    const cfg = (multi ? MULTI : LANES)[id];
    // 单端问答: upload and cloud-to-cloud traffic; 多端协同: none (the comparison column between the lanes has them)
    const metrics = multi ? [] : [["edge", "signal", "上传云端", id === "plain" ? "bad" : "good"], ["cloud", "server", "云云传输", id === "v2" ? "good" : ""]];
    const el = document.createElement("article");
    el.className = `lane card lane--${id}${multi ? " lane--multi" : ""}`;
    el.dataset.lane = id;
    el.dataset.kind = "mail";
    // the band on top says what each generation gets per question; its ticker spells it out
    el.innerHTML = `
      <div class="lane__ribbon">${ICON(cfg.ribbonIcon)}<span class="lane__id">${cfg.ribbon}</span><div class="ticker"><span class="ticker__track ${id === "plain" ? "bad" : "mono"}" data-ticker></span></div></div>
      <header class="lane__head">
        <div class="lane__icon">${ICON(cfg.icon)}</div>
        <div class="lane__title"><h3>${cfg.title}<small>${cfg.tag}</small></h3><p data-desc>${cfg.desc}</p></div>
        <span class="pill" data-pill>待命</span>
      </header>
      <div class="flow" data-flow></div>
      <ol class="rail">${cfg.stages.map(([k, l]) => `<li data-stage="${k}"><span>${l}</span><b>—</b></li>`).join("")}<li class="rail__total" data-total><span>完整回答</span><b>—</b></li></ol>
      ${metrics.length ? `<div class="metrics">${metrics.map(([k, ic, l, c]) => `<div class="metric ${c}" data-mbox="${k}"><label>${ICON(ic)}${l}</label><output data-m="${k}">—</output></div>`).join("")}</div>` : ""}
      <div class="answer">
        <div class="answer__head"><span>${ICON("brain")} <b data-model></b> 的回答<em class="answer__mode" data-mode></em></span><span class="answer__q" data-quality></span><span data-evcount></span></div>
        <div class="answer__notice" data-notice hidden></div>
        <div class="answer__text" data-answer>${IDLE}</div>
        <div class="evlist" data-evlist></div>
      </div>`;
    host.appendChild(el);
    // 多端协同: the comparison column (scoreboard + key-facts checklist) sits between the two lanes
    if (multi && id === "plain") host.insertAdjacentHTML("beforeend", BOARD);
    const flow = new Flow($("[data-flow]", el), multi ? (id === "plain" ? plainTopo() : topoV2()) : cfg);
    state.lanes[id] = { id, el, cfg, flow, multi, kind: "mail", text: "", evidence: [], stageT: {}, ends: {}, m: {} };
    setTicker(id);
  }
}
const plainTopo = () => { const h = hpic1Pick(); return h.route === "direct" ? topoDirect(h.devices[0]) : topoSync(); };
// the HPIC 1.0 lane's drawing follows the scenario and its device choice (rebuilt while no run is on)
function redrawPlain() {
  const P = lane("plain");
  if (!P?.multi || state.running) return;
  P.flow.destroy();
  const host = $("[data-flow]", P.el);
  host.innerHTML = "";
  P.flow = new Flow(host, plainTopo());
}

function lane(id) { return state.lanes[id]; }
function setTotal(id, ms) {
  const li = $("[data-total]", lane(id).el);
  const b = $("b", li);
  li.classList.add("done");
  b.classList.remove("ticking", "pop");
  b.textContent = msText(msr(ms));
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
  if (!el) return;
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
    $("b", li).textContent = msText(Math.max(0, msr(t) - msr(prev)));
  }
}
function setTicker(id, text) {
  const L = lane(id);
  if (!L) return;
  const tr = $("[data-ticker]", L.el);
  const defaults = {
    plain: plainExposure(),
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
// what HPIC 1.0 receives with every question: the whole collection (单端问答), or what reached the phone (多端协同)
const REUPLOAD = "HPIC 1.0 不保存数据，下次提问还要重新上传";
function plainExposure() {
  if (lane("plain")?.multi) {
    const { route, devices } = state.running && state.run?.hpic1 ? { route: state.run.route, devices: state.run.hpic1 } : hpic1Pick();
    const names = (ds) => ds.map((d) => deviceOf(d).label).join("、");
    const size = bytesText(devices.reduce((a, d) => a + (deviceOf(d).bytes || 0), 0));
    if (route === "direct") {
      const rest = DEVICE_ORDER.filter((d) => !devices.includes(d));
      return `${names(devices)}直接上传自己的全部数据 · ${size}　｜　${names(rest)}的数据这次用不上　｜　${REUPLOAD}`;
    }
    const synced = devices.filter((d) => d !== "phone");
    const left = ["watch", "pc", "car"].filter((d) => !synced.includes(d));
    return `手机每次提问上传：本机照片${synced.length ? ` + 已同步的${names(synced)}数据` : ""} · ${size}${left.length ? `　｜　${names(left)}未同步，这次用不上` : ""}　｜　${REUPLOAD}`;
  }
  if (state.kind === "mail") return state.plainTicker || `每次提问由手机上传整个邮箱：邮件原文、附件、体检报告、账单、合同　｜　${REUPLOAD}`;
  const K = kindOf(state.kind);
  const dev = deviceOf(state.device || deviceFor(state.kind));
  return `每次提问由手机上传：全部 ${dev.records ? intText(dev.records) + " " + K.unit : ""}${K.noun}　｜　${REUPLOAD}`;
}

// Set the lanes up for one kind of data and device (from a preset before the run, from the plan during it).
function applyKind(kind, device) {
  if (lane("plain")?.multi) return;
  kind = KINDS[kind] ? kind : "mail";
  state.kind = kind;
  state.device = device || null;
  const K = kindOf(kind);
  const dev = device ? deviceOf(device) : null;
  // the phone (主设备) asks in both lanes; data of the PC, watch or car reaches HPIC 1.0 only once synced to the phone
  const phone = { icon: "smartphone", label: "手机" };
  const synced = !dev || dev.id !== "phone";
  for (const L of Object.values(state.lanes)) {
    L.kind = kind;
    L.el.dataset.kind = kind;
    L.flow.setTerminal(phone, L.id === "plain" ? `${synced ? "已同步 " : ""}${holdings(kind, device)}` : "掌控密钥");
    if (L.id === "plain") {
      L.flow.linkLabel("edge", K.link);
      $('[data-stage="upload"] span', L.el).textContent = K.stage;
      $("[data-desc]", L.el).textContent = `每次提问由手机把${synced ? "同步来的" : ""}${K.whole}上传到 HPIC 1.0，在机密计算环境内现场检索并回答`;
    } else {
      $("[data-desc]", L.el).textContent = kind === "mail" ? L.cfg.desc : L.cfg.desc.replace("几封", "几条");
    }
  }
  $("#device .device__tag use").setAttribute("href", `${SPRITE}#${(dev || phone).icon}`);
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
    L.up = null;
    L.missing = null;
    L.el.classList.remove("running", "done", "halted");
    for (const li of $$(".rail li", L.el)) {
      li.className = li.hasAttribute("data-total") ? "rail__total" : "";
      li.style.removeProperty("--p");
      const b = $("b", li);
      b.textContent = "—";
      b.classList.remove("ticking", "pop");
      clearInterval(L["sp_" + li.dataset.stage]);
    }
    for (const o of $$("output", L.el)) { o.textContent = "—"; o.dataset.v = 0; o.parentElement.classList.remove("full", "low"); }
    L.searched = 0;
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
  $("#board")?.classList.remove("final");
  applyMulti();
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
    .replace(/这部分无法回答/g, `<strong class="miss">这部分无法回答</strong>`) // a device whose data HPIC 1.0 did not get
    .replace(/\n/g, "<br>");
  if (!final) html += `<span class="caret"></span>`;
  box.innerHTML = html;
  box.scrollTop = final ? 0 : box.scrollHeight;
}

// Evidence: mail chips as before; photos as a thumbnail grid; watch and car records as a compact list.
const evDevice = (e) => e.device || deviceFor(e.kind || "mail");
function renderEvidence(id) {
  const L = lane(id);
  if (L.multi) {
    const host = $("[data-evlist]", L.el);
    host.className = "evlist evlist--multi";
    const n = new Set(L.evidence.map(evDevice)).size;
    $("[data-evcount]", L.el).textContent = L.evidence.length ? `取证 ${L.evidence.length} 条 · ${n} 台设备` : "";
    host.innerHTML = L.evidence
      .map((e, i) => { const d = deviceOf(evDevice(e)); return `<button class="ev ev--dev" data-i="${i}" title="${esc(d.label)} · ${esc(e.subject || e.title)}"><b>${e.n ?? i + 1}</b>${ICON(d.icon)}<span>${esc(e.subject || e.title || "")}</span></button>`; })
      .join("");
    return;
  }
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
  const multi = !!lane("plain")?.multi;
  const pick = multi ? hpic1Pick() : null;
  state.run = multi
    ? { mode: "multi", devices: state.mpreset?.devices || DEVICE_ORDER, facts: state.mpreset?.facts || [], scen: pick.sc.id, route: pick.route, hpic1: pick.devices, t0: performance.now() }
    : { kind: state.kind, device: state.device, total: null };
  const ctrl = new AbortController();
  state.abort = ctrl;
  $("#run").disabled = true;
  $("#run span").textContent = "对比中…";
  $("#stop").hidden = false;
  $("#stop").disabled = false;
  $("#stop span").textContent = "停止";
  resetLanes();
  if (multi) shadeDevices();
  const lanes = ["plain", "v2"];
  for (const id of lanes) {
    pill(id, "运行中", "run");
    lane(id).el.classList.add("running");
    $("[data-answer]", lane(id).el).innerHTML = `<div class="ph ph--idle ph--run"><i></i><b>检索中</b><span>找到证据后，模型开始作答</span></div>`;
  }
  $("#device-chips").innerHTML = "";
  const body = JSON.stringify(multi
    ? { question, model: state.model, mode: "multi", route: state.run.route, hpic1: state.run.hpic1, lanes, net: state.net }
    : { question, model: state.model, lanes, net: state.net });
  $("#modes").classList.add("locked");
  $("#presets").classList.add("locked");
  const t0 = performance.now();
  clearInterval(state.clock);
  state.clock = setInterval(() => {
    const el = performance.now() - t0;
    for (const id of lanes) {
      const L = lane(id);
      if (!L || L.done) continue;
      const b = $("[data-total] b", L.el);
      b.textContent = msText(msr(el));
      b.classList.add("ticking");
    }
    if (multi) boardTime(el);
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
    $("#modes").classList.remove("locked");
    $("#presets").classList.remove("locked");
    if (multi) renderBoard();
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
const STRIP_IDLE = `<span class="device__idle">提问后，终端在本地生成查询向量和 HMAC 检索令牌，把目标向量密封给 HPIS。密钥不离开终端。</span>`;
const STRIP_MULTI = `<span class="device__idle">提问后，手机只发出一次密封请求。HPIC 2.0 在 HPIS 里按设备逐个密态检索，HPIC 1.0 只能用已同步到手机的数据。</span>`;
function idleStrip(force) {
  if (!state.running && (force || $("#device-chips .dchip"))) $("#device-chips").innerHTML = lane("plain")?.multi ? STRIP_MULTI : STRIP_IDLE;
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
      if (lane("plain")?.multi) {
        state.run = { ...state.run, mode: "multi", devices: ev.devices || state.run.devices, hpic1: ev.hpic1 || state.run.hpic1, route: ev.route || state.run.route, facts: ev.facts || state.run.facts, plans: ev.plans || {} };
        applyMulti();
        renderBoard();
        break;
      }
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
      notice(ev.lane, ev.device && !ev.lane ? `${deviceOf(ev.device).label}：${ev.text}` : ev.text);
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
        // 场景二 uploads from the one device, otherwise the phone uploads
        f.node(L.multi && state.run?.route === "direct" ? state.run.hpic1?.[0] || "dev" : "dev", "active");
        f.node("cloud", "active", "接收数据中", "hot");
        f.hot("edge", true);
        f.stream("edge", true, { kind: "plaintext", every: 45, dur: 700 });
      } else if (id === "plain" && ev.stage === "upload" && ev.state === "end") {
        f.stream("edge", false);
        f.hot("edge", false);
        stage(id, "upload", "end", t);
        stage(id, "retrieve", "start", t);
        f.node("cloud", "active", "现场检索中", "hot");
        if (L.multi && state.run?.route !== "direct") f.node("dev", "active", "已上传全部数据", "hot");
      } else if (ev.stage === "request" && ev.state === "start") {
        stage(id, "request", "start", t);
        f.node("dev", "active", L.multi ? "发出密封请求" : undefined, "hot");
        if (L.multi) { // 密封请求: one small request, shown long enough to see
          f.glow("edge", 1000);
          f.burst("edge", 3, { kind: "cipher", r: 4, every: 150, dur: 620 });
        } else {
          f.hot("edge", true);
          f.send("edge", { kind: "cipher", r: 4 });
        }
        metric(id, "edge", ev.edge_up, "bytes");
        f.linkValue("edge", bytesText(ev.edge_up), "small");
        if (L.multi) { L.up = ev.edge_up; renderBoard(); }
      } else if (ev.stage === "generate" && ev.state === "first") {
        stage(id, "generate", "start", L.stageT.generate ?? t);
      }
      break;
    }
    case "upload": {
      if (L.done) break;
      metric(id, "edge", ev.edge_up, "bytes");
      if (L.multi) {
        // the link is short in the device drawings: the size on the link, the batches on HPIC 1.0
        L.flow.linkValue("edge", `${state.run?.route === "direct" ? "直传 " : ""}${bytesText(ev.edge_up)}`, "big");
        L.flow.node("cloud", "active", `接收 ${ev.batch}/${ev.batches} 批`, "hot");
        // 场景一: the phone uploads, device by device, the data synced to it (the devices themselves stay still)
        if (state.run?.route !== "direct" && ev.device) {
          L.flow.node("dev", "active", ev.device === "phone" ? "上传本机照片" : `上传${deviceOf(ev.device).label}的数据`, "hot");
        }
        L.up = ev.edge_up;
        renderBoard();
      } else L.flow.linkValue("edge", `${bytesText(ev.edge_up)} · ${ev.batch}/${ev.batches} 批`, "big");
      stage(id, "upload", "progress", t, ev.batch / ev.batches);
      break;
    }
    case "auth": {
      if (L.done) break;
      stage(id, "request", "end", t);
      L.flow.node("hpic", "active", "票据有效 · 转发");
      const key = id === "v1" ? "fetch" : "search";
      stage(id, key, "start", t);
      if (L.multi) { // 密态检索: HPIC 2.0 hands the per-device searches to HPIS (the request link fades on its own)
        L.flow.node("hpis", "active", "SSE 筛选 + 密态向量");
        L.flow.glow("cloudl", 1200);
        L.flow.burst("cloudl", 2, { kind: "cipher", r: 3.5, every: 160, dur: 480 });
        break;
      }
      L.flow.hot("edge", false);
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
      const what = L.multi ? `${ev.rows} 条命中记录` : `${ev.rows} ${K.unit}命中${K.noun}`;
      L.flow.node("hpic", "active", L.multi ? `解密 ${intText(ev.rows)} 条` : `解密 ${intText(ev.rows)} ${K.unit}`, "hot");
      setTicker(id, `HPIC 本次仅解密 ${what} · HPIS 只见匿名标识、HMAC 令牌与 AES-GCM 密文`);
      break;
    }
    case "search": { // HPIC 1.0
      if (L.done) break;
      L.flow.node("cloud", "active", L.multi ? `现场检索 ${wanText(ev.scanned)}条` : `解析 ${intText(ev.scanned)} ${unit} · 现算向量 ${intText(ev.embedded ?? ev.candidates)} ${unit}`, "hot");
      break;
    }
    // 多端协同, 场景一: data the watch, PC or car synced to the phone before the question
    case "sync": {
      if (!L || L.done) break;
      // synced before the question: the device and its link stay still (nothing moves from it during the run)
      L.flow.node(nid(ev.device), null, "✓ 已同步", "ok");
      break;
    }
    // 场景二: the one device sends its own data straight to HPIC 1.0
    case "direct": {
      if (!L || L.done) break;
      L.flow.node(nid(ev.device), "active", "✓ 直传", "ok");
      if (ev.bytes) L.flow.linkValue("edge", `直传 ${bytesText(ev.bytes)}`, "big");
      break;
    }
    case "missing": {
      if (!L || L.done) break;
      L.missing = ev.devices || [];
      const direct = state.run?.route === "direct";
      for (const d of L.missing) {
        L.flow.off(nid(d), true);
        L.flow.node(nid(d), null, direct ? "未上传" : d === "phone" ? "✗ 本机数据未上传" : "✗ 未同步", direct ? "" : "miss");
      }
      renderBoard();
      break;
    }
    // one search per device: in the uploaded data (HPIC 1.0), or in that device's AID in HPIS (HPIC 2.0)
    case "device_search": {
      if (!L || L.done) break;
      const label = deviceOf(ev.device).label;
      // 多端协同: the search runs in HPIS (HPIC 2.0) or in what was uploaded (HPIC 1.0), not on the devices, so the
      // devices keep their fixed status; the count of devices searched shows on HPIS, the hits on the board
      if (id === "v2") {
        L.flow.glow("cloudl", 1200);
        L.flow.send("cloudl", { reverse: true, kind: "cipher", r: 3.2, dur: 480 });
        L.searched = (L.searched || 0) + 1;
        L.flow.node("hpis", "active", `密态检索 ${L.searched}/${(state.run?.devices || []).length || L.searched} 台`, "hot");
      } else {
        L.flow.node("cloud", "active", `检索${label}数据`, "hot");
      }
      break;
    }
    case "evidence": {
      if (L.done) break;
      L.evidence = ev.items || [];
      renderEvidence(id);
      if (L.multi) renderBoard();
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
      if (L.multi) renderBoard();
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
      if (L.multi) {
        // the drawing keeps what each device did, without the pulsing
        L.flow.quiet();
        for (const n of Object.values(L.flow.nodes)) n.g.classList.remove("active", "alert");
        L.q = multiScore(id);
        renderBoard();
      } else L.q = quality(id, question);
      if (ev.timed_out) pill(id, "超时 · 已截断", "warn");
      else pill(id, L.llm?.replay ? "完成 · 回放" : L.llm?.fallback ? "完成 · 摘录" : "完成", "ok");
      L.el.classList.remove("running");
      L.el.classList.add("done", "flash");
      setTimeout(() => L.el.classList.remove("flash"), 1200);
      for (const o of $$(".metric output", L.el)) { o.classList.remove("pop"); void o.offsetWidth; o.classList.add("pop"); }
      if (id === "plain") L.flow.node("cloud", null, L.multi ? "完成 · 不保存数据" : "处理完毕 · 不保存数据");
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
function showVerdict(title, tiles) {
  $("#verdict-title").textContent = title;
  $("#verdict-grid").innerHTML = tiles.map((x, i) => `<div class="vstat" style="animation-delay:${i * 90}ms"><b>${x.big}</b><span>${x.title}</span><em title="${esc(x.sub)}">${esc(x.sub)}</em></div>`).join("");
  $("#verdict").hidden = false;
  requestAnimationFrame(() => {
    $("#verdict").classList.add("on");
    document.body.classList.add("scoreboard", "results");
    for (const id of ["plain", "v2"]) { const b = lane(id) && $("[data-answer]", lane(id).el); if (b) b.scrollTop = 0; }
  });
}
// how long each lane took for the complete answer: the faster one's factor, or "about the same"
function timeTile(P, V2) {
  const pt = msr(P.final.total_ms), vt = msr(V2.final.total_ms);
  const ratio = pt / Math.max(1, vt), sub = `HPIC 1.0 ${msText(pt)} → ${msText(vt)}`;
  // Small collections (a watch or car log) upload in a blink: then the two lanes take about as long,
  // and the tile says so instead of "1.0× faster".
  if (ratio >= 1.1) return { big: fold(ratio), title: "完整回答更快", sub };
  if (ratio > 0.9) return { big: `≈1<small>×</small>`, title: "完整回答用时相近", sub };
  return { big: fold(1 / ratio), title: "完整回答较慢", sub };
}
function verdict() {
  const P = lane("plain"), V2 = lane("v2");
  if (!P?.final || !V2?.final) return;
  if (P.multi) { renderBoard(); $("#board")?.classList.add("final"); return; }
  const kind = state.run?.kind || "mail";
  const K = kindOf(kind);
  // records of this kind: the plan says how many when the server sends it
  const total = state.run?.total ?? kindTotal(kind, state.run?.device);
  const tiles = [];
  // Upload per question, the measure the cover, the flow diagram and the cloud view all use.
  const pe = P.final.edge_up, ve = V2.final.edge_up;
  tiles.push({ big: fold(pe / ve), title: "上传云端减少", sub: `HPIC 1.0 ${bytesText(pe)} → ${bytesText(ve)}` });
  if (V2.decryptRows && total) {
    const share = (100 * V2.decryptRows) / total;
    tiles.push({ big: `${share < 0.1 ? share.toFixed(2) : share.toFixed(1)}<small>%</small>`, title: "HPIS 只读命中密文", sub: `${V2.decryptRows} / ${intText(total)} ${K.unit} · 云内 ${bytesText(V2.m.cloud)}` });
  }
  if (P.final.total_ms && V2.final.total_ms) tiles.push(timeTile(P, V2));
  const vf = state.verify;
  if (vf && vf.plain_ms) {
    const noise = vf.overhead_pct < 0.5;
    const big = noise ? `≈0<small>%</small>` : `+${vf.overhead_pct.toFixed(1)}<small>%</small>`;
    const same = vf.identical ? "Top-10 与常规检索完全一致" : "结果与常规检索不同";
    const sub = noise
      ? `同题 ${vf.pairs} 次密态与常规检索配对，时延差异在测量误差内 · ${same}`
      : `同题 ${vf.pairs} 次配对：常规 ${vf.plain_p25_ms.toFixed(2)} ms · 密态 ${vf.cipher_p25_ms.toFixed(2)} ms · ${same}`;
    tiles.push({ big, title: "密态检索代价", sub });
  }
  tiles.push({ big: `1<small>次</small>`, title: "数据只需上传一次", sub: "HPIC 2.0 存入 HPIS 后按需检索，HPIC 1.0 每次提问重新上传" });
  showVerdict("本次提问 · HPIC 2.0 对比 HPIC 1.0", tiles);
}

// ------------------------------------------------------------------ 多端协同: the comparison column
// Between the two lanes: the scoreboard of the chosen scenario (场景一 → upload and full-answer time, 场景二 →
// devices covered and key facts) and the key-facts checklist, both filled in live as the events arrive.
// A device counts as covered when the lane's evidence has a record from it. A preset's key fact counts when its
// device is covered and one of its match strings is in the answer (spaces and thousands separators ignored).
// A typed question has no preset facts: its rows are the records the HPIC 2.0 answer cites ([n] → that lane's
// evidence item n), and HPIC 1.0 gets ✓ when its own answer cites the same record (kind + id).
const BOARD = `<aside class="board card" id="board" aria-label="对比"></aside>`;
const FACT_DEVICES = ["phone", "pc", "watch", "car"];
const normFact = (x) => String(x).replace(/\s/g, "").replace(/[￥¥]/g, "").replace(/[,，]/g, "").replace(/：/g, ":");
const devNames = (ds) => ds.map((d) => deviceOf(d).label).join("、");
// the evidence numbers an answer cites, in order: [1] [1][3] [1,3] [1-3] 【2】 (read as prompts.parse_citations does)
function citeNums(text) {
  const seen = [];
  for (const m of String(text || "").matchAll(/[\[［【]\s*(\d+(?:\s*(?:[,，、;；]|[-–~～至])\s*\d+)*)\s*[\]］】]/g)) {
    for (const piece of m[1].split(/\s*[,，、;；]\s*/)) {
      let ns = piece.split(/\s*[-–~～至]\s*/).filter(Boolean).map(Number);
      if (ns.length === 2 && ns[1] > ns[0] && ns[1] - ns[0] <= 20) ns = Array.from({ length: ns[1] - ns[0] + 1 }, (_, k) => ns[0] + k);
      for (const n of ns) if (!seen.includes(n)) seen.push(n);
    }
  }
  return seen;
}
const recKey = (e) => `${e.kind || "mail"}:${e.id}`;
// the records a lane's answer cites (key → evidence item), in the order it first cites them
// the words a record's card shows (title or subject, summary, place), for the key-fact check
const recText = (e) => normFact([e.title, e.subject, e.snippet, e.summary, e.place_text].filter(Boolean).join(" "));
function citedRecs(L) {
  const out = new Map();
  const numbered = L.evidence.some((x) => x.n != null);
  for (const n of citeNums(L.text)) {
    const e = numbered ? L.evidence.find((x) => x.n === n) : L.evidence[n - 1];
    if (e && e.id != null && !out.has(recKey(e))) out.set(recKey(e), e);
  }
  return out;
}
// a short name for a cited record, with its date: the mail subject, the photo's place, the record's kind and
// figure (or where a trip went)
function recLabel(e) {
  const kind = e.kind || "mail";
  let text;
  if (kind === "mail") text = e.subject; // as written ("Re: …" tells a reply from the mail it answers)
  else if (kind === "photo") text = (e.place && typeof e.place === "object" && e.place.name) || placeText(e.place) || e.place_text || e.title;
  else {
    const p = String(e.title || "").split(/\s*·\s*/);
    const to = p.length >= 3 ? p[1].split(/\s*→\s*/)[1] : "";
    // "跑步 · 某公园 · 6.51 公里" → "跑步 6.51 公里", a trip → "去<终点>", "用车周报 · <日期>" → "用车周报" (dated anyway)
    text = p.length >= 3 ? (to ? `去${to}` : `${p[0]} ${p[p.length - 1]}`)
      : p.length === 2 && /报$/.test(p[0]) ? p[0] : p.length === 2 && /^前往/.test(p[1]) ? p[0] + p[1] : p.join(" · ");
  }
  const date = dateText(e.date);
  return { text: text || e.title || `记录 ${e.n ?? ""}`, date: date.slice(5), title: [e.subject || e.title, date].filter(Boolean).join(" · ") };
}
// what the board shows: the run on screen (running or finished), else the current choice.
// cited: no preset facts, so the checklist is built from the records the answers cite
function boardCtx() {
  const shown = state.running || ["plain", "v2"].some((id) => { const L = lane(id); return L && (L.final || L.halted || L.evidence.length || L.text); });
  let ctx;
  if (shown && state.run?.mode === "multi") {
    ctx = { live: true, scen: state.run.scen, route: state.run.route, hpic1: state.run.hpic1 || [], need: state.run.devices || DEVICE_ORDER, facts: state.run.facts || [] };
  } else {
    const h = hpic1Pick();
    ctx = { live: false, scen: h.sc.id, route: h.route, hpic1: h.devices, need: state.mpreset?.devices || DEVICE_ORDER, facts: state.mpreset?.facts || [] };
  }
  ctx.cited = !ctx.facts.length;
  return ctx;
}
// the checklist's rows: a preset's key facts, or (no preset) one per record the HPIC 2.0 answer cites,
// grouped by device, in the order the answer first cites them
function boardRows(ctx) {
  if (!ctx.cited) return ctx.facts.map((f, i) => ({ key: `f${i}`, device: f.device, label: f.label, title: f.label, fact: f }));
  if (!ctx.live || !lane("v2")) return [];
  const recs = [...citedRecs(lane("v2")).values()];
  return FACT_DEVICES.flatMap((d) => recs.filter((e) => evDevice(e) === d)).map((e) => {
    const l = recLabel(e);
    return { key: recKey(e), device: evDevice(e), label: l.text, date: l.date, title: `${deviceOf(evDevice(e)).label} · ${l.title}`, rec: e };
  });
}
// per lane: each row's cell (wait | ok | no | nodata), the devices covered and the rows answered
function multiScore(id, ctx = boardCtx(), rows = boardRows(ctx)) {
  const L = lane(id);
  const evDevs = new Set(L.evidence.map(evDevice));
  const seen = L.evidence.length > 0 || !!L.done;
  const gone = new Set(L.missing || []);
  const text = normFact(L.text);
  const mine = ctx.cited && id === "plain" ? citedRecs(L) : null;
  const refs = ctx.cited ? null : [...citedRecs(L).values()].map((e) => [evDevice(e), recText(e)]);
  const cells = rows.map((r) => {
    if (r.rec) {
      // a cited record: HPIC 2.0 cited it; HPIC 1.0 has it when its answer cites the same record
      if (id === "v2") return "ok";
      if (gone.has(r.device) || !ctx.hpic1.includes(r.device)) return "nodata";
      return mine.has(r.key) ? "ok" : L.done ? "no" : "wait";
    }
    const f = r.fact;
    if (f.device && (gone.has(f.device) || (seen && !evDevs.has(f.device)))) return "nodata";
    // a key fact counts when the answer states it, or cites a record of that device that holds it
    // (「机场停车4天，停车费¥240[16]」 cites 「北京大兴国际机场 P2停车楼 → 望京」)
    const keys = [f.value, ...(f.match || [])].filter(Boolean).map(normFact);
    const hit = (!f.device || evDevs.has(f.device)) && (keys.some((m) => text.includes(m))
      || refs.some(([d, t]) => (!f.device || d === f.device) && keys.some((m) => t.includes(m))));
    return hit ? "ok" : L.done ? "no" : "wait";
  });
  return { cells, seen, done: !!L.done, covered: ctx.need.filter((d) => evDevs.has(d)), need: ctx.need, hit: cells.filter((c) => c === "ok").length, n: rows.length };
}
function buildBoard(ctx) {
  const host = $("#board");
  const perf = ctx.route !== "direct";
  const sc = scenarios().find((x) => x.id === ctx.scen) || scenario();
  const stats = perf ? [["up", "上传数据"], ["time", "完整回答"]] : [["cover", "覆盖设备"], ["facts", "关键信息"]];
  // preset facts are known up front; cited records are added as the answer cites them (syncCited)
  const facts = ctx.cited ? [] : boardRows(ctx);
  const rows = FACT_DEVICES.filter((d) => facts.some((r) => r.device === d))
    .map((d) => factGroup(d) + facts.filter((r) => r.device === d).map(factRow).join("")).join("");
  host.className = `board card board--${perf ? "perf" : "acc"}`;
  host.innerHTML = `
    <header class="brow board__head"><span class="board__title">${ICON(perf ? "gauge" : "check-circle")}${perf ? "性能对比" : "准确性对比"}</span><span class="bcol c1"><b>HPIC 1.0</b><small>${esc((sc.label || "").split(" · ")[0])}</small></span><span class="bcol c2"><b>HPIC 2.0</b><small>场景三</small></span></header>
    <div class="board__stats">${stats.map(([k, l]) => `<div class="brow bstat" data-k="${k}"><span class="bstat__k">${l}<em data-x></em></span><b class="bval c1" data-v="plain">—</b><b class="bval c2" data-v="v2">—</b></div>`).join("")}</div>
    <p class="board__note" data-note></p>
    <div class="facts${ctx.cited ? " facts--cited" : ""}">
      <div class="brow facts__head"><span class="facts__t">关键信息核对${ctx.cited ? `<small title="每行是 HPIC 2.0 回答引用的一条记录；HPIC 1.0 的回答也引用了同一条记录记 ✓">按回答引用的记录核对</small>` : `<small title="回答写出了这条信息，或回答引用的记录里有这条信息，记 ✓">按回答及引用的记录核对</small>`}</span>${perf ? `<span class="fcount" data-fc="plain"></span><span class="fcount" data-fc="v2"></span>` : ""}</div>
      ${perf ? `<div class="brow facts__cover"><span>覆盖设备</span><span class="fcount" data-cv="plain"></span><span class="fcount" data-cv="v2"></span></div>` : ""}
      <div class="facts__list">${ctx.cited ? `<p class="facts__none" data-none></p>` : rows || `<p class="facts__none">这个问题没有预设的关键信息</p>`}</div>
    </div>`;
}
const factGroup = (d) => { const dev = deviceOf(d); return `<div class="fgroup" data-g="${d}">${ICON(dev.icon)}${esc(dev.label)} · ${esc(kindOf(DEVICES[d].kind).short)}</div>`; };
const factRow = (r) => `<div class="brow fact" data-r="${esc(r.key)}"><span class="fact__k" title="${esc(r.title)}">${r.date ? `<time>${esc(r.date)}</time>` : ""}${esc(r.label)}</span><i class="cell" data-c="plain"></i><i class="cell" data-c="v2"></i></div>`;
const htmlNode = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
// the cited records' rows, added in place as the answer cites them (existing rows are never moved, so their
// cells do not replay); a new run's first render clears the last run's rows
function syncCited(host, rows, ctx) {
  const list = $(".facts__list", host);
  if (!list) return;
  const want = new Set(rows.map((r) => r.key));
  if ($$("[data-r]", list).some((el) => !want.has(el.dataset.r))) for (const el of $$("[data-r], [data-g]", list)) el.remove();
  const none = $("[data-none]", list);
  let prev = none;
  for (const d of FACT_DEVICES) {
    const rs = rows.filter((r) => r.device === d);
    if (!rs.length) continue;
    let g = $(`[data-g="${d}"]`, list);
    if (!g) list.insertBefore((g = htmlNode(factGroup(d))), prev ? prev.nextSibling : list.firstChild);
    prev = g;
    for (const r of rs) {
      let el = $(`[data-r="${r.key}"]`, list);
      if (!el) list.insertBefore((el = htmlNode(factRow(r))), prev.nextSibling);
      prev = el;
    }
  }
  if (!none) return;
  const V = lane("v2");
  none.hidden = rows.length > 0;
  put(none, !ctx.live ? "提问后，按 HPIC 2.0 回答引用的记录逐条核对"
    : V?.done || V?.halted ? "HPIC 2.0 的回答没有引用记录" : "HPIC 2.0 的回答还没有引用记录");
}
// innerHTML only when it changed, so streaming tokens do not restart the cells' animations
function put(el, html, cls) {
  if (!el) return;
  if (el._h !== html) { el.innerHTML = html; el._h = html; }
  if (cls != null && el._c !== cls) { el.className = cls; el._c = cls; }
}
const frac = (x, n) => `${x}<small>/${n}</small>`;
const CELL = { wait: "", ok: ICON("check"), no: ICON("x"), nodata: `无数据<b class="cell__x">${ICON("x")}</b>` };
function renderBoard() {
  const host = $("#board");
  if (!host || !lane("plain")?.multi) return;
  const ctx = boardCtx();
  const key = `${ctx.route}|${ctx.scen}|${ctx.cited ? "cited" : ctx.facts.map((f) => f.label).join(",")}`;
  if (host._key !== key) { buildBoard(ctx); host._key = key; }
  host.classList.toggle("idle", !ctx.live);
  const rows = boardRows(ctx);
  if (ctx.cited) syncCited(host, rows, ctx);
  const sc = { plain: multiScore("plain", ctx, rows), v2: multiScore("v2", ctx, rows) };
  const P = lane("plain"), V = lane("v2");
  const cellOf = (k, id) => $(`[data-k="${k}"] [data-v="${id}"]`, host);
  if (ctx.route !== "direct") {
    // 场景一: performance. Upload per question, then the full-answer time (ticking while a lane runs).
    const up = { plain: P.final?.edge_up ?? P.up, v2: V.final?.edge_up ?? V.up };
    for (const id of ["plain", "v2"]) put(cellOf("up", id), up[id] ? bytes(up[id]).map((x, i) => (i ? `<small>${x}</small>` : x)).join("") : "—");
    put($(`[data-k="up"] [data-x]`, host), up.plain && up.v2 && P.done ? `减少 ${fold(up.plain / up.v2)}` : "");
    for (const id of ["plain", "v2"]) {
      const L = lane(id);
      if (L.final?.total_ms != null) put(cellOf("time", id), secs(L.final.total_ms).map((x, i) => (i ? `<small>${x}</small>` : x)).join(""), `bval c${id === "plain" ? 1 : 2}`);
      else if (!state.running) put(cellOf("time", id), "—", `bval c${id === "plain" ? 1 : 2}`);
    }
    const pt = P.final?.total_ms, vt = V.final?.total_ms;
    let tx = "";
    if (pt && vt) { const r = pt / Math.max(1, vt); tx = r >= 1.1 ? `快 ${fold(r)}` : r > 0.9 ? "用时相近" : `慢 ${fold(1 / r)}`; }
    put($(`[data-k="time"] [data-x]`, host), tx);
    const left = ["watch", "pc", "car"].filter((d) => ctx.need.includes(d) && !ctx.hpic1.includes(d));
    put($("[data-note]", host), ctx.live || left.length
      ? (left.length ? `${ICON("alert")}<span>${devNames(left)}未同步到手机，HPIC 1.0 缺这部分数据</span>` : `${ICON("layers")}<span>数据都已同步到手机，两边覆盖的设备相同</span>`)
      : `${ICON("info")}<span>比较每次提问要上传多少数据、多久拿到完整回答</span>`, `board__note${left.length ? " warn" : ""}`);
    for (const id of ["plain", "v2"]) {
      const s = sc[id];
      put($(`[data-fc="${id}"]`, host), ctx.live && s.n ? frac(s.hit, s.n) : "—", `fcount${ctx.live && s.done ? (s.hit === s.n ? " full" : " low") : ""}`);
      put($(`[data-cv="${id}"]`, host), ctx.live && s.seen ? frac(s.covered.length, s.need.length) : "—", `fcount${ctx.live && s.seen ? (s.covered.length === s.need.length ? " full" : " low") : ""}`);
    }
  } else {
    // 场景二: accuracy. Devices covered and key facts answered, then what HPIC 1.0 missed.
    for (const id of ["plain", "v2"]) {
      const s = sc[id], c = id === "plain" ? 1 : 2;
      put(cellOf("cover", id), ctx.live && s.seen ? frac(s.covered.length, s.need.length) : "—", `bval c${c}${ctx.live && s.seen ? (s.covered.length === s.need.length ? " full" : " low") : ""}`);
      put(cellOf("facts", id), ctx.live && s.n ? frac(s.hit, s.n) : "—", `bval c${c}${ctx.live && s.done && s.n ? (s.hit === s.n ? " full" : " low") : ""}`);
    }
    const missed = rows.filter((r, i) => sc.plain.cells[i] === "no" || sc.plain.cells[i] === "nodata");
    // a typed question whose HPIC 2.0 answer cited nothing has nothing to check
    const empty = ctx.cited && !rows.length && (V.done || V.halted);
    put($("[data-note]", host), !ctx.live
      ? `${ICON("info")}<span>比较两边用到了几台设备的数据、答出了几条关键信息</span>`
      : missed.length
        ? `${ICON("x")}<span><b>HPIC 1.0 漏掉 ${missed.length} 条：</b>${missed.map((r) => esc(r.label)).join("、")}</span>`
        : empty ? `${ICON("info")}<span>没有可核对的记录</span>`
        : sc.plain.done && (V.done || !ctx.cited) ? `${ICON("check")}<span>HPIC 1.0 没有漏掉关键信息</span>` : `${ICON("clock")}<span>核对中…</span>`, `board__note${missed.length ? " miss" : ""}`);
  }
  // the checklist: one row per fact (or cited record), a cell per lane
  rows.forEach((r, i) => {
    const row = $(`[data-r="${r.key}"]`, host);
    if (!row) return;
    for (const id of ["plain", "v2"]) {
      const st = ctx.live ? sc[id].cells[i] : "idle";
      put($(`[data-c="${id}"]`, row), CELL[st] ?? "", `cell ${st}${st === "wait" && state.running && !lane(id).done ? " run" : ""}`);
    }
    row.classList.toggle("gap", ctx.live && (sc.plain.cells[i] === "no" || sc.plain.cells[i] === "nodata"));
  });
}
// the full-answer time ticks while a lane runs (场景一)
function boardTime(ms) {
  const host = $("#board");
  if (!host || state.run?.route === "direct") return;
  for (const id of ["plain", "v2"]) {
    const L = lane(id);
    if (!L || L.done) continue;
    put($(`[data-k="time"] [data-v="${id}"]`, host), secs(ms).map((x, i) => (i ? `<small>${x}</small>` : x)).join(""), `bval c${id === "plain" ? 1 : 2} ticking`);
  }
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
  // All of the owner's devices: their records, and the one-time provisioning time of every kind
  // (the deployment's own time is the mailbox's; each other kind records its own).
  const devs = state.status?.devices || [];
  const multi = devs.length > 1;
  const records = devs.reduce((a, d) => a + (d.records || 0), 0) || b.emails;
  const secs = (tee.seconds || 0) + Object.values(b.kinds || {}).reduce((a, k) => a + (k.seconds?.tee || 0), 0);
  const what = multi ? "数据" : "邮件";
  const wanPlus = (n) => (n >= 1e4 ? `${Math.floor(n / 1e4)}万+` : intText(n)); // 106,081 -> 10万+
  // Upload per question: the whole mailbox versus one sealed request of about 3 KB.
  $("#prov").innerHTML = `
    <div class="prov__title">${ICON("shield-check")}<div>一次性加密预置<small>${multi ? "多端数据只在开通时加密上传一次" : "邮箱只在开通时加密上传一次"}</small></div></div>
    <div><b>${wanPlus(records)}</b><span>${multi ? "条多端数据在终端加密后存入 HPIS" : "封邮件在终端加密后存入 HPIS"}</span></div>
    <div><b>${secs ? secs.toFixed(0) + " s" : "—"}</b><span>一次性预置用时：加密全部${what}，生成检索令牌与向量密文</span></div>
    <div><b>${bytesText(tee.store_bytes)}</b><span>HPIS 中的全部存储：${what}密文与密文索引</span></div>
    <div class="prov__x"><b>万倍+</b><div class="prov__cmp"><em>每次提问上传云端的数据量</em>
      <p><span>端侧直传 HPIC 1.0</span><i class="bad">${up.bytes ? bytesText(up.bytes) : "—"}</i><small>整个邮箱</small></p>
      <p><span>HPIC 2.0</span><i class="good">约 3 KB</i><small>一次密封请求</small></p></div></div>`;
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
  $("#plain-count").textContent = multi ? `${intText(total)} 条原始数据` : `${intText(total)} 封邮件`;
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
// the owner and the devices the data lives on
function renderPlainBar() {
  const name = state.plainRows?.[0]?.owner || OWNER;
  const devs = state.devices.size ? DEVICE_ORDER.filter((d) => state.devices.has(d)) : ["pc"];
  $("#plain-bar").innerHTML = `<span class="vbar__who">${ICON("user")}<b>${esc(name)}</b></span>${devs
    .map((d) => {
      const dev = deviceOf(d), K = kindOf(dev.kind);
      const n = dev.kind === "mail" ? state.status?.mailbox?.emails : dev.records;
      return `<span class="vdev">${ICON(dev.icon)}${esc(dev.label)} · ${esc(K.label)}${n ? ` <b>${intText(n)}</b>` : ""}</span>`;
    })
    .join("")}<span class="vbar__tag">${ICON("key")}密钥在本人设备</span>`;
}
// hits: rows the local search returned (the word may sit past the visible snippet)
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
    <div class="tenants">${T.map((t) => `<span class="tchip ${tenantClass(aidKey(t))}${t.mine ? " mine" : ""}" data-aid="${esc(aidKey(t))}" title="${t.records != null ? `${intText(t.records)} 条密文` : ""}"><i></i>AID ${esc(aidKey(t))}${t.mine ? `<b class="own">${esc(ownerOf(t))}</b>` : ""}</span>`).join("")}</div>
    <span class="vbar__tag">${ICON("unlink")}匿名 · 不可关联</span>
    ${mine.length ? `<button class="reveal" id="reveal" type="button" title="揭晓" aria-label="揭晓">${ICON("fingerprint")}</button>` : ""}
    ${mine.length ? `<div class="reveal__note">${ICON("fingerprint")}<span>其中 ${mine.length} 个 AID 来自${OWNER}的 ${mine.length} 台设备，各用独立密钥。HPIS 无法把它们关联到同一个人</span></div>` : ""}`;
  $("#reveal")?.addEventListener("click", () => $("#pane-cloud").classList.toggle("revealed"));
}
function renderHpisRows(rows, fresh = false) {
  const mine = new Set(cloud.tenants.filter((t) => t.mine).map(aidKey));
  $("#hpis-rows").innerHTML = rows
    .map((r) => {
      const k = aidKey(r);
      const matched = new Set(r.matched || []);
      const tags = (r.tags || []).map((t) => (matched.has(t) ? `<mark>${esc(t)}</mark>` : esc(t))).join(" · ");
      return `<div class="row hrow ${tenantClass(k)}${mine.has(k) ? " mine" : ""}${r.hit ? " hit" : ""}${fresh ? " fresh" : ""}">
        <div class="k aidc"><i></i>AID ${esc(k)}… #${esc(r.rid)}</div><div class="hb">${esc(r.body)}</div>
        ${r.tags ? `<div class="k">令牌</div><div>${tags}</div>` : ""}
        ${r.vector ? `<div class="k">向量</div><div class="vec">${esc(r.vector)}</div>` : ""}
        ${r.hit ? `<span class="found">${ICON("check")}命中 ${esc(r.rank)}</span>` : `<span class="miss">${ICON("x")}未命中</span>`}
      </div>`;
    })
    .join("");
}
// the AID the encrypted search ran in lights up with its hit count; the other AIDs dim
function markAid(h) {
  const bar = $("#hpis-bar .tenants");
  if (!bar) return;
  bar.classList.toggle("lit", !!h);
  for (const c of $$(".tchip", bar)) {
    const on = !!h && c.dataset.aid === h.aid_short;
    c.classList.toggle("hit", on);
    if (on) c.dataset.hits = h.hits;
    else delete c.dataset.hits;
  }
}
// a band of light down a table; rows it passes get `cls` (a class name, or a function of the row)
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
      if (cls) for (const r of rows) if (!r._swept && r.offsetTop + r.offsetHeight / 2 <= y) { r._swept = true; r.classList.add(typeof cls === "function" ? cls(r) : cls); }
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
  markAid(null);
  try {
    await cloud.loading?.catch(() => {}); // the first page of both tables is on screen before a search replaces it
    let answered = false;
    const asked = (async () => {
      const r = await getJSON(`/api/spy?q=${encodeURIComponent(q)}`);
      // the encrypted search's hits first, then other stored rows of every AID; if HPIS could not
      // search (or an older server answered), another page of the store, fetched ahead
      let fresh = r.hpis_rows;
      cloud.ownRows = !fresh?.length;
      if (cloud.ownRows) fresh = ((await (cloud.next || freshSample())) || cloud.hpis?.rows || []).map((x) => ({ ...x, hit: false }));
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
    plainHost.insertAdjacentHTML("beforebegin", `<div class="vres vres--plain">${ICON("search")}<span>终端本地检索：命中 <b>${intText(r.plain_hits)}</b> ${unit}</span></div>`);
    // HPIS side: the same search, run by the device with its keys (tokens + a sealed query vector);
    // its hits light up as the scan passes them, still ciphertext under an anonymous AID
    const h = r.hpis;
    renderHpisRows(fresh, true);
    hpisHost.scrollTop = 0;
    const mark = (row) => (row.classList.contains("hit") ? "lit" : "missed");
    await sweep(hpisHost, 1500, mark);
    for (const row of $$(".hrow", hpisHost)) if (!row._swept) row.classList.add(mark(row));
    markAid(h);
    const tokens = h?.token_count ? ` · 令牌 ${esc(h.tokens[0].slice(0, 8))}…${h.token_count > 1 ? ` 等 ${intText(h.token_count)} 个` : ""}` : "";
    hpisHost.insertAdjacentHTML(
      "beforebegin",
      h
        ? `<div class="vres vres--hpis"><b class="hitn">${ICON("check-circle")}HPIS 密态检索：命中 ${intText(h.hits)} 条</b><span class="mono">AID ${esc(h.aid_short)}…${tokens} · 查询向量已密封</span><span class="why">${ICON("eye-off")}云端只见密文，不知道查的是什么、写了什么、是谁的</span></div>`
        : `<div class="vres vres--hpis"><span class="why">${ICON("alert")}HPIS 暂时无法检索，请稍后重试</span></div>`,
    );
    $("#spy-result").innerHTML = `<span class="spy__local">终端本地检索：命中 <b class="num">${intText(r.plain_hits)}</b> ${unit}</span> &nbsp;·&nbsp; <span style="color:var(--v2)">HPIS 密态检索：${h ? `命中 <b class="num">${intText(h.hits)}</b> 条（云端只见密文）` : "暂时无法检索"}</span>`;
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
    <div class="bhero__txt"><h3>${esc(head.title || "全部密文检索场景的时延损耗均低于 10%")}</h3></div></div>`;
  for (const s of sc.filter((x) => x.headline !== false)) {
    const ov = s.overhead_pct, ci = s.ci95 || [ov, ov];
    const scale = (v) => Math.max(0, Math.min(100, ((v + 2) / 14) * 100));
    html += `<div class="bcard card ${s.wide ? "bcard--wide" : ""}">
      <h4>${esc(s.name_zh)} ${s.tag ? `<span class="tag">${esc(s.tag)}</span>` : ""}</h4>
      <p class="desc">${esc(s.desc_zh || "")}</p>
      <div class="bover"><b class="${ov < 10 ? "good" : "warn"}">${ov >= 0 ? "+" : ""}${ov.toFixed(2)}%</b><span>95% 区间 ${ci[0].toFixed(1)} ~ ${ci[1].toFixed(1)}%</span></div>
      <div class="bbar"><div class="bbar__zero" style="left:${scale(0)}%"></div><div class="bbar__fill" data-w="${scale(ov)}"></div><div class="bbar__ci" style="left:${scale(ci[0])}%;width:${Math.max(0.5, scale(ci[1]) - scale(ci[0]))}%"></div><div class="bbar__limit" style="left:${scale(10)}%"><span>10% 上限</span></div></div>
      <div class="bnums"><div><label>常规检索均值</label><b>${s.plaintext_mean_ms.toFixed(3)} ms</b></div><div><label>密态检索均值</label><b>${s.cipher_mean_ms.toFixed(3)} ms</b></div><div><label>${esc(s.correctness_label || "正确性")}</label><b>${esc(s.correctness || "—")}</b></div></div>
    </div>`;
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
    <div class="tech card"><h4>${ICON("layers")}密态向量检索（软硬协同）</h4><p>向量以 <b>AES-256-GCM</b> 加密存放；目标向量密封给 HPIS 可信计算区，只在 TEE 内解开并精确排序，与常规检索结果<b>完全一致</b>。无硬件信任时可切换纯密码学 PPE 模式。</p></div>
    <div class="tech card"><h4>${ICON("lock")}纯密码学向量检索（PPE）</h4><p>不依赖可信硬件：终端用私有的随机旋转加有界随机扰动（<b>RAP</b>）变换向量，HPIS 直接在变换后的向量上比较距离、近似排序，全程不解密。实测 Top-10 召回约 <b>89%</b>（β=0.25）；也可多取 40 条交 HPIC 解密后精排，与常规检索结果<b>完全一致</b>。</p></div>
    <div class="tech card"><h4>${ICON("fingerprint")}匿名与最小暴露</h4><ul><li>每台设备一个匿名 AID，HPIS 无法把它们关联到同一人</li><li>HPIS 只见匿名标识 AID、令牌与密文</li><li>HPIC 凭匿名票据转发，仅解密本次命中的几条</li><li>数据密钥、索引密钥、向量密钥始终由终端掌控</li></ul></div>
  </div>
`;
}
// The solution at a glance: cloud above, the user's devices below. Each device uploads its own data
// once, encrypted, under its own anonymous AID (①); a question then goes device → HPIC ⇄ HPIS → device.
// Colours come from CSS variables, so both themes work.
function archSvg() {
  const box = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="20" class="arch-box"/>`;
  const chip = (x, y, w, text, kind = "") =>
    `<g class="arch-chip${kind ? " arch-chip--" + kind : ""}"><rect x="${x}" y="${y}" width="${w}" height="34" rx="9"/><text x="${x + w / 2}" y="${y + 22}" text-anchor="middle" font-size="13.5">${text}</text></g>`;
  const cyl = (cx, y, label) =>
    `<g class="arch-cyl"><path d="M${cx - 38} ${y}v46a38 10 0 0 0 76 0v-46"/><ellipse cx="${cx}" cy="${y}" rx="38" ry="10"/></g><text x="${cx}" y="${y + 33}" text-anchor="middle" class="arch-cyl-label" font-size="12.5">${label}</text>`;
  const flow = (d, label, lx, ly, anchor = "middle", delay = 0) =>
    `<path d="${d}" class="arch-flow" style="animation-delay:${delay}s" marker-end="url(#ah)"/><text x="${lx}" y="${ly}" text-anchor="${anchor}" class="arch-label">${label}</text>`;
  const devices = DEVICE_ORDER.map((id, i) => {
    const d = deviceOf(id), K = kindOf(d.kind);
    const n = d.kind === "mail" ? state.status?.mailbox?.emails : d.records;
    return { ...d, i, K, n, aidText: d.aid ? `AID ${d.aid.slice(0, 6)}…` : `匿名 AID ${i + 1}` };
  });
  // device tiles: 4 × 116 wide with gaps of 10, from x = 68 to 562
  const tiles = devices
    .map((d) => {
      const x = 68 + d.i * 126, y = 412;
      return `<g class="arch-dev a${d.i + 1}">
      <rect x="${x}" y="${y}" width="116" height="92" rx="14" class="arch-dev__box"/>
      <circle cx="${x + 25}" cy="${y + 26}" r="16" class="arch-dev__ring"/>
      <use href="${SPRITE}#${d.icon}" x="${x + 16}" y="${y + 17}" width="18" height="18" class="arch-ic arch-dev__ic"/>
      <text x="${x + 50}" y="${y + 31}" class="arch-dev__name">${esc(d.label)}</text>
      <text x="${x + 13}" y="${y + 59}" class="arch-dev__data">${d.K.short}${d.n ? ` · ${wanText(d.n)}${d.K.unit}` : ""}</text>
      <g class="arch-aid"><rect x="${x + 10}" y="${y + 66}" width="96" height="19" rx="9.5"/><text x="${x + 58}" y="${y + 79.5}" text-anchor="middle">${esc(d.aidText)}</text></g>
    </g>`;
    })
    .join("");
  // the same AIDs inside HPIS: one partition each, nothing that links them
  const parts = devices
    .map((d) => {
      const x = 670 + (d.i % 2) * 145, y = 512 + Math.floor(d.i / 2) * 42;
      return `<g class="arch-part a${d.i + 1}"><rect x="${x}" y="${y}" width="135" height="34" rx="9"/><rect x="${x}" y="${y}" width="5" height="34" rx="2.5" class="arch-part__bar"/><text x="${x + 14}" y="${y + 22}" class="arch-part__aid">${esc(d.aidText)}</text><text x="${x + 126}" y="${y + 22}" text-anchor="end" class="arch-part__kind">${d.K.short}</text></g>`;
    })
    .join("");
  return `<svg viewBox="0 0 1000 644" xmlns="http://www.w3.org/2000/svg" font-family="HarmonyOS Sans SC, PingFang SC, Microsoft YaHei, sans-serif" role="img" aria-label="方案架构：手机、PC、车机、手表各以独立匿名 AID 加密上传；提问经 HPIC 与 HPIS 密态检索后作答">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="arch-arrow"/></marker>
    <linearGradient id="hpisg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="arch-g0"/><stop offset="1" class="arch-g1"/></linearGradient>
  </defs>
  <line x1="20" y1="300" x2="980" y2="300" class="arch-sep"/>
  <text x="26" y="290" class="arch-side">云</text><text x="26" y="324" class="arch-side">端</text>

  <!-- HPIC 2.0: the trusted compute zone and the anonymous store together, behind both boxes -->
  <path d="M302 12H968A18 18 0 0 1 986 30V614A18 18 0 0 1 968 632H654A18 18 0 0 1 636 614V296A18 18 0 0 0 618 278H302A18 18 0 0 1 284 260V30A18 18 0 0 1 302 12Z" class="arch-frame"/>
  <g class="arch-legend"><rect x="300" y="0" width="246" height="24" rx="12"/><text x="423" y="17.5" text-anchor="middle">HPIC 2.0 个人智能计算平台</text></g>

  ${box(50, 40, 200, 150)}
  <text x="70" y="74" class="arch-h">终端云认证服务</text>
  ${chip(70, 92, 160, "认证")}${chip(70, 136, 160, "匿名令牌颁发")}

  ${box(300, 40, 270, 220)}
  <text x="320" y="74" class="arch-h">HPIC 机密计算平台</text>
  <text x="320" y="98" class="arch-sub">无状态 · 只在本次请求内持有密钥</text>
  ${chip(320, 114, 230, "匿名票据校验 · 转发")}
  ${chip(320, 158, 230, "解密命中的 K 条记录", "key")}
  ${chip(320, 210, 230, "业务模型", "model")}
  <text x="435" y="204" text-anchor="middle" class="arch-sub">⑥ 上下文 ↓</text>

  <rect x="650" y="40" width="320" height="572" rx="20" fill="url(#hpisg)" class="arch-hpis"/>
  <text x="670" y="74" class="arch-h">HPIS 安全存储平台</text>
  <text x="670" y="98" class="arch-sub">只见匿名标识 AID、HMAC 令牌与密文</text>
  ${chip(670, 116, 280, "新增 · SSE 密文索引检索", "new")}
  ${chip(670, 158, 280, "新增 · 密态向量检索（TEE / PPE）", "new")}
  ${chip(670, 206, 135, "匿名索引查询")}${chip(815, 206, 135, "匿名身份令牌")}
  ${chip(670, 248, 135, "令牌所属证明")}${chip(815, 248, 135, "可验证数据删除")}
  <text x="810" y="318" text-anchor="middle" class="arch-sub">↓ 只读取命中的部分数据密文</text>
  ${cyl(722, 342, "OBS")}${cyl(810, 342, "GaussDB")}${cyl(898, 342, "ES")}
  ${chip(670, 412, 135, "非结构化数据")}${chip(815, 412, 135, "结构化数据")}
  <text x="810" y="470" text-anchor="middle" class="arch-sub">正文与向量均为 AES-256-GCM 密文</text>
  <text x="810" y="500" text-anchor="middle" class="arch-sub">每台设备一个匿名 AID · 互不关联</text>
  ${parts}

  ${box(50, 340, 530, 272)}
  <text x="70" y="374" class="arch-h">用户多端设备</text>
  <text x="70" y="398" class="arch-sub">多端数据加密共享 · 本地掌控授权 · 数据可用不可见</text>
  ${tiles}
  ${chip(68, 518, 152, "数据密钥 DEK", "key")}${chip(230, 518, 152, "检索密钥 SSE", "key")}${chip(392, 518, 170, "向量密钥 VEK", "key")}
  ${chip(68, 562, 242, "HPIS SDK · 生成目标索引密文", "new")}${chip(320, 562, 242, "HPIS SDK · 密封目标向量", "new")}

  ${flow("M582 458 L644 458", "① 上传", 607, 446, "middle", 0)}
  ${flow("M118 340 L118 196", "② 匿名令牌请求", 128, 270, "start", 0.2)}
  ${flow("M420 340 L420 266", "③ 推理请求 + 数据密钥 + 目标索引/向量密文", 408, 324, "end", 0.4)}
  ${flow("M572 130 L644 130", "④ 密封检索", 610, 120, "middle", 0.6)}
  ${flow("M650 182 L578 182", "⑤ K 条密文", 612, 202, "middle", 0.8)}
  ${flow("M520 266 L520 336", "⑦ 推理结果", 532, 324, "start", 1)}
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
    }
    const scenChanged = !!state.multi && JSON.stringify(s.multi?.scenarios || null) !== JSON.stringify(state.multi.scenarios || null);
    if (first || (s.presets && s.presets.length !== state.single.length) || !!s.multi?.presets?.length !== !!state.multi || (scenChanged && !state.running)) {
      initPresets(s);
      if (scenChanged && !first && state.mode === "multi") { redrawPlain(); resetLanes(); }
    }
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
  if (state.mode !== "multi") $("#question").placeholder = "向你的照片、邮件、运动与出行数据提问…";
  $("#cloud-sub").textContent = "左边是终端本地的原始数据：同一个人的邮件、照片、运动与出行记录。右边是这些数据在 HPIS 安全存储平台中的样子，直接读取自 HPIS 的存储文件。";
  if (!state.running) { applyKind(state.kind, state.device); applyMulti(); renderMeta(); }
}
function bootOverlay(s) {
  const boot = $("#boot");
  if (s.phase === "ready") { boot.classList.add("off"); return; }
  boot.classList.remove("off");
  const total = s.mailbox?.emails || 100000;
  const titles = { unpacking: "首次运行：解压多端数据与向量", building: "首次运行：在本机加密预置多端数据", "starting-services": "启动安全存储平台与机密计算平台", warming: "预热密态索引", error: "启动失败" };
  $("#boot-title").textContent = titles[s.phase] || "正在启动密态服务…";
  if (s.phase === "error") $("#boot-sub").textContent = s.error || "";
  const bars = [["tee", "HPIS 密态租户（AES-GCM + HMAC + 向量密文）"], ["plain", "常规检索对照数据"], ["upload", "HPIC 1.0 上传批次"]];
  $("#boot-bars").innerHTML = s.phase === "building" ? bars.map(([k, l]) => {
    const n = (s.progress || {})[k] || 0;
    return `<div class="bootbar"><label><span>${l}</span><span class="num">${intText(n)} / ${intText(total)}</span></label><div><i style="width:${Math.min(100, (n / total) * 100)}%"></i></div></div>`;
  }).join("") : "";
}
// the data strip right of the presets: what the device in play holds
function renderMeta() {
  const s = state.status;
  if (!s?.mailbox) return;
  if (lane("plain")?.multi) {
    $("#mailbox-meta").innerHTML = DEVICE_ORDER.filter((d) => state.devices.has(d)).map((d) => {
      const dev = deviceOf(d), K = kindOf(dev.kind);
      return `<span class="mdev">${ICON(dev.icon)}<b>${wanText(dev.records || 0)}</b>${K.unit}${K.short}</span>`;
    }).join("");
    return;
  }
  const dev = state.device ? deviceOf(state.device) : null;
  const tag = dev ? `<span class="mdev">${ICON(dev.icon)}数据来自${esc(dev.label)}</span>` : "";
  if (state.kind === "mail" || !dev) {
    $("#mailbox-meta").innerHTML = `${tag}<span>${ICON("mail")} <b>${intText(s.mailbox.emails)}</b> 封邮件</span><span>${ICON("paperclip")} <b>${intText(s.mailbox.attachments)}</b> 个附件</span><span>${ICON("clock")} ${s.mailbox.first.slice(0, 7)} ~ ${s.mailbox.last.slice(0, 7)}</span>`;
    return;
  }
  const K = kindOf(state.kind);
  $("#mailbox-meta").innerHTML = `${tag}<span>${ICON(K.icon)} <b>${intText(dev.records)}</b> ${K.unit}${K.noun}</span>${dev.bytes ? `<span>${ICON("hard-drive")} ${bytesText(dev.bytes)}</span>` : ""}`;
}
const keyLabel = (i) => (i < 9 ? String(i + 1) : i === 9 ? "0" : "");
// The preset questions are shown by scene, in this order; a preset may name its scene (体检对比 is 运动健康).
const SCENES = [
  { id: "daily", label: "日常生活", icon: "calendar", hint: "邮件 · 用车出行" },
  { id: "album", label: "一生相随", icon: "camera", hint: "相册" },
  { id: "health", label: "运动健康", icon: "activity", hint: "体检 · 运动 · 睡眠" },
];
const sceneOf = (p) => p.scene || { photo: "album", sport: "health" }[p.kind || "mail"] || "daily";
const STRIP_DEVICES = ["pc", "phone", "watch", "car"]; // at the far left: the questions draw on all four devices
function initPresets(s) {
  const list = s.presets || [];
  const grouped = list.some((p) => p.device || p.kind);
  // the numbering (and the number keys) follow the order on screen
  state.single = grouped ? SCENES.flatMap((sc) => list.filter((p) => sceneOf(p) === sc.id)) : list;
  state.grouped = grouped;
  state.multi = s.multi?.presets?.length ? s.multi : null;
  if (!scenarios().some((x) => x.id === state.scen)) state.scen = scenarios()[0].id;
  $("#modes").hidden = !state.multi;
  const want = state.multi && (state.mode === "multi" || (!state.moded && PARAMS.get("mode") === "multi")) ? "multi" : "single";
  state.moded = true;
  if (want !== state.mode || !$("#presets").childElementCount) setMode(want, true);
  else renderPresets();
}
const chipHtml = (p, i) => `<button class="preset" data-i="${i}">${keyLabel(i) ? `<span class="k">${keyLabel(i)}</span>` : `<span class="k k--none"></span>`}${esc(p.chip)}</button>`;
function renderPresets() {
  const host = $("#presets");
  if (state.mode === "multi") {
    state.presets = state.multi.presets;
    host.innerHTML = `<span class="pgroup"><span class="pscene">${ICON("layers")}多端协同</span>${state.presets.map(chipHtml).join("")}</span>${scenHtml()}`;
    host.className = "presets multi";
  } else {
    state.presets = state.single;
    const list = state.single;
    if (!state.grouped) {
      host.innerHTML = list.map(chipHtml).join("");
    } else {
      const devOf = (p) => p.device || deviceFor(p.kind || "mail");
      const devs = STRIP_DEVICES.filter((d) => list.some((p) => devOf(p) === d));
      // two devices above, two below, beside the two rows of questions
      let html = `<span class="pdevs" title="多端数据">${devs
        .map((d) => `<span class="pdev">${ICON(deviceOf(d).icon)}${esc(deviceOf(d).label)}</span>`)
        .join("")}</span><span class="pscenes">`;
      for (const sc of SCENES) {
        const items = list.map((p, i) => [p, i]).filter(([p]) => sceneOf(p) === sc.id);
        if (items.length) html += `<span class="pgroup"><span class="pscene" title="${sc.hint}">${ICON(sc.icon)}${sc.label}</span>${items.map(([p, i]) => chipHtml(p, i)).join("")}</span>`;
      }
      host.innerHTML = html + "</span>";
    }
    host.className = `presets${state.grouped ? " grouped" : ""}`;
  }
  const cur = state.presets.findIndex((p) => p.question === $("#question").value);
  if (cur >= 0) $$(".preset").forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === cur));
}
// 单端问答 | 多端协同: the lanes, presets and data strip are rebuilt for the mode
function setMode(mode, quiet = false) {
  if (state.running || (mode === "multi" && !state.multi)) return;
  const changed = mode !== state.mode || !state.lanes.plain?.el.isConnected || !!lane("plain")?.multi !== (mode === "multi");
  state.mode = mode;
  document.body.dataset.mode = mode;
  $$("#modes button").forEach((b) => { b.classList.toggle("active", b.dataset.mode === mode); b.setAttribute("aria-selected", b.dataset.mode === mode); });
  $("#verdict").classList.remove("on");
  if (changed) {
    buildLanes();
    resetLanes();
    if (mode === "single") applyKind(state.kind, state.device);
  }
  $("#question").value = "";
  renderPresets();
  if (state.presets.length) selectPreset(0);
  idleStrip(true);
  renderMeta();
  $("#question").placeholder = mode === "multi" ? "向手机、手表、PC、车机上的全部数据提问…" : state.devices.size ? "向你的照片、邮件、运动与出行数据提问…" : "向你的私人邮箱提问…";
  if (!quiet) $("#question").focus({ preventScroll: true });
}
// The HPIC 1.0 scenario switch, then its device choice: 场景一 toggles the devices synced to the phone,
// 场景二 picks the one device that uploads.
function scenHtml() {
  const { sc } = hpic1Pick();
  const pick = state.pick[sc.id] || sc.default || [];
  const one = sc.choose === "one" || sc.route === "direct";
  const opts = scenarios().map((x) => `<button type="button" role="radio" class="sopt${x.id === sc.id ? " active" : ""}" data-scen="${esc(x.id)}" aria-checked="${x.id === sc.id}"><span>${esc(x.label)}</span><em>${esc(x.issue)}</em></button>`).join("");
  const devs = (sc.devices || []).map((d) => {
    const on = pick.includes(d);
    return `<button type="button" role="${one ? "radio" : "checkbox"}" aria-checked="${on}" class="sdev${on ? " on" : ""}${one ? " one" : ""}" data-dev="${esc(d)}">${ICON(deviceOf(d).icon)}${esc(deviceOf(d).label)}</button>`;
  }).join("");
  return `<span class="scen"><span class="scen__label">${ICON("cloud-upload")}HPIC 1.0</span><span class="scen__opts" role="radiogroup" aria-label="HPIC 1.0 的场景">${opts}</span><span class="sdevs" role="${one ? "radiogroup" : "group"}" aria-label="${one ? "直传的设备" : "同步到手机的设备"}"><span class="sdevs__label">${one ? "直传设备" : "同步到手机"}</span>${devs}</span></span>`;
}
// a device just chosen for HPIC 1.0 (场景一: synced to the phone; 场景二: the one that uploads): its data moves in
// both drawings, to the phone or HPIC 1.0 on the left, and on the right into HPIS, where HPIC 2.0 keeps it encrypted
function syncShow(d) {
  const P = lane("plain"), V = lane("v2");
  if (!P?.multi || !V || state.running) return;
  const link = hpic1Pick().route === "direct" ? "edge" : `s_${d}`;
  P.flow.flash(nid(d), 1100);
  P.flow.glow(link, 1100);
  P.flow.burst(link, 4, { kind: "plaintext", every: 150, dur: 620 });
  V.flow.flash(nid(d), 1100);
  V.flow.glow(`s_${d}`, 1100);
  V.flow.burst(`s_${d}`, 4, { kind: "cipher", every: 150, dur: 620 });
}
// once a run starts, only the device that sends something for the question stays lit: the phone (its upload to
// HPIC 1.0, its sealed request to HPIC 2.0), or in 场景二 on the left the one device that uploads. The others
// become shadows until the next choice; their data still counts (synced to the phone, or stored in HPIS).
function shadeDevices() {
  const P = lane("plain"), V = lane("v2");
  if (!P?.multi || !V) return;
  const sender = state.run?.route === "direct" ? state.run.hpic1?.[0] : "phone";
  for (const d of DEVICE_ORDER) {
    P.flow.shade(d, d !== sender);
    V.flow.shade(d, d !== "phone");
    // HPIC 2.0: the data was stored in HPIS once, before the question, so its dotted links fade for the run and
    // each device says where its data is
    V.flow.fade(`s_${d}`, true);
    if (d !== "phone") V.flow.node(nid(d), null, "✓ 已存入 HPIS", "ok");
  }
}
// a new scenario or device choice: redraw HPIC 1.0's topology and clear the last run
function changeScen() {
  renderPresets();
  redrawPlain();
  resetLanes();
}
// 多端协同: the lanes' texts and the idle state of both drawings for the chosen scenario (or the run on screen)
function applyMulti() {
  const P = lane("plain"), V = lane("v2");
  if (!P?.multi) return;
  const ctx = boardCtx();
  const sc = scenarios().find((x) => x.id === ctx.scen) || scenario();
  const direct = ctx.route === "direct", got = ctx.hpic1;
  $(".lane__id", P.el).innerHTML = `${esc(sc.label)}<em class="lane__issue">${esc(sc.issue)}</em>`;
  $("[data-desc]", P.el).textContent = direct
    ? `${devNames(got)}只把自己的数据直接上传到 HPIC 1.0，手机和其他设备的数据这次用不上`
    : "手表、PC、车机先把数据同步到手机，每次提问由手机全部上传到 HPIC 1.0";
  $('[data-stage="upload"] span', P.el).textContent = direct ? "直传数据" : "手机上传";
  const size = got.reduce((a, d) => a + (deviceOf(d).bytes || 0), 0);
  const photos = deviceOf("phone").records;
  for (const d of DEVICE_ORDER) {
    const has = got.includes(d);
    if (direct) {
      P.flow.off(nid(d), !has);
      P.flow.idle(nid(d), has ? "只传本机数据" : "未上传");
    } else if (d === "phone") {
      P.flow.idle("dev", photos ? `含本机照片 ${intText(photos)} 张` : "含本机照片");
    } else {
      P.flow.off(d, !has);
      P.flow.linkOff(`s_${d}`, !has);
      P.flow.idle(d, has ? "已同步" : "未同步", has ? "" : "miss");
    }
    const aid = deviceOf(d).aid;
    V.flow.off(nid(d), !ctx.need.includes(d));
    V.flow.idle(nid(d), d === "phone" ? (aid ? `照片 AID ${aid.slice(0, 8)}` : "照片已加密存入") : aid ? `AID ${aid.slice(0, 8)}` : "已加密存入");
  }
  // before the run, the link says what HPIC 1.0 gets per question
  if (!state.running && !P.final) P.flow.linkValue("edge", `${direct ? "直传 " : ""}${bytesText(size)}`, "big");
  // a run on screen (running or finished) keeps its look: shadows, faded HPIS links, each device's fixed status
  if (ctx.live) shadeDevices();
  setTicker("plain");
  renderBoard();
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
  if (state.mode === "multi") {
    state.mpreset = p;
    if (!state.running) resetLanes();
    return;
  }
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
function loadPlainTicker() {
  const sens = state.status?.mailbox?.sensitive || {};
  const head = Object.values(sens).filter((x) => x.count).map((x) => `${x.label} ${intText(x.count)} 封`).join(" · ");
  if (head) state.plainTicker = `每次提问由手机上传整个邮箱：${head}　｜　${REUPLOAD}`;
  if (!state.running) setTicker("plain");
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
  const name = `个人智能计算平台2.0-${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}.${type.includes("mp4") ? "mp4" : "webm"}`;
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
  // 进入业务演示 from the cover always starts in 单端问答 (多端协同 is one click away)
  $("#enter").addEventListener("click", () => { if (state.mode !== "single") setMode("single"); hideCover("race"); });
  $("#run").addEventListener("click", run);
  $("#stop").addEventListener("click", () => stopRun("stopped"));
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  $("#question").addEventListener("input", () => {
    $$(".preset").forEach((b) => b.classList.remove("active"));
    if (state.mode === "multi") { state.mpreset = null; applyMulti(); }
    idleStrip();
  });
  $("#presets").addEventListener("click", (e) => {
    const b = e.target.closest(".preset");
    if (b) selectPreset(Number(b.dataset.i));
    const so = e.target.closest(".sopt");
    if (so && !state.running && so.dataset.scen !== state.scen) { state.scen = so.dataset.scen; changeScen(); }
    const sd = e.target.closest(".sdev");
    if (sd && !state.running) {
      const { sc } = hpic1Pick();
      const cur = state.pick[sc.id] || sc.default || [];
      const d = sd.dataset.dev;
      if (sc.choose === "one" || sc.route === "direct") state.pick[sc.id] = [d];
      else state.pick[sc.id] = (sc.devices || []).filter((x) => (x === d ? !cur.includes(x) : cur.includes(x)));
      changeScen();
      if (state.pick[sc.id].includes(d)) syncShow(d);
    }
  });
  $("#modes").addEventListener("click", (e) => { const b = e.target.closest("[data-mode]"); if (b) setMode(b.dataset.mode); });
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
    if (c) {
      const n = Number(c.dataset.n), ev = lane(laneEl.dataset.lane)?.evidence || [];
      const i = ev.findIndex((x) => x.n === n);
      openEvidence(laneEl.dataset.lane, i >= 0 ? i : n - 1);
      return;
    }
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
