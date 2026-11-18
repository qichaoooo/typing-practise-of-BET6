#!/usr/bin/env python3
"""vocabtype — 边背 CET-6 单词边练打字。

拼写模式：
  show   （默认）两阶段：先照着英文单词拼写；再从该词的义项中按词频顺序
         加权抽一条，看着逐字拼音把释义全拼打出来（打错不计入错词）
  recall 只显示中文释义，凭记忆拼出英文单词

释义检测（背单词模式）：
  普通模式拼写结束后自动检测释义；首答答对进熟词表，答错进生词表。
  复习按义项进行：只登记实际检测过的义项，没练过的释义不会进入复习。
  检测默认 --check split：候选释义按逗号拆开，多选全部正确片段。
  可选 --check single：四选一，按数字或输入拼音模糊筛选，再按 ;/Enter 确定。
  --review 只复习生词义项和今天到期的熟词义项；--no-quiz 跳过检测。

用法示例：
  python3 -I vocabtype.py                 # 拼写 + 拆分多选检测
  python3 -I vocabtype.py --check single  # 释义检测改用四选一
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
YELLOW = "\033[33m"
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
    CTRL_D = 0x04
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


def ensure_word_review(stats, entry, day=None):
    """把 review[word] 规范为 {义项 key: 记录}。

    只为已经检测过的义项建记录；没练过的义项不预先登记，避免复习模式
    抽到「学过单词但没学过的释义」。
    """
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
    return recs


def normalize_review(stats, entries):
    """迁移旧进度，并清掉从未检测过的义项记录。

    旧版本会把一个词的所有义项都登记为 new；这些义项从未练过，不应进入
    复习池。同时丢弃词条释义已变化留下的过期记录。
    """
    by_word = {e["word"]: e for e in entries}
    review = stats.get("review", {})
    for word in list(review):
        entry = by_word.get(word)
        if not entry:
            continue
        recs = ensure_word_review(stats, entry)
        valid = {sense_key(s) for s in entry.get("senses", [])}
        for key in list(recs):
            rec = recs[key]
            if key not in valid or (isinstance(rec, dict)
                                    and rec.get("state") == "new"):
                del recs[key]
        if not recs:
            del review[word]


def register_encountered(stats, entry, sense, day=None):
    """开始练习一个义项时先记入生词表。

    已有生词/熟词记录保持不变（不能因为重新练一次就降级）；
    没有记录或只有旧版 new 记录时，建立 learning 记录。
    """
    if sense is None:
        return False
    day = day or date.today()
    recs = ensure_word_review(stats, entry, day=day)
    key = sense_key(sense)
    rec = recs.get(key)
    if isinstance(rec, dict) and rec.get("state") in ("learning", "known"):
        return False
    rec = new_review_record(day)
    rec["state"] = "learning"
    rec["due"] = day.isoformat()
    recs[key] = rec
    return True


def demote_unfinished(stats, items, day=None):
    """检测提前退出时处理未作答的义项。

    到期的熟词保持不变；其余未作答的义项进入生词表（不增加 wrong 次数）。
    """
    day = day or date.today()
    for entry, sense in items:
        recs = ensure_word_review(stats, entry, day=day)
        key = sense_key(sense)
        rec = recs.get(key)
        if (isinstance(rec, dict) and rec.get("state") == "known"
                and is_due(rec, day)):
            continue
        if rec is None:
            rec = recs[key] = new_review_record(day)
        rec["state"] = "learning"
        rec["level"] = 0
        rec["due"] = day.isoformat()
        rec["streak"] = 0


def mark_review_result(stats, entry, sense, correct, intervals, day=None,
                       early=False):
    """首次检测结果决定该义项的状态；重抽不调用本函数。

    early=True 用于熟词加练：答对只巩固，不升级、不改到期日；答错仍回生词。
    """
    day = day or date.today()
    recs = ensure_word_review(stats, entry, day=day)
    rec = recs.setdefault(sense_key(sense), new_review_record(day))
    rec["last"] = day.isoformat()
    if correct:
        rec["correct"] = rec.get("correct", 0) + 1
        rec["streak"] = rec.get("streak", 0) + 1
        if early:
            return rec
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


def sense_is_known(stats, entry, sense):
    """该义项是否已经在熟词表（按义项判断，不看同词其他释义）。"""
    recs = stats.get("review", {}).get(entry["word"])
    if not isinstance(recs, dict) or "state" in recs:
        return False
    rec = recs.get(sense_key(sense))
    return isinstance(rec, dict) and rec.get("state") == "known"


def select_items(entries, stats, args):
    """返回本次要练的 [(entry, sense)]，按释义抽取。

    默认排除已经变熟的义项；--all-senses 时从全部词库抽取（包含熟词）。
    抽取单位是义项，所以同一个词的不同释义可能各占一次。每个词内部仍按
    义项顺序做 Zipf 加权（第 1 个 1、第 2 个 1/2……），再归一化，使每个词
    的总权重为 1，近似原来的「先均匀选词，再按频率选义项」。
    """
    n = args.count
    include_known = getattr(args, "all_senses", False)
    level_pool = [e for e in entries
                  if args.level == "all" or e.get("level") == args.level]
    if not level_pool:
        sys.exit(f"没有 level={args.level} 的单词。")

    def weighted_senses(entry):
        senses = entry.get("senses") or []
        picked = []
        for idx, s in enumerate(senses):
            if include_known or not sense_is_known(stats, entry, s):
                picked.append((s, 1.0 / (idx + 1)))
        total = sum(w for _, w in picked)
        if not total:
            return []
        return [((entry, s), w / total) for s, w in picked]

    if args.weak:
        practiced = []
        for e in level_pool:
            s = stats["words"].get(e["word"])
            if s and s["n"] > 0:
                rate = (s["w"] + 0.5) / (s["n"] + 1.0)
                practiced.append((rate, s["w"], e))
        practiced.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen = []
        chosen_words = set()
        for _rate, _wrong, e in practiced:
            if len(chosen) >= n:
                break
            items = weighted_senses(e)
            if items:
                chosen.append(weighted_sample(items, 1)[0])
                chosen_words.add(e["word"])
        rest = [item for e in level_pool if e["word"] not in chosen_words
                for item in weighted_senses(e)]
        return chosen + weighted_sample(rest, max(0, n - len(chosen)))

    if args.new:
        pool = [item for e in level_pool
                if stats["words"].get(e["word"], {}).get("n", 0) == 0
                for item in weighted_senses(e)]
        if not pool:
            pool = [item for e in level_pool for item in weighted_senses(e)]
    else:
        pool = [item for e in level_pool for item in weighted_senses(e)]
    return weighted_sample(pool, n)


def fmt_time(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def normalize_pinyin(text):
    """比较用：只保留 a-z，v 视为 u（nve/nue 都接受）。"""
    return "".join("u" if ch == "v" else ch for ch in text.lower()
                   if "a" <= ch <= "z")


def disp_width(ch):
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


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


def truncate_display(text, width):
    """按终端显示宽度截断纯文本（超出部分用省略号）。"""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    out = []
    used = 0
    limit = max(1, width - 1)
    for ch in text:
        cw = disp_width(ch)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def pad_display(text, width):
    return text + " " * max(0, width - display_width(text))


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

    def clear(to_input=False):
        nonlocal total_lines
        seq = f"\r\033[{total_lines - 1}A\033[J"
        if to_input:
            seq += "\033[A"  # 回到面板上方那一行输入行
        sys.stdout.write(seq)
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
            # 清掉本次面板并回到输入行，避免内容叠加、光标错位
            clear(to_input=True)
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
    """第二阶段：照着逐字拼音把释义全拼打出来。

    对照行（汉字行 + 拼音行）按输入进度变色：已正确输入的字母/已完成字符
    变绿，输入错误的字母/字符变红，未输入保持默认。打错不记录，Enter 过关。
    """
    letters = [normalize_pinyin(py) for _, py in sense["tokens"]]
    expected = "".join(letters)
    cells = []
    for (ch, py), let in zip(sense["tokens"], letters):
        w = max(disp_width(ch), len(py), 1) + 1
        cells.append((ch, py, w))
    typed = []
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns or 80
    header_lines = 0
    input_lines = 0
    first = True

    def norm_typed():
        return normalize_pinyin("".join(typed))

    def hint(nletters):
        acc = 0
        for (ch, py), let in zip(sense["tokens"], letters):
            acc += len(let)
            if nletters < acc:
                return f"{ch} {py}".strip()
        return "✓"

    def colored_guide(norm):
        top_parts, bottom_parts = [], []
        pos = 0
        for (ch, py, w), let in zip(cells, letters):
            if let:
                span = norm[pos:pos + len(let)]
                if span and any(span[i] != let[i] for i in range(len(span))):
                    ch_color = RED
                elif span and len(span) == len(let):
                    ch_color = GREEN
                else:
                    ch_color = ""
            else:
                ch_color = ""
            top_ch = f"{ch_color}{ch}{RESET}" if ch_color else ch
            top_parts.append(top_ch + " " * (w - disp_width(ch)))

            py_parts = []
            for j, letter in enumerate(py):
                if pos + j < len(norm):
                    exp = let[j] if j < len(let) else ""
                    color = GREEN if norm[pos + j] == exp else RED
                    py_parts.append(f"{color}{letter}{RESET}")
                else:
                    py_parts.append(letter)
            py_text = "".join(py_parts)
            bottom_parts.append(py_text + " " * (w - len(py)))
            pos += len(let)
        return "".join(top_parts), "".join(bottom_parts)

    def build_header():
        norm = norm_typed()
        top, bottom = colored_guide(norm)
        sense_line = f"   {DIM}{sense['pos']}.{RESET} {BOLD}{sense['cn']}{RESET}"
        top_line = "   " + top.rstrip()
        bottom_line = "   " + bottom.rstrip()
        header = "\n" + sense_line + "\n" + top_line + "\n" + bottom_line
        return header, sense_line, top_line, bottom_line

    def input_text():
        norm = norm_typed()
        return ("   > " + color_pinyin_typed(typed, expected)
                + f"   {DIM}{hint(len(norm))}{RESET}")

    def refresh():
        nonlocal header_lines, input_lines, first
        if not first:
            up = (input_lines - 1) + header_lines
            sys.stdout.write(f"\r\033[{up}A\033[J")
        header, sense_line, top_line, bottom_line = build_header()
        sys.stdout.write(header + "\n")
        text = input_text()
        sys.stdout.write(text)
        sys.stdout.flush()
        header_lines = (1 + physical_lines(sense_line, cols)
                        + physical_lines(top_line, cols)
                        + physical_lines(bottom_line, cols))
        input_lines = physical_lines(text, cols)
        first = False

    refresh()
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
            refresh()
            continue
        if k in Keys.BACKSPACE:
            if typed:
                typed.pop()
            refresh()
            continue
        if k == Keys.CTRL_U:
            typed.clear()
            refresh()
            continue
        if k in Keys.ENTER:
            break
        if 32 <= k <= 126:
            typed.append(chr(k))
            refresh()
            if expected and norm_typed() == expected:
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
    熟词加练（--review-known N）：从全部熟词义项中均匀抽 N 个，忽略到期日。
    """
    intervals = args.intervals
    by_word = {e["word"]: e for e in entries}
    n_known = getattr(args, "review_known", None)
    if n_known:
        pool = []
        for word, recs in stats.get("review", {}).items():
            entry = by_word.get(word)
            if not entry or not isinstance(recs, dict) or "state" in recs:
                continue
            if args.level != "all" and entry.get("level") != args.level:
                continue
            sense_map = {sense_key(s): s for s in entry.get("senses", [])}
            for skey, rec in recs.items():
                sense = sense_map.get(skey)
                if (sense is not None and isinstance(rec, dict)
                        and rec.get("state") == "known"):
                    pool.append((entry, sense, rec))
        return random.sample(pool, min(n_known, len(pool)))

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
            if state == "learning":
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


