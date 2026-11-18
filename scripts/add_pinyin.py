#!/usr/bin/env python3
"""给 cet6.json 的每个义项补上逐字拼音（用于第二阶段全拼练习）。

构建期脚本，需要 pypinyin（运行期不需要）：
    python3 -m venv .venv && .venv/bin/pip install pypinyin
    .venv/bin/python -I scripts/add_pinyin.py data/cet6.json

输出结构（每个义项）：
    {"pos": "adj", "cn": "遥远的，偏僻的",
     "tokens": [["遥","yao"],["远","yuan"],["的","de"],["，",""], ...]}
tokens 与 cn 逐字符对齐，标点/非中文字符的拼音为空串。
"""
import json
import os
import re
import sys

from pypinyin import Style, pinyin

CJK = re.compile(r"[㐀-鿿]")


def is_cjk(ch):
    return bool(CJK.match(ch))


def strip_phonetic_brackets(text):
    """去掉释义里混入的 IPA 注音，如 “宣判…有罪[ˈkɒnvɪkt]”；保留 “[总称]” 这类中文标注。"""
    return re.sub(r"\[[^\]]*\]",
                  lambda m: m.group(0) if CJK.search(m.group(0)) else "",
                  text)


def tokens_for(text):
    """逐字返回拼音，同时保留整句的短语级多音字判断。

    lazy_pinyin 会把连续的 "] " 或 "..." 合并成一个 token，导致长度与
    原文对不上；旧逻辑此时退化为逐字转换，丢掉上下文（例如“偿还”会被
    读成 chang hai）。这里用 pinyin(..., errors=...) 让非中文字符也逐字
    返回，既能对齐原文，又不需要放弃整句转换。
    """
    aligned = pinyin(text, style=Style.NORMAL, errors=lambda s: list(s))
    if len(aligned) != len(text):
        raise ValueError(f"pinyin 无法逐字对齐：{text!r}")
    tokens = []
    for ch, readings in zip(text, aligned):
        py = readings[0] if readings else ""
        if is_cjk(ch) and py and py != ch:
            tokens.append([ch, py.lower()])
        else:
            tokens.append([ch, ""])
    return tokens


def apply_fix(tokens, readings, key):
    """用 data/pinyin_fixes.json 里的读音覆盖自动结果。"""
    cjk = [i for i, (ch, _) in enumerate(tokens) if is_cjk(ch)]
    if len(readings) != len(cjk):
        print(f"warning: pinyin fix for {key!r} has {len(readings)} readings "
              f"but {len(cjk)} CJK chars; ignored", file=sys.stderr)
        return tokens
    for idx, reading in zip(cjk, readings):
        tokens[idx][1] = str(reading).lower()
    return tokens


def split_senses(entry, fixes):
    senses = []
    for t in entry.get("trans", []):
        pos = t.get("pos", "")
        for chunk in re.split(r"[；;]", t["cn"]):
            chunk = strip_phonetic_brackets(chunk)
            chunk = re.sub(r"\s+", " ", chunk).strip(" ，,；;。.")
            if not chunk or not CJK.search(chunk):
                continue
            tokens = tokens_for(chunk)
            key = f"{entry['word']}|{chunk}"
            if key in fixes:
                tokens = apply_fix(tokens, fixes[key], key)
            if not any(py for _, py in tokens):
                continue
            senses.append({"pos": pos, "cn": chunk, "tokens": tokens})
    return senses


def main(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    fixes_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                              "pinyin_fixes.json")
    fixes = {}
    if os.path.exists(fixes_path):
        with open(fixes_path, encoding="utf-8") as f:
            fixes = json.load(f)

    n = 0
    for entry in data["words"]:
        entry["senses"] = split_senses(entry, fixes)
        n += len(entry["senses"])

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    print(f"{len(data['words'])} words, {n} senses -> {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
