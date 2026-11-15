# vocabtype — CET-6 背单词 + 打字练习

终端里把「背单词」和「练打字」合并成一件事：练习材料是 **5,823 个词条**
（CET4 4,434 / CET6 1,310 / 大纲外扩展 79），按《全国大学英语四、六级考试大纲
（2016 年修订版）》的官方词表标注级别。每个词条包含音标、词性、中文释义、短语、
词根/助记、同根词、近义词、例句/真题、词形变化、词频，以及每条释义的逐字拼音。
运行期只用 Python 标准库；只有重建数据时才需要 pypinyin、ECDICT 和 pymupdf。

## 快速开始

```sh
vocabtype                     # 随机 30 个词（四级+六级+扩展）：拼英文 + 全拼释义
vocabtype --level cet6        # 只练官方六级★词（推荐备考时用）
vocabtype -n 50 --mode recall # 只看中文释义，凭记忆拼写英文
vocabtype --weak              # 专练错误率最高的词
vocabtype --new               # 只练还没练过的词
vocabtype --stats             # 查看累计进度和最近的练习记录
```

练习中随时按 `;` 查看该词的词根/同根词/例句等信息，再按 `;` 返回继续拼写。

在仓库目录下直接运行 `./vocabtype`；也可以把它软链到 PATH：

```sh
ln -s "$PWD/vocabtype" ~/.local/bin/vocabtype
```

## 两种模式

| 模式 | 屏幕显示 | 适合 |
|---|---|---|
| `show`（默认） | 英文单词 + 音标 + 中文释义，两阶段练习 | 熟悉单词、练手速；边打边建立形-音-义关联 |
| `recall` | 只有中文释义 | 真正的「背单词」：回忆拼写，检验记忆 |

`show` 模式分两个阶段：

1. **拼英文单词**：打对最后一个字母自动进入下一阶段。打错可以退格，错误计入
   准确率，打错的词稍后会在本次练习中再出现一次。
2. **全拼打出释义**：每条释义逐字给出拼音，照着重打一遍。打错不计入错词、
   不影响准确率，`Enter` 可以直接过关。输入时提示当前正在拼哪个字（如
   `当前：消 xiao`），让打字和字义始终对得上。

第二阶段每次只取该词的**一个义项**，按义项顺序做 Zipf 加权（第 1 个义项权重
1、第 2 个 1/2、第 3 个 1/3……）近似真实使用频率：常见义项出现得多，冷门义项
也会偶尔出现。释义本身没有公开的义项频率数据，这是基于词典义项顺序的近似。
抽到的义项会在一开始的完整释义行里用下划线标出，练习前就能看到这次考哪一条。
不想要第二阶段就加 `--no-pinyin`。

### 按 `;` 查看助记与相关信息

拼英文或拼释义时都可以按 `;`：练习暂停，展开该词的信息——

```
释义   vt. 悄悄地跟踪
助记   s ＋ talk(说话) → 悄悄跟踪时不敢说话 → 悄悄地跟踪
同根   adj. stalked 有茎的；n. stalker 高视阔步者
近义   n. stem / tracking / handle；vt. trail / follow up on
变形   过去式 stalked，过去分词 stalked，现在分词 stalking，复数 stalks
例句   celery stalks  芹菜茎
短语   corn stalk 玉米杆；flower stalk 花柄
词频   BNC 10135 · COCA 6634 · Collins 2星
```

再按 `;`（或 `Enter`）关闭面板并回到原来的输入，已输入的内容保留；面板占用的
终端行会被清除，所以反复按 `;` 不会在屏幕上叠加重复内容。暂停时间不计入 WPM。
没有助记的词会退回展示同根词、例句、短语等可用信息。

面板里按 `j` / `k` 可以循环查看该词的其他义项：

- 释义行显示 `(i/n)`；同根词、近义词按当前义项的词性过滤；多个义项同词性时
  标注「（词性级）」。
- 例句、短语会按当前义项的中文关键词过滤；匹配不到时显示「该义项暂无匹配
  例句/短语」，不会拿别的义项的例子充数。
- 真题只有英文原文，无法可靠对应义项，因此标注「（词级）」。
- **助记是词级数据**（源数据每个词只有一条 `remMethod`），无法按义项拆成不同
  版本。面板会标注它「（对应本义项）」或「（词级，主要对应：X）」；本项目不会
  为了每个义项编造不同的助记。

注意：「助记」来自词库的 `remMethod` 字段，**部分是词根拆分，部分只是联想记忆**
（例如把 abandon 拆成 a+band+on），不代表真实的历史词源。想了解严格词源请另查
词典；本项目不会为了凑词根而编造来源。

