#!/usr/bin/env bash
# 构建后台：前端（Vue）打包进后端（Spring Boot）的 jar，放到 /opt/kiya-admin/kiya-admin.jar
#   bash deploy/admin/build.sh                 # 构建（会跑后端测试，约 1 分钟）
#   SKIP_TESTS=1 bash deploy/admin/build.sh    # 跳过测试
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

echo "== 前端：npm install（国内镜像）+ vite build"
cd "$REPO/admin"
npm install --registry=https://registry.npmmirror.com --no-audit --no-fund
npm run build

echo "== 后端：mvn package（阿里云 Maven 镜像）"
cd "$REPO/backend"
MVN_ARGS=(-s "$REPO/deploy/admin/maven-settings.xml" -B package)
if [ -n "${SKIP_TESTS:-}" ]; then MVN_ARGS+=(-DskipTests); fi
mvn "${MVN_ARGS[@]}"

install -D -m 644 target/kiya-admin.jar /opt/kiya-admin/kiya-admin.jar
echo "构建完成：/opt/kiya-admin/kiya-admin.jar"
echo "重启生效：systemctl restart kiya-admin"
