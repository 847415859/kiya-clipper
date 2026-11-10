# worker

Python 处理程序。

- **阶段 1（当前）**：`screen.py` 用**弹幕 + 音频响度 + 画面变化**三类信号粗筛候选片段，`cut.py` 精确剪切，`evaluate.py` 对照人工标注算命中率。
- 阶段 0：`analyze_danmaku.py` 只用弹幕找高峰（录像已被同步删掉时仍可用）；`try_llm.py` 试用多模态大模型。

## 阶段 1：多信号粗筛

```bash
cd /opt/kiya-clipper/worker && source .venv/bin/activate

# 1. 粗筛最近一场：首次会提取音频 / 画面特征（4 小时录像在 2 核机器上约几分钟），结果缓存
python scripts/screen.py /data/rec --latest

# 2. 剪出候选片段（默认重新编码成 720p，起点准确；--fast 不重新编码，快但起点可能偏 1–2 秒）
python scripts/cut.py output/<场次>/candidates.json
python scripts/cut.py output/<场次>/candidates.json --ranks 1 2 3      # 只剪前 3 个

# 3. 人工标注后评测，并记录这次改动
python scripts/evaluate.py
python scripts/evaluate.py --log "情绪词加了草和绷"
python scripts/evaluate.py --sweep          # 按标注试不同分数线，给出建议值
```

| 输出（output/<场次>/） | 说明 |
| --- | --- |
| `candidates.json` | 候选列表：时间、分数、各信号贡献、弹幕延迟。cut / evaluate / try_llm 都读它 |
| `peaks.csv` | 表格版，“主要信号”列显示是哪两路信号把它推上来的；最后两列给你标注 |
| `curve.png` | 综合分数和每路信号的曲线，高峰标了序号 |
| `features.npz` | 音频 / 画面特征缓存；改权重、词典后重跑只要几秒 |
| `clips/` | cut.py 剪出的小视频 |

**信号与权重**（`config/kiya.yaml` 的 `weights`）：弹幕密度 0.35、情绪词 0.20、礼物 0.15、响度突增 0.20、画面变化 0.10。录像不在本地时自动只用弹幕三项。

**弹幕延迟**：观众打字比画面慢几秒。有音频时自动用“响度起势 vs 弹幕起势”的互相关估计（终端会显示“弹幕延迟 X 秒（自动估计）”），不可靠时退回 `danmaku_delay_seconds`。

**分数线，不硬凑**：`top_n` 只是上限；`analysis.min_score` 设了以后，低于分数线的高峰不进候选、不剪、不送审（记在 `candidates.json` 的 `below_threshold` 里备查，曲线图上是灰色 ×，红虚线是分数线）。一场没有片段过线时就是 0 个，监控推送“不用审核”。分数线用 `evaluate.py --sweep` 按你的标注定：它列出每条线送审几个、保住几个有趣片段，并建议一个保住全部有趣片段的值；攒到 3 场以上再定一次。

**标注与评测**：标注页标好的结果在 `eval/reviews/<场次>.csv`；审核之外漏掉的有趣片段手写到 `eval/annotations.csv`（可以扫一遍 `transcript.txt` 找）。`evaluate.py` 把两者合并作为标准答案，输出送审数、精确率（送审里有趣的比例）、召回率（有趣片段被找到的比例）。阶段 1 验收：3 场以上召回率 ≥ 90%、精确率 ≥ 40%。

**下播后自动运行**：监控脚本在下播后会自动跑 `screen.py`，把前 5 个候选推到微信。

## 标注工具（网页）

在浏览器里逐个播放候选片段，按键标注，自动保存到 `eval/reviews/<场次>.csv`（`evaluate.py` 直接读取），不用再手改 CSV。

```bash
# 服务器上（可先剪好片段：python scripts/cut.py output/<场次>/candidates.json --height 480）
cd /opt/kiya-clipper/worker && source .venv/bin/activate
python scripts/review.py

# 你的电脑上：建隧道后浏览器打开 http://localhost:8765
ssh -N -o ServerAliveInterval=30 -L 8765:127.0.0.1:8765 root@<服务器IP>
```

