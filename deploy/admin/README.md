# 后台管理部署（Spring Boot + Vue + MySQL 8）

后台功能：场次（扫描录像、进度）、候选审核（取代 `review.py`）、任务编排（转写 / 粗筛 / 剪切 / 全流程，看日志、可取消）、字幕样式（按 schema 自动生成表单、实时预览、设为默认）、评测（评测、分数线扫描、历史记录）、配置（额外热词、纠错词、改密码）。

```
浏览器 ──HTTPS:8443──▶ nginx ──▶ 127.0.0.1:8080 kiya-admin.jar ──▶ MySQL 8（127.0.0.1:3306）
                                                     └─ 调用 worker/.venv 里的 Python 脚本（同一时间只跑一个）
```

和现有部分的关系：

- 数据：worker 的输出文件（`worker/output/<场次>/`）仍是事实来源；数据库只存场次索引、人工标注、字幕样式、任务记录。标注会同步导出到 `eval/reviews/<场次>.csv`，`evaluate.py` 照常可用；之前用 `review.py` 标的结果会自动导入。
- 设置：后台改的默认字幕样式、额外热词、纠错词写到 `config/admin_overrides.json`，监控的下播自动剪切、命令行脚本也会用这套设置。
- 监控脚本（cron）不变，下播后照常自动转写、粗筛、剪切。

内存预算（2 核 2G）：MySQL 约 200 MB、后台约 350 MB、录播姬约 150 MB，剪切 / 粗筛时 Python + ffmpeg 再用 300–500 MB。**先加 2 GB swap**，避免内存不够时进程被系统杀掉。

---

## 1. 加 swap（一次性）

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/99-swap.conf && sysctl -p /etc/sysctl.d/99-swap.conf
free -m
```

## 2. 安装 MySQL 8、JDK 17、Maven、Node.js

```bash
apt update
apt install -y mysql-server openjdk-17-jdk-headless maven nodejs npm nginx
cp /opt/kiya-clipper/deploy/admin/mysql-lowmem.cnf /etc/mysql/mysql.conf.d/kiya-lowmem.cnf
systemctl restart mysql
java -version && mvn -v && node -v       # node 需要 18 以上
```

## 3. 建数据库和账号

密码只在终端里输入，不要写进命令历史或截图；用字母和数字，不要带引号。

**先单独运行这一行**，输入密码后回车（不要和下面的命令一起粘贴，否则 `read` 会把粘贴的下一行当成密码）：

```bash
read -rsp "数据库密码：" DBPW; echo; echo "长度 ${#DBPW}"
```

再粘贴下面这段（每条命令一行）：

```bash
mysql -e "CREATE DATABASE IF NOT EXISTS kiya_admin DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; CREATE USER IF NOT EXISTS 'kiya'@'127.0.0.1' IDENTIFIED BY '${DBPW}'; ALTER USER 'kiya'@'127.0.0.1' IDENTIFIED BY '${DBPW}'; GRANT ALL PRIVILEGES ON kiya_admin.* TO 'kiya'@'127.0.0.1'; FLUSH PRIVILEGES;"
install -m 600 /dev/null /etc/kiya-admin.env
printf 'DB_PASSWORD=%s\nKIYA_REPO_DIR=/opt/kiya-clipper\nREC_DIR=/data/rec\n' "$DBPW" > /etc/kiya-admin.env
MYSQL_PWD="$DBPW" mysql -h127.0.0.1 -ukiya kiya_admin -e "SELECT '数据库连接正常' AS ok"
unset DBPW
```

看到“数据库连接正常”即可。`/etc/kiya-admin.env` 只有 root 能读，后台从这里拿数据库密码。

表结构在后台第一次启动时自动创建（`backend/src/main/resources/schema.sql`）。

## 4. 构建

```bash
cd /opt/kiya-clipper && git pull
bash deploy/admin/build.sh
```

第一次要下载依赖，几分钟。前端用 npmmirror、后端用阿里云 Maven 镜像。构建时会跑后端的冒烟测试（内存数据库，不碰 MySQL）；测试报错时先把报错发出来，急用可以 `SKIP_TESTS=1 bash deploy/admin/build.sh`。

服务器内存紧时，也可以在你的电脑上构建：装 JDK 17、Maven、Node 18+，在仓库里运行同样的两步（`admin` 里 `npm install && npm run build`，`backend` 里 `mvn package`），把 `backend/target/kiya-admin.jar` 用 `rz` 传到服务器的 `/opt/kiya-admin/`。

## 5. 启动后台

```bash
cp /opt/kiya-clipper/deploy/admin/kiya-admin.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now kiya-admin
sleep 20 && systemctl status kiya-admin --no-pager | head -5
journalctl -u kiya-admin | grep 初始密码      # 第一次启动随机生成的管理员密码（用户名 admin）
```

登录后先在“配置”页改密码（至少 10 位）。

## 6. 对公网开放（nginx + HTTPS）

先用自签名证书（浏览器第一次打开会提示“不安全”，确认继续即可；连接仍是加密的）：

```bash
mkdir -p /etc/nginx/ssl
IP=$(curl -s ifconfig.me)       # 服务器公网 IP
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout /etc/nginx/ssl/kiya-admin.key -out /etc/nginx/ssl/kiya-admin.crt \
  -subj "/CN=kiya-admin" -addext "subjectAltName=IP:${IP}"
