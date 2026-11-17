# CLAUDE.md — vocabtype 项目要求与注意事项

面向后续接手/修改本项目的助手。用户文档见 `README.md`。

## 项目概况

- 终端里的 CET-6 单词 + 打字练习器，命令 `vocabtype`。
- 用户正在备考英语六级，希望「边背单词边练打字」；使用实体键盘，终端可能经
  SSH/Termux 连接，宽度可能较窄。
- 运行期只用 Python 标准库；构建期需要 pypinyin、pymupdf、ECDICT。
- 远端仓库：`git@github.com:qichaoooo/typing-practise-of-BET6.git`，主分支 `main`。

## 第一原则：正确性

用户明确要求：**尽量保证正确性，不希望背到错误的东西。** 数据正确 > 数量 > 丰富度。

- 不得编造释义、音标、拼音、词根、词源、例句或词频。不确定就留空/不显示，绝不猜。
- **词表范围以官方《全国大学英语四、六级考试大纲（2016 年修订版）》词表为准**：
  5,418 个词目（含拼写变体），六级词带 ★（1,263 个）。官方词表只列词形，没有
  释义/音标；释义与音标必须来自 ECDICT 等词典，不得自行编写。
- `data/official_2016.json` 是官方 PDF 的提取结果（5,377 行），是范围与级别的
  权威依据；不要凭记忆增删官方词，改动前先对照官方 PDF。
- 新增或修改词条前，至少在另一个独立来源交叉核对（mahavivo 词表、ECDICT、
  其他 CET-6 词书）。
- 来源字段 `remMethod` 是「助记」，**部分是词根拆分，部分只是联想记忆/谐音文字游戏**
  （例如 abandon → `a + band + on`）。UI 与文档一律标为「助记」，不得说成历史词源。
- 拼音由 pypinyin 自动生成，常见多音字基本正确；已人工抽查 38 个上下文相关的
  非默认读音。仍可能有罕见错误，拼音只作提示，且第二阶段打错不计分。
- ECDICT 的 `cet6` 标签有 5407 条，范围过宽，**不能**当官方词表用；它的用途是
  补释义/音标/词形/词频，以及交叉核对。
- 词条的 `level` 只能是 `cet4`、`cet6`、`extra`；`extra` 表示不在官方词表中，
  UI 标为「扩展」。不要把官方词误标成 extra，也不要把 extra 混进 cet6。
- 词频（BNC/COCA）是排名，数字越小越常用，不要解释反了。
- 不要手改 `data/cet6.json`：它是生成物，重建会覆盖。修正走下面的 fixes 文件。

## 功能规格（修改时必须保持）

### 模式

- `show`（默认）两阶段：
  1. **拼英文单词**：显示单词、音标、完整释义；本次要练的义项在释义行里加下划线。
     打对最后一个字母自动进入下一阶段；打错可退格，错误计入准确率；拼错的词
     在本次队列里往后挪约 6 位再出现一次。
  2. **全拼释义**：只显示抽中的那一个义项和逐字拼音；输入行提示 `当前：字 拼音`。
     空格随意，`v`/`u` 等价（nve/nue 都接受）；打错不计入错词、不影响准确率；
     拼音打对自动过关，否则 `Enter` 直接过关。
- `recall`：只显示中文释义，凭记忆拼英文，`Enter` 提交。
- `--no-pinyin`：`show` 跳过第二阶段，同时不抽义项、不加下划线。
- `--level all|cet6|cet4|extra`：按官方级别筛选候选词，默认 `all`。
- 头部显示 `CET4` / `CET6` / `扩展` 标签；级别来自 `level` 字段。
- 阶段 1 的比较目标是 `typing` 字段（纯 ASCII），显示仍是 `word`。带重音或短语
  的词条按 `typing` 敲，例如 `coup d'état` 敲 `coup d'etat`、`cliché` 敲 `cliche`；
  短语（如 `according to`）要打空格。

