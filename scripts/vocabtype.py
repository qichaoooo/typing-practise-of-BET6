#!/usr/bin/env python3
"""vocabtype — 边背 CET-6 单词边练打字。

拼写模式：
  show   （默认）两阶段：先照着英文单词拼写；再从该词的义项中按词频顺序
         加权抽一条，看着逐字拼音把释义全拼打出来（打错不计入错词）
  recall 只显示中文释义，凭记忆拼出英文单词

释义检测（背单词模式）：
  普通模式拼写结束后自动检测释义；首答答对进熟词表，答错进生词表。
  复习按义项进行：一个词第一次进入检测时会登记它的所有义项，逐个复习。
  检测时按数字直选，或输入拼音模糊筛选，再按 ; 或 Enter 确定。
  --review 只复习未测/生词义项和今天到期的熟词义项；--no-quiz 跳过检测。

用法示例：
  python3 -I vocabtype.py                 # 拼写 + 释义检测
  python3 -I vocabtype.py --level cet6    # 只练官方六级★词
  python3 -I vocabtype.py --review        # 复习生词/到期熟词
  python3 -I vocabtype.py --no-quiz       # 只拼写
  python3 -I vocabtype.py --stats         # 查看累计进度

按键：Esc / Ctrl-C 退出并查看本次结果；练习中按 ; 查看该词的词根、同根词、
近义词、变形、例句、真题和词频，再按 ; 继续拼写。
"""
import argparse
import difflib
import json
import os
import random
import re
import shutil
import sys
import termios
import time
import tty
import unicodedata
from datetime import date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(PROJECT_DIR, "data", "cet6.json")
DEFAULT_STATS = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "vocabtype", "stats.json")

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"

LEVEL_LABEL = {"cet6": "CET6", "cet4": "CET4", "extra": "扩展"}

DEFAULT_INTERVALS = (1, 3, 7, 15, 30)  # 熟词 5 级复习间隔（天）


class Keys:
    ESC = 0x1B
    ENTER = (0x0D, 0x0A)
    BACKSPACE = (0x7F, 0x08)
    CTRL_U = 0x15
    CTRL_C = 0x03


def load_words(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["words"]


def load_stats(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"words": {}, "sessions": []}
    data.setdefault("words", {})
    data.setdefault("sessions", [])
    data.setdefault("review", {})
    data.setdefault("quiz_sessions", [])
    return data


def save_stats(path, stats):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)
    os.replace(tmp, path)


def word_stat(stats, word):
    return stats["words"].setdefault(word, {"r": 0, "w": 0, "n": 0, "t": 0.0})


def parse_intervals(text):
    try:
        vals = tuple(int(x) for x in str(text).split(",") if x.strip())
    except ValueError:
        sys.exit("--intervals 需要逗号分隔的正整数，例如 1,3,7,15,30")
    if not vals or any(v <= 0 for v in vals):
        sys.exit("--intervals 必须是正整数，例如 1,3,7,15,30")
    return vals


def today_iso():
    return date.today().isoformat()


def is_due(rec, day=None):
    due = rec.get("due")
    if not due:
        return True
    try:
        return date.fromisoformat(due) <= (day or date.today())
    except ValueError:
        return True


def sense_key(sense):
    return f"{sense.get('pos', '')}|{sense.get('cn', '')}"


def new_review_record(day=None):
    day = day or date.today()
    return {"state": "new", "level": 0, "due": day.isoformat(),
            "streak": 0, "correct": 0, "wrong": 0, "last": None}


def find_sense(entry, ref):
    if not ref:
        return None
    for s in entry.get("senses", []):
        if (s is ref or
                (s.get("cn") == ref.get("cn") and
                 s.get("pos") == ref.get("pos"))):
            return s
    return None


def ensure_word_review(stats, entry, create_all=False, day=None):
    """把 review[word] 规范为 {义项 key: 记录}，并可按需补齐所有义项。"""
    day = day or date.today()
    review = stats.setdefault("review", {})
    recs = review.get(entry["word"])
    senses = entry.get("senses") or []
    if not isinstance(recs, dict):
        recs = {}
        review[entry["word"]] = recs
    elif "state" in recs:
        # 兼容旧格式：每个词一条记录
        old = recs
        recs = {}
        review[entry["word"]] = recs
        target = find_sense(entry, old.get("sense"))
        if target is None and senses:
            target = senses[0]
        if target is not None:
            recs[sense_key(target)] = {
                "state": old.get("state", "learning"),
                "level": int(old.get("level", 0)),
                "due": old.get("due") or day.isoformat(),
                "streak": int(old.get("streak", 0)),
                "correct": int(old.get("correct", 0)),
                "wrong": int(old.get("wrong", 0)),
                "last": old.get("last"),
            }
    if create_all:
        valid = {sense_key(s) for s in senses}
        for key in list(recs):
            if key not in valid:  # 词条释义变了，丢弃过期记录
                del recs[key]
        for s in senses:
            recs.setdefault(sense_key(s), new_review_record(day))
    return recs


def normalize_review(stats, entries):
    """把旧进度迁移成按义项记录，并为已进入复习的词补齐所有义项。"""
    by_word = {e["word"]: e for e in entries}
    for word in list(stats.get("review", {})):
        entry = by_word.get(word)
        if entry:
            ensure_word_review(stats, entry, create_all=True)


def mark_review_result(stats, entry, sense, correct, intervals, day=None):
    """首次检测结果决定该义项的状态；重抽不调用本函数。"""
    day = day or date.today()
    recs = ensure_word_review(stats, entry, create_all=True, day=day)
    rec = recs.setdefault(sense_key(sense), new_review_record(day))
    rec["last"] = day.isoformat()
    if correct:
        rec["correct"] = rec.get("correct", 0) + 1
        rec["streak"] = rec.get("streak", 0) + 1
        level = (min(int(rec.get("level", 0)) + 1, len(intervals))
                 if rec.get("state") == "known" else 1)
        rec["state"] = "known"
        rec["level"] = level
        rec["due"] = (day + timedelta(days=intervals[level - 1])).isoformat()
    else:
        rec["wrong"] = rec.get("wrong", 0) + 1
        rec["streak"] = 0
        rec["state"] = "learning"
        rec["level"] = 0
        rec["due"] = day.isoformat()
    return rec


def meaning(entry, sep="；"):
    return sep.join(f"{t['pos']}. {t['cn']}" if t["pos"] else t["cn"]
                    for t in entry["trans"]) or "（无释义）"


