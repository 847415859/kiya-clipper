# Qwen 多模态研究接入

更新日期：2026-11-19。

本接入提供实际视频画面与音频理解的研究入口，供可洛参考视频深拆使用。它不改生产精排模型、不修改旧研究结论、不恢复暂停的人工任务，也不自动批准生产规则。

## 1. 实现范围

- `worker/clipper/qwen_multimodal.py`：独立 Qwen 研究客户端，视频/音频 Base64 输入、SSE 流式响应、证据结构校验、缓存和用量记录。
- `worker/scripts/qwen_research.py`：配置检查、本机媒体准备、小范围真实调用和结果落盘。
- `worker/tests/test_qwen_multimodal.py`：本机假 HTTP/SSE 服务及真实 FFmpeg 集成检查，不使用真实凭证或付费接口。

画面使用无声视频，音频使用从同一区间提取的 WAV，分别调用模型，独立保存观察。两者共享成片时间映射，但当前入口不自动声称已经完成联合语义融合、完整事件识别或跨视频风格归纳。

## 2. 配置

凭证放在项目根目录 `.env`，该文件被 Git 忽略。也支持进程环境变量，或用 `KIYA_ENV_FILE` 指定环境文件；不要把 Key 放进命令行或聊天。

```dotenv
DASHSCOPE_API_KEY=填写百炼Key
DASHSCOPE_BASE_URL=填写该Key对应地域与业务空间的兼容接口根地址
QWEN_VISION_MODEL=qwen3-vl-plus
QWEN_AUDIO_MODEL=qwen3-omni-flash
```

地址应止于 `/compatible-mode/v1`，不要重复追加 `/chat/completions`。Key、地域和业务空间须匹配。地址从账号控制台实际说明复制；新业务空间地址可能包含空间 ID，不应照抄其他人的空间地址。

若研究使用独立服务，可配置 `QWEN_API_KEY`、`QWEN_BASE_URL`，优先于对应 `DASHSCOPE_*`。默认模型是与现有项目及已核对接口兼容的起点，账号未开通时配置为该账号实际可用的同模态模型，不能只改名字就认为能力已验证。

模型参数独立于 `DASHSCOPE_MODEL`，不会因研究配置改变现有精排。进程环境变量优先于 `.env`；改配置后重新启动命令。单次 CLI 会重新加载配置。

## 3. 使用

以下命令从项目根目录运行。Windows 可用 `py -3.11`，Linux 将其替换为 worker 虚拟环境的 Python。

### 检查配置，不调用模型

```powershell
py -3.11 worker/scripts/qwen_research.py --check
```

只显示凭证是否存在、模型名和配置状态，不显示密钥。返回码 2 表示配置缺失或不合法；配置存在不代表真实调用已成功。

### 准备可洛片段，不调用模型

```powershell
py -3.11 worker/scripts/qwen_research.py --video worker/output/_reference_study/recency_pilot_v1/BV1nFnG6WEDH/proxy.mp4 --start 30 --duration 20 --dry-run
```

准备视觉 MP4、音频 WAV 和本地清单，不上传、不产生模型调用费用。输入必须有对应媒体流，区间不能超过输入视频时长。

### 实际分析画面与声音

```powershell
py -3.11 worker/scripts/qwen_research.py --video worker/output/_reference_study/recency_pilot_v1/BV1nFnG6WEDH/proxy.mp4 --start 30 --duration 20
```

默认分别执行一次视觉与音频请求；有匹配缓存时复用。`--mode visual` 只分析画面，`--mode audio` 只听音；`--refresh` 强制重调，会增加费用。当前入口不自动重试网络、额度或无效输出，避免无界重复计费。

时长默认 20 秒，每次最大 60 秒。片段本机重编码至不超过 1280×720、保持比例的无声视频，音频为 16 kHz 单声道 PCM。小字和细节仍需原分辨率补证，单声道输入不能用于声道定位结论。

每件媒体编码后的 Base64 必须低于 9.5 MB 的本地限制，提前拒绝过大输入。此限制是针对已核对官方限额的保守值，不代表所有模型、地域和未来版本都有同一限额。

## 4. 输出与状态

默认写入：

```text
worker/output/_reference_study/qwen_runs/
  <时间_随机标识>/
    visual.mp4
    audio.wav
    run.json
    capability_probe.json
  cache/<请求哈希>.json
```

