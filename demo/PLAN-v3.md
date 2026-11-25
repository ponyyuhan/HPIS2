# Demo v3 — multi-device data, photos, robustness (working contract)

Shared contract for the parallel work on the leadership demo (面向个人敏感数据的智能问答).
Three streams work in separate git worktrees and must not edit each other's files.
The integrator merges them and updates the docs (README, H200部署步骤.md, HPIS2交付说明.md).

| Stream | Owns | Must not edit |
|---|---|---|
| DATA | `demo/corpus/devices/` (new package), `demo/data/devices/` (new folder) | anything else |
| BACKEND | `demo/*.py`, `demo/tests/` | `demo/web/`, `demo/corpus/`, `demo/data/`, `hpis/`, `acceptance/`, docs |
| FRONTEND | `demo/web/` | everything else |

House rules that still apply: UI text is Chinese; dark navy theme by default plus the ivory light theme
(`?theme=light`); no explanatory footnotes; every number must read the same on every page; lane times are
`上传 / 检索 / 模型作答 / 完整回答`, cut on one terminal-side clock so they add up exactly; the plain lane and
the encrypted lane share code paths except for what is being compared. Keep intranet IPs, usernames and
API keys out of the repo; never print `OPENROUTER_API_KEY`.

## 1. Story

林舟's personal data comes from four devices. Each device uploads its own data to HPIS under **its own
anonymous AID with its own keys**, so HPIS cannot tell the four AIDs belong to one person. HPIS also holds
other people's data (decoy tenants), so the store is visibly multi-tenant and anonymous.

| device id | label | kind | kind label | data |
|---|---|---|---|---|
| `pc` | PC | `mail` | 邮件 | existing 100,000 emails |
| `phone` | 手机 | `photo` | 照片 | new photo album (DATA) |
| `watch` | 手表 | `sport` | 运动健康 | new watch records (DATA) |
| `car` | 车机 | `car` | 用车出行 | new car records (DATA) |

## 2. Data files (DATA → BACKEND), all under `demo/data/devices/`

- `photo.jsonl`, `sport.jsonl`, `car.jsonl` — one record per line, `id` = 1..N in line order.
- `photos/img/{id}.jpg` (long side 960 px, JPEG quality ≈ 80) and `photos/thumb/{id}.jpg` (long side 360 px).
- `photos/ATTRIBUTION.md` — source dataset, licence, photographer per photo.
- `embeddings_{kind}.f32` — float32, 512-d, L2-normalised, row i = line i of `{kind}.jsonl`, computed with
  `demo.embed` (bge-small-zh-v1.5, document side) on the text described in `embeddings_{kind}.json`
  (`{"model", "dim", "count", "text"}`).
- `presets.json` — the new preset questions (schema below).
- `stats.json` — counts, total bytes and date range per kind.
- No single file above 90 MB (split like `demo/data/embeddings.f32.partNN` if ever needed).

Record schema (all kinds):

```json
{
  "id": 1,
  "kind": "photo",
  "device": "phone",
  "date": "2025-09-16T08:12:00+02:00",
  "title": "慕尼黑 · 玛利亚广场",
  "text": "Chinese text the model reads; for photos the caption plus place and time",
  "fields": {"year": "2025", "month": "2025-09", "country": "德国", "city": "慕尼黑", "type": "街景", "trip": "德国出差"}
}
```

Kind-specific keys:
- photo: `file`, `thumb` (paths relative to `demo/data/devices/`), `width`, `height`, `bytes`,
  `place` `{name, lat, lon, country, city}`, `caption` (Chinese, 1–2 sentences, only what is visible),
  `tags` (Chinese scene tags), `source` `{dataset, id, photographer, url}`.
- sport: `activity` (跑步 骑行 游泳 步行 羽毛球 力量训练 睡眠 日常 周报 月报), `metrics` (numbers).
- car: `event` (行程 充电 导航 保养 提醒 周报 月报), `from`, `to`, `distance_km`, `duration_min`, `energy_kwh` …

