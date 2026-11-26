#!/usr/bin/env python3
"""复习状态机与检测池的单元测试（标准库 unittest）。

运行：
    python3 -I scripts/test_review.py
"""
import os
import sys
import tempfile
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

    def test_only_tested_sense_is_registered(self):
        stats = {}
        self.mark(stats, True)
        self.assertEqual(list(stats["review"]["w"]),
                         [vocabtype.sense_key(self.s0)])

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
        new, learning, known, levels, due, words = vocabtype.review_counts(
            stats, day=self.day)
        # 只登记检测过的义项；熟词 due 是明天，所以今天没有待复习
        self.assertEqual((new, learning, known, due, words), (0, 0, 1, 0, 1))
        self.assertEqual(levels, {1: 1})

    def test_review_counts_can_scope_to_words(self):
        stats = {}
        self.mark(stats, True)
        scoped = vocabtype.review_counts(
            stats, day=self.day, allowed_words={"other"})
        self.assertEqual(scoped[:3], (0, 0, 0))
        self.assertEqual(scoped[5], 0)
        included = vocabtype.review_counts(
            stats, day=self.day, allowed_words={"w"})
        self.assertEqual(included[5], 1)

    def test_early_correct_keeps_level_and_due(self):
        stats = {}
        self.mark(stats, True)
        rec = vocabtype.mark_review_result(
            stats, self.e, self.s0, True, self.intervals,
            day=self.day, early=True)
        self.assertEqual(rec["state"], "known")
        self.assertEqual(rec["level"], 1)
        self.assertEqual(rec["due"], "2026-11-18")
        self.assertEqual(rec["correct"], 2)

    def test_early_wrong_demotes_to_learning(self):
        stats = {}
        self.mark(stats, True)
        rec = vocabtype.mark_review_result(
            stats, self.e, self.s0, False, self.intervals,
            day=self.day, early=True)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["level"], 0)
        self.assertEqual(rec["due"], "2026-11-17")
        self.assertEqual(rec["wrong"], 1)

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
        self.assertNotIn(vocabtype.sense_key(self.s1), stats["review"]["w"])

    def test_normalize_drops_untested_senses(self):
        stats = {"review": {"w": {
            vocabtype.sense_key(self.s0): {
                "state": "known", "level": 1, "due": "2026-11-20"},
            vocabtype.sense_key(self.s1): vocabtype.new_review_record(self.day),
        }}}
        vocabtype.normalize_review(stats, [self.e])
        self.assertIn(vocabtype.sense_key(self.s0), stats["review"]["w"])
        self.assertNotIn(vocabtype.sense_key(self.s1), stats["review"]["w"])


class EncounterTest(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 17)
        self.intervals = (1, 3, 7, 15, 30)
        self.e = entry("w", senses=[sense("n", "甲", "jia"),
                                    sense("v", "乙", "yi")])
        self.s0, self.s1 = self.e["senses"]

    def test_register_marks_learning_without_counts(self):
        stats = {}
        changed = vocabtype.register_encountered(
            stats, self.e, self.s0, day=self.day)
        self.assertTrue(changed)
        rec = rec_for(stats, "w", self.s0)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["level"], 0)
        self.assertEqual(rec["due"], "2026-11-17")
        self.assertEqual(rec["wrong"], 0)
        self.assertNotIn(vocabtype.sense_key(self.s1), stats["review"]["w"])

    def test_register_does_not_demote_known(self):
        stats = {}
        vocabtype.mark_review_result(
            stats, self.e, self.s0, True, self.intervals, day=self.day)
        before = dict(rec_for(stats, "w", self.s0))
        changed = vocabtype.register_encountered(
            stats, self.e, self.s0, day=self.day)
        self.assertFalse(changed)
        self.assertEqual(rec_for(stats, "w", self.s0), before)

    def test_register_replaces_legacy_new(self):
        stats = {"review": {"w": {
            vocabtype.sense_key(self.s0):
                vocabtype.new_review_record(self.day)}}}
        vocabtype.register_encountered(stats, self.e, self.s0, day=self.day)
        self.assertEqual(rec_for(stats, "w", self.s0)["state"], "learning")

    def test_demote_keeps_due_known(self):
        stats = {"review": {"w": {
            vocabtype.sense_key(self.s0): {
                "state": "known", "level": 2, "due": "2026-11-17"},
            vocabtype.sense_key(self.s1): {
                "state": "known", "level": 3, "due": "2026-12-01"},
        }}}
        vocabtype.demote_unfinished(
            stats, [(self.e, self.s0), (self.e, self.s1)], day=self.day)
        due_rec = rec_for(stats, "w", self.s0)
        other_rec = rec_for(stats, "w", self.s1)
        self.assertEqual((due_rec["state"], due_rec["level"]), ("known", 2))
        self.assertEqual((other_rec["state"], other_rec["level"]),
                         ("learning", 0))
        self.assertEqual(other_rec["due"], "2026-11-17")

    def test_demote_keeps_learning_counts(self):
        stats = {}
        vocabtype.mark_review_result(
            stats, self.e, self.s0, False, self.intervals, day=self.day)
        vocabtype.demote_unfinished(stats, [(self.e, self.s0)], day=self.day)
        rec = rec_for(stats, "w", self.s0)
        self.assertEqual(rec["state"], "learning")
        self.assertEqual(rec["wrong"], 1)