`run.json` 保存源文件哈希、提交片段哈希、时间区间、实际模型、模态、结构化观察、不确定项、返回用量、耗时和失败状态。请求中的完整 Base64、Authorization、上游错误正文和推理过程不写入报告。

模型时间先使用本次片段内整数毫秒，程序校验范围后增加片段起点，得到 `absolute_start_ms/absolute_end_ms`。原始局部时间仍保留，避免重复偏移。时间准确性还需证据复看，不因字段是毫秒就认为具有毫秒测量精度。

| 状态 | 含义 |
| --- | --- |
| `prepared_no_api_calls` | 已准备媒体，无外部调用 |
| `partial_or_failed` | 至少一个模态尚未成功，错误单独保存 |
| `api_schema_validated_needs_review` | 所选模态真实调用并通过结构/时间检查，尚未验证语义准确性 |

所有结果保持 `human_confirmed=false`、`production_approved=false`。这不是失败，而是避免把接口联通升级成风格结论或人工批准。

视觉请求禁止输出声音观察，音频请求禁止输出画面观察；越界时间、不合结构、被截断/过滤或不完整的流式结果不能写入成功缓存。空观察列表仍可能是模型的有效回答，不自动计作识别到动作。

缓存区分接口、模型、请求内容、参数版本及绝对区间；缓存命中不代表发起了新调用。缓存以本地研究数据保护，不提交 Git。

## 5. 怎样继续多模态深拆

当前入口先解决真实调用链。三条整片研究仍应按《多模态风格深拆自主执行任务书》继续，不把一次 20 秒连通测试作为完整试跑：

1. 在不同内容段做真实输入试验，复看具体画面和听音结果。
2. 以受限片段推进整片，保存块起点，合并跨块上下文并去重。
3. 对重要动作提高画面精度，并核对实际声音。
4. 独立复核节点与动作，再写剪切案例、编排图和候选配方。
5. 通过研究验收后扩展样本，仍然保留人工状态与生产批准的区别。

本入口不覆盖旧 `capability_probe.json`、旧 JSONL 或 SQLite 账本。后续执行器应引用真实新调用记录，按实际能力更新新阶段状态，不把旧的硬编码“无语义接口”结论永久沿用。

## 6. 测试与接口依据

```powershell
py -3.11 -m pytest -q worker/tests/test_qwen_multimodal.py
```

测试覆盖真实本机 HTTP/SSE 传输、中文流拼接、用量、截断拒绝、HTTP 错误脱敏、无自动重试、模态与时间越界、缓存、实际 FFmpeg 片段准备和 CLI 双模态流程。模拟接口测试不代替账号权限、真实识别效果或平台限额验证。

接口实现依据：

- [百炼图像与视频理解](https://help.aliyun.com/zh/model-studio/vision)。
- [Qwen-Omni 输入与流式调用](https://help.aliyun.com/zh/model-studio/qwen-omni)。
- [qwen3-omni-flash 能力说明](https://help.aliyun.com/zh/model-studio/qwen3-omni-flash)。

音频文本输出采用实际流式接口，不请求音频生成；不依赖该旧版模型未提供的强制结构化输出能力，而是在提示词约束后本地校验 JSON。

## 7. 首次真实联调结果

2026-11-19，已使用用户配置的百炼服务实际运行可洛 `BV1nFnG6WEDH` 的 30–50 秒区间。两次请求均非缓存命中：视觉模型 `qwen3-vl-plus`、音频模型 `qwen3-omni-flash` 返回通过 JSON、时间与模态检查的结果。报告为 `worker/output/_reference_study/qwen_runs/20261119T020257Z_607dec0a/run.json`。

视觉返回包含人物构图、局部放大和电影插入画面；抽查片段内第 3 秒截图，确有猿类角色与人物的电影画面插入，与对应视觉描述相符。这只是一个画面样本核对，不是全段剪切时机或全部台词准确性的验收。音频已实际提交并收到语义输出，但尚未独立逐句听审。

该联调证明真实请求链路已可用，不能继续把旧探测文件中的“无语义服务”视为当前全局结论。三条整片研究、独立复核、规则归纳和 kiya 样片验证仍需后续执行，不自动更新旧任务的完成状态。
