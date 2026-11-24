# 汉化说明

本仓库是 [saphid/frame-control](https://github.com/saphid/frame-control) 的**非官方简体中文分支**，作者与上游无关，同样以 MIT 许可证发布。这里记录汉化了什么、没汉化什么，以及上游发新版本后怎么把中文补回去。

- 上游基线：`saphid/frame-control@b8f20aa`（2026-11-24，版本 0.4.64）
- 汉化范围：全部**界面文案**，共 21 个文件、3628 条字符串
- 运行方式：见 [README 的安装一节](README.md#install)

## 汉化了什么

| 文件 | 内容 | 条数 |
|---|---|---|
| `ui/index.html` | 主界面（应用 / 游戏 / 工具 / 设置四个标签页、连接向导、头显画面、传文件、备份、Decky、模组、舒适度等） | 2463 |
| `ui/control-center.js` | 控制中心搜索的条目、描述与关键词（关键词中英双语，两种语言都能搜到） | 296 |
| `ui/framey-host.js` | Framey 运行时注入的界面文案 | 195 |
| `ui/headset.html` | 头显内打开的网页面板（SteamVR 里的那个页面） | 153 |
| `app/main.js` | Electron 菜单、窗口标题、对话框、通知 | 124 |
| `ui/store.html` | 商店页 | 94 |
| `ui/mac-view.html` | 把 Mac 窗口投进头显的页面 | 47 |
| `app/updater.js` | 更新提示、下载进度、失败原因 | 40 |
| `ui/wireless-doctor.html` | 无线适配器诊断页 | 37 |
| `ui/assistant.html` | 助手 / AI 代理页 | 34 |
| `ui/framey-downloads.js` | Framey 下载条目 | 28 |
| `app/preload.js` | 桥接层里给界面的提示文字 | 25 |
| `app/meta-signin.js` | Meta 登录窗口与提示 | 21 |
| `ui/wired-doctor.html` | 有线诊断页 | 19 |
| `ui/nxnative.js` | NX 原生控件（安装 APK 等）的按钮与提示 | 18 |
| `app/login-item.js` | 开机启动项的提示 | 10 |
| `ui/artwork-settings.js` | 封面图设置 | 6 |
| `app/obs-source.js` | OBS 源状态提示 | 6 |
| `ui/framey-events.js` | Framey 事件提示 | 5 |
| `ui/framey-running.js` | Framey 运行状态 | 4 |
| `app/preferences.js` | 偏好设置 | 3 |

另外：

- `<html lang>` 改成 `zh-CN`，主界面字体栈补上 `Microsoft YaHei` / `PingFang SC` / `Noto Sans CJK SC` 等中文字体，行高从 1.45 调到 1.5，中文不再挤在一起。
- `README.md` 是中文版（结构、链接、图片与上游一一对应，另加了 `<a id="...">` 锚点，保证上游文档里的 `README#install` 之类链接仍然有效）；英文原文保留在 `README.en.md`。
- 界面截图见 `docs/img/frame-control-zh.png`。

## 没汉化什么

- **Python 后端**（`ui/*.py`、`frame/`、`apk-catalog/`、`desktop/` 等）：日志、异常、CLI 输出、写进系统里的文件名仍是英文。少数后端直接把英文句子丢进界面（比如某些失败原因、HTTP 错误原文），这些地方会中英混排。
- **文档**：`docs/` 下 70 多篇 Markdown 保持英文，只有 `README.md` 是中文。
- **官网与静态站点**：`site/`、`docs/install.html`。
- **iOS / Android 原生端**：`ios/`、`android/` 的界面字符串。
- **测试**：`tests/` 里大量断言直接比对英文界面文字，汉化后这些断言会失败，所以本分支**删掉了上游的 CI**（`checks.yml` 等是给英文界面写的，留着只会每次推送都红）。`.github/workflows/` 里现在只剩一个 `release-win.yml`：在 GitHub 上编译 Windows 安装包与便携 zip 并挂到 Release。上游的 Python 单元测试大部分与文案无关，本地仍可运行。
- **Windows「PC 进头显」串流辅助库**：上游 CI 用 MSYS2 + GStreamer 现场编译 `desktop/`，本分支的 release 工作流没有编它。该功能在上游仍是实验性的、默认关闭；缺了它只是这一项不可用。
- 品牌名、命令、路径、URL、版本号、协议名（`Frame Control`、`SteamVR`、`Decky`、`APK`、`SSH`、`Wi-Fi` 等）按惯例保留英文。

## 翻译是怎么做的

不是逐行手改，而是**按字符区间替换**，这样代码一个字节都不会被碰到：

1. `tools/zh-hans/extract.py`：把 HTML 的文本节点、`title` / `aria-label` / `placeholder` / `alt` 等属性值，以及 JS 里的字符串字面量和模板字符串片段，连同上千个精确字符偏移一起抽出来。
2. `tools/zh-hans/select.py`：过滤掉 URL、CSS、类名、事件名、标识符等不该翻的东西，留下真正要翻的 3628 条。
3. 按 `tools/zh-hans/glossary.md` 的术语表分批翻译（`chunk.py` 切批），产出 `{id: 中文}` 的 JSON。
4. `tools/zh-hans/merge.py`：按偏移把这些 JSON 写回原文件，并校验占位符（`${...}`、`%s`、`&amp;` 等）与首尾空格没有漂移。
5. 校验：HTML 标签序列在汉化前后必须完全一致（结构没动）、`node --check` 检查每个 script 与 JS 文件语法、再扫一遍是否有漏翻的英文界面文字。

流程细节见 `tools/zh-hans/README.md`。

## 上游发新版本后怎么跟进

```sh
git remote add upstream https://github.com/saphid/frame-control.git
git fetch upstream
git merge upstream/main          # 冲突大多会落在被汉化过的行上
```

合并之后，漏掉的新文案是英文的，重新跑一遍流程即可：

```sh
cd tools/zh-hans
python extract.py ../../ui/index.html > /tmp/raw.jsonl
python select.py < /tmp/raw.jsonl > /tmp/sel.jsonl
python chunk.py /tmp/sel.jsonl --size 150 --out /tmp/chunks
# 翻译 /tmp/chunks/*.json，结果按 {id: 中文} 写进 /tmp/out/chunk_XXX.json
python merge.py --units /tmp/chunks/all_units.jsonl --out /tmp/out --write
```

`select.py` 只会挑出**还不是中文**的文字，已经翻好的段落不会重复出现在批次里，所以可以放心对整棵树重跑。

## 质量与边界

- 术语统一靠 `glossary.md`（头显、佩戴者、侧载、面板、库、商店、诊断……），但界面很长，同一句英文在不同位置可能翻了两种说法，属于可接受的差异。
- 翻译是按界面片段做的，个别句子缺少上下文，可能读起来略生硬；发现别扭或错译欢迎开 issue 指出来，带上界面位置和原文。
- 汉化不改任何行为：网络请求、SSH 命令、写入头显的文件名、IPC 频道名、CSS 类名、用来做比较的字符串都保持原样。

## 踩过的坑：被翻译掉的「代码语义字符串」

界面文字里混着一批**看起来是文案、其实被代码当数据用**的字符串。把它们翻成中文，程序不报语法错，但逻辑会静默失效；最惨的一次是整页脚本直接死在第一行，所有按钮都没反应。

第一版（zh-0.4.64 初版）就踩了三处：

| 位置 | 原文 | 翻成 | 后果 |
|---|---|---|---|
| `ui/index.html` Decky 三个按钮 | `for (const action of ['Install','Update','Remove'])` | `['安装','更新','移除']` | 循环里用 `$('decky'+action)` 取元素，取到 `null` 再设 `onclick` → **Uncaught TypeError**，脚本中断，整页按钮全失灵 |
| 同上，头显面板五个按钮 | `['Install','Update','Open','Close','Remove']` | 部分中文 | 同上；另外 `action.toLowerCase()` 出来的 `verb` 再也匹配不上 `'install'` 等分支 |
| `ui/index.html`、`ui/control-center.js` 电池状态 | `b.status === 'Charging'` 等 | `=== '充电中'` | 状态由 Python 后端返回英文，中文永远不相等 → 电量/充电态一直落到「不可用」分支 |
| `ui/index.html` 文件管理器名 | `h.fileManager === "your file manager"` | `"你的文件管理器"` | 同上，取后端英文值 |
| `app/obs-source.js` | `output.includes('Frame Control OBS stream is ready')` | 中文 | 匹配的是 Python 子进程 stdout，永远不命中 |

修法：**这些串改回英文**；需要中文的地方另做一层显示映射，例如

```js
const DECKY_ACTION_ZH = {Install: '安装', Update: '更新', Remove: '移除'};
for (const action of ['Install', 'Update', 'Remove']) {      // id 和 verb 都靠它
  runJob(`${DECKY_ACTION_ZH[action]} Decky Loader`, ...);     // 显示用中文
```

现在有守卫脚本 `tools/zh-hans/check_code_positions.py`：扫描「中文串出现在比较、`case`、选择器/id、`startsWith`/`includes`、事件名、属性名、`for..of` 数组里」的位置，自己写进 `textContent` 再读出来比较的除外。CI 的 release 工作流会在打包前跑它，命中就红。

```sh
python tools/zh-hans/check_code_positions.py ui/*.html ui/*.js app/*.js
```

另外两条自查经验：

- 改完一定要在浏览器里跑一遍，看 console 有没有 `Uncaught`。`node --check` 只能验语法，验不出 `null.onclick` 这种运行期问题。
- 后端（`ui/*.py`）返回的枚举、路径、状态一律按英文处理；中文只加在最后渲染出来的那一层。

## 发行包与自更新

Windows 安装包和便携 zip 由 `.github/workflows/release-win.yml` 在 GitHub Actions 上编译（`npm ci` → `fetch-deps` → `electron-builder` → `SHA256SUMS` → 传到 Release），推送 `zh-*` / `v*` tag 或手动 dispatch 触发。工作流不编译 `desktop/`（Windows「PC 进头显」串流辅助库，上游仍属实验性、默认关闭），所以那一项在这个包里不可用。

**自更新仍然指向上游**：`app/updater.js` 里 `REPO = "saphid/frame-control"`，检查的是上游的 release 与 `update.json`。所以点了「更新并重启」会装成**英文原版**，中文界面随之消失。要更新中文版，只能回到本仓库的 Release 页面手动下新包。要彻底关掉提示，可以在应用设置里关闭自动更新检查，或者干脆不改。