def meaning_with_mark(entry, mark_sense):
    """重建释义行，并把本次要练的义项加下划线。"""
    senses = entry.get("senses") or []
    if not senses:
        return meaning(entry)
    chunks = []
    last_pos = None
    for s in senses:
        prefix = f"{s['pos']}. " if s["pos"] and s["pos"] != last_pos else ""
        text = prefix + s["cn"]
        if s is mark_sense:
            text = f"{UNDERLINE}{text}{RESET}"
        chunks.append(text)
        last_pos = s["pos"]
    return "；".join(chunks)


def select_words(entries, stats, args):
    n = args.count
    level_pool = [e for e in entries
                  if args.level == "all" or e.get("level") == args.level]
    if not level_pool:
        sys.exit(f"没有 level={args.level} 的单词。")
    if args.weak:
        practiced = []
        for e in level_pool:
            s = stats["words"].get(e["word"])
            if s and s["n"] > 0:
                rate = (s["w"] + 0.5) / (s["n"] + 1.0)
                practiced.append((rate, s["w"], e))
        practiced.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen = [e for _, _, e in practiced[:n]]
        rest = [e for e in level_pool if e not in chosen]
        random.shuffle(rest)
        return chosen + rest[:max(0, n - len(chosen))]
    if args.new:
        pool = [e for e in level_pool
                if stats["words"].get(e["word"], {}).get("n", 0) == 0]
        pool = pool or level_pool
    else:
        pool = list(level_pool)
    random.shuffle(pool)
    return pool[:n]