- 快捷键：**Y** 有趣 · **N** 不有趣（标完自动跳下一个）· **←/→** 切换 · **空格** 播放/暂停 · **J/L** 后退/前进 5 秒 · **E** 写备注
- 还没剪的片段，页面上点“剪切这个片段”，服务器现剪（480p 加载更快）
- 每个片段旁边显示主要信号、高频弹幕；页面底部可展开本场信号曲线
- 只监听服务器本机，必须通过 SSH 隧道访问；标完 `Ctrl+C` 关掉即可
- 3M 带宽下 1 分钟 720p 片段约 10–20 秒加载，480p 快一倍

备注建议写：发生了什么、笑点大概在第几秒、边界问题（太早 / 太晚 / 被截断）。这些会直接用来调参数和写阶段 2 的提示词。

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

## 试用语音转写（阶段 2 准备）

用阿里云百炼的录音文件识别（Paraformer / Fun-ASR）把主播原话转成带时间戳的文字，**不需要 GPU**，2 核 2G 的录制机就能跑：脚本抽出 16k 单声道 MP3（约 0.24 MB/分钟）→ 走内网传到你的 OSS → 生成 6 小时有效的签名链接交给百炼 → 取回结果后删掉 OSS 上的临时文件。

| 模型 | 单价（北京） | 4 小时一场 | 说明 |
|---|---|---|---|
| paraformer-v2 | ¥0.00008/秒 | ≈ ¥1.15 | 便宜，支持中英日混说 |
| fun-asr | ¥0.00022/秒 | ≈ ¥3.17 | 更新、口语和方言更强 |

前提：百炼控制台已开通这两个模型；`.env` 里有 `DASHSCOPE_API_KEY`、`OSS_BUCKET`；rclone 的 `oss` 远端可用（阶段 0 配好的）。

```bash
# 两个模型一起跑，对比效果
python scripts/try_asr.py /tmp/kiya_test.mp4 --model paraformer-v2 fun-asr

# 录像里的一段（秒）
python scripts/try_asr.py /data/rec/<录像>.flv --start 600 --end 900
```

结果在 `output/asr_trials/<时间>/`：`<模型>.txt`（每句一行带时间）、`<模型>.json`（含词级时间戳）。重点看：人名 / 梗（kiya、ki由米、人类酱、律师函…）认得对不对，笑声、尖叫、BGM 下的句子有没有漏。专有名词错得多，阶段 2 再加热词表。

### 整场转写（对比完选定模型后）

```bash
python scripts/transcribe.py /data/rec --latest             # 最近一场 → output/<场次>/transcript.json、transcript.txt
python scripts/transcribe.py /data/rec --latest --dry-run   # 只算时长和费用
python scripts/screen.py /data/rec --latest                 # 再粗筛一次，候选带上主播原话
```

- 模型在 `config/kiya.yaml` 的 `asr.model` 里选；一场的所有分段放在一个识别任务里，时间自动拼成整场时间轴。
- 转写过的场次：`peaks.csv` 多一列“主播原话”；标注页显示原话，点一句跳到那里；`try_llm.py --peaks` 自动把原话带进提示词。
- 录像 2 天后会被同步脚本移走，要在下播后尽快转写。在 `.env` 加 `KIYA_ASR=1`，监控脚本下播后会自动“转写 → 粗筛 → 剪成带字幕的 480p 片段”（剪切由 `KIYA_AUTO_CUT` 控制，默认开），收到“片段已剪好”的推送就能直接开标注页。

### 热词表（减少名字、梗的错字）

把主播的名字、粉丝名、梗、B站常用词（舰长、舰礼、上舰…）交给百炼，识别时优先认这些词。热词功能免费，只按识别时长计费。

```bash
python scripts/hotwords.py show                                  # 看会提交哪些词（不调接口）
python scripts/hotwords.py sync                                  # 创建 / 更新热词表，ID 自动写进 .env
python scripts/try_asr.py /tmp/kiya_test.mp4                     # 用热词表识别
python scripts/try_asr.py /tmp/kiya_test.mp4 --no-hotwords       # 不用热词表，对比效果
```

- 词表 = `config/kiya.yaml` 里 `asr.hotwords` 手写的 + 主播资料 `lore`（昵称、粉丝名、观众称呼、世界观、口头禅、常见话题）+ `lore_words`，自动去重；纯数字和单字会跳过（容易误伤）。
- 看 `transcript.txt` 发现总认错的词，加到 `asr.hotwords`，再 `sync` 一次。
- 热词表跟识别模型绑定；改了 `asr.model` 要重新 `sync`（会自动删旧建新），否则转写时会提示“不匹配，未使用”。
- 权重默认 4（1–5），不要随便调高，太高会把发音相近的普通词也认成热词。

