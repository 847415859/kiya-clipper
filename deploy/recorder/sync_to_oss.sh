#!/usr/bin/env bash
# 把录像和弹幕同步到同地域 OSS（走内网，不占 3M 公网带宽，内网流量免费）
# 本地只保留最近 KEEP_DAYS 天，更早的上传后删除，防止 40G 系统盘被写满。
#
# 前提：已安装 rclone，并用 `rclone config` 配好名为 oss 的远端
#   类型选 s3 → provider 选 Alibaba → endpoint 填内网地址 oss-cn-beijing-internal.aliyuncs.com
#
# 定时运行（每小时）：crontab -e 加一行
#   7 * * * * /opt/kiya-clipper/deploy/recorder/sync_to_oss.sh >> /var/log/kiya-sync.log 2>&1
set -euo pipefail

REC_DIR="${REC_DIR:-/data/rec}"
OSS_REMOTE="${OSS_REMOTE:-oss:your-bucket-name/rec}"
KEEP_DAYS="${KEEP_DAYS:-3}"

echo "[$(date '+%F %T')] sync start"

# 1) 写完超过 10 分钟的文件先复制上去（正在录制的分段不碰）
rclone copy "$REC_DIR" "$OSS_REMOTE" --min-age 10m --transfers 2 --log-level NOTICE

# 2) 超过 KEEP_DAYS 天的视频从本地移走（move = 确认上传后再删本地）
#    弹幕 XML 很小，本地保留，方便直接在服务器上跑分析
rclone move "$REC_DIR" "$OSS_REMOTE" --min-age "${KEEP_DAYS}d" --include "*.flv" --log-level NOTICE

df -h "$REC_DIR" | tail -1
echo "[$(date '+%F %T')] sync done"
