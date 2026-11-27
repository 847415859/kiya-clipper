# 历史隔离试跑 Linux 双用户权限

## 问题与修复范围

后台服务实际身份为 `root`，历史 Worker 为 `kiya:kiya`。原先后台原子落盘使用 Java `createTempFile`，POSIX 默认0600；rename保留该模式，即使最终目录是组可写，Worker仍读不到root创建的队列文件。后台用默认umask创建的目录也可能阻断Worker写状态/成片。

本补丁只设置 `${KIYA_ACCEPTANCE_DIR}/history` 内权限。没有修改生产队列、生产账本、原始录像、候选文件权限，也没有全局修改后台umask或Worker通用存储函数。

- 配置 `kiya.historical-trial-group`，环境变量 `KIYA_HISTORICAL_TRIAL_GROUP`，默认 `kiya`。
- 后台对 history及queue/status/production/locks/ledger 设置共享组与 **2770**（setgid，组可遍历和写入，其他用户无权限）。既有属主保留；部署初始化统一kiya:kiya。
- 后台创建的任务、初始状态、提交锁为 **root:kiya 0640**。原子JSON写完临时文件后设置共享组与0640，再执行force与atomic move；不是rename后才修权限。
- Worker在共享目录读取任务，并通过目录写权限原子替换root创建的状态。Worker仍保持 `UMask=0077`，生成的状态、内部检查点和媒体通常为kiya:kiya 0600/0700；后台root可回读，毋须放宽成片权限。
- Windows等非POSIX文件系统不应用Linux chmod/group。Linux缺少配置组时停止提交，不创建其他用户可读的替代文件。

## 部署前检查

服务用户/组必须已存在。所有父路径需允许kiya遍历，素材路径需按现有只读约定允许kiya读取。本补丁不改这些历史根之外的路径；若父路径是root:root 0700，先由部署人员核对现有父目录访问约定，不能靠仅修history解决。

```bash
getent passwd kiya
getent group kiya
systemctl show kiya-admin -p User -p Group
systemctl show kiya-historical-trial-worker -p User -p Group -p UMask
namei -l /var/lib/kiya-next-session-acceptance/history
```

确认后台root（systemd User空也代表root）、Worker kiya:kiya。自定义共享组时，后台配置、初始化脚本的group、Worker的Group及实际组成员必须一致。

## 首次部署与旧目录迁移

先停止历史Worker，并暂停网页历史提交（关闭fixture开关或暂停后台）。迁移需要无历史写入者，避免遍历与原子替换竞争。只备份、修复history树，不使用对acceptance、录像或仓库的递归chown。

```bash
HISTORY=/var/lib/kiya-next-session-acceptance/history
sudo systemctl stop kiya-historical-trial-worker
sudo tar --numeric-owner --acls --xattrs -cpf /var/tmp/kiya-history-before-permissions.tar -C "$(dirname "$HISTORY")" history
sudo python3 deploy/admin/prepare-historical-trial.py --root "$HISTORY" --user kiya --group kiya
sudo stat -c '%U:%G %a %n' "$HISTORY" "$HISTORY/queue" "$HISTORY/status" "$HISTORY/production" "$HISTORY/locks"
sudo -u kiya test -r "$HISTORY/queue"
sudo -u kiya test -w "$HISTORY/status"
sudo -u kiya test -w "$HISTORY/production"
```

首次部署history尚不存在时跳过备份命令；acceptance父目录需已存在。初始化脚本只在history内递归处理：目录kiya:kiya 2770、既有普通文件kiya:kiya 0640；拒绝符号链接、特殊文件、嵌套挂载与多硬链接文件，防止权限变更逃出历史树。后台新创建的任务仍是root:kiya0640。

安装修复后的后台程序，保持 `KIYA_HISTORICAL_TRIAL_GROUP=kiya`；确认Worker单元仍为 `User=kiya / Group=kiya / UMask=0077`，再恢复后台提交与Worker。渲染bundle、浏览器、字体和输入素材读取约定沿用原部署说明。

## 双用户验收

自动测试入口 `admin/tests/historical-trial-linux-users.py` 必须在真实Linux文件系统（本轮WSL Debian ext4）以root运行；不以Windows挂载盘的模拟POSIX权限代替。

