"""Independent integer-dot and error-bound checks for an enabled native Q8 library."""

import os
import unittest

import numpy as np

from . import load_kernel


@unittest.skipUnless(
    os.getenv("HPIS_MAPPED_Q8"), "set HPIS_MAPPED_Q8 to a compiled library"
)
class QuantizedBounds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = load_kernel(os.environ["HPIS_MAPPED_Q8"])

    def test_integer_kernel_and_roundoff_bound(self):
        rng = np.random.default_rng(618)
        # Widths on and off the four-coordinate packing boundary and a tail
        # block of rows expose both SIMD layout mistakes and partial loads.
        for dimension in [1, 2, 3, 4, 31, 63, 100, 103, 511, 4096]:
            with self.subTest(dimension=dimension):
                rows = rng.uniform(-1, 1, (35, dimension)).astype("<f4")
                rows[0], rows[1], rows[2] = 1, -1, 0
                rows[3, ::2], rows[3, 1::2] = 1, -1
                rows[4] *= 1e-25
                rows[5] = 2.0**-145
                rows[6, 1:] *= 1e-7
                row_scale = (np.max(np.abs(rows), axis=1).astype("d") / 127).astype(
                    "<f4"
                )
                denom = np.where(row_scale == 0, 1, row_scale).astype("d")
                xq = np.clip(
                    np.rint(rows.astype("d") / denom[:, None]), -127, 127
                ).astype("i8")
                xq[row_scale == 0] = 0
                handle = self.lib.coarse_prepare(rows.ctypes.data, *rows.shape)
                self.assertTrue(handle)
                try:
                    queries = [
                        np.ones(dimension, dtype="<f4"),
                        -np.ones(dimension, dtype="<f4"),
                        rows[3],
                        rows[4],
                        rows[5],
                        np.ascontiguousarray(rng.normal(size=dimension), dtype="<f4"),
                    ]
                    for query in queries:
                        scale = np.float32(np.max(np.abs(query)).astype("d") / 63)
                        qi = (
                            np.clip(np.rint(query.astype("d") / scale), -63, 63).astype(
                                "i8"
                            )
                            if scale
                            else np.zeros(dimension, dtype="i8")
                        )
                        integer = xq @ qi
                        expected = integer.astype("<f4") * row_scale * scale
                        raw = np.empty(len(rows), dtype="<f4")
                        bound = self.lib.coarse_approx(
                            handle, query.ctypes.data, raw.ctypes.data
                        )
                        self.assertGreaterEqual(bound, 0)
                        np.testing.assert_array_equal(raw, expected)
                        actual = rows.astype("d") @ query.astype("d")
                        self.assertTrue(np.all(np.abs(actual - raw) <= bound))
                    bad = np.zeros(dimension, dtype="<f4")
                    self.assertLess(
                        self.lib.coarse_approx(
                            handle, bad.ctypes.data, raw.ctypes.data
                        ),
                        0,
                    )
                    bad[0] = np.nan
                    self.assertLess(
                        self.lib.coarse_approx(
                            handle, bad.ctypes.data, raw.ctypes.data
                        ),
                        0,
                    )
                finally:
                    self.lib.coarse_free(handle)
