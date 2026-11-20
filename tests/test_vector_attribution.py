import itertools
import random
import unittest

import numpy as np

from bench.vector_attribution import ARMS, crossover, inv_norms, kernel
from hpis.codec import F32, unit_vectors


class AttributionControls(unittest.TestCase):
    def test_order_balance(self):
        schedule = list(crossover(ARMS, 18, random.Random(12)))
        self.assertEqual(sorted(i for i, _ in schedule), list(range(18)))
        all_orders = set(itertools.permutations(ARMS))
        for block in range(0, 18, 6):
            self.assertEqual(
                set(order for _, order in schedule[block : block + 6]), all_orders
            )

    def test_fp32_tie_and_exact_correction(self):
        # Both dot products round to 1 in FP32; ID 1 is the actual best match.
        data = unit_vectors([[1.0, 2**-14], [1.0, 0.0]], 2)
        q = np.array([1, 0], dtype=F32)
        args = data, inv_norms(data), np.array([0, 1]), q, 1
        self.assertEqual(kernel(*args, "plain_fast")[0], [0])
        self.assertEqual(kernel(*args, "ppe_fast")[0], [0])
        self.assertEqual(kernel(*args, "plain_exact")[0], [1])


if __name__ == "__main__":
    unittest.main()
