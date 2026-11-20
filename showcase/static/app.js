"use strict";
const $ = (s, scope = document) => scope.querySelector(s);
const $$ = (s, scope = document) => [...scope.querySelectorAll(s)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (s) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        s
      ],
  );
function glyph(name) {
  return (
    '<svg class="icon" aria-hidden="true"><use href="/assets/icons.svg#' +
    esc(name) +
    '"></use></svg>'
  );
}
function hydrate(scope = document) {
  $$("[data-icon]", scope).forEach((el) => {
    el.outerHTML = glyph(el.dataset.icon);
  });
}
const num = (n) =>
  Number.isFinite(Number(n))
    ? new Intl.NumberFormat("en-US").format(Number(n))
    : "—";
function bytes(n) {
  if (n == null || !Number.isFinite(+n)) return ["—", ""];
  if (n < 1000) return [String(Math.round(n)), "B"];
  const power = Math.min(3, Math.floor(Math.log(n) / Math.log(1000))),
    value = n / 1000 ** power;
  return [
    value
      .toFixed(value >= 100 ? 1 : 2)
      .replace(/\.00$/, "")
      .replace(/(\.\d)0$/, "$1"),
    ["B", "KB", "MB", "GB"][power],
  ];
}
function duration(n) {
  if (n == null || !Number.isFinite(+n)) return ["—", ""];
  return n >= 1000
    ? [(n / 1000).toFixed(2), "s"]
    : [
        n < 10
          ? n.toFixed(2)
          : n < 100
            ? n.toFixed(1)
            : Math.round(n).toString(),
        "ms",
      ];
}
const fmtBytes = (n) => bytes(n).join(" ").trim(),
  fmtTime = (n) => duration(n).join(" ").trim();
