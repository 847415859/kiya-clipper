# 新场验收页面部署与验收

## 功能边界

入口：后台「场次」→「新场验收」，或直接访问 `#/sessions/acceptance`。默认读隔离验收队列；切换至生产队列后读取 `KIYA_OPS_DIR`。两个详情和媒体路由独立，浏览器页面不展示恢复、入队、续跑、发布动作。`monitor-state.json` 中无时区的 `last_check` 保留原始墙上时间并明确时区未记录，不伪造北京时间。费用与 HTTP 调用审计没有可核实字段时显示未知。

## 本地模拟验收

模拟验收只验证 UI 状态、GET-only 请求、刷新失败保留、详情布局以及浏览器对 FFmpeg fixture MP4 的播放和 seek。它不启动 Spring 后端，不触碰生产/隔离运营目录，不构成真实下播或 Worker 产出证据。

```powershell
cd admin
node --test tests/acceptance.test.mjs
npm exec --yes --package=playwright -- node tests/acceptance-browser.mjs
```

浏览器脚本使用 Vite 临时端口 `5198`、空环境目录和 mock API。脚本生成的报告与截图位于 `docs/acceptance-evidence/`，名称带 `mock`，不得当作线上验收证据。

## 后端定向测试与构建

Windows 上显式指定 Java 17；系统默认 Java 8 无法编译该 Spring Boot 工程。

```powershell
$env:JAVA_HOME='D:\Develop\Java\JDK\jdk-17.0.14+7'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
cd backend
mvn -q '-Dtest=OperationsQueueTests,AcceptanceOperationsTests' test
mvn -q package
cd ..\admin
npm run build
```

定向测试包含生产/隔离同名场次隔离、只读字段、未匹配任务、旧版本审片不冒充当前版本、新版媒体库存与哈希校验。额外 `AcceptanceMediaHttpTests` 通过 MockMvc 验证服务端 `Range: bytes=2-5` 返回 206、`Content-Range: bytes 2-5/8` 以及正确字节，且无恢复调用。

本次本地结果：Java 17 下 `mvn -q package` 成功，9 个测试类合计 56 项，失败/错误/跳过均为 0；前端格式化单测 4 项通过，Vite build 成功；Chrome mock 浏览器 16 项断言通过，23 次 API 请求均为 GET，6 秒 fixture 实际播放并 seek 到第 3 秒。截图已生成，当前模型不支持图像输入，尚未进行截图视觉审阅。真实服务器部署和下一场下播验收尚未执行。

## 部署

先核对备份和部署提交；不要改动 `.env` 密钥。新增验收目录应指向隔离观察任务实际使用的根目录，生产队列仍使用现有配置。`npm run build` 已输出到 `backend/src/main/resources/static`；该构建目录属于现有部署产物，部署归档前一并纳入发布包。

```bash
cd /opt/kiya-clipper
stamp=$(date +%Y%m%d-%H%M%S)
cp -a backend/target/kiya-admin.jar "/var/backups/kiya-admin-$stamp.jar"
cp -a backend/src/main/resources/static "/var/backups/kiya-static-$stamp"
```

部署前将实现构建为 jar，并同步静态产物。服务环境增加或确认 `KIYA_ACCEPTANCE_DIR=/var/lib/kiya-next-session-acceptance`；保持 `KIYA_OPS_DIR` 原值。随后重启现有 Spring Boot 服务，并以登录后的只读 GET 验证：

```bash
curl -fsS -b "$COOKIE_JAR" https://ADMIN_HOST/api/sessions/acceptance-operations
curl -fsS -b "$COOKIE_JAR" https://ADMIN_HOST/api/sessions/production-acceptance
curl -fsS -b "$COOKIE_JAR" https://ADMIN_HOST/api/sessions/SESSION/acceptance-operations
```

在验收目录存在合格候选时，从详情返回的媒体地址通过浏览器登录态播放；用开发者工具确认媒体请求为 Range/206。服务器现场完整 MP4 必须由实际入库报告决定，不可用本地 mock 截图替代。

## 回滚

若只需回滚静态页面，恢复部署前 `static` 备份；若同步回滚 API，实现应恢复与该静态包兼容的 jar。重启服务后验证登录、场次页及生产任务接口。不要删除隔离队列、账本、媒体或用户数据。

```bash
cp -a /var/backups/kiya-static-$stamp/. /opt/kiya-clipper/backend/src/main/resources/static/
cp -a /var/backups/kiya-admin-$stamp.jar /opt/kiya-clipper/backend/target/kiya-admin.jar
sudo systemctl restart kiya-admin
sudo systemctl --no-pager --full status kiya-admin
```

## 真实下播后验收记录

当前工作区没有证据表明新版本已部署，也没有接入服务器实际 `monitor-state.json`、队列、Worker 报告或真实 MP4。故本地模拟与服务器真实验收分开记录：

| 项目 | 当前结论 |
|---|---|
| UI 模拟（监控未知/直播/过期、排队、预算、失败、成片） | 由 `acceptance-browser.mjs` mock 场景验证；运行后以 JSON 报告和截图为准 |
| 页面只读请求 | mock 浏览器脚本断言所有 `/api/` 请求为 GET；后端定向测试断言只读 DTO 不包含恢复 action |
| 生产与隔离同名场次 | 后端临时目录测试验证；仍需部署后对实际两根目录核对 |
| 真实服务器观察到下播 | 未验证，待部署并等待下一场实际下播 |
| 真实 Worker 生产合格媒体 | 未验证，待实际场次结束、Worker 入队/处理及媒体 Range/seek 检查 |

真实验收时记录 job ID、观察下播时间、匹配录播段数、Worker 状态、候选版本、当前版本审片/质检绑定、媒体时长和 Range seek 结果。不得把截图或本地临时视频标成真实服务器产物。
