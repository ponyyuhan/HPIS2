"""The ordinary cloud embeds device records from exactly the text the device data was embedded from.

If the two drift apart, the cloud's on-the-fly vectors stop matching the stored ones and the two
lanes can pick different evidence for the same question.
"""

from __future__ import annotations

import json
import unittest

from demo import config, records


class DeviceTextTests(unittest.TestCase):
    def test_cloud_text_matches_the_data_builder(self):
        try:
            from demo.corpus.devices.embed import embed_text
        except ImportError as exc:  # the generator package is optional at runtime
            self.skipTest(f"data builder not importable: {exc}")
        checked = 0
        for kind in ("photo", "sport", "car"):
            path = config.DEVICES / f"{kind}.jsonl"
            if not path.exists():
                continue
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    self.assertEqual(records.doc_text(kind, rec), embed_text(rec), f"{kind} #{rec['id']}")
                    checked += 1
        if not checked:
            self.skipTest("no device data in demo/data/devices/")


if __name__ == "__main__":
    unittest.main()
