"""跨场次全文索引（技术方案 阶段 4：跨场次呼应）。

把每场的整场转写按句存起来，之后“找这句话以前有没有说过类似的”。两种实现，接口一样：

  MySQLIndex   正式用：表 transcript_segment，MySQL 5.7 的 ngram 全文索引（中文可检索），用 pymysql 直连后台同一个库
               （环境变量 DB_HOST / DB_NAME / DB_USER / DB_PASSWORD，和后台一致，后台启动 worker 时会带过去）。
               表由 ensure_schema() 自己建（CREATE TABLE IF NOT EXISTS），不放进后台的 schema.sql——后台的单元测试用 H2，不认 ngram 语法。
  MemoryIndex  兜底和测试用：直接读 output/<主播>/<场次>/transcript.json，用 2 字组重叠打分。数据量小（几十场）时够用。

open_index() 先试 MySQL，连不上（没装 pymysql、没配密码、库不在）就退回 MemoryIndex 并说明原因，呼应功能不会因此失效。
时间都是“整场录像里的秒数”（和 transcript.json 里的 t0 / t1 一致）。场次名 YYYYMMDD-HHMM，按字符串比较就是时间顺序。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

NORM = re.compile(r"[^0-9a-z一-鿿]+")
MAX_TEXT = 500
MIN_CHARS = 2          # 去掉标点后少于 2 个字的句子不入库（“嗯”“啊”）

# 立 flag 的说法：入库时给句子打标记，检索“打脸”时优先看这些。宁可多标——后面还有大模型判断，标错只是多一个候选。
FLAG_RE = re.compile(
    r"再也不|再也没|再也别|绝对不|绝不|肯定不会|一定不会|不可能|我发誓|发誓|保证|我要是.{0,12}就|"
    r"(明天|下次|下回|以后|今晚|这周|下周)(一定|必须|就|再也|都不|肯定)|打死也|这辈子|"
    r"谁(再|要是).{0,8}谁|我(宣布|决定)|从今天起|从现在开始|最后一次")


def norm(text: str) -> str:
    return NORM.sub("", (text or "").lower())


def bigrams(text: str) -> set[str]:
    n = norm(text)
    return {n[i:i + 2] for i in range(len(n) - 1)}


def is_flag(text: str) -> bool:
    return len(norm(text)) >= 6 and bool(FLAG_RE.search(text or ""))


def date_label(session: str) -> str:
    """20261103-2014 → 11月3日（场次名不是这个格式就原样返回）。"""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", session or "")
    return f"{int(m.group(2))}月{int(m.group(3))}日" if m else (session or "")


@dataclass
class Hit:
    session: str
    idx: int
    t0: float
    t1: float
    text: str
    score: float = 0.0
    flag: bool = False
    bg: set = field(default_factory=set, repr=False, compare=False)
    shared: tuple = field(default=(), repr=False, compare=False)       # 和查询共有的内容二字组（检索重打分后填，调试用）


def rows_from_sentences(sentences: list[dict]) -> list[tuple[int, float, float, str, int]]:
    """转写里的句子 → 入库的行 (idx, t0, t1, text, flag)。idx 是句子在整场里的序号（保留空档，方便取前后文）。"""
    rows = []
    for i, s in enumerate(sentences):
        text = str(s.get("text") or "").strip()[:MAX_TEXT]
        if len(norm(text)) < MIN_CHARS:
            continue
        rows.append((i, float(s["t0"]), float(s["t1"]), text, 1 if is_flag(text) else 0))
    return rows


class IndexUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- 内存 / 文件实现
class MemoryIndex:
    kind = "files"

    def __init__(self):
        self.data: dict[tuple[str, str], list[Hit]] = {}

    @classmethod
    def from_output(cls, root: Path, streamer: str) -> "MemoryIndex":
        idx = cls()
        for d in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
            f = d / "transcript.json"
            if d.is_dir() and not d.name.startswith("_") and f.exists():
                try:
                    sents = json.loads(f.read_text(encoding="utf-8")).get("sentences") or []
                except (OSError, ValueError):
                    continue
                idx.replace_session(streamer, d.name, sents)
        return idx

    def replace_session(self, streamer: str, session: str, sentences: list[dict]) -> int:
        rows = rows_from_sentences(sentences)
        self.data[(streamer, session)] = [Hit(session, i, a, b, t, 0.0, bool(f), bigrams(t)) for i, a, b, t, f in rows]
        return len(rows)

    def sessions(self, streamer: str) -> list[str]:
        return sorted(s for (st, s) in self.data if st == streamer)

    def search(self, streamer: str, query: str, before: str | None = None, limit: int = 20, flag_only: bool = False) -> list[Hit]:
        q = bigrams(query)
        if not q:
            return []
        out = []
        for (st, sess), hits in self.data.items():
            if st != streamer or (before and sess >= before):
                continue
            for h in hits:
                if flag_only and not h.flag:
                    continue
                shared = len(q & h.bg)
                if shared:
                    out.append(Hit(h.session, h.idx, h.t0, h.t1, h.text, shared / (len(h.bg) ** 0.5 + 1.0), h.flag, h.bg))
        out.sort(key=lambda h: (-h.score, h.session, h.idx))
        return out[:limit]

    def neighbors(self, streamer: str, session: str, lo: int, hi: int) -> list[Hit]:
        return [h for h in self.data.get((streamer, session), []) if lo <= h.idx <= hi]


# ---------------------------------------------------------------- MySQL 实现
SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript_segment (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    streamer_id  VARCHAR(32)  NOT NULL DEFAULT 'kiya',
    session_name VARCHAR(64)  NOT NULL,
    idx          INT          NOT NULL,
    start_s      DOUBLE       NOT NULL,
    end_s        DOUBLE       NOT NULL,
    text         VARCHAR(500) NOT NULL,
    flag         TINYINT      NOT NULL DEFAULT 0,
    CONSTRAINT uk_segment UNIQUE (streamer_id, session_name, idx),
    KEY k_segment_flag (streamer_id, flag, session_name),
    FULLTEXT KEY ft_segment_text (text) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class MySQLIndex:
    kind = "mysql"

    def __init__(self, connect):
        """connect：无参函数，返回 DB-API 连接（pymysql.connect(...)）。测试里传假的。"""
        self.connect = connect
        self.conn = None

    @classmethod
    def from_env(cls) -> "MySQLIndex":
        try:
            import pymysql  # 惰性导入：没装也不影响其他功能
        except ImportError as e:
            raise IndexUnavailable("没装 pymysql（pip install PyMySQL）") from e
        pw = os.environ.get("DB_PASSWORD")
        if pw is None:
            raise IndexUnavailable("没有 DB_PASSWORD 环境变量（后台启动的任务会带上；手动运行请先 set -a; source /etc/kiya-admin.env; set +a）")

        def connect():
            return pymysql.connect(host=os.environ.get("DB_HOST", "127.0.0.1"), port=int(os.environ.get("DB_PORT", "3306")),
                                   user=os.environ.get("DB_USER", "kiya"), password=pw, database=os.environ.get("DB_NAME", "kiya_admin"),
                                   charset="utf8mb4", connect_timeout=5, autocommit=True)
        idx = cls(connect)
        try:
            idx.ensure_schema()
        except Exception as e:                       # 连不上 / 没权限 / 版本不支持 ngram
            raise IndexUnavailable(f"连不上 MySQL 或建表失败：{type(e).__name__}: {e}") from e
        return idx

    def _cur(self):
        if self.conn is None:
            self.conn = self.connect()
        return self.conn.cursor()

    def ensure_schema(self) -> None:
        cur = self._cur()
        cur.execute(SCHEMA)

    def replace_session(self, streamer: str, session: str, sentences: list[dict]) -> int:
        rows = rows_from_sentences(sentences)
        cur = self._cur()
        cur.execute("DELETE FROM transcript_segment WHERE streamer_id=%s AND session_name=%s", (streamer, session))
        if rows:
            cur.executemany(
                "INSERT INTO transcript_segment (streamer_id, session_name, idx, start_s, end_s, text, flag) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [(streamer, session, i, a, b, t, f) for i, a, b, t, f in rows])
        return len(rows)

    def sessions(self, streamer: str) -> list[str]:
        cur = self._cur()
        cur.execute("SELECT DISTINCT session_name FROM transcript_segment WHERE streamer_id=%s ORDER BY session_name", (streamer,))
        return [r[0] for r in cur.fetchall()]

    def search(self, streamer: str, query: str, before: str | None = None, limit: int = 20, flag_only: bool = False) -> list[Hit]:
        q = norm(query)
        if len(q) < 2:
            return []
        # ngram 解析器会把查询按 2 字切开，自然语言模式下按相关度排序；不要把标点、空格带进去
        sql = ("SELECT session_name, idx, start_s, end_s, text, flag, MATCH(text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS rel "
               "FROM transcript_segment WHERE streamer_id=%s AND MATCH(text) AGAINST (%s IN NATURAL LANGUAGE MODE)")
        args: list = [q, streamer, q]
        if before:
            sql += " AND session_name < %s"
            args.append(before)
        if flag_only:
            sql += " AND flag = 1"
        sql += " ORDER BY rel DESC LIMIT %s"
        args.append(int(limit))
        cur = self._cur()
        cur.execute(sql, args)
        return [Hit(r[0], int(r[1]), float(r[2]), float(r[3]), r[4], float(r[6]), bool(r[5]), bigrams(r[4])) for r in cur.fetchall()]

    def neighbors(self, streamer: str, session: str, lo: int, hi: int) -> list[Hit]:
        cur = self._cur()
        cur.execute("SELECT session_name, idx, start_s, end_s, text, flag FROM transcript_segment "
                    "WHERE streamer_id=%s AND session_name=%s AND idx BETWEEN %s AND %s ORDER BY idx", (streamer, session, int(lo), int(hi)))
        return [Hit(r[0], int(r[1]), float(r[2]), float(r[3]), r[4], 0.0, bool(r[5]), bigrams(r[4])) for r in cur.fetchall()]


def open_index(streamer: str, output_root: Path, prefer: str = "auto") -> tuple[object, str]:
    """返回 (索引, 说明)。prefer: auto 先 MySQL 后文件 / files 只用文件（不连库）。"""
    if prefer != "files":
        try:
            idx = MySQLIndex.from_env()
            return idx, "MySQL ngram 全文索引"
        except IndexUnavailable as e:
            why = str(e)
    else:
        why = "配置指定只用文件"
    return MemoryIndex.from_output(output_root, streamer), f"直接读 transcript.json（没用 MySQL：{why}）"
