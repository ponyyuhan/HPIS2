"""Datasets, synthetic record bodies, and the exhaustive FP64 reference (outside timing)."""

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from hpis.codec import unit_vectors

GLOVE_URL = "https://ann-benchmarks.com/glove-100-angular.hdf5"
GLOVE_FULL_SHAPE = {"train": [1183514, 100], "test": [10000, 100]}

# Neutral business vocabulary for deterministic ~N-byte memo bodies.
_VOCAB = (
    "季度 预算 审批 合同 会议 纪要 项目 进度 风险 客户 需求 交付 采购 发票 报销 出差 培训 "
    "招聘 绩效 数据 安全 合规 审计 归档 检索 加密 授权 终端 云端 服务 版本 测试 上线 运维 "
    "告警 复盘 计划 目标 供应 库存 物流 质检 合作 方案 评审 预案 资产 权限 备份 迁移"
).split()


def _filler():
    rng = random.Random(20261120)
    return "、".join(rng.choice(_VOCAB) for _ in range(6000))


_FILLER = _filler()  # every character is 3 bytes in UTF-8


def content(i, size):
    """Deterministic UTF-8 memo of about ``size`` bytes for record ``i``."""
    head = f"备忘录 {i}：请在下次会议前复核事项 {i % 73}。"
    need = max(0, (size - len(head.encode())) // 3)
    need = min(need, len(_FILLER) - 1)
    off = (i * 7919) % (len(_FILLER) - need)
    return head + _FILLER[off : off + need]


def record(i, vector, size):
    """One record: 1 unique field value, 1 keyword shared by 10 records, vector, body."""
    return {
        "id": i,
        "fields": {"ref": f"item-{i}"},
        "keywords": [f"g{i // 10}"],
        "vector": vector,
        "content": content(i, size),
    }


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Dataset:
    base: np.ndarray
    queries: np.ndarray
    warm: np.ndarray
    info: dict = field(default_factory=dict)


def load(path, rows, queries, warmups, *, query_start=0, dim=None, query_noise=0.0, seed=1):
    """GloVe-style HDF5 (train/test), or a raw float32 matrix (.f32/.npy) with held-out queries."""
    path = Path(path)
    suffix = path.suffix.lower()
    info = {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size}
    if suffix in (".hdf5", ".h5"):
        import h5py

        with h5py.File(path, "r") as f:
            train, test = f["train"], f["test"]
            shape = {"train": list(train.shape), "test": list(test.shape)}
            if not 1 <= rows <= len(train):
                raise ValueError("--rows exceeds the HDF5 train split")
            end = query_start + queries
            if end > len(test):
                raise ValueError("--query-start/--queries exceed the HDF5 test split")
            base = np.asarray(train[:rows], dtype=np.float32)
            q = np.asarray(test[query_start:end], dtype=np.float32)
            # Warm-up vectors: other test rows (never the measured ones), then unused train rows.
            pool = [i for i in range(len(test)) if not query_start <= i < end][:warmups]
            warm = np.asarray(test[pool], dtype=np.float32) if pool else np.empty((0, base.shape[1]), np.float32)
            if len(warm) < warmups and rows < len(train):
                extra = train[rows : rows + warmups - len(warm)]
                warm = np.concatenate([warm, np.asarray(extra, dtype=np.float32)])
            distance = f.attrs.get("distance")
            if isinstance(distance, bytes):
                distance = distance.decode()
        glove = shape == GLOVE_FULL_SHAPE or (
            distance == "angular" and shape["train"][1] == 100 and "glove" in path.name.lower()
        )
        info.update(
            format="ann-benchmarks hdf5",
            name="GloVe-100 angular" if glove else path.stem,
            source=GLOVE_URL if glove else None,
            file_shape=shape,
            distance=distance,
            selection=f"train[0:{rows}] inserted; test[{query_start}:{end}] queried",
        )
        if shape != GLOVE_FULL_SHAPE and glove:
            info["note"] = (
                f"local prefix copy of {GLOVE_URL}: first {shape['train'][0]} train rows "
                f"and first {shape['test'][0]} test rows, byte-identical prefix"
            )
    else:
        if suffix == ".npy":
            matrix = np.load(path, mmap_mode="r")
        else:
            if not dim:
                raise ValueError("raw float32 datasets need --dim")
            matrix = np.memmap(path, dtype=np.float32, mode="r").reshape(-1, dim)
        n = len(matrix)
        held = queries + warmups
        if rows + held > n:
            raise ValueError(f"need rows + queries + warmups <= {n}")
        base = np.array(matrix[:rows], dtype=np.float32)
        q = np.array(matrix[n - held : n - warmups], dtype=np.float32)
        warm = np.array(matrix[n - warmups :], dtype=np.float32)
        if query_noise:
            rng = np.random.default_rng(seed)
            qn = unit_vectors(q, q.shape[1]).astype(np.float64)
            noise = rng.standard_normal(qn.shape)
            noise *= query_noise / np.linalg.norm(noise, axis=1, keepdims=True)
            q = (qn + noise).astype(np.float32)
        info.update(
            format="raw float32 matrix",
            name=path.stem,
            file_shape=[n, matrix.shape[1]],
            distance="cosine",
            selection=(
                f"rows[0:{rows}] inserted; held-out rows[{n - held}:{n - warmups}] queried"
                + (f" with additive noise of norm {query_noise}" if query_noise else "")
            ),
        )
    if not len(base) or base.shape[1] != q.shape[1]:
        raise ValueError("empty dataset or dimension mismatch")
    info.update(rows=int(len(base)), dims=int(base.shape[1]), queries=int(len(q)), warmups=int(len(warm)))
    return Dataset(base, q, warm, info)


def exact_topk(base, queries, k, chunk=64):
    """Exhaustive cosine Top-K in FP64 on the vectors exactly as stored (unit FP32),
    ordered by (score desc, record id asc). Independent of the code under test."""
    dim = base.shape[1]
    stored = unit_vectors(base, dim).astype(np.float64)
    stored /= np.linalg.norm(stored, axis=1, keepdims=True)
    qs = unit_vectors(queries, dim).astype(np.float64)
    qs /= np.linalg.norm(qs, axis=1, keepdims=True)
    out = []
    for s in range(0, len(qs), chunk):
        scores = stored @ qs[s : s + chunk].T
        for j in range(scores.shape[1]):
            d = -scores[:, j]
            kth = np.partition(d, k - 1)[k - 1]
            cand = np.flatnonzero(d <= kth)
            out.append(cand[np.lexsort((cand, d[cand]))][:k].tolist())
    return out
