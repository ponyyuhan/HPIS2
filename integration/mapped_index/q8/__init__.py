"""Binding for the optional Q8 filter and authenticated native exact scorer."""

import ctypes
from dataclasses import dataclass
from functools import lru_cache
import sys
from time import perf_counter_ns
import weakref

from cryptography.exceptions import InvalidTag
import numpy as np


@lru_cache(maxsize=4)
def load_kernel(path):
    if sys.byteorder != "little":
        raise RuntimeError("the native mapped index requires little-endian storage")
    lib = ctypes.CDLL(str(path))
    ptr = ctypes.c_void_p
    lib.coarse_prepare.argtypes = [ptr, ctypes.c_int, ctypes.c_int]
    lib.coarse_prepare.restype = ptr
    lib.coarse_free.argtypes = [ptr]
    lib.coarse_free.restype = None
    lib.coarse_approx.argtypes = [ptr, ptr, ptr]
    lib.coarse_approx.restype = ctypes.c_double
    lib.hpis_rank_q8.argtypes = [
        ctypes.c_int64,
        ctypes.c_int,
        ctypes.c_int,
        ptr,
        ptr,
        ptr,
        ptr,
        ctypes.c_char_p,
        ptr,
        ptr,
        ptr,
        ptr,
        ctypes.c_double,
        ctypes.c_double,
        ptr,
        ptr,
        ptr,
        ptr,
    ]
    lib.hpis_rank_q8.restype = ctypes.c_int
    return lib


@dataclass
class PreparedIndex:
    pointer: int
    array_bytes: int


def prepare(kernel, image):
    """Build once per mapped revision; the retained object owns the native allocation."""
    cached = image.__dict__.get("_prepared_q8")
    if cached is not None:
        return cached, 0.0
    if image.size > np.iinfo(np.int32).max:
        raise ValueError("the native Q8 index supports at most INT_MAX rows")
    start = perf_counter_ns()
    pointer = kernel.coarse_prepare(image.native_data_ptr, image.size, image.dim)
    if not pointer:
        raise ValueError("could not allocate or quantize the mapped vector index")
    padded_rows = (image.size + 31) // 32 * 32
    padded_dim = (image.dim + 3) // 4 * 4
    item = PreparedIndex(pointer, padded_rows * padded_dim + image.size * 4)
    weakref.finalize(item, kernel.coarse_free, pointer)
    # If two local callers prepare the same fresh image concurrently, the extra
    # object releases its own allocation and both callers use the retained image.
    winner = image.__dict__.setdefault("_prepared_q8", item)
    return winner, (perf_counter_ns() - start) / 1000


def rank(kernel, image, unit, rotated, k, key):
    secure = image.sealed is not None
    if secure and (not isinstance(key, bytes) or len(key) != 32):
        raise PermissionError("256-bit vector key required")
    unit = np.asarray(unit, dtype="<f4", order="C")
    projected = np.asarray(rotated, dtype="<f4", order="C") if secure else unit
    if unit.shape != (image.dim,) or projected.shape != (image.dim,):
        raise ValueError("unexpected exact-ranking query shape")
    prepared, cold_us = prepare(kernel, image)
    start = perf_counter_ns()
    scores = np.empty(image.size, dtype="<f4")
    result = np.empty(k, dtype="<i8")
    opened = (ctypes.c_int64 * 2)()
    slack = image.fp_slack if secure else 8 * image.dim * np.finfo(np.float32).eps
    status = kernel.hpis_rank_q8(
        image.size,
        image.dim,
        k,
        scores.ctypes.data,
        image.native_ids_ptr,
        image.native_data_ptr,
        image.sealed_ptr if secure else None,
        key,
        unit.ctypes.data,
        *image.native_controls,
        image.radius if secure else slack,
        slack,
        result.ctypes.data,
        ctypes.addressof(opened),
        prepared.pointer,
        projected.ctypes.data,
    )
    if status:
        raise InvalidTag("invalid vector ciphertext, index or query")
    image.diagnostic = {
        "rank_kernel": "q8",
        "q8_prepare_us": cold_us,
        "q8_index_array_bytes": prepared.array_bytes,
        # The Q8 GEMV and selective FP32 pass share one native call.
        "gemv_us": 0.0,
        "select_us": (perf_counter_ns() - start) / 1000,
        "exact_evaluations": opened[0],
        "data_alignment": image.native_data_ptr % 64,
        "query_alignment": projected.ctypes.data % 64,
    }
    if opened[1]:
        resolved, extra = image.resolve_ties(unit, projected, k, key)
        return resolved, extra + (opened[0] if secure else 0)
    return result.tolist(), opened[0] if secure else 0
