# 阶段 0：在阿里云服务器上部署录播姬

目标：娜由纪雅kiya（直播间 **1724773744**）一开播就自动录制视频和弹幕，录像不把硬盘写满，管理界面不暴露到公网。

适用服务器：阿里云 ECS 经济型 e，2 核 2G，40G ESSD 系统盘，3M 固定带宽，华北 2（北京）。

> 这台机器在阶段 0–2 只做录制和弹幕分析；阶段 3 之后跑完整系统需要 4 核 8G。它可以一直留作专职录制机。

> **实际部署记录（2026-11-09）**：服务器已换成 Ubuntu 24.04；国内服务器连不上 Docker Hub（拉镜像超时），
> 改用录播姬命令行版 v2.20.0，装在 `/opt/brec`，用 systemd 服务 `brec` 运行（配置见 `recorder/brec.service`）。
> 第 3、4 步的 Docker 方式保留作参考，实际按“附录：不用 Docker，直接运行录播姬”执行。
> 服务器直连 GitHub 下载很慢，大文件在本机下载后用 `rz` 或 Xftp 上传。

---

## 第 1 步：换系统为 Ubuntu 24.04（推荐，5 分钟）

当前镜像是 CentOS Stream 10，Docker 对它的支持不确定。趁服务器还是空的，换成 Ubuntu 最省事：

1. ECS 控制台 → 实例 → 更多 → 云盘和镜像 → **更换操作系统**。
2. 镜像选 **Ubuntu 24.04 64 位**，设置登录密码或密钥。
3. 等实例重启完成。

> 不想换系统：跳到文末“附录：留在 CentOS Stream 10”。

## 第 2 步：加一块数据盘（强烈建议）

40G 系统盘装完系统大约剩 30G，一场 4 小时原画约 10GB，只够存 2–3 场。

1. ECS 控制台 → 云盘 → 创建云盘：**同可用区**、ESSD Entry、**100GB**，挂载到这台实例。
2. SSH 登录服务器，格式化并挂载到 `/data`：

```bash
lsblk                                   # 找到新盘，一般是 /dev/vdb，确认它没有分区
sudo mkfs.ext4 /dev/vdb                 # 只对新盘执行！
sudo mkdir -p /data
echo "UUID=$(sudo blkid -s UUID -o value /dev/vdb) /data ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount -a && df -h /data            # 看到 /data 约 98G 即成功
sudo mkdir -p /data/rec && sudo chown 1000:1000 /data/rec
```

> 不加数据盘也能先录，但务必配好第 6 步的 OSS 同步，否则两三场后硬盘写满就会漏录。

## 第 3 步：安装 Docker

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git python3-venv ffmpeg
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

**如果拉取镜像超时**（国内访问 Docker Hub 不稳定）：

- 在阿里云控制台“容器镜像服务 ACR → 镜像工具 → 镜像加速器”查看是否有可用的加速地址，写入 `/etc/docker/daemon.json` 的 `registry-mirrors` 后 `sudo systemctl restart docker`；
- 或者改用文末“附录：不用 Docker，直接运行录播姬”。

## 第 4 步：启动录播姬

```bash
sudo git clone <你的仓库地址> /opt/kiya-clipper     # 或 scp 上传本仓库
cd /opt/kiya-clipper/deploy/recorder
cp ../../.env.example .env
nano .env        # 改 BREC_HTTP_BASIC_PASS 为长随机密码；REC_DIR=/data/rec
sudo docker compose up -d
sudo docker compose logs -f            # 看到监听 2356 端口即启动成功，Ctrl+C 退出日志
```

管理界面只绑定在服务器本机的 `127.0.0.1:2356`，**不要在安全组里放行 2356 端口**。

## 第 5 步：通过 SSH 隧道打开管理界面，添加直播间

在你自己的 Linux 电脑上执行：

```bash
ssh -N -L 2356:127.0.0.1:2356 root@<服务器公网IP>
```

保持这个窗口开着，浏览器打开 `http://localhost:2356`，用 `.env` 里的账号密码登录，然后：

