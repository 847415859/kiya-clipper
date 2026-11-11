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
python scripts/cut.py output/<场次>/candidates.json --no-refined      # 不按精排，固定剪峰值前后各 30 秒

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
| `clips/` | cut.py 剪出的小视频；`clips/windows.json` 记每条实际剪切的整场时间范围 |

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

### 边播边筛（直播中提前出候选）

`.env` 里 `KIYA_LIVE_SCREEN=1`（默认关）后，监控脚本在直播中每 30 分钟跑一次 `python scripts/screen.py /data/rec --latest --live`：

- 只用**已经录完**的分段（录播姬每 30 分钟一段；文件 `live_screen.quiet_seconds` 秒内还在写的不算），已录部分不到 `min_seconds`（15 分钟）先不筛。
- 只出候选：`candidates.json` 带 `live: true`、`covered_seconds`，审核页顶部有"临时结果"横幅，场次列表有"边播边筛·临时"标记。不转写、不精排、不剪切、不写整场特征缓存（2 核机器录制时 CPU 吃紧，已用 `nice` 低优先级运行）。
- 各分段的音频 / 画面特征缓存在 `output/<场次>/segfeat/`，下播后的正式粗筛直接复用，只需要提取最后一段。
- 下播后正式粗筛整体覆盖 live 结果（分数是整场重新算的，序号和分数会变）：后台会把已经标的 Y/N、原因、耗时按**片段起点相近（5 秒内）**对应到新候选上；对不上的（新结果里已不在候选里）才丢掉。已有正式结果的场次，`--live` 不会覆盖它。
- 直播中编辑成片、发布包按钮是灰的（序号还会变），等下播后再做。

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

### 多版本初稿（克制 / 标准 / 热闹）

后台成片编辑器的"AI 初稿"按钮先弹出**版本选择**（克制 / 标准 / 热闹，**默认三个都选**，可以只选其中几个，任务 `AUTO_EDIT` 带 `styles`，命令行 `autoedit.py --rank N --styles restrained,standard,lively`）；人勾选的不受排名限制，选一个就只出那一版（不生成多版本文件，当前脚本和 AI 原稿就是那一版）。批量场景用 `--variants auto`：**AI 评分靠前的前 `autoedit.variants.top_n`（5）个候选**出三版（`autoedit.variants.styles` 可改成别的组合），其余只出标准版。同一份精排结论 / 原话 / 弹幕 / 梗库检索结果，按三个风格档位各出一版，其余候选只出标准版（多一版 = 多一次大模型调用，同样输入走缓存不重复花钱）：

- **克制**：只在笑点出 1–3 个效果，最多 1 个梗，几乎不用贴图（对应技术方案里的"纯享版"）；
- **标准**：原来的默认编排；
- **热闹**：效果更密、优先用梗和贴图，上限比标准版多 3 个（对应"整活版"里现在能落地的部分）。

三版各自受同一套约束：同一时刻最多 2 个效果、两次推镜隔 10 秒、梗的数量和间隔（克制版梗上限 1）。结果在 `edits/<序号>.variants.json`；`edits/<序号>.json` 是当前选中的那一版（默认标准版，编辑器改的就是它），`edits/<序号>.ai.json` 也跟着是选中的那一版——**剪辑口味对比的是"选定版本 vs 人工终稿"**。编辑器顶部可以切换版本，画面里立刻看到效果；切换会替换当前脚本（有手动修改时会先确认），并记下 `chosen_by: human`，攒着以后看哪一档更常被选。重新生成单版本（超出前 N 个，或不带 `--variants`）时旧的多版本文件会被删掉，免得和当前脚本对不上。

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

## 主播周报（给主播看的内容报告）

后台"主播周报"页（或 `python scripts/weekly_report.py [--end 2026-11-10] [--days 7]`）把最近 7 天的场次整理成一份主播看得懂的报告，纯本地统计，不调大模型、不花钱：

- **本周最火的梗 / 说法 TOP5**：主播说了几次、观众刷了几条、较上周涨跌、第一次出现在哪一幕；本周才冒出来的标"新出现"，不在主播档案里的标"新发现"（可去"精排记忆"页转正）。一个说法本周至少出现 `weekly.min_mentions`（5）次才算，不够就少列，不凑数。
- **高光场面 TOP5**：精排认为好笑的片段，按精排总分排；命中禁区话题的、审核标了不有趣的不出现。
- **哪类内容最容易出效果**（破防 / 口胡 / 高能…）：本周高光片段的类型占比，攒够 `weekly.min_type_items`（5）个才出，几个片段的占比没有参考价值；切片号绑定满 5 条成片后再加一列"切片播放表现"（相对中位数）。
- **观众常刷的词（词云）**：弹幕 n-gram 汇总，去掉无意义词、禁区词、主播名，被更长说法几乎包含的短说法去重。

