# worker

Python 处理程序。阶段 0 只有一个功能：**分析弹幕，找出前 20 个弹幕高峰**，用来验证“弹幕高峰 = 有趣片段”这个核心假设。

## 安装（服务器或你的电脑都行，Python 3.10+）

```bash
cd kiya-clipper/worker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/   # 国内用阿里云镜像
python -m pytest -q tests                                                      # 5 个测试全部通过即安装成功
```

2 核 2G 的服务器上跑没问题：分析一场 4 小时直播只读弹幕 XML，几秒钟完成。

## 使用

```bash
# 分析最近一场
python scripts/analyze_danmaku.py /data/rec --latest --out output

# 分析录像目录下所有场次（按分段开始时间自动分场）
python scripts/analyze_danmaku.py /data/rec --out output

# 只看前 30 个
python scripts/analyze_danmaku.py /data/rec --latest --top 30 --out output
```

终端会打印每个高峰的时间、分数、每分钟弹幕数和高频弹幕。每场在 `output/<开播时间>/` 下生成：

| 文件 | 用途 |
| --- | --- |
| `peaks.csv` | 前 N 个高峰：时间、片段起止、分数、高频弹幕、所在视频文件。最后两列“是否有趣(Y/N)”“备注”留给你填 |
| `curve.png` | 整场热度曲线（上）和每分钟弹幕数（下），高峰标了序号 |
| `cut_candidates.sh` | 把每个高峰前后各 30 秒剪成小视频的 ffmpeg 命令，不重新编码，几秒一段 |

## 手工验证流程（阶段 0 第 0.4 步）

1. 在服务器上运行分析，再运行 `bash output/<场次>/cut_candidates.sh`，在 `clips/` 里得到 20 个约 1 分钟的小视频。
2. 打包下载到你的电脑（20 段加起来几百 MB，3M 带宽下十几到二十分钟）：
   `scp -r root@<服务器IP>:/opt/kiya-clipper/worker/output/<场次> .`
3. 逐个看，在 `peaks.csv` 的“是否有趣”列填 Y/N，备注写发生了什么（例如“恐怖游戏被吓尖叫”）。
4. 统计 Y 的比例：**超过一半 → 假设成立**，进入阶段 1；不到一半 → 看看漏掉的有趣片段有什么特征，调整 `config/kiya.yaml` 后重跑。
5. 填好的 `peaks.csv` 复制到 `eval/reviews/`，它就是评测集的第一批数据。

## 试用多模态大模型（阶段 0 第 0.3 步）

用阿里云百炼的 Qwen-VL 看片段截图 + 弹幕，判断有不有趣、给出标题，提前感受阶段 2 的效果和成本。

1. 百炼控制台开通服务，创建 API Key；在“模型广场”确认 `qwen3-vl-flash` 可用（没有就用 `qwen3-vl-plus` 等）。
2. 仓库根目录 `cp .env.example .env`，填 `DASHSCOPE_API_KEY`。**Key 只放 .env，不要贴到聊天、截图或命令行里。**
3. 运行：

```bash
# 直接测弹幕分析出的第 3 个高峰（自动抽 8 帧 + 带上高频弹幕）
python scripts/try_llm.py --peaks output/<场次>/peaks.csv --rank 3 --rec-dir /data/rec

# 还没录到直播时：随便几张直播截图
python scripts/try_llm.py --images 1.jpg 2.jpg 3.jpg --danmaku "哈哈哈 | 草 | ？？？"

# 先不花钱，只看抽帧和提示词
python scripts/try_llm.py --peaks output/<场次>/peaks.csv --rank 3 --dry-run

# 估算费用：填控制台上的单价（元 / 百万 tokens）
python scripts/try_llm.py --peaks ... --rank 3 --price-in <输入单价> --price-out <输出单价>
```

输出：是否值得切、1–10 分、类型、发生了什么、为什么、笑点在第几张图、3 个候选标题，以及耗时和 token 数。每次结果存到 `output/llm_trials/<时间>/`（截图 + `result.json`）。

建议把前 20 个高峰都跑一遍，和你自己在 `peaks.csv` 里填的“是否有趣”对比，看大模型判断得准不准。阶段 0 还没有主播原话（转写），准确率会偏低，这是正常的。

## 调参（config/kiya.yaml）

- **情绪词**：看 `peaks.csv` 的“高频弹幕”列，把 kiya 粉丝常刷的梗、口头禅加进 `emotion_words` 或 `lore_words`。
- **权重**：情绪词比单纯刷屏更可靠时，调高 `weights.emotion`。
- **弹幕延迟**：如果剪出来的片段总是“刚好错过笑点”，增大 `danmaku_delay_seconds` 或 `clip_before_seconds`。
- **高峰间隔**：同一个梗被切成两段时，增大 `min_peak_gap_seconds`。

每次调整后重跑，把命中率记到 `eval/results.csv`，方便比较哪次改动真的有效。

## 代码结构

```
worker/
├── clipper/danmaku.py   # 解析录播姬 XML；多个分段按真实时间拼成一场（断流也对得上）
├── clipper/peaks.py     # 分桶、滑动基线、稳健标准化、加权、平滑、峰值检测
├── scripts/analyze_danmaku.py   # 命令行入口：分场、输出 CSV / 曲线图 / 剪切脚本
├── scripts/try_llm.py           # 试用百炼多模态大模型判断片段（阶段 0）
└── tests/               # 用合成弹幕测试，不需要真实录像
```
