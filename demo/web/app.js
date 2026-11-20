// HPIS 2.0 demo — front-end
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
const ICON = (id, cls = "i") => `<svg class="${cls}"><use href="/assets/icons.svg#${id}"/></svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const state = {
  status: null,
  presets: [],
  model: null,
  running: false,
  lanes: {},
  verify: null,
  plainTicker: "",
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

// ------------------------------------------------------------------ ambient
function ambient() {
  const c = $("#ambient");
  const ctx = c.getContext("2d");
  const glyphs = "0123456789abcdef";
  let w, h, cols;
  const drops = [];
  function size() {
    w = c.width = innerWidth * devicePixelRatio;
    h = c.height = innerHeight * devicePixelRatio;
    cols = Math.floor(w / (28 * devicePixelRatio));
    drops.length = 0;
    for (let i = 0; i < 70; i++) drops.push({ x: Math.random() * w, y: Math.random() * h, v: 0.15 + Math.random() * 0.45, a: Math.random() * 0.5, g: glyphs[(Math.random() * 16) | 0] });
  }
  size();
  addEventListener("resize", size);
  let last = 0;
  function frame(t) {
    requestAnimationFrame(frame);
    if (t - last < 40) return;
    last = t;
    ctx.clearRect(0, 0, w, h);
    ctx.font = `${12 * devicePixelRatio}px JBMono, monospace`;
    for (const d of drops) {
      d.y += d.v * devicePixelRatio * 2;
      if (d.y > h) { d.y = -20; d.x = Math.random() * w; }
      if (Math.random() < 0.02) d.g = glyphs[(Math.random() * 16) | 0];
      ctx.fillStyle = `rgba(63,240,200,${0.05 + d.a * 0.12})`;
      ctx.fillText(d.g, d.x, d.y);
    }
  }
  requestAnimationFrame(frame);
}

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

// ------------------------------------------------------------------ lane config
const LANES = {
  plain: {
    title: "端侧直传普通云", en: "Plain upload", icon: "cloud-upload",
    desc: "每次提问把整个邮箱上传到普通云，由云端明文检索并回答",
    stages: [["upload", "上传邮箱"], ["retrieve", "云端解析与检索"], ["generate", "模型生成"]],
    nodes: [["dev", 70, "smartphone", "终端", "10 万封邮件"], ["cloud", 486, "cloud", "普通云", "明文处理"]],
    links: [["edge", "dev", "cloud", "邮件与附件 · 分批上传"]],
  },
  v1: {
    title: "HPIS 1.0", en: "Full fetch", icon: "database",
    desc: "数据匿名加密存储，但每次检索都要把该用户全部密文搬到 HPIC 解密",
    stages: [["request", "密封请求"], ["fetch", "全量取回"], ["decrypt", "全部解密检索"], ["generate", "模型生成"]],
    nodes: [["dev", 56, "smartphone", "终端", "掌控密钥"], ["hpic", 290, "cpu", "HPIC", "可信计算"], ["hpis", 512, "database", "HPIS", "匿名密态存储"]],
    links: [["edge", "dev", "hpic", "推理请求"], ["cloudl", "hpic", "hpis", "全量数据密文"]],
  },
  v2: {
    title: "HPIS 2.0", en: "Encrypted search", icon: "shield-check",
    desc: "SSE 在密文索引上精确筛选，向量在可信计算区内精确排序，只取回命中的几封密文",
    stages: [["request", "密封请求"], ["search", "密态检索"], ["decrypt", "按需解密"], ["generate", "模型生成"]],
    nodes: [["dev", 56, "smartphone", "终端", "掌控密钥"], ["hpic", 290, "cpu", "HPIC", "可信计算"], ["hpis", 512, "database", "HPIS", "SSE + 密态向量"]],
    links: [["edge", "dev", "hpic", "推理请求 + 目标索引密文"], ["cloudl", "hpic", "hpis", "部分数据密文"]],
  },
};

// ------------------------------------------------------------------ flow diagram
const flows = new Set();
class Flow {
  constructor(host, cfg) {
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("viewBox", "0 0 568 150");
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
      this.el("rect", { width: 72, height: 56, rx: 16 }, g);
      const use = this.el("use", { href: `/assets/icons.svg#${icon}`, x: 22, y: 14, width: 28, height: 28 }, g);
      use.setAttribute("style", "fill:none;stroke:currentColor");
      const l = this.el("text", { x: 36, y: 76, class: "nlabel" }, g);
      l.textContent = label;
      const s = this.el("text", { x: 36, y: 92, class: "nsub" }, g);
      s.textContent = sub;
      this.nodes[id] = { g, s, sub };
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
    const c = this.el("circle", { r, class: `packet ${kind}`, cx: reverse ? l.x2 : l.x1, cy: l.y });
    this.packets.push({ c, l, reverse, t0: performance.now(), dur });
  }
  stream(id, on, opts = {}) {
    clearInterval(this.streams[id]);
    delete this.streams[id];
    if (on) this.streams[id] = setInterval(() => this.send(id, opts), opts.every || 70);
  }
  reset(cfg) {
    for (const k of Object.keys(this.streams)) this.stream(k, false);
    for (const p of this.packets) p.c.remove();
    this.packets = [];
    for (const [id, , , , sub] of cfg.nodes) this.node(id, null, sub);
    for (const id of Object.keys(this.links)) { this.linkValue(id, ""); this.hot(id, false); }
  }
  frame(now) {
    this.packets = this.packets.filter((p) => {
      const t = (now - p.t0) / p.dur;
      if (t >= 1) { p.c.remove(); return false; }
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const x = p.reverse ? p.l.x2 - (p.l.x2 - p.l.x1) * e : p.l.x1 + (p.l.x2 - p.l.x1) * e;
      p.c.setAttribute("cx", x);
      p.c.setAttribute("opacity", t > 0.85 ? (1 - t) / 0.15 : 1);
      return true;
    });
  }
}
(function animate(now) {
  for (const f of flows) f.frame(now);
  requestAnimationFrame(animate);
})(performance.now());

