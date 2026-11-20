"""Small independent, mathematical boundary fixtures for the mapped search profile."""

import itertools
import importlib.util
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from hpis.codec import F32, context_key, encrypt_item, unit_vectors
from hpis.store import SCHEMA, Store
from integration.mapped_index.index import MappedImage, image_path, locator, publish


class MappedNumerics(unittest.TestCase):
    @contextmanager
    def snapshot(self, values, sketches, beta, ids):
        d = values.shape[1]
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            store = Store(temp / "test.sqlite")
            try:
                store.db.executescript(SCHEMA)
                aid, owner, key = os.urandom(16), os.urandom(32), os.urandom(32)
                tenant = (owner, "encrypted", d, beta)
                store.db.execute(
                    "INSERT INTO tenants VALUES(?,?,?,?,?)", (aid, *tenant)
                )
                cipher = context_key(key)
                rows = [
                    (
                        rid,
                        b"unused",
                        [],
                        sketches[i].tobytes(),
                        encrypt_item(cipher, b"vector", rid, values[i].tobytes()),
                    )
                    for i, rid in enumerate(ids)
                ]
                store.write(aid, "encrypted", d, rows)
                publish(store.db, temp, aid, tenant, 1)
                yield image_path(temp, locator(aid, owner)), key
            finally:
                store.close()

    @staticmethod
    def backends():
        modes = [("python", "", "")]
        if os.getenv("HPIS_MAPPED_NATIVE"):
            modes.append(("native", os.environ["HPIS_MAPPED_NATIVE"], ""))
        if os.getenv("HPIS_MAPPED_Q8"):
            modes.append(("q8", "", os.environ["HPIS_MAPPED_Q8"]))
        return modes

    def _check(self, raw, query, sketches, beta, expected, k, ids=None):
        d = len(query)
        unit = unit_vectors(query, d)
        values = unit_vectors(raw, d)
        ids = list(range(len(values))) if ids is None else ids
        sketches = values if sketches is None else np.asarray(sketches, dtype=F32)
        # Identity rotation and these admissible noise values exercise the
        # worst-case certificate, including vectors close to the cap boundary.
        self.assertLessEqual(
            np.max(np.linalg.norm(values.astype("d") - sketches.astype("d"), axis=1)),
            beta / 4,
        )
        with self.snapshot(values, sketches, beta, ids) as (path, key):
            for name, native, q8 in self.backends():
                with (
                    self.subTest(backend=name),
                    patch.dict(os.environ, {"HPIS_MAPPED_NATIVE": native, "HPIS_MAPPED_Q8": q8}),
                ):
                    image = MappedImage(path)
                    self.assertEqual(image.exact(unit, unit, k, key)[0], expected)

    @unittest.skipUnless(
        importlib.util.find_spec("h5py"),
        "install benchmark dependencies for the independent oracle",
    )
    def test_independent_exhaustive_reference(self):
        from bench.run import oracle
        from hpis.codec import VectorCodec, rotation

        rng = np.random.default_rng(789)
        for d in (2, 3, 9, 33, 100):
            for beta in (0.125, 0.25, 2.0):
                data = rng.standard_normal((57, d))
                data[1] = data[2]
                queries = rng.standard_normal((17, d))
                queries[0], queries[1] = data[1], -data[1]
                stored, target = unit_vectors(data, d), unit_vectors(queries, d)
                matrix = rotation(d)
                sketches = VectorCodec(d, beta, matrix).sketch(stored)
                with self.snapshot(stored, sketches, beta, list(range(len(data)))) as (
                    path,
                    key,
                ):
                    for k in (1, 7, 56):
                        expected = oracle(data, queries, k)
                        for name, native, q8 in self.backends():
                            with (
                                self.subTest(d=d, beta=beta, k=k, backend=name),
                                patch.dict(
                                    os.environ, {"HPIS_MAPPED_NATIVE": native, "HPIS_MAPPED_Q8": q8}
                                ),
                            ):
                                image = MappedImage(path)
                                for row, want in zip(target, expected, strict=True):
                                    self.assertEqual(
                                        image.exact(row, row @ matrix, k, key)[0], want
                                    )

    def test_permutations_and_top_k_boundary(self):
        values = list(itertools.permutations([1.0, 0.5, -0.25, 0.125]))
        self._check(values, [1, 1, 1, 1], None, 0.25, [0, 1, 2, 3], 4)
        self._check(values, [-1, -1, -1, -1], None, 0.25, [0, 1, 2, 3], 4)
        self._check([[1.0, 2**-30], [1.0, 2**-31]], [1.0, 0.0], None, 0.25, [1], 1)

    def test_valid_antiparallel_cap_does_not_discard_winner(self):
        # The competing row is the coarse seed. For row zero the observed
        # centre cosine rounds to -1; widening only the resulting cap bound
        # is too late near the square-root endpoint. Test both noise regimes.
        inputs = [
            (
                0.25,
                [0.7163968682289124, 0.6976930499076843],
                [-0.7588904500007629, -0.6512182950973511],
                [-0.7153046727180481, -0.6960121989250183],
                -0.998028,
            ),
            (
                2.0,
                [0.5815274715423584, -0.8135268092155457],
                [-0.09638514369726181, 0.9953441023826599],
                [-0.5032843351364136, 0.7047747373580933],
                -0.86592,
            ),
        ]
        for beta, q, x, sketch, runner_up_score in inputs:
            direction = np.asarray(q, dtype="d")
            direction /= np.linalg.norm(direction)
            perpendicular = np.array([-direction[1], direction[0]])
            runner_up = (
                runner_up_score * direction
                + np.sqrt(1 - runner_up_score**2) * perpendicular
            )
            competitor_sketch = runner_up + beta / 8 * direction
            self.assertGreater(
                float(np.dot(unit_vectors(x, 2), direction)), runner_up_score
            )
            with self.subTest(beta=beta):
                self._check(
                    [x, runner_up], q, [sketch, competitor_sketch], beta, [0], 1
                )