## 字幕

场次转写过（有 `output/<场次>/transcript.json`）时，`cut.py` 剪切会自动把主播原话烧录进视频，并在旁边生成同名 `.srt`（可作为 B 站 CC 字幕上传）和 `.ass`。标注页的“剪切”按钮也一样。

**第一次用先装字体**（不装的话中文会变成方块）：

```bash
apt install -y fonts-noto-cjk          # 思源黑体，默认样式用
# 综艺快乐体（kuaile 样式）见 fonts/README.md
python scripts/subtitle.py styles      # 列出样式，检查字体是否就绪
```

**样式**在 `config/subtitles.yaml` 里，现在有 4 套：`default` 白字黑边、`kuaile` 综艺快乐体、`box` 半透明底框、`karaoke` 逐字变色。主播昵称、粉丝名、专属梗（来自 `config/kiya.yaml`）会自动换色。每个字段的含义、取值范围、默认值都在 `config/schema/subtitle_style.schema.json`，以后后台管理就用这个 schema 生成编辑表单，再通过 `cut.py --sub-style-json` 下发样式，worker 这边不用改。

```bash
python scripts/subtitle.py preview --all                                   # 每套样式一张预览图 → output/sub_preview/
python scripts/subtitle.py preview --style kuaile --video output/<场次>/clips/01_xxx.mp4 --at 12   # 用真实画面预览
python scripts/cut.py output/<场次>/candidates.json --sub-style kuaile      # 用指定样式剪
python scripts/cut.py output/<场次>/candidates.json --no-subs               # 不要字幕
python scripts/subtitle.py make output/<场次>/candidates.json              # 只重新生成 .srt/.ass，不重剪视频
```

- **错字修正**：识别固定出错的词（比如“发件礼”→“发舰礼”）加到 `config/subtitles.yaml` 的 `corrections`，下次剪切生效。
- **已经剪好的片段**要加字幕或换样式，重新运行 `cut.py` 就会覆盖。

### 转写抽测（字错率）

挑 5 分钟左右、说话密集的一段，自己听写成纯文本（标点随意），和识别结果比：

```bash
python scripts/asr_eval.py output/<场次>/transcript.json --start 01:23:00 --end 01:28:00 --ref ref.txt
```

输出字错率（CER，忽略标点和空格）和最常错的片段（如“舰 → 发件”）。错的多是名字、梗时，加进热词表再测。

转写同时会写 `transcript.jsonl`（一句一行，带场次名），以后做全文检索 / 梗库时直接导入。

## 阶段 2：大模型精排 + AI 剪辑初稿

```bash
# 1. 精排：每个候选抽 8 张截图 + 片段内原话 + 片段前 60 秒前情 + 弹幕 / 礼物，交给多模态大模型
python scripts/rerank.py output/<场次>/candidates.json --dry-run     # 先不花钱，看 rerank_preview/ 里的提示词和截图
python scripts/rerank.py output/<场次>/candidates.json               # → output/<场次>/rerank.json
python scripts/rerank.py output/<场次>/candidates.json --ranks 3 7   # 只重跑几个

# 2. 比较：粗筛顺序 vs 精排顺序，哪个更接近审核页的 Y/N 标注
python scripts/evaluate.py --rerank

# 3. AI 剪辑初稿：按精排结果写一版花字 / 运镜 / 贴图（已有脚本不覆盖，加 --force 覆盖）
python scripts/autoedit.py output/<场次>/candidates.json --rank 3
```

