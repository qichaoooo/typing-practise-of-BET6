# vocabtype — CET-6 背单词 + 打字练习

终端里把「背单词」和「练打字」合并成一件事：练习材料是 2352 个六级词汇
（音标、词性、中文释义、短语、词根记忆、同根词、近义词、例句/真题、词形变化、
词频，以及每条释义的逐字拼音）。运行期只用 Python 标准库；只有重建数据时才需要
pypinyin 和 ECDICT。

## 快速开始

```sh
vocabtype                     # 随机 30 个词：拼英文 + 全拼释义
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

### 按 `;` 查看词源与助记

拼英文或拼释义时都可以按 `;`：练习暂停，展开该词的信息——

```
释义   vt. 悄悄地跟踪
词根   s ＋ talk(说话) → 悄悄跟踪时不敢说话 → 悄悄地跟踪
同根   adj. stalked 有茎的；n. stalker 高视阔步者
近义   n. stem / tracking / handle；vt. trail / follow up on
变形   过去式 stalked，过去分词 stalked，现在分词 stalking，复数 stalks
例句   celery stalks  芹菜茎
短语   corn stalk 玉米杆；flower stalk 花柄
词频   BNC 10135 · COCA 6634 · Collins 2星
```

再按 `;`（或 `Enter`）回到原来的输入，已输入的内容保留；暂停时间不计入 WPM。
没有词根记忆的词会退回展示同根词、例句、短语等可用信息。

## 常用参数

```
-n, --count N      本次词数（默认 30）
--mode show|recall 练习模式
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

最终词库 **2352 词 / 5390 个义项**：

- 主体来自 [kajweb/dict](https://github.com/kajweb/dict) 的 CET6_3 词库（2345 词），
  清洗后保留全部词条。
- 与 [mahavivo/english-wordlists](https://github.com/mahavivo/english-wordlists) 的
  2219 个六级词交叉核对：共同 2211 词；补入对方有而 CET6_3 缺的 7 词
  （`air-condition`、`canon`、`correspondent`、`glamour`、`proceedings`、
  `telecommunication`、`weapon`）；其余差异词逐一核对后保留。
- 用 [ECDICT](https://github.com/skywind3000/ECDICT) 交叉核对：原 2345 词中 2077 个
  直接带 `cet6` 标签，其余多为六级词表也会收录的 CET4 基础词或词典未收录词。
- 释义清洗：删除混入中文释义的 IPA 注音（如 `['kɒnvɪkt]`），统一空白和标点。
- 信息补全（2352 词中）：词根记忆 1819、同根词 1973、近义词 2249、例句 2325、
  真题句 1193、短语 1792；ECDICT 补齐 2033 词的词形变化、2351 词的 BNC/COCA
  词频；缺失音标从 ECDICT 回填，个别上游也缺的手工补。
- 逐字拼音由 pypinyin 生成；拼音阶段打错不计分，个别多音字读法不准确也不影响
  练习记录。

重建步骤：

```sh
# 1. 基础词库（--ecdict 可选，用于补词形和词频）
python3 -I scripts/build_wordlist.py data/cet6_raw.zip data/extra_words.json \
    data/cet6.json data/cet6-words.txt --ecdict data/sources/stardict.db

# 2. 逐字拼音
python3 -m venv .venv && .venv/bin/pip install pypinyin
.venv/bin/python -I scripts/add_pinyin.py data/cet6.json
```

第二步会覆盖 `data/cet6.json` 里的 `senses`；只跑第一步会丢失拼音数据。
原始下载文件（`data/sources/`，约 1 GB）不入库：

- CET6_3：`https://raw.githubusercontent.com/kajweb/dict/master/book/1521164633851_CET6_3.zip`
- mahavivo：`https://raw.githubusercontent.com/mahavivo/english-wordlists/master/CET6_edited.txt`
- ECDICT：`https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip`（解压出 `stardict.db`）
