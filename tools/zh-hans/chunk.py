#!/usr/bin/env python3
"""Split selected units into translation batches (JSON) for subagents.

usage: python chunk.py <sel.jsonl>... [--size N] [--out DIR]
"""
import argparse
import json
import os

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sel", nargs="+")
    ap.add_argument("--size", type=int, default=120)
    ap.add_argument("--out", default="chunks")
    a = ap.parse_args()

    units = []
    for p in a.sel:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    units.append(json.loads(line))

    src_cache = {}
    n = 0
    batch = []
    os.makedirs(a.out, exist_ok=True)
    for i, u in enumerate(units):
        f = u["file"]
        if f not in src_cache:
            src_cache[f] = open(f, encoding="utf-8").read()
        src = src_cache[f]
        u["id"] = i
        ctx = src[max(0, u["start"] - 90):u["start"]].replace("\n", " ")[-90:]
        batch.append({"id": i, "kind": u["kind"], "file": os.path.basename(f),
                      "text": u["text"], "ctx": ctx})
        if len(batch) >= a.size:
            _write(a.out, n, batch)
            n += 1
            batch = []
    if batch:
        _write(a.out, n, batch)
        n += 1
    with open(os.path.join(a.out, "all_units.jsonl"), "w", encoding="utf-8") as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    print("batches:", n, "units:", len(units))


def _write(out, n, batch):
    path = os.path.join(out, "chunk_%03d.json" % n)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(batch, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
