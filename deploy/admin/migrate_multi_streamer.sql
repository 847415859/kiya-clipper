-- 多主播升级：给已有部署的 live_session / candidate 加 streamer_id，并把唯一约束改成带主播的组合键。
-- 可重复执行（已经升级过的表会跳过）；已有数据全部归到默认主播 kiya。
-- 用法见 deploy/admin/README.md「多主播升级」。仅适用于 MySQL 8（本地开发用 H2 时，schema.sql 会直接建新表，不需要）。
SET @db = DATABASE();

-- 1) live_session.streamer_id
SET @s = (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE live_session ADD COLUMN streamer_id VARCHAR(32) NOT NULL DEFAULT ''kiya'' AFTER id',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_session' AND COLUMN_NAME = 'streamer_id');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- 2) live_session 唯一键：name → (streamer_id, name)
SET @s = (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE live_session DROP INDEX uk_session_name, ADD UNIQUE KEY uk_session_name (streamer_id, name)',
    'DO 0')
  FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_session' AND INDEX_NAME = 'uk_session_name' AND COLUMN_NAME = 'streamer_id');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- 3) candidate.streamer_id
SET @s = (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE candidate ADD COLUMN streamer_id VARCHAR(32) NOT NULL DEFAULT ''kiya'' AFTER id',
    'DO 0')
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'candidate' AND COLUMN_NAME = 'streamer_id');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- 4) candidate 唯一键：(session_name, rnk) → (streamer_id, session_name, rnk)
SET @s = (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE candidate DROP INDEX uk_candidate, ADD UNIQUE KEY uk_candidate (streamer_id, session_name, rnk)',
    'DO 0')
  FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'candidate' AND INDEX_NAME = 'uk_candidate' AND COLUMN_NAME = 'streamer_id');
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

SELECT 'live_session' AS tbl, COUNT(*) AS rows_, SUM(streamer_id = 'kiya') AS kiya FROM live_session
UNION ALL SELECT 'candidate', COUNT(*), SUM(streamer_id = 'kiya') FROM candidate;
