#!/usr/bin/env python3
"""Build the clean CET-6 word list from the downloaded source JSONL.

Usage:
    python3 -I scripts/build_wordlist.py \
        data/cet6_raw.zip data/extra_words.json \
        data/cet6.json data/cet6-words.txt \
        [--ecdict data/sources/stardict.db]

Sources:
  - kajweb/dict CET6_3: headwords, phonetics, translations, phrases,
    example / real-exam sentences, word families (同根词), synonyms,
    and the 词根记忆 (mnemonic) field.
  - data/extra_words.json: CET-6 words present in mahavivo/english-wordlists
    but missing from CET6_3.
  - ECDICT 1.0.28 (optional): word forms (exchange) and frequency ranks.

Pinyin senses are added afterwards by scripts/add_pinyin.py.
"""
import argparse
import json
import os
import re
import sqlite3
import zipfile

CJK = re.compile(r"[㐀-鿿]")

FORM_LABELS = {
    "p": "过去式",
    "d": "过去分词",
    "i": "现在分词",
    "3": "第三人称单数",
    "s": "复数",
    "r": "比较级",
    "t": "最高级",
}

# 上游数据缺失音标、ECDICT 也没有的词，手工补。
PHONETIC_FIXES = {
    "air-conditioning": "ˈeәkәnˌdiʃəniŋ",
}


def clean_text(text):
    """Normalize whitespace and drop IPA brackets such as [ˈkɒnvɪkt]."""
    text = text or ""
    text = re.sub(r"\[[^\]]*\]",
                  lambda m: m.group(0) if CJK.search(m.group(0)) else "",
                  text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*([，。；、,;])\s*", r"\1", text)
    return text


def parse_entry(obj):
    word = obj["content"]["word"]
    head = word["wordHead"].strip()
    c = word["content"]

    trans = []
    for t in c.get("trans", []):
        cn = clean_text(t.get("tranCn"))
        if cn:
            trans.append({"pos": (t.get("pos") or "").strip(), "cn": cn})

    phrases = []
    for p in (c.get("phrase") or {}).get("phrases", [])[:3]:
        cn = clean_text(p.get("pCn"))
        if p.get("pContent") and cn:
            phrases.append({"en": p["pContent"].strip(), "cn": cn})

    method = clean_text((c.get("remMethod") or {}).get("val", ""))

    family = []
    for rel in (c.get("relWord") or {}).get("rels", []):
        for w in rel.get("words", []):
            hwd = (w.get("hwd") or "").strip()
            if hwd:
                family.append({"pos": rel.get("pos", "").strip(),
                               "word": hwd,
                               "cn": clean_text(w.get("tran"))})
    family = family[:5]

    synonyms = []
    for s in (c.get("syno") or {}).get("synos", []):
        words = [h["w"] for h in s.get("hwds", []) if h.get("w")]
        if words:
            synonyms.append({"pos": (s.get("pos") or "").strip(),
                             "cn": clean_text(s.get("tran")),
                             "words": words})
    synonyms = synonyms[:3]

    examples = []
    for s in (c.get("sentence") or {}).get("sentences", [])[:2]:
        en = (s.get("sContent") or "").strip()
        if en:
            examples.append({"en": en, "cn": clean_text(s.get("sCn"))})

    exam = []
    for s in (c.get("realExamSentence") or {}).get("sentences", [])[:1]:
        en = (s.get("sContent") or "").strip()
        if not en:
            continue
        info = s.get("sourceInfo") or {}
        source = " ".join(str(info[k]) for k in ("year", "level", "paper")
                          if info.get(k))
        exam.append({"en": en, "source": source})

    return {
        "word": head,
        "phonetic": (c.get("usphone") or "").strip(),
        "trans": trans,
        "phrases": phrases,
        "method": method,
        "family": family,
        "synonyms": synonyms,
        "examples": examples,
        "exam": exam,
        "rank": obj.get("wordRank", 0),
    }


def parse_exchange(value):
    forms = {}
    for part in (value or "").split("/"):
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        if key in FORM_LABELS and val:
            forms[key] = val
    return forms


def enrich_ecdict(entries, db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    hit = 0
    for e in entries:
        row = cur.execute(
            "SELECT phonetic, exchange, bnc, frq, collins, oxford "
            "FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 1",
            (e["word"],)).fetchone()
        if not row:
            continue
        hit += 1
        phonetic, exchange, bnc, frq, collins, oxford = row
        if not e["phonetic"] and phonetic:
            e["phonetic"] = phonetic.strip()
        forms = parse_exchange(exchange)
        if forms:
            e["forms"] = forms
        freq = {k: v for k, v in
                (("bnc", bnc), ("frq", frq), ("collins", collins),
                 ("oxford", oxford)) if v}
        if freq:
            e["freq"] = freq
    con.close()
    print(f"ECDICT: enriched {hit}/{len(entries)} words")


def load_extras(path):
    with open(path, encoding="utf-8") as f:
        extras = json.load(f)
    for e in extras:
        e.setdefault("phrases", [])
        e.setdefault("method", "")
        e.setdefault("family", [])
        e.setdefault("synonyms", [])
        e.setdefault("examples", [])
        e.setdefault("exam", [])
        e.setdefault("rank", 0)
    return extras


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("zip_path")
    p.add_argument("extras_path")
    p.add_argument("out_json")
    p.add_argument("out_txt")
    p.add_argument("--ecdict", help="ECDICT stardict.db (optional)")
    p.add_argument("--fixes", default="data/word_fixes.json",
                   help="per-word field overrides (optional)")
    args = p.parse_args()

    with zipfile.ZipFile(args.zip_path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".json"))
        raw = zf.read(name).decode("utf-8")

    entries = [parse_entry(json.loads(line)) for line in raw.splitlines()
               if line.strip()]

    seen = {e["word"].lower() for e in entries}
    extras = [e for e in load_extras(args.extras_path)
              if e["word"].lower() not in seen]
    max_rank = max((e["rank"] for e in entries), default=0)
    for i, e in enumerate(extras, 1):
        e["rank"] = max_rank + i
    entries.extend(extras)

    entries.sort(key=lambda e: e["rank"])

    if args.ecdict:
        enrich_ecdict(entries, args.ecdict)
    for e in entries:
        if not e["phonetic"] and e["word"] in PHONETIC_FIXES:
            e["phonetic"] = PHONETIC_FIXES[e["word"]]

    # 人工核对后的逐词覆盖（优先级最高），例如改正释义或音标。
    if args.fixes and os.path.exists(args.fixes):
        with open(args.fixes, encoding="utf-8") as f:
            fixes = {k.lower(): v for k, v in json.load(f).items()}
        applied = 0
        for e in entries:
            fix = fixes.get(e["word"].lower())
            if fix:
                e.update(fix)
                applied += 1
        if applied:
            print(f"fixes: applied {applied} overrides from {args.fixes}")

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump({
            "source": "kajweb/dict CET6_3 + mahavivo/english-wordlists + ECDICT",
            "words": entries,
        }, f, ensure_ascii=False, indent=1)
    with open(args.out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(e["word"] for e in entries) + "\n")
    print(f"{len(entries)} words ({len(extras)} added) -> "
          f"{args.out_json}, {args.out_txt}")


if __name__ == "__main__":
    main()