`fields` are what SSE indexes as HMAC labels: photos year, month, country, city, type, trip; sport year,
month, activity; car year, month, event, city. Values short and canonical (Chinese names).
Weekly and monthly summary records (周报 / 月报) exist for sport and car, so "最近一个月…" questions are
answerable from a handful of retrieved records.

Presets (`presets.json`): same schema as `demo/data/presets.json` plus `kind` and `device`:

```json
{"id": "photo_germany", "chip": "德国出差照片", "kind": "photo", "device": "phone",
 "question": "整理我在德国出差时拍的照片",
 "plan": {"kind": "photo", "countries": ["德国"], "cities": [], "trips": [], "types": [],
          "date_from": null, "date_to": null, "semantic": "德国 出差 照片"},
 "gold": {"facts": [{"label": "…", "value": "…", "match": ["…"]}]},
 "evidence_ids": [1, 2, 3]}
```

At least: `photo_germany` (整理我在德国出差时拍的照片), `photo_korea` (给我 2024 年去韩国旅游的照片),
`sport_month` (我最近一个月的运动情况怎么样？), `car_month` (上个月我开车去过哪些地方，充了几次电？).
Gold facts must be true in the data and checkable by substring match. "Today" is 2026-11-20 18:00 +08:00.

## 3. Engine and API (BACKEND → FRONTEND)

### Routing (on-device planner)
Question → kind (photo / sport / car, else mail) + filters + semantic text. Must handle rephrasings,
relative years (今年 去年 前年), relative windows (上个月 最近一个月 上周 本周), place names via a gazetteer
(country and city, Chinese, incl. 慕尼黑 柏林 法兰克福 首尔 釜山 济州 东京 京都 …). Unknown questions go to
mail with pure vector search. If filters leave 0 candidates, drop filters in order
(city/trip → country → type/activity/event → time) until ≥ 1 candidate, report it in `plan.relaxed` and a
`notice` event, and let the model say plainly what was not found.

### HPIS tenants
One trusted-process tenant per device (distinct AID and keys) plus ≥ 6 decoy tenants holding other people's
ciphertext (re-encrypted samples are fine). Photo image bytes are stored as AES-256-GCM ciphertext in HPIS.

### Lanes
- Plain: upload the whole collection of that kind (emails 275 MB; the full photo album; the full watch or
  car log), cloud parses, filters, builds a lexical shortlist (≤ 100), embeds on the fly within a 6 s budget
  (stop early and rank what is embedded), Top-10, evidence 5 (photos 6), same model.
- Encrypted: unchanged (sealed request, SSE match, TEE exact Top-10, HPIC decrypts the hits); without
  filters it ranks all records of that kind.
- Photos are given to the model as images (long side ≤ 960 px) plus date, place and caption when the model
  supports vision, otherwise captions only. Vision probe: Ollama `/api/show` capabilities contains "vision";
  OpenRouter `input_modalities` contains "image"; env `HPIS_LLM_VISION=0|1` overrides.

### Limits (never hang)
- A run never exceeds 90 s; typical free-form questions finish in < 20 s locally.
- Model: first token within 25 s, whole answer within 60 s; on timeout finish with what was generated, or an
  extractive summary of the evidence if nothing was, with `finish.timed_out = true`.
- `POST /api/stop` cancels the active run: each lane ends with `finish {stopped: true}`, the stream ends with
  `end {stopped: true}`, the engine lock is released, and the cloud worker stops its embedding loop.

### Run events (NDJSON from `POST /api/run`, existing events kept)
- `plan` adds `kind`, `device`, `total` (records of that kind), `relaxed` (list of dropped filter names).
- `notice` (new): `{"type": "notice", "lane": null | "plain" | "v2", "text": "…"}`.
- `evidence` items for non-mail kinds: `{"id", "kind", "title", "date", "summary"}`; photos add `place`
  and `thumb` (`/api/photo/{id}?size=thumb`). Mail items unchanged.