def fmt_time(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def normalize_pinyin(text):
    """比较用：只保留 a-z，v 视为 u（nve/nue 都接受）。"""
    return "".join("u" if ch == "v" else ch for ch in text.lower()
                   if "a" <= ch <= "z")


def disp_width(ch):
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def pick_sense(entry):
    """按义项顺序做 Zipf 加权（1/(i+1)），越靠前的义项越常出现。

    词表本身没有义项频率数据，用顺序近似真实频率。
    """
    senses = entry.get("senses") or []
    if not senses:
        return None
    weights = [1.0 / (i + 1) for i in range(len(senses))]
    return random.choices(senses, weights=weights, k=1)[0]


FORM_ORDER = (("p", "过去式"), ("d", "过去分词"), ("i", "现在分词"),
              ("3", "第三人称单数"), ("s", "复数"),
              ("r", "比较级"), ("t", "最高级"))


def pos_group(pos):
    parts = set((pos or "").lower().replace("&", " ").replace("/", " ").split())
    if parts & {"v", "vt", "vi"}:
        return "v"
    if "n" in parts:
        return "n"
    return (pos or "").lower()


def same_pos(a, b):
    if not a or not b:
        return True
    return pos_group(a) == pos_group(b)


def sense_keywords(sense):
    if not sense:
        return []
    text = re.sub(r"\[[^\]]*\]", "", sense.get("cn", ""))
    out = []
    for part in re.split(r"[，,；;、/]", text):
        part = part.strip(" 。.…（）()")
        if len(part) >= 2:
            out.append(part)
    return out


def matches_any(text, keywords):
    return bool(text) and any(k in text for k in keywords)


def info_lines(entry, sense, index=None, total=None, senses=None):
    """按 ; 展示的词源/助记信息；能按义项过滤的字段尽量过滤。"""
    lines = []
    if sense:
        label = f"{sense['pos']}. " if sense.get("pos") else ""
        tag = f" ({index}/{total})" if index and total and total > 1 else ""
        lines.append(f"{DIM}释义{tag}{RESET}   {label}{sense['cn']}")
    keywords = sense_keywords(sense)
    multi = len(senses or []) > 1

    if entry.get("method"):
        note = ""
        if sense and multi:
            method = entry["method"]
            target = method.split("→")[-1] if "→" in method else method
            if keywords and any(k in target for k in keywords):
                note = "（对应本义项）"
            else:
                other = None
                for s in (senses or []):
                    if s is sense:
                        continue
                    if any(k in target for k in sense_keywords(s)):
                        other = s["cn"]
                        break
                note = ("（词级" + (f"，主要对应：{other}" if other else "")
                        + "）")
        lines.append(f"{DIM}助记{note}{RESET}   {entry['method']}")

    same_pos_count = 0
    if sense:
        same_pos_count = sum(
            1 for s in (senses or [])
            if s is not sense and same_pos(s.get("pos"), sense.get("pos")))
    pos_note = "（词性级）" if same_pos_count else ""

    family = entry.get("family") or []
    if sense and sense.get("pos"):
        matched = [f for f in family if same_pos(f.get("pos"), sense["pos"])]
        family = matched or family
    if family:
        fam = "；".join(
            f"{f['pos']}. {f['word']} {f['cn']}".strip()
            for f in family[:4])
        lines.append(f"{DIM}同根{pos_note}{RESET}   {fam}")

    synonyms = entry.get("synonyms") or []
    if sense and sense.get("pos"):
        matched = [s for s in synonyms if same_pos(s.get("pos"), sense["pos"])]
        synonyms = matched or synonyms
    if synonyms:
        syns = []
        for s in synonyms[:3]:
            prefix = f"{s['pos']}. " if s.get("pos") else ""
            syns.append(prefix + " / ".join(s["words"]))
        lines.append(f"{DIM}近义{pos_note}{RESET}   " + "；".join(syns))

    forms = entry.get("forms") or {}
    if forms:
        parts = [f"{label} {forms[key]}" for key, label in FORM_ORDER
                 if forms.get(key)]
        if parts:
            lines.append(f"{DIM}变形{RESET}   " + "，".join(parts))

    all_examples = entry.get("examples") or []
    examples = all_examples
    if multi and sense and keywords:
        examples = [ex for ex in all_examples
                    if matches_any(ex.get("cn"), keywords)]
    if examples:
        ex = examples[0]
        line = f"{DIM}例句{RESET}   {ex['en']}"
        if ex.get("cn"):
            line += f"  {DIM}{ex['cn']}{RESET}"
        lines.append(line)
    elif all_examples and multi and keywords:
        lines.append(f"{DIM}例句{RESET}   （该义项暂无匹配例句）")

    if entry.get("exam"):
        ex = entry["exam"][0]
        word_level = "（词级）" if multi and sense else ""
        line = f"{DIM}真题{word_level}{RESET}   {ex['en']}"
        if ex.get("source"):
            line += f"  {DIM}({ex['source']}){RESET}"
        lines.append(line)

    all_phrases = entry.get("phrases") or []
    phrases = all_phrases
    if multi and sense and keywords:
        phrases = [p for p in all_phrases
                   if matches_any(p.get("cn"), keywords)]
    if phrases:
        ph = "；".join(f"{p['en']} {p['cn']}" for p in phrases[:3])
        lines.append(f"{DIM}短语{RESET}   {ph}")
    elif all_phrases and multi and keywords:
        lines.append(f"{DIM}短语{RESET}   （该义项暂无匹配短语）")
    freq = entry.get("freq") or {}
    if freq:
        parts = []
        if freq.get("bnc"):
            parts.append(f"BNC {freq['bnc']}")
        if freq.get("frq"):
            parts.append(f"COCA {freq['frq']}")
        if freq.get("collins"):
            parts.append(f"Collins {freq['collins']}星")
        if freq.get("oxford"):
            parts.append("Oxford 核心")
        if parts:
            lines.append(f"{DIM}词频{RESET}   " + " · ".join(parts))
    if not lines:
        lines.append(f"{DIM}暂无更多词源信息{RESET}")
    return lines


ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def display_width(text):
    text = ANSI_RE.sub("", text)
    return sum(disp_width(ch) for ch in text)


def physical_lines(text, cols):
    return max(1, (display_width(text) + cols - 1) // cols)


def show_info(term, entry, sense, senses):
    """暂停练习，显示词源/助记；j/k 切换义项；; 或 Enter 关闭。Esc 退出。"""
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns or 80
    try:
        idx = senses.index(sense) if sense is not None else None
    except ValueError:
        idx = None
    total_lines = 0

    def render():
        nonlocal total_lines
        current = senses[idx] if idx is not None else sense
        lines = info_lines(entry, current,
                           idx + 1 if idx is not None else None,
                           len(senses), senses)
        can_switch = idx is not None and len(senses) > 1
        hint_text = ("j/k 查看其他义项 · " if can_switch else "") + "再按 ; 继续拼写"
        hint = f"   {DIM}{hint_text}{RESET}"
        print()
        for line in lines:
            print("   " + line)
        sys.stdout.write(hint)
        sys.stdout.flush()
        total_lines = (1 + sum(physical_lines("   " + line, cols)
                               for line in lines)
                       + physical_lines(hint, cols))

    def clear():
        nonlocal total_lines
        sys.stdout.write(f"\r\033[{total_lines - 1}A\033[J")
        sys.stdout.flush()
        total_lines = 0

    render()
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if idx is not None and len(senses) > 1 and k == ord("j"):
            clear()
            idx = (idx + 1) % len(senses)
            render()
            continue
        if idx is not None and len(senses) > 1 and k == ord("k"):
            clear()
            idx = (idx - 1) % len(senses)
            render()
            continue
        if k == ord(";") or k in Keys.ENTER:
            # 清掉本次面板，避免多次按 ; 时内容在屏幕上重复叠加
            clear()
            return True


def color_pinyin_typed(typed, expected):
    out = []
    i = 0
    for ch in typed:
        c = ch.lower()
        if "a" <= c <= "z":
            c = "u" if c == "v" else c
            ok = i < len(expected) and c == expected[i]
            out.append(f"{GREEN if ok else RED}{ch}{RESET}")
            i += 1
        else:
            out.append(f"{DIM}{ch}{RESET}")
    return "".join(out)


def practice_pinyin(term, entry, sense):
    """第二阶段：照着逐字拼音把释义全拼打出来。打错不记录，Enter 直接过关。"""
    letters = [normalize_pinyin(py) for _, py in sense["tokens"]]
    expected = "".join(letters)
    cells = []
    for (ch, py), let in zip(sense["tokens"], letters):
        w = max(disp_width(ch), len(py), 1) + 1
        cells.append((ch, py, w))
    top = "".join(ch + " " * (w - disp_width(ch)) for ch, py, w in cells)
    bottom = "".join(py + " " * (w - len(py)) for ch, py, w in cells)

    print()
    print(f"   {DIM}{sense['pos']}.{RESET} {BOLD}{sense['cn']}{RESET}")
    print("   " + top.rstrip())
    print("   " + bottom.rstrip())

    typed = []

    def hint(nletters):
        acc = 0
        for (ch, py), let in zip(sense["tokens"], letters):
            acc += len(let)
            if nletters < acc:
                return f"{ch} {py}".strip()
        return "✓"

    def draw():
        norm = normalize_pinyin("".join(typed))
        sys.stdout.write("\r\033[K   > " + color_pinyin_typed(typed, expected)
                         + f"   {DIM}{hint(len(norm))}{RESET}")
        sys.stdout.flush()

    draw()
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k == ord(";"):
            if show_info(term, entry, sense, entry.get("senses") or []) is None:
                return None
            draw()
            continue
        if k in Keys.BACKSPACE:
            if typed:
                typed.pop()
            draw()
            continue
        if k == Keys.CTRL_U:
            typed.clear()
            draw()
            continue
        if k in Keys.ENTER:
            break
        if 32 <= k <= 126:
            typed.append(chr(k))
            draw()
            if expected and normalize_pinyin("".join(typed)) == expected:
                break
    sys.stdout.write("\r\033[K   > " + color_pinyin_typed(typed, expected)
                     + f"  {GREEN}✓{RESET}\n")
    sys.stdout.flush()
    return True


def sense_pinyin(sense):
    return normalize_pinyin("".join(py for _, py in sense.get("tokens", [])))


def fuzzy_score(typed, expected):
    """拼音模糊分：完全匹配/前缀/子串/顺序子序列/编辑相似度。"""
    if not typed or not expected:
        return 0.0
    if typed == expected:
        return 1.0
    if expected.startswith(typed):
        return 0.92 + 0.08 * len(typed) / len(expected)
    if typed in expected:
        return 0.9
    pos, matched = 0, 0
    for ch in typed:
        idx = expected.find(ch, pos)
        if idx >= 0:
            matched += 1
            pos = idx + 1
    coverage = matched / len(typed)
    ratio = difflib.SequenceMatcher(None, typed, expected).ratio()
    return max(ratio, 0.75 * coverage)


def fuzzy_matches(typed, options, threshold=0.35, margin=0.08):
    """返回仍可能匹配的选项下标及各自分数。

    - 空输入：返回空列表（调用方按「全部绿色」处理）
    - 有完全匹配：只保留完全匹配项
    - 否则保留绝对分达标、且与最佳分差距不超过 margin 的项
    """
    norm = normalize_pinyin(typed)
    if not norm:
        return [], []
    scores = [fuzzy_score(norm, sense_pinyin(sense)) for sense in options]
    if not scores:
        return [], []
    best = max(scores)
    if best <= 0:
        return [], scores
    if best >= 0.999:
        return [scores.index(best)], scores
    matches = [i for i, score in enumerate(scores)
               if score >= threshold and score >= best - margin]
    return matches, scores


def fuzzy_match_index(typed, options, threshold=0.35):
    """在仍匹配的选项里取分数最高者；没有则返回 None。"""
    matches, scores = fuzzy_matches(typed, options, threshold)
    if not matches:
        return None, 0.0
    best_idx = max(matches, key=lambda i: scores[i])
    return best_idx, scores[best_idx]


def weighted_sample(items, k):
    """items: [(item, weight)]，不放回加权抽样，返回 item 列表。"""
    pool = list(items)
    if k <= 0 or not pool:
        return []
    k = min(k, len(pool))
    out = []
    for _ in range(k):
        total = sum(w for _, w in pool)
        r = random.random() * total
        acc = 0.0
        picked = None
        for i, (entry, weight) in enumerate(pool):
            acc += weight
            if r <= acc:
                picked = pool.pop(i)[0]
                break
        out.append(picked if picked is not None else pool.pop()[0])
    return out


def build_quiz_scope(entries, stats, args, session_items=None):
    """返回 [(entry, sense, rec_or_None)]。

    普通模式：本次练习的义项 + 复习池；复习模式：只取复习池。
    """
    intervals = args.intervals
    by_word = {e["word"]: e for e in entries}
    seen = set()
    scope = []
    if session_items:
        for entry, sense in session_items:
            key = (entry["word"], sense_key(sense))
            if key in seen:
                continue
            scope.append((entry, sense, None))
            seen.add(key)

    review_items = []
    for word, recs in stats.get("review", {}).items():
        entry = by_word.get(word)
        if not entry or not isinstance(recs, dict) or "state" in recs:
            continue
        if args.level != "all" and entry.get("level") != args.level:
            continue
        sense_map = {sense_key(s): s for s in entry.get("senses", [])}
        for skey, rec in recs.items():
            sense = sense_map.get(skey)
            if not isinstance(rec, dict):
                continue
            if sense is None or (word, skey) in seen:
                continue
            state = rec.get("state")
            if state in ("new", "learning"):
                review_items.append(((entry, sense, rec), 1.0))
            elif state == "known" and is_due(rec):
                level = max(1, min(int(rec.get("level", 1)), len(intervals)))
                review_items.append(((entry, sense, rec),
                                     1.0 / intervals[level - 1]))

    cap = args.quiz_size or 0
    if cap > 0:
        if len(scope) >= cap:
            random.shuffle(scope)
            return scope[:cap]
        scope.extend(weighted_sample(review_items, cap - len(scope)))
    else:
        scope.extend(item for item, _ in review_items)
    random.shuffle(scope)
    return scope


def build_question(entry, all_options, preferred=None):
    """生成一道题：一个正确义项 + 最多 3 个来自其他词的干扰义项。

    preferred 是本次拼写时下划线标出的义项（或复习时记录的上次义项）；
    没给或找不到时退回该词的第一个义项。
    """
    senses = [s for s in entry.get("senses", []) if sense_pinyin(s)]
    if not senses:
        return None
    correct = None
    if preferred:
        for s in senses:
            if (s is preferred or
                    (s.get("cn") == preferred.get("cn") and
                     s.get("pos") == preferred.get("pos"))):
                correct = s
                break
    if correct is None:
        correct = senses[0]
    correct_cn = {s["cn"] for s in entry.get("senses", [])}
    used_cn = {correct["cn"]}
    used_py = {sense_pinyin(correct)}

    def usable(e, s):
        if e["word"] == entry["word"]:
            return False
        if s["cn"] in correct_cn or s["cn"] in used_cn:
            return False
        py = sense_pinyin(s)
        return bool(py) and py not in used_py

    def tier(e, s, n):
        if n == 0:
            return (e.get("level") == entry.get("level") and
                    same_pos(s.get("pos"), correct.get("pos")))
        if n == 1:
            return e.get("level") == entry.get("level")
        if n == 2:
            return same_pos(s.get("pos"), correct.get("pos"))
        return True

    chosen = []
    for n in range(4):
        candidates = [(e, s) for e, s in all_options
                      if usable(e, s) and tier(e, s, n)]
        random.shuffle(candidates)
        for e, s in candidates:
            if s["cn"] in used_cn or sense_pinyin(s) in used_py:
                continue
            chosen.append(s)
            used_cn.add(s["cn"])
            used_py.add(sense_pinyin(s))
            if len(chosen) == 3:
                break
        if len(chosen) == 3:
            break

    options = [correct] + chosen
    if len(options) < 2:
        return None
    random.shuffle(options)
    correct_index = next(i for i, s in enumerate(options) if s is correct)
    return {"entry": entry, "correct": correct, "options": options,
            "correct_index": correct_index}


def split_pieces(text):
    return [p.strip() for p in re.split(r"[，,]", text or "") if p.strip()]


def build_split_question(question):
    """把四选一的候选释义逐个按逗号拆成片段，返回多选题目。"""
    options = question["options"]
    correct_index = question["correct_index"]
    pieces = []
    index_by_text = {}
    for oi, sense in enumerate(options):
        is_correct = oi == correct_index
        for piece in split_pieces(sense.get("cn", "")):
            if piece in index_by_text:
                if is_correct:
                    pieces[index_by_text[piece]][1] = True
            else:
                index_by_text[piece] = len(pieces)
                pieces.append([piece, is_correct])
    if not pieces:
        return None
    random.shuffle(pieces)
    texts = [p[0] for p in pieces]
    correct = {i for i, (_text, is_corr) in enumerate(pieces) if is_corr}
    if not correct:
        return None
    return {"entry": question["entry"], "options": texts,
            "correct": correct, "source": question}


def read_quiz_input(term, option_count, state_fn, on_state_change=None,
                    on_comma=None):
    """统一的答题输入：数字直选，或输入拼音模糊匹配，再按 ;/Enter 确定。

    state_fn(text, selected) 返回 (输入行提示, 可确认的下标或 None, 渲染状态)。
    渲染状态变化时调用 on_state_change(render_state) 重绘选项。
    """
    typed = []
    selected = None
    last_state = "unset"

    def draw():
        nonlocal last_state
        text = "".join(typed)
        hint, _confirm, render_state = state_fn(text, selected)
        if on_state_change is not None and render_state != last_state:
            on_state_change(render_state)
            last_state = render_state
        suffix = f"   {hint}" if hint else ""
        sys.stdout.write("\r\033[K   > " + text + suffix)
        sys.stdout.flush()

    draw()
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if ord("1") <= k <= ord("9"):
            idx = k - ord("1")
            if idx < option_count:
                selected = idx
                typed.clear()
                draw()
            continue
        if k == ord(",") and on_comma is not None:
            selected = None
            on_comma()
            last_state = "unset"
            draw()
            continue
        if k in Keys.BACKSPACE:
            if typed:
                typed.pop()
            elif selected is not None:
                selected = None
            draw()
            continue
        if k == Keys.CTRL_U:
            typed.clear()
            selected = None
            draw()
            continue
        if k in Keys.ENTER or k == ord(";"):
            _, confirm, _ = state_fn("".join(typed), selected)
            if confirm is None:
                continue
            return "".join(typed), confirm
        if 32 <= k <= 126:
            if selected is not None:
                selected = None
            typed.append(chr(k))
            draw()


def quiz_one(term, question, idx, total):
    entry = question["entry"]
    options = question["options"]
    correct = question["correct"]
    correct_index = question["correct_index"]
    pos = f"{correct['pos']}." if correct.get("pos") else ""
    level = LEVEL_LABEL.get(entry.get("level"), "")
    phonetic = (f"  {DIM}/{entry['phonetic']}/{RESET}"
                if entry.get("phonetic") else "")
    level_tag = f"  {DIM}{level}{RESET}" if level else ""
    header = (f"{DIM}[{idx}/{total}]{RESET} {BOLD}{entry['word']}{RESET}"
              f"{phonetic}{level_tag}  {DIM}{pos}{RESET}")
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns or 80
    block_lines = 0

    def render(note="", matched=None, selected=None):
        nonlocal block_lines
        hint = (f"   {DIM}输入拼音模糊筛选（可拼错/少拼），或按 1-{len(options)} "
                f"直接选；再按 ; 或 Enter 确定；, 打乱；Esc 退出{note}{RESET}")
        print(header)
        option_texts = []
        for i, s in enumerate(options, 1):
            oi = i - 1
            if selected is not None:
                color = (BOLD + UNDERLINE + GREEN if oi == selected else DIM)
            elif matched is None:
                color = GREEN
            elif oi in matched:
                color = BOLD + UNDERLINE + GREEN if len(matched) == 1 else GREEN
            else:
                color = DIM
            line = f"   {color}{i}. {s['cn']}{RESET}"
            print(line)
            option_texts.append(line)
        print(hint)
        block_lines = (physical_lines(header, cols)
                       + sum(physical_lines(line, cols)
                             for line in option_texts)
                       + physical_lines(hint, cols))
        return block_lines

    print()
    render()

    def state_fn(text, selected):
        if selected is not None:
            return (f"{GREEN}已选：{selected + 1}，按 ; 或 Enter 确定{RESET}",
                    selected, ("selected", selected))
        norm = normalize_pinyin(text)
        if not norm:
            return "", None, ("matches", None)
        matches, _scores = fuzzy_matches(text, options)
        if len(matches) == 1:
            return (f"{GREEN}唯一匹配：{matches[0] + 1}，"
                    f"按 ; 或 Enter 确定{RESET}",
                    matches[0], ("matches", tuple(matches)))
        if matches:
            return (f"{DIM}匹配 {len(matches)} 项，继续输入…（也可按数字选）"
                    f"{RESET}", None, ("matches", tuple(matches)))
        return (f"{RED}无匹配，Backspace 修改；也可按数字选{RESET}",
                None, ("matches", ()))

    def show_state(state):
        nonlocal block_lines
        sys.stdout.write(f"\r\033[{block_lines}A\033[J")
        sys.stdout.flush()
        kind, value = state
        if kind == "selected":
            block_lines = render(selected=value)
        else:
            block_lines = render(matched=value)

    def reshuffle():
        nonlocal correct_index
        sys.stdout.write(f"\r\033[{block_lines}A\033[J")
        sys.stdout.flush()
        random.shuffle(options)
        correct_index = next(i for i, s in enumerate(options) if s is correct)
        render("（已打乱）")

    result = read_quiz_input(term, len(options), state_fn,
                             on_state_change=show_state,
                             on_comma=reshuffle)
    if result is None:
        return None
    text, chosen = result
    ok = chosen == correct_index

    if ok:
        print(f"   {GREEN}✓ 正确{RESET}   {pos} {correct['cn']}")
    else:
        print(f"   {RED}✗ 错误{RESET}   正确：{pos} {correct['cn']}")
        answer_py = " ".join(py for _, py in correct.get("tokens", []) if py)
        print(f"   {DIM}正确拼音：{answer_py}{RESET}")
        if text:
            print(f"   {DIM}你输入的是：{text}{RESET}")
        if chosen is not None:
            print(f"   {DIM}你选择的是：{options[chosen]['cn']}{RESET}")
    if len(entry.get("senses", [])) > 1:
        print(f"   {DIM}全部释义：{meaning(entry)}{RESET}")
    print(f"   {DIM}按 ; 或 Enter 继续{RESET}")
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k in Keys.ENTER or k == ord(";"):
            return ok


def quiz_one_split(term, sq, idx, total):
    """拆分多选：按逗号拆开的片段中，选出属于正确释义的全部片段。"""
    entry = sq["entry"]
    options = sq["options"]
    correct = sq["correct"]
    correct_sense = sq["source"]["correct"]
    pos = f"{correct_sense['pos']}." if correct_sense.get("pos") else ""
    level = LEVEL_LABEL.get(entry.get("level"), "")
    phonetic = (f"  {DIM}/{entry['phonetic']}/{RESET}"
                if entry.get("phonetic") else "")
    level_tag = f"  {DIM}{level}{RESET}" if level else ""
    header = (f"{DIM}[{idx}/{total}]{RESET} {BOLD}{entry['word']}{RESET}"
              f"{phonetic}{level_tag}  {DIM}{pos}{RESET}")
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns or 80
    block_lines = 0
    cursor = 0
    selected = set()

    def render():
        nonlocal block_lines
        hint = (f"   {DIM}j/k 移动，空格或数字 1-9 切换，; 或 Enter 提交，"
                f"Esc 退出{RESET}")
        print(header)
        option_texts = []
        for i, text in enumerate(options, 1):
            oi = i - 1
            marker = ">" if oi == cursor else " "
            check = "x" if oi in selected else " "
            color = BOLD + UNDERLINE + GREEN if oi in selected else GREEN
            line = f" {marker} {i:>2}. [{check}] {color}{text}{RESET}"
            print(line)
            option_texts.append(line)
        print(hint)
        block_lines = (physical_lines(header, cols)
                       + sum(physical_lines(line, cols)
                             for line in option_texts)
                       + physical_lines(hint, cols))
        return block_lines

    def draw_input():
        items = sorted(selected)
        if not items:
            text = "未选择"
        else:
            shown = ", ".join(str(i + 1) for i in items[:10])
            text = shown + (f" 等 {len(items)} 项" if len(items) > 10 else "")
        sys.stdout.write("\r\033[K   > 已选：" + text)
        sys.stdout.flush()

    def redraw():
        sys.stdout.write(f"\r\033[{block_lines}A\033[J")
        sys.stdout.flush()
        render()
        draw_input()

    print()
    block_lines = render()
    draw_input()

    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k == ord("j"):
            cursor = (cursor + 1) % len(options)
            redraw()
            continue
        if k == ord("k"):
            cursor = (cursor - 1) % len(options)
            redraw()
            continue
        if k == ord(" "):
            if cursor in selected:
                selected.remove(cursor)
            else:
                selected.add(cursor)
            redraw()
            continue
        if ord("1") <= k <= ord("9"):
            oi = k - ord("1")
            if oi < len(options):
                if oi in selected:
                    selected.remove(oi)
                else:
                    selected.add(oi)
                redraw()
            continue
        if k == Keys.CTRL_U:
            selected.clear()
            redraw()
            continue
        if k in Keys.ENTER or k == ord(";"):
            if not selected:
                continue
            break

    ok = selected == correct
    if ok:
        chosen = " / ".join(options[i] for i in sorted(selected))
        print(f"   {GREEN}✓ 正确{RESET}   {chosen}")
    else:
        correct_text = " / ".join(options[i] for i in sorted(correct))
        chosen_text = (" / ".join(options[i] for i in sorted(selected))
                       or "（空）")
        missing = sorted(correct - selected)
        extra = sorted(selected - correct)
        print(f"   {RED}✗ 错误{RESET}")
        print(f"   {DIM}正确：{correct_text}{RESET}")
        print(f"   {DIM}你选了：{chosen_text}{RESET}")
        if missing:
            print(f"   {DIM}漏选：{' / '.join(options[i] for i in missing)}"
                  f"{RESET}")
        if extra:
            print(f"   {DIM}多选：{' / '.join(options[i] for i in extra)}"
                  f"{RESET}")
    if len(entry.get("senses", [])) > 1:
        print(f"   {DIM}全部释义：{meaning(entry)}{RESET}")
    print(f"   {DIM}按 ; 或 Enter 继续{RESET}")
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k in Keys.ENTER or k == ord(";"):
            return ok


def review_counts(stats):
    """按义项统计：未测 / 生词 / 熟词 / 待复习 / 覆盖词数。"""
    new_count = 0
    learning = 0
    known = 0
    due = 0
    levels = {}
    words = set()
    for word, recs in stats.get("review", {}).items():
        if not isinstance(recs, dict) or "state" in recs:
            continue
        for rec in recs.values():
            if not isinstance(rec, dict):
                continue
            words.add(word)
            state = rec.get("state")
            if state == "new":
                new_count += 1
                due += 1
            elif state == "learning":
                learning += 1
                due += 1
            elif state == "known":
                known += 1
                lvl = int(rec.get("level", 1))
                levels[lvl] = levels.get(lvl, 0) + 1
                if is_due(rec):
                    due += 1
    return new_count, learning, known, levels, due, len(words)


def run_quiz(args, kind="review", session_words=None, session_senses=None):
    entries = load_words(args.file)
    stats = load_stats(args.stats_file)
    session_senses = session_senses or {}
    normalize_review(stats, entries)

    session_items = []
    if session_words:
        for entry in session_words:
            sense = session_senses.get(entry["word"])
            if sense is None:
                senses = entry.get("senses") or []
                sense = senses[0] if senses else None
            if sense is not None:
                session_items.append((entry, sense))

    scope = build_quiz_scope(entries, stats, args, session_items)
    if not scope:
        print("没有需要检测的单词（生词表为空，且今天没有到期熟词）。")
        return

    all_options = [(e, s) for e in entries for s in e.get("senses", [])]
    label = "四选一" if args.check == "single" else "拆分多选"
    print(f"\n{BOLD}──── 释义检测（{label}）────{RESET}")
    print(f"{DIM}本轮 {len(scope)} 个义项；首次结果决定各义项的状态，"
          f"错题随后重刷。{RESET}")

    def ask(term, entry, sense, i, total):
        question = build_question(entry, all_options, sense)
        if question is None:
            return None, None
        if args.check == "split":
            sq = build_split_question(question)
            if sq is None:
                return None, None
            return quiz_one_split(term, sq, i, total), question
        return quiz_one(term, question, i, total), question

    first_total = first_correct = 0
    first_wrong = 0
    wrong = []
    quit_early = False
    retry_rounds = retry_correct = retry_wrong = 0

    with Terminal() as term:
        try:
            for i, (entry, sense, _rec) in enumerate(scope, 1):
                result, question = ask(term, entry, sense, i, len(scope))
                if question is None:
                    continue
                if result is None:
                    quit_early = True
                    break
                first_total += 1
                mark_review_result(stats, entry, question["correct"], result,
                                   args.intervals)
                if result:
                    first_correct += 1
                else:
                    wrong.append((entry, question["correct"]))
            first_wrong = len(wrong)

            if not args.no_retry and wrong and not quit_early:
                while wrong:
                    retry_rounds += 1
                    random.shuffle(wrong)
                    next_wrong = []
                    print(f"\n{BOLD}──── 错题重刷 第 {retry_rounds} 轮："
                          f"{len(wrong)} 个义项 ────{RESET}")
                    print(f"{DIM}重刷只作巩固，不改变首次检测决定的状态。{RESET}")
                    for j, (entry, sense) in enumerate(wrong, 1):
                        result, question = ask(term, entry, sense,
                                               j, len(wrong))
                        if question is None:
                            continue
                        if result is None:
                            quit_early = True
                            break
                        if result:
                            retry_correct += 1
                        else:
                            retry_wrong += 1
                            next_wrong.append((entry, question["correct"]))
                    if quit_early:
                        break
                    wrong = next_wrong
        finally:
            termios.tcsetattr(term.fd, termios.TCSADRAIN, term.old)

    new_count, learning, known, levels, due, words = review_counts(stats)
    level_text = " / ".join(f"L{k} {v}" for k, v in sorted(levels.items()))
    print(f"\n{BOLD}──── 检测结果 ────{RESET}")
    print(f"首次检测：{first_total} 个义项，正确 {first_correct}，"
          f"错误 {first_wrong}")
    if retry_rounds:
        print(f"错题重刷：{retry_rounds} 轮，答对 {retry_correct}，"
              f"仍错 {retry_wrong}")
    print(f"当前：未测 {new_count} 义项 · 生词 {learning} · 熟词 {known}"
          f"{('（' + level_text + '）') if level_text else ''} · "
          f"待复习 {due} · 覆盖 {words} 词")
    if quit_early:
        print(f"{DIM}中途退出，剩余义项未检测。{RESET}")

    stats.setdefault("quiz_sessions", []).append({
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "kind": kind,
        "quiz": "mixed",
        "check": args.check,
        "scope": len(scope),
        "total": first_total,
        "correct": first_correct,
        "wrong": first_wrong,
        "retry_rounds": retry_rounds,
        "retry_correct": retry_correct,
        "retry_wrong": retry_wrong,
        "quit_early": quit_early,
    })
    stats["quiz_sessions"] = stats["quiz_sessions"][-500:]
    save_stats(args.stats_file, stats)
    print(f"{DIM}进度已保存到 {args.stats_file}{RESET}")


class Terminal:
    def __init__(self):
        self.fd = sys.stdin.fileno()

    def __enter__(self):
        if not os.isatty(self.fd):
            sys.exit("vocabtype 需要交互式终端运行。")
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        return False

    def key(self):
        return os.read(self.fd, 1)[0]


def color_typed(typed, target):
    out = []
    for i, ch in enumerate(typed):
        if i < len(target) and ch != target[i]:
            out.append(f"{RED}{ch}{RESET}")
        else:
            out.append(f"{GREEN}{ch}{RESET}")
    return "".join(out)


def practice_one(term, entry, idx, total, mode, sense=None):
    """练习一个词。

    返回 (ok, elapsed, keystrokes, errors, start) 或 None 表示退出。
    start 是这个词第一次击键的时刻。
    """
    target = entry.get("typing") or entry["word"]
    display = entry["word"]
    level = LEVEL_LABEL.get(entry.get("level"))
    level_tag = f"  {DIM}{level}{RESET}" if level else ""
    phonetic = (f"  {DIM}/{entry['phonetic']}/{RESET}"
                if entry.get("phonetic") else "")
    shown = meaning_with_mark(entry, sense) if mode == "show" else meaning(entry)
    if mode == "show":
        head = (f"{DIM}[{idx}/{total}]{RESET} {BOLD}{display}{RESET}"
                f"{phonetic}{level_tag}  {shown}")
    else:
        tag = f"{DIM}{level}{RESET} " if level else ""
        head = f"{DIM}[{idx}/{total}]{RESET} {tag}{BOLD}{shown}{RESET}"

    print("\r\033[K" + head)
    typed = []
    keystrokes = 0
    errors = 0
    start = None
    paused = 0.0

    def draw():
        sys.stdout.write("\r\033[K   > "
                         + (color_typed(typed, target) if mode == "show"
                            else "".join(typed)))
        sys.stdout.flush()

    draw()
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k == ord(";"):
            pause_start = time.monotonic()
            if show_info(term, entry, sense, entry.get("senses") or []) is None:
                return None
            if start is not None:
                paused += time.monotonic() - pause_start
            draw()
            continue
        if k in Keys.BACKSPACE:
            if typed:
                typed.pop()
            draw()
            continue
        if k == Keys.CTRL_U:
            typed.clear()
            draw()
            continue
        if k in Keys.ENTER:
            break
        if 32 <= k <= 126:
            if start is None:
                start = time.monotonic()
            keystrokes += 1
            pos = len(typed)
            if pos >= len(target) or chr(k) != target[pos]:
                errors += 1
            typed.append(chr(k))
            draw()
            if mode == "show" and "".join(typed) == target:
                break

    elapsed = (time.monotonic() - start - paused) if start else 0.0
    answer = "".join(typed)
    ok = answer == target
    if ok:
        sys.stdout.write(f"\r\033[K   > {GREEN}{answer}{RESET}\n")
    else:
        sys.stdout.write(f"\r\033[K   > {RED}{answer}{RESET}"
                         f"  →  {GREEN}{BOLD}{display}{RESET}"
                         f"  {DIM}{shown}{RESET}\n")
    sys.stdout.flush()
    return ok, elapsed, keystrokes, errors, start


def run(args):
    entries = load_words(args.file)
    stats = load_stats(args.stats_file)
    words = select_words(entries, stats, args)
    if not words:
        sys.exit("没有符合条件的单词。")

    queue = [[e, False] for e in words]
    total_planned = len(words)
    done = 0
    reviews = 0
    ok_words = 0
    correct_chars = 0
    total_keystrokes = 0
    total_errors = 0
    typing_seconds = 0.0
    start_time = None
    missed = []
    pinyin_count = 0
    session_senses = {}
    i = 0
    quit_early = False

    with Terminal() as term:
        try:
            while i < len(queue):
                entry, is_review = queue[i]
                sense = (pick_sense(entry)
                         if args.mode == "show" and not args.no_pinyin else None)
                if sense is not None:
                    session_senses[entry["word"]] = sense
                result = practice_one(term, entry, min(done + 1, total_planned),
                                      total_planned, args.mode, sense)
                if result is None:
                    quit_early = True
                    break
                ok, elapsed, keys, errors, word_start = result
                if start_time is None and word_start is not None:
                    start_time = word_start
                typing_seconds += elapsed
                done += 1
                if is_review:
                    reviews += 1
                total_keystrokes += keys
                total_errors += errors

                s = word_stat(stats, entry["word"])
                s["n"] += 1
                s["t"] += elapsed
                if ok:
                    s["r"] += 1
                    ok_words += 1
                    correct_chars += len(entry.get("typing") or entry["word"])
                else:
                    s["w"] += 1
                    missed.append(entry)
                    if not is_review and not args.no_requeue:
                        queue.insert(min(i + 6, len(queue)), [entry, True])

                # 标准模式的第二阶段：按逐字拼音把释义打出来（不计入错词和准确率）
                if ok and sense:
                    if practice_pinyin(term, entry, sense) is None:
                        quit_early = True
                        break
                    pinyin_count += 1
                i += 1
        finally:
            termios.tcsetattr(term.fd, termios.TCSADRAIN, term.old)

    if done == 0:
        print("\n已退出，本次没有练习记录。")
        return [], True, 0, {}

    elapsed_total = (time.monotonic() - start_time) if start_time else 0.0
    minutes = max(typing_seconds / 60.0, 1e-9)
    wpm = (correct_chars / 5.0) / minutes
    acc = ((total_keystrokes - total_errors) / total_keystrokes * 100
           if total_keystrokes else 100.0)

    print(f"\n{BOLD}──── 本次结果 ────{RESET}")
    print(f"完成 {done} 次（复习 {reviews} 次）  正确 {ok_words}  错误 {done - ok_words}")
    print(f"击键准确率 {acc:.1f}%    速度 {wpm:.0f} WPM    用时 {fmt_time(elapsed_total)}")
    if args.mode == "show" and not args.no_pinyin:
        print(f"释义拼音完成 {pinyin_count} 次（不计入准确率和错词）")
    if missed:
        seen = []
        for e in missed:
            if e["word"] not in seen:
                seen.append(e["word"])
        print(f"{RED}错词{RESET}: " + ", ".join(seen))

    stats["sessions"].append({
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "mode": args.mode,
        "done": done,
        "reviews": reviews,
        "pinyin": pinyin_count,
        "wpm": round(wpm, 1),
        "accuracy": round(acc, 1),
        "quit_early": quit_early,
    })
    stats["sessions"] = stats["sessions"][-500:]
    save_stats(args.stats_file, stats)
    print(f"{DIM}进度已保存到 {args.stats_file}{RESET}")
    return words, quit_early, done, session_senses


def show_stats(args):
    entries = load_words(args.file)
    stats = load_stats(args.stats_file)
    ws = stats["words"]
    practiced = sum(1 for w, s in ws.items() if s.get("n"))
    right = sum(s.get("r", 0) for s in ws.values())
    wrong = sum(s.get("w", 0) for s in ws.values())
    print(f"已练单词: {practiced} / {len(entries)}")
    counts = {k: sum(1 for e in entries if e.get("level") == k)
              for k in ("cet6", "cet4", "extra")}
    print(f"词库: CET6 {counts['cet6']} · CET4 {counts['cet4']} · "
          f"扩展 {counts['extra']}")
    normalize_review(stats, entries)
    new_count, learning, known, levels, due, words = review_counts(stats)
    level_text = " / ".join(f"L{k} {v}" for k, v in sorted(levels.items()))
    print(f"复习条目: 未测 {new_count} · 生词 {learning} · 熟词 {known}"
          f"{('（' + level_text + '）') if level_text else ''} · "
          f"待复习 {due} · 覆盖 {words} 词")
    if right + wrong:
        print(f"累计正确 {right}  错误 {wrong}  准确率 {right / (right + wrong) * 100:.1f}%")
    weak = sorted(
        ((s["w"] / s["n"], s["w"], w) for w, s in ws.items() if s.get("n")),
        reverse=True,
    )[:10]
    if weak:
        print("最需要复习:")
        lookup = {e["word"]: e for e in entries}
        for rate, w, word in weak:
            if w == 0:
                break
            e = lookup.get(word, {"word": word, "trans": []})
            print(f"  {word:<20} 错 {w}/{ws[word]['n']}  {DIM}{meaning(e)}{RESET}")
    sessions = stats.get("sessions", [])
    if sessions:
        recent = sessions[-5:]
        print("最近记录:")
        for s in recent:
            print(f"  {s.get('time', '')}  {s.get('mode', ''):<6} "
                  f"{s.get('done', 0):>3} 词  "
                  f"{s.get('wpm', 0):>5.0f} WPM  "
                  f"{s.get('accuracy', 0):>5.1f}%")
    quiz_sessions = stats.get("quiz_sessions", [])
    if quiz_sessions:
        print("最近释义检测:")
        for q in quiz_sessions[-5:]:
            mode = q.get("check") or q.get("quiz", "")
            print(f"  {q.get('time', '')}  {q.get('kind', ''):<6} "
                  f"{mode:<6} {q.get('total', 0):>3} 词  "
                  f"正确 {q.get('correct', 0)}  错误 {q.get('wrong', 0)}")


class ChineseArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        text = super().format_help()
        return (text.replace("usage:", "用法:")
                    .replace("options:", "选项:")
                    .replace("positional arguments:", "位置参数:"))


def main():
    epilog = """\
示例:
  vocabtype                         普通模式：拼写 + 全拼释义 + 释义检测
  vocabtype --no-quiz               只做拼写练习
  vocabtype --check split           释义检测改为拆分多选
  vocabtype --review                复习模式：检测生词和到期熟词
  vocabtype --review --quiz-size 50 本次最多检测 50 个
  vocabtype --stats                 查看复习进度
"""
    p = ChineseArgumentParser(
        description="CET-6 单词 + 打字练习 + 释义检测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog, add_help=False)
    p.add_argument("-h", "--help", action="help", help="显示本帮助并退出")
    p.add_argument("-f", "--file", default=DEFAULT_DATA, help="单词数据文件")
    p.add_argument("-n", "--count", type=int, default=30,
                   help="普通模式本次拼写练习词数（默认 30）")
    p.add_argument("--mode", choices=["show", "recall"], default="show",
                   help="show=显示单词照着打（含全拼释义阶段）；"
                        "recall=只看释义拼写")
    p.add_argument("--no-pinyin", action="store_true",
                   help="标准模式不进行第二阶段的释义全拼")
    p.add_argument("--level", choices=["all", "cet6", "cet4", "extra"],
                   default="all",
                   help="按官方级别筛选：all=全部（默认），cet6=六级★词，"
                        "cet4=四级词，extra=大纲外扩展词")
    p.add_argument("--weak", action="store_true", help="优先练错误率最高的词")
    p.add_argument("--new", action="store_true", help="只练尚未练过的词")
    p.add_argument("--no-requeue", action="store_true", help="错词不在本次重复")
    p.add_argument("--review", action="store_true",
                   help="复习模式：只做释义检测，抽取生词和今天到期的熟词")
    p.add_argument("--check", choices=["single", "split"], default="single",
                   help="检查方式：single=四选一（默认）；split=把候选释义"
                        "按逗号拆开，多选全部正确片段")
    p.add_argument("--no-quiz", action="store_true",
                   help="普通模式结束后不做释义检测")
    p.add_argument("--quiz-size", type=int, default=0,
                   help="释义检测最多几题；0=全部（默认）")
    p.add_argument("--no-retry", action="store_true",
                   help="首次检测答错的词不在本轮重刷")
    p.add_argument("--intervals", default=",".join(map(str, DEFAULT_INTERVALS)),
                   help="熟词各级复习间隔（天），逗号分隔；"
                        "默认 1,3,7,15,30")
    p.add_argument("--stats", action="store_true", help="显示累计进度")
    p.add_argument("--stats-file", default=DEFAULT_STATS,
                   help="进度保存路径")
    p.add_argument("--seed", type=int, default=None, help="固定随机种子")
    args = p.parse_args()
    args.intervals = parse_intervals(args.intervals)
    if args.quiz_size < 0:
        sys.exit("--quiz-size 不能为负数")
    if args.seed is not None:
        random.seed(args.seed)
    if args.stats:
        show_stats(args)
        return
    if args.review:
        if args.no_quiz:
            sys.exit("复习模式本身就是释义检测，不能与 --no-quiz 同用。")
        if args.new or args.weak:
            sys.exit("复习模式不支持 --new / --weak。")
        run_quiz(args, kind="review")
        return
    words, quit_early, done, session_senses = run(args)
    if args.no_quiz:
        return
    if quit_early:
        print(f"{DIM}已退出，本次未进行释义检测。{RESET}")
        return
    if done:
        run_quiz(args, kind="normal", session_words=words,
                 session_senses=session_senses)


if __name__ == "__main__":
    main()