1. **添加房间**：输入 `1724773744`，打开“自动录制”。
2. **设置 → 弹幕**：开启录制弹幕，同时勾选录制礼物、SC（醒目留言）、上舰。
3. **设置 → 文件分段**：按时间分段，每 30 分钟一段（断流时损失更小，也方便同步）。
4. **（建议）设置 → Cookie**：填一个 B站小号的 Cookie。未登录时可能拿不到原画，弹幕里的用户名也会被打码。Cookie 等同于登录凭证，只用小号，不要用主号。

## 第 6 步：录像同步到 OSS，防止硬盘写满（建议）

服务器在北京，OSS 也建在**华北 2（北京）**，走内网上传，不占那 3M 公网带宽，内网流量免费。

1. OSS 控制台创建 Bucket（北京、标准存储、私有），设置生命周期规则：`rec/` 前缀下文件 **14 天后删除**。
2. 创建一个只拥有该 Bucket 读写权限的 RAM 用户，拿到 AccessKey。
3. 服务器上安装并配置 rclone：

```bash
sudo apt install -y rclone
rclone config
#   n) New remote → name: oss → Storage: s3 → provider: Alibaba
#   填 AccessKey ID / Secret → endpoint: oss-cn-beijing-internal.aliyuncs.com
rclone lsd oss:                        # 能列出 bucket 即成功
```

4. 修改 `sync_to_oss.sh` 里的 `OSS_REMOTE`（或用环境变量），加入定时任务：

```bash
chmod +x /opt/kiya-clipper/deploy/recorder/sync_to_oss.sh
sudo crontab -e
# 加一行：每小时第 7 分钟同步一次
7 * * * * REC_DIR=/data/rec OSS_REMOTE=oss:<bucket>/rec /opt/kiya-clipper/deploy/recorder/sync_to_oss.sh >> /var/log/kiya-sync.log 2>&1
```

脚本会把写完的文件复制到 OSS，并删掉本地超过 3 天的视频；弹幕 XML 很小，一直留在本地，方便直接分析。

## 第 7 步：第一场录完后检查

```bash
ls -lh /data/rec/*/                     # 每 30 分钟一个 .flv 和同名 .xml
ffprobe -v error -show_entries format=duration,bit_rate -of default=nw=1 <某个.flv>
```

检查三件事：

- [ ] **视频能播放**：随便下载一小段（见下）在本地播放器打开。
- [ ] **实际码率**：`bit_rate` 除以 1000000 就是 Mbps。原画一般在几 Mbps；如果接近 10Mbps，说明入带宽可能不够，要考虑换按流量计费的带宽。
- [ ] **弹幕时间对得上**：用 `worker/` 的分析脚本跑一遍，挑一个高峰对照画面。

只下载一小段（3M 带宽下整场要好几个小时，不要下载整场）：

```bash
# 服务器上：从第 600 秒开始截 60 秒，不重新编码，几秒完成
ffmpeg -ss 600 -i <某个.flv> -t 60 -c copy /tmp/sample.flv
# 你的电脑上：
scp root@<服务器IP>:/tmp/sample.flv .
```

---

## 一键发布到 B站（可选）

