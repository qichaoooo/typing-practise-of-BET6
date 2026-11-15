# vocabtype — CET-6 背单词 + 打字练习

终端里把「背单词」和「练打字」合并成一件事：练习材料就是 2345 个六级词汇
（含音标、词性、中文释义、常用短语，以及每条释义的逐字拼音）。运行期只用
Python 标准库；只有重建拼音数据时才需要 pypinyin。

## 快速开始

```sh
vocabtype                     # 随机 30 个词：拼英文 + 全拼释义
vocabtype -n 50 --mode recall # 只看中文释义，凭记忆拼写英文
vocabtype --weak              # 专练错误率最高的词
vocabtype --new               # 只练还没练过的词
vocabtype --stats             # 查看累计进度和最近的练习记录
```

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
不想要第二阶段就加 `--no-pinyin`。

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

## 数据

`data/cet6.json` 由 [kajweb/dict](https://github.com/kajweb/dict) 的 CET6_3 词库
（2345 词）清洗生成；`senses` 字段（逐字拼音）由 `add_pinyin.py` 用 pypinyin 生成。
重新生成需要两步：

```sh
python3 -I scripts/build_wordlist.py data/cet6_raw.zip data/cet6.json data/cet6-words.txt

python3 -m venv .venv && .venv/bin/pip install pypinyin
.venv/bin/python -I scripts/add_pinyin.py data/cet6.json
```

第二步会覆盖 `data/cet6.json` 里的 `senses`；只跑第一步会丢失拼音数据。