class DetailTest(unittest.TestCase):
    def setUp(self):
        self.e = entry("w", senses=[sense("n", "甲", "jia"),
                                    sense("v", "乙", "yi")])
        self.stats = {"review": {"w": {
            "n|甲": {"state": "learning", "level": 0,
                     "due": "2026-11-18", "correct": 0, "wrong": 2,
                     "last": "2026-11-17"},
            "v|乙": {"state": "known", "level": 2,
                     "due": "2999-01-01", "correct": 3, "wrong": 0,
                     "last": "2026-11-17"},
        }}}
        self.rows = vocabtype.build_review_rows([self.e], self.stats)

    def test_expands_every_sense(self):
        self.assertEqual(
            [(r["word"], r["cn"], r["state"]) for r in self.rows],
            [("w", "甲", "learning"), ("w", "乙", "known")])

    def test_state_labels(self):
        self.assertEqual([vocabtype.review_state_label(r)
                          for r in self.rows], ["生词", "熟词 L2"])

    def test_filter_counts(self):
        counts = {f: len([r for r in self.rows
                          if vocabtype.review_row_matches(r, f)])
                  for f in ("all", "learning", "known", "due",
                            "unlearned")}
        self.assertEqual(counts, {"all": 2, "learning": 1, "known": 1,
                                  "due": 1, "unlearned": 0})

    def test_unlearned_rows_only_contain_untouched_senses(self):
        e2 = entry("x", senses=[sense("n", "丙", "bing")])
        unlearned = vocabtype.build_unlearned_rows(
            [self.e, e2], self.stats)
        self.assertEqual([(r["word"], r["cn"]) for r in unlearned],
                         [("x", "丙")])
        row = unlearned[0]
        self.assertEqual(row["state"], "unlearned")
        self.assertEqual(vocabtype.review_state_label(row), "未学习")
        self.assertTrue(vocabtype.review_row_matches(row, "unlearned"))
        self.assertFalse(vocabtype.review_row_matches(row, "due"))
        self.assertTrue(vocabtype.review_row_matches_search(row, "bing"))
        self.assertTrue(vocabtype.review_row_matches_search(row, "x"))

    def test_due_labels(self):
        day = date(2026, 11, 17)
        self.assertEqual(vocabtype.due_short("2026-11-18", day),
                         "11-18 1天后")
        self.assertEqual(vocabtype.due_full("2026-11-17", day),
                         "2026-11-17（今天）")
        self.assertEqual(vocabtype.due_short(None, day), "—")

    def test_pinyin_is_stored_for_search(self):
        self.assertEqual([r["pinyin"] for r in self.rows], ["jia", "yi"])

    def test_search_by_word_and_pinyin(self):
        row = self.rows[0]
        self.assertTrue(vocabtype.review_row_matches_search(row, "w"))
        self.assertTrue(vocabtype.review_row_matches_search(row, "jia"))
        self.assertTrue(vocabtype.review_row_matches_search(row, "ji a"))
        self.assertFalse(vocabtype.review_row_matches_search(row, "yi"))
        self.assertFalse(vocabtype.review_row_matches_search(row, "zzz"))
        self.assertTrue(vocabtype.review_row_matches_search(row, ""))


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(count=10, level="all", weak=False,
                                    new=False, all_senses=False)
        self.entries = [
            entry("a", senses=[sense("n", "甲", "jia"),
                               sense("v", "乙", "yi")]),
            entry("b", level="cet4", senses=[sense("n", "丙", "bing")]),
        ]
        self.stats = {"words": {}, "review": {
            "a": {"n|甲": {"state": "known", "level": 1,
                           "due": "2999-01-01"}},
        }}

    def senses(self, items):
        return {(e["word"], s["cn"]) for e, s in items}

    def test_default_excludes_known_senses_only(self):
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        # a 的甲义项已熟，乙义项没学过，仍然可以抽到
        self.assertEqual(self.senses(items),
                         {("a", "乙"), ("b", "丙")})

    def test_all_senses_includes_known(self):
        self.args.all_senses = True
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(self.senses(items),
                         {("a", "甲"), ("a", "乙"), ("b", "丙")})

    def test_count_limits_senses(self):
        self.args.count = 2
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(len(items), 2)

    def test_same_word_can_be_drawn_for_two_senses(self):
        self.args.count = 2
        stats = {"words": {}, "review": {}}
        items = vocabtype.select_items([self.entries[0]], stats, self.args)
        self.assertEqual(self.senses(items), {("a", "甲"), ("a", "乙")})

    def test_level_filter(self):
        self.args.level = "cet4"
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(self.senses(items), {("b", "丙")})

    def test_cet4_plus_6_filter(self):
        self.args.level = "cet4+6"
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(self.senses(items), {("a", "乙"), ("b", "丙")})

    def test_new_filters_at_word_level(self):
        self.stats["words"]["a"] = {"n": 1}
        self.args.new = True
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(self.senses(items), {("b", "丙")})

    def test_all_known_returns_empty_without_all_senses(self):
        self.stats["review"]["a"]["v|乙"] = {
            "state": "known", "level": 1, "due": "2999-01-01"}
        self.stats["review"]["b"] = {
            "n|丙": {"state": "known", "level": 1, "due": "2999-01-01"}}
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(items, [])
        self.args.all_senses = True
        items = vocabtype.select_items(self.entries, self.stats, self.args)
        self.assertEqual(len(items), 3)