后台“发布”卡片的“一键发布到 B站”靠 [biliup](https://github.com/biliup/biliup-rs) 命令行上传。这是非官方接口，有被 B站风控 / 限制账号的风险，建议用切片号，并先用“先放着”试。

```bash
# 1. 到 biliup-rs 的 GitHub Releases 下载 Linux x64 版，解压到 /opt/biliup（GitHub 慢就在自己电脑下载再 scp）
chmod +x /opt/biliup/biliup && ln -sf /opt/biliup/biliup /usr/local/bin/biliup
biliup upload --help                    # 能看到 --copyright --source --tid --title --desc --tag --dtime --cover 就对了

# 2. 在服务器上自己登录一次（扫码最省事），登录文件放在 kiya 用户能读到的地方
mkdir -p ~/.config/biliup && cd ~/.config/biliup && biliup login      # 按提示选扫码
chmod 600 ~/.config/biliup/cookies.json
```

- 也可在网站 **设置 → B站发布账号** 中扫码、核对 UID 后确认使用，无需在服务器交互登录；需先更新前后端及 worker，并安装更新后的 `worker/requirements.txt`。网站账号按主播隔离保存，优先于旧发布配置；详见 [发布账号网页登录](../docs/发布账号网页登录.md)。
- 登录文件就是你的账号凭证，不要提交到 git、不要发给别人；后台和脚本都不会接触你的密码。未使用网站托管时，`config/kiya.yaml` 的 `publish.bili.cookie` 指向它（后端以哪个用户运行，就要用那个用户登录，或者改成绝对路径）。
- 验证：后台发布弹窗顶部没有红 / 黄提示即可；命令行 `python scripts/publish_bili.py --status` 能看到 `biliup` 和 `logged_in` 都是 true。
- 第一次：先点“先预演”看命令，再用“先放着”真投一条，到 B站创作中心确认标题 / 分区 / 来源 / 定时时间，没问题再用别的方式。
- 分区 id 在 `publish.bili.tid`，默认 21（生活·日常），按你的习惯改。

## 跨场次呼应的全文索引（可选）

“跨场次呼应”要在历史场次的转写里找原话。有 MySQL 时用 ngram 全文索引（表 `transcript_segment` 由 worker 第一次入库时自己建，需要 MySQL 5.7.6+，库账号要有建表权限，后台那个账号就行）；没有也能用，会退回直接读各场的 `transcript.json`。

```bash
cd /opt/kiya-clipper/worker && source .venv/bin/activate
pip install PyMySQL                                      # 国内装不动就加 -i https://mirrors.aliyun.com/pypi/simple/
set -a; source /etc/kiya-admin.env; set +a               # 带上 DB_PASSWORD（后台启动的任务会自动带）
python scripts/transcript_index.py status                # 能看到“MySQL 索引里有 N 场”就连上了
python scripts/transcript_index.py index --all           # 历史场次一次性补入库；之后每次转写完会自动入库
python scripts/transcript_index.py search "再也不吃辣了" --flag
```

连不上库时转写任务不会失败，日志里会写“没有写入全文索引”。

## 附录：不用 Docker，直接运行录播姬

到录播姬的 GitHub Releases 下载 Linux x64 的命令行版（CLI），解压到 `/opt/brec`，然后：

```bash
/opt/brec/BililiveRecorder.Cli run --bind "http://127.0.0.1:2356" \
  --http-basic-user admin --http-basic-pass '<长随机密码>' /data/rec
```

看到 `Web host started.` 即成功（已在 v2.20.0 验证）。然后 `Ctrl+C`，注册为 systemd 服务：

```bash
cp /opt/kiya-clipper/deploy/recorder/brec.service /etc/systemd/system/
nano /etc/systemd/system/brec.service      # 把 <你的密码> 换成真实密码（只改服务器上这份，仓库里保留占位符）
systemctl daemon-reload && systemctl enable --now brec
systemctl status brec --no-pager
```
GitHub 下载慢时，可以先在你自己的电脑下载，再 `scp` 上传到服务器。

## 附录：留在 CentOS Stream 10

CentOS Stream 10 自带 Podman，可以直接运行同一个镜像：

```bash
sudo dnf install -y podman git python3 ffmpeg-free rclone
sudo podman run -d --name brec --restart=always -p 127.0.0.1:2356:2356 \
  -v /data/rec:/rec:Z \
  -e BREC_HTTP_BASIC_USER=admin -e BREC_HTTP_BASIC_PASS='<长随机密码>' \
  docker.io/bililive/recorder:latest
```

Podman 的 `--restart=always` 在服务器重启后不一定生效，需要用 `podman generate systemd` 生成 systemd 服务。另外 `ffmpeg-free` 和 `rclone` 是否在默认软件源里，以 `dnf` 实际结果为准。这些都比 Ubuntu 麻烦，所以更推荐第 1 步换系统。


## 投稿成功后本地 BV 绑定失败的恢复

若任务日志在 `bind_bvid` 处出现 `decisions_path() takes 0 positional arguments but 1 was given`，这是发布脚本导入了错误的同名路径函数。出现该调用说明 biliup 已返回成功且识别到 BV 号；先在创作中心核对稿件，不要点“重新投稿”。修复后的发布脚本直接使用按主播取路径的 `clipper.streamer.decisions_path`，并在本地绑定前原子保存投稿结果；绑定失败会提示手动补记，仍保留成功记录防止重复上传。

同步修复后的 `worker/scripts/publish_bili.py` 后，从创作中心复制这条稿件的真实 BV 号，仅补记本地结果和精排记忆：

```bash
cd /opt/kiya-clipper/worker
.venv/bin/python scripts/publish_bili.py output/20261111-2126/candidates.json --rank 6 --bind-bvid BVxxxxxxxxxx
```

`BVxxxxxxxxxx` 必须替换为已核对的真实 BV 号；若采用新目录结构，候选路径改为 `output/<主播>/20261111-2126/candidates.json`。此命令不运行 biliup，不重新上传；若已有另一条成功投稿记录则拒绝覆盖。脚本每次任务重新加载，更新该 Python 文件不需要重启录播姬。


### 重投后仍保存旧回执

旧版本可能在重投成功后、保存新回执前因本地绑定错误退出，导致 `<序号>.bili.json` 仍记录旧 BV。默认补记命令会拒绝覆盖另一条成功记录；确认创作中心的新旧稿件后，用 `--replace-bvid` 指定当前旧记录：

```bash
cd /opt/kiya-clipper/worker
.venv/bin/python scripts/publish_bili.py output/20261111-2126/candidates.json --rank 6 --bind-bvid BV1UGsg63E8Y --replace-bvid BV1k9WX6nEyq
```

以上 BV 仅对应本次故障示例，使用前需核对实际稿件。旧号须与当前回执一致；已经成功替换为目标 BV 时允许重复补记。替换前把旧回执原始内容备份到该场次 `publish/history/`，新回执包含 `previous` 和 `previous_receipt`。补记时间标记为 `time_source: repair`，不沿用旧稿件的投稿时间或上传日志。此操作仅更新本地记录与精排记忆，不调用 biliup；`--replace-bvid` 不能单独使用，`--bind-bvid` 不能与 `--again`、`--dry-run`、`--status` 混用。


## 精排记忆更新成功但投稿列表返回 -352

此时本地转写、标注、剪辑口味仍会更新，两个账号的投稿列表失败后保留旧缓存。`-352` 的响应本身不能证明登录过期，需单独验证登录状态。上传登录文件与查询文件过去互相独立，更新后的查询可复用 biliup JSON 登录文件；还修复了 Netscape `#HttpOnly_` 行被当作注释而漏读的问题（格式依据：[curl Cookie 文档](https://curl.se/docs/http-cookies.html)）。

查询 Cookie 来源优先级：`KIYA_COOKIE_FILE` → 主播配置 `memory.cookie_file` → `KIYA_BILIUP_COOKIE` → 主播配置 `publish.bili.cookie` → 旧默认 `/root/bili_cookies.txt`。显式文件缺失或损坏时不会悄悄换另一个账号。支持 `~` 展开，长期服务建议使用绝对路径。录播姬网页的登录配置与这些查询文件不是同一设置。

同步本次代码后，在服务器执行只读诊断；请使用与后台服务相同的用户，BV 替换为要核对的已投稿视频：

```bash
cd /opt/kiya-clipper/worker
.venv/bin/python scripts/bili_check.py --bvid BV1UGsg63E8Y
# 需要测试特定账号的列表时，加 --uid <切片号UID>
# 显式测试某份登录文件时，加 --cookie-file /绝对路径/cookies.json
```

诊断只显示文件路径、文件是否存在、关键 Cookie 名是否已读取、登录状态和指定 BV 的公开统计，不输出 Cookie 值。登录有效但列表仍被限制时，代码不做挑战绕过、代理切换或连续重试；对已经手动绑定的 BV 单独查询，最多 30 条，校验投稿 UID。若单条视频也不可读，则保留缓存，不伪造零播放或零点赞。网络状态恢复后再次更新即可；更新脚本不保证服务器端限制解除。

页面分别显示本地绑定数和已获取数据数，并显示数据来源。类型汇总要求足够多的绑定投稿具有类型及播放数据，只有绑定数量达标但统计未获取时不会启用。`learn.py --no-fetch` 完全不联网，也不会验证 Cookie。
