"""Routing, time windows, places and relaxation of the on-device planner.

Uses the fixture devices (demo/tests/fixtures.py) and a small mailbox; no services.

    .venv-demo/bin/python -m unittest demo.tests.test_planner -v
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from demo.tests.fixtures import make_devices

BIBLE = {
    "family": [{"name": "苏晴"}, {"name": "林小禾"}],
    "colleagues": [{"name": "韩振东"}, {"name": "姚佳"}],
    "orgs": [{"name": "瀚海物流"}, {"name": "星河数据"}],
    "services": [{"name": "星辰银行"}, {"name": "育才实验小学"}],
}
MAIL_PRESETS = [
    {"id": "card", "chip": "信用卡账单", "question": "我的星辰银行信用卡上个月账单一共多少钱？最大的三笔消费是什么？",
     "plan": {"orgs": ["星辰银行"], "people": [], "categories": ["finance"], "date_from": "2026-11-01",
              "date_to": "2026-11-20", "semantic": "星辰银行 信用卡 账单"}, "gold": {}},
]


def make_mail(folder: Path):
    tz = dt.timezone(dt.timedelta(hours=8))
    rows = []
    specs = [("星辰银行", "finance", "信用卡电子账单"), ("育才实验小学", "school", "家长会通知"), ("瀚海物流", "client", "项目风险"),
             ("星河数据", "work", "合同付款"), ("云购商城", "promotion", "双十一优惠")]
    start = dt.datetime(2024, 1, 3, 9, tzinfo=tz)
    for i in range(400):
        org, cat, subject = specs[i % len(specs)]
        date = start + dt.timedelta(hours=63 * i)  # 2024-01-03 .. 2026-11-15
        people = [{"name": "林小禾"}] if cat == "school" else []
        rows.append({"id": i + 1, "date": date.isoformat(), "category": cat, "subject": f"{subject} {i}",
                     "from": {"name": "联系人", "email": "x@y.cn", "org": org}, "to": [], "cc": [],
                     "body": f"{org}{subject}", "attachments": [], "facets": {"org": org, "people": people}})
    with open(folder / "mailbox.jsonl", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (folder / "bible.json").write_text(json.dumps(BIBLE, ensure_ascii=False))
    (folder / "presets.json").write_text(json.dumps(MAIL_PRESETS, ensure_ascii=False))


class PlannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "data").mkdir()
        make_mail(root / "data")
        make_devices(root / "data" / "devices", dim=16)
        cls.env = {k: os.environ.get(k) for k in ("HPIS_DEMO_DATA", "HPIS_DEMO_RUNTIME", "HPIS_DEMO_DEVICES")}
        os.environ["HPIS_DEMO_DATA"] = str(root / "data")
        os.environ["HPIS_DEMO_RUNTIME"] = str(root / "runtime")
        os.environ.pop("HPIS_DEMO_DEVICES", None)
        from demo import config

        importlib.reload(config)
        from demo import devices, mailbox, planner

        cls.planner = planner
        cls.catalog = devices.Catalog(mailbox.Mailbox())

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def plan(self, question: str, relax: bool = True) -> dict:
        p = self.planner.plan_question(question, self.catalog.presets(), self.catalog.mailbox.bible, catalog=self.catalog)
        if relax:
            p = self.planner.relax(p, lambda x: self.catalog.matching(p["kind"], x))
        return p

    def check(self, question: str, kind: str, **expect):
        p = self.plan(question)
        self.assertEqual(p["kind"], kind, f"{question}: {p}")
        self.assertEqual(p["device"], {"mail": "pc", "photo": "phone", "sport": "watch", "car": "car"}[kind])
        for key, value in expect.items():
            if key in ("date_from", "date_to"):
                self.assertEqual((p.get(key) or "")[:10] or None, value, f"{question}: {key} {p.get(key)}")
            else:
                self.assertEqual(p.get(key), value, f"{question}: {key} {p.get(key)}")
        self.assertGreater(p["matched"], 0, f"{question}: nothing matched {p}")
        return p

    # ------------------------------------------------------------------ photos
    def test_photo_routing(self):
        self.check("整理我在德国出差时拍的照片", "photo", countries=["德国"], preset="photo_germany")
        self.check("给我 2024 年去韩国旅游的照片", "photo", countries=["韩国"], date_from="2024-01-01", preset="photo_korea")
        self.check("我在慕尼黑拍的照片", "photo", cities=["慕尼黑"], countries=["德国"])
        self.check("去年九月在柏林拍了哪些照片", "photo", cities=["柏林"], date_from="2025-09-01", date_to="2025-09-30")
        self.check("济州岛的风景照", "photo", cities=["济州"], types=["风景"])
        self.check("前年在首尔吃了什么好吃的，有照片吗", "photo", cities=["首尔"], types=["美食"], date_from="2024-01-01")
        self.check("国庆去杭州拍的照片", "photo", cities=["杭州"], date_from="2026-10-01", date_to="2026-10-07")
        self.check("看看京都的照片", "photo", cities=["京都"], countries=["日本"])
        self.check("相册里有没有夜景", "photo", types=["夜景"])
        self.check("2025年4月的照片", "photo", date_from="2025-04-01", date_to="2025-04-30")
        self.check("韩国的照片", "photo", countries=["韩国"])
        self.check("孩子运动会的照片", "photo")
        self.check("法兰克福展会的照片", "photo", cities=["法兰克福"], types=["会议"])
        self.check("在釜山拍过海边的照片吗", "photo", cities=["釜山"])

    def test_photo_relaxation(self):
        p = self.plan("我在巴黎拍的照片")
        self.assertEqual(p["kind"], "photo")
        self.assertEqual(p["relaxed"], ["cities", "countries"])
        self.assertEqual(p["cities"], [])
        self.assertGreater(p["matched"], 0)
        self.assertIn("巴黎", self.planner.relax_notice(p))
        self.assertIn("没有找到", self.planner.model_note(p))
        p = self.plan("去年在巴黎拍的夜景照片")
        self.assertEqual(p["relaxed"], ["cities", "countries"])
        self.assertEqual(p["types"], ["夜景"])
        self.assertEqual(p["date_from"], "2025-01-01")
        p = self.plan("2023年在慕尼黑拍的照片")  # the city exists, the year does not: only time goes
        self.assertEqual(p["relaxed"], ["time"])
        self.assertIsNone(p["date_from"])
        self.assertEqual(p["cities"], ["慕尼黑"])
        p = self.plan("上个月在慕尼黑拍的照片")  # a named place outweighs the time: Munich photos, not last month's
        self.assertEqual(p["relaxed"], ["time"])
        self.assertEqual(p["cities"], ["慕尼黑"])
        self.assertGreater(p["matched"], 0)

    def test_free_multi_question_follows_the_trip(self):
        """A typed multi-device question that names a place: every device searches that trip's days,
        whether the asked time missed the trip (「上个月去慕尼黑」) or no time was asked (「那几天」)."""
        from demo.engine import Engine

        eng = Engine.__new__(Engine)
        eng.catalog, eng.mailbox = self.catalog, self.catalog.mailbox
        idx = self.catalog.index("photo")
        munich = sorted({d for d in idx.days[idx.cols["city"] == "慕尼黑"].tolist() if d})
        for question, missed in (("整理我上个月去慕尼黑的照片、运动和用车记录", True),
                                 ("我去慕尼黑那几天的照片、运动和用车记录", False)):
            mp = eng.plan_multi(question)
            photo = mp["plans"]["phone"]
            self.assertEqual(photo["cities"], ["慕尼黑"], question)
            self.assertGreater(photo["matched"], 0, question)
            note = photo["anchor_note"]
            self.assertIn(munich[0], note)
            self.assertEqual(note.startswith("问题所说的时间里没有「慕尼黑」"), missed, note)
            self.assertEqual("回答开头" in self.planner.model_note(photo), missed, question)
            for device, plan in mp["plans"].items():  # the trip's days stay even where they hold nothing
                self.assertEqual(plan["anchor_note"], note, device)
                self.assertNotIn("time", plan["relaxed"], device)
                self.assertLessEqual(plan["date_from"][:10], munich[0], device)
                self.assertGreaterEqual(plan["date_to"][:10], munich[-1], device)
        same = eng.plan_multi("整理我最近一周的照片、运动和用车记录")  # no place named: nothing moves
        self.assertFalse(any(p.get("anchor_note") for p in same["plans"].values()))

    def test_relax_can_keep_a_filter(self):
        plan = self.planner.finalize({"kind": "sport", "activities": ["跑步"], "date_from": "2025-09-13",
                                      "date_to": "2025-09-16", "semantic": "跑步"})
        out = self.planner.relax(plan, lambda p: 0 if p.get("date_from") else 5, keep=("time",))
        self.assertEqual((out["matched"], out["relaxed"], out["activities"]), (0, [], ["跑步"]))
        self.assertEqual(out["date_from"][:10], "2025-09-13")
        out = self.planner.relax(plan, lambda p: 0 if p.get("activities") else 5, keep=("time",))
        self.assertEqual((out["matched"], out["relaxed"]), (5, ["activities"]))

    def test_photo_presets_keep_their_plan(self):
        p = self.plan("给我2024年去韩国旅游的照片")  # spacing differs from the preset
        self.assertEqual(p.get("preset"), "photo_korea")
        self.assertEqual(p["relaxed"], [])

    # ------------------------------------------------------------------ watch
    def test_sport_routing(self):
        self.check("我最近一个月的运动情况怎么样？", "sport", preset="sport_month")
        self.check("上周跑了几次步", "sport", activities=["跑步", "周报"], date_from="2026-11-09", date_to="2026-11-15")
        self.check("这个月睡眠怎么样", "sport", activities=["睡眠", "月报"], date_from="2026-11-01")
        self.check("最近骑行了多少公里", "sport", activities=["骑行", "月报"], date_from="2026-10-20")
        self.check("今年游泳了几次", "sport", activities=["游泳", "月报"], date_from="2026-01-01")
        self.check("我的静息心率是多少", "sport")
        self.check("上个月去健身房做了几次力量训练", "sport", date_from="2026-10-01", date_to="2026-10-31")
        self.check("最近一周的步数", "sport", activities=["日常"], date_from="2026-11-13")
        self.check("最近打羽毛球了吗", "sport", activities=["羽毛球"])
        self.check("今年半马成绩怎么样", "sport", date_from="2026-01-01")
        # a plain month-scale overview is answered by the monthly reports, a week-scale one by the weekly ones
        self.check("最近一个月运动得怎么样", "sport", activities=["月报"], date_from="2026-10-20")
        self.check("上周的运动情况", "sport", activities=["周报"], date_from="2026-11-09", date_to="2026-11-15")
        self.check("我的全马成绩", "sport")

    def test_sport_relaxation(self):
        p = self.plan("2023年跑了几次步")
        self.assertEqual(p["kind"], "sport")
        self.assertEqual(p["relaxed"], ["time"])
        self.assertIn("跑步", p["activities"])
        p = self.plan("今年打了几次网球的运动记录")  # no tennis at all: nothing to drop but time
        self.assertEqual(p["kind"], "sport")
        self.assertGreater(p["matched"], 0)

    # ------------------------------------------------------------------ car
    def test_car_routing(self):
        self.check("上个月我开车去过哪些地方，充了几次电？", "car", preset="car_month")
        self.check("这个月充了几次电", "car", events=["充电", "月报"], date_from="2026-11-01")
        self.check("最近一次保养是什么时候", "car", events=["保养"])
        self.check("我开车去过天津吗", "car", cities=["天津"])
        self.check("上周开车通勤一共开了多少公里", "car", date_from="2026-11-09", date_to="2026-11-15")
        self.check("胎压提醒", "car", events=["提醒"])
        self.check("导航去机场的记录", "car", events=["导航"])
        # a visit to a domestic (or unnamed) place is the car's log; abroad is the photo album
        self.check("上个月去过北戴河吗", "car", date_from="2026-10-01", date_to="2026-10-31")
        self.check("我上个月去过哪些地方", "car", date_from="2026-10-01")
        self.check("上个月去故宫了吗", "car")
        self.check("去年去过日本吗", "photo")
        self.check("我去过哪些国家", "photo")
        self.check("上周去了几次健身房", "sport")
        self.check("老师说孩子去了哪里", "mail")

    def test_car_relaxation(self):
        p = self.plan("去北戴河的那次自驾")  # the records file the trip under 秦皇岛
        self.assertEqual(p["kind"], "car")
        self.assertIn("cities", p["relaxed"])
        self.assertGreater(p["matched"], 0)

    # ------------------------------------------------------------------ mail
    def test_mail_routing(self):
        self.check("我的星辰银行信用卡上个月账单一共多少钱？最大的三笔消费是什么？", "mail", preset="card")
        p = self.check("帮我总结一下最近和客户沟通中提到的风险", "mail", categories=["client"], date_from="2026-10-20")
        self.assertFalse(p.get("unknown"))
        self.check("林小禾学校最近有什么通知", "mail", people=["林小禾"], categories=["school"])
        self.check("我信用卡上个月花了多少钱", "mail", categories=["finance"], date_from="2026-10-01", date_to="2026-10-31")
        self.check("帮我找一下关于预算的邮件", "mail", date_from=None)
        self.check("瀚海物流最近的邮件", "mail", orgs=["瀚海物流"])
        self.check("韩国旅游花了多少钱", "mail")

    def test_unknown_questions_are_a_mail_vector_search(self):
        p = self.check("宇宙的尽头是什么", "mail", date_from=None, orgs=[], categories=[], people=[])
        self.assertTrue(p.get("unknown"))
        self.assertFalse(self.planner.has_filters(p))
        p = self.plan("今天天气怎么样", relax=False)
        self.assertEqual(p["kind"], "mail")
        self.assertTrue(p.get("unknown"))
        self.assertEqual(p["date_from"], "2026-11-20")  # a period the user named is kept …
        p = self.plan("今天天气怎么样")
        self.assertEqual(p["relaxed"], ["time"])  # … unless nothing was received that day
        p = self.plan("最近有什么新鲜事", relax=False)  # the vague "最近" is not a filter for an unknown question
        self.assertIsNone(p["date_from"])

    def test_mail_relaxation(self):
        p = self.plan("林小禾2023年学校的通知")  # mail starts in 2024: only the year is dropped
        self.assertEqual(p["kind"], "mail")
        self.assertEqual(p["relaxed"], ["time"])
        self.assertEqual(p["people"], ["林小禾"])
        self.assertEqual(p["categories"], ["school"])
        self.assertGreater(p["matched"], 0)
        p = self.plan("苏晴发来的学校通知")  # nobody called 苏晴 in the school mails
        self.assertEqual(p["relaxed"], ["people"])

    # ------------------------------------------------------------------ windows
    def test_time_windows(self):
        today = dt.date(2026, 11, 20)
        cases = {
            "今年": ("2026-01-01", "2026-11-20"),
            "去年": ("2025-01-01", "2025-12-31"),
            "前年": ("2024-01-01", "2024-12-31"),
            "上个月": ("2026-10-01", "2026-10-31"),
            "最近一个月": ("2026-10-20", "2026-11-20"),
            "上周": ("2026-11-09", "2026-11-15"),
            "本周": ("2026-11-16", "2026-11-20"),
            "这个星期": ("2026-11-16", "2026-11-20"),
            "过去三个月": ("2026-08-19", "2026-11-20"),
            "近半年": ("2026-05-21", "2026-11-20"),
            "九月": ("2026-09-01", "2026-09-30"),
            "十二月份": ("2025-12-01", "2025-12-31"),
            "2025年3月": ("2025-03-01", "2025-03-31"),
            "去年九月": ("2025-09-01", "2025-09-30"),
            "去年下半年": ("2025-07-01", "2025-12-31"),
            "今年上半年": ("2026-01-01", "2026-06-30"),
            "国庆": ("2026-10-01", "2026-10-07"),
            "昨天": ("2026-11-19", "2026-11-19"),
            "上周末": ("2026-11-14", "2026-11-15"),
            "2024年": ("2024-01-01", "2024-12-31"),
            "最近7天": ("2026-11-13", "2026-11-20"),
        }
        for text, (start, end) in cases.items():
            s, e = self.planner.time_window(f"我{text}的记录", today)
            self.assertEqual((s.isoformat(), e.isoformat()), (start, end), text)
        self.assertEqual(self.planner.time_window("三个月的总结", today), (None, None))
        self.assertEqual(self.planner.time_rule("最近有什么通知", today)[2], "vague")
        self.assertEqual(self.planner.time_rule("最近一个月的通知", today)[2], "explicit")

    def test_date_cover_is_exact(self):
        d = dt.date.fromisoformat
        cover = self.planner.date_cover
        self.assertEqual(cover(d("2026-11-09"), d("2026-11-15")), [["day", f"2026-11-{i:02d}"] for i in range(9, 16)])
        self.assertEqual(cover(d("2026-10-20"), d("2026-11-20")),
                         [["day", f"2026-10-{i}"] for i in range(20, 32)] + [["month", "2026-11"]])
        self.assertEqual(cover(d("2026-01-01"), d("2026-11-20")), [["year", "2026"]])  # nothing after today
        self.assertEqual(cover(d("2025-07-01"), d("2025-12-31")), [["quarter", "2025Q3"], ["quarter", "2025Q4"]])
        self.assertEqual(cover(d("2025-09-01"), d("2025-09-30")), [["month", "2025-09"]])
        self.assertEqual(cover(d("2026-11-20"), d("2026-11-20")), [["day", "2026-11-20"]])
        today = dt.date(2026, 11, 20)

        def days_of(labels):
            out = set()
            day = dt.date(2023, 1, 1)
            while day <= today:
                tags = {("year", f"{day.year}"), ("quarter", f"{day.year}Q{(day.month - 1) // 3 + 1}"),
                        ("month", day.strftime("%Y-%m")), ("day", day.isoformat())}
                if tags & {tuple(x) for x in labels}:
                    out.add(day)
                day += dt.timedelta(days=1)
            return out

        import random

        rng = random.Random(5)
        for _ in range(200):
            a = dt.date(2024, 1, 1) + dt.timedelta(days=rng.randrange(1060))
            b = a + dt.timedelta(days=rng.randrange(400))
            want = {a + dt.timedelta(days=i) for i in range((min(b, today) - a).days + 1)} if a <= today else set()
            labels = cover(a, b)
            self.assertEqual(days_of(labels), want, (a, b))
            self.assertLessEqual(len(labels), 70)

    def test_places(self):
        self.assertEqual(self.planner.places("慕尼黑和柏林"), (["德国"], ["慕尼黑", "柏林"]))
        self.assertEqual(self.planner.places("南韩"), (["韩国"], []))
        self.assertEqual(self.planner.places("济州岛"), (["韩国"], ["济州"]))
        self.assertEqual(self.planner.places("在京都和东京"), (["日本"], ["京都", "东京"]))

    def test_mail_only_without_device_data(self):
        p = self.planner.plan_question("整理我在德国出差时拍的照片", [], BIBLE)  # no catalog: mail only
        self.assertEqual(p["kind"], "mail")
        self.assertEqual(p["device"], "pc")

    def test_describe_chips(self):
        p = self.plan("我在慕尼黑拍的照片")
        chips = {c["kind"]: c["value"] for c in self.planner.describe(p)}
        self.assertEqual(chips["数据"], "手机 · 照片")
        self.assertEqual(chips["城市"], "慕尼黑")
        mail = self.plan("我信用卡上个月花了多少钱")
        self.assertNotIn("数据", {c["kind"] for c in self.planner.describe(mail)})


class LexicalTest(unittest.TestCase):
    """The ordinary cloud's shortlist: content words only, BM25 order, newest first on ties."""

    def test_terms_skip_question_filler(self):
        from demo.retrieval import query_terms

        terms = query_terms("帮我总结一下最近和客户沟通中提到的风险")
        self.assertIn("风险", terms)
        self.assertIn("沟通", terms)
        for junk in ("帮我", "最近", "近和", "中提", "提到", "总结"):
            self.assertNotIn(junk, terms)
        self.assertEqual(query_terms("关于预算的邮件"), ["预算"])
        self.assertIn("sdk", query_terms("天枢 SDK 2.0 交付"))

    def test_order(self):
        from demo.retrieval import lexical_order

        texts = ["季度预算 预算 调整", "周末聚餐", "预算", "部门预算初稿 " + "说明" * 80, "周末聚餐"]
        order = lexical_order(texts, "预算", 3)
        self.assertEqual(set(order), {0, 2, 3})
        self.assertEqual(order[-1], 3)  # the long text with one mention ranks last
        self.assertEqual(lexical_order(texts, "天气", 2), [4, 3])  # nothing matches: newest first
        self.assertEqual(lexical_order([], "预算", 5), [])


if __name__ == "__main__":
    unittest.main()
