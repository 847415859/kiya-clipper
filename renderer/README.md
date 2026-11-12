# renderer

成片渲染：Remotion（React）。按后台编辑好的剪辑脚本，把一个候选片段渲染成带**放大运镜、字幕、花字、贴图、吐槽旁白、伪 UI、背景音乐**的成片（横屏 16:9）。

```
后台“成片编辑”  →  output/<场次>/edits/<序号>.json（剪辑脚本）
任务 RENDER     →  worker/scripts/render.py：剪干净的源视频 + 算字幕 + 准备素材目录
               →  node renderer/render.mjs：Remotion 渲染
               →  output/<场次>/final/<序号>_<高峰时间>_<分辨率>p.mp4
```

```
src/
├── index.jsx / Root.jsx       # 注册合成（尺寸、时长按传入的 props 计算）
├── Clip.jsx                   # 成片：[前情 <Callback>（跨场次呼应的旧画面）] → 源视频 → 运镜层 → 字幕 → 花字 / 贴图 / 旁白 / 伪UI；背景音乐 <Bgm>
├── anim.js                    # 动画数学（弹出、砸下、抖动、弹跳、推近拉回；BGM 的压低 / 淡入淡出），后台编辑器的预览也用这一份
├── fakeui.js                  # 吐槽旁白和 4 种伪 UI 的样式树（纯函数），成片（FxNode.jsx）和后台预览（FxView.vue）共用
└── components/                # Subtitles（字幕样式与 config/schema 一致）、TextFx（花字）、Sticker（贴图）、Narration、FakeUI、FxNode
render.mjs                     # 命令行入口：起本地文件服务、打包（有缓存）、渲染
```

字幕画在运镜层之外：放大时画面变大，字幕不跟着放大。

## 服务器上安装（一次性）

```bash
# 1. Node 依赖（国内镜像）
cd /opt/kiya-clipper/renderer
npm install --registry=https://registry.npmmirror.com

# 2. Chrome 运行需要的系统库（Ubuntu 24.04）
apt install -y libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 fonts-noto-cjk

# 3. 浏览器：Remotion 默认从 Google 下载，国内一般下不动，改用 npmmirror 的镜像
#    打开 https://registry.npmmirror.com/binary.html?path=chrome-for-testing/ 选一个较新的版本号（例如 131.0.6778.204）
VER=131.0.6778.204
cd /opt && wget "https://cdn.npmmirror.com/binaries/chrome-for-testing/${VER}/linux64/chrome-headless-shell-linux64.zip"
unzip -o chrome-headless-shell-linux64.zip
/opt/chrome-headless-shell-linux64/chrome-headless-shell --version
echo "REMOTION_BROWSER=/opt/chrome-headless-shell-linux64/chrome-headless-shell" >> /opt/kiya-clipper/.env
```

版本号不存在会下载失败（404），换列表里另一个版本即可。服务器能访问 Google 的话可以跳过第 3 步，Remotion 会自动下载。

## 试一下

在后台：审核页 →“编辑成片 →”→ 加几个花字 / 运镜 →“渲染 480p 预览”。或者命令行：

```bash
cd /opt/kiya-clipper/worker && source .venv/bin/activate
python scripts/render.py output/<场次>/candidates.json --rank 3 --height 480
```

第一次会先打包模板（约 1 分钟，之后有缓存）。

## 性能（2 核 2G）

- 同一时间只渲染一个（任务队列本来就是单线程），`REMOTION_CONCURRENCY` 默认 1，别调高。
- 1 分钟片段：480p 大约几分钟，1080p 可能 10–20 分钟以上。先用 480p 看效果，满意了再出 1080p。
- 渲染时 Chrome + Node 大约占 600 MB–1 GB 内存，一定要有 swap（见 deploy/admin/README.md 第 1 步）。
- 太慢的话：在你自己的电脑上装好 Node 和本仓库，跑同样的 `render.py`（录像要在本机），或者以后按需租一台按量计费的机器。

## 许可

Remotion 对个人和 3 人以内的公司免费；超过需要购买公司许可（见 remotion.dev/license）。

## 本地开发模板

```bash
npm install && npm run studio      # 浏览器里实时调动画；Root.jsx 里有示例数据
```

## 前情（跨场次呼应）

剪辑脚本里有 `callback` 时，`render.py` 先从旧场次录像剪出一小段（`callback.mp4`）和旧字幕，props 里多 `callback: {video, duration, label, subtitles}`，
`duration` 变成“前情 + 正片”的总时长，`mainDuration` 是正片时长。`Clip.jsx` 里前情放在 `<Sequence from={0}>`，正片整体放进 `<Sequence from={前情帧数}>`，
所以正片里所有效果、字幕、BGM 的时间不受前情影响。旧录像不在本地时 `render.py` 不传 `callback`，改成在正片开头加一张“前情提要”伪 UI 文字卡，并在日志里说明。

