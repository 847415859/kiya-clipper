#!/usr/bin/env bash
# 把录像和弹幕同步到同地域 OSS（走内网，不占 3M 公网带宽，内网流量免费）
# 磁盘余量低于 REC_MIN_FREE_GIB 时，每次最多清理一个已核验归档的旧场次。
#
# 前提：已安装 rclone，并用 `rclone config` 配好名为 oss 的远端
#   类型选 s3 → provider 选 Alibaba → endpoint 填内网地址 oss-cn-beijing-internal.aliyuncs.com
#
# 定时运行（每小时）：crontab -e 加一行
#   7 * * * * /opt/kiya-clipper/deploy/recorder/sync_to_oss.sh >> /var/log/kiya-sync.log 2>&1
set -euo pipefail

REC_DIR="${REC_DIR:-/data/rec}"
OSS_REMOTE="${OSS_REMOTE:-oss:your-bucket-name/rec}"
REC_MIN_FREE_GIB="${REC_MIN_FREE_GIB:-8}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
prune=(python3 "$script_dir/prune_recordings.py" "$REC_DIR" "$OSS_REMOTE" "$REC_MIN_FREE_GIB")

if [[ "${1:-}" == "--plan" ]]; then
    exec "${prune[@]}" --plan
fi
if [[ "$#" -ne 0 ]]; then
    echo "用法: $0 [--plan]" >&2
    exit 2
fi

exec 9<"$REC_DIR"
if ! flock -n 9; then
    echo "录像同步任务已在运行，本次跳过"
    exit 0
fi

echo "[$(date '+%F %T')] sync start"

# 1) 写完超过 10 分钟的文件先复制上去（正在录制的分段不碰）
rclone copy "$REC_DIR" "$OSS_REMOTE" --min-age 10m --transfers 2 --log-level NOTICE

# 2) 检查录像所在磁盘的余量，必要时核验并移除最老场次的本地 FLV。
"${prune[@]}"

df -h "$REC_DIR" | tail -1
echo "[$(date '+%F %T')] sync done"
