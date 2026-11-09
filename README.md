# kiya-clipper

VTuber 直播自动切片系统。首位主播：娜由纪雅kiya（B站 UID 646830928，直播间 1724773744）。

完整技术方案与开发流程见 Claude 文档《VTuber 直播切片技术方案》（“技术方案”“开发流程”两个标签页）。

## 当前进度：阶段 1 攒数据中 / 后台管理第一版

| 任务 | 文件 |
| --- | --- |
| 服务器部署录播姬，开始录制 | [deploy/README.md](deploy/README.md) |
| 监控告警：开播 / 漏录 / 硬盘 / 同步推送到微信 | [deploy/monitor/README.md](deploy/monitor/README.md) |
| 阶段 1 粗筛：弹幕 + 音频 + 画面，转写、字幕、剪切与评测 | [worker/README.md](worker/README.md) |
| 后台管理：场次、审核、任务、字幕样式、评测、配置 | [deploy/admin/README.md](deploy/admin/README.md) |
| 主播合作与授权沟通 | [docs/合作邀请与授权说明.md](docs/合作邀请与授权说明.md) |
| 评测集标注 | [eval/README.md](eval/README.md) |
| 主播档案与梗百科 | [docs/主播档案-娜由纪雅kiya.md](docs/主播档案-娜由纪雅kiya.md) |

## 目录结构

```
kiya-clipper/
├── backend/     # Spring Boot 后台：场次、审核、任务编排、字幕样式、评测
├── admin/       # 后台前端（Vue 3 + Element Plus），打包进 backend 的 jar
├── worker/      # Python：预处理、粗筛、转写、大模型、剪辑脚本（阶段 0 起）
├── renderer/    # Remotion：视频模板（阶段 3）
├── schema/      # 剪辑脚本、任务消息的 JSON Schema（阶段 2）
├── prompts/     # 版本化的大模型提示词（阶段 2）
├── deploy/      # 部署：录播姬、监控、后台（MySQL / nginx / systemd）
├── eval/        # 评测集：人工标注的高光时间点、评测结果
├── config/      # 主播配置：直播间、信号权重、弹幕词典
└── docs/        # 合作、授权等非代码文档
```

## 约定

- 密钥（API Key、云账号）只放 `.env`，不进 Git；从 `.env.example` 复制。
- 主播配置、权重、词典放 `config/*.yaml`，进 Git。
- 每次改权重、词典、提示词，都跑一遍评测并记入 `eval/results.csv`。
