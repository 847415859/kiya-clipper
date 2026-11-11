# 贴图库（素材库）

后台「成片编辑」里用的表情包 / 贴图存在这里（不进 git）。支持三种入库方式，**只用有授权或可商用的素材**（网络来源的图带 `net_` 前缀，商用前要清点替换）。

## 入库方式

| 方式 | 入口 | 说明 |
| --- | --- | --- |
| 本地上传 | 成片编辑器 →「+ 贴图」→「我的贴图」→ 上传 | PNG（透明最好）/ JPG / GIF / WEBP，最大 5 MB，按文件头校验 |
| 网络搜索单张 | 成片编辑器 →「+ 贴图」→「网络搜索」 | 搜斗图吧，预览后点「添加」下载，文件名 `net_` 前缀 |
| 批量抓取 | `worker/scripts/fetch_stickers.py` | 按情绪标签批量灌入（破防/问号/惊讶/大笑…），断点续传 |

## 关键文件

- `stickers.json`：每个贴图的含义说明（`meaning` 画的什么 / `mood` 适合情绪 / `avoid` 不适合）。**没有含义说明的贴图，AI 编排不会用**（`clipper/stickers.py`）。
- `worker/scripts/describe_stickers.py`：让多模态模型看图自动写含义说明。
- `worker/scripts/fetch_stickers.py`：批量从斗图吧抓梗图，见下。

## 批量抓取

```bash
cd worker && source .venv/bin/activate
python scripts/fetch_stickers.py --all --dry-run      # 先看会搜到什么，不下载
python scripts/fetch_stickers.py --emotion 破防 --limit 20   # 只抓一类
python scripts/fetch_stickers.py --all                 # 全部情绪标签
python scripts/describe_stickers.py                    # 自动写含义说明（AI 编排才会用）
```

下载的文件名形如 `net_破防_01.gif`——`net_` 前缀标记「版权未验证」，商用前清点这些文件，换成授权素材或自制梗图。

## 梗 → 贴图绑定

后台「梗库」页编辑一个梗时，可以「绑定贴图」——从贴图库里选一张。这样剪辑初稿检索到某个梗时，会直接被告知用这张贴图，而不是随便选（`clipper/memes.py` 的 `offer_text` 会带出对应贴图）。