## 常用参数

```
-n, --count N      本次词数（默认 30）
--mode show|recall 练习模式
--level LEVEL      all（默认）| cet6 | cet4 | extra，按官方级别筛选
--no-pinyin        标准模式跳过全拼释义阶段
--weak             按历史错误率优先挑选
--new              只选没练过的词
--no-requeue       错词不在本次重复
--stats            显示累计统计
-f FILE            换用其他单词数据文件
--seed N           固定随机顺序（复现同一组词）
```

按 `Esc` 或 `Ctrl-C` 随时退出并查看本次结果。进度保存在
`~/.local/share/vocabtype/stats.json`。

速度（WPM）和击键准确率只统计第一阶段拼英文单词的过程：不含看题停顿，也不含
第二阶段的释义拼音。所以数字会比网页测速偏高一些——更适合用来看自己的趋势，
而不是和别人的分数比较。

## 建议的练法

- **每次 15 分钟左右**：`vocabtype -n 40 --mode recall`，比一次练一小时有效。
- **准确率优先**：准确率低于 95% 时先放慢，别为了 WPM 数字牺牲正确率。
- **每天先 recall 再 show**：recall 检验记忆，show 用来热身和提速。
- **隔天用 `--weak`**：错误率高的词自动排到前面，形成简易的错词本。

如果只想要纯打字测速（不关心词义），项目里的 `data/cet6-words.txt` 每行一个词，
可直接喂给 mitype 之类的工具：`mitype -f data/cet6-words.txt`。

## 数据来源与核对

### 官方词表

- 《全国大学英语四、六级考试大纲（2016 年修订版）》由全国大学英语四、六级考试
  委员会编写，教育部教育考试院（NEEA）官网发布，现行有效。
  [官方大纲页](https://cet.neea.edu.cn/html1/folder/16113/1588-1.htm) ·
  [官方 PDF](https://cet.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf)
- 大纲附「四、六级考试词表」：官方口径 **5,418 个词目**（含拼写变体），其中
  **六级词用 ★ 标出（1,263 个）**。词表**只列词形**，不给词性、释义、读音。
- `data/official_2016.json` 是从官方 PDF 提取并人工核对过的词表（5,377 行，
  含 ★ 标记与派生词），随仓库版本化；提取脚本是 `scripts/parse_official.py`。
- 官方词表没有释义，所以网上所有带中文释义、音标、助记的「大纲词汇」都是二次
  加工版本。本项目用 ECDICT 补释义/音标/词形/词频，原有 kajweb 数据中的助记、
  同根词、例句等能保留的继续保留。

### 合并结果

最终 **5,823 个词条 / 13,439 个义项**：

- 级别：CET4 4,434 · CET6 1,310 · 大纲外扩展 79（界面分别标注 `CET4`、
  `CET6`、`扩展`）。
- 原 kajweb CET6_3 的 2,345 词保留；新增 **3,471 个官方词目**，释义和音标来自
  ECDICT；官方词目覆盖率为 100%。
- 同形异义词（官方分列的 `bank`、`check` 等）合并为一条。
- 少量复合词/短语在 ECDICT 没有音标（如 `according to`、`brand-new`），界面就不
  显示音标；带重音或短语的词条用 `typing` 字段给出实际敲击形式，例如
  `coup d'état` 敲 `coup d'etat`、`cliché` 敲 `cliche`。
- 信息补全（有则显示）：词根/助记 1,819、同根词 1,973、近义词 2,249、例句
  2,325、真题句 1,193、短语 1,792；ECDICT 补词形变化和 BNC/COCA 词频。
- 逐字拼音由 pypinyin 生成，38 个上下文多音字选择经过人工抽查；拼音阶段打错
  不计分。
- 发现释义、音标等错误时写入 `data/word_fixes.json`；发现拼音错误写入
  `data/pinyin_fixes.json`，再重建。两个修正文件随仓库版本化，不要手改生成物。

### 重建

```sh
# 0. 从官方 PDF 生成版本化的官方词表（需要 pymupdf）
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

原始下载文件（`data/sources/`，约 1 GB）不入库：

- 官方 2016 大纲 PDF：`https://cet.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf`
- 官方词表纯文本（用于修复 PDF 排版）：`https://raw.githubusercontent.com/JavaProgrammerLB/cet-word-list/master/word-list.txt`
- CET6_3：`https://raw.githubusercontent.com/kajweb/dict/master/book/1521164633851_CET6_3.zip`
- mahavivo：`https://raw.githubusercontent.com/mahavivo/english-wordlists/master/CET6_edited.txt`
- ECDICT：`https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip`（解压出 `stardict.db`）