`--runtime` 指向独立测试程序树，其中包含backend/classes、backend/test-classes、backend/lib/*.jar、worker源码、config、renderer、venv。Python venv安装Worker依赖与playwright，系统安装Java17+、Node、FFmpeg、Chromium；准备kiya用户。测试程序树是可读执行的临时副本，禁止指向线上运行目录。

```bash
sudo /var/tmp/kiya-history-dual-user-test/runtime/venv/bin/python \
  admin/tests/historical-trial-linux-users.py \
  --runtime /var/tmp/kiya-history-dual-user-test/runtime \
  --out /var/tmp/kiya-history-dual-user-test/run-unique \
  --prepare-script deploy/admin/prepare-historical-trial.py
```

测试自动执行：
1. 构造旧root0700/0600历史目录，迁移并验证属主模式；保存history之外源文件、生产sentinel及父目录属主/权限快照。
2. 真正以root、umask0077启动Spring，通过登录/CSRF HTTP提交；断言queue/status为root:kiya0640，运行目录2770。
3. `runuser -u kiya`读取任务，以umask0077执行真实Worker/Production/Remotion/FFmpeg；断言状态与媒体属kiya。
4. 后台root回读详情、Range206媒体，浏览器播放和拖动；模拟预览不变成正式合格库存。
5. 比对history之外内容、属主、模式完全一致，原子临时文件无残留，临时Spring进程finally结束。

### 本轮实际结果：通过

环境为本机 WSL Debian（原生Linux文件系统，非/mnt盘权限模拟），Java21运行Java17编译产物，Python3.13、Node20、Chromium。真实进程身份为后台uid0(root)、Worker uid999(kiya)、gid989(kiya)。两者均使用umask0077。

| 对象/行为 | 实际结果 |
|---|---|
| 旧history root0700与旧JSON0600迁移 | kiya:kiya 2770 / 0640 |
| root HTTP提交后的queue与初始status | root:kiya 0640 |
| queue/status/production/locks/ledger目录 | kiya:kiya 2770 |
| kiya领取并真实制作 | has_clips，真实720p含音频MP4 |
| Worker替换后的状态与成片 | kiya:kiya 0600，后台root成功回读 |
| 后台媒体服务与浏览器 | Range206读取1024字节；播放及拖动超过1.05秒 |
| history之外 | 原始素材、生产sentinel内容/属主/模式及父目录属主/模式均未变 |
| 模型与发布 | paidRequests=0，publish=false |

任务ID：`history-9233dc24-1000-4be2-9963-9699d71a4f6c`。

证据：[双用户结果](<../worker/output/_historical-trial-acceptance/linux-two-users-20261127-1209/result.json>)、[播放截图](<../worker/output/_historical-trial-acceptance/linux-two-users-20261127-1209/playback.png>)。JSON中的权限在Linux原位置采集；复制回Windows后的文件系统模式不作为权限证据。

回归结果：
- Windows Java完整回归：87项，**85通过、2项POSIX条件跳过**，0失败/错误。
- Linux独立JUnit运行HistoricalTrialTests：15项，**14通过、1项非POSIX兼容测试按条件中止**，0失败；Linux精确2770/0640、缺组停止与外部权限不变检查均实际执行。
- Linux迁移范围回归：**3/3通过**，包括外部文件和父目录保持、符号链接、多硬链接拒绝。

首次双用户测试已完成真实制作和Range，但浏览器测试误用了页面路由与候选label，随后只修验收脚本选择器，使用全新目录完整重跑通过。没有为通过验收放宽umask或生产权限。systemd单元未在线启动；该测试复现其User/Group/UMask身份，ProtectSystem等挂载限制仍沿用原部署前检查。

## 回滚与失败排查

- 组不存在：创建/选择与Worker一致的现有组，再恢复提交；不要将文件改成0666或目录0777。
- Worker读不到queue：`namei -l`核对父路径遍历，`stat`核对最终JSON是root:kiya0640，而非0600；确认运行的是修复后后台。
- Worker写不了status：核对status目录2770、组匹配；文件本身0640无需对root状态原地写入，Worker通过同目录临时文件替换。
- 后台root可读但浏览器播不了：核对媒体及绑定报告，不把模型模拟审片标为正式库存。
- 回滚程序前先停止历史写入并保留新增任务证据；如确需还原旧权限，使用部署前tar备份，在只读窗口恢复history树。不要对history之外执行递归权限恢复。恢复旧0600权限也会恢复旧故障，通常只回滚程序而保留可用共享组约定。

本轮不对线上服务器执行部署，不执行Git暂存/提交/回滚。