- 后台：场次页“运行 → 大模型精排”；审核页右上角“显示 AI 精排”（没标注的片段默认不显示 AI 判断，免得先入为主、标注就没法用来评测）；编辑页“AI 初稿”；评测页“精排对比”。
- 大模型输出用 pydantic 校验，不合法会带着错误提示重问一次；请求结果按“模型 + 提示词版本 + 内容（含图片）”缓存在 `output/_llm_cache/`，重跑不重复收费；429 / 5xx 自动重试。
- 综合分 = 0.8 × 大模型分 / 10 + 0.2 × 粗筛名次，大模型判断“不好笑”再 × 0.6（`config/kiya.yaml` 的 `rerank.weights`）。
- 起止修正（`clipper/boundary.py`）：大模型给的起止点对齐到句首 / 句末，笑点前至少留 8 秒铺垫、结尾留 1.5 秒余韵，限制在 30 秒–3 分钟。编辑页没有剪辑脚本时，默认裁剪范围就用它（限制在审核片段内）。
- AI 初稿只用贴图库里已有的贴图；同一时刻最多 2 个效果、两次推镜至少隔 10 秒，超出的自动丢掉。后台保存时同样检查，太密只提醒不阻止。
- 提示词在仓库根目录 `prompts/`，改动时升版本号（见 [prompts/README.md](../prompts/README.md)），评测结果才能对应到版本。
- 费用：任务日志最后打印 token 用量；在 `rerank.price_in / price_out` 填控制台单价（元 / 百万 tokens）就显示金额。
- 下播自动精排：`.env` 加 `KIYA_RERANK=1`（需要 `KIYA_ASR=1`）。**先手动跑一两场、用“精排对比”看效果再打开。**

### 精排记忆：让精排跟着数据变

提示词模板（`prompts/rerank_v2.md`）是固定的，填进去的【积累】会随着数据变化。`scripts/learn.py` 负责更新它：下播后监控会自动跑（`KIYA_LEARN=1`，默认开），也可以在后台“精排记忆”页点“更新精排记忆”。结果写到 `output/_memory/memory.json`。

| 来源 | 变成什么 | 什么时候开始起作用 |
| --- | --- | --- |
| 各场转写 + 弹幕 | 反复出现的说法 / 梗：主播说、观众也刷、投稿标题里也有的排在前面 | 至少 2 场里都出现过 |
| 审核页 Y/N + 备注 | 给大模型看的审核例子（每次各 3 个，有备注的优先） | 标注了就会用；精排某一场时不用这一场自己的 |
| 主播自己的投稿 | 最近的标题、播放：她觉得什么值得发、最近在聊什么、标题风格 | 下次更新就有（`config` 的 `streamer.uid`） |
| 切片号投稿 | 播放 / 点赞（完播率、涨粉不公开，可以在编辑页手填），按精排类型汇总 | 后台填切片号 UID、成片编辑页绑定 BV 号，满 5 条后 |
| 精排 + 标注 | 排序权重：留一场交叉验证，比现在的命中更多才“建议” | 满 3 场；后台点“应用”才生效，然后 `rerank.py --rescore` 重排（不花钱） |

- 后台“精排记忆”页可以删掉自动发现的梗（以后不会再加回来）、手动加梗、排除某个审核例子；这些决定存在 `config/memory_decisions.json`，下次精排立刻生效，不用等重新更新记忆。
- `rerank.json` 里每条都记了 `memory_version`。记忆变了，提示词内容也跟着变，所以重跑会重新请求、重新收费，不会命中旧缓存。
- B站接口：投稿列表要 WBI 签名，还要带监控用的那份 Cookie（`KIYA_COOKIE_FILE`，默认 `/root/bili_cookies.txt`），否则机房 IP 容易被风控。拉取失败时沿用上次拉到的数据，后台页面顶部会提示。
- 梗是按字符片段统计出来的，不做分词，难免有半截词或常用说法，在后台删掉就行。删得越多，以后越干净。

### 审核反馈、剪辑口味、梗转正和禁区

**审核页的一键原因 + 两种模式。** Y/N 下面有一排原因（开头太早 / 太晚、结尾太早 / 太晚、不好笑、要前情才懂、只是念SC、重复的梗、主播不想发、特别好笑），点一下或按 1–0；选了“不好笑”这类会顺手标 N，“特别好笑”标 Y。原因、模式、耗时存在 `output/<场次>/review_meta.json`，并导出到 `eval/reviews/<场次>.csv` 新增的三列（原因、模式、耗时）。

| 模式 | 看不看 AI | 标注用在哪 |
| --- | --- | --- |
| 盲审（评测） | 标注前不显示 | 评测精排（`evaluate.py --rerank`）、校准排序权重、审核例子 |
| 快审（生产） | 始终显示，AI ≥8 分或 ≤3 分时可按 A 直接采纳 | 只做审核例子（看过 AI 再判，会被带偏，不进评测和校准） |
| 采纳 AI | — | 什么都不进（只是复述 AI） |

