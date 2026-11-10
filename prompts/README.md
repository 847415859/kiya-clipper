# prompts

版本化的大模型提示词。文件名带版本号（`rerank_v1.md`），首行 `version: xxx` 会写进每条结果和缓存键：
改提示词就新建一个版本文件（`rerank_v2.md`），在 `config/kiya.yaml` 里切换，再跑 `evaluate.py --rerank` 和旧版本比较。

| 文件 | 用途 | 调用方 |
| --- | --- | --- |
| `rerank_v1.md` | 精排：判断候选片段好不好笑、打分、笑点时间、建议起止、标题 | `worker/scripts/rerank.py` |
| `edit_v1.md` | 剪辑脚本初稿（综艺版）：花字、放大运镜 / 震屏、贴图 | `worker/scripts/autoedit.py` |

格式：`=== system ===` 和 `=== user ===` 两段；`{{变量}}` 由脚本填入（模板里的 JSON 花括号不用转义）。
同样的输入会命中 `worker/output/_llm_cache/` 的缓存，重跑不重复花钱；改了提示词版本号自然不命中。