chmod 600 /etc/nginx/ssl/kiya-admin.key
cp /opt/kiya-clipper/deploy/admin/nginx-kiya-admin.conf /etc/nginx/conf.d/kiya-admin.conf
nginx -t && systemctl reload nginx
```

然后在阿里云控制台 → ECS → 安全组 → 入方向，放行 **TCP 8443**。条件允许时把“授权对象”限定成你常用的 IP，比 `0.0.0.0/0` 安全得多。

浏览器打开 `https://<服务器公网IP>:8443`。

说明：

- 用了 8443 端口和 IP 访问。以后想用域名 + 80/443 端口 + 免费正式证书（Let's Encrypt），在阿里云国内服务器上需要先做 ICP 备案。
- 安全措施：密码 BCrypt 存储；同一 IP 连续输错 5 次锁 15 分钟，同一用户名被试 20 次锁 15 分钟；会话 Cookie 只走 HTTPS、SameSite=Strict；所有写操作有 CSRF 校验；后台进程只监听本机，外部只能经过 nginx；能执行的 worker 命令是固定的几种，参数逐个校验，不经过 shell。
- 想临时不开公网：安全组关掉 8443，改用 SSH 隧道 `ssh -N -L 8080:127.0.0.1:8080 root@<服务器IP>`，并在 `/etc/kiya-admin.env` 加 `KIYA_COOKIE_SECURE=false` 后重启，浏览器开 `http://localhost:8080`。

## 6.5 成片渲染（可选）

后台的“成片编辑”需要渲染器（Remotion）：按 [renderer/README.md](../../renderer/README.md) 安装 Node 依赖和浏览器。不装也不影响其他功能，只是“渲染”任务会失败并提示安装。

如果之前已经配置过 nginx，把 `/etc/nginx/conf.d/kiya-admin.conf` 里的 `client_max_body_size` 改成 `8m`（上传贴图用），然后 `nginx -t && systemctl reload nginx`。

## 7. 更新

```bash
cd /opt/kiya-clipper && git pull && bash deploy/admin/build.sh && systemctl restart kiya-admin
```

## 排查

| 现象 | 处理 |
| --- | --- |
| 打不开页面 | `systemctl status kiya-admin nginx`；`curl -s http://127.0.0.1:8080/api/auth/me` 应返回 `{"authenticated":false}`；安全组是否放行 8443 |
| 登录后又跳回登录页 | 用 http 访问时 Cookie 带不上：用 https 访问，或按上面 SSH 隧道的做法设 `KIYA_COOKIE_SECURE=false` |
| 启动失败，日志里有 Access denied | `/etc/kiya-admin.env` 里的 `DB_PASSWORD` 和建账号时的不一致 |
| 任务失败 | “任务”页点日志，看 worker 的原始输出 |
| 内存不足、进程被杀 | `free -m` 看 swap 是否生效；`dmesg | grep -i kill` |
| 忘记密码 | `mysql kiya_admin -e "DELETE FROM sys_user"`，在 `/etc/kiya-admin.env` 里设 `ADMIN_PASSWORD=新密码`（10 位以上），重启后台，登录后把这行删掉 |

## 本地开发

```bash
# 后端：本机装 MySQL，或者用测试配置里的 H2
cd backend && mvn spring-boot:run -Dspring-boot.run.arguments="--kiya.repo-dir=.. --server.servlet.session.cookie.secure=false"
# 前端：热更新，接口转发到 8080
cd admin && npm install && npm run dev
```
