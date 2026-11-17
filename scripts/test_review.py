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


def rec_for(stats, word, s):
    return stats["review"][word][vocabtype.sense_key(s)]


class IntervalTest(unittest.TestCase):
    def setUp(self):
        self.intervals = (1, 3, 7, 15, 30)
        self.day = date(2026, 11, 17)
        self.e = entry("w", senses=[sense("n", "甲", "jia"),
                                    sense("v", "乙", "yi")])
        self.s0, self.s1 = self.e["senses"]

    def mark(self, stats, correct):
        return vocabtype.mark_review_result(
            stats, self.e, self.s0, correct, self.intervals, day=self.day)

    def test_learning_correct_enters_l1(self):
        stats = {}
        rec = self.mark(stats, True)
        self.assertEqual(rec["state"], "known")
        self.assertEqual(rec["level"], 1)
        self.assertEqual(rec["due"], "2026-11-18")

    def test_all_senses_are_registered(self):
        stats = {}
        self.mark(stats, True)
        self.assertEqual(len(stats["review"]["w"]), 2)
        self.assertEqual(rec_for(stats, "w", self.s1)["state"], "new")

    def test_learning_wrong_stays_learning(self):
        stats = {}
        rec = self.mark(stats, False)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["level"], 0)
        self.assertEqual(rec["wrong"], 1)

    def test_known_level_up_uses_next_interval(self):
        stats = {}
        self.mark(stats, True)
        rec = self.mark(stats, True)
        self.assertEqual(rec["level"], 2)
        self.assertEqual(rec["due"], "2026-11-20")  # 17 + 3

    def test_known_wrong_returns_to_learning(self):
        stats = {}
        self.mark(stats, True)
        rec = self.mark(stats, False)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["level"], 0)
        self.assertEqual(rec["wrong"], 1)

    def test_max_level_stays_max(self):
        stats = {}
        for _ in range(10):
            rec = self.mark(stats, True)
        self.assertEqual(rec["level"], 5)
        self.assertEqual(rec["due"], "2026-12-17")  # 17 + 30

    def test_review_counts(self):
        stats = {}
        self.mark(stats, True)
        new, learning, known, levels, due, words = vocabtype.review_counts(stats)
        # 熟词 due 是明天，所以今天只有未测的第二个义项待复习
        self.assertEqual((new, learning, known, due, words), (1, 0, 1, 1, 1))
        self.assertEqual(levels, {1: 1})

    def test_parse_intervals(self):
        self.assertEqual(vocabtype.parse_intervals("1,2,4"), (1, 2, 4))

    def test_migrate_old_word_level_record(self):
        stats = {"review": {"w": {
            "state": "known", "level": 2, "due": "2026-11-20",
            "streak": 2, "correct": 2, "wrong": 0, "last": "2026-11-17",
            "sense": {"pos": "n", "cn": "甲"},
        }}}
        vocabtype.normalize_review(stats, [self.e])
        self.assertEqual(rec_for(stats, "w", self.s0)["level"], 2)
        self.assertEqual(rec_for(stats, "w", self.s1)["state"], "new")


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
            "a": {"n|甲": {"state": "new", "level": 0,
                           "due": "2999-01-01"}},
            "b": {"n|乙": {"state": "known", "level": 1,
                           "due": "2000-01-01"}},
            "c": {"n|丙": {"state": "known", "level": 2,
                           "due": "2999-01-01"}},
        }}

    def scope_senses(self, scope):
        return {(e["word"], s["cn"]) for e, s, _ in scope}

    def test_scope_includes_new_and_due_only(self):
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(self.scope_senses(scope), {("a", "甲"), ("b", "乙")})

    def test_level_filter(self):
        self.args.level = "cet4"
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(scope, [])

    def test_quiz_size_cap(self):
        self.args.quiz_size = 1
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(len(scope), 1)

    def test_session_items_are_included(self):
        session_items = [(self.entries[2], self.entries[2]["senses"][0])]
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args, session_items)
        self.assertIn(("c", "丙"), self.scope_senses(scope))


class FuzzyTest(unittest.TestCase):
    def setUp(self):
        self.options = [sense("n", "甲", "jia"),
                        sense("n", "乙", "yi"),
                        sense("n", "丙", "bing")]

    def test_exact_and_prefix(self):
        self.assertEqual(vocabtype.fuzzy_match_index("jia", self.options)[0], 0)
        self.assertEqual(vocabtype.fuzzy_match_index("ji", self.options)[0], 0)

    def test_typo_and_omission(self):
        self.assertEqual(vocabtype.fuzzy_match_index("jai", self.options)[0], 0)
        self.assertEqual(vocabtype.fuzzy_match_index("bing", self.options)[0], 2)

    def test_empty_is_none(self):
        self.assertIsNone(vocabtype.fuzzy_match_index("", self.options)[0])

    def test_narrows_to_unique(self):
        opts = [sense("n", "甲", "jia"), sense("n", "乙", "jing"),
                sense("n", "丙", "yi")]
        partial, _ = vocabtype.fuzzy_matches("ji", opts)
        self.assertEqual(len(partial), 2)
        exact, _ = vocabtype.fuzzy_matches("jia", opts)
        self.assertEqual(exact, [0])


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

    def test_preferred_sense_is_used(self):
        target = entry("a", senses=[sense("n", "甲", "jia"),
                                    sense("v", "乙", "yi")])
        entries = [target,
                   entry("b", senses=[sense("n", "丙", "bing")]),
                   entry("c", senses=[sense("n", "丁", "ding")])]
        all_options = [(e, s) for e in entries for s in e["senses"]]
        q = vocabtype.build_question(target, all_options,
                                     preferred=target["senses"][1])
        self.assertIs(q["correct"], target["senses"][1])


if __name__ == "__main__":
    unittest.main()