### 义项选择

- 每个词按 `；` 切成义项，每次只取一个。
- 按义项顺序做 Zipf 加权：第 i 个权重 `1/(i+1)`。这是对真实使用频率的近似
  （词库没有义项级频率数据），文档里必须说明是近似，不能说成真实频率。
- 抽中的义项在阶段 1 的完整释义里用下划线标出，且与阶段 2 练的义项一致。

### `;` 信息面板

- 阶段 1、阶段 2 都可以按 `;`：暂停练习，展示当前义项、助记、同根词、近义词、
  词形变化、例句、真题、短语、词频（有哪项显示哪项）。
- 再按 `;` 或 `Enter` 关闭面板并继续；关闭时必须清除本次面板占用的终端行，
  避免同一词多次按 `;` 时内容在屏幕上重复叠加。再次按 `;` 会重新显示。
- 面板内按 `j` / `k` 循环查看该词的其他义项（只查看，不改变正在练的义项）：
  释义行显示 `(i/n)`；同根词/近义词按当前义项的词性过滤，同词性多义项时标注
  「（词性级）」；例句/短语按义项中文关键词过滤，无匹配时显示「该义项暂无匹配
  例句/短语」；真题无法可靠对应义项，标注「（词级）」。提示语为
  `j/k 查看其他义项 · 再按 ; 继续拼写`。
- 助记是**词级**数据（源数据每词只有一条 `remMethod`），无法为每个义项提供
  不同助记。面板标注「（对应本义项）」或「（词级，主要对应：X）」；不得编造
  分义项助记。单义项词不做这些标注和过滤。
- 再按 `;` 或 `Enter` 返回，已输入的缓冲保留。
- 阶段 1 的暂停时长必须从 WPM 计时中扣除。
- `Esc` / `Ctrl-C` 在面板里同样退出。

### 释义检测与生词/熟词

- 普通模式在拼写结束后自动进入释义检测；`--no-quiz` 跳过；拼写中途 Esc 退出时
  不进入检测。
- `--review` 复习模式：只做释义检测，不拼写；不能与 `--no-quiz`、`--new`、
  `--weak` 同用。
- 检测范围：
  - 普通模式 = 本次练习的义项 + 复习池（全部未测/生词义项 + 今天到期的熟词义项）。
  - 复习模式 = 复习池。
  - 默认全部刷完；`--quiz-size N` 限制题数（0=全部），普通模式优先保留本次义项。
- 检测方式：
  - `--quiz pinyin`（默认）：显示英文单词、词性、打乱的中文选项，选项初始全部
    绿色。输入拼音进行模糊匹配（完全匹配、前缀、子串、顺序子序列、编辑相似度），
    不匹配的选项变暗（视觉排除），仍匹配的保持绿色。匹配规则：有完全匹配就只
    保留完全匹配项；否则保留分数 ≥ 0.35 且与最高分差距 ≤ 0.08 的项。
  - 输入行显示 `匹配 N 项，继续输入…` / `无匹配…` / `唯一匹配：N`。**只有唯一
    匹配时**该项加粗下划线，`;` 或 `Enter` 才提交；多项、空输入或无匹配时按键
    无效，继续输入。按 `,` 重新打乱选项；忽略大小写、空格、标点和 `v/u` 差异；
    允许退格和 Ctrl-U。
  - `--quiz choice`：四选一，按 `1-4`。
  - 干扰项来自其他词，排除原词的所有义项，避免多正确答案；优先同级别、同词性。
  - **正确义项必须来自练习时的标记**：普通模式用阶段 1 下划线标出的义项（同词
    多次出现取最后一次）；复习模式用该条义项记录；没有记录时用第一个义项。
    不得在检测时另随机一个义项。
