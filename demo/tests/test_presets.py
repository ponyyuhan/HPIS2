"""The preset questions the page shows: their data, and the group (scene) a preset may name.

Run:  .venv-demo/bin/python -m unittest demo.tests.test_presets -v
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from demo import server

DATA = Path(__file__).resolve().parents[1] / "data"


class PresetTest(unittest.TestCase):
    def test_scene_reaches_the_page(self):
        mailbox = SimpleNamespace(
            presets=[
                {"id": "minutes", "chip": "会议总结", "question": "q1"},
                {"id": "checkup", "chip": "体检对比", "question": "q2", "scene": "health"},
            ]
        )
        rows = server.preset_rows({"mailbox": mailbox})
        self.assertNotIn("scene", rows[0])  # shown under its kind's own group
        self.assertEqual(rows[1]["scene"], "health")
        self.assertEqual((rows[1]["kind"], rows[1]["device"]), ("mail", "pc"))

    def test_the_demo_presets(self):
        presets = json.loads((DATA / "presets.json").read_text("utf-8"))
        ids = [p["id"] for p in presets]
        self.assertNotIn("contract", ids)  # 合同条款 is no longer a preset (its emails stay in the mailbox)
        self.assertEqual(next(p["chip"] for p in presets if p["id"] == "minutes"), "会议总结")
        self.assertEqual(next(p.get("scene") for p in presets if p["id"] == "checkup"), "health")


if __name__ == "__main__":
    unittest.main()
