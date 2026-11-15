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
import itertools
import json
import os
import re
import sqlite3
import unicodedata
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


POS_MAP = {
    "a": "adj", "adj": "adj", "ad": "adv", "adv": "adv", "n": "n",
    "v": "v", "vt": "vt", "vi": "vi", "prep": "prep", "conj": "conj",
    "pron": "pron", "num": "num", "art": "art", "int": "int", "aux": "aux",
}


def expand_official(word):
    """把官方词表的写法展开成具体拼写。

    armo(u)r -> armor / armour；analyze/-yse -> analyze / analyse；
    a/an -> a / an；coup (d'état) -> coup d'état。
    """
    word = word.replace("Ｇ", "-").strip()
    if word == "coup (d'état)":
        return {"coup d'état"}
    matches = list(re.finditer(r"\((.*?)\)", word))
    out = set()
    for bits in itertools.product([0, 1], repeat=len(matches)):
        text, idx = "", 0
        for m, keep in zip(matches, bits):
            text += word[idx:m.start()]
            if keep:
                text += m.group(1)
            idx = m.end()
        text += word[idx:]
        out.add(text.strip())
    result = set(out)
    for w in list(out):
        if "/" in w:
            parts = w.split("/")
            first = parts[0]
            result.add(first)
            for part in parts[1:]:
                if part.startswith("-"):
                    letters = part[1:]
                    result.add(first[:max(0, len(first) - len(letters))] + letters)
                else:
                    result.add(part)
    return {w for w in result if re.search(r"[A-Za-z]", w)}


def match_key(text):
    """匹配用：保留字母（含带音标字母），去掉空格、连字符、撇号等。"""
    return re.sub(r"[^a-zà-ÿ]", "", text.lower())


def ascii_typing(text):
    """练习时实际要敲的 ASCII 形式（é -> e，去掉声调符号）。"""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    s = "".join(c for c in s if ord(c) < 128)
    return s.strip() or text


def build_official_maps(official):
    level_map, folded_map = {}, {}

    def add(text, level):
        for variant in expand_official(text):
            key = match_key(variant)
            if not key:
                continue
            if level_map.get(key) != "cet6":
                level_map[key] = level
            folded = match_key(ascii_typing(variant))
            if folded and folded_map.get(folded) != "cet6":
                folded_map[folded] = level

    for entry in official:
        add(entry["word"], entry["level"])
        for derivative in entry["derivatives"]:
            add(derivative, entry["level"])
    return level_map, folded_map


def parse_ecdict_translation(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?:\[([^\]]+)\]\s*)?([A-Za-z]+\.)?\s*(.+)$", line)
        if not m:
            continue
        domain, pos, cn = m.groups()
        cn = clean_text(cn)
        cn = cn.replace(", ", "，").replace(",", "，")
        if not cn:
            continue
        pos = (pos or "")[:-1].lower() if pos else ""
        pos = POS_MAP.get(pos, pos)
        if domain:
            cn = f"[{domain}] {cn}"
        out.append({"pos": pos, "cn": cn})
    return out


def ecdict_candidates(cur, spelling):
    """返回 (row, exact) ；先查原拼写，再查去音标后的拼写。"""
    for candidate, exact in ((spelling, True), (ascii_typing(spelling), False)):
        row = cur.execute(
            "SELECT translation, phonetic, exchange, bnc, frq, collins, oxford "
            "FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 1",
            (candidate,)).fetchone()
        if row:
            return row, exact
    return None, False


def choose_official_spelling(variants, cur):
    scored = []
    for variant in variants:
        row, exact = ecdict_candidates(cur, variant)
        if not row or not row[0]:
            continue
        ranks = [x for x in (row[3], row[4]) if x]
        rank = min(ranks) if ranks else 10 ** 9
        scored.append(((0 if exact else 1, rank, len(variant)), variant, row))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    _, spelling, row = scored[0]
    return spelling, row


def add_official_entries(entries, official, cur):
    existing = {match_key(e["word"]) for e in entries}
    folded = {match_key(ascii_typing(e["word"])) for e in entries}
    next_rank = max((e["rank"] for e in entries), default=0) + 1
    added, skipped = 0, []

    for oe in official:
        variants = expand_official(oe["word"])
        if ({match_key(v) for v in variants} & existing or
                {match_key(ascii_typing(v)) for v in variants} & folded):
            continue
        chosen = choose_official_spelling(variants, cur)
        if chosen is None:
            skipped.append(oe["word"])
            continue
        spelling, row = chosen
        trans = parse_ecdict_translation(row[0])
        if not any(CJK.search(t["cn"]) for t in trans):
            skipped.append(oe["word"])
            continue
        entry = {
            "word": spelling,
            "typing": ascii_typing(spelling),
            "phonetic": (row[1] or "").strip(),
            "trans": trans,
            "phrases": [],
            "method": "",
            "family": [],
            "synonyms": [],
            "examples": [],
            "exam": [],
            "rank": next_rank,
            "level": oe["level"],
        }
        forms = parse_exchange(row[2])
        if forms:
            entry["forms"] = forms
        freq = {k: v for k, v in
                (("bnc", row[3]), ("frq", row[4]), ("collins", row[5]),
                 ("oxford", row[6])) if v}
        if freq:
            entry["freq"] = freq
        entries.append(entry)
        existing.add(match_key(spelling))
        folded.add(match_key(ascii_typing(spelling)))
        next_rank += 1
        added += 1

    return added, skipped


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("zip_path")
    p.add_argument("extras_path")
    p.add_argument("out_json")
    p.add_argument("out_txt")
    p.add_argument("--ecdict", help="ECDICT stardict.db (optional)")
    p.add_argument("--official", default="data/official_2016.json",
                   help="official 2016 word list (optional)")
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

    official = []
    if args.official and os.path.exists(args.official):
        with open(args.official, encoding="utf-8") as f:
            official = json.load(f)["entries"]
        level_map, folded_map = build_official_maps(official)
        for e in entries:
            e["level"] = (level_map.get(match_key(e["word"]))
                          or folded_map.get(match_key(ascii_typing(e["word"])),
                                           "extra"))
        if args.ecdict:
            con = sqlite3.connect(args.ecdict)
            added, skipped = add_official_entries(entries, official, con.cursor())
            con.close()
            print(f"official: added {added} missing headwords, "
                  f"skipped {len(skipped)}")
            if skipped:
                print("  skipped sample:", skipped[:10])
        else:
            print("official: levels applied; --ecdict is needed to add "
                  "missing headwords")
    else:
        for e in entries:
            e.setdefault("level", "extra")

    for e in entries:
        e.setdefault("typing", ascii_typing(e["word"]))

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
            "source": ("kajweb/dict CET6_3 + mahavivo/english-wordlists + "
                       "全国大学英语四、六级考试大纲（2016 年修订版）词表 + "
                       "ECDICT"),
            "words": entries,
        }, f, ensure_ascii=False, indent=1)
    with open(args.out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(e["word"] for e in entries) + "\n")
    levels = {k: sum(1 for e in entries if e.get("level") == k)
              for k in ("cet4", "cet6", "extra")}
    print(f"{len(entries)} words ({len(extras)} extras; "
          f"cet4 {levels['cet4']}, cet6 {levels['cet6']}, "
          f"extra {levels['extra']}) -> {args.out_json}, {args.out_txt}")


if __name__ == "__main__":
    main()