const stamp = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString("zh-CN", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";
const state = {
  boot: null,
  run: null,
  presets: [],
  model: "extractive",
  answer: "protected",
  view: "workbench",
  busy: false,
  questionDirty: false,
  progress: {},
  slide: 0,
  lib: { q: "", offset: 0, limit: 10, total: 0, items: [] },
  research: null,
  librarySeq: 0,
  detailsSeq: 0,
  replay: -1,
};
const paneNames = { plain: "普通云直传", protected: "HPIS 密态检索" };
const modelName = (id) =>
  state.boot?.models?.find((m) => m.id === id)?.label ||
  { extractive: "本地证据抽取", qwen: "Qwen 27B", gemma: "Gemma 4" }[id] ||
  id;
const fallback = (r) =>
  r?.model?.status &&
  ["unavailable", "evidence-fallback", "fallback", "error"].includes(
    r.model.status,
  );
const statusLabel = (r) =>
  fallback(r)
    ? "回退至本地证据抽取"
    : r?.engine || state.run?.engine || "本地证据抽取";
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("on");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("on"), 2200);
}
async function readJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error("数据暂时无法读取");
  return res.json();
}
function setConnection(ready, error) {
  const c = $("#connection");
  c.innerHTML =
    '<i class="dot ' +
    (error ? "bad" : ready ? "good" : "busy") +
    '"></i><span>' +
    (error ? "服务离线" : ready ? "服务在线" : "正在准备") +
    "</span>";
}
function hint(msg = "") {
  const el = $("#form-hint");
  el.hidden = !msg;
  el.textContent = msg;
}
function installPresets() {
  const host = $("#presets");
  host.innerHTML = state.presets
    .map(
      (p, i) =>
        '<button type="button" class="preset" data-preset="' +
        esc(p.id) +
        '">' +
        glyph(["briefcase-business", "coins", "map-pin"][i] || "mail") +
        esc(p.label) +
        "</button>",
    )
    .join("");
  markPreset();
}
function fitQuestion() {
  const q = $("#question");
  if (!q || !q.offsetWidth) return;
  q.style.height = "auto";
  q.style.height = Math.min(q.scrollHeight, 92) + "px";
}
function markPreset() {
  fitQuestion();
  const q = $("#question").value.trim();
  $$("[data-preset]").forEach((b) => {
    const selected =
      state.presets.find((p) => p.id === b.dataset.preset)?.question === q;
    b.classList.toggle("selected", selected);
    b.setAttribute("aria-pressed", selected);
  });
}
function metric(label, pair) {
  return (
    '<div class="metric"><div class="metric-label">' +
    label +
    '</div><div class="metric-value">' +
    esc(pair[0]) +
    '<span class="metric-unit">' +
    esc(pair[1]) +
    "</span></div></div>"
  );
}
function node(label, icon) {
  return (
    '<div class="flow-node"><span>' + glyph(icon) + "</span>" + label + "</div>"
  );
}
function wire(caption) {
  return '<div class="flow-wire"><small>' + caption + "</small></div>";
}
function pathCard(kind, run = state.run, interactive = true) {
  const p = kind === "protected",
    result = run?.[kind],
    prog = state.busy && interactive ? state.progress[kind] : null,
    working = Boolean(prog && !prog.done);
  const current = prog?.done ? prog.result : !prog ? result : null;
  const ms = current?.elapsedMs ?? (working ? prog.elapsedMs : null),
    amount = current?.uploadBytes ?? (working ? prog.bytes : null),
    count = current?.readRows ?? null;
  const order = p
    ? node("终端", "smartphone") +
      wire("密文请求") +
      node("HPIC", "cpu") +
      wire("按需取证") +
      node("HPIS", "database")
    : node("终端邮箱", "laptop") + wire("邮件与附件") + node("普通云", "cloud");
  const replaying = interactive && state.replay >= 0;
  const phase = working
    ? { upload: "传输中", search: "检索中", answer: "回答中" }[prog.stage] ||
      "运行中"
    : current
      ? "已完成"
      : "待运行";
  return (
    '<article class="path-card ' +
    kind +
    (working || replaying ? " running" : "") +
    (replaying ? " replay-step-" + state.replay : "") +
    '"><div class="path-head"><span class="route-icon">' +
    glyph(p ? "shield-check" : "cloud-upload") +
    "</span><h3>" +
    paneNames[kind] +
    '</h3><span class="kind ' +
    (working ? "active" : "") +
    '">' +
    glyph(working ? "refresh-cw" : current ? "check" : "clock-3") +
    phase +
    "</span></div>" +
    '<p class="path-description">' +
    (p
      ? "终端发送查询，HPIC 解开命中的邮件。"
      : "终端分批上传邮箱，由普通云查找相关内容。") +
    "</p>" +
    '<div class="flow" aria-label="' +
    (p ? "终端到 HPIC，HPIS 密文检索" : "终端邮箱上传到普通云") +
    '">' +
    order +
    "</div>" +
    '<div class="metric-row">' +
    metric("端侧上传", bytes(amount)) +
    metric("读取正文", [count == null ? "—" : num(count), "封"]) +
    metric("端到端用时", duration(ms)) +
    "</div>" +
    '<div class="route-foot"><span>' +
    glyph(p ? "lock-keyhole" : "eye") +
    (replaying
      ? (p
          ? ["终端生成并密封查询", "HPIS 命中索引并取证", "HPIC 解开正文并回答"]
          : ["终端分批上传邮件", "普通云扫描完整邮箱", "普通云返回回答及出处"])[
          state.replay
        ]
      : p
        ? "HPIS 保存密文"
        : "普通云可见上传正文") +
    "</span>" +
    (interactive
      ? '<button class="text-button" type="button" data-trace="' +
        kind +
        '">运行明细' +
        glyph("chevron-right") +
        "</button>"
      : "") +
    "</div></article>"
  );
}
function renderPaths() {
  $("#path-grid").innerHTML = pathCard("plain") + pathCard("protected");
  const run = state.run,
    insight = $("#insight");
  insight.hidden = !run || state.busy;
  if (!insight.hidden) {
    const saved = run.plain.uploadBytes - run.protected.uploadBytes;
    insight.innerHTML =
      glyph("shield-check") +
      "<span>本次提问，端侧少传 <strong>" +
      esc(fmtBytes(saved)) +
      "</strong>，HPIC 按需读取 <strong>" +
      num(run.protected.readRows) +
      " 封正文</strong>。</span><small>一次性预置 " +
      esc(fmtBytes(state.boot?.dataset?.setupBytes)) +
      " · 按次查询统计</small>";
  }
  updateStamp();
  const replay = $("#replay");
  replay.disabled = !run || state.busy;
  replay.innerHTML =
    glyph(state.replay >= 0 ? "circle-stop" : "rotate-ccw") +
    (state.replay >= 0 ? "停止回放" : "回放流程");
}
function updateStamp() {
  const el = $("#run-stamp");
  el.textContent =
    state.replay >= 0
      ? "分步回放 · 非实际耗时"
      : state.busy
        ? "正在对比两条路径"
        : !state.run
          ? ""
          : $("#question").value.trim() !== state.run.question ||
              state.model !== (state.run.model || "extractive")
            ? "新设置待运行"
            : "最近完成 " + stamp(state.run.completedAt);
}
function stopReplay() {
  clearTimeout(stopReplay.timer);
  state.replay = -1;
}
function replayFlow() {
  if (state.busy || !state.run) return;
  if (state.replay >= 0) {
    stopReplay();
    renderPaths();
    if ($("#presenter").open) renderSlide();
    return;
  }
  function step(n) {
    state.replay = n;
    renderPaths();
    if ($("#presenter").open) renderSlide();
    if (n < 2) stopReplay.timer = setTimeout(() => step(n + 1), 1400);
    else
      stopReplay.timer = setTimeout(() => {
        stopReplay();
        renderPaths();
        if ($("#presenter").open) renderSlide();
      }, 1500);
  }
  step(0);
}
function evidenceLink(id) {
  return (
    '<button type="button" class="cite" data-evidence="' +
    Number(id) +
    '" aria-label="查看引用邮件">' +
    glyph("paperclip") +
    String(Number(id)).padStart(2, "0") +
    "</button>"
  );
}
function sourceRow(e) {
  return (
    '<button type="button" class="source-row" data-evidence="' +
    Number(e.id) +
    '"><span class="source-badge">' +
    glyph("mail") +
    '</span><span class="source-text"><span class="source-title">' +
    esc(e.subject) +
    '</span><span class="source-meta">' +
    esc(e.sender) +
    "<span>·</span>" +
    esc(e.date) +
    (e.attachments?.length || e.attachmentCount
      ? " " + glyph("paperclip")
      : "") +
    "</span></span>" +
    glyph("chevron-right") +
    "</button>"
  );
}
function renderAnswer() {
  const r = state.run?.[state.answer],
    answer = r?.answer,
    sources = r?.evidence || [];
  const root = $("#answer-panel");
  $$("[data-answer]").forEach((b) => {
    b.classList.toggle("on", b.dataset.answer === state.answer);
    b.setAttribute("aria-pressed", b.dataset.answer === state.answer);
  });
  $("#source-count").textContent = sources.length;
  $("#sources").innerHTML = sources.length
    ? sources.map(sourceRow).join("")
    : '<p class="empty">当前问题尚无引用邮件。</p>';
  if (!answer) {
    root.innerHTML =
      '<p class="empty">运行对比后可在这里核对回答与邮件出处。</p>';
    return;
  }
  const pts = answer.points || [];
  root.innerHTML =
    '<div class="panel-heading"><h3>' +
    glyph("messages-square") +
    esc(answer.headline || "本次回答") +
    '</h3><span class="answer-meta">' +
    glyph("cpu") +
    esc(statusLabel(r)) +
    "</span></div>" +
    (pts.length
      ? '<div class="facts">' +
        pts
          .map(
            (pt) =>
              '<div class="fact"><strong class="fact-label">' +
              esc(pt.label) +
              "</strong><div>" +
              esc(pt.text) +
              (pt.sourceIds || []).map(evidenceLink).join("") +
              "</div></div>",
          )
          .join("") +
        "</div>"
      : '<p class="empty">邮箱中没有足够证据回答这个问题。</p>') +
    (r?.quality
      ? '<div class="answer-check">' +
        glyph("list-checks") +
        " 证据覆盖 <strong>" +
        num(r.quality.matched) +
        " / " +
        num(r.quality.total) +
        "</strong>" +
        (state.run?.plain?.answer?.sourceIds &&
        JSON.stringify(state.run.plain.answer.sourceIds) ===
          JSON.stringify(state.run?.protected?.answer?.sourceIds)
          ? "<span>两路引用一致</span>"
          : "") +
        "</div>"
      : "") +
    (fallback(r)
      ? '<p class="tiny">' +
        esc(r.model.detail || "模型输出未通过证据核对，本轮显示原文摘录。") +
        "</p>"
      : "");
}
function renderRun() {
  renderPaths();
  renderAnswer();
  if ($("#presenter").open) renderSlide();
}
let raf = 0;
function onProgress(ev) {
  if (ev.type !== "stage") return;
  const s = state.progress[ev.route] || {};
  Object.assign(s, ev);
  if (ev.stage === "done") {
    s.done = true;
    s.result = ev.result || {
      uploadBytes: ev.bytes,
      elapsedMs: ev.elapsedMs,
      readRows: ev.readRows,
    };
  }
  state.progress[ev.route] = s;
  if (!raf)
    raf = requestAnimationFrame(() => {
      raf = 0;
      renderPaths();
      if ($("#presenter").open && state.slide === 1) renderSlide();
    });
}
async function runQuestion() {
  const question = $("#question").value.trim();
  if (!question) {
    hint("请输入问题，或选择一个示例场景。");
    $("#question").focus();
    return;
  }
  if (state.busy || !state.boot?.ready) return;
  stopReplay();
  hint();
  state.busy = true;
  state.progress = {
    plain: { stage: "upload" },
    protected: { stage: "upload" },
  };
  setButton();
  renderPaths();
  let complete = false;
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, model: state.model }),
    });
    if (!res.ok) {
      let message = "服务暂时无法完成比较";
      try {
        message = (await res.json()).error || message;
      } catch {}
      throw new Error(message);
    }
    const reader = res.body.getReader(),
      decode = new TextDecoder();
    let buffer = "";
    function consume(line) {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === "error") throw new Error(event.message);
      if (event.type === "complete") {
        state.run = event.run;
        complete = true;
      } else onProgress(event);
    }
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decode.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) {
        consume(buffer.slice(0, end));
        buffer = buffer.slice(end + 1);
      }
    }
    buffer += decode.decode();
    if (buffer.trim()) consume(buffer);
    if (!complete) throw new Error("比较尚未完整返回，请重试。");
    state.answer = "protected";
    state.questionDirty = false;
  } catch (err) {
    hint(err.message || "运行失败，请重试。");
  } finally {
    state.busy = false;
    state.progress = {};
    setButton();
    renderRun();
  }
}
function setButton() {
  const btn = $("#run-button");
  btn.disabled = state.busy || !state.boot?.ready;
  btn.classList.toggle("running", state.busy);
  btn.innerHTML =
    glyph(state.busy ? "refresh-cw" : "play") +
    "<span>" +
    (state.busy ? "对比中" : "运行对比") +
    "</span>";
}
function showView(view, fromHash = false) {
  if (!["workbench", "library", "research"].includes(view)) view = "workbench";
  state.view = view;
  $$(".view").forEach((n) => (n.hidden = n.id !== view));
  $$("[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (view !== "workbench" && state.replay >= 0) {
    stopReplay();
    renderPaths();
  }
  if (!fromHash) history.replaceState(null, "", "#" + view);
  if (view === "library") loadLibrary();
  if (view === "research") loadResearch();
  window.scrollTo({ top: 0, behavior: "instant" });
}
function openSheet(label, icon, html) {
  $("#sheet-label").innerHTML = glyph(icon) + esc(label);
  $("#sheet-content").innerHTML = html;
  const dlg = $("#details");
  if (!dlg.open) dlg.showModal();
  $("#sheet-content").scrollTop = 0;
}
function fields(pairs) {
  return (
    '<dl class="field-list">' +
    pairs
      .map(([k, v]) => "<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>")
      .join("") +
    "</dl>"
  );
}
function modelOptions() {
  const models = state.boot?.models || [
    { id: "extractive", label: "本地证据抽取", available: true },
  ];
  return models
    .map(
      (m) =>
        '<option value="' +
        esc(m.id) +
        '" ' +
        (m.id === state.model ? "selected " : "") +
        ((m.available ?? m.configured) ? "" : "disabled") +
        ">" +
        esc(m.label) +
        ((m.available ?? m.configured) ? "" : " · 未配置") +
        "</option>",
    )
    .join("");
}
function chooseModel(id) {
  state.model = id;
  $("#inline-engine").innerHTML = modelOptions();
  if ($("#engine-select")) $("#engine-select").innerHTML = modelOptions();
  updateStamp();
  toast("下次运行使用" + modelName(id));
}
function showMethods() {
  const d = state.boot?.dataset,
    opts = state.boot?.models || [
      { id: "extractive", label: "本地证据抽取", available: true },
    ];
  openSheet(
    "对比口径",
    "sliders-horizontal",
    '<h2>同一邮箱，同一问题</h2><p>两条路径各自执行检索和回答，使用相同的邮件、查询条件与回答引擎。</p><h3>回答引擎</h3><select class="select-field" id="engine-select" aria-label="选择本轮回答引擎">' +
      opts
        .map(
          (m) =>
            '<option value="' +
            esc(m.id) +
            '" ' +
            (m.id === state.model ? "selected " : "") +
            ((m.available ?? m.configured) ? "" : "disabled") +
            ">" +
            esc(m.label) +
            ((m.available ?? m.configured) ? "" : " · 未配置") +
            "</option>",
        )
        .join("") +
      '</select><p class="hint">默认从命中原文摘录。H200 服务配置完成后可选择 Qwen 27B 或 Gemma 4。</p><h3>数据与预置</h3>' +
      fields([
        ["邮件", num(d?.total) + " 封合成邮件"],
        ["附件", num(d?.attachments) + " 个"],
        ["加密预置上传", fmtBytes(d?.setupBytes)],
        ["一次性预置用时", fmtTime(d?.setupMs)],
      ]) +
      '<h3>本次计入的工作</h3><p>普通云从终端分批接收完整邮箱。HPIS 复用已建的密文索引，每次由终端提交查询，再把命中的密文正文交给 HPIC。</p><div class="sheet-block soft"><p>传输量统计序列化应用消息体，1 MB 等于 1,000,000 B。两条链路在本机回环上实际运行，计入本轮回答生成。密文索引预置成本单独列出。</p></div>' +
      (state.run
        ? '<div class="sheet-block estimate"><div><h3>累计端侧上传</h3><output id="count-label">2 次提问</output></div><input id="count-range" type="range" min="1" max="10" value="2" aria-label="累计提问次数"><div id="count-cost"></div><p class="hint">按本轮实际包体大小推算。密态路径含一次性预置，按邮箱内容不变计算。</p></div>'
        : "") +
      '<h3>离线展示</h3><p><a href="/media/hpis-briefing.mp4" target="_blank" rel="noopener">播放现场实录 ' +
      glyph("circle-play") +
      '</a>　<span class="hint">约 41 秒，本地证据抽取模式</span></p>' +
      '<h3>检索性能</h3><p>同库明文索引与密文索引之间的 10% 目标，在独立基准中比较。</p><button class="button outline" data-go-research>查看检索实测</button>',
  );
  renderEstimate(2);
}
function renderEstimate(n) {
  const out = $("#count-cost");
  if (!out || !state.run) return;
  $("#count-label").textContent = n + " 次提问";
  const totalHPIS =
    state.boot.dataset.setupBytes + state.run.protected.uploadBytes * n;
  const totalCloud = state.run.plain.uploadBytes * n;
  out.innerHTML = fields([
    ["普通云直传", fmtBytes(totalCloud)],
    ["HPIS 含预置", fmtBytes(totalHPIS)],
  ]);
}
function trace(kind) {
  const r = state.run?.[kind];
  if (!r) {
    toast("完成一次查询后即可查看明细");
    return;
  }
  const p = kind === "protected",
    d = state.boot?.dataset;
  let sample = r.queryCipherSample;
  openSheet(
    "运行明细",
    "activity",
    "<h2>" +
      paneNames[kind] +
      "</h2><p>" +
      esc(state.run.question) +
      '</p><h3>本轮执行</h3><ol class="timeline">' +
      (r.stages || [])
        .map(
          (s) =>
            "<li>" +
            glyph("circle-check") +
            esc(s.label) +
            "<b>" +
            fmtTime(s.ms) +
            "</b></li>",
        )
        .join("") +
      "</ol>" +
      fields([
        ["端到端用时", fmtTime(r.elapsedMs)],
        ["端侧上传", fmtBytes(r.uploadBytes)],
        ["端侧接收", fmtBytes(r.downloadBytes)],
        ["上传批次", num(r.batches) + " 批"],
        ["云间应用载荷", fmtBytes(r.cloudWireBytes)],
        ["正文读取", num(r.readRows) + " / " + num(d?.total) + " 封"],
        [
          "证据覆盖",
          r.quality
            ? num(r.quality.matched) + " / " + num(r.quality.total)
            : "自由提问，无预置金标准",
        ],
        ["实际回答方式", statusLabel(r)],
      ]) +
      (p && sample
        ? '<h3>本轮查询密文片段</h3><div class="cipher-sample">' +
          esc(sample) +
          '</div><p class="hint">显示密封查询的前 16 字节。</p>'
        : "") +
      (r.modelWireBytes
        ? '<p class="hint">另有 ' +
          fmtBytes(r.modelWireBytes) +
          " 的模型端点应用载荷。</p>"
        : "") +
      (p && r.bodyCipherSample
        ? '<h3>取回的正文密文片段</h3><div class="cipher-sample">' +
          esc(r.bodyCipherSample) +
          "</div>"
        : "") +
      (!p && r.evidence?.length
        ? '<h3>普通云收到的正文示例</h3><div class="citation-note">' +
          esc(r.evidence[0].subject) +
          "<br>" +
          esc(r.evidence[0].body.slice(0, 84)) +
          "…</div>"
        : "") +
      '<button class="button outline" data-export-run>' +
      glyph("download") +
      "导出本轮记录</button>" +
      '<div class="sheet-block soft"><p>' +
      (p
        ? "HPIS 能观察同一索引的访问与命中数量。HPIC 获授权后可见本轮命中的正文。"
        : "普通云在本次查询中收到所有上传的模拟邮件与附件内容。") +
      "</p></div>",
  );
}
function attachmentPreview(a) {
  const value = a.text ?? a.content ?? "";
  if (a.name.endsWith(".csv")) {
    const rows = [];
    let line = [],
      cell = "",
      quoted = false;
    for (let i = 0; i < value.length; i++) {
      const char = value[i];
      if (char === '"') {
        if (quoted && value[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = !quoted;
      } else if (char === "," && !quoted) {
        line.push(cell);
        cell = "";
      } else if (char === "\n" && !quoted) {
        line.push(cell.replace(/\r$/, ""));
        rows.push(line);
        line = [];
        cell = "";
      } else cell += char;
    }
    if (cell || line.length) {
      line.push(cell);
      rows.push(line);
    }
    return (
      '<div class="file-table-wrap"><table class="file-table">' +
      rows
        .map(
          (row, index) =>
            "<tr>" +
            row
              .map((c) =>
                index === 0
                  ? "<th>" + esc(c) + "</th>"
                  : "<td>" + esc(c) + "</td>",
              )
              .join("") +
            "</tr>",
        )
        .join("") +
      "</table></div>"
    );
  }
  if (a.name.endsWith(".md"))
    return (
      '<div class="file-markdown">' +
      value
        .split(/\r?\n/)
        .filter((x) => x.trim())
        .map((x) =>
          x.startsWith("## ")
            ? "<h4>" + esc(x.slice(3)) + "</h4>"
            : x.startsWith("# ")
              ? "<h3>" + esc(x.slice(2)) + "</h3>"
              : "<p>" + esc(x) + "</p>",
        )
        .join("") +
      "</div>"
    );
  return "<pre>" + esc(value) + "</pre>";
}
async function showEvidence(id) {
  const seq = ++state.detailsSeq;
  const embedded = [
    ...(state.run?.protected?.evidence || []),
    ...(state.run?.plain?.evidence || []),
  ].find((e) => e.id === id);
  if (!embedded)
    openSheet("邮件证据", "mail-open", '<p class="empty">正在读取原邮件…</p>');
  try {
    const e = embedded || (await readJSON("/api/evidence/" + id));
    if (seq !== state.detailsSeq) return;
    const cites = (state.run?.[state.answer]?.answer?.points || [])
      .flatMap((p) => p.quotes || [])
      .filter((q) => q.id === id);
    openSheet(
      "邮件证据",
      "mail-open",
      "<h2>" +
        esc(e.subject) +
        '</h2><div class="email-head"><div class="avatar">' +
        esc((e.sender || "?").slice(-1)) +
        "</div><div><strong>" +
        esc(e.sender) +
        " · " +
        esc(e.role || "项目组") +
        "</strong><small>" +
        esc(e.email || "") +
        "　" +
        esc(e.date) +
        " " +
        esc(e.time || "") +
        "</small></div></div>" +
        (cites.length
          ? '<div class="citation-note"><strong>本轮引用</strong><br>' +
            cites
              .map(
                (q) =>
                  esc(q.text) +
                  (q.attachment ? "　〈" + esc(q.attachment) + "〉" : ""),
              )
              .join("<br>") +
            "</div>"
          : "") +
        '<div class="mail-body">' +
        esc(e.body) +
        "</div>" +
        ((e.attachments || []).length
          ? "<h3>附件</h3>" +
            e.attachments
              .map(
                (a, i) =>
                  '<details class="attachment" ' +
                  (cites.some((q) => q.attachment === a.name) ? "open" : "") +
                  "><summary>" +
                  glyph("paperclip") +
                  esc(a.name) +
                  glyph("chevron-down") +
                  "</summary>" +
                  attachmentPreview(a) +
                  '<a download href="/api/evidence/' +
                  id +
                  "/attachment/" +
                  i +
                  '">' +
                  glyph("download") +
                  "下载附件</a></details>",
              )
              .join("")
          : "") +
        '<p class="hint" style="margin-top:18px">演示邮箱 · 合成邮件 #' +
        id +
        "</p>",
    );
  } catch {
    openSheet(
      "邮件证据",
      "mail-open",
      '<p class="empty">读取邮件失败，请关闭后重试。</p>',
    );
  }
}
async function loadLibrary() {
  const seq = ++state.librarySeq,
    l = state.lib;
  $("#library-count").textContent = "查询中…";
  try {
    const d = await readJSON(
      "/api/library?" +
        new URLSearchParams({ q: l.q, offset: l.offset, limit: l.limit }),
    );
    if (seq !== state.librarySeq) return;
    Object.assign(l, d);
    $("#library-count").textContent = num(l.total) + " 封邮件";
    $("#library-list").innerHTML = l.items.length
      ? '<div class="lib-table-head"><span></span><span>邮件主题</span><span>发件人</span><span class="date-column">日期</span><span></span></div>' +
        l.items
          .map(
            (e) =>
              '<button class="lib-row" type="button" data-evidence="' +
              e.id +
              '"><span class="source-badge">' +
              glyph("mail") +
              "</span><strong>" +
              esc(e.subject) +
              '</strong><span class="lib-meta">' +
              esc(e.sender) +
              '</span><span class="lib-meta date-column">' +
              esc(e.date) +
              "</span>" +
              glyph("chevron-right") +
              "</button>",
          )
          .join("")
      : '<div class="empty">没有找到匹配的邮件。</div>';
    $("#library-page").textContent = l.total
      ? "第 " +
        num(l.offset + 1) +
        "–" +
        num(Math.min(l.offset + l.limit, l.total)) +
        " 封"
      : "0 封";
    $("#library-prev").disabled = l.offset <= 0;
    $("#library-next").disabled = l.offset + l.limit >= l.total;
  } catch {
    $("#library-count").textContent = "读取失败";
    $("#library-list").innerHTML = '<p class="empty">邮件证据库暂不可用。</p>';
  }
}
async function loadResearch() {
  const el = $("#research-body");
  try {
    const d = state.research || (await readJSON("/data/research.json"));
    state.research = d;
    el.innerHTML =
      '<div class="research-summary">' +
      d.highlights
        .map(
          (h, i) =>
            '<article class="research-stat ' +
            (i === 0 ? "featured" : "") +
            '"><h3>' +
            esc(h.label) +
            '</h3><div class="big">' +
            esc(h.value) +
            "</div><p>" +
            esc(h.note) +
            "</p></article>",
        )
        .join("") +
      "</div>" +
      d.sections
        .map(
          (s) =>
            '<article class="panel research-panel"><h2>' +
            esc(s.title) +
            '</h2><p class="research-detail">' +
            esc(s.summary) +
            "</p>" +
            (s.rows
              ? '<table class="data-table"><thead><tr>' +
                s.columns.map((v) => "<th>" + esc(v) + "</th>").join("") +
                "</tr></thead><tbody>" +
                s.rows
                  .map(
                    (row) =>
                      "<tr>" +
                      row.map((v) => "<td>" + esc(v) + "</td>").join("") +
                      "</tr>",
                  )
                  .join("") +
                "</tbody></table>"
              : "") +
            (s.note
              ? '<p class="research-detail">' + esc(s.note) + "</p>"
              : "") +
            (s.source
              ? '<p class="research-detail"><a href="' +
                esc(s.source) +
                '" target="_blank" rel="noopener">查看测量记录 ' +
                glyph("external-link") +
                "</a></p>"
              : "") +
            "</article>",
        )
        .join("");
  } catch {
    el.innerHTML = '<p class="empty">实测记录暂时无法加载。</p>';
  }
}
function spotlight(kind) {
  const protectedPath = kind === "protected",
    r = state.run?.[kind],
    progress = state.busy ? state.progress[kind] : null;
  const amount = bytes(progress ? progress.bytes : r?.uploadBytes);
  const bodyCount = progress ? progress.readRows : r?.readRows;
  const elapsed = duration(progress ? progress.elapsedMs : r?.elapsedMs);
  const steps = protectedPath
    ? ["加密提交查询", "HPIS 密态检索", "HPIC 按需解密"]
    : ["终端分批上传", "云端扫描邮箱", "读取原文回答"];
  const selected =
    state.replay >= 0
      ? state.replay
      : progress
        ? ({ upload: 0, search: 1, answer: 2, done: 2 }[progress.stage] ?? 0)
        : -1;
  return (
    '<article class="spot-card ' +
    kind +
    '"><div class="spot-head"><span class="spot-icon">' +
    glyph(protectedPath ? "shield-check" : "cloud-upload") +
    "</span><h3>" +
    paneNames[kind] +
    "</h3></div>" +
    '<div class="spot-main"><span>端侧上传</span><div class="spot-number">' +
    esc(amount[0]) +
    "<small>" +
    esc(amount[1]) +
    "</small></div></div>" +
    '<div class="spot-track">' +
    steps
      .map(
        (x, i) =>
          '<span class="' +
          (i === selected ? "selected" : "") +
          '">' +
          glyph(
            (protectedPath
              ? ["smartphone", "database", "cpu"]
              : ["laptop", "cloud-upload", "mail-open"])[i],
          ) +
          "<b>" +
          x +
          "</b></span>",
      )
      .join("") +
    "</div>" +
    '<div class="spot-details"><div><span>读取正文</span><strong>' +
    num(bodyCount) +
    "<small>封</small></strong></div><div><span>端到端用时</span><strong>" +
    esc(elapsed[0]) +
    "<small>" +
    esc(elapsed[1]) +
    "</small></strong></div></div></article>"
  );
}
function renderSlide() {
  const idx = state.slide,
    r = state.run,
    el = $("#slide-content");
  if (idx === 0) {
    const question = $("#question").value || r?.question || "准备演示问题";
    el.innerHTML =
      '<div class="slide-cover"><div><div class="hero-eyebrow"><span class="beacon"></span>合成邮箱 · 现场讲解</div><h2>个人邮件密态问答</h2><p>' +
      esc(question) +
      '</p><div class="hero-meta"><span>' +
      glyph("inbox") +
      num(state.boot?.total) +
      " 封邮件</span><b></b><span>" +
      glyph("paperclip") +
      num(state.boot?.dataset?.attachments) +
      ' 个附件</span></div><div class="present-choices">' +
      state.presets
        .map(
          (p) =>
            '<button type="button" data-project-preset="' +
            esc(p.id) +
            '" class="' +
            (p.question === question ? "on" : "") +
            '">' +
            esc(p.label) +
            "</button>",
        )
        .join("") +
      '</div><button type="button" class="button present-run" data-project-run ' +
      (state.busy ? "disabled" : "") +
      ">" +
      glyph("play") +
      "现场运行</button></div></div>";
  } else if (idx === 1) {
    el.innerHTML =
      '<h2>本轮端侧上传量</h2><p class="slide-sub">' +
      esc(
        state.busy ? $("#question").value : r?.question || "请先运行一轮对比",
      ) +
      '</p><div class="slide-card-grid">' +
      spotlight("plain") +
      spotlight("protected") +
      '</div><div class="spot-note"><span>' +
      glyph("info") +
      (state.replay >= 0
        ? "正在分步回放。数字为本轮实测值。"
        : "统计本轮应用载荷，加密邮箱已提前预置。") +
      '</span><button class="button exit-present" data-project-replay ' +
      (state.busy ? "disabled" : "") +
      ">" +
      glyph(state.replay < 0 ? "rotate-ccw" : "circle-stop") +
      (state.replay < 0 ? "回放流程" : "停止回放") +
      "</button></div>";
  } else if (state.presentationEvidence) {
    const e = r?.protected?.evidence?.find(
      (x) => x.id === state.presentationEvidence,
    );
    if (!e) {
      state.presentationEvidence = 0;
      renderSlide();
      return;
    }
    el.innerHTML =
      '<div class="project-source"><button class="button exit-present" data-project-back>' +
      glyph("chevron-left") +
      "返回回答</button><h2>" +
      esc(e.subject) +
      '</h2><p class="slide-sub">' +
      esc(e.sender) +
      " · " +
      esc(e.role) +
      "　" +
      esc(e.date) +
      " " +
      esc(e.time) +
      '</p><div class="project-mail-body">' +
      esc(e.body) +
      "</div>" +
      (e.attachments || [])
        .map(
          (a) =>
            '<div class="project-file"><h3>' +
            glyph("paperclip") +
            esc(a.name) +
            "</h3>" +
            attachmentPreview(a) +
            "</div>",
        )
        .join("") +
      "</div>";
  } else {
    const rr = r?.protected,
      pts = rr?.answer?.points || [];
    el.innerHTML =
      "<h2>" +
      esc(rr?.answer?.headline || "回答与证据") +
      '</h2><p class="slide-sub">' +
      esc(statusLabel(rr)) +
      " · " +
      (rr?.evidence?.length || 0) +
      ' 封引用邮件</p><div class="slide-answer">' +
      (pts.length
        ? pts
            .map(
              (p) =>
                '<article class="slide-fact"><h3>' +
                esc(p.label) +
                "</h3><p>" +
                esc(p.text) +
                '</p><div class="project-cites">' +
                (p.sourceIds || [])
                  .map((i) => rr.evidence.find((e) => e.id === i))
                  .filter(Boolean)
                  .map(
                    (e) =>
                      '<button type="button" data-project-evidence="' +
                      e.id +
                      '">' +
                      glyph("mail") +
                      esc(e.sender) +
                      " · " +
                      esc(e.subject) +
                      glyph("chevron-right") +
                      "</button>",
                  )
                  .join("") +
                "</div></article>",
            )
            .join("")
        : "<p>此问题尚无可引用的证据。</p>") +
      "</div>";
  }
  $$("[data-slide]").forEach((b) =>
    b.classList.toggle("on", Number(b.dataset.slide) === idx),
  );
  $("#slide-num").textContent = idx + 1 + " / 3";
  $("#slide-prev").disabled = idx === 0;
  $("#slide-next").disabled = idx === 2;
}
async function enterPresent() {
  if (!state.run) {
    toast("演示数据准备就绪后即可投屏");
    return;
  }
  state.slide = 0;
  state.presentationEvidence = 0;
  renderSlide();
  const d = $("#presenter");
  d.showModal();
  try {
    await d.requestFullscreen?.();
    d.dataset.wasFull = document.fullscreenElement === d ? "yes" : "no";
  } catch {}
}
async function closePresent() {
  if (document.fullscreenElement)
    try {
      await document.exitFullscreen();
    } catch {}
  if ($("#presenter").open) $("#presenter").close();
}
function moveSlide(n) {
  state.presentationEvidence = 0;
  state.slide = Math.max(0, Math.min(2, n));
  renderSlide();
}
async function boot() {
  try {
    const d = await readJSON("/api/bootstrap");
    state.boot = d;
    state.presets = d.presets || [];
    setConnection(d.ready, d.error);
    if (!state.questionDirty && !$("#question").value)
      $("#question").value =
        d.last?.question || state.presets[0]?.question || "";
    $$("[data-total]").forEach((n) => (n.textContent = num(d.total)));
    installPresets();
    $("#inline-engine").innerHTML = modelOptions();
    setButton();
    if (d.last && !state.run) {
      state.run = d.last;
      renderRun();
    }
    if (d.error) hint("后台准备失败，请检查本地服务。");
    else if (!d.ready) {
      hint(
        "加密索引准备中，已写入 " +
          num(d.indexed) +
          " / " +
          num(d.total) +
          " 封邮件。",
      );
      setTimeout(boot, 900);
    } else if (!state.questionDirty) hint();
  } catch {
    setConnection(false, true);
    hint("正在连接本地演示服务…");
    setTimeout(boot, 2000);
  }
}
hydrate();
renderRun();
showView(location.hash.slice(1) || "workbench", true);
boot();
$("#query-form").addEventListener("submit", (e) => {
  e.preventDefault();
  runQuestion();
});
window.addEventListener("resize", fitQuestion);
$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    runQuestion();
  }
});
$("#question").addEventListener("input", () => {
  state.questionDirty = true;
  markPreset();
  hint();
  updateStamp();
});
$("#inline-engine").addEventListener("change", (e) =>
  chooseModel(e.target.value),
);
$("#replay").addEventListener("click", replayFlow);
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-export-run]") && state.run) {
    const body = JSON.stringify(
      {
        dataset: state.boot.dataset,
        scope: "合成邮箱 · 序列化应用消息体 · 本地双服务",
        run: state.run,
      },
      null,
      2,
    );
    const url = URL.createObjectURL(
      new Blob([body], { type: "application/json;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "HPIS-对比-" + state.run.id + ".json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return;
  }
  const pPreset = e.target.closest("[data-project-preset]");
  if (pPreset) {
    const p = state.presets.find((x) => x.id === pPreset.dataset.projectPreset);
    if (p) {
      $("#question").value = p.question;
      state.questionDirty = true;
      markPreset();
      updateStamp();
      renderSlide();
    }
    return;
  }
  if (e.target.closest("[data-project-run]")) {
    moveSlide(1);
    runQuestion();
    renderSlide();
    return;
  }
  if (e.target.closest("[data-project-replay]")) {
    replayFlow();
    return;
  }
  const pe = e.target.closest("[data-project-evidence]");
  if (pe) {
    state.presentationEvidence = Number(pe.dataset.projectEvidence);
    renderSlide();
    return;
  }
  if (e.target.closest("[data-project-back]")) {
    state.presentationEvidence = 0;
    renderSlide();
    return;
  }
  const preset = e.target.closest("[data-preset]");
  if (preset) {
    const p = state.presets.find((p) => p.id === preset.dataset.preset);
    if (p) {
      $("#question").value = p.question;
      state.questionDirty = true;
      markPreset();
      hint();
      updateStamp();
    }
    return;
  }
  const view = e.target.closest("[data-view]");
  if (view) {
    showView(view.dataset.view);
    return;
  }
  const a = e.target.closest("[data-answer]");
  if (a) {
    state.answer = a.dataset.answer;
    renderAnswer();
    return;
  }
  const evidence = e.target.closest("[data-evidence]");
  if (evidence) {
    showEvidence(Number(evidence.dataset.evidence));
    return;
  }
  const tr = e.target.closest("[data-trace]");
  if (tr) {
    trace(tr.dataset.trace);
    return;
  }
  const sl = e.target.closest("[data-slide]");
  if (sl) {
    moveSlide(Number(sl.dataset.slide));
    return;
  }
  if (e.target.closest("[data-go-research]")) {
    $("#details").close();
    showView("research");
  }
});
window.addEventListener("hashchange", () =>
  showView(location.hash.slice(1), true),
);
$("#methods").addEventListener("click", showMethods);
$("#close-details").addEventListener("click", () => $("#details").close());
$("#details").addEventListener("close", () => {
  state.detailsSeq++;
});
$("#details").addEventListener("click", (e) => {
  if (e.target === $("#details")) $("#details").close();
});
$("#details").addEventListener("input", (e) => {
  if (e.target.id === "count-range") renderEstimate(Number(e.target.value));
});
$("#details").addEventListener("change", (e) => {
  if (e.target.id === "engine-select") {
    chooseModel(e.target.value);
  }
});
$("#library-query").addEventListener("input", (e) => {
  state.lib.q = e.target.value.trim();
  state.lib.offset = 0;
  clearTimeout(loadLibrary.timer);
  loadLibrary.timer = setTimeout(loadLibrary, 230);
});
$("#library-prev").addEventListener("click", () => {
  state.lib.offset = Math.max(0, state.lib.offset - state.lib.limit);
  loadLibrary();
});
$("#library-next").addEventListener("click", () => {
  state.lib.offset += state.lib.limit;
  loadLibrary();
});
$("#project").addEventListener("click", enterPresent);
$("#exit-present").addEventListener("click", closePresent);
$("#slide-prev").addEventListener("click", () => moveSlide(state.slide - 1));
$("#slide-next").addEventListener("click", () => moveSlide(state.slide + 1));
$("#presenter").addEventListener("cancel", (e) => {
  e.preventDefault();
  closePresent();
});
document.addEventListener("keydown", (e) => {
  if ($("#presenter").open) {
    if (e.key === "ArrowRight" || e.key === " ") {
      e.preventDefault();
      moveSlide(state.slide + 1);
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      moveSlide(state.slide - 1);
    }
    if (e.key === "Escape") {
      e.preventDefault();
      closePresent();
    }
  }
});

document.addEventListener("fullscreenchange", () => {
  const d = $("#presenter");
  if (!document.fullscreenElement && d.open && d.dataset.wasFull === "yes") {
    d.dataset.wasFull = "no";
    d.close();
  }
});