- `finish` adds `stopped` and `timed_out`; `end` adds `stopped`.

### Endpoints
- `GET /api/status` adds `devices`: `[{"id", "label", "kind", "kind_label", "aid", "records", "bytes",
  "exposure"}]` (`exposure` e.g. "普通云可读取：全部 412 张照片及拍摄地点、时间"); presets gain `kind`,
  `device`; models gain `vision`.
- `POST /api/stop` → `{"ok": true, "stopped": bool}`.
- `GET /api/photo/{id}?size=thumb|full` → image/jpeg (the device's own copy).
- `GET /api/record/{kind}/{id}` → plaintext record for the drawer (mail stays `/api/mail/{id}`).
- `GET /api/cipher/{id}?kind=mail|photo|sport|car` → the HPIS view of one record (default mail, as today):
  `{"aid", "rid", "body", "bodyBytes", "tags", "vector", "vectorBytes", "imageBytes"?}`.
- `GET /api/cloudview?kind=plain&limit=N` → `{"rows": [{"owner": "林舟", "device", "kind", "date", "from",
  "subject", "snippet", "thumb"?}], "total"}` — real-name rows across kinds.
- `GET /api/cloudview?kind=hpis&limit=N` → `{"docs", "bytes", "postings", "tenants": [{"aid", "aid_short",
  "records", "bytes", "mine"}], "rows": [{"aid", "aid_short", "tenant", "rid", "body", "tags", "vector"}]}`
  — rows interleaved from many AIDs; `mine` only for an optional presenter reveal.
- `GET /api/spy?q=` → `{"q", "plain_hits", "samples", "hpis_scanned", "hpis_bytes", "hpis_hits": 0,
  "hpis_rows": [{"aid_short", "tenant", "rid", "body", "matched": false}], "tenants", "note"}` — a fresh
  HPIS sample for every query, so the encrypted side visibly reacts.

## 4. UI (FRONTEND)

1. Top tabs, in order: 主页 (shows the cover again) · 业务演示 · 方案架构 · 云端视角 · 性能对比.
   Rename every 现场对比 to 业务演示 (cover button 进入业务演示).
2. Cover: keep the look, add the HPIC certification (2026-06-01，HarmonyOS 个人智能计算系统 HPIC 通过中国信息
   通信研究院泰尔实验室认证，证书编号 CTTL-XXFF-2026-0242，端云协同场景 AI 隐私安全能力达到增强级，该认证体系下
   首个获此评级的端云协同系统) in a fitting, tasteful style, and the labs 数据保护技术实验室 and
   安全与隐私保护技术实验室. Optionally hint at the four devices.
3. 业务演示 lanes distinguishable at a glance: left = ordinary plaintext (warm red/orange, unlocked,
   "明文" identity everywhere), right = privacy-preserving (teal/green, shield/lock, "密态").
4. Presets from `/api/status`, grouped or marked by device (手机 PC 车机 手表); a Stop button while running;
   a client watchdog; `notice` banners; photo answers render a thumbnail grid (click → drawer with the photo,
   metadata, and "HPIS 中的样子"); sport/car evidence render as compact lists; terminal node icon/label follows
   `plan.device`.
5. 方案架构: redesign the diagram — four devices (手机 PC 车机 手表, each with local compute + storage and
   their data) uploading to HPIC (机密计算 + 大模型) and HPIS (匿名存储: SSE + 密态向量, encrypted
   邮件/照片/运动/出行), one anonymous AID per device, unlinkable. Must look better than a box sketch.
6. 云端视角: left = 实名 (owner, device, kind visible), right = 匿名 (many AIDs, colour-coded, different
   ciphertext, 不可关联 markers). Searching visibly runs on both sides: the plain side highlights hits, the
   HPIS side animates a scan over fresh ciphertext rows and shows 命中 0 条 with the reason.
7. 性能验收 tab renamed 性能对比.
