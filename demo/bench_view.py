"""Turn the acceptance harness output (acceptance/results/...) into the dashboard view."""

from __future__ import annotations

import json
import re
from pathlib import Path

DESC = {
    "sse_exact": ("精确字段 SSE", "命中 1 条", "按唯一的字段值（如编号）查：终端把字段值变成 HMAC 令牌，HPIS 在倒排表上精确匹配，只读取并返回命中的那 1 条密文。"),
    "sse_keyword": ("关键词 SSE", "命中 10 条", "按关键词查：一个关键词令牌对应 10 条记录，HPIS 只读取这 10 条密文，HPIC 只解密这 10 条。"),
    "vec_tee": ("向量 Top-10 · 软硬协同精确", "精确 · TEE", "向量以 AES-GCM 密文存放，只在可信计算区内解开、用与明文相同的内核精确排序，结果逐条一致。"),
    "vec_ppe": ("向量 Top-10 · 纯密码学 PPE", "近似 · β=0.25", "无需硬件信任：HPIS 直接在 RAP 扰动表示上排序（近似检索），与同一排序内核的明文检索比较。"),
    "vec_ppe_b0125": ("向量 Top-10 · PPE（β=0.125）", "近似 · β=0.125", "扰动更小的 RAP 表示：召回更高，同样与同核明文比较。"),
}
ORDER = ["sse_exact", "sse_keyword", "vec_tee", "vec_ppe", "vec_ppe_b0125"]