// ------------------------------------------------------------------ lanes
function buildLanes() {
  const host = $("#lanes");
  host.innerHTML = "";
  host.classList.add("two");
  for (const id of ["plain", "v2"]) {
    const cfg = LANES[id];
    const el = document.createElement("article");
    el.className = `lane card lane--${id}`;
    el.dataset.lane = id;
    const seesCls = id === "plain" ? "sees--bad" : id === "v1" ? "sees--mid" : "sees--good";
    const seesLabel = id === "plain" ? `${ICON("eye")}云端可见明文` : id === "v1" ? `${ICON("unlock")}HPIC 解密` : `${ICON("eye-off")}存储侧仅见密文`;
    el.innerHTML = `
      <header class="lane__head">
        <div class="lane__icon">${ICON(cfg.icon)}</div>
        <div class="lane__title"><h3>${cfg.title}<small>${cfg.en}</small></h3><p>${cfg.desc}</p></div>
        <span class="pill" data-pill>待命</span>
      </header>
      <div class="flow" data-flow></div>
      <ol class="rail">${cfg.stages.map(([k, l]) => `<li data-stage="${k}"><span>${l}</span><b>—</b></li>`).join("")}</ol>
      <div class="metrics">
        <div class="metric ${id === "plain" ? "bad" : id === "v2" ? "good" : ""}"><label>${ICON("signal")}端云传输</label><output data-m="edge">—</output></div>
        <div class="metric ${id === "v1" ? "v1" : id === "v2" ? "good" : ""}"><label>${ICON("server")}云云传输</label><output data-m="cloud">—</output></div>
        <div class="metric"><label>${ICON("zap")}首字响应</label><output data-m="ttft">—</output></div>
        <div class="metric"><label>${ICON("clock")}完整回答</label><output data-m="total">—</output></div>
      </div>
      <div class="sees ${seesCls}"><span class="sees__label">${seesLabel}</span><div class="ticker"><span class="ticker__track ${id === "plain" ? "bad" : "mono"}" data-ticker></span></div></div>
      <div class="answer">
        <div class="answer__head"><span>${ICON("brain")} <b data-model></b> 的回答</span><span data-evcount></span></div>
        <div class="answer__text" data-answer><span class="ph">等待提问…</span></div>
        <div class="evlist" data-evlist></div>
        <div class="answer__foot" data-quality></div>
      </div>`;
    host.appendChild(el);
    const flow = new Flow($("[data-flow]", el), cfg);
    state.lanes[id] = { id, el, cfg, flow, text: "", evidence: [], stageT: {}, m: {} };
    setTicker(id);
  }
}

