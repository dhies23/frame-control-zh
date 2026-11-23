#!/usr/bin/env python3
"""Turn raw extracted spans into work units that actually need translating.

Reads units.jsonl on stdin, writes units_sel.jsonl on stdout.
"""
import json
import re
import sys

COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
TEXTNODE_RE = re.compile(r">([^<]+)<")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]")
IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.:-]*$")
URLISH_RE = re.compile(r"^(?:https?:|/|\./|\.\./|#|\$|@|\{\{|\$\{)|://")
CSSISH_RE = re.compile(r"[{};].*:|:\s*[a-z-]+\s*;")

CODE_CTX_RE = re.compile(
    r"(?:class(Name)?\s*=\s*$|classList|setAttribute\s*\(\s*$|querySelector|"
    r"getElementById|matches\s*\(\s*$|closest\s*\(\s*$|addEventListener\s*\(\s*$|"
    r"removeEventListener\s*\(\s*$|dataset|\.id\s*=\s*$|localStorage|sessionStorage|"
    r"dispatchEvent|CustomEvent|\.type\s*=\s*$|\.key\s*=\s*$|fetch\s*\(|"
    r"\.style\.|insertAdjacent|\.className\s*=\s*$|attributeName|"
    r"postMessage\s*\(|new\s+Error\s*\(\s*$|console\.|\.join\s*\(\s*$|"
    r"\[data-|data-[a-z-]+=)"
)

# single ASCII word allowed as UI text only when it is not an identifier
STOP_SINGLE = {
    "true", "false", "null", "undefined", "nan", "ok", "get", "post", "put",
    "delete", "head", "utf-8", "json", "html", "text", "px", "em", "rem", "vh",
    "vw", "dvh", "auto", "none", "block", "flex", "grid", "hidden", "inline",
    "button", "div", "span", "input", "click", "change", "input", "scroll",
    "keydown", "keyup", "resize", "load", "error", "open", "close", "message",
    "submit", "toggle", "primary", "secondary", "danger", "ghost", "active",
    "disabled", "loading", "ready", "default", "dark", "light", "frame",
    "steam", "apk", "adb", "ssh", "jsonl", "csv", "png", "jpg", "svg", "webp",
    "aria", "role", "test", "debug", "info", "warn", "main", "index", "app",
}


def is_englishy(text):
    if len(WORD_RE.findall(text)) < 1:
        return False
    letters = sum(ch.isascii() and ch.isalpha() for ch in text)
    return letters >= 2


def clean(t):
    return t.strip()


def eligible(text, kind, ctx):
    t = clean(text)
    if not t or not is_englishy(t):
        return False
    if URLISH_RE.search(t):
        return False
    if CSSISH_RE.search(t) and len(t) < 400:
        return False
    if kind.startswith("html_attr") and t.lower() in ("frame control",):
        return False
    if kind.startswith("js_"):
        if CODE_CTX_RE.search(ctx or ""):
            return False
        words = re.findall(r"[A-Za-z][A-Za-z0-9'’_-]*", t)
        if len(words) == 1:
            w = words[0]
            if w.lower() in STOP_SINGLE:
                return False
            if IDENT_RE.match(t) and (t.islower() or t.isupper()) and len(t) <= 12:
                return False
            if not t[0].isupper():
                return False
        if len(words) == 0:
            return False
    return True


def main():
    src_cache = {}
    for line in sys.stdin:
        u = json.loads(line)
        f = u["file"]
        if f not in src_cache:
            src_cache[f] = open(f, encoding="utf-8").read()
        text = u["text"]
        if COMMENT_RE.match(text.strip()) and text.strip().startswith("<!--"):
            continue
        if "<" in text and re.search(r"<[a-zA-Z/!]", text):
            # HTML fragment inside a JS string: translate only its text nodes
            for m in TEXTNODE_RE.finditer(text):
                inner = m.group(1)
                if not eligible(inner, "html_text", ""):
                    continue
                yield_unit(u, inner, u["start"] + m.start(1), u["start"] + m.end(1))
            continue
        if not eligible(text, u["kind"], u.get("src_head", "")):
            continue
        yield_unit(u, text, u["start"], u["end"])


def yield_unit(u, text, start, end):
    print(json.dumps({"file": u["file"], "start": start, "end": end,
                      "kind": u["kind"], "text": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
