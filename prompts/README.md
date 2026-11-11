# prompts

版本化的大模型提示词。文件名带版本号（`rerank_v1.md`），首行 `version: xxx` 会写进每条结果和缓存键：
改提示词就新建一个版本文件（`rerank_v2.md`），在 `config/kiya.yaml` 里切换，再跑 `evaluate.py --rerank` 和旧版本比较。

| 文件 | 用途 | 调用方 |
| --- | --- | --- |
| `rerank_v1.md` | 精排：判断候选片段好不好笑、打分、笑点时间、建议起止、标题 | `worker/scripts/rerank.py` |
| `rerank_v2.md` | 精排（旧，可切回）：v1 + 【积累】：自动发现的梗、她的投稿、审核例子、切片号表现（`worker/scripts/learn.py` 生成，见 `worker/clipper/memory.py`） | `worker/scripts/rerank.py` |
| `rerank_v3.md` | 精排（旧，可切回）：v2 + 严格的打分校准（8 分以上一场通常不超过 2–3 个）+ 必须能单独看懂、起承转合完整 + 给“后续原话”看反应 + 起止按内容定（不凑整分钟）；多输出 `needs_context` / `complete`，脚本据此算“推荐发布”（pick） | `worker/scripts/rerank.py` |
| `rerank_v4.md` | 精排（当前使用）：v3 + 去掉“粗筛高峰”锚定 + 主播 / 观众 / 审核 / 剪辑师四个角色把关 + 证据（原话 + 秒数）、风险、置信度、开头钩子、失败原因 + 前情字幕 / 节拍 / 笑点原话 / 封面帧 | `worker/scripts/rerank.py` |
| `rerank_list_v1.md` | 同场二次比较：给通过单条评审的候选定 推荐 / 备选 / 淘汰，去重，限制一场推荐条数（`rerank.listwise`） | `worker/scripts/rerank.py` |
| `sticker_v1.md` | 给贴图写含义说明（看图写一句：画的是什么、适合的情绪、不适合的场合），结果存 `assets/stickers/stickers.json`，编排只用有说明的贴图 | `worker/scripts/describe_stickers.py` |
| `edit_v1.md` | 剪辑脚本初稿（综艺版）：花字、放大运镜 / 震屏、贴图 | `worker/scripts/autoedit.py` |
| `edit_v2.md` | 剪辑脚本初稿：v1 + 【剪辑师的习惯】：人改过 AI 初稿的记录攒够条数后总结（`worker/clipper/editdiff.py`），没攒够时写“还没有积累” | `worker/scripts/autoedit.py` |
| `edit_v3.md` | 剪辑脚本初稿：v2 + 【可用的梗】：从梗库里按情境检索出的几个已启用的梗（含义 + 用法 + 尺度），模型自己判断用不用，用了就在花字 / 贴图里写 `meme` + `meme_reason`；`max_memes` / `meme_gap` 由 `memes.max_per_clip` / `min_gap` 填入 | `worker/scripts/autoedit.py` |
| `edit_v4.md` | 剪辑脚本初稿（旧，可切回）：v3 + 【这一版的风格】`{{variant}}`：克制 / 标准 / 热闹三档的一段风格要求（`worker/clipper/variants.py`），`{{max_items}}` / `{{max_memes}}` 也按档位填 | `worker/scripts/autoedit.py` |
| `edit_v5.md` | 剪辑脚本初稿（当前使用）：v4 + 【精排给剪辑的交接】：前情字幕 / 节拍 / 笑点原话 / 开头怎么改，花字对齐节拍、推镜放在笑点前、贴图只放反应节拍 | `worker/scripts/autoedit.py` |

格式：`=== system ===` 和 `=== user ===` 两段；`{{变量}}` 由脚本填入（模板里的 JSON 花括号不用转义）。
同样的输入会命中 `worker/output/_llm_cache/` 的缓存，重跑不重复花钱；改了提示词版本号自然不命中。

**模板和记忆分开**：`rerank_v2.md` 的模板固定，`{{memory}}` 每次下播后由 learn.py 更新。所以精排会随数据变化，
但不需要每次改提示词版本；`rerank.json` 里每条都记了 `memory_version`，评测时能对上是哪一版记忆。
记忆变了，同一片段的请求内容也变了，会重新请求（不命中旧缓存）。