- 原因里“内容类”（不好笑、要前情才懂、只是念SC、重复的梗、特别好笑）会写进精排提示词的审核例子；“主播不想发”作为明确否决单独列出。
- “边界类”原因只做统计：攒够 `memory.min_boundary_votes` 票（默认 8）且 70% 以上偏向一边，才在“精排记忆”页提示调 `analysis.clip_before_seconds` / `clip_after_seconds`，不会自动改配置。
- 审核耗时只算页面可见的时间，记录每条从打开到下决定用了多久，记忆页汇总平均值。

**AI 初稿 vs 人工终稿 = 剪辑口味。** `autoedit.py` 生成初稿时另存一份原稿 `edits/<序号>.ai.json`；后台保存（或渲染 1080p 确认）时终稿里记 `human_saved_at`。两份都在才算一条人工修改，`clipper/editdiff.py` 算出删了哪些效果、改了哪些花字、裁剪挪了多少。攒够 `memory.min_edits` 条（默认 5）且来自至少 `memory.min_edit_sessions` 场（默认 2）才写进 `prompts/edit_v2.md` 的“剪辑师的习惯”；编辑器里“还原 AI 原稿”那次保存不计入。

**梗转正。** “精排记忆”页点一个自动发现的梗，选类型（梗 / 口头禅 / 常聊话题）转正：它并入主播档案，热词表（点“同步热词表”后生效）、字幕关键词、投稿标签、提示词都会固定使用。存在 `config/memory_decisions.json` 的 `promoted_lore`，不会改写 `kiya.yaml`（`clipper/kcfg.py` 读取时合并）。

**禁区话题。** 在同一页添加禁区词（至少 2 个字）。切片范围内主播原话命中时，精排把它的综合分置 0、排到最后，审核页标红并高亮命中的词，发布包的 `taboo_hits` 会提示标题 / 简介 / 原话里有禁区词。加删禁区词后后台自动对已精排的场次跑一次 `rerank.py --rescore`（不请求大模型，不花钱）。

### 音频事件（不需要额外模型）

粗筛多了三个音频信号，都缓存在特征文件里（升级后第一次粗筛会重新提取）：

- **笑声**：转写文本里的“哈哈 / 笑死 / hhh”等（需要先转写）。
- **尖叫**：响度比基线高 6 dB 以上，且高频（>2.5 kHz）能量占比明显升高。
- **音乐 / 唱歌**：音高持续平稳的比例（说话音高变化快，唱歌和 BGM 平稳）。`peaks.csv` 的“音乐”列显示每个候选的值；`analysis.music_filter: true` 时过滤音乐值超过 `music_threshold` 的高峰（歌回）。**默认关闭**，先看几场真实数据里歌回和聊天的音乐值再决定阈值。

## 成片渲染（花字 / 放大运镜 / 贴图）

在后台审核页点“编辑成片 →”编排效果，再点“渲染”；也可以命令行：

```bash
python scripts/render.py output/<场次>/candidates.json --rank 3 --height 480   # 先 480p 看效果
```

编辑页“AI 初稿”可以先让大模型排一版再微调（见上一节）。成片不限于 60 秒的审核片段：剪辑脚本的 `window`（素材范围）默认取“审核片段 ∪ 精排修正后的起止”，前后各留 5 秒；编辑页可以往前、往后扩，渲染时直接从录像重新剪。素材范围超出审核片段时，编辑器的预览视频由 `scripts/edit_preview.py` 另剪一段（480p，带字幕）。录像被同步脚本移走后就剪不了了，要先从 OSS 拉回来。剪辑脚本在 `output/<场次>/edits/<序号>.json`（字段见 `schema/edit_script.schema.json`，校验在 `clipper/edit.py`），成片在 `output/<场次>/final/`。渲染器安装与性能说明见 [renderer/README.md](../renderer/README.md)。

## 发布包（封面、标题、简介、标签）

成片编辑页右下“发布”卡片：封面（默认取精排找到的笑点那一帧，可以换成当前播放位置、改上面的大字）、标题（从精排的标题候选里选或自己写）、简介（`config` 的 `publish.desc_template`，自带“非官方切片”声明和主播链接）、标签（固定标签 + 类型 + 片段里出现的梗，最多 10 个），都有“复制”按钮。投稿仍是手动上传，类型选“转载”，来源填直播间。

