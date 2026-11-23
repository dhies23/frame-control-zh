#!/usr/bin/env python3
"""Extract translatable units (text nodes / UI attributes / JS string literals)
from Frame Control HTML and JS files, recording exact character offsets so the
merge step can rewrite only those spans.

Usage: python extract.py <file>... > units.jsonl
"""
import json
import re
import sys

KEYWORDS_BEFORE_REGEX = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
}


def _regex_allowed(prev_char, prev_word):
    if prev_char is None:
        return True
    if prev_word in KEYWORDS_BEFORE_REGEX:
        return True
    return prev_char in "(,=:[!&|?{};+-*%<>~^"


def scan_js(src):
    """Return [(start, end, kind)] for string ('str') and template-quasi
    ('tmpl') spans. Comments, regex literals and interpolated code are skipped.
    """
    out = []
    n = len(src)
    i = 0
    mode = "code"
    braces = []          # open `${ ... }` interpolations, remaining depth
    seg_start = 0
    prev_char = None
    prev_word = ""
    while i < n:
        ch = src[i]
        if mode == "quasi":
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                out.append((seg_start, i, "tmpl"))
                i += 1
                mode = "code"
                prev_char = "`"
                prev_word = ""
                continue
            if ch == "$" and i + 1 < n and src[i + 1] == "{":
                out.append((seg_start, i, "tmpl"))
                i += 2
                mode = "code"
                braces.append(1)
                prev_char = "{"
                prev_word = ""
                continue
            i += 1
            continue

        # ---- code mode
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "'\"":
            q = ch
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == q or src[j] == "\n":
                    break
                j += 1
            out.append((i + 1, min(j, n), "str"))
            i = min(j + 1, n)
            prev_char = q
            prev_word = ""
            continue
        if ch == "`":
            mode = "quasi"
            seg_start = i + 1
            i += 1
            continue
        if ch == "{":
            if braces:
                braces[-1] += 1
            i += 1
            prev_char = "{"
            prev_word = ""
            continue
        if ch == "}":
            if braces:
                braces[-1] -= 1
                if braces[-1] == 0:
                    braces.pop()
                    mode = "quasi"
                    seg_start = i + 1
                    i += 1
                    continue
            i += 1
            prev_char = "}"
            prev_word = ""
            continue
        if ch == "/":
            if _regex_allowed(prev_char, prev_word):
                j = i + 1
                in_class = False
                while j < n:
                    c2 = src[j]
                    if c2 == "\\":
                        j += 2
                        continue
                    if c2 == "[":
                        in_class = True
                    elif c2 == "]":
                        in_class = False
                    elif c2 == "/" and not in_class:
                        break
                    elif c2 == "\n":
                        break
                    j += 1
                i = min(j + 1, n)
                prev_char = "/"
                prev_word = ""
                continue
            i += 1
            prev_char = ch
            continue
        if ch.isalnum() or ch in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            prev_word = src[i:j]
            prev_char = src[j - 1]
            i = j
            continue
        if not ch.isspace():
            prev_char = ch
            prev_word = ""
        i += 1
    if mode == "quasi":
        out.append((seg_start, n, "tmpl"))
    return out


SCRIPT_RE = re.compile(r"(?is)<script\b([^>]*)>(.*?)</script\s*>")
STYLE_RE = re.compile(r"(?is)<style\b[^>]*>(.*?)</style\s*>")
COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
TEXTNODE_RE = re.compile(r">([^<]+)<")
ATTR_RE = re.compile(
    r"""(?is)\b(title|aria-label|placeholder|alt|label|data-label|aria-description)\s*=\s*"([^"]*)\""""
)


def mask(src, spans):
    chars = list(src)
    for s, e in spans:
        for k in range(s, min(e, len(chars))):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


def extract_html(path, src, out):
    script_body = [(m.start(2), m.end(2)) for m in SCRIPT_RE.finditer(src)]
    skip = [(m.start(0), m.end(0)) for m in SCRIPT_RE.finditer(src)]
    skip += [(m.start(0), m.end(0)) for m in STYLE_RE.finditer(src)]
    skip += [(m.start(0), m.end(0)) for m in COMMENT_RE.finditer(src)]

    markup = mask(src, skip)
    for m in TEXTNODE_RE.finditer(markup):
        out.append({"file": path, "start": m.start(1), "end": m.end(1),
                    "kind": "html_text", "text": m.group(1)})
    for m in ATTR_RE.finditer(markup):
        out.append({"file": path, "start": m.start(2), "end": m.end(2),
                    "kind": "html_attr_" + m.group(1).lower(), "text": m.group(2)})

    for s, e in script_body:
        body = src[s:e]
        for a, b, kind in scan_js(body):
            out.append({"file": path, "start": s + a, "end": s + b,
                        "kind": "js_" + kind, "text": src[s + a:s + b]})


def extract_js(path, src, out):
    for a, b, kind in scan_js(src):
        out.append({"file": path, "start": a, "end": b, "kind": "js_" + kind,
                    "text": src[a:b]})


def main():
    units = []
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        local = []
        if path.endswith(".html"):
            extract_html(path, src, local)
        else:
            extract_js(path, src, local)
        for u in local:
            u["src_head"] = src[max(0, u["start"] - 60):u["start"]].replace("\n", " ")[-60:]
        units.extend(local)
    for u in units:
        print(json.dumps(u, ensure_ascii=False))


if __name__ == "__main__":
    main()