- **复习按义项进行，不是按词**：
  - 某个词第一次进入检测时，`ensure_word_review` 会为它的**所有义项**建立记录；
    当前检测的义项写入结果，其余义项状态为 `new`（未测）。
  - 因此一个多义词会以多个义项条目进入复习池；同一个英文单词可能在一次复习中
    出现多次，题目里的词性/选项不同。
  - 未测/生词 + 对 → 熟词 L1；错 → 生词。
  - 熟词 Lk + 对 → L(k+1)（最高级不变），按新等级排下次复习。
  - 熟词 + 错 → 回生词（L0）。
  - 首次答错的义项在本轮结束后循环重刷，直到答对或用户退出；重刷只作巩固，
    不改变首次结果。
- 间隔：默认 5 级 `1,3,7,15,30` 天，`--intervals` 可调。熟词义项到期才进入
  复习池；池内抽取权重 ∝ `1/间隔`；未测/生词义项每轮都参与。间隔依据 Ebbinghaus
  复现与 SM-2/Anki 的扩展间隔，文档中不得声称权重本身来自实验数据。
- 进度存 `stats.json`，`review` 是「词 → {义项 key → 记录}」：
  ```json
  "review": {
    "stalk": {
      "n|茎，梗，柄": {"state": "known", "level": 2, "due": "2026-11-20",
                       "streak": 2, "correct": 2, "wrong": 0,
                       "last": "2026-11-17"},
      "vt|悄悄地跟踪": {"state": "new", "level": 0, "due": "2026-11-17",
                        "streak": 0, "correct": 0, "wrong": 0, "last": null}
    }
  }
  ```
  义项 key 为 `词性|中文释义`。旧版按词一条记录的格式会自动迁移到对应义项，
  并为其余义项建立 `new` 记录。另有 `quiz_sessions` 列表。
- `--stats` 按义项统计：未测 / 生词 / 熟词各等级 / 待复习 / 覆盖词数。
- `--help` 全部中文，包含用法和示例。新增选项时同步更新 README、本文档和
  `--help`。
- 单元测试在 `scripts/test_review.py`，覆盖状态转移、到期判断、池子过滤、选项
  唯一性、所有义项登记、旧格式迁移，以及拼音模糊匹配（前缀/拼错/少拼/多项匹配
  收窄到唯一）。

### 统计与按键

- WPM/击键准确率只统计阶段 1 的英文拼写；不含阶段 2 拼音，不含看题/暂停时间。
- 统计文件：`$XDG_DATA_HOME/vocabtype/stats.json`，默认
  `~/.local/share/vocabtype/stats.json`。
- 参数：`-n/--count`、`--mode show|recall`、`--level all|cet6|cet4|extra`、
  `--no-pinyin`、`--weak`、`--new`、`--no-requeue`、`--review`、
  `--quiz pinyin|choice`、`--no-quiz`、`--quiz-size`、`--no-retry`、
  `--intervals`、`--stats`、`--stats-file`、`--seed`、`-f/--file`。
  `--weak`/`--new` 在 `--level` 过滤后的池子里选词；复习模式不支持它们。
- `--help` 输出必须为中文（用法、选项说明、示例），新增参数时同步更新。
- 按键：`Esc`/`Ctrl-C` 退出；退格删除；`Ctrl-U` 清空当前输入。

## 文件结构

| 路径 | 说明 |
|---|---|
| `vocabtype` | 启动器；用 `readlink -f` 解析自身路径，可安全软链 |
| `scripts/vocabtype.py` | 主程序（运行期仅标准库） |
| `scripts/parse_official.py` | 从官方 PDF 生成 `official_2016.json`（构建期依赖 pymupdf） |
| `scripts/build_wordlist.py` | 数据构建：原始 zip + 官方词表 + ECDICT + 补充词 |
| `scripts/add_pinyin.py` | 数据构建：生成逐字拼音 `senses` |
| `scripts/test_review.py` | 复习状态机/检测池的 unittest |
| `data/official_2016.json` | 官方 2016 词表的版本化提取（5,377 行，含 ★） |
| `data/cet6.json` | 生成物，5,823 词 / 13,439 义项，**勿手改** |
| `data/extra_words.json` | 人工补充的词（部分已属于官方词表，级别以官方为准） |
| `data/word_fixes.json` | 逐词字段覆盖（最高优先级），发现释义/音标错误时使用 |
| `data/pinyin_fixes.json` | 逐义项拼音覆盖 |
| `data/cet6-words.txt` | 纯单词表，可喂给 mitype 等 |
| `data/sources/` | 原始下载文件（约 1 GB），已 gitignore |
| `.venv/` | 构建期 pypinyin + pymupdf 环境，已 gitignore |

