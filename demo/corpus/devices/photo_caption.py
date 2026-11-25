"""Stage 3 (photos): Chinese captions and scene labels from a vision model (OpenRouter).

    python3 -I demo/corpus/devices/photo_caption.py [--limit N] [--model qwen/qwen3.8-flash]

Reads ``$BUILD/work/cap/{photo_id}.jpg`` (640 px copies made by photo_fetch.py) and
writes one cached JSON response per photo under ``$BUILD/cache/llm/<model>/``.
Re-runs only call the model for photos without a cached answer. Needs the
environment variable OPENROUTER_API_KEY (never printed). Prints the running cost.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.corpus.devices.common import LLM_CACHE, WORK, read_jsonl  # noqa: E402

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "qwen/qwen3.8-flash"
TYPES = ["街景", "建筑", "地标", "夜景", "美食", "咖啡馆", "自然风光", "海边", "公园", "花卉", "会议展览", "交通出行",
         "室内", "运动"]
SYSTEM = ("你是相册图片标注助手。只依据画面中真实可见的内容作答：不推测人物身份，不写品牌或店名，"
          "不写相机参数；看不出具体地点时不要编造地名。")
PROMPT = """请观察这张照片，只输出一个 JSON 对象，不要输出其他文字：
{
 "caption": "1–2 句中文（不超过 60 字），客观描述画面中可见的内容；不提人名、品牌、店名；没有把握时不要写具体城市或国家",
 "tags": ["3–6 个中文场景标签，例如 街景 建筑 夜景 美食 咖啡 公园 湖泊 海边 樱花 雪景 日落 机场 列车 酒店 会议 跑道 寺庙 花卉 天空"],
 "type": "从这些里选一个：%s",
 "region": "画面最可能拍摄于哪里：中国、日本、韩国、德国、欧洲其他、北美、东南亚、其他、无法判断",
 "region_evidence": "判断依据，如可见文字、著名地标、典型建筑风格；无法判断时为空字符串",
 "text_lang": "画面中可读文字的语言：无、中文、日文、韩文、德文、英文、其他",
 "people": "人物情况：无、远处小人物、有人物但看不清脸、清晰可辨的人脸",
 "season": "春、夏、秋、冬、无法判断",
 "time": "白天、清晨或傍晚、夜晚、室内",
 "landmark": "能确定的著名地标中文名，没有则为空字符串",
 "brand": "画面中是否有醒目的品牌标志或商标：true 或 false",
 "quality": "1–5 的整数：作为普通人手机相册里的生活或旅行照片是否自然、好看"
}""" % "、".join(TYPES)


def _key(pid: str, model: str) -> str:
    return hashlib.sha256(f"{model}|{SYSTEM}|{PROMPT}|{pid}".encode()).hexdigest()[:16]


def cache_path(pid: str, model: str = MODEL) -> Path:
    return LLM_CACHE / model.replace("/", "__") / f"{pid}-{_key(pid, model)}.json"


def parse(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.M)
    s, e = t.find("{"), t.rfind("}")
    obj = json.loads(t[s : e + 1])
    if not isinstance(obj, dict) or not str(obj.get("caption", "")).strip():
        raise ValueError("no caption")
    obj["tags"] = [str(x).strip() for x in (obj.get("tags") or []) if str(x).strip()][:6]
    if obj.get("type") not in TYPES:
        obj["type"] = obj.get("type") or ""
    try:
        obj["quality"] = int(obj.get("quality") or 0)
    except (TypeError, ValueError):
        obj["quality"] = 0
    obj["brand"] = str(obj.get("brand")).lower() == "true"
    return obj


def call(pid: str, model: str = MODEL) -> tuple[str, dict | None, float]:
    path = cache_path(pid, model)
    if path.exists():
        rec = json.loads(path.read_text("utf-8"))
        return pid, rec.get("parsed"), 0.0
    img = (WORK / "cap" / f"{pid}.jpg").read_bytes()
    b64 = base64.b64encode(img).decode()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ]},
        ],
        "temperature": 0.2,
        "max_tokens": 600,
        "usage": {"include": True},
        "reasoning": {"enabled": False},
    }
    headers = {
        "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
        "Content-Type": "application/json",
        "X-Title": "hpis2-demo-devices",
    }
    delay = 3.0
    last = ""
    for _attempt in range(5):
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = data["choices"][0]["message"].get("content") or ""
            usage = data.get("usage") or {}
            cost = float(usage.get("cost") or 0.0)
            parsed = parse(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            rec = {"pid": pid, "model": model, "provider": data.get("provider"), "text": text, "parsed": parsed,
                   "usage": usage, "cost": cost, "ts": time.time()}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
            tmp.replace(path)
            return pid, parsed, cost
        except urllib.error.HTTPError as e:
            last = f"http {e.code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:80]
        time.sleep(delay)
        delay *= 2
    print(f"[caption] {pid} failed: {last}", flush=True)
    return pid, None, 0.0


def total_cost(model: str = MODEL) -> tuple[int, float]:
    n, usd = 0, 0.0
    folder = LLM_CACHE / model.replace("/", "__")
    for p in folder.glob("*.json"):
        if p.name.startswith("._"):
            continue
        try:
            usd += float(json.loads(p.read_text("utf-8")).get("cost") or 0)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n, usd


def main() -> None:
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else MODEL
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    pids = [r["id"] for r in read_jsonl(WORK / "pool.jsonl") if (WORK / "cap" / f"{r['id']}.jpg").exists()]
    if limit:
        pids = pids[:limit]
    t0 = time.time()
    spent = 0.0
    ok = 0
    with ThreadPoolExecutor(6) as ex:
        for i, (_pid, parsed, cost) in enumerate(ex.map(lambda p: call(p, model), pids), 1):
            spent += cost
            ok += parsed is not None
            if i % 50 == 0:
                print(f"[caption] {i}/{len(pids)} ok={ok} this run ${spent:.4f} {time.time() - t0:.0f}s", flush=True)
    n, usd = total_cost(model)
    print(f"[caption] done: {ok}/{len(pids)} captioned; this run ${spent:.4f}; cache total {n} answers ${usd:.4f}")


if __name__ == "__main__":
    main()