class UnlearnedSelectionTest(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 27)
        self.args = SimpleNamespace(level="all")
        self.entries = [
            entry("a", senses=[sense("n", "甲", "jia"),
                               sense("v", "乙", "yi"),
                               sense("v", "丙", "bing")]),
            entry("b", level="cet4", senses=[sense("n", "丁", "ding")]),
        ]
        self.stats = {"words": {}, "review": {
            "a": {"n|甲": {"state": "known", "level": 1,
                           "due": "2999-01-01"},
                  "v|乙": {"state": "learning", "level": 0,
                           "due": "2026-11-27"}},
        }}

    def senses(self, items):
        return {(e["word"], s["cn"]) for e, s in items}

    def test_only_unlearned_senses_are_selected(self):
        items = vocabtype.select_unlearned_items(
            self.entries, self.stats, self.args, 10)
        self.assertEqual(self.senses(items), {("a", "丙"), ("b", "丁")})

    def test_same_word_can_yield_multiple_senses(self):
        stats = {"words": {}, "review": {}}
        items = vocabtype.select_unlearned_items(
            [self.entries[0]], stats, self.args, 3)
        self.assertEqual(self.senses(items), {("a", "甲"), ("a", "乙"),
                                              ("a", "丙")})

    def test_old_new_record_counts_as_unlearned(self):
        self.stats["review"]["a"]["n|甲"] = {
            "state": "new", "level": 0, "due": "2999-01-01"}
        items = vocabtype.select_unlearned_items(
            self.entries, self.stats, self.args, 10)
        self.assertIn(("a", "甲"), self.senses(items))

    def test_level_filter(self):
        self.args.level = "cet4"
        items = vocabtype.select_unlearned_items(
            self.entries, self.stats, self.args, 10)
        self.assertEqual(self.senses(items), {("b", "丁")})

    def test_fewer_than_requested_returns_all_available(self):
        items = vocabtype.select_unlearned_items(
            self.entries, self.stats, self.args, 10)
        self.assertEqual(len(items), 2)

    def test_add_registers_learning_l0_due_today(self):
        items = vocabtype.add_unlearned_to_review(
            self.entries, self.stats, self.args, 10, day=self.day)
        self.assertEqual(self.senses(items), {("a", "丙"), ("b", "丁")})
        for e, s in items:
            rec = rec_for(self.stats, e["word"], s)
            self.assertEqual(rec["state"], "learning")
            self.assertEqual(rec["level"], 0)
            self.assertEqual(rec["due"], "2026-11-27")
        # 已有熟词/生词记录不被改写
        self.assertEqual(self.stats["review"]["a"]["n|甲"]["state"], "known")
        self.assertEqual(self.stats["review"]["a"]["v|乙"]["state"], "learning")


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

    def test_scope_includes_only_learning_and_due(self):
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        # a 是未测义项（没学过），不应进入复习池
        self.assertEqual(self.scope_senses(scope), {("b", "乙")})

    def test_untested_senses_are_not_drawn(self):
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertNotIn("a", {e["word"] for e, _, _ in scope})

    def test_level_filter(self):
        self.args.level = "cet4"
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(scope, [])

    def test_level_filter_keeps_due_matching_word(self):
        self.stats["review"]["c"]["n|丙"]["due"] = "2000-01-01"
        self.args.level = "cet4"
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(self.scope_senses(scope), {("c", "丙")})

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

    def test_session_items_keep_two_senses_of_same_word(self):
        e = entry("a", senses=[sense("n", "甲", "jia"),
                               sense("v", "乙", "yi")])
        session_items = [(e, e["senses"][0]), (e, e["senses"][1])]
        scope = vocabtype.build_quiz_scope(
            [e], {"review": {}}, self.args, session_items)
        self.assertEqual(self.scope_senses(scope), {("a", "甲"), ("a", "乙")})


