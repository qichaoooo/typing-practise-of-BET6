#!/usr/bin/env python3
"""Parse the official 2016 CET syllabus word list into a versioned JSON file.

Inputs:
  - the official PDF (downloaded from cet.neea.edu.cn)
  - a clean plain-text extraction of the same list, without stars
    (JavaProgrammerLB/cet-word-list word-list.txt), used to repair kerning and
    spacing in the PDF text layer

Output:
  data/official_2016.json  {"entries": [{"word", "level", "variants", "derivatives"}]}

Build-time dependency: pymupdf. Run with the build venv, e.g.
    .venv/bin/python -I scripts/parse_official.py \
        data/sources/official_cet_2016.pdf data/sources/gh_cet_word_list.txt \
        data/official_2016.json
"""
import json
import re
import sys

import pymupdf

LIST_PAGES = range(20, 149)  # 0-based; PDF pages 21..149

# Rows where the PDF text layer splits a single word across text boxes.
BROKEN_ROWS = {
    "mo(u)": ("mo(u)ld", ["mo(u)ldy"]),
    "fulfil(": ("fulfil(l)", ["fulfil(l)ment"]),
    "coup(d\U001001b3état)": ("coup (d'état)", []),
}


def normalize_key(text):
    return re.sub(r"[^a-z]", "", text.lower())


def extract_rows(pdf_path):
    doc = pymupdf.open(pdf_path)
    rows = []
    for pno in LIST_PAGES:
        items = []
        for x0, y0, x1, y1, word, *_ in doc[pno].get_text("words"):
            if 60 < y0 < 660 and x0 < 480 and word.strip():
                items.append((y0, x0, word.strip()))
        items.sort(key=lambda t: (t[0], t[1]))
        cur, last_y = [], None
        for y, x, word in items:
            if last_y is None or abs(y - last_y) <= 3:
                cur.append((x, word))
                last_y = y if last_y is None else (last_y + y) / 2
            else:
                rows.append(cur)
                cur, last_y = [(x, word)], y
        if cur:
            rows.append(cur)

    cleaned = []
    for row in rows:
        row.sort()
        head = row[0][1]
        if not re.search(r"[A-Za-z]", head):
            continue
        cleaned.append({
            "raw": head.lstrip("★"),
            "cet6": head.startswith("★"),
        })
    return cleaned


def strip_superscripts(text):
    return text.translate(str.maketrans({"¹": "", "²": "", "³": "",
                                         "⁴": "", "⁵": ""}))


def split_head(text, line):
    """Return (headword, derivative-string) using the clean text line.

    Matching is done on letters only, so PDF spacing problems ("accordingto"
    vs "according to") do not matter. The consumed prefix of `line` keeps its
    original spacing and punctuation.
    """
    key = normalize_key(text)
    letters = 0
    consumed = 0
    for i, ch in enumerate(line):
        if "a" <= ch.lower() <= "z":
            if letters >= len(key):
                break
            if ch.lower() != key[letters]:
                break
            letters += 1
        consumed = i + 1
        if letters == len(key):
            break
    if letters == len(key):
        # 吞掉词尾的带音标字母（résumé 的 é）和右括号（afterward(s)）
        while consumed < len(line):
            ch = line[consumed]
            if ch.isspace() or ch in "/'’" or ch.isdigit() or ch in "¹²³⁴⁵":
                break
            consumed += 1
        head = line[:consumed].strip()
        rest = line[consumed:].strip()
        if head:
            return head, rest
    return None, None


def clean_derivatives(rest):
    out = []
    for token in strip_superscripts(rest).split():
        token = token.strip()
        token = re.sub(r"^[0-9]+", "", token)
        # 保留括号和斜杠，交给 build_wordlist.expand_official 展开
        if re.search(r"[A-Za-z]", token):
            out.append(token)
    return out


def main(pdf_path, clean_txt_path, out_path):
    rows = extract_rows(pdf_path)
    lines = [line.strip() for line in open(clean_txt_path, encoding="utf-8")
             if line.strip()]
    if len(rows) != len(lines):
        sys.exit(f"row misalignment: pdf {len(rows)} vs txt {len(lines)}")

    entries = []
    for row, line in zip(rows, lines):
        raw = row["raw"]
        if raw.lower() in BROKEN_ROWS:
            head, derivs = BROKEN_ROWS[raw.lower()]
        else:
            head, rest = split_head(raw, line)
            if head is None:
                head, derivs = raw, []  # should not happen
            else:
                derivs = clean_derivatives(rest)
        head = strip_superscripts(head).strip()
        variants = [h.strip() for h in head.split("/") if h.strip()]
        if not variants:
            continue
        entries.append({
            "word": head,
            "level": "cet6" if row["cet6"] else "cet4",
            "variants": variants,
            "derivatives": derivs,
        })

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "source": "全国大学英语四、六级考试大纲（2016 年修订版）词表",
            "source_url": "https://cet.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf",
            "entries": entries,
        }, f, ensure_ascii=False, indent=1)

    cet6 = sum(1 for e in entries if e["level"] == "cet6")
    print(f"{len(entries)} entries ({cet6} CET6, {len(entries) - cet6} CET4) "
          f"-> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
