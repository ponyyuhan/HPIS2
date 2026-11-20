import unittest
from copy import deepcopy

from showcase import corpus


def answer(question, count=64):
    plan = corpus.plan_query(question)
    hits = [m for m in corpus.iter_messages(count) if corpus.matches_plan(m, plan)]
    return corpus.make_answer(question, [corpus.message_content(m) for m in hits], plan)


class ShowcaseCorpusTests(unittest.TestCase):
    def test_presets_have_distinct_actual_evidence(self):
        for preset in corpus.PRESETS:
            with self.subTest(preset=preset["id"]):
                result = answer(preset["question"])
                self.assertTrue(result["matched"])
                self.assertEqual(result["sourceIds"], preset["expectedIds"])
                for section in result["sections"]:
                    for claim in section["quotes"]:
                        mail = corpus.message_for_id(claim["id"])
                        if claim["attachment"]:
                            source = next(
                                a["content"]
                                for a in mail["attachments"]
                                if a["name"] == claim["attachment"]
                            )
                        else:
                            source = mail["body"]
                        self.assertIn(claim["text"], source)

    def test_reader_follows_changed_attachments_and_missing_evidence(self):
        question = "星澜项目什么时候上线？"
        plan = corpus.plan_query(question)
        mail = deepcopy(corpus.message_for_id(2))
        mail["attachments"][0]["content"] = mail["attachments"][0]["content"].replace(
            "12 月 15 日", "12 月 18 日"
        )
        result = corpus.make_answer(question, [mail], plan)
        self.assertIn("12 月 18 日", result["sections"][0]["text"])
        mail["attachments"] = []
        self.assertFalse(corpus.make_answer(question, [mail], plan)["matched"])

    def test_unanswerable_and_historical_context(self):
        for q in (
            "火星计划什么时间上线？",
            "北京出差住哪？",
            "星澜项目的 CTO 是谁？",
            "杭州酒店每晚多少钱？",
            "2023 年星澜上线了吗？",
        ):
            self.assertFalse(answer(q)["matched"], q)
        self.assertEqual(answer("星澜原定的上线时间是什么？")["sourceIds"], [4])
        self.assertEqual(
            answer("星澜最初和最终上线时间分别是什么？")["sourceIds"], [4, 2]
        )
        self.assertEqual(answer("只问上线日期。")["sourceIds"], [2])
        self.assertEqual(answer("白名单还有什么待解问题？")["sourceIds"], [3])
        self.assertEqual(answer("看一下邮件 FY-000021。")["sourceIds"], [21])

    def test_mailbox_record_access_is_reproducible(self):
        rows = list(corpus.iter_messages(40))
        self.assertEqual([r["id"] for r in rows], list(range(1, 41)))
        self.assertEqual(rows[26], corpus.message_for_id(27))
        self.assertEqual(corpus.message_for_id(100_000)["fields"]["编号"], "FY-100000")
        self.assertTrue(rows[26]["attachments"][0]["content"])
        self.assertNotIn("keywords", corpus.message_content(rows[26]))