### 修正文件格式

`data/word_fixes.json`（按词覆盖任意字段，区分大小写不敏感）：

```json
{"abandon": {"phonetic": "əˈbændən", "method": "……"}}
```

`data/pinyin_fixes.json`（key 为 `词|义项原文`，value 按义项中汉字顺序给读音）：

```json
{"cancel|取消，撤销，删去": ["qu", "xiao", "che", "xiao", "shan", "qu"]}
```

修正文件必须随仓库提交，并在提交信息里说明修正原因。

## 数据管线

```sh
# 0. 官方词表（需要官方 PDF + 官方词表纯文本 + pymupdf）
.venv/bin/python -I scripts/parse_official.py \
    data/sources/official_cet_2016.pdf data/sources/gh_cet_word_list.txt \
    data/official_2016.json

# 1. 合并词库（--official 标级别并补缺失词，--ecdict 提供释义/音标/词形/词频）
python3 -I scripts/build_wordlist.py data/cet6_raw.zip data/extra_words.json \
    data/cet6.json data/cet6-words.txt \
    --ecdict data/sources/stardict.db --official data/official_2016.json

# 2. 逐字拼音（会覆盖 senses）
python3 -m venv .venv
.venv/bin/pip install pypinyin pymupdf
.venv/bin/python -I scripts/add_pinyin.py data/cet6.json
```

- `build_wordlist.py` 最后应用 `--fixes`（默认 `data/word_fixes.json`）。
- `add_pinyin.py` 从输出 JSON 同目录读取 `pinyin_fixes.json`。
- 原始数据下载地址见 README；`data/sources/`、`.venv/` 不入库。
- 数据来源：官方 2016 词表（范围与级别）+ kajweb/dict CET6_3（助记/例句等）
  + ECDICT（新增词的释义/音标/词形/词频）+ mahavivo（补充词）。
- 官方词表覆盖率为 100%；新增词的释义只来自 ECDICT，没有助记/例句时面板就不
  显示这两项，不要为了填满而编造。

## 测试

单元测试：

```sh
python3 -I scripts/test_review.py -v
```

覆盖状态转移（生词/熟词、升级、答错回生词、满级）、到期判断、检测池过滤、
`--quiz-size` 上限、选项唯一性、记录检测义项，以及检测优先使用练习时标记的
义项。改状态机或间隔逻辑必须补/改测试。

交互用伪终端验证：

```sh
(sleep 1; printf 'stalk'; sleep 0.3; printf ';'; sleep 0.3; printf ';'; sleep 0.2; printf '\r') \
  | timeout 12 script -qec "vocabtype -f /tmp/test.json -n 1 --stats-file /tmp/vt_stats.json" /dev/null
```

- 必须用 `script` 分配 pty；程序要求 `isatty`，且用 cbreak 逐键读取。
- 输入前延迟约 1 秒：`tty.setcbreak` 用 `TCSAFLUSH`，过早送入的按键会被丢弃。
- 测试一律用 `--stats-file /tmp/...`，不要污染用户真实进度。
- 至少覆盖：`show` 完整流程、拼音阶段、`;` 面板（两个阶段、`j`/`k` 切换义项、
  关闭时清除面板）、下划线义项、错词重排、`recall`、`--no-pinyin`、`--stats`、
  `--level cet6/cet4/extra`、带空格的短语（`according to`）、带重音词
  （`coup d'état` 敲 `coup d'etat`）。
