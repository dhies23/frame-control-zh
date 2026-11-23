#!/usr/bin/env python3
"""Apply translations (id -> 中文) back into the source files by offset.

usage: python merge.py [--units all_units.jsonl] [--out outdir] [--write]
Without --write it only reports coverage.
"""
import argparse
import glob
import json
import os
import re
import sys

PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}|%[sd]|\{\d+\}|\{[a-zA-Z_][a-zA-Z0-9_]*\}|&(?:amp|lt|gt|nbsp|quot|#\d+);")


def load_units(path):
    units = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                units.append(json.loads(line))
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", default="chunks/all_units.jsonl")
    ap.add_argument("--out", default="out")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    zh = {}
    for p in sorted(glob.glob(os.path.join(a.out, "chunk_*.json"))):
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print("BAD JSON", p, e)
            continue
        for k, v in data.items():
            zh[int(k)] = v

    units = load_units(a.units)
    missing = [u["id"] for u in units if u["id"] not in zh or not str(zh[u["id"]]).strip()]
    print("units:", len(units), "translated:", len(zh), "missing:", len(missing))
    if missing[:10]:
        print("first missing ids:", missing[:10])
        for u in units:
            if u["id"] in missing[:10]:
                print("  ", u["id"], u["file"], repr(u["text"][:80]))

    # placeholder sanity
    bad = []
    for u in units:
        t = zh.get(u["id"])
        if not t:
            continue
        if PLACEHOLDER_RE.findall(u["text"]) != PLACEHOLDER_RE.findall(t):
            bad.append((u["id"], u["text"], t))
    print("placeholder mismatches:", len(bad))
    for b in bad[:15]:
        print("  ", b[0], repr(b[1][:70]), "->", repr(b[2][:70]))

    if not a.write:
        return

    by_file = {}
    for u in units:
        if u["id"] in zh and str(zh[u["id"]]).strip():
            by_file.setdefault(u["file"], []).append(u)

    for f, us in by_file.items():
        src = open(f, encoding="utf-8").read()
        span_ok = True
        for u in us:
            if src[u["start"]:u["end"]] != u["text"]:
                print("SPAN MISMATCH", f, u["id"], repr(src[u["start"]:u["end"]][:60]), repr(u["text"][:60]))
                span_ok = False
                break
        if not span_ok:
            continue
        for u in sorted(us, key=lambda x: -x["start"]):
            src = src[:u["start"]] + str(zh[u["id"]]) + src[u["end"]:]
        open(f, "w", encoding="utf-8", newline="").write(src)
        print("wrote", f, len(us), "units")


if __name__ == "__main__":
    main()
