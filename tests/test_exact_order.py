"""Numerically independent regressions for mathematical, stored-float32 cosine ordering."""

import itertools
import unittest

import numpy as np

from hpis.codec import unit_vectors
from hpis.exact_order import exact_nearest
from hpis.store import cosine_distances


class CanonicalCosineTests(unittest.TestCase):
    def rank(self, rows, q, k, ids=None):
        dim = len(q)
        vectors = unit_vectors(rows, dim)
        unit = unit_vectors(q, dim)
        ids = np.arange(len(vectors)) if ids is None else np.asarray(ids)
        approx = cosine_distances(vectors, unit)
        return ids[exact_nearest(ids, approx, k, unit, vectors)].tolist()

    def test_actual_ties_and_k_boundary(self):
        for row in ((1.0, 0.5, -0.25), (1.0, 0.5, -0.25, 0.125)):
            rows = list(itertools.permutations(row))
            ids = np.arange(len(rows), 0, -1)
            for k in (1, 4, len(rows)):
                with self.subTest(dim=len(row), k=k):
                    expected = list(range(1, k + 1))
                    self.assertEqual(self.rank(rows, [1] * len(row), k, ids), expected)
                    self.assertEqual(self.rank(rows, [-1] * len(row), k, ids), expected)

    def test_sub_ulp_non_tie_and_negative_dots(self):
        rows = [[1, 2.0**-30], [1, 2.0**-31], [-1, 2.0**-31], [-1, 2.0**-30], [0, 1]]
        self.assertEqual(self.rank(rows, [1, 0], 5), [1, 0, 4, 3, 2])
        self.assertEqual(self.rank(rows, [1, 0], 1), [1])

    def test_separated_queries_do_not_open_vectors(self):
        def forbidden(index):
            self.fail(f"spurious exact opening of row {index}")

        self.assertEqual(
            exact_nearest(
                np.array([9, 4, 7]),
                np.array([2.0, 0.0, 4.0]),
                2,
                np.array([1.0, 0.0], dtype=np.float32),
                forbidden,
            ).tolist(),
            [1, 0],
        )

    def test_nonzero_subnormal_squared_lengths(self):
        a = [1.6e-162, 1.6e-162]
        np.testing.assert_array_equal(unit_vectors(a, 2), unit_vectors([1.0, 1.0], 2))
        with np.errstate(over="ignore", under="ignore"):
            both = unit_vectors([a, [1e308, 1e308]], 2)
        np.testing.assert_array_equal(both, unit_vectors([[1.0, 1.0], [1.0, 1.0]], 2))