- 释义检测至少覆盖：`--quiz pinyin` 正确/错误/未匹配、`--quiz choice` 正确/错误、
  错题重刷、首次答错后状态仍为生词、`--no-retry`、`--review`、`--quiz-size`、
  `--intervals` 自定义、`--stats` 的生词/熟词/到期显示。
- 修改码后先 `python3 -I -m py_compile scripts/vocabtype.py scripts/build_wordlist.py`。

### 数据改动后的检查

- 无空词/空释义/空 `senses`；无大小写重复词；无重复的 `typing` 目标。
- 官方词表覆盖率 100%（`official_2016.json` 的每个词目都能在最终数据中匹配到，
  含拼写变体归一化）。
- 每个汉字 token 都有拼音；`senses` 数量与按 `；` 切分一致。
- 释义中不应残留 IPA 方括号注音（如 `['kɒnvɪkt]`）。
- `level` 计数合理；大纲外词必须是 `extra`，官方词不得误标 extra。
- 抽查被改词的释义、音标、助记与拼音。
- 重建后跑一遍真实数据下的交互冒烟测试。

## Git 工作流

- **用户要求：每次修改及时提交并推送，不要等用户开口。**
- 提交信息清晰；结尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 不要提交 `data/sources/`、`.venv/`、`__pycache__`、统计文件。
- 远端走 SSH（用户的 key 已配置）。

## 已知限制

- 官方大纲词表是 **5,418 个词目**（按拼写变体计）；`official_2016.json` 提取为
  5,377 行，按词形归一化后覆盖率 100%，但行数与官方口径不完全相等。
- 新增的 3,471 个官方词目的释义/音标来自 ECDICT，没有助记、同根词、例句；
  面板只显示已有的信息。
- 官方同形异义词（bank/check 等）在本项目中合并为一条，可能少一个独立义项。
- 79 个 `extra` 词不在官方词表（如 paternity、canon），UI 标「扩展」；其中个别
  只是拼写/派生变体，不一定是超纲词。
- 助记覆盖率 1,819/5,823，且不保证历史词源真实性。
- 30 个复合词/短语在 ECDICT 没有音标，界面不显示音标。
- 拼音自动生成；个别多音字可能有误，但拼音阶段不计分。
- 例句/真题来自源数据，可能是带省略号的片段。
- 义项频率是顺序近似，不是语料统计。
- 终端 UI 依赖 ANSI 颜色和 CJK 双宽字符；长信息行在窄终端会换行。
- 普通模式的释义检测紧跟在拼写之后，测的是短期记忆；长期效果靠复习模式的
  1/3/7/15/30 天间隔。
- 拼音检测是模糊匹配：可拼错、少拼或只输入一部分；不匹配的选项变暗但不会从
  列表删除。只有唯一匹配时才能用 `;`/`Enter` 提交；多项/空/无匹配按键无效。
  模糊规则可能把相近拼音留在候选集合里，继续输入可收窄；这是模糊策略的取舍。
- 错题重刷只作巩固，不改变首次检测决定的生词/熟词；这是刻意设计。
- 默认检测范围是全部未测/生词义项 + 到期熟词义项 + 本次义项，可能很长；用
  `--quiz-size` 限制。
- 多义词第一次进入复习时会一次性登记所有义项，复习池可能因此变大；同一个英文
  单词可能在一次复习中连续出现多次（不同词性/释义）。
- 旧进度文件里的词在第一次被释义检测前不属于任何义项；旧版按词记录的进度会
  迁移到当时记录的义项或第一个义项，其余义项标为未测。
- 干扰项不足时选项会少于 4 个；优先同级别、同词性，但不保证语义难度完全一致。
