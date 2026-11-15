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
import re
import sys

from pypinyin import Style, lazy_pinyin

CJK = re.compile(r"[㐀-鿿]")


def is_cjk(ch):
    return bool(CJK.match(ch))


def strip_phonetic_brackets(text):
    """去掉释义里混入的 IPA 注音，如 “宣判…有罪[ˈkɒnvɪkt]”；保留 “[总称]” 这类中文标注。"""
    return re.sub(r"\[[^\]]*\]",
                  lambda m: m.group(0) if CJK.search(m.group(0)) else "",
                  text)


def tokens_for(text):
    parts = lazy_pinyin(text, style=Style.NORMAL)
    if len(parts) != len(text):
        # 整句转换无法逐字对齐时，退化为逐字转换
        parts = [lazy_pinyin(ch, style=Style.NORMAL)[0] for ch in text]
    tokens = []
    for ch, py in zip(text, parts):
        if is_cjk(ch) and py and py != ch:
            tokens.append([ch, py.lower()])
        else:
            tokens.append([ch, ""])
    return tokens


def split_senses(entry):
    senses = []
    for t in entry.get("trans", []):
        pos = t.get("pos", "")
        for chunk in re.split(r"[；;]", t["cn"]):
            chunk = strip_phonetic_brackets(chunk)
            chunk = re.sub(r"\s+", " ", chunk).strip(" ，,；;。.")
            if not chunk or not CJK.search(chunk):
                continue
            tokens = tokens_for(chunk)
            if not any(py for _, py in tokens):
                continue
            senses.append({"pos": pos, "cn": chunk, "tokens": tokens})
    return senses


def main(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    n = 0
    for entry in data["words"]:
        entry["senses"] = split_senses(entry)
        n += len(entry["senses"])

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    import os
    os.replace(tmp, path)
    print(f"{len(data['words'])} words, {n} senses -> {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
