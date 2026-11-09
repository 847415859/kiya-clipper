# backend

后台管理的后端：Spring Boot 3.3 + MyBatis-Plus + MySQL 8（JDK 17）。前端在 `../admin`，构建时打包进本项目的 jar。部署见 [deploy/admin/README.md](../deploy/admin/README.md)。

```
src/main/java/com/kiya/admin/
├── config/    # 安全（登录、CSRF、防暴力破解）、kiya.* 配置项
├── entity/    # 表：sys_user、live_session、candidate、subtitle_style、job、app_config
├── mapper/    # MyBatis-Plus Mapper
├── service/
│   ├── SessionService    # 扫描场次、同步 candidates.json、标注（导出 eval/reviews/*.csv）
│   ├── JobService        # 任务队列：调用 worker 脚本，单线程，日志 output/_jobs/<id>.log
│   ├── StyleService      # 字幕样式：按 config/schema 校验、预览、默认样式
│   ├── OverridesService  # 热词 / 纠错词 / 默认样式 → config/admin_overrides.json
│   ├── ProcessRunner     # 调用 worker 的短命令（不经过 shell）
│   └── WorkerPaths       # 仓库目录与路径穿越校验
├── web/       # REST 接口 /api/**
└── common/    # 错误处理、CSV
src/main/resources/schema.sql   # 建表（启动时执行，可重复）
```

测试：`mvn test`（H2 内存库，验证启动、登录、CSRF、参数校验）。
