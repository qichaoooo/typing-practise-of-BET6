#!/usr/bin/env python3
"""vocabtype — 边背 CET-6 单词边练打字。

两种模式：
  show   （默认）两阶段：先照着英文单词拼写；再从该词的义项中按词频顺序
         加权抽一条，看着逐字拼音把释义全拼打出来（打错不计入错词）
  recall 只显示中文释义，凭记忆拼出英文单词

用法示例：
  python3 -I vocabtype.py                 # 随机 30 个词，显示模式
  python3 -I vocabtype.py -n 50 --mode recall
  python3 -I vocabtype.py --weak          # 专练错得最多的词
  python3 -I vocabtype.py --new           # 只练还没练过的词
  python3 -I vocabtype.py --stats         # 查看累计进度

按键：Esc / Ctrl-C 退出并查看本次结果；练习中按 ; 查看该词的词根、同根词、
近义词、变形、例句、真题和词频，再按 ; 继续拼写。
"""
import argparse
import json
import os
import random
import sys
import termios
import time
import tty
import unicodedata

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
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"words": {}, "sessions": []}


def save_stats(path, stats):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)
    os.replace(tmp, path)


def word_stat(stats, word):
    return stats["words"].setdefault(word, {"r": 0, "w": 0, "n": 0, "t": 0.0})


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
    if args.weak:
        practiced = []
        for e in entries:
            s = stats["words"].get(e["word"])
            if s and s["n"] > 0:
                rate = (s["w"] + 0.5) / (s["n"] + 1.0)
                practiced.append((rate, s["w"], e))
        practiced.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen = [e for _, _, e in practiced[:n]]
        rest = [e for e in entries if e not in chosen]
        random.shuffle(rest)
        return chosen + rest[:max(0, n - len(chosen))]
    if args.new:
        pool = [e for e in entries if stats["words"].get(e["word"], {}).get("n", 0) == 0]
        pool = pool or entries
    else:
        pool = list(entries)
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


def info_lines(entry, sense):
    """按 ; 展示的词源/助记信息。"""
    lines = []
    if sense:
        label = f"{sense['pos']}. " if sense.get("pos") else ""
        lines.append(f"{DIM}释义{RESET}   {label}{sense['cn']}")
    if entry.get("method"):
        lines.append(f"{DIM}助记{RESET}   {entry['method']}")
    if entry.get("family"):
        fam = "；".join(
            f"{f['pos']}. {f['word']} {f['cn']}".strip()
            for f in entry["family"][:4])
        lines.append(f"{DIM}同根{RESET}   {fam}")
    if entry.get("synonyms"):
        syns = []
        for s in entry["synonyms"][:3]:
            prefix = f"{s['pos']}. " if s.get("pos") else ""
            syns.append(prefix + " / ".join(s["words"]))
        lines.append(f"{DIM}近义{RESET}   " + "；".join(syns))
    forms = entry.get("forms") or {}
    if forms:
        parts = [f"{label} {forms[key]}" for key, label in FORM_ORDER
                 if forms.get(key)]
        if parts:
            lines.append(f"{DIM}变形{RESET}   " + "，".join(parts))
    if entry.get("examples"):
        ex = entry["examples"][0]
        line = f"{DIM}例句{RESET}   {ex['en']}"
        if ex.get("cn"):
            line += f"  {DIM}{ex['cn']}{RESET}"
        lines.append(line)
    if entry.get("exam"):
        ex = entry["exam"][0]
        line = f"{DIM}真题{RESET}   {ex['en']}"
        if ex.get("source"):
            line += f"  {DIM}({ex['source']}){RESET}"
        lines.append(line)
    if entry.get("phrases"):
        ph = "；".join(f"{p['en']} {p['cn']}" for p in entry["phrases"][:3])
        lines.append(f"{DIM}短语{RESET}   {ph}")
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


def show_info(term, entry, sense):
    """暂停练习，显示词源/助记；再按 ; 或 Enter 返回。Esc 退出。"""
    print()
    for line in info_lines(entry, sense):
        print("   " + line)
    print(f"   {DIM}再按 ; 继续拼写{RESET}")
    while True:
        try:
            k = term.key()
        except KeyboardInterrupt:
            return None
        if k == Keys.ESC or k == Keys.CTRL_C:
            return None
        if k == ord(";") or k in Keys.ENTER:
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
            if show_info(term, entry, sense) is None:
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
    target = entry["word"]
    shown = meaning_with_mark(entry, sense) if mode == "show" else meaning(entry)
    if mode == "show":
        head = (f"{DIM}[{idx}/{total}]{RESET} {BOLD}{target}{RESET}"
                f"  {DIM}/{entry['phonetic']}/{RESET}  {shown}")
    else:
        head = f"{DIM}[{idx}/{total}]{RESET} {BOLD}{shown}{RESET}"

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
            if show_info(term, entry, sense) is None:
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
                         f"  →  {GREEN}{BOLD}{target}{RESET}"
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
    i = 0
    quit_early = False

    with Terminal() as term:
        try:
            while i < len(queue):
                entry, is_review = queue[i]
                sense = (pick_sense(entry)
                         if args.mode == "show" and not args.no_pinyin else None)
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
                    correct_chars += len(entry["word"])
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
        return

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


def show_stats(args):
    entries = load_words(args.file)
    stats = load_stats(args.stats_file)
    ws = stats["words"]
    practiced = sum(1 for w, s in ws.items() if s.get("n"))
    right = sum(s.get("r", 0) for s in ws.values())
    wrong = sum(s.get("w", 0) for s in ws.values())
    print(f"已练单词: {practiced} / {len(entries)}")
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
            print(f"  {s['time']}  {s['mode']:<6} {s['done']:>3} 词  "
                  f"{s['wpm']:>5.0f} WPM  {s['accuracy']:>5.1f}%")


def main():
    p = argparse.ArgumentParser(description="CET-6 单词 + 打字练习")
    p.add_argument("-f", "--file", default=DEFAULT_DATA, help="单词数据文件")
    p.add_argument("-n", "--count", type=int, default=30, help="本次练习词数（默认 30）")
    p.add_argument("--mode", choices=["show", "recall"], default="show",
                   help="show=显示单词照着打（含全拼释义阶段）；recall=只看释义拼写")
    p.add_argument("--no-pinyin", action="store_true",
                   help="标准模式不进行第二阶段的释义全拼")
    p.add_argument("--weak", action="store_true", help="优先练错误率最高的词")
    p.add_argument("--new", action="store_true", help="只练尚未练过的词")
    p.add_argument("--no-requeue", action="store_true", help="错词不在本次重复")
    p.add_argument("--stats", action="store_true", help="显示累计进度")
    p.add_argument("--stats-file", default=DEFAULT_STATS)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    if args.stats:
        show_stats(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