```bash
python scripts/publish_pack.py output/<场次>/candidates.json --rank 3                         # 生成
python scripts/publish_pack.py output/<场次>/candidates.json --rank 3 --cover-t 5150.5 --cover-text "金油！"
```

- 封面 1920×1200（16:10），字在中间 4:3 安全区里，首页按 4:3 裁切时不会切到字。字体用 `publish.cover_font`（fc-match 写法）。
- 截图优先用原录像；录像被同步走后，依次改用成片、编辑预览、审核片段里的画面（清晰度会低一些，先渲染 1080p 成片再截最好）。
- 后台改过的标题 / 简介 / 标签不会被重新生成覆盖；点“按精排重新生成文字”才会覆盖。
- 发布后在同一张卡片里填 BV 号，精排记忆会拉它的播放数据。

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
├── clipper/session.py   # 按开播时间把分段分成一场一场
├── clipper/media.py     # 音频响度（每秒）、关键帧画面变化，按整场时间轴拼接与缓存
├── clipper/peaks.py     # 多信号分桶、滑动基线、稳健标准化、弹幕延迟估计、加权、峰值检测
├── scripts/screen.py    # 阶段 1 粗筛入口
├── scripts/cut.py       # 精确剪切（跨分段自动拼接）
├── scripts/evaluate.py  # 命中率 / 召回率评测，记录到 eval/results.csv
├── scripts/review.py    # 网页标注工具（SSH 隧道访问）
├── scripts/analyze_danmaku.py   # 命令行入口：分场、输出 CSV / 曲线图 / 剪切脚本
├── scripts/try_llm.py           # 试用百炼多模态大模型判断片段（阶段 0）
├── clipper/subtitles.py # 字幕：样式校验（按 schema）、分条、ASS/SRT 生成、烧录参数
├── scripts/subtitle.py  # 字幕样式列表 / 预览图 / 只生成字幕文件
├── clipper/edit.py      # 剪辑脚本（成片包装）校验
├── scripts/render.py    # 成片渲染：剪源视频、算字幕、调用 renderer（Remotion）
├── scripts/edit_check.py # 后台保存剪辑脚本时调用的校验
├── clipper/asr.py       # 百炼录音文件识别：抽音频、OSS 签名链接、异步任务、整场时间轴
├── scripts/transcribe.py        # 整场语音转写 → transcript.json
├── scripts/hotwords.py          # 百炼热词表：从主播资料生成、创建 / 更新 / 删除
├── scripts/try_asr.py           # 试用百炼语音转写（Paraformer / Fun-ASR），不需要 GPU
├── scripts/asr_eval.py          # 转写抽测：和手打的参考文本比字错率
├── clipper/llm.py       # 大模型调用层：OpenAI 兼容接口、磁盘缓存、重试、pydantic 校验、提示词加载
├── clipper/context.py   # 精排上下文：主播资料、原话 / 弹幕 / 礼物整理、抽帧
├── clipper/boundary.py  # 起止点对齐句子、铺垫 / 余韵、时长限制
├── scripts/rerank.py    # 阶段 2 精排 → rerank.json
├── scripts/autoedit.py  # AI 剪辑初稿 → edits/<序号>.json
├── clipper/bili.py      # B站公开接口：投稿列表（WBI 签名）、视频数据
├── clipper/memory.py    # 精排记忆：梗挖掘、审核例子、切片号类型表现、权重校准、填进提示词的文字
├── scripts/learn.py     # 更新精排记忆 → output/_memory/memory.json
├── clipper/editdiff.py  # AI 初稿 vs 人工终稿的差别 → 剪辑口味
├── clipper/kcfg.py      # 读 kiya.yaml 并合并后台决定（梗转正、禁区话题）
├── clipper/publish.py   # 发布包：封面绘制、简介模板、标签
├── scripts/publish_pack.py      # 发布包 → output/<场次>/publish/<序号>.json + 封面
├── scripts/edit_preview.py      # 编辑器预览视频（素材范围超出审核片段时）
└── tests/               # 合成弹幕 + 合成录像 + 假百炼接口的端到端测试，不需要真实录像（约 1 分钟）
```
