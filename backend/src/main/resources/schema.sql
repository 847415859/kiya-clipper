-- 每次启动执行，必须可重复执行（只用 CREATE TABLE IF NOT EXISTS）
CREATE TABLE IF NOT EXISTS sys_user (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(64)  NOT NULL,
    password_hash VARCHAR(100) NOT NULL,
    created_at    DATETIME     NOT NULL,
    CONSTRAINT uk_user_name UNIQUE (username)
);

-- 场次：扫描录像目录得到（worker/scripts/sessions.py）
CREATE TABLE IF NOT EXISTS live_session (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    name             VARCHAR(64) NOT NULL,
    start_time       VARCHAR(40),
    segments         INT,
    duration_seconds DOUBLE,
    videos_present   INT,
    video_bytes      BIGINT,
    danmaku          INT,
    scanned_at       DATETIME,
    CONSTRAINT uk_session_name UNIQUE (name)
);

-- 候选片段：从 output/<场次>/candidates.json 同步；标注（mark / note）只存在这里，并导出到 eval/reviews/<场次>.csv
CREATE TABLE IF NOT EXISTS candidate (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_name    VARCHAR(64) NOT NULL,
    rnk             INT         NOT NULL,
    t               DOUBLE,
    score           DOUBLE,
    clip_start      DOUBLE,
    clip_end        DOUBLE,
    danmaku_per_min DOUBLE,
    emotion_hits    INT,
    gift_value      DOUBLE,
    top_danmaku     TEXT,
    speech          TEXT,
    contrib         TEXT,
    mark            VARCHAR(1),
    note            VARCHAR(500),
    clip_file       VARCHAR(255),
    CONSTRAINT uk_candidate UNIQUE (session_name, rnk)
);

-- 字幕样式：config_json 的结构由 config/schema/subtitle_style.schema.json 定义
CREATE TABLE IF NOT EXISTS subtitle_style (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    code        VARCHAR(32)  NOT NULL,
    name        VARCHAR(64)  NOT NULL,
    config_json TEXT         NOT NULL,
    is_default  TINYINT      NOT NULL DEFAULT 0,
    updated_at  DATETIME,
    CONSTRAINT uk_style_code UNIQUE (code)
);

-- 后台任务：调用 worker 脚本，同一时间只跑一个
CREATE TABLE IF NOT EXISTS job (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    type         VARCHAR(32)  NOT NULL,
    session_name VARCHAR(64),
    params       TEXT,
    status       VARCHAR(16)  NOT NULL,
    log_file     VARCHAR(255),
    exit_code    INT,
    summary      VARCHAR(500),
    created_by   VARCHAR(64),
    created_at   DATETIME     NOT NULL,
    started_at   DATETIME,
    finished_at  DATETIME
);

-- 键值配置：额外热词、纠错词等
CREATE TABLE IF NOT EXISTS app_config (
    k VARCHAR(64) PRIMARY KEY,
    v TEXT
);
