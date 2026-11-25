// Dev-only fixture: ?mock=1 on the page URL (?mock=edge adds a lane notice and a model timeout).
//
// Serves sample data for what the backend is adding (PLAN-v3 §3: devices, photo / sport / car runs,
// records, photos, the multi-tenant HPIS view), so the page can be checked before the backend has it.
// app.js imports this file only when the URL carries ?mock=; without it nothing here runs.
// Questions about mail still go to the real server.

const realFetch = window.fetch.bind(window);
const enc = new TextEncoder();
let MODE = "1";

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const hex = (n, seed) => { const r = rng(seed); let s = ""; for (let i = 0; i < n; i++) s += "0123456789abcdef"[(r() * 16) | 0]; return s; };
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

// ------------------------------------------------------------------ sample photos (drawn, not real)
const SKIES = [["#7fb6e8", "#f3d9b1"], ["#1f3b73", "#e88c5a"], ["#9cc9e6", "#e9f1f6"], ["#3b6aa0", "#a9cde8"], ["#f0a35e", "#fbe3b8"], ["#284a7a", "#7fa7cf"]];
function photoSvg(id, w, h) {
  const r = rng(id * 7919);
  const [a, b] = SKIES[id % SKIES.length];
  const hill = (y0, amp, col) => {
    let d = `M0 ${h}`;
    for (let x = 0; x <= w; x += w / 8) d += ` L${x.toFixed(0)} ${(y0 - r() * amp).toFixed(0)}`;
    return `<path d="${d} L${w} ${h} Z" fill="${col}"/>`;
  };
  let town = "";
  if (id % 2 === 0) for (let x = 0; x < w; x += w / 14) { const bh = h * (0.12 + r() * 0.22); town += `<rect x="${x.toFixed(0)}" y="${(h * 0.78 - bh).toFixed(0)}" width="${(w / 16).toFixed(0)}" height="${bh.toFixed(0)}" fill="#2b3446" opacity=".82"/>`; }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs><rect width="${w}" height="${h}" fill="url(#s)"/><circle cx="${(w * (0.2 + r() * 0.6)).toFixed(0)}" cy="${(h * 0.28).toFixed(0)}" r="${(h * 0.08).toFixed(0)}" fill="#fff6d8" opacity=".85"/>${hill(h * 0.62, h * 0.16, "#5d7a8c")}${town}${hill(h * 0.8, h * 0.1, "#34495a")}</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
const photoUrl = (id, size) => (size === "full" ? photoSvg(Number(id), 960, 640) : photoSvg(Number(id), 360, 240));

// ------------------------------------------------------------------ sample records
const PHOTOS = {
  germany: [
    [101, "慕尼黑 · 玛利亚广场", "2025-09-16T08:12:00+02:00", "玛利亚广场", "慕尼黑", "德国", 48.1374, 11.5755, "清晨的玛利亚广场，新市政厅的钟楼在晨光里，广场上行人还很少。"],
    [104, "慕尼黑 · 宁芬堡宫", "2025-09-16T17:40:00+02:00", "宁芬堡宫", "慕尼黑", "德国", 48.1583, 11.5033, "宁芬堡宫前的长运河，两侧是修剪整齐的草坪。"],
    [112, "法兰克福 · 罗马广场", "2025-09-18T12:05:00+02:00", "罗马广场", "法兰克福", "德国", 50.1106, 8.6821, "罗马广场的木桁架老房子，正午阳光很强。"],
    [118, "法兰克福 · 美因河畔", "2025-09-18T19:22:00+02:00", "美因河畔", "法兰克福", "德国", 50.1072, 8.6837, "傍晚的美因河，远处是金融区的高楼。"],
    [126, "柏林 · 勃兰登堡门", "2025-09-20T09:30:00+02:00", "勃兰登堡门", "柏林", "德国", 52.5163, 13.3777, "勃兰登堡门正面，门前有不少游客。"],
    [131, "柏林 · 博物馆岛", "2025-09-20T15:10:00+02:00", "博物馆岛", "柏林", "德国", 52.5169, 13.4019, "施普雷河边的博物馆岛，柏林大教堂的穹顶。"],
  ],
  korea: [
    [205, "首尔 · 景福宫", "2024-04-06T10:20:00+09:00", "景福宫", "首尔", "韩国", 37.5796, 126.977, "景福宫勤政殿前的广场，春天的樱花开了。"],
    [209, "首尔 · 北村韩屋村", "2024-04-06T15:45:00+09:00", "北村韩屋村", "首尔", "韩国", 37.5826, 126.9831, "北村的韩屋屋檐，坡道上能看到远处的首尔塔。"],
    [214, "釜山 · 海云台", "2024-04-08T07:05:00+09:00", "海云台海水浴场", "釜山", "韩国", 35.1587, 129.1604, "清晨的海云台沙滩，海面很平静。"],
    [217, "釜山 · 甘川文化村", "2024-04-08T14:30:00+09:00", "甘川文化村", "釜山", "韩国", 35.0975, 129.0106, "甘川文化村彩色的房子沿山坡层层排开。"],
    [223, "济州 · 城山日出峰", "2024-04-10T06:10:00+09:00", "城山日出峰", "济州", "韩国", 33.4581, 126.9425, "城山日出峰的火山口边缘，日出前的天空。"],
    [228, "济州 · 涉地可支", "2024-04-10T16:00:00+09:00", "涉地可支", "济州", "韩国", 33.4241, 126.9307, "海边的灯塔和油菜花田。"],
  ],
};
const photoItem = ([id, title, date, name, city, country, lat, lon, caption]) => ({
  id, kind: "photo", title, date, summary: caption, place: { name, city, country, lat, lon }, thumb: photoUrl(id, "thumb"), caption,
  device: "phone", width: 960, height: 640, bytes: 380000 + id * 97, tags: ["街景", "建筑", city], fields: { country, city, type: "街景" },
  source: { dataset: "示例数据", id: `mock-${id}`, photographer: "示例", url: "" },
});
const SPORT = [
  [2101, "11 月第 3 周 · 运动周报", "2026-11-16T21:00:00+08:00", "跑步 3 次 21.4 km · 骑行 1 次 32 km · 平均睡眠 7.1 h", "周报", { distance_km: 53.4, sessions: 4, sleep_h: 7.1, resting_hr: 58 }],
  [2094, "11 月第 2 周 · 运动周报", "2026-11-09T21:00:00+08:00", "跑步 2 次 14.6 km · 游泳 1 次 1.5 km · 平均睡眠 6.6 h", "周报", { distance_km: 16.1, sessions: 3, sleep_h: 6.6, resting_hr: 60 }],
  [2088, "晨跑 10.2 km", "2026-11-14T06:40:00+08:00", "配速 5'41\" · 平均心率 148", "跑步", { distance_km: 10.2, duration_min: 58, pace: "5'41\"", avg_hr: 148, calories: 712 }],
  [2079, "骑行 32 km · 奥森", "2026-11-08T08:15:00+08:00", "均速 24.6 km/h · 爬升 120 m", "骑行", { distance_km: 32, duration_min: 78, speed_kmh: 24.6, elevation_m: 120, calories: 860 }],
  [2071, "10 月 · 运动月报", "2026-10-31T21:00:00+08:00", "运动 14 次 · 跑步 68 km · 平均睡眠 6.9 h", "月报", { distance_km: 96.5, sessions: 14, sleep_h: 6.9, resting_hr: 59 }],
].map(([id, title, date, summary, activity, metrics]) => ({ id, kind: "sport", device: "watch", title, date, summary, activity, metrics, text: `${title}。${summary}。`, fields: { activity } }));
const CAR = [
  [1301, "望京 → 天津滨海", "2026-10-12T08:05:00+08:00", "126 km · 1 小时 52 分 · 21.3 kWh", "行程", { from: "北京 望京", to: "天津 滨海新区", distance_km: 126, duration_min: 112, energy_kwh: 21.3 }],
  [1302, "充电 · 天津滨海超充站", "2026-10-12T12:40:00+08:00", "38 kWh · 20% → 85%", "充电", { energy_kwh: 38, soc_from: 20, soc_to: 85, duration_min: 32, cost_yuan: 46.5 }],
  [1311, "望京 → 雁栖湖", "2026-10-19T09:20:00+08:00", "68 km · 1 小时 10 分 · 11.8 kWh", "行程", { from: "北京 望京", to: "怀柔 雁栖湖", distance_km: 68, duration_min: 70, energy_kwh: 11.8 }],
  [1318, "充电 · 家充桩", "2026-10-24T22:10:00+08:00", "42 kWh · 15% → 90%", "充电", { energy_kwh: 42, soc_from: 15, soc_to: 90, duration_min: 380 }],
  [1330, "10 月 · 用车月报", "2026-10-31T21:00:00+08:00", "行程 23 次 · 812 km · 充电 4 次", "月报", { distance_km: 812, trips: 23, charges: 4, energy_kwh: 141 }],
].map(([id, title, date, summary, event, extra]) => ({ id, kind: "car", device: "car", title, date, summary, event, ...extra, text: `${title}。${summary}。`, fields: { event } }));
const RECORDS = new Map([...PHOTOS.germany, ...PHOTOS.korea].map(photoItem).concat(SPORT, CAR).map((r) => [`${r.kind}/${r.id}`, r]));

const PRESETS = [
  { id: "photo_germany", chip: "德国出差照片", kind: "photo", device: "phone", question: "整理我在德国出差时拍的照片", gold: { facts: [{ label: "城市", value: "慕尼黑" }, { label: "城市", value: "法兰克福" }, { label: "城市", value: "柏林" }] } },
  { id: "photo_korea", chip: "韩国旅游照片", kind: "photo", device: "phone", question: "给我 2024 年去韩国旅游的照片", gold: { facts: [{ label: "城市", value: "首尔" }, { label: "城市", value: "釜山" }, { label: "城市", value: "济州" }] } },
  { id: "sport_month", chip: "最近一月运动", kind: "sport", device: "watch", question: "我最近一个月的运动情况怎么样？", gold: { facts: [{ label: "跑步", value: "21.4" }] } },
  { id: "car_month", chip: "上月用车出行", kind: "car", device: "car", question: "上个月我开车去过哪些地方，充了几次电？", gold: { facts: [{ label: "充电", value: "4 次", match: ["4次", "4 次"] }] } },
];

let devices = null;
async function deviceList() {
  if (devices) return devices;
  let pcAid = hex(32, 12);
  try { pcAid = (await (await realFetch("/api/cloudview?kind=hpis&limit=1")).json()).rows[0].aid; } catch {}
  devices = [
    { id: "phone", label: "手机", kind: "photo", kind_label: "照片", aid: hex(32, 11), records: 412, bytes: 186400000, exposure: "普通云可读取：全部 412 张照片及拍摄地点、时间" },
    { id: "pc", label: "PC", kind: "mail", kind_label: "邮件", aid: pcAid, records: 100000, bytes: 274490306, exposure: "普通云可读取：全部 100,000 封邮件与附件" },
    { id: "car", label: "车机", kind: "car", kind_label: "用车出行", aid: hex(32, 13), records: 1460, bytes: 2380000, exposure: "普通云可读取：两年内全部 1,460 条行程起止地点与充电记录" },
    { id: "watch", label: "手表", kind: "sport", kind_label: "运动健康", aid: hex(32, 14), records: 2190, bytes: 3120000, exposure: "普通云可读取：两年内全部 2,190 条心率、睡眠与运动记录" },
  ];
  return devices;
}
async function tenants() {
  const mine = (await deviceList()).map((d) => ({ aid: d.aid, aid_short: d.aid.slice(0, 8), records: d.records, bytes: d.bytes, mine: true }));
  const decoys = [21, 22, 23, 24, 25, 26].map((s, i) => { const aid = hex(32, s); return { aid, aid_short: aid.slice(0, 8), records: [38210, 912, 15400, 2650, 640, 7720][i], bytes: [1.9e8, 2.1e8, 7.4e7, 3.1e6, 1.6e8, 2.2e7][i] }; });
  // interleaved, so HPIS shows no grouping by person
  return [mine[1], decoys[0], mine[0], decoys[1], decoys[2], mine[3], decoys[3], mine[2], decoys[4], decoys[5]];
}

// ------------------------------------------------------------------ a synthetic run
function kindFor(q) {
  const p = PRESETS.find((x) => x.question.replace(/\s/g, "") === q.replace(/\s/g, ""));
  if (p) return [p.kind, p.id];
  if (/照片|相册|拍/.test(q)) return ["photo", /韩国|首尔|釜山|济州/.test(q) ? "photo_korea" : "photo_germany"];
  if (/运动|跑步|睡眠|心率|骑行/.test(q)) return ["sport", "sport_month"];
  if (/开车|用车|充电|行程|车/.test(q)) return ["car", "car_month"];
  return [null, null];
}
const ANSWERS = {
  photo_germany: "2025 年 9 月德国出差期间共找到 **6 张照片**，按城市整理如下：\n- **慕尼黑**（9 月 16 日）：清晨的玛利亚广场 [1]，傍晚的宁芬堡宫 [2]\n- **法兰克福**（9 月 18 日）：罗马广场的老房子 [3]，美因河畔夜景 [4]\n- **柏林**（9 月 20 日）：勃兰登堡门 [5]，博物馆岛 [6]\n需要的话可以按时间顺序生成一个出差相册。",
  photo_korea: "没有找到 2024 年 4 月在首尔市区以外的合影，已按 2024 年韩国旅行整理出 **6 张照片**：\n- **首尔**（4 月 6 日）：景福宫 [1]，北村韩屋村 [2]\n- **釜山**（4 月 8 日）：海云台清晨 [3]，甘川文化村 [4]\n- **济州**（4 月 10 日）：城山日出峰 [5]，涉地可支灯塔 [6]",
  sport_month: "最近一个月你一共运动 **18 次**，整体比上个月更规律：\n- 跑步累计约 **36 km**，最长一次晨跑 10.2 km，平均心率 148 [3]\n- 11 月第 3 周最活跃：跑步 3 次 21.4 km，另有一次 32 km 骑行 [1][4]\n- 平均睡眠约 **6.9 小时**，第 2 周偏少（6.6 小时）[2][5]\n建议把睡眠稳定在 7 小时以上。",
  car_month: "上个月（10 月）你开车出行 **23 次**，共约 **812 km** [5]，主要去了：\n- **天津滨海新区**：10 月 12 日往返，单程 126 km [1]\n- **怀柔雁栖湖**：10 月 19 日，68 km [3]\n共充电 **4 次**，其中一次在天津滨海超充站（38 kWh）[2]，一次在家充桩（42 kWh）[4]。",
};
function evidenceFor(preset) {
  if (preset === "photo_germany") return PHOTOS.germany.map(photoItem);
  if (preset === "photo_korea") return PHOTOS.korea.map(photoItem);
  const pick = (r) => ({ id: r.id, kind: r.kind, title: r.title, date: r.date, summary: r.summary });
  return preset === "sport_month" ? SPORT.map(pick) : CAR.map(pick);
}
let active = null;
function mockRun(body, kind, preset) {
  const devs = devices || [];
  const dev = { photo: "phone", sport: "watch", car: "car" }[kind];
  const total = devs.find((d) => d.id === dev)?.records || 400;
  const bytes = devs.find((d) => d.id === dev)?.bytes || 4e6;
  const batches = Math.max(1, Math.ceil(bytes / 16777216));
  const ev = evidenceFor(preset);
  const answer = ANSWERS[preset];
  const chips = kind === "photo" ? [{ kind: "国家", value: preset === "photo_korea" ? "韩国" : "德国" }, { kind: "时间", value: preset === "photo_korea" ? "2024 年" : "全部" }] : [{ kind: "时间", value: kind === "sport" ? "最近一个月" : "2026-10" }];
  const relaxed = preset === "photo_korea" ? ["city"] : [];
  const sched = [];
  const at = (t, e) => sched.push([t, e]);
  at(0, { type: "plan", question: body.question, plan: { kind, device: dev, total, relaxed, semantic: body.question }, kind, device: dev, total, relaxed, chips, embed_ms: 13.8, net: body.net, model: body.model });
  if (relaxed.length) at(8, { type: "notice", lane: null, text: "没有找到 2024 年 4 月在首尔市区以外的合影，已放宽城市条件" });
  at(20, { type: "sealed", lane: "v2", bytes: 3168, sealed_bytes: 2240, preview: hex(80, 5), tokens: [hex(24, 6), hex(24, 7), hex(24, 8)] });
  at(24, { type: "stage", lane: "v2", stage: "request", state: "start", edge_up: 3168 });
  at(70, { type: "auth", lane: "v2", edge_up: 3168 });
  at(150, { type: "hpis", lane: "v2", up_bytes: 2600, down_bytes: 41200, rows: 10, vector_rows: total, edge_up: 3168 });
  at(170, { type: "decrypt", lane: "v2", rows: ev.length, ms: 3.2 });
  at(190, { type: "evidence", lane: "v2", items: ev });
  const tok = (lane, t0) => {
    const parts = answer.match(/.{1,6}/gs);
    parts.forEach((p, i) => at(t0 + i * 45, { type: "token", lane, text: p }));
    at(t0, { type: "stage", lane, stage: "generate", state: "first", ttft_ms: t0 });
    return t0 + parts.length * 45 + 30;
  };
  const v2end = tok("v2", 1700);
  at(v2end, { type: "finish", lane: "v2", ttft_ms: 1700, total_ms: v2end, edge_up: 3168, edge_down: 9800 });
  at(30, { type: "stage", lane: "plain", stage: "upload", state: "start", batches });
  let t = 30;
  for (let i = 1; i <= batches; i++) { t += 260; at(t, { type: "upload", lane: "plain", batch: i, batches, edge_up: Math.min(bytes, i * 16777216) }); }
  at(t + 10, { type: "stage", lane: "plain", stage: "upload", state: "end", edge_up: bytes });
  at(t + 40, { type: "stage", lane: "plain", stage: "retrieve", state: "start" });
  at(t + 400, { type: "search", lane: "plain", scanned: total, candidates: Math.min(100, total), edge_up: bytes + 2400 });
  at(t + 1500, { type: "evidence", lane: "plain", items: ev });
  const pend = tok("plain", t + 2600);
  if (MODE === "edge") at(t + 1200, { type: "notice", lane: "plain", text: "普通云 6 秒内只为 64 条候选现算了向量，仅在这些记录中排序" });
  at(pend, { type: "finish", lane: "plain", ttft_ms: t + 2600, total_ms: pend, edge_up: bytes + 2400, edge_down: 9800, ...(MODE === "edge" ? { timed_out: true } : {}) });
  const last = Math.max(v2end, pend);
  at(last + 300, { type: "verify", plain_ms: 4.12, cipher_ms: 4.31, plain_p25_ms: 3.98, cipher_p25_ms: 4.15, overhead_pct: 4.27, identical: true, k: 10, pairs: 60 });
  at(last + 320, { type: "end" });
  sched.sort((a, b) => a[0] - b[0]);

  const timers = [];
  const finished = new Set();
  let ctrl;
  const stream = new ReadableStream({
    start(c) {
      ctrl = c;
      const t0 = performance.now();
      for (const [ms, e] of sched) timers.push(setTimeout(() => {
        const now = Math.round(performance.now() - t0);
        const out = { ...e, t: now };
        if (e.type === "finish") { finished.add(e.lane); out.total_ms = now; }
        c.enqueue(enc.encode(JSON.stringify(out) + "\n"));
        if (e.type === "end") { c.close(); active = null; }
      }, ms));
      active = {
        stop() {
          timers.forEach(clearTimeout);
          const now = Math.round(performance.now() - t0);
          for (const lane of ["plain", "v2"]) if (!finished.has(lane)) c.enqueue(enc.encode(JSON.stringify({ type: "finish", lane, stopped: true, t: now }) + "\n"));
          c.enqueue(enc.encode(JSON.stringify({ type: "end", stopped: true, t: now }) + "\n"));
          c.close();
          active = null;
        },
      };
    },
    cancel() { timers.forEach(clearTimeout); active = null; },
  });
  return new Response(stream, { headers: { "Content-Type": "application/x-ndjson" } });
}

// ------------------------------------------------------------------ fetch
async function handle(url, init) {
  const p = url.pathname;
  if (p === "/api/status") {
    const s = await (await realFetch(url, init)).json();
    s.devices = await deviceList();
    s.presets = (s.presets || []).map((x) => ({ kind: "mail", device: "pc", ...x })).concat(PRESETS);
    s.models = (s.models || []).map((m) => ({ ...m, vision: m.id === "qwen" }));
    return json(s);
  }
  if (p === "/api/run" && init?.method === "POST") {
    const body = JSON.parse(init.body);
    const [kind, preset] = kindFor(body.question || "");
    if (kind) { await deviceList(); return mockRun(body, kind, preset); }
    return null;
  }
  if (p === "/api/stop") {
    if (active) { active.stop(); return json({ ok: true, stopped: true }); }
    return null;
  }
  let m = p.match(/^\/api\/record\/(\w+)\/(\d+)$/);
  if (m) { const r = RECORDS.get(`${m[1]}/${m[2]}`); return r ? json(r) : json({ error: "not found" }, 404); }
  m = p.match(/^\/api\/cipher\/(\d+)$/);
  if (m && url.searchParams.get("kind") && url.searchParams.get("kind") !== "mail") {
    const id = Number(m[1]);
    const kind = url.searchParams.get("kind");
    const dev = (await deviceList()).find((d) => d.kind === kind);
    return json({ aid: dev.aid, rid: id, body: hex(384, id), bodyBytes: 640 + (id % 97), tags: [hex(64, id + 1), hex(64, id + 2), hex(64, id + 3)], vector: hex(96, id + 4), vectorBytes: 2076, ...(kind === "photo" ? { imageBytes: 380028 + id * 97 } : {}) });
  }
  if (p === "/api/cloudview") {
    const real = await (await realFetch(url, init)).json();
    if (url.searchParams.get("kind") === "plain") {
      const extra = [...PHOTOS.germany.slice(0, 3).map(photoItem), ...SPORT.slice(0, 2), ...CAR.slice(0, 2)].map((r) => ({
        id: r.id, owner: "林舟", device: r.device, kind: r.kind, date: r.date.slice(0, 16).replace("T", " "), from: r.kind === "photo" ? r.place.city : "", subject: r.title, snippet: r.summary, thumb: r.thumb,
      }));
      const rows = real.rows.map((r) => ({ owner: "林舟", device: "pc", kind: "mail", ...r }));
      for (let i = 0; i < extra.length; i++) rows.splice(2 + i * 3, 0, extra[i]);
      return json({ rows, total: (await deviceList()).reduce((a, d) => a + d.records, 0) });
    }
    const T = await tenants();
    return json({ ...real, tenants: T, rows: real.rows.map((r, i) => { const t = T[(i * 7) % T.length]; return { ...r, aid: t.aid, aid_short: t.aid_short, tenant: (i * 7) % T.length }; }) });
  }
  if (p === "/api/spy") {
    const real = await (await realFetch(url, init)).json();
    const T = await tenants();
    const seed = Math.floor(Math.random() * 1e6);
    const rows = Array.from({ length: 40 }, (_, i) => { const t = T[(seed + i * 3) % T.length]; return { aid_short: t.aid_short, tenant: (seed + i * 3) % T.length, rid: 1 + ((seed * 31 + i * 977) % 90000), body: hex(96, seed + i), matched: false }; });
    return json({ ...real, hpis_scanned: T.reduce((a, t) => a + t.records, 0), hpis_rows: rows, tenants: T, note: "HPIS 没有终端的检索密钥：明文关键词无法变成它认识的检索令牌" });
  }
  return null;
}

export function install(mode) {
  MODE = mode;
  window.HPIS_MOCK = { photoUrl };
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
      const r = await handle(url, init);
      if (r) return r;
    }
    return realFetch(input, init);
  };
  document.documentElement.dataset.mock = "1";
}
