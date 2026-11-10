# 监控告警

`kiya_monitor.py` 每 3 分钟运行一次（cron），出问题时推送到你的微信。

| 推送 | 什么时候 |
| --- | --- |
| 🔴 开播了 | kiya 开播，附带“录制正常”或“录像还没开始写入” |
| ⚪ 下播了 | 本场时长、录像段数和大小、弹幕条数 |
| 📈 弹幕分析 | 下播后自动（转写 →）粗筛（弹幕 + 音频 + 画面），推送前 5 个候选 |
| ✂️ 片段已剪好 | 粗筛后自动剪成带字幕的 480p 小视频，推送剪了几段、用了什么字幕样式，可以直接开标注页审核 |
| ⚠️ 录播服务没有运行 | `brec` 服务不是 active |
| ⚠️ 开播了但没有在录制 | 开播 4 分钟后，3 分钟内仍没有录像写入（= 正在漏录） |
| ⚠️ 弹幕可能没在录 | 录像在写，但 15 分钟没有新弹幕（直播间很安静时可能误报） |
| ⚠️ 硬盘快满了 | 录像目录所在磁盘使用率 ≥ 80% |
| ⚠️ OSS 同步太久没成功 | 超过 3 小时没有成功同步 |
| ⚠️ 查询开播状态失败 | 连续 5 次查不到 B站直播间状态 |
| ✅ 已恢复 | 上面任一问题恢复时 |

同一个问题只推一次，6 小时内未恢复会再提醒一次。

## 第 1 步：选一个推送渠道，拿到密钥

任选一个即可（可以同时配多个）。各家的免费额度和实名要求会变，以官网为准。

- **Server酱**（推荐，最简单）：打开 sct.ftqq.com，微信扫码登录，复制 SendKey。消息推到“方糖”服务号。免费版每天条数有限，开播 / 下播 / 分析 / 剪好每天约 4 条，够用。
- **PushPlus**：打开 www.pushplus.plus，微信扫码登录，复制 token。
- **企业微信群机器人**：在企业微信群里添加机器人，复制 Webhook 地址；微信里可通过企业微信插件收到。
- **钉钉群机器人**：安全设置选“自定义关键词”，填 `kiya`，复制 Webhook 地址。

## 第 2 步：填进 .env（不在屏幕上显示）

```bash
cd /opt/kiya-clipper && git pull
read -rsp "粘贴 SendKey 后回车: " K && echo && \
  (grep -q '^SERVERCHAN_SENDKEY=' .env && sed -i "s|^SERVERCHAN_SENDKEY=.*|SERVERCHAN_SENDKEY=$K|" .env \
   || echo "SERVERCHAN_SENDKEY=$K" >> .env) && unset K
```

用 PushPlus 就把两处 `SERVERCHAN_SENDKEY` 换成 `PUSHPLUS_TOKEN`；企业微信换成 `WECOM_WEBHOOK`。

## 第 3 步：测试推送

```bash
python3 /opt/kiya-clipper/deploy/monitor/kiya_monitor.py --test-notify
```

手机收到“kiya 监控测试”即成功。

## 第 4 步：先手动跑一次检查，再加定时任务

```bash
python3 /opt/kiya-clipper/deploy/monitor/kiya_monitor.py --dry-run   # 只打印，不推送
crontab -e
```

在最后加一行：

```
*/3 * * * * /usr/bin/python3 /opt/kiya-clipper/deploy/monitor/kiya_monitor.py >> /var/log/kiya-monitor.log 2>&1
```

`crontab -l` 确认已加上。运行记录在 `/var/log/kiya-monitor.log`，状态文件在 `/var/lib/kiya-monitor/state.json`。

## 可选配置（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KIYA_AUTO_ANALYZE` | 1 | 下播后自动跑粗筛，0 关闭 |
| `KIYA_ANALYZE_NO_VIDEO` | 0 | 1 = 自动粗筛时跳过画面特征（更快） |
| `KIYA_ANALYZE_TIMEOUT` | 1800 | 自动粗筛超时秒数 |
| `KIYA_ASR` | 0 | 1 = 下播后先做整场语音转写（百炼，每场约 ¥1–3），候选带上主播原话。`try_asr.py` 效果验证过再打开 |
| `KIYA_ASR_TIMEOUT` | 3600 | 语音转写超时秒数 |
| `KIYA_RERANK` | 0 | 1 = 粗筛后自动用多模态大模型精排（要先有转写），推送新的前 5；先在后台手动试几场再打开 |
| `KIYA_RERANK_TIMEOUT` | 1800 | 精排超时秒数 |
| `KIYA_LEARN` | 1 | 1 = 下播后（精排前）更新精排记忆：从各场转写 / 弹幕找梗、汇总审核例子、拉主播和切片号的 B站投稿数据 |
| `KIYA_LEARN_TIMEOUT` | 900 | 更新精排记忆超时秒数 |
| `KIYA_LIVE_SCREEN` | 0 | 1 = 边播边筛：直播中每隔 `KIYA_LIVE_SCREEN_MINUTES`（默认 30）分钟，对**已录完**的分段粗筛一次（`screen.py --live`，低优先级），审核页提前有候选，第一次出候选时推送一条。只出候选：不转写、不精排、不剪切；下播后正式粗筛整体覆盖，已标的 Y/N 按片段时间位置对应过去。录完的分段不够时 5 分钟后再试 |
| `KIYA_LIVE_SCREEN_TIMEOUT` | 900 | 边播边筛单次超时秒数 |
| `KIYA_MEME_REFRESH` | 1 | 1 = 梗库每日刷新：不在直播时，每个日历日一次，抓B站热门 / 每周必看标题和热搜（只抓文本、低优先级）并重建梗库（`worker/scripts/memes.py refresh`）。新梗默认“待确认”，后台“梗库”页确认后才会被剪辑初稿用；失败当天不重试，只告警一次 |
| `KIYA_MEME_TIMEOUT` | 300 | 梗库刷新单次超时秒数 |
| `KIYA_AUTO_CUT` | 1 | 1 = 粗筛后自动把候选剪成带字幕的小视频（低优先级运行），剪完再推送一条“片段已剪好”，打开标注页就能审 |
| `KIYA_CUT_HEIGHT` | 480 | 自动剪切的分辨率（审核用，480p 剪得快、加载快） |
| `KIYA_CUT_TIMEOUT` | 5400 | 自动剪切超时秒数 |
| `KIYA_DISK_ALERT_PERCENT` | 80 | 硬盘告警阈值（%） |
| `KIYA_DANMAKU_STALE_SECONDS` | 900 | 多久没新弹幕算异常；直播间很安静导致误报时调大 |
| `KIYA_SYNC_STALE_HOURS` | 3 | 多久没同步成功算异常 |
| `KIYA_COOKIE_FILE` | /root/bili_cookies.txt | 查询开播状态被风控（412）时自动带上这份 Cookie |

## 测试

```bash
cd /opt/kiya-clipper && worker/.venv/bin/python -m pytest -q deploy/monitor/test_monitor.py
```

8 个场景：一切正常不打扰、服务停止并恢复、开播到下播、开播但漏录、弹幕中断、硬盘与同步、接口连续失败、下播自动剪切。