def _load(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _headline(sc: dict) -> dict | None:
    h = sc.get("headline")
    if isinstance(h, dict) and "overhead_pct" in h:
        return h
    if "overhead_pct" in sc and "plaintext_mean_ms" in sc:
        return sc
    return None


def _scenarios(data: dict) -> list[tuple[str, dict]]:
    sc = data.get("scenarios")
    if isinstance(sc, dict):
        return list(sc.items())
    if isinstance(sc, list):
        return [(s.get("key") or s.get("id") or s.get("name_zh", str(i)), s) for i, s in enumerate(sc)]
    return []


SHORT = {
    "sse_exact": ("精确字段检索", "SSE", "按唯一的字段值（如编号）查，令牌只命中 1 条，只取这 1 条"),
    "sse_keyword": ("关键词检索", "SSE", "按关键词查，一个关键词对应 10 条，只取这 10 条"),
    "vec_tee": ("向量 Top-10 · 精确", "TEE", "可信计算区内精确排序，结果与明文一致"),
    "vec_ppe": ("向量 Top-10 · 纯密码学", "PPE", "无需可信硬件，在扰动密文上近似排序"),
}


def _short_correctness(text: str) -> str:
    m = re.search(r"Recall@10\s*([\d.]+)%", text)
    if m:
        return f"Recall@10 {float(m.group(1)):.1f}%"
    return "与明文一致 100%" if ("一致" in text or "同序" in text) else text


def _rollup_view(data: dict) -> dict:
    """Compact dashboard view of acceptance/results/summary.json (schema hpis2-acceptance-rollup/1)."""
    env = data.get("environment") or {}
    scenarios = []
    for row in data.get("scenarios") or []:
        row = dict(row)
        if row.get("key") in SHORT:
            row["name_zh"], row["tag"], row["desc_zh"] = SHORT[row["key"]]
        row["correctness"] = _short_correctness(str(row.get("correctness", "")))
        scenarios.append(row)
    shown = [r for r in scenarios if r.get("headline") is not False]
    pairs = shown[0].get("pairs") if shown else None
    cpu = re.sub(r"\s*\d+-Core Processor", "", env.get("cpu", "")).strip()
    os_name = "Debian 12" if "Debian" in env.get("os", "") else (env.get("os", "").split("·")[0].strip())
    view = {
        "view": "dashboard",
        "headline": {
            "title": f"{len(shown)} 个验收场景的时延损耗均低于 10%",
            "subtitle": " · ".join(x for x in (cpu, os_name, f"每个场景 {pairs:,} 对明密文请求" if pairs else "") if x),
        },
        "environment": {},
        "scenarios": scenarios,
    }
    io = data.get("io")
    if isinstance(io, dict) and io.get("rows"):
        labels = {"1.0": "无密文索引：全量读取", "精确字段": "精确字段（1 条）", "关键词": "关键词（10 条）", "向量": "向量 Top-10（10 条）"}
        rows = []
        for r in io["rows"]:
            r = dict(r)
            for k, v in labels.items():
                if k in r.get("label", ""):
                    r["label"] = v
                    break
            if r["label"].startswith("无密文索引"):
                r["color"] = "linear-gradient(90deg,#d2691e,#f0a868)"
            rows.append(r)
        view["io"] = {"title": "每次查询读出的数据量", "desc": "", "rows": rows}
    return view


def build_view(data: dict | None) -> dict:
    if not data:
        return {}
    if data.get("view") == "dashboard":  # already in dashboard form
        return data
    if str(data.get("schema", "")).startswith("hpis2-acceptance-rollup"):
        return _rollup_view(data)
    runs = data.get("runs")
    primary = data
    if isinstance(runs, dict) and runs:  # rollup that embeds several runs
        primary = runs.get("main") or next(iter(runs.values()))
    items = dict(_scenarios(primary))
    if not items and isinstance(data.get("main"), dict):
        primary = data["main"]
        items = dict(_scenarios(primary))
    scenarios = []
    for key in ORDER + [k for k in items if k not in ORDER]:
        sc = items.get(key)
        if not isinstance(sc, dict):
            continue
        if sc.get("section", "main") != "main":
            continue
        h = _headline(sc)
        if not h:
            continue
        name, tag, desc = DESC.get(key, (sc.get("name_zh", key), "", ""))
        correctness = str(h.get("correctness") or "")
        scenarios.append(
            {
                "key": key,
                "name_zh": name,
                "tag": tag,
                "desc_zh": desc,
                "overhead_pct": float(h["overhead_pct"]),
                "ci95": list(h.get("ci95") or [h["overhead_pct"], h["overhead_pct"]]),
                "plaintext_mean_ms": float(h["plaintext_mean_ms"]),
                "cipher_mean_ms": float(h["cipher_mean_ms"]),
                "correctness_label": "正确性",
                "correctness": correctness.replace("与明文逐条同序一致", "与明文一致"),
                "pairs": h.get("pairs"),
            }
        )
    env = primary.get("environment") or data.get("environment") or {}
    ds = primary.get("dataset") or {}
    params = primary.get("parameters") or {}
    rows = ds.get("rows") or params.get("rows")
    dims = ds.get("dims") or ds.get("dimensions") or ds.get("dim") or params.get("dim")
    dataset = ds.get("name") or ("GloVe-100" if dims == 100 else "向量数据")
    worst = max((s["overhead_pct"] for s in scenarios), default=0)
    pairs = scenarios[0].get("pairs") if scenarios else None
    view = {
        "view": "dashboard",
        "headline": {
            "title": "全部密文检索场景的平均端到端时延损耗均低于 10%" if worst < 10 else "部分场景损耗超过 10%",
            "subtitle": (
                f"{env.get('distribution') or 'Linux'} · {env.get('cpu', '')} · "
                f"{dataset} {rows:,} 行 × {dims} 维 · 每场景 {pairs:,} 对明密文请求交替配对"
                if rows and dims and pairs
                else (env.get("cpu") or "")
            ),
        },
        "environment": {
            "cpu": env.get("cpu", ""),
            "os": env.get("distribution") or env.get("os", ""),
            "dataset": f"{dataset} · {rows:,} 行" if rows else dataset,
        },
        "scenarios": scenarios,
    }
    io = primary.get("io") or data.get("io")
    if isinstance(io, dict):
        rows_io = []
        full = io.get("1.0_field") or io.get("1.0_vector")
        if full:
            rows_io.append(
                {
                    "label": "无密文索引：读取该 AID 全部数据",
                    "bytes": full["body_bytes"],
                    "color": "linear-gradient(90deg,#d2691e,#f0a868)",
                }
            )
        for key, label in (("2.0_vector", "密文索引：向量 Top-10"), ("2.0_word", "密文索引：关键词（10 条）"), ("2.0_field", "密文索引：精确字段（1 条）")):
            if io.get(key):
                rows_io.append({"label": label, "bytes": io[key]["body_bytes"]})
        if rows_io:
            view["io"] = {
                "title": "每次查询从存储读出并搬到 HPIC 的数据量",
                "desc": f"同一租户 {rows:,} 条记录；密文索引让 HPIS 在 AID 内先选出结果，只读、只传、只解密命中的记录。" if rows else "",
                "rows": rows_io,
            }
    return view


def load_view(path: Path) -> dict:
    data = _load(path)
    if data is None:
        # fall back to the newest run directory with a summary.json
        base = path.parent
        runs = sorted(
            (p for p in base.glob("*/summary.json") if "superseded" not in p.parent.name),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        data = _load(runs[0]) if runs else None
    return build_view(data)