def split_sense_pieces(sense):
    """按逗号拆分义项，同时保留每个片段的逐字拼音 tokens。"""
    cn = sense.get("cn", "")
    tokens = sense.get("tokens") or []
    if not tokens or "".join(ch for ch, _ in tokens) != cn:
        return [{"cn": p, "pos": sense.get("pos", ""),
                 "tokens": [[ch, ""] for ch in p]}
                for p in split_pieces(cn)]
    pieces = []
    chars, buf = [], []
    for ch, py in tokens:
        if ch in "，,":
            text = "".join(chars).strip()
            if text:
                pieces.append({"cn": text, "pos": sense.get("pos", ""),
                               "tokens": buf})
            chars, buf = [], []
        else:
            chars.append(ch)
            buf.append([ch, py])
    text = "".join(chars).strip()
    if text:
        pieces.append({"cn": text, "pos": sense.get("pos", ""),
                       "tokens": buf})
    return pieces


def build_split_question(question):
    """把四选一的候选释义逐个按逗号拆成片段，返回多选题目。"""
    options = question["options"]
    correct_index = question["correct_index"]
    pieces = []
    index_by_text = {}
    for oi, sense in enumerate(options):
        is_correct = oi == correct_index
        for piece in split_sense_pieces(sense):
            text = piece["cn"]
            if text in index_by_text:
                if is_correct:
                    pieces[index_by_text[text]]["correct"] = True
            else:
                piece["correct"] = is_correct
                index_by_text[text] = len(pieces)
                pieces.append(piece)
    if not pieces:
        return None
    random.shuffle(pieces)
    correct = {i for i, piece in enumerate(pieces) if piece.get("correct")}
    if not correct:
        return None
    return {"entry": question["entry"], "options": pieces,
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
    print(f"   {DIM}空格 查看助记；; 或 Enter 继续{RESET}")
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k == ord(" "):
            if show_info(term, entry, correct,
                         entry.get("senses") or []) is None:
                return None
            continue
        if k in Keys.ENTER or k == ord(";"):
            return ok


def quiz_one_split(term, sq, idx, total):
    """拆分多选：拼音筛选/数字切换 + 空格切换唯一匹配，; 或 Enter 提交。"""
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
    selected = set()
    typed = []

    def current_matches():
        norm = normalize_pinyin("".join(typed))
        if not norm:
            return None
        matches, _scores = fuzzy_matches(norm, options)
        return tuple(matches)

    def render(result=False):
        nonlocal block_lines
        if result:
            hint = (f"   {DIM}绿色=选对  黄色=漏选  红色=多选  "
                    f"灰色=未选{RESET}")
        else:
            hint = (f"   {DIM}输入拼音筛选，空格切换唯一匹配；数字 1-9 切换；"
                    f"; 或 Enter 提交；Esc 退出{RESET}")
        print(header)
        matched = None if result else current_matches()
        option_texts = []
        for i, piece in enumerate(options, 1):
            oi = i - 1
            check = "x" if oi in selected else " "
            if result:
                if oi in selected and oi in correct:
                    color = GREEN
                elif oi in selected:
                    color = RED
                elif oi in correct:
                    color = YELLOW
                else:
                    color = DIM
            elif oi in selected:
                # 下划线表示已选；颜色仍跟随拼音筛选，绿色只代表匹配
                if matched is None or oi in matched:
                    color = BOLD + UNDERLINE + GREEN
                else:
                    color = DIM + UNDERLINE
            elif matched is None or oi in matched:
                color = GREEN
            else:
                color = DIM
            line = f"   {i:>2}. [{check}] {color}{piece['cn']}{RESET}"
            print(line)
            option_texts.append(line)
        print(hint)
        block_lines = (physical_lines(header, cols)
                       + sum(physical_lines(line, cols)
                             for line in option_texts)
                       + physical_lines(hint, cols))
        return block_lines

    def draw_input():
        norm = normalize_pinyin("".join(typed))
        hint = ""
        if norm:
            matched = current_matches() or ()
            if len(matched) == 1:
                oi = matched[0]
                action = "已选，空格取消" if oi in selected else "空格选中"
                hint = f"{GREEN}唯一匹配：{oi + 1}，{action}{RESET}"
            elif matched:
                hint = f"{DIM}匹配 {len(matched)} 项，继续输入…{RESET}"
            else:
                hint = f"{RED}无匹配，Backspace 修改{RESET}"
        count = f"已选 {len(selected)} 项" if selected else "未选择"
        suffix = f"   {hint}" if hint else ""
        sys.stdout.write("\r\033[K   > " + "".join(typed) + suffix
                         + f"   {DIM}{count}{RESET}")
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
        if k == ord(" "):
            if typed:
                matched = current_matches() or ()
                if len(matched) == 1:
                    oi = matched[0]
                    if oi in selected:
                        selected.remove(oi)
                    else:
                        selected.add(oi)
                    typed.clear()
                    redraw()
            continue
        if ord("1") <= k <= ord("9"):
            oi = k - ord("1")
            if oi < len(options):
                if oi in selected:
                    selected.remove(oi)
                else:
                    selected.add(oi)
                typed.clear()
                redraw()
            continue
        if k in Keys.BACKSPACE:
            if typed:
                typed.pop()
                redraw()
            continue
        if k == Keys.CTRL_U:
            if typed:
                typed.clear()
            else:
                selected.clear()
            redraw()
            continue
        if k in Keys.ENTER or k == ord(";"):
            if not selected:
                continue
            break
        if 32 <= k <= 126:
            typed.append(chr(k))
            redraw()

    ok = selected == correct

    # 提交后重绘选项：绿色=选对，黄色=漏选，红色=多选，灰色=未选
    sys.stdout.write(f"\r\033[{block_lines}A\033[J")
    sys.stdout.flush()
    render(result=True)

    if ok:
        chosen = " / ".join(options[i]["cn"] for i in sorted(selected))
        print(f"   {GREEN}✓ 正确{RESET}   {chosen}")
    else:
        correct_text = " / ".join(options[i]["cn"] for i in sorted(correct))
        chosen_text = (" / ".join(options[i]["cn"] for i in sorted(selected))
                       or "（空）")
        missing = sorted(correct - selected)
        extra = sorted(selected - correct)
        print(f"   {RED}✗ 错误{RESET}")
        print(f"   {DIM}正确：{correct_text}{RESET}")
        print(f"   {DIM}你选了：{chosen_text}{RESET}")
        if missing:
            print(f"   {DIM}漏选：{' / '.join(options[i]['cn'] for i in missing)}"
                  f"{RESET}")
        if extra:
            print(f"   {DIM}多选：{' / '.join(options[i]['cn'] for i in extra)}"
                  f"{RESET}")
    if len(entry.get("senses", [])) > 1:
        print(f"   {DIM}全部释义：{meaning(entry)}{RESET}")
    print(f"   {DIM}空格 查看助记；; 或 Enter 继续{RESET}")
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k == ord(" "):
            if show_info(term, entry, correct_sense,
                         entry.get("senses") or []) is None:
                return None
            continue
        if k in Keys.ENTER or k == ord(";"):
            return ok


def review_counts(stats, day=None):
    """按义项统计：生词 / 熟词 / 待复习 / 覆盖词数。

    返回值第一项是旧数据里残留的「未测」义项数，正常应为 0。
    """
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
                if is_due(rec, day):
                    due += 1
    return new_count, learning, known, levels, due, len(words)


def run_quiz(args, kind="review", session_items=None):
    entries = load_words(args.file)
    stats = load_stats(args.stats_file)
    normalize_review(stats, entries)
    session_items = session_items or []

    scope = build_quiz_scope(entries, stats, args, session_items)
    if not scope:
        if getattr(args, "review_known", None):
            print("没有可加练的熟词。")
        else:
            print("没有需要检测的单词（生词表为空，且今天没有到期熟词）。")
        return

    all_options = [(e, s) for e in entries for s in e.get("senses", [])]
    label = "四选一" if args.check == "single" else "拆分多选"
    print(f"\n{BOLD}──── 释义检测（{label}）────{RESET}")
    if kind == "known":
        print(f"{DIM}本轮从熟词中抽取 {len(scope)} 个义项；答对只巩固，"
              f"不升级、不改到期日；答错回生词。{RESET}")
    else:
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

    skipped = []
    unfinished = []
    with Terminal() as term:
        try:
            for i, (entry, sense, _rec) in enumerate(scope, 1):
                result, question = ask(term, entry, sense, i, len(scope))
                if question is None:
                    skipped.append((entry, sense))
                    continue
                if result is None:
                    quit_early = True
                    unfinished = skipped + [(e, s) for e, s, _ in scope[i - 1:]]
                    break
                first_total += 1
                mark_review_result(stats, entry, question["correct"], result,
                                   args.intervals, early=(kind == "known"))
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

    if quit_early and unfinished:
        demote_unfinished(stats, unfinished)

    _new_count, learning, known, levels, due, words = review_counts(stats)
    level_text = " / ".join(f"L{k} {v}" for k, v in sorted(levels.items()))
    print(f"\n{BOLD}──── 检测结果 ────{RESET}")
    print(f"首次检测：{first_total} 个义项，正确 {first_correct}，"
          f"错误 {first_wrong}")
    if retry_rounds:
        print(f"错题重刷：{retry_rounds} 轮，答对 {retry_correct}，"
              f"仍错 {retry_wrong}")
    print(f"当前：生词 {learning} 义项 · 熟词 {known}"
          f"{('（' + level_text + '）') if level_text else ''} · "
          f"待复习 {due} · 覆盖 {words} 词")
    if quit_early:
        if unfinished:
            print(f"{DIM}中途退出；未作答的义项已记入生词表"
                  f"（到期的熟词保持不变）。{RESET}")
        else:
            print(f"{DIM}中途退出。{RESET}")

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

    show 模式会自动重绘首行：目标单词按已输入字符逐字标绿/红，释义按当前
    拼写状态整体标绿/红；其他行为保持不变。
    """
    target = entry.get("typing") or entry["word"]
    display = entry["word"]
    level = LEVEL_LABEL.get(entry.get("level"))
    level_tag = f"  {DIM}{level}{RESET}" if level else ""
    phonetic = (f"  {DIM}/{entry['phonetic']}/{RESET}"
                if entry.get("phonetic") else "")
    show_mode = mode == "show"
    base_shown = (meaning_with_mark(entry, sense) if show_mode
                  else meaning(entry))
    typed = []
    keystrokes = 0
    errors = 0
    start = None
    paused = 0.0
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns or 80
    header_lines = 0
    input_lines = 0
    first = True

    def typed_text():
        return "".join(typed)

    def prefix_ok(text):
        return (bool(text) and len(text) <= len(target) and
                all(text[i] == target[i] for i in range(len(text))))

    def build_head():
        text = typed_text()
        if len(display) == len(target):
            parts = []
            for i, ch in enumerate(display):
                if i < len(text):
                    color = GREEN if text[i] == target[i] else RED
                    parts.append(f"{BOLD}{color}{ch}{RESET}")
                else:
                    parts.append(f"{BOLD}{ch}{RESET}")
            word_text = "".join(parts)
        else:
            if text:
                color = GREEN if prefix_ok(text) else RED
                word_text = f"{BOLD}{color}{display}{RESET}"
            else:
                word_text = f"{BOLD}{display}{RESET}"
        return (f"{DIM}[{idx}/{total}]{RESET} {word_text}"
                f"{phonetic}{level_tag}  {base_shown}")

    def input_text():
        return ("   > " + (color_typed(typed, target) if show_mode
                           else "".join(typed)))

    def draw_input():
        sys.stdout.write("\r\033[K" + input_text())
        sys.stdout.flush()

    def refresh_header():
        nonlocal header_lines, input_lines, first
        if not first:
            up = (input_lines - 1) + header_lines
            sys.stdout.write(f"\r\033[{up}A\033[J")
        head = build_head()
        sys.stdout.write(head + "\n")
        text = input_text()
        sys.stdout.write(text)
        sys.stdout.flush()
        header_lines = physical_lines(head, cols)
        input_lines = physical_lines(text, cols)
        first = False

    if show_mode:
        refresh_header()
    else:
        tag = f"{DIM}{level}{RESET} " if level else ""
        head = f"{DIM}[{idx}/{total}]{RESET} {tag}{BOLD}{base_shown}{RESET}"
        print("\r\033[K" + head)
        draw_input()

    def redraw():
        if show_mode:
            refresh_header()
        else:
            draw_input()

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
            redraw()
            continue
        if k in Keys.BACKSPACE:
            if typed:
                typed.pop()
            redraw()
            continue
        if k == Keys.CTRL_U:
            typed.clear()
            redraw()
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
            redraw()
            if show_mode and typed_text() == target:
                break

    elapsed = (time.monotonic() - start - paused) if start else 0.0
    answer = typed_text()
    ok = answer == target
    if ok:
        sys.stdout.write(f"\r\033[K   > {GREEN}{answer}{RESET}\n")
    else:
        sys.stdout.write(f"\r\033[K   > {RED}{answer}{RESET}"
                         f"  →  {GREEN}{BOLD}{display}{RESET}"
                         f"  {DIM}{base_shown}{RESET}\n")
    sys.stdout.flush()
    return ok, elapsed, keystrokes, errors, start


def run(args):
    entries = load_words(args.file)
    stats = load_stats(args.stats_file)
    normalize_review(stats, entries)
    items = select_items(entries, stats, args)
    if not items:
        sys.exit("没有可练习的义项（默认排除熟词；"
                 "可用 --all-senses 从全部词库抽取）。")

    queue = [[e, s, False] for e, s in items]
    total_planned = len(items)
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
    i = 0
    quit_early = False
    encountered = False

    with Terminal() as term:
        try:
            while i < len(queue):
                entry, sense, is_review = queue[i]
                practice_sense = (sense if args.mode == "show"
                                  and not args.no_pinyin else None)
                if not args.no_quiz:
                    if register_encountered(stats, entry, sense):
                        encountered = True
                result = practice_one(term, entry, min(done + 1, total_planned),
                                      total_planned, args.mode, practice_sense)
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
                        queue.insert(min(i + 6, len(queue)),
                                     [entry, sense, True])

                # 标准模式的第二阶段：按逐字拼音把释义打出来（不计入错词和准确率）
                if ok and practice_sense:
                    if practice_pinyin(term, entry, practice_sense) is None:
                        quit_early = True
                        break
                    pinyin_count += 1
                i += 1
        finally:
            termios.tcsetattr(term.fd, termios.TCSADRAIN, term.old)

    if done == 0:
        if encountered:
            save_stats(args.stats_file, stats)
            print("\n已退出；本次遇到的单词已记入生词表。")
        else:
            print("\n已退出，本次没有练习记录。")
        return [], True, 0

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
    return items, quit_early, done


DETAIL_FILTERS = (("all", "全部"), ("learning", "生词"),
                  ("known", "熟词"), ("due", "待复习"))


def due_relative(due, day=None):
    try:
        d = date.fromisoformat(due)
    except (TypeError, ValueError):
        return None
    day = day or date.today()
    delta = (d - day).days
    if delta == 0:
        return "今天"
    if delta > 0:
        return f"{delta}天后"
    return f"过{-delta}天"


def due_short(due, day=None):
    rel = due_relative(due, day)
    if rel is None:
        return "—"
    d = date.fromisoformat(due)
    day = day or date.today()
    date_text = (f"{d.month:02d}-{d.day:02d}"
                 if d.year == day.year else d.isoformat())
    return f"{date_text} {rel}"


def due_full(due, day=None):
    rel = due_relative(due, day)
    if rel is None:
        return due or "—"
    return f"{due}（{rel}）"


def build_review_rows(entries, stats):
    """把 review 记录展开成按词/义项排序的展示行。"""
    by_word = {e["word"]: e for e in entries}
    rows = []
    review = stats.get("review", {})
    if not isinstance(review, dict):
        return rows
    for word in sorted(review, key=str.lower):
        recs = review[word]
        if not isinstance(recs, dict) or "state" in recs:
            continue
        entry = by_word.get(word)
        senses = entry.get("senses", []) if entry else []
        ordered = []
        seen = set()
        for sense in senses:
            key = sense_key(sense)
            if key in recs and key not in seen:
                ordered.append((key, sense))
                seen.add(key)
        for key in recs:
            if key not in seen:
                ordered.append((key, None))
        for key, sense in ordered:
            rec = recs.get(key)
            if not isinstance(rec, dict):
                continue
            if sense:
                pos, cn = sense.get("pos", ""), sense.get("cn", "")
            elif "|" in key:
                pos, cn = key.split("|", 1)
            else:
                pos, cn = "", key
            rows.append({
                "word": word,
                "level": entry.get("level") if entry else "",
                "pos": pos,
                "cn": cn,
                "rec": rec,
                "state": rec.get("state", ""),
                "due": rec.get("due"),
                "correct": int(rec.get("correct", 0) or 0),
                "wrong": int(rec.get("wrong", 0) or 0),
                "last": rec.get("last"),
            })
    return rows


def review_state_label(row):
    state = row["state"]
    if state == "known":
        try:
            level = int(row["rec"].get("level", 1))
        except (TypeError, ValueError):
            level = 1
        return f"熟词 L{level}"
    if state == "learning":
        return "生词"
    if state == "new":
        return "未测"
    return state or "?"


def review_row_matches(row, filt):
    if filt == "all":
        return True
    if filt == "learning":
        return row["state"] == "learning"
    if filt == "known":
        return row["state"] == "known"
    if filt == "due":
        return row["state"] != "known" or is_due(row["rec"])
    return True


def show_review_detail(entries, stats):
    """全屏义项状态浏览器：j/k 移动，f 筛选，q 退出。"""
    rows = build_review_rows(entries, stats)
    filter_idx = 0
    pos = 0
    sys.stdout.write("\033[?1049h\033[?2J\033[H")
    sys.stdout.flush()
    try:
        with Terminal() as term:
            while True:
                size = shutil.get_terminal_size(fallback=(80, 24))
                cols = size.columns or 80
                height = size.lines or 24
                filt, label = DETAIL_FILTERS[filter_idx]
                filtered = [r for r in rows if review_row_matches(r, filt)]
                if filtered:
                    pos = min(max(0, pos), len(filtered) - 1)
                else:
                    pos = 0
                page = max(1, height - 5)
                if filtered:
                    start = min(max(0, pos - page // 2),
                                max(0, len(filtered) - page))
                    end = min(start + page, len(filtered))
                else:
                    start = end = 0

                word_w = min(18, max([display_width(r["word"]) for r in rows]
                                     + [8]))
                state_w = min(10, max([display_width(review_state_label(r))
                                       for r in rows] + [6]))
                due_w = min(16, max([display_width(due_short(r["due"]))
                                     for r in rows] + [8]))
                fixed = 2 + word_w + 2 + state_w + 2 + due_w + 2
                sense_w = max(4, cols - fixed)

                learning = sum(1 for r in rows if r["state"] == "learning")
                known = sum(1 for r in rows if r["state"] == "known")
                due_count = sum(1 for r in rows
                                if r["state"] != "known" or is_due(r["rec"]))
                lines = [f"{BOLD}──── 义项状态 ────{RESET}"]
                counts_plain = (f"全部 {len(rows)} · 生词 {learning} · "
                                f"熟词 {known} · 待复习 {due_count} · "
                                f"筛选：{label} {len(filtered)}")
                lines.append(DIM + truncate_display(counts_plain, cols)
                             + RESET)

                if not filtered:
                    lines.append(f"{DIM}   （该筛选下没有义项）{RESET}")
                for idx in range(start, end):
                    row = filtered[idx]
                    selected = idx == pos
                    marker = f"{GREEN}>{RESET}" if selected else " "
                    word_text = truncate_display(row["word"], word_w)
                    word_pad = pad_display(word_text, word_w)
                    if selected:
                        word_pad = f"{BOLD}{word_pad}{RESET}"
                    state_text = truncate_display(review_state_label(row),
                                                  state_w)
                    state_color = (GREEN if row["state"] == "known"
                                   else YELLOW if row["state"] == "learning"
                                   else DIM)
                    state_pad = (f"{state_color}"
                                 f"{pad_display(state_text, state_w)}{RESET}")
                    due_text = pad_display(
                        truncate_display(due_short(row["due"]), due_w), due_w)
                    sense_prefix = (f"{row['pos']}. " if row["pos"] else "")
                    sense_text = truncate_display(
                        sense_prefix + row["cn"], sense_w)
                    lines.append(f"{marker} {word_pad}  {state_pad}  "
                                 f"{due_text}  {sense_text}")

                if filtered:
                    row = filtered[pos]
                    level = LEVEL_LABEL.get(row.get("level"), "")
                    word = row["word"] + (f"（{level}）" if level else "")
                    detail_plain = (
                        f"所选：{word} · {row['pos']}. {row['cn']} · "
                        f"{review_state_label(row)} · 到期 "
                        f"{due_full(row['due'])} · 对 {row['correct']} "
                        f"错 {row['wrong']}")
                    if row["last"]:
                        detail_plain += f" · 上次 {row['last']}"
                else:
                    detail_plain = "所选：—"
                lines.append(DIM + truncate_display(detail_plain, cols)
                             + RESET)

                controls_plain = (
                    "j/k 移动 · Ctrl-D/U 翻页 · g/G 首尾 · f 筛选 · q 退出"
                    f"    {start + 1 if filtered else 0}-{end}/{len(filtered)}")
                lines.append(DIM + truncate_display(controls_plain, cols)
                             + RESET)

                sys.stdout.write("\033[H\033[2J")
                sys.stdout.write("\n".join(lines))
                sys.stdout.flush()

                try:
                    k = term.key()
                except KeyboardInterrupt:
                    break
                if k in (ord("q"), Keys.ESC, Keys.CTRL_C):
                    break
                if k == ord("j"):
                    pos += 1
                elif k == ord("k"):
                    pos -= 1
                elif k == ord("g"):
                    pos = 0
                elif k == ord("G"):
                    pos = len(filtered) - 1
                elif k == Keys.CTRL_D:
                    pos += max(1, page // 2)
                elif k == Keys.CTRL_U:
                    pos -= max(1, page // 2)
                elif k == ord("f"):
                    filter_idx = (filter_idx + 1) % len(DETAIL_FILTERS)
                    pos = 0
    finally:
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()


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
    _new_count, learning, known, levels, due, words = review_counts(stats)
    level_text = " / ".join(f"L{k} {v}" for k, v in sorted(levels.items()))
    print(f"复习条目: 生词 {learning} · 熟词 {known}"
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
    if sys.stdin.isatty() and sys.stdout.isatty():
        print(f"{DIM}按 d 查看每个义项的详细状态，其他键退出{RESET}",
              end="", flush=True)
        try:
            with Terminal() as term:
                k = term.key()
        except (KeyboardInterrupt, EOFError):
            k = None
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        if k in (ord("d"), ord("D")):
            show_review_detail(entries, stats)


class ChineseArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        text = super().format_help()
        return (text.replace("usage:", "用法:")
                    .replace("options:", "选项:")
                    .replace("positional arguments:", "位置参数:"))


def main():
    epilog = """\
示例:
  vocabtype                         普通模式：拼写 + 全拼释义 + 拆分多选检测
  vocabtype --check single          释义检测改用四选一
  vocabtype --no-quiz               只做拼写练习
  vocabtype --review                复习模式：检测生词和到期熟词
  vocabtype --review --quiz-size 50 本次最多检测 50 个
  vocabtype --review-known 20       从熟词中抽 20 个义项加练（不看是否到期）
  vocabtype --all-senses            拼写选词包含熟词，从全部词库按释义抽取
  vocabtype --stats                 查看复习进度
"""
    p = ChineseArgumentParser(
        description="CET-6 单词 + 打字练习 + 释义检测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog, add_help=False)
    p.add_argument("-h", "--help", action="help", help="显示本帮助并退出")
    p.add_argument("-f", "--file", default=DEFAULT_DATA, help="单词数据文件")
    p.add_argument("-n", "--count", type=int, default=30,
                   help="普通模式本次拼写练习的义项数（默认 30）")
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
    p.add_argument("--all-senses", action="store_true",
                   help="拼写选词从全部词库按释义抽取（包含熟词）；"
                        "默认只抽还没变熟的义项")
    p.add_argument("--no-requeue", action="store_true", help="错词不在本次重复")
    p.add_argument("--review", action="store_true",
                   help="复习模式：只做释义检测，抽取生词和今天到期的熟词")
    p.add_argument("--review-known", type=int, metavar="N", default=None,
                   help="熟词加练：从熟词中抽 N 个义项检测，不看是否到期；"
                        "答对不升级、不改到期日，答错回生词")
    p.add_argument("--check", choices=["single", "split"], default="split",
                   help="检查方式：split=拆分多选（默认）；single=四选一")
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
    if args.review_known is not None:
        if args.review_known < 1:
            sys.exit("--review-known 需要正整数 N，例如 --review-known 20")
        if args.review:
            sys.exit("--review-known 不能与 --review 同用。")
        if args.no_quiz:
            sys.exit("--review-known 本身就是释义检测，不能与 --no-quiz 同用。")
        if args.new or args.weak:
            sys.exit("--review-known 不支持 --new / --weak。")
        if args.quiz_size:
            sys.exit("--review-known 已指定题数，不能与 --quiz-size 同用。")
    if args.seed is not None:
        random.seed(args.seed)
    if args.stats:
        show_stats(args)
        return
    if args.review_known is not None:
        run_quiz(args, kind="known")
        return
    if args.review:
        if args.no_quiz:
            sys.exit("复习模式本身就是释义检测，不能与 --no-quiz 同用。")
        if args.new or args.weak:
            sys.exit("复习模式不支持 --new / --weak。")
        run_quiz(args, kind="review")
        return
    session_items, quit_early, done = run(args)
    if args.no_quiz:
        return
    if quit_early:
        print(f"{DIM}已退出，本次未进行释义检测。{RESET}")
        return
    if done:
        run_quiz(args, kind="normal", session_items=session_items)


if __name__ == "__main__":
    main()