页面上还有给运营看的提示（哪几场没法统计、排除了多少、数据攒到哪一步）。**"导出给主播的文档"** 生成单个 `.html`（手机 / 电脑浏览器直接打开，也能打印成 PDF），只渲染上面四块，不含审核备注、通过率、禁区、场次编号等内部信息。结果在 `output/_reports/weekly-<截止日>.json / .html`。

数据来自各场的 `ngrams.json`（转写 + 弹幕，下播后 `learn.py` 或这里第一次用到时生成并缓存，录像 / 弹幕文件被同步走之后也能统计）和 `rerank.json`，所以**要先对这些场次做过转写和精排**，周报才有梗和高光。

## 梗库（热门梗 + 向量检索，让 AI 初稿会用梗）

后台"梗库"页（或 `python scripts/memes.py refresh | search "<情境>"`）。监控脚本默认**每天抓一次**（`KIYA_MEME_REFRESH=1`，不在直播时、低优先级）：

- **来源**：主播档案里的梗 / 口头禅（含"转正"的，默认可用、不衰减）、弹幕 / 转写里发现的说法（`learn.py` 的结果）、**B站热门**（综合热门、每周必看的**标题**和**热搜词**里反复出现的短语，至少在 `memes.hot_min_titles`（3）个标题里出现）、后台手动添加的。
- **只抓文本、低频**：每天最多 4 个请求（间隔 1 秒），只取标题 / 热搜词，不抓评论、不下载任何视频；快照只存"短语 + 出现次数"，留 14 天（`output/_memory/hot/`）。**没有抓热门评论**。
- **准入**：新梗（弹幕 / 转写、B站热门来的）默认**待确认**，在后台填好含义、来历、用法、尺度（大众 / 圈层 / 谨慎）点"启用"后才会被剪辑初稿用；"谨慎"级不会自动用；命中**黑名单词**或**禁区话题**的直接拉黑，后台点启用也不行。
- **热度衰减**：B站热门来的梗按近 7 天加权日均出现次数算热度（越近权重越大），低于 `memes.min_heat` 算"降温"，检索权重 ×0.3；弹幕 / 转写来的，近 14 天没提过同样降温。主播自己的梗是"常青"，不衰减。
- **检索**：生成初稿时把精排结论（发生了什么 + 铺垫 / 笑点 / 反应）当情境，用百炼文本向量（`text-embedding-v3`，同一个 `DASHSCOPE_API_KEY`，每个文本缓存到磁盘，只在内存里算相似度）在**已启用**的梗里找 Top `retrieve_k`，相似度低于 `min_sim` 的不给——宁缺毋滥；没有 Key 或接口失败时自动退回字面匹配（梗的原话出现在情境里算最像）。
- **初稿怎么用**：`edit_v3` 提示词的【可用的梗】只列检索到的几个（含义 + 用法 + 尺度），要求模型"说得出为什么好笑才用"、写 `meme` / `meme_reason`；之后代码再卡一遍——一条最多 `max_per_clip`（2）个、两个梗至少隔 `min_gap`（15）秒，不在可用列表里的（模型编的）去掉标记，超出的整个效果去掉。编辑器右侧"用到的梗"列出每个梗和理由，不想要直接删那个效果。`edits/NN.ai.json` 里记了给过哪些梗、用了哪些、被卡掉哪些。
- **统计与淘汰**：每个梗被初稿用了几次、人工终稿里还留着几次；用过且被人工看过 ≥ `min_uses`（5）次、保留率低于 `low_retention`（40%）的权重 ×0.5（页面标"常被删"）。
- **后台决定**写在 `config/memory_decisions.json` 的 `memes` / `meme_blacklist`，`output/_memory/memes.json` 是 worker 重建的结果，两边各写各的。

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
├── scripts/screen.py    # 阶段 1 粗筛入口（--live：边播边筛，只用录完的分段）
├── clipper/weekly.py    # 主播周报：梗 TOP5、高光、类型占比、词云；导出文档只渲染给主播看的部分
├── scripts/weekly_report.py     # 主播周报入口 → output/_reports/weekly-<日期>.json / .html
├── clipper/hot.py       # B站热门标题 / 热搜 → 高频短语快照、热度（近 7 天加权）
├── clipper/embed.py     # 文本向量（百炼 OpenAI 兼容接口）+ 磁盘缓存 + 字面相似度备用
├── clipper/memes.py     # 梗库：合并来源、准入 / 黑名单 / 降温、使用与保留统计、检索、初稿里梗的数量限制
├── clipper/variants.py  # 初稿多版本：风格档位（克制 / 标准 / 热闹）、前 N 个才出、variants.json 读写
├── scripts/memes.py     # 梗库入口：refresh（抓热门 + 重建 + 预热向量）/ search → output/_memory/memes.json
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

