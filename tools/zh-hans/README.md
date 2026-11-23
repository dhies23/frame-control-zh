# zh-hans：这套汉化工具

给 Frame Control 的界面文案例做简体中文化的工具链。思路是**只替换文字所在的字符区间**，不动任何代码：脚本先抽取出「哪些字符是给人看的文字」，翻译后再按同一批偏移写回去，因此 HTML 结构、JS 语法、CSS 类名、IPC 频道名、`${...}` 占位符都不会变。

所有脚本只依赖 Python 3 标准库；校验步骤额外需要 Node.js（用 `node --check` 验语法）。

## 流程

在仓库根目录执行，输出都放在 `tools/zh-hans/.work/` 下（不进版本库）。

```sh
cd tools/zh-hans
mkdir -p .work

# 1. 抽出所有候选文字（HTML 文本节点、UI 属性、JS 字符串与模板片段）
python extract.py ../../ui/index.html ../../ui/headset.html ../../ui/store.html \
  ../../ui/assistant.html ../../ui/mac-view.html ../../ui/wired-doctor.html \
  ../../ui/wireless-doctor.html ../../ui/control-center.js ../../ui/framey-host.js \
  ../../ui/nxnative.js ../../ui/artwork-settings.js ../../ui/framey-downloads.js \
  ../../ui/framey-events.js ../../ui/framey-running.js ../../app/main.js \
  ../../app/updater.js ../../app/preload.js ../../app/meta-signin.js \
  ../../app/obs-source.js ../../app/login-item.js ../../app/preferences.js \
  > .work/raw.jsonl

# 2. 过滤：去掉 URL、CSS、类名、事件名、标识符等不该翻的东西
python select.py < .work/raw.jsonl > .work/sel.jsonl

# 3. 切成批次，每批一个 JSON 数组（{id, kind, file, text, ctx}）
python chunk.py .work/sel.jsonl --size 150 --out .work/chunks

# 4. 翻译 .work/chunks/chunk_*.json，结果写成 {"<id>": "<中文>"} 存到 .work/out/chunk_*.json
#    规则见 glossary.md；译文里不要出现半角引号、反斜杠、< > &，占位符原样保留

# 5. 写回源文件（先不带 --write 看一遍覆盖率和占位符校验）
python merge.py --units .work/chunks/all_units.jsonl --out .work/out
python merge.py --units .work/chunks/all_units.jsonl --out .work/out --write
```

`select.py` 只挑「还不是中文」的文字，所以对一棵已经汉化过的树重跑，只会冒出上游新增或改写的条目，这正是跟进上游时想要的。

## 手工检查

- **结构**：汉化前后 HTML 标签序列必须一致（`extract.py` 的 mask 区域之外，`<tag ...>` 逐条比对）。属性值里的文字会变，标签本身不会。
- **语法**：`node --check` 跑每个 `<script>` 块和每个 `.js` 文件。
- **占位符**：`merge.py` 会核对 `${...}`、`%s`、`%d`、`{0}`、`&amp;` 等的序列在替换前后完全一致，并提醒首尾空格漂移。
- **残留英文**：汉化后再跑一遍 `extract.py | select.py`，剩下的条目按「ASCII 字母占比」筛一遍，就能看出有没有漏翻的界面文字（剩下的多半是 IPC 名、选择器、MIME 类型这类不该翻的东西）。

## 注意

- `chunk.py` 生成的 `ctx` 字段是原文前面的源码上下文，只给译者判断语义用，不要翻译、不要写回。
- 一些字符串片段是 HTML 属性拼接（例如 ` class="desk-only"`），必须原样保留，翻了会改坏 `classList` 开关的目标。
- 不要翻译用来做比较的字符串。判断办法：看上下文里这个字面量前后是不是 `===`、`case`、`.includes(` 这类；本项目汉化时已确认没有这类字面量被选中。
