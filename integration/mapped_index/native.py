"""Optional local OpenSSL/C selector for the mapped profile."""

import ctypes
import os
import sys
from time import perf_counter_ns
import numpy as np
from cryptography.exceptions import InvalidTag


def load_library():
    path = os.environ.get("HPIS_MAPPED_NATIVE")
    if not path:
        return None
    if sys.byteorder != "little":
        raise RuntimeError("the optional native scorer requires a little-endian host")
    fn = ctypes.CDLL(path).hpis_rank
    pointer = ctypes.c_void_p
    fn.argtypes = [
        ctypes.c_int64,
        ctypes.c_int,
        ctypes.c_int,
        pointer,
        pointer,
        pointer,
        pointer,
        ctypes.c_char_p,
        pointer,
        pointer,
        pointer,
        pointer,
        ctypes.c_double,
        ctypes.c_double,
        pointer,
        pointer,
    ]
    fn.restype = ctypes.c_int
    return fn


def rank(fn, image, unit, rotated, k, key):
    secure = image.sealed is not None
    t0 = perf_counter_ns()
    scores = image.sketch @ (rotated if secure else unit)
    t1 = perf_counter_ns()
    result = np.empty(k, dtype="<i8")
    opened = (ctypes.c_int64 * 2)()
    sealed = image.sealed_ptr if secure else None
    controls = image.native_controls
    # HPIC accepts only a complete 256-bit VEK. Never pass a short buffer to OpenSSL.
    if secure and (not isinstance(key, bytes) or len(key) != 32):
        raise PermissionError("256-bit vector key required")
    margin = image.fp_slack if secure else 8 * image.dim * np.finfo(np.float32).eps
    code = fn(
        image.size,
        image.dim,
        k,
        scores.ctypes.data,
        image.native_ids_ptr,
        image.native_data_ptr,
        sealed,
        key,
        unit.ctypes.data,
        *controls,
        image.radius if secure else margin,
        margin,
        result.ctypes.data,
        ctypes.addressof(opened),
    )
    if code:
        raise InvalidTag("invalid vector ciphertext or data")
    image.diagnostic = {
        "gemv_us": (t1 - t0) / 1000,
        "select_us": (perf_counter_ns() - t1) / 1000,
        "exact_evaluations": opened[0],
        "data_alignment": image.native_data_ptr % 64,
        "query_alignment": (rotated if secure else unit).ctypes.data % 64,
    }
    if opened[1]:
        resolved, extra = image.resolve_ties(unit, rotated, k, key)
        return resolved, extra + (opened[0] if secure else 0)
    return result.tolist(), opened[0] if secure else 0