## 审核片段的长度

`cut.py` 如果这场有精排结果（`rerank.json`），就按精排修正后的起止剪（对齐句子，30–180 秒），审核看到的就是将要成片的长度；这个候选没有精排、精排失败、或精排的起止不包含高峰时间点，就退回粗筛的固定窗口（峰值前后各 30 秒）。顺序是：先 `rerank.py`，再 `cut.py`（监控脚本的自动流程本来就是这个顺序）；已经剪过的场次，精排补跑后重新执行 `cut.py` 即可，会覆盖原来的片段。

候选的 `clip_start` / `clip_end` 不变（标注对齐、评测重叠、精排记忆都靠它），实际剪切范围写在 `clips/windows.json`，格式 `{"01": {"start": 整场秒, "end": 整场秒, "refined": true}}`。审核页的播放起点、`publish_pack.py` 的封面抽帧、`subtitle.py` 重生成字幕都读它；旧场次没有这个文件，等于候选窗口。

注意：审核时点的“开头 / 结尾太早 / 太晚”现在评的是精排修正后的边界，不再对应 `analysis.clip_before_seconds` / `clip_after_seconds` 的调参建议（后者只影响没有精排的候选）。

## 精排 v3：更严、按内容定范围

`config/kiya.yaml` 的 `rerank.prompt` 现在是 `rerank_v3`（旧的 `rerank_v2` 保留，改回去即可）。和 v2 的区别：分数校准（1–4 普通、5–6 小插曲、7 勉强、8 好、9–10 极少，一场 8 分以上通常不超过 2–3 个）；有趣必须同时满足“有明确笑点 / 不看直播也能看懂 / 起承转合完整”；除了前情（`context_before`，120 秒）还给“后续原话”（`context_after`，240 秒）；起止按内容完整性定，不凑整分钟（`min_len` 20 秒、`max_len` 480 秒；成片编辑器的素材范围上限同步放宽到 500 秒）。结果里多了 `needs_context`、`complete` 和 `pick`（推荐发布：有趣 + 能单独看懂 + 完整 + 分数 ≥ `pick_score`），审核页列表标“★推荐”；`weights.not_standalone`（默认 0.7）让不能单独看懂的往下排。

换了提示词版本，缓存不命中，要对整场重新精排（几十个请求，费用很小）：

```bash
python scripts/rerank.py output/<场次>/candidates.json     # 结尾会打印“推荐发布 N 条”
python scripts/cut.py output/<场次>/candidates.json        # 再按精排范围重新剪
python scripts/evaluate.py --rerank                         # 和人工标注对比（至少看两三场，别只看一场）
```

## 精排 v4：当“审核员”，并让下游用上精排结论

`config/kiya.yaml` 的 `rerank.prompt` 现在是 `rerank_v4`（`rerank_v3` 保留，改回去即可回滚；新字段缺失时全部按旧逻辑兜底）。和 v3 的区别：

