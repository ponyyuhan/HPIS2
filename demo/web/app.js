// 面向个人敏感数据的智能问答 — front-end
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
    title: "端侧+HPIC+HPIS", en: "Encrypted search", icon: "shield-check",
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
      this.el("rect", { width: 72, height: 56, rx: 16, class: "ripple" }, g);
      this.el("rect", { width: 72, height: 56, rx: 16, class: "box" }, g);
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
  reset(cfg) {
    for (const k of Object.keys(this.streams)) this.stream(k, false);
    for (const p of this.packets) { p.c.remove(); p.tail.forEach((x) => x.remove()); }
    this.packets = [];
    for (const [id, , , , sub] of cfg.nodes) this.node(id, null, sub);
    for (const id of Object.keys(this.links)) { this.linkValue(id, ""); this.hot(id, false); }
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
        <div class="metric ${id === "plain" ? "bad" : id === "v2" ? "good" : ""}"><label>${ICON("signal")}上传云端</label><output data-m="edge">—</output></div>
        <div class="metric ${id === "v1" ? "v1" : id === "v2" ? "good" : ""}"><label>${ICON("server")}云云传输</label><output data-m="cloud">—</output></div>
        <div class="metric"><label>${ICON("zap")}首字响应</label><output data-m="ttft">—</output></div>
        <div class="metric"><label>${ICON("clock")}完整回答</label><output data-m="total">—</output></div>
      </div>
      <div class="sees ${seesCls}"><span class="sees__label">${seesLabel}</span><div class="ticker"><span class="ticker__track ${id === "plain" ? "bad" : "mono"}" data-ticker></span></div></div>
      <div class="answer">
        <div class="answer__head"><span>${ICON("brain")} <b data-model></b> 的回答</span><span data-evcount></span></div>
        <div class="answer__text" data-answer><div class="ph ph--idle"><i></i><b>等待提问</b><span>点击「开始对比」，两条路径会同时检索并作答</span></div></div>
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
    $("[data-answer]", L.el).innerHTML = `<div class="ph ph--idle"><i></i><b>等待提问</b><span>点击「开始对比」，两条路径会同时检索并作答</span></div>`;
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
      metric(id, "edge", ev.edge_up, "bytes");
      if (L.m.cloud == null) metric(id, "cloud", id === "plain" ? 0 : null, "bytes");
      L.final = ev;
      L.q = quality(id, question);
      const note = L.llm?.replay ? "完成 · 回放" : L.llm?.fallback ? "完成 · 摘录" : "完成";
      pill(id, note, "ok");
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
  // Upload per question, the measure the cover, the flow diagram and the cloud view all use.
  const pe = P.final.edge_up, ve = V2.final.edge_up;
  tiles.push({ big: fold(pe / ve), title: "上传云端减少", sub: `普通云 ${bytesText(pe)} → ${bytesText(ve)}` });
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
    <div class="tech card"><h4>${ICON("fingerprint")}匿名与最小暴露</h4><ul><li>HPIS 只见匿名标识 AID、令牌与密文</li><li>HPIC 凭匿名票据转发，仅解密本次命中的几条</li><li>数据密钥、索引密钥、向量密钥始终由终端掌控</li></ul></div>
  </div>
`;
}
function archSvg() {
  const box = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="20" class="arch-box"/>`;
  const chip = (x, y, w, text, kind = "") =>
    `<g class="arch-chip${kind ? " arch-chip--" + kind : ""}"><rect x="${x}" y="${y}" width="${w}" height="34" rx="9"/><text x="${x + w / 2}" y="${y + 22}" text-anchor="middle" font-size="13.5">${text}</text></g>`;
  const cyl = (cx, y, label) =>
    `<g class="arch-cyl"><path d="M${cx - 38} ${y}v46a38 10 0 0 0 76 0v-46"/><ellipse cx="${cx}" cy="${y}" rx="38" ry="10"/></g><text x="${cx}" y="${y + 33}" text-anchor="middle" class="arch-cyl-label" font-size="12.5">${label}</text>`;
  const flow = (d, label, lx, ly, anchor = "middle", delay = 0) =>
    `<path d="${d}" class="arch-flow" style="animation-delay:${delay}s" marker-end="url(#ah)"/><text x="${lx}" y="${ly}" text-anchor="${anchor}" class="arch-label">${label}</text>`;
  return `<svg viewBox="0 0 1000 560" xmlns="http://www.w3.org/2000/svg" font-family="HarmonyOS Sans SC, PingFang SC, Microsoft YaHei, sans-serif">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="arch-arrow"/></marker>
    <linearGradient id="hpisg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="arch-g0"/><stop offset="1" class="arch-g1"/></linearGradient>
  </defs>
  <line x1="20" y1="300" x2="980" y2="300" class="arch-sep"/>
  <text x="26" y="290" class="arch-side">云</text><text x="26" y="324" class="arch-side">端</text>

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

  <rect x="650" y="40" width="320" height="500" rx="20" fill="url(#hpisg)" class="arch-hpis"/>
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
      if (s.build?.upload?.bytes) $("[data-cover=upload]").textContent = bytesText(s.build.upload.bytes);
    }
    if (first || (s.presets && s.presets.length !== state.presets.length)) initPresets(s);
    if (first) initModels(s);
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
function initPresets(s) {
  state.presets = s.presets || [];
  const sens = /体检|账单|合同|学校/;
  $("#presets").innerHTML = state.presets.map((p, i) => `<button class="preset ${sens.test(p.chip) ? "sensitive" : ""}" data-i="${i}"><span class="k">${i + 1}</span>${esc(p.chip)}</button>`).join("");
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
    state.cipherTicker = "HPIS 中的真实存储：" + r.rows.map((x) => `AID ${x.aid.slice(0, 10)} · 正文 ${x.body.slice(0, 40)} · 令牌 ${(x.tags[0] || "").slice(0, 24)}`).join("　｜　");
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
    state.plainTicker = (head ? `普通云可读取：${head}　｜　` : "") + r.rows.map((x) => `【${x.from}】${x.subject}：${x.snippet.slice(0, 40)}`).join("　·　");
    setTicker("plain");
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
      void num.offsetWidth;  // restart the pop animation
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
    return;  // the share picker was cancelled
  }
  await countdown(3);  // the countdown itself is not in the video
  rec.mime = recMime();
  rec.chunks = [];
  rec.stream = stream;
  rec.recorder = new MediaRecorder(stream, { mimeType: rec.mime || undefined, videoBitsPerSecond: 12000000 });
  rec.recorder.ondataavailable = (e) => { if (e.data.size) rec.chunks.push(e.data); };
  rec.recorder.onstop = saveRecording;
  stream.getVideoTracks()[0].addEventListener("ended", stopRecording);  // 「停止共享」 in the browser bar
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
  initTabs();
  initNet();
  initRecording();
  buildLanes();
  renderArch();
  initTheme();
  $("#enter").addEventListener("click", () => $("#cover").classList.add("off"));
  $("#run").addEventListener("click", run);
  $("#question").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  $("#question").addEventListener("input", () => $$(".preset").forEach((b) => b.classList.remove("active")));
  $("#presets").addEventListener("click", (e) => { const b = e.target.closest(".preset"); if (b) selectPreset(Number(b.dataset.i)); });
  $("#model").addEventListener("change", (e) => {
    state.model = e.target.value;
    // An answer on screen keeps the name of the model that wrote it; the new one shows from the next run.
    for (const L of Object.values(state.lanes)) {
      if (!L.el.classList.contains("running") && !L.el.classList.contains("done")) $("[data-model]", L.el).textContent = modelLabel();
    }
  });
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
