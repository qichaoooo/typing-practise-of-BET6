#!/usr/bin/env python3
"""复习状态机与检测池的单元测试（标准库 unittest）。

运行：
    python3 -I scripts/test_review.py
"""
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vocabtype  # noqa: E402


def sense(pos, cn, pinyin):
    tokens = []
    for ch, py in zip(cn, pinyin.split()):
        tokens.append([ch, py])
    return {"pos": pos, "cn": cn, "tokens": tokens}


def entry(word, level="cet6", senses=None):
    return {"word": word, "level": level,
            "senses": senses or [sense("n", "测试", "ce shi")]}


class IntervalTest(unittest.TestCase):
    def setUp(self):
        self.intervals = (1, 3, 7, 15, 30)
        self.day = date(2026, 11, 17)

    def test_learning_correct_enters_l1(self):
        stats = {}
        rec = vocabtype.mark_review_result(
            stats, "w", True, self.intervals, day=self.day)
        self.assertEqual(rec["state"], "known")
        self.assertEqual(rec["level"], 1)
        self.assertEqual(rec["due"], "2026-11-18")

    def test_learning_wrong_stays_learning(self):
        stats = {}
        rec = vocabtype.mark_review_result(
            stats, "w", False, self.intervals, day=self.day)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["level"], 0)
        self.assertEqual(rec["wrong"], 1)

    def test_known_level_up_uses_next_interval(self):
        stats = {}
        vocabtype.mark_review_result(stats, "w", True, self.intervals, day=self.day)
        rec = vocabtype.mark_review_result(
            stats, "w", True, self.intervals, day=self.day)
        self.assertEqual(rec["level"], 2)
        self.assertEqual(rec["due"], "2026-11-20")  # 17 + 3

    def test_known_wrong_returns_to_learning(self):
        stats = {}
        vocabtype.mark_review_result(stats, "w", True, self.intervals, day=self.day)
        rec = vocabtype.mark_review_result(
            stats, "w", False, self.intervals, day=self.day)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["level"], 0)
        self.assertEqual(rec["wrong"], 1)

    def test_max_level_stays_max(self):
        stats = {}
        for _ in range(10):
            rec = vocabtype.mark_review_result(
                stats, "w", True, self.intervals, day=self.day)
        self.assertEqual(rec["level"], 5)
        self.assertEqual(rec["due"], "2026-12-17")  # 17 + 30

    def test_parse_intervals(self):
        self.assertEqual(vocabtype.parse_intervals("1,2,4"),
                         (1, 2, 4))


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(intervals=(1, 3, 7, 15, 30),
                                    level="all", quiz_size=0)
        self.entries = [
            entry("a", senses=[sense("n", "甲", "jia")]),
            entry("b", senses=[sense("n", "乙", "yi")]),
            entry("c", level="cet4", senses=[sense("n", "丙", "bing")]),
        ]
        self.stats = {"review": {
            "a": {"state": "learning", "level": 0, "due": "2999-01-01"},
            "b": {"state": "known", "level": 1, "due": "2000-01-01"},
            "c": {"state": "known", "level": 2, "due": "2999-01-01"},
        }}

    def test_scope_includes_learning_and_due_only(self):
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual({e["word"] for e in scope}, {"a", "b"})

    def test_level_filter(self):
        self.args.level = "cet4"
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        # c 是 CET4 但未到期；a/b 是 CET6
        self.assertEqual(scope, [])

    def test_quiz_size_cap(self):
        self.args.quiz_size = 1
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(len(scope), 1)


class QuestionTest(unittest.TestCase):
    def test_options_are_unique_and_include_correct(self):
        entries = [
            entry("a", senses=[sense("n", "甲", "jia")]),
            entry("b", senses=[sense("n", "乙", "yi")]),
            entry("c", senses=[sense("n", "丙", "bing")]),
            entry("d", senses=[sense("n", "丁", "ding")]),
        ]
        all_options = [(e, s) for e in entries for s in e["senses"]]
        q = vocabtype.build_question(entries[0], all_options)
        self.assertIsNotNone(q)
        self.assertIn(q["correct"], q["options"])
        self.assertEqual(len({s["cn"] for s in q["options"]}), len(q["options"]))
        self.assertEqual(len({vocabtype.sense_pinyin(s) for s in q["options"]}),
                         len(q["options"]))


if __name__ == "__main__":
    unittest.main()