function lane(id) { return state.lanes[id]; }
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
    const start = L.stageT[key] ?? t;
    $("b", li).textContent = label ?? secsText(Math.max(0, t - start));
  }
}
function setTicker(id, text) {
  const L = lane(id);
  if (!L) return;
  const tr = $("[data-ticker]", L.el);
  const defaults = {
    plain: state.plainTicker || "邮件原文、附件、体检报告、账单、合同金额、联系方式……全部以明文进入普通云",
    v1: "HPIS 只存密文；但 HPIC 每次要解密该用户的全部邮件，才能找出需要的那几封",
    v2: "AID · HMAC-SHA256 检索令牌 · AES-256-GCM 正文密文 · 密封的目标向量",
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

function resetLanes() {
  for (const L of Object.values(state.lanes)) {
    L.flow.reset(L.cfg);
    L.text = "";
    L.evidence = [];
    L.stageT = {};
    L.m = {};
    L.done = false;
    L.el.classList.remove("running", "done");
    for (const li of $$(".rail li", L.el)) { li.className = ""; li.style.removeProperty("--p"); $("b", li).textContent = "—"; clearInterval(L["sp_" + li.dataset.stage]); }
    for (const o of $$("output", L.el)) { o.textContent = "—"; o.dataset.v = 0; }
    $("[data-answer]", L.el).innerHTML = `<span class="ph">等待提问…</span>`;
    $("[data-evlist]", L.el).innerHTML = "";
    $("[data-quality]", L.el).innerHTML = "";
    $("[data-evcount]", L.el).textContent = "";
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

function renderEvidence(id) {
  const L = lane(id);
  $("[data-evcount]", L.el).textContent = L.evidence.length ? `取证 ${L.evidence.length} 封` : "";
  $("[data-evlist]", L.el).innerHTML = L.evidence
    .map((e, i) => `<span class="ev" data-id="${e.id}" title="${esc(e.subject)}"><b>${i + 1}</b><span>${esc(e.subject)}</span></span>`)
    .join("");
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
  const gold = preset.gold.evidence_ids || [];
  const ev = gold.filter((g) => citedIds.has(g)).length;
  const retrieved = gold.filter((g) => L.evidence.some((e) => e.id === g)).length;
  host.innerHTML = `
    <span class="qbadge ${hit === facts.length ? "ok" : "warn"}">${ICON("check-circle")}关键事实 ${hit}/${facts.length}</span>
    <span class="qbadge ${retrieved === gold.length ? "ok" : "warn"}">${ICON("file-text")}检出证据 ${retrieved}/${gold.length}</span>
    <span class="qbadge ${ev > 0 ? "ok" : "warn"}">${ICON("paperclip")}引用 ${citedIds.size} 封</span>`;
  return { hit, facts: facts.length, ev, gold: gold.length };
}

// ------------------------------------------------------------------ run
function modelLabel() {
  const m = (state.status?.models || []).find((x) => x.id === state.model);
  return m ? m.label : state.model || "";
}

async function run() {
  if (state.running) return;
  const question = $("#question").value.trim();
  if (!question) return;
  if (state.status?.phase !== "ready") return;
  state.running = true;
  $("#run").disabled = true;
  $("#run span").textContent = "对比中…";
  resetLanes();
  const lanes = ["plain", "v2"];
  for (const id of lanes) { pill(id, "运行中", "run"); lane(id).el.classList.add("running"); }
  $("#device-chips").innerHTML = "";
  const body = JSON.stringify({ question, model: state.model, lanes, net: state.net });
  const t0 = performance.now();
  clearInterval(state.clock);
  state.clock = setInterval(() => {
    const el = performance.now() - t0;
    for (const id of lanes) {
      const L = lane(id);
      if (!L || L.done) continue;
      if (L.m.ttft == null) { const o = $('[data-m="ttft"]', L.el); renderSecs(o, el); o.dataset.v = el; o.classList.add("ticking"); }
      const o2 = $('[data-m="total"]', L.el); renderSecs(o2, el); o2.dataset.v = el; o2.classList.add("ticking");
    }
  }, 60);
  try {
    const res = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body });
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
    for (const id of lanes) pill(id, "出错", "err");
    console.error(err);
  } finally {
    clearInterval(state.clock);
    for (const o of $$(".ticking")) o.classList.toggle("ticking", false);
    state.running = false;
    $("#run").disabled = false;
    $("#run span").textContent = "开始对比";
  }
}

function dchip(label, value, code) {
  const d = document.createElement("span");
  d.className = "dchip";
  d.innerHTML = `<em>${esc(label)}</em>${code ? `<code>${esc(value)}</code>` : `<b>${esc(value)}</b>`}`;
  $("#device-chips").appendChild(d);
}

function handle(ev, question) {
  const id = ev.lane;
  const L = id ? lane(id) : null;
  const t = ev.t;
  switch (ev.type) {
    case "plan": {
      dchip("查询向量", `512 维 · ${ev.embed_ms.toFixed(0)} ms`);
      for (const c of ev.chips.filter((c) => c.kind !== "语义")) dchip(c.kind, c.value);
      break;
    }
    case "sealed": {
      dchip("SSE 检索令牌", ev.tokens.length ? ev.tokens[0].slice(0, 16) + "…" : "无", true);
      dchip("密封请求", bytesText(ev.bytes));
      setTicker("v2", `匿名标识 AID · 检索令牌 ${ev.tokens.map((x) => x.slice(0, 12)).join(" ")} · 密封目标向量 ${ev.preview} · 正文 AES-256-GCM 密文`);
      break;
    }
    case "stage": {
      if (!L) break;
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
        metric(id, "ttft", ev.ttft_ms);
        stage(id, "generate", "start", L.stageT.generate ?? t);
      }
      break;
    }
    case "upload": {
      metric(id, "edge", ev.edge_up, "bytes");
      L.flow.linkValue("edge", `${bytesText(ev.edge_up)} · ${ev.batch}/${ev.batches} 批`, "big");
      stage(id, "upload", "progress", t, ev.batch / ev.batches);
      break;
    }
    case "auth": {
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
      const key = id === "v1" ? "fetch" : "search";
      L.flow.stream("cloudl", false);
      L.flow.hot("cloudl", false);
      const cloud = ev.up_bytes + ev.down_bytes;
      metric(id, "cloud", cloud, "bytes");
      L.flow.linkValue("cloudl", `${bytesText(cloud)} · ${intText(ev.rows)} 封`, id === "v1" ? "big" : "small");
      if (id === "v2") {
        L.flow.send("cloudl", { reverse: true, kind: "cipher", r: 3.5, dur: 420 });
        L.flow.node("hpis", "active", `命中 ${intText(ev.vector_rows)} → 取 ${ev.rows} 封`, "hot");
      } else {
        L.flow.node("hpis", "active", `搬出 ${intText(ev.rows)} 封密文`, "bad");
      }
      stage(id, key, "end", t, secsText(ev.hop_ms));
      stage(id, "decrypt", "start", t);
      L.flow.node("hpic", "active", "解密中…");
      break;
    }
    case "decrypt": {
      L.decryptMs = ev.ms;
      L.decryptRows = ev.rows;
      L.flow.node("hpic", "active", `解密 ${intText(ev.rows)} 封`, id === "v1" ? "bad" : "hot");
      setTicker(id, id === "v1"
        ? `HPIC 本次解密 ${intText(ev.rows)} 封邮件（${secsText(ev.ms)}），在明文中再检索；HPIS 侧仍只见密文`
        : `HPIC 本次仅解密 ${ev.rows} 封命中邮件 · HPIS 只见匿名标识、HMAC 令牌与 AES-GCM 密文`);
      break;
    }
    case "search": {  // plain cloud
      stage(id, "retrieve", "end", t, secsText(ev.search_ms));
      L.flow.node("cloud", "alert", `解析 ${intText(ev.scanned)} 封 · 现算向量 ${intText(ev.candidates)} 封`, "bad");
      $(`[data-stage="retrieve"] b`, L.el).textContent = secsText(ev.search_ms);
      break;
    }
    case "evidence": {
      L.evidence = ev.items;
      renderEvidence(id);
      if (id !== "plain") stage(id, "decrypt", "end", t, secsText((L.decryptMs || 0) + (ev.rank_ms || 0)));
      stage(id, "generate", "start", t);
      $("[data-answer]", L.el).innerHTML = `<span class="ph">模型生成中…</span>`;
      break;
    }
    case "token": {
      L.text += ev.text;
      renderAnswer(id);
      break;
    }
    case "done": {
      L.llm = ev;
      break;
    }
    case "finish": {
      L.done = true;
      renderAnswer(id, true);
      stage(id, "generate", "end", t);
      metric(id, "ttft", ev.ttft_ms);
      metric(id, "total", ev.total_ms);
      metric(id, "edge", ev.edge_up + ev.edge_down, "bytes");
      if (L.m.cloud == null) metric(id, "cloud", id === "plain" ? 0 : null, "bytes");
      L.final = ev;
      L.q = quality(id, question);
      const note = L.llm?.replay ? "完成 · 回放" : L.llm?.fallback ? "完成 · 摘录" : "完成";
      pill(id, note, "ok");
      L.el.classList.remove("running");
      L.el.classList.add("done");
      if (id === "plain") L.flow.node("cloud", "alert", "已读取全部明文", "bad");
      break;
    }
    case "verify": {
      state.verify = ev;
      break;
    }
    case "error": {
      if (L) { pill(id, "出错", "err"); $("[data-answer]", L.el).innerHTML = `<span class="ph">${esc(ev.message)}</span>`; }
      break;
    }
    case "end": {
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
  const tiles = [];
  const pe = P.final.edge_up + P.final.edge_down, ve = V2.final.edge_up + V2.final.edge_down;
  tiles.push({ big: fold(pe / ve), title: "端云传输减少", sub: `普通云 ${bytesText(pe)} → ${bytesText(ve)}` });
  if (V2.decryptRows && state.status?.mailbox?.emails) {
    const share = (100 * V2.decryptRows) / state.status.mailbox.emails;
    tiles.push({ big: `${share < 0.1 ? share.toFixed(2) : share.toFixed(1)}<small>%</small>`, title: "HPIS 只读命中密文", sub: `${V2.decryptRows} / ${intText(state.status.mailbox.emails)} 封 · 云内 ${bytesText(V2.m.cloud)}` });
  }
  if (P.final.ttft_ms && V2.final.ttft_ms) {
    tiles.push({ big: fold(P.final.ttft_ms / V2.final.ttft_ms), title: "首字响应更快", sub: `普通云 ${secsText(P.final.ttft_ms)} → ${secsText(V2.final.ttft_ms)}` });
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
  tiles.push({ big: `0<small>封</small>`, title: "存储侧可见明文", sub: `HPIC 可信区仅解密 ${V2.decryptRows || "K"} 封 · 普通云可读 ${intText(state.status?.mailbox?.emails)} 封` });
  $("#verdict-grid").innerHTML = tiles.map((x, i) => `<div class="vstat" style="animation-delay:${i * 90}ms"><b>${x.big}</b><span>${x.title}</span><em>${esc(x.sub)}</em></div>`).join("");
  $("#verdict").hidden = false;
  requestAnimationFrame(() => {
    $("#verdict").classList.add("on");
    document.body.classList.add("scoreboard", "results");
    for (const id of ["plain", "v2"]) { const b = lane(id) && $("[data-answer]", lane(id).el); if (b) b.scrollTop = 0; }
  });
}

// ------------------------------------------------------------------ drawer
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
  const body = $("#drawer-body");
  body.dataset.plain = plain;
  body.dataset.id = mailId;
  body.innerHTML = plain;
  $$(".drawer__seg button").forEach((b) => b.classList.toggle("active", b.dataset.view === "plain"));
  $("#drawer").classList.add("on");
  $("#scrim").classList.add("on");
}
async function cipherView() {
  const body = $("#drawer-body");
  const res = await fetch(`/api/cipher/${body.dataset.id}`);
  const c = await res.json();
  body.innerHTML = `<div class="cipherview">
    <h5>匿名标识 AID</h5><div>${c.aid}</div>
    <h5>记录号</h5><div>${c.rid}</div>
    <h5>正文密文 AES-256-GCM（${bytesText(c.bodyBytes)}，节选）</h5><div>${c.body}</div>
    <h5>SSE 检索令牌 HMAC-SHA256（${c.tags.length} 个）</h5>${c.tags.map((t) => `<div>${t}</div>`).join("")}
    <h5>向量密文 AES-256-GCM（${bytesText(c.vectorBytes)}，节选）</h5><div>${c.vector}</div>
    <p class="muted" style="font-family:var(--cn);margin-top:1.2rem">以上数据直接读取自 HPIS 的存储文件。HPIS 无法从中得知发件人、主题、时间或内容；只有持有密钥的终端，或终端授权的 HPIC 在本次请求内，才能打开命中的这几条。</p>
  </div>`;
}
function closeDrawer() { $("#drawer").classList.remove("on"); $("#scrim").classList.remove("on"); }

// ------------------------------------------------------------------ cloud view
function renderProv() {
  const b = state.status?.build;
  if (!b || !b.deployments) return;
  const tee = b.deployments.tee || {};
  const up = b.upload || {};
  const per = 3.2e3;
  $("#prov").innerHTML = `
    <div class="prov__title">${ICON("shield-check")}<div>一次性加密预置<small>终端加密后上传一次，之后每次提问只发几 KB</small></div></div>
    <div><b>${intText(b.emails)}</b><span>封邮件加密入库</span></div>
    <div><b>${tee.seconds ? tee.seconds.toFixed(0) + " s" : "—"}</b><span>本机预置用时（含 SSE 令牌与向量密文）</span></div>
    <div><b>${bytesText(tee.store_bytes)}</b><span>HPIS 侧密文与索引存储</span></div>
    <div><b>${up.bytes ? fold(up.bytes / per).replace(/<[^>]+>/g, "") : "—"}</b><span>相比每次直传整个邮箱（${bytesText(up.bytes)}），每次提问的端云流量之比</span></div>`;
}
async function loadCloudView() {
  renderProv();
  const [plain, hp] = await Promise.all([fetch("/api/cloudview?kind=plain&limit=60").then((r) => r.json()), fetch("/api/cloudview?kind=hpis&limit=60").then((r) => r.json())]);
  state.plainRows = plain.rows;
  renderPlainRows();
  $("#plain-count").textContent = `${intText(state.status?.mailbox?.emails)} 封 · 全部可读`;
  $("#hpis-count").textContent = `${intText(hp.docs)} 条密文 · ${bytesText(hp.bytes)} · ${intText(hp.postings)} 个令牌`;
  $("#hpis-rows").innerHTML = hp.rows.map((r) => `<div class="row"><div class="k">AID ${r.aid.slice(0, 8)}… #${r.rid}</div><div>${r.body}</div><div class="k">令牌</div><div>${r.tags.join(" · ")}</div><div class="k">向量</div><div>${r.vector}</div></div>`).join("");
}
function renderPlainRows(q = "") {
  const rows = state.plainRows || [];
  const hl = (s) => (q ? esc(s).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(s));
  $("#plain-rows").innerHTML = rows.map((r) => `<div class="row ${q && (r.subject + r.snippet).includes(q) ? "hit" : ""}"><div class="muted">${esc(r.date.slice(0, 10))}</div><div>${esc(r.from)}</div><div><div class="s">${hl(r.subject)}</div><div class="p">${hl(r.snippet)}</div></div></div>`).join("");
}
async function spy() {
  const q = $("#spy-q").value.trim();
  if (!q) return;
  $("#spy-result").innerHTML = "检索中…";
  const r = await fetch(`/api/spy?q=${encodeURIComponent(q)}`).then((x) => x.json());
  state.plainRows = r.samples.concat((state.plainRows || []).filter((x) => !r.samples.some((s) => s.id === x.id))).slice(0, 80);
  renderPlainRows(q);
  $("#spy-result").innerHTML = `<span style="color:var(--risk)">普通云：命中 <b class="num">${intText(r.plain_hits)}</b> 封明文</span> &nbsp;·&nbsp; <span style="color:var(--v2)">HPIS：命中 <b class="num">${intText(r.hpis_hits)}</b> 条（扫描 ${bytesText(r.hpis_bytes)} 密文）</span>`;
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
  const env = data.environment || {};
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
      .map((r) => `<div class="io__row"><span>${esc(r.label)}</span><div class="io__bar"><i data-w="${(Math.log10(r.bytes + 1) / mx) * 100}" style="background:${r.color || "linear-gradient(90deg,#3ff0c8,#48b8ff)"}"></i></div><b>${bytesText(r.bytes)}${r.bytes < top ? `<em class="io__ratio">1/${intText(Math.round(top / Math.max(1, r.bytes)))}</em>` : ""}</b></div>`)
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
    <div class="tech card"><h4>${ICON("fingerprint")}匿名与最小暴露</h4><ul><li>HPIS 只见匿名标识 AID、令牌与密文</li><li>HPIC 凭匿名票据转发，仅解密本次命中的几条</li><li>数据密钥、索引密钥、向量密钥始终由终端掌控</li></ul></div>
  </div>
`;
}
function archSvg() {
  const box = (x, y, w, h, stroke = "rgba(255,255,255,.14)", fill = "rgba(255,255,255,.03)") =>
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="20" fill="${fill}" stroke="${stroke}"/>`;
  const chip = (x, y, w, text, kind = "") => {
    const st = {
      "": ["rgba(255,255,255,.04)", "rgba(255,255,255,.14)", "#eaf0fb", 400],
      new: ["rgba(63,240,200,.10)", "rgba(63,240,200,.55)", "#9ff6e2", 700],
      model: ["rgba(155,140,255,.10)", "rgba(155,140,255,.45)", "#d5ceff", 600],
      key: ["rgba(72,184,255,.08)", "rgba(72,184,255,.4)", "#bfe3ff", 600],
    }[kind];
    return `<rect x="${x}" y="${y}" width="${w}" height="34" rx="9" fill="${st[0]}" stroke="${st[1]}"/><text x="${x + w / 2}" y="${y + 22}" text-anchor="middle" fill="${st[2]}" font-weight="${st[3]}" font-size="13.5">${text}</text>`;
  };
  const cyl = (cx, y, label) =>
    `<g fill="rgba(255,255,255,.04)" stroke="rgba(255,255,255,.18)"><path d="M${cx - 38} ${y}v46a38 10 0 0 0 76 0v-46"/><ellipse cx="${cx}" cy="${y}" rx="38" ry="10"/></g><text x="${cx}" y="${y + 33}" text-anchor="middle" fill="#eaf0fb" font-size="12.5">${label}</text>`;
  const flow = (d, label, lx, ly, anchor = "middle", delay = 0) =>
    `<path d="${d}" class="arch-flow" style="animation-delay:${delay}s" marker-end="url(#ah)"/><text x="${lx}" y="${ly}" text-anchor="${anchor}" class="arch-label">${label}</text>`;
  return `<svg viewBox="0 0 1000 560" xmlns="http://www.w3.org/2000/svg" font-family="HarmonyOS Sans SC, PingFang SC, Microsoft YaHei, sans-serif">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#48b8ff"/></marker>
    <linearGradient id="hpisg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="rgba(63,240,200,.10)"/><stop offset="1" stop-color="rgba(63,240,200,.02)"/></linearGradient>
  </defs>
  <style>
    .arch-flow{fill:none;stroke:#48b8ff;stroke-width:2.4;stroke-dasharray:7 7;animation:archdash 1.2s linear infinite}
    .arch-label{fill:#cfe6ff;font-size:13px;font-weight:600}
    .arch-sub{fill:#93a0bb;font-size:12.5px}
    .arch-h{fill:#eaf0fb;font-size:19px;font-weight:700}
    @keyframes archdash{to{stroke-dashoffset:-28}}
  </style>
  <line x1="20" y1="300" x2="980" y2="300" stroke="rgba(255,255,255,.18)" stroke-dasharray="4 6"/>
  <text x="26" y="290" fill="#5d6884" font-size="13" font-weight="700">云</text><text x="26" y="324" fill="#5d6884" font-size="13" font-weight="700">端</text>

  ${box(50, 40, 200, 150)}
  <text x="70" y="74" class="arch-h">终端云认证服务</text>
  ${chip(70, 92, 160, "认证")}${chip(70, 136, 160, "匿名令牌颁发")}

  ${box(300, 40, 270, 220)}
  <text x="320" y="74" class="arch-h">HPIC 可信计算区</text>
  <text x="320" y="98" class="arch-sub">无状态 · 只在本次请求内持有密钥</text>
  ${chip(320, 114, 230, "匿名票据校验 · 转发")}
  ${chip(320, 158, 230, "解密命中的 K 条记录", "key")}
  ${chip(320, 210, 230, "业务模型 Qwen / Gemma", "model")}
  <text x="435" y="204" text-anchor="middle" class="arch-sub">⑤ 上下文 ↓</text>

  <rect x="650" y="40" width="320" height="500" rx="20" fill="url(#hpisg)" stroke="rgba(63,240,200,.35)"/>
  <text x="670" y="74" class="arch-h">HPIS 匿名私人智能存储</text>
  <text x="670" y="98" class="arch-sub">只见匿名标识 AID、HMAC 令牌与密文</text>
  ${chip(670, 116, 280, "新增 · SSE 密文索引检索", "new")}
  ${chip(670, 158, 280, "新增 · 密态向量检索（TEE / PPE）", "new")}
  ${chip(670, 206, 135, "匿名索引查询")}${chip(815, 206, 135, "匿名身份令牌")}
  ${chip(670, 248, 135, "令牌所属证明")}${chip(815, 248, 135, "可验证数据删除")}
  <text x="810" y="318" text-anchor="middle" class="arch-sub">↓ 只读取命中的部分数据密文</text>
  ${cyl(722, 346, "OBS")}${cyl(810, 346, "GaussDB")}${cyl(898, 346, "ES")}
  ${chip(670, 432, 135, "非结构化数据")}${chip(815, 432, 135, "结构化数据")}
  <text x="810" y="508" text-anchor="middle" class="arch-sub">正文与向量均为 AES-256-GCM 密文</text>

  ${box(50, 340, 550, 190)}
  <text x="70" y="374" class="arch-h">用户端侧设备</text>
  <text x="70" y="398" class="arch-sub">云上数据匿名加密存储 · 本地掌控数据授权使用</text>
  ${chip(70, 416, 160, "数据密钥 DEK", "key")}${chip(240, 416, 160, "检索密钥 SSE", "key")}${chip(410, 416, 170, "向量密钥 VEK", "key")}
  ${chip(70, 464, 250, "HPIS SDK · 生成目标索引密文", "new")}${chip(330, 464, 250, "HPIS SDK · 密封目标向量", "new")}

  ${flow("M150 340 L150 196", "① 匿名令牌请求", 160, 270, "start", 0)}
  ${flow("M420 340 L420 266", "② 推理请求 + 数据密钥 + 目标索引/向量密文", 408, 324, "end", .2)}
  ${flow("M572 130 L644 130", "③ 密封检索", 610, 120, "middle", .4)}
  ${flow("M650 182 L578 182", "④ K 条密文", 612, 202, "middle", .6)}
  ${flow("M520 266 L520 336", "⑥ 推理结果", 532, 324, "start", .8)}
</svg>`;
}

// ------------------------------------------------------------------ status / boot
async function poll() {
  try {
    const s = await fetch("/api/status").then((r) => r.json());
    const first = !state.status;
    state.status = s;
    const st = $("#status");
    const phases = { starting: "启动中", unpacking: "解压数据", building: "加密预置中", "starting-services": "启动服务", warming: "预热密态索引", ready: "服务就绪", error: "出错" };
    st.className = "status " + (s.phase === "ready" ? "ready" : s.phase === "error" ? "error" : "");
    $("span", st).textContent = phases[s.phase] || s.phase;
    if (s.mailbox) {
      $("#mailbox-meta").innerHTML = `<span>${ICON("mail")} <b>${intText(s.mailbox.emails)}</b> 封邮件</span><span>${ICON("paperclip")} <b>${intText(s.mailbox.attachments)}</b> 个附件</span><span>${ICON("clock")} ${s.mailbox.first.slice(0, 7)} ~ ${s.mailbox.last.slice(0, 7)}</span>`;
      $("[data-cover=emails]").textContent = intText(s.mailbox.emails);
    }
    if (first || (s.presets && s.presets.length !== state.presets.length)) initPresets(s);
    if (first) initModels(s);
    bootOverlay(s);
    if (s.phase !== "ready" && s.phase !== "error") setTimeout(poll, 800);
    else if (s.phase === "ready" && !state.loadedSide) { state.loadedSide = true; loadPlainTicker(); }
  } catch (e) {
    setTimeout(poll, 1500);
  }
}
function bootOverlay(s) {
  const boot = $("#boot");
  if (s.phase === "ready") { boot.classList.add("off"); return; }
  boot.classList.remove("off");
  const total = s.mailbox?.emails || 100000;
  const titles = { unpacking: "首次运行：解压邮箱与向量数据", building: "首次运行：在本机加密预置邮箱", "starting-services": "启动 HPIS / HPIC / 普通云服务", warming: "预热密态索引", error: "启动失败" };
  $("#boot-title").textContent = titles[s.phase] || "正在启动密态服务…";
  if (s.phase === "error") $("#boot-sub").textContent = s.error || "";
  const bars = [["tee", "HPIS 2.0 密态租户（AES-GCM + HMAC + 向量密文）"], ["plain", "明文对照租户"], ["upload", "普通云上传批次"]];
  $("#boot-bars").innerHTML = s.phase === "building" ? bars.map(([k, l]) => {
    const n = (s.progress || {})[k] || 0;
    return `<div class="bootbar"><label><span>${l}</span><span class="num">${intText(n)} / ${intText(total)}</span></label><div><i style="width:${Math.min(100, (n / total) * 100)}%"></i></div></div>`;
  }).join("") : "";
}
function initPresets(s) {
  state.presets = s.presets || [];
  const sens = /体检|账单|合同|学校/;
  $("#presets").innerHTML = state.presets.map((p, i) => `<button class="preset ${sens.test(p.chip) ? "sensitive" : ""}" data-i="${i}"><span class="k">${i + 1}</span>${esc(p.chip)}</button>`).join("");
  if (state.presets.length && !$("#question").value) selectPreset(0);
}
function selectPreset(i) {
  const p = state.presets[i];
  if (!p) return;
  $("#question").value = p.question;
  $$(".preset").forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === i));
}
function initModels(s) {
  const sel = $("#model");
  sel.innerHTML = (s.models || []).map((m) => `<option value="${m.id}">${esc(m.label)}</option>`).join("");
  state.model = s.defaultModel && s.models.some((m) => m.id === s.defaultModel) ? s.defaultModel : s.models?.[0]?.id;
  sel.value = state.model;
  for (const L of Object.values(state.lanes)) $("[data-model]", L.el).textContent = modelLabel();
}
async function loadPlainTicker() {
  try {
    const r = await fetch("/api/cloudview?kind=plain&limit=10&seed=3&sensitive=1").then((x) => x.json());
    const sens = state.status?.mailbox?.sensitive || {};
    const head = Object.values(sens).filter((x) => x.count).map((x) => `${x.label} ${intText(x.count)} 封`).join(" · ");
    state.plainTicker = (head ? `普通云可读取：${head}　｜　` : "") + r.rows.map((x) => `【${x.from}】${x.subject}：${x.snippet.slice(0, 40)}`).join("　·　");
    setTicker("plain");
  } catch {}
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
function initTabs() {
  const show = (tab) => {
    $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + tab));
    if (tab === "cloud" && !state.cloudLoaded) { state.cloudLoaded = true; loadCloudView(); }
    if (tab === "bench") loadBench();
  };
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  state.showTab = show;
}

