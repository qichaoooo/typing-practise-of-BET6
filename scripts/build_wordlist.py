#!/usr/bin/env python3
"""Convert the downloaded CET-6 JSONL (zipped) into a clean word list.

Usage:
    python3 -I build_wordlist.py data/cet6_raw.zip data/cet6.json data/cet6-words.txt
"""
import json
import re
import sys
import zipfile


def clean_cn(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s*([，。；、,;])\s*", r"\1", text)
    return text


def main(zip_path, out_json, out_txt):
    with zipfile.ZipFile(zip_path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".json"))
        raw = zf.read(name).decode("utf-8")

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        word = obj["content"]["word"]
        head = word["wordHead"].strip()
        content = word["content"]
        trans = []
        for t in content.get("trans", []):
            cn = clean_cn(t.get("tranCn"))
            if cn:
                trans.append({"pos": (t.get("pos") or "").strip(), "cn": cn})
        phrases = []
        for p in (content.get("phrase") or {}).get("phrases", [])[:2]:
            cn = clean_cn(p.get("pCn"))
            if p.get("pContent") and cn:
                phrases.append({"en": p["pContent"].strip(), "cn": cn})
        entries.append({
            "word": head,
            "phonetic": (content.get("usphone") or "").strip(),
            "trans": trans,
            "phrases": phrases,
            "rank": obj.get("wordRank", 0),
        })

    entries.sort(key=lambda e: e["rank"])
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"source": "CET6_3 (kajweb/dict)", "words": entries},
                  f, ensure_ascii=False, indent=1)
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(e["word"] for e in entries) + "\n")
    print(f"{len(entries)} words -> {out_json}, {out_txt}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
