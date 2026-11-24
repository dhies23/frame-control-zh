#!/usr/bin/env python3
"""Guard against a whole class of localization bugs: a string that looks like
display text but is really consumed by code.

Screen text can be translated freely. But some literals are compared against
backend values, used to build element ids, or matched with startsWith(). If those
get translated, the code silently stops matching — in the worst case a top-level
`$(...)` returns null and the whole script dies, so every button stops working
(this happened once: `for (const action of ['Install','Update','Remove'])` builds
`$('decky'+action)`; translating the array killed the page).

Usage:
    python check_code_positions.py ui/*.html ui/*.js app/*.js
Exit status 1 when something needs a human look.
"""
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff]")
LIT = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\\n]|\\.)*)`")

BEFORE_RISK = [
    (re.compile(r"(?:===|!==|==|!=)\s*$"), "和值比较（多半是后端返回的英文枚举）"),
    (re.compile(r"\bcase\s*$"), "case 标签"),
    (re.compile(r"(?:\$\(|getElementById\(|querySelector(?:All)?\()\s*$"), "选择器 / 元素 id"),
    (re.compile(r"\.(?:includes|indexOf|startsWith|endsWith|has)\(\s*$"), "子串匹配"),
    (re.compile(r"(?:addEventListener|removeEventListener|dispatchEvent)\(\s*$"), "事件名"),
    (re.compile(r"\.(?:setAttribute|getAttribute|hasAttribute)\(\s*$"), "属性名"),
    (re.compile(r"(?:dataset|classList)\.[\w$]*\s*[=!]==?\s*$"), "属性比较"),
]
FOR_OF = re.compile(r"for\s*\(\s*(?:const|let|var)\s+\w+\s+of\s*\[")

# 自我一致的写法可以放过：同一个中文串既被写进 textContent 又被读出来比较
SELF_CONSISTENT = re.compile(r"textContent\s*=\s*['\"`]")


def assigned_text(src):
    """Chinese strings the page itself writes into textContent/innerText.
    Comparing those back is self-consistent, so they are not a bug."""
    out = set()
    for line in src.splitlines():
        if not re.search(r"(textContent|innerText)\s*=", line):
            continue
        for m in LIT.finditer(line):
            lit = next((g for g in m.groups() if g is not None), "")
            if CJK.search(lit):
                out.add(lit.strip())
    return out


def main():
    problems = 0
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8").read()
        assigned = assigned_text(src)
        for lineno, line in enumerate(src.splitlines(), 1):
            if not CJK.search(line):
                continue
            flagged = []
            for m in LIT.finditer(line):
                lit = next((g for g in m.groups() if g is not None), "")
                if not CJK.search(lit):
                    continue
                before = line[:m.start()]
                why = next((w for rx, w in BEFORE_RISK if rx.search(before)), None)
                if why:
                    flagged.append((lit, why))
            if FOR_OF.search(line):
                for lit in re.findall(r"'([^']*)'", line):
                    if CJK.search(lit):
                        flagged.append((lit, "for..of 数组元素（常用来拼 id）"))
            for lit, why in flagged:
                # 自己写进去、自己读出来比较的，安全
                if lit.strip() in assigned:
                    continue
                problems += 1
                print("%s:%d  %s" % (path, lineno, why))
                print("    %s" % line.strip()[:150])
                print("    可疑字符串：%r" % lit)
    if problems:
        print("\n%d 处需要人工确认：这些串要么改回英文，要么把显示文字拆成单独的变量。" % problems)
        return 1
    print("ok：没有发现被翻译过的代码语义字符串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