function init() {
  ambient();
  initTabs();
  initNet();
  buildLanes();
  renderArch();
  $("#enter").addEventListener("click", () => $("#cover").classList.add("off"));
  $("#run").addEventListener("click", run);
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  $("#question").addEventListener("input", () => $$(".preset").forEach((b) => b.classList.remove("active")));
  $("#presets").addEventListener("click", (e) => { const b = e.target.closest(".preset"); if (b) selectPreset(Number(b.dataset.i)); });
  $("#model").addEventListener("change", (e) => { state.model = e.target.value; for (const L of Object.values(state.lanes)) $("[data-model]", L.el).textContent = modelLabel(); });
  $("#lanes").addEventListener("click", (e) => {
    const c = e.target.closest(".cite");
    const laneEl = e.target.closest(".lane");
    if (c && laneEl) { const L = lane(laneEl.dataset.lane); const ev = L.evidence[Number(c.dataset.n) - 1]; if (ev) openMail(ev.id, L.id); }
    const chip = e.target.closest(".ev");
    if (chip && laneEl) openMail(chip.dataset.id, laneEl.dataset.lane);
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  $$(".drawer__seg button").forEach((b) => b.addEventListener("click", () => {
    $$(".drawer__seg button").forEach((x) => x.classList.toggle("active", x === b));
    if (b.dataset.view === "cipher") cipherView(); else $("#drawer-body").innerHTML = $("#drawer-body").dataset.plain;
  }));
  $("#spy-go").addEventListener("click", spy);
  $("#verdict-close").addEventListener("click", () => { $("#verdict").classList.remove("on"); document.body.classList.remove("scoreboard"); });
  $("#lanes").addEventListener("dblclick", () => document.body.classList.toggle("results"));
  $("#spy-q").addEventListener("keydown", (e) => { if (e.key === "Enter") spy(); });
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.key === "Escape") { closeDrawer(); $("#cover").classList.add("off"); }
    if (/^[1-9]$/.test(e.key)) selectPreset(Number(e.key) - 1);
    if (e.key === "Enter") run();
    if (e.key === "f") document.documentElement.requestFullscreen?.();
  });
  poll();
}
init();