class KnownReviewTest(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(intervals=(1, 3, 7, 15, 30),
                                    level="all", quiz_size=0,
                                    review_known=10)
        self.entries = [
            entry("a", senses=[sense("n", "甲", "jia"),
                               sense("v", "乙", "yi")]),
            entry("b", level="cet4", senses=[sense("n", "丙", "bing")]),
            entry("c", senses=[sense("n", "丁", "ding")]),
        ]
        self.stats = {"review": {
            # 两个熟词义项，都远未到期
            "a": {"n|甲": {"state": "known", "level": 1,
                           "due": "2999-01-01"},
                  "v|乙": {"state": "known", "level": 3,
                           "due": "2999-01-02"}},
            # 生词、旧版未测义项都不应被加练抽到
            "b": {"n|丙": {"state": "learning", "level": 0,
                           "due": "2000-01-01"}},
            "c": {"n|丁": {"state": "new", "level": 0,
                           "due": "2999-01-01"}},
        }}

    def scope_senses(self, scope):
        return {(e["word"], s["cn"]) for e, s, _ in scope}

    def test_draws_known_even_when_not_due(self):
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(self.scope_senses(scope), {("a", "甲"), ("a", "乙")})

    def test_only_known_senses_are_drawn(self):
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        words = {e["word"] for e, _, _ in scope}
        self.assertEqual(words, {"a"})

    def test_count_caps_and_samples_by_sense(self):
        self.args.review_known = 1
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(len(scope), 1)
        self.assertIn(self.scope_senses(scope).pop()[1], {"甲", "乙"})

    def test_level_filter(self):
        self.args.level = "cet4"
        scope = vocabtype.build_quiz_scope(
            self.entries, self.stats, self.args)
        self.assertEqual(scope, [])

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


def piece_sense(cn, tokens):
    return {"pos": "n", "cn": cn, "tokens": tokens}


class SplitTest(unittest.TestCase):
    def test_split_pieces(self):
        self.assertEqual(vocabtype.split_pieces("甲，乙, 丙 "),
                         ["甲", "乙", "丙"])
        self.assertEqual(vocabtype.split_pieces(""), [])

    def test_split_sense_pieces_keep_pinyin(self):
        s = piece_sense("甲，乙", [["甲", "jia"], ["，", ""], ["乙", "yi"]])
        pieces = vocabtype.split_sense_pieces(s)
        self.assertEqual(
            [(p["cn"], vocabtype.sense_pinyin(p)) for p in pieces],
            [("甲", "jia"), ("乙", "yi")])

    def test_build_split_question_marks_all_correct_pieces(self):
        correct = piece_sense("甲，乙", [["甲", "jia"], ["，", ""], ["乙", "yi"]])
        distractors = [
            piece_sense("丙，丁", [["丙", "bing"], ["，", ""], ["丁", "ding"]]),
            piece_sense("戊", [["戊", "wu"]]),
            piece_sense("甲，己", [["甲", "jia"], ["，", ""], ["己", "ji"]]),
        ]
        question = {"entry": entry("a"), "correct": correct,
                    "options": [correct] + distractors, "correct_index": 0}
        sq = vocabtype.build_split_question(question)
        self.assertIsNotNone(sq)
        self.assertEqual({p["cn"] for p in sq["options"]},
                         {"甲", "乙", "丙", "丁", "戊", "己"})
        correct_texts = {sq["options"][i]["cn"] for i in sq["correct"]}
        self.assertEqual(correct_texts, {"甲", "乙"})


class FormTest(unittest.TestCase):
    def form_entry(self, pos, forms):
        return {"word": "w", "trans": [{"pos": pos, "cn": "测试"}],
                "senses": [sense(pos, "测试", "ce shi")], "forms": forms}

    def test_verb_s_is_third_person_not_plural(self):
        e = self.form_entry("v", {"s": "cancels", "3": "cancels"})
        self.assertEqual(vocabtype.form_parts(e), ["第三人称单数 cancels"])

    def test_noun_only_s_is_plural(self):
        e = self.form_entry("n", {"s": "books", "3": "books"})
        self.assertEqual(vocabtype.form_parts(e), ["复数 books"])

    def test_noun_and_verb_same_form_is_combined(self):
        e = self.form_entry("n&vt", {"s": "cans", "3": "cans"})
        self.assertEqual(vocabtype.form_parts(e),
                         ["第三人称单数/复数 cans"])

    def test_noun_and_verb_different_forms(self):
        e = self.form_entry("n&v", {"s": "foci", "3": "focuses"})
        self.assertEqual(vocabtype.form_parts(e),
                         ["第三人称单数 focuses", "复数 foci"])

    def test_verb_forms_require_verb_pos(self):
        e = self.form_entry("n", {"p": "courted", "d": "courted",
                                  "i": "courting", "3": "courts",
                                  "s": "courts"})
        self.assertEqual(vocabtype.form_parts(e), ["复数 courts"])

    def test_comparatives_require_adjective_pos(self):
        e = self.form_entry("n", {"s": "busts", "r": "buster",
                                  "t": "bustest"})
        self.assertEqual(vocabtype.form_parts(e), ["复数 busts"])

    def test_adjective_hides_fake_plural(self):
        e = self.form_entry("adj", {"s": "remotes", "r": "remoter",
                                    "t": "remotest"})
        self.assertEqual(vocabtype.form_parts(e),
                         ["比较级 remoter", "最高级 remotest"])


class LevelTest(unittest.TestCase):
    def e(self, level, word="w"):
        return {"word": word, "level": level}

    def test_level_choices(self):
        self.assertTrue(vocabtype.level_matches(self.e("cet4"), "all"))
        self.assertTrue(vocabtype.level_matches(self.e("cet4"), "cet4+6"))
        self.assertTrue(vocabtype.level_matches(self.e("cet6"), "cet4+6"))
        self.assertFalse(vocabtype.level_matches(self.e("extra"), "cet4+6"))
        self.assertTrue(vocabtype.level_matches(self.e("cet6"), "cet6"))
        self.assertFalse(vocabtype.level_matches(self.e("cet4"), "cet6"))

    def test_core_levels_use_word_lists(self):
        old = vocabtype._CORE_LEVELS
        vocabtype._CORE_LEVELS = {"core": {"alpha"}, "core+": {"alpha", "beta"}}
        self.addCleanup(setattr, vocabtype, "_CORE_LEVELS", old)
        self.assertTrue(vocabtype.level_matches(
            self.e("cet4", "alpha"), "core"))
        self.assertFalse(vocabtype.level_matches(
            self.e("cet6", "beta"), "core"))
        self.assertTrue(vocabtype.level_matches(
            self.e("cet6", "beta"), "core+"))

    def test_core_word_file_is_consistent(self):
        old = vocabtype._CORE_LEVELS
        vocabtype._CORE_LEVELS = None
        self.addCleanup(setattr, vocabtype, "_CORE_LEVELS", old)
        levels = vocabtype.core_levels()
        self.assertIn("core", levels)
        self.assertIn("core+", levels)
        self.assertLessEqual(levels["core"], levels["core+"])
        self.assertGreater(len(levels["core"]), 2900)
        self.assertGreater(len(levels["core+"]), 4700)


class CoreSenseTest(unittest.TestCase):
    def setUp(self):
        self.old_levels = vocabtype._CORE_LEVELS
        self.old_excl = vocabtype._CORE_SENSE_EXCLUSIONS
        vocabtype._CORE_LEVELS = {"core": {"a"}, "core+": {"a"}}
        vocabtype._CORE_SENSE_EXCLUSIONS = {"a": {"n|乙"}}
        self.addCleanup(setattr, vocabtype, "_CORE_LEVELS", self.old_levels)
        self.addCleanup(setattr, vocabtype, "_CORE_SENSE_EXCLUSIONS", self.old_excl)
        self.entries = [entry("a", senses=[sense("n", "甲", "jia"),
                                           sense("n", "乙", "yi")])]
        self.s0, self.s1 = self.entries[0]["senses"]

    def test_excluded_sense_is_not_selectable(self):
        args = SimpleNamespace(count=10, level="core", weak=False,
                               new=False, all_senses=True)
        items = vocabtype.select_items(
            self.entries, {"words": {}, "review": {}}, args)
        self.assertEqual({s["cn"] for _e, s in items}, {"甲"})

    def test_excluded_sense_is_not_in_review_pool(self):
        stats = {"review": {"a": {"n|乙": {
            "state": "learning", "level": 0, "due": "2000-01-01"}}}}
        args = SimpleNamespace(intervals=(1, 3, 7, 15, 30),
                               level="core", quiz_size=0, review_known=None)
        scope = vocabtype.build_quiz_scope(self.entries, stats, args)
        self.assertEqual(scope, [])

    def test_sense_allowed_only_for_core(self):
        self.assertFalse(vocabtype.sense_allowed(
            self.entries[0], self.s1, "core"))
        self.assertTrue(vocabtype.sense_allowed(
            self.entries[0], self.s0, "core"))
        self.assertTrue(vocabtype.sense_allowed(
            self.entries[0], self.s1, "all"))

    def test_core_sense_exclusion_file_is_valid(self):
        old = vocabtype._CORE_SENSE_EXCLUSIONS
        vocabtype._CORE_SENSE_EXCLUSIONS = None
        self.addCleanup(setattr, vocabtype, "_CORE_SENSE_EXCLUSIONS", old)
        exclusions = vocabtype.core_sense_exclusions()
        self.assertGreater(len(exclusions), 600)
        # Real data check: every excluded key must exist and never remove all senses.
        import json as _json
        with open(vocabtype.DEFAULT_DATA, encoding="utf-8") as f:
            data = _json.load(f)
        data_words = {e["word"].lower(): e for e in data["words"]}
        for word, keys in exclusions.items():
            entry = data_words[word]
            valid = {f"{s.get('pos', '')}|{s['cn']}" for s in entry["senses"]}
            self.assertTrue(keys <= valid, word)
            self.assertLess(len(keys), len(valid), word)

    def test_review_counts_honors_excluded_senses(self):
        stats = {"review": {"a": {"n|乙": {
            "state": "learning", "level": 0, "due": "2000-01-01"}}}}
        result = vocabtype.review_counts(
            stats, excluded_senses={"a": {"n|乙"}})
        self.assertEqual(result[:3], (0, 0, 0))
        self.assertEqual(result[5], 0)


class ProfileTest(unittest.TestCase):
    def with_paths(self, root):
        old_stats = vocabtype.DEFAULT_STATS
        old_dir = vocabtype.PROFILES_DIR
        old_pointer = vocabtype.PROFILE_POINTER
        vocabtype.DEFAULT_STATS = os.path.join(root, "stats.json")
        vocabtype.PROFILES_DIR = os.path.join(root, "profiles")
        vocabtype.PROFILE_POINTER = os.path.join(root, "current_profile")
        self.addCleanup(setattr, vocabtype, "DEFAULT_STATS", old_stats)
        self.addCleanup(setattr, vocabtype, "PROFILES_DIR", old_dir)
        self.addCleanup(setattr, vocabtype, "PROFILE_POINTER", old_pointer)

    def test_default_profile_keeps_legacy_path(self):
        self.assertEqual(vocabtype.profile_stats_path("default"),
                         vocabtype.DEFAULT_STATS)

    def test_named_profile_path(self):
        with tempfile.TemporaryDirectory() as root:
            self.with_paths(root)
            self.assertEqual(vocabtype.profile_stats_path("cet6"),
                             os.path.join(root, "profiles", "cet6.json"))

    def test_invalid_profile_names_are_rejected(self):
        for name in ("", "../x", "a/b", "-x", "x" * 41):
            with self.assertRaises(SystemExit):
                vocabtype.profile_stats_path(name)

    def test_current_profile_defaults_when_pointer_missing(self):
        with tempfile.TemporaryDirectory() as root:
            self.with_paths(root)
            self.assertEqual(vocabtype.current_profile_name(), "default")

    def test_set_current_profile_persists_choice(self):
        with tempfile.TemporaryDirectory() as root:
            self.with_paths(root)
            vocabtype.set_current_profile("work")
            self.assertEqual(vocabtype.current_profile_name(), "work")

    def test_invalid_pointer_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as root:
            self.with_paths(root)
            with open(vocabtype.PROFILE_POINTER, "w", encoding="utf-8") as f:
                f.write("../bad\n")
            self.assertEqual(vocabtype.current_profile_name(), "default")

    def test_set_invalid_profile_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            self.with_paths(root)
            with self.assertRaises(SystemExit):
                vocabtype.set_current_profile("../bad")

    def test_profile_level_defaults_to_all(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "new.json")
            self.assertEqual(vocabtype.get_profile_level(path), "all")

    def test_profile_level_persists(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "new.json")
            vocabtype.set_profile_level(path, "cet4+6")
            self.assertEqual(vocabtype.get_profile_level(path), "cet4+6")

    def test_invalid_profile_level_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "new.json")
            with self.assertRaises(SystemExit):
                vocabtype.set_profile_level(path, "cet5")

    def test_profile_paths_lists_default_and_valid_files(self):
        with tempfile.TemporaryDirectory() as root:
            self.with_paths(root)
            os.makedirs(vocabtype.PROFILES_DIR)
            for filename in ("work.json", "bad name.json", "default.json"):
                open(os.path.join(vocabtype.PROFILES_DIR, filename),
                     "w").close()
            names = [name for name, _path in vocabtype.profile_paths()]
            self.assertEqual(names, ["default", "work"])


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