- **去锚定**：不再告诉模型“粗筛认为高峰在第 N 秒”，只说这是程序按热度圈出的候选、热度不等于好笑，让模型自己找笑点。
- **四个角色把关**：主播（不想提的话题、隐私）、观众（前 5 秒抓不抓人、路人能不能看懂）、审核（政治 / 低俗 / 引战 / 隐私 / 导流 / 未成年人）、剪辑师（起止、前情字幕、封面帧）。
- **证据与风险**：`evidence`（1–3 句原话 + 秒数，引不出就不算有笑点）、`asr_doubt`（笑点依赖的词可能听错）、`confidence`（把握）、`risk_level` + `risks`、`hook_ok` + `hook_note`（开头拖沓要降 1 分）；`fail_reason` 在不推荐时必选（取值 = 审核页原因标签 + `incomplete` / `weak_hook` / `other`），方便和人工标注直接对照。
- **前情字幕**：只差一句背景的，不再判“不能单独看懂”，而是给 `context_caption`（≤20 字）；一句补不了的才是 `needs_context`。
- **给剪辑的交接**：`context_caption`、`beats`（2–4 个节拍）、`punch_line`、`cover_time`。
- **断章取义与人设**：她的人设是抽象 / 自黑 / 玩梗，单独剪出来容易被路人当成真失态、真冒犯。风险里单列“断章取义”（补一句字幕能避免的算 low，补不了的算 high），并新增 `in_character` / `in_character_note` 判断会不会误读她的性格。
- **开头三档**：`hook` 强 / 弱 / 无（看前 3 秒）；弱和无要写最炸的 1–2 秒在哪、能不能倒叙（`hook_ok` 由它推出，旧结果仍用 `hook_ok`）。
- **标题不得编造**：`title_candidates` 必须基于片段里真实发生的事。
- **节奏与高能弹幕**：`pace`（单笑点 / 连续 / 渐强）决定编排效果的疏密，`highlight_danmaku`（≤5 条弹幕原文）给编排当花字素材。
- **审核一眼验证**：`evidence` 每条带来源（原话 / 弹幕 / 画面）；`start_reason` / `end_reason` 说明为什么从这里切、到这里停；`types` 多类型标签（第一个 = `type`）；置信度低于 0.6 的审核页标“AI 不确定”，重点看。
- **剪辑线索更细**：`types` 决定手法（反差推镜放大表情、破防震屏 + 花字、金句大字 + 慢推、口胡吐槽花字），`highlight_times`（表情最夸张的时刻）是放大候选点，`edit_hints` 是带秒数的具体建议；`edit_v5` 全部带上。
- **输入构造**（`context.py`）：抽帧 `rerank.frames_mode: peak`——一半帧集中在粗筛高峰前后（笑点的表情多半在那里），另一半在其余位置均匀取（和高峰帧至少隔 2 秒），仍然恰好 `frames` 张；`uniform` 恢复均匀。弹幕超过 `danmaku_limit`（60）条时（`danmaku_smart: true`）约 2/3 名额按“命中情绪词 / 专属梗 + 离高峰近”取最高分的，剩下 1/3 均匀抽样保留氛围；礼物 / SC / 上舰超过 30 条时 SC 和上舰优先保留；提示词里强调 SC 的文字往往就是这一段的起因。
- **推荐（pick）更严**：除 v3 的条件外，`risk_level` 不能是 high、`in_character` 不能是 false、`confidence` ≥ `rerank.pick_confidence`（0.6）。

**同场二次比较**（`rerank.listwise: true`，提示词 `rerank_list_v1`）：单条评完后，分数 ≥ `listwise_min_score`（7）的前 `listwise_top_n`（10）条再横向比一次，分 推荐 / 备选 / 淘汰；同一个笑点被切成几条的只留一条；一场最多推荐 `listwise_max_pick`（3）条。档位写进 `llm.tier`：综合分 推荐 ×1.1、淘汰 ×0.75，`pick` 要求档位是“推荐”。只有 1 条候选、请求失败时跳过，沿用单条结论；`--ranks` 只精排部分序号时，二次比较只看这次精排的那几条。

**积累里优先放“AI 判错”的例子**：AI 给了 ≥7 分但人工标 N（误报）、AI 给了 ≤6 分但人工标 Y（漏报），排在最前，并在提示词里写明 AI 当时的分数和结论错在哪。

**下游用上精排结论**：编排提示词 `edit_v5`（`autoedit.prompt`）照着交接来编排——开头补前情字幕（模型漏了，`ensure_caption` 会在成片开头补一条）、花字对齐节拍并紧跟原话、推镜放在笑点原话前 0.2 秒、贴图只放在反应节拍；`publish_pack.py` 的封面优先用 `cover_time`；审核页展示同场档位、风险、证据、没推荐的原因、前情字幕和节拍。

没有做：短 / 长多版本剪辑、跳切（编排脚本目前只支持裁剪 + 效果，没有消费方）。
