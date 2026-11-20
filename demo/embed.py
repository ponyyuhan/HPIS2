"""Sentence embeddings for the demo mailbox: BAAI/bge-small-zh-v1.5 on CPU.

The ONNX export of the model (Hugging Face repo ``Xenova/bge-small-zh-v1.5``)
runs with onnxruntime + tokenizers only, so the same vectors come out on macOS
and Linux. Model files are cached under ``demo/data/models/``.

    from demo.embed import Embedder, doc_text
    emb = Embedder()
    docs = emb.encode_docs([doc_text(mail) for mail in mails])   # float32 [N, 512], L2-normalised
    q = emb.encode_query("下周去深圳出差，航班、酒店和会议分别是怎么安排的？")  # float32 [512]
    scores = docs @ q

``doc_text`` is the single recipe used to build ``demo/data/embeddings.f32``;
``embeddings.json`` records the same recipe parameters.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np

MODEL_ID = "BAAI/bge-small-zh-v1.5"
ONNX_REPO = "Xenova/bge-small-zh-v1.5"
ONNX_FILES = ("onnx/model.onnx", "tokenizer.json", "config.json", "tokenizer_config.json", "special_tokens_map.json")
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
DIM = 512
MAX_LEN = 512

DATA = Path(os.environ.get("HPIS_DEMO_DATA", Path(__file__).resolve().parent / "data"))
MODEL_DIR = Path(os.environ.get("HPIS_DEMO_EMBED_MODEL", DATA / "models" / "bge-small-zh-v1.5"))

# ---------------------------------------------------------------- doc recipe
RECIPE = {
    "fields": ["subject", "from.org + from.name", "body (quoted history removed)", "attachment names", "attachment text"],
    "body_chars": 360,
    "attachment_chars": 220,
    "max_attachments": 2,
    "max_tokens": MAX_LEN,
    "query_prefix": QUERY_PREFIX,
}

_QUOTE_MARKERS = re.compile(
    r"(?m)^(?:-{3,}\s*原始邮件\s*-{3,}|-{3,}\s*Original Message\s*-{3,}|-{3,}\s*转发的邮件\s*-{3,}|>+ ?.*$|在 .{4,40}写道[:：]$)"
)
_WS = re.compile(r"[ \t　]+")
_BLANKS = re.compile(r"\n{2,}")


def strip_quoted(body: str) -> str:
    """Keep only the new text of a reply/forward (drop the quoted history)."""
    m = _QUOTE_MARKERS.search(body or "")
    text = body[: m.start()] if m and m.start() > 0 else (body or "")
    return text


def _squeeze(text: str) -> str:
    text = _WS.sub(" ", text)
    return _BLANKS.sub("\n", text).strip()


def doc_text(mail: dict, recipe: dict | None = None) -> str:
    """Text embedded for one email: subject + sender + new body + attachment names/text."""
    r = recipe or RECIPE
    sender = mail.get("from") or {}
    head = mail.get("subject", "")
    who = " ".join(x for x in (sender.get("org") or "", sender.get("name") or "") if x)
    body = _squeeze(strip_quoted(mail.get("body") or ""))[: r["body_chars"]]
    parts = [head, who, body]
    atts = (mail.get("attachments") or [])[: r["max_attachments"]]
    if atts:
        parts.append("附件：" + "；".join(a.get("name", "") for a in atts))
        for a in atts:
            t = _squeeze(a.get("text") or "")[: r["attachment_chars"]]
            if t:
                parts.append(t)
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- model files
def ensure_model(model_dir: Path = MODEL_DIR) -> Path:
    model_dir = Path(model_dir)
    missing = [f for f in ONNX_FILES if not (model_dir / f).exists()]
    if missing:
        from huggingface_hub import hf_hub_download

        model_dir.mkdir(parents=True, exist_ok=True)
        for f in missing:
            hf_hub_download(ONNX_REPO, f, local_dir=str(model_dir))
    return model_dir


class Embedder:
    """bge-small-zh-v1.5 (CLS pooling, L2-normalised, 512-d) on onnxruntime CPU."""

    def __init__(self, model_dir: str | Path | None = None, max_len: int = MAX_LEN, threads: int | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_dir = ensure_model(Path(model_dir) if model_dir else MODEL_DIR)
        self.max_len = max_len
        self.tok = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.tok.no_padding()
        self.tok.enable_truncation(max_length=max_len)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            opts.intra_op_num_threads = int(threads)
        self.sess = ort.InferenceSession(
            str(self.model_dir / "onnx" / "model.onnx"), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.sess.get_inputs()}

    # -------------------------------------------------------------- core
    def _forward(self, encs) -> np.ndarray:
        n = len(encs)
        width = max(len(e.ids) for e in encs)
        ids = np.zeros((n, width), dtype=np.int64)
        mask = np.zeros((n, width), dtype=np.int64)
        for i, e in enumerate(encs):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.ids)] = 1
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        out = self.sess.run(None, feeds)[0]  # last_hidden_state [n, width, 512]
        cls = out[:, 0, :].astype(np.float32)
        cls /= np.maximum(np.linalg.norm(cls, axis=1, keepdims=True), 1e-12)
        return cls

    def _encode(self, texts: list[str], batch_tokens: int = 16384, progress=None) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        encs = self.tok.encode_batch([t or " " for t in texts])
        order = sorted(range(len(encs)), key=lambda i: len(encs[i].ids))
        out = np.zeros((len(encs), DIM), dtype=np.float32)
        i = 0
        done = 0
        while i < len(order):
            width = len(encs[order[min(len(order) - 1, i + 7)]].ids)
            bs = max(1, min(256, batch_tokens // max(width, 1)))
            idx = order[i : i + bs]
            width = max(len(encs[j].ids) for j in idx)
            bs = max(1, min(bs, batch_tokens // max(width, 1)))
            idx = order[i : i + bs]
            out[idx] = self._forward([encs[j] for j in idx])
            i += len(idx)
            done += len(idx)
            if progress:
                progress(done, len(encs))
        return out

    # -------------------------------------------------------------- public
    def encode_docs(self, texts: list[str], progress=None) -> np.ndarray:
        """float32 [N, 512], L2-normalised."""
        return self._encode(list(texts), progress=progress)

    def encode_query(self, text: str) -> np.ndarray:
        """float32 [512], L2-normalised, with the bge retrieval instruction."""
        return self._encode([QUERY_PREFIX + text])[0]

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode([QUERY_PREFIX + t for t in texts])


def load_embeddings(folder: str | Path | None = None) -> np.ndarray:
    """Memory-map demo/data/embeddings.f32 as float32 [count, 512] (row i = email id i+1)."""
    folder = Path(folder or DATA)
    raw = np.memmap(folder / "embeddings.f32", dtype=np.float32, mode="r")
    return raw.reshape(-1, DIM)


if __name__ == "__main__":  # quick self-test
    import sys
    import time

    e = Embedder()
    t = time.time()
    v = e.encode_docs(["瀚海物流 华东智能调度平台 项目启动会 会议纪要", "星辰银行信用卡电子账单"] * 4)
    q = e.encode_query(sys.argv[1] if len(sys.argv) > 1 else "瀚海物流项目会议纪要")
    print(v.shape, v.dtype, np.round(v[:2] @ q, 4), f"{time.time() - t:.2f}s")
