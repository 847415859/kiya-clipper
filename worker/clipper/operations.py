"""Daily operations planning with transactional slot and charge reservations."""
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import os
import sqlite3
from uuid import uuid4


BEIJING = timezone(timedelta(hours=8))
DAILY_PUBLISH_LIMIT = 3
DAILY_COST_FEN = 5000


def reserve_external_cost(kind):
    """Reserve the configured upper bound before any billable managed operation."""
    ledger_path = os.environ.get("KIYA_OPS_LEDGER")
    if not ledger_path:
        return
    key = f"KIYA_OPS_{kind}_MAX_FEN"
    try:
        maximum = int(os.environ.get(key, ""))
    except ValueError:
        maximum = 0
    if not 0 < maximum <= DAILY_COST_FEN:
        raise RuntimeError(f"缺少有效的付费上界 {key}")
    attempt = f"{kind.lower()}:{uuid4().hex}"
    ledger = DailyLedger(ledger_path)
    day = beijing_day(datetime.now(timezone.utc))
    if ledger.reserve_cost("kiya", day, attempt, maximum) != "reserved":
        marker = os.environ.get("KIYA_OPS_BUDGET_BLOCKED")
        if marker:
            Path(marker).touch()
        raise RuntimeError(f"北京时间 {day} 的付费额度不足，暂停 {kind}")


def beijing_day(at):
    if at.tzinfo is None:
        raise ValueError("时间必须带时区")
    return at.astimezone(BEIJING).date().isoformat()


def _day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("日期必须为 YYYY-MM-DD")
    return value


def _amount(value):
    if type(value) is not int or value < 0:
        raise ValueError("金额必须是非负整数分")
    return value


class DailyLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS publish_day (
                    account TEXT, day TEXT, reconciled INTEGER NOT NULL,
                    PRIMARY KEY (account, day)
                );
                CREATE TABLE IF NOT EXISTS upload (
                    account TEXT, id TEXT, day TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','unknown','confirmed','released')),
                    PRIMARY KEY (account, id)
                );
                CREATE TABLE IF NOT EXISTS cost (
                    scope TEXT, id TEXT, day TEXT NOT NULL, reserved INTEGER NOT NULL,
                    actual INTEGER, PRIMARY KEY (scope, id)
                );
            """)

    @contextmanager
    def _write(self):
        with closing(sqlite3.connect(self.path, timeout=15, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def reconcile(self, account, day, confirmed_ids, *, complete=False):
        """Record known platform uploads, including manual ones, by stable receipt ID."""
        day = _day(day)
        with self._write() as db:
            for receipt in confirmed_ids:
                if not receipt:
                    raise ValueError("投稿凭据标识不能为空")
                db.execute(
                    "INSERT INTO upload VALUES (?,?,?,'confirmed') "
                    "ON CONFLICT(account,id) DO UPDATE SET state='confirmed', day=excluded.day",
                    (account, receipt, day),
                )
            db.execute("INSERT OR REPLACE INTO publish_day VALUES (?,?,?)", (account, day, bool(complete)))

    @staticmethod
    def _publish_state(db, account, day):
        checked = db.execute("SELECT reconciled FROM publish_day WHERE account=? AND day=?",
                             (account, day)).fetchone()
        used = db.execute("SELECT count(*) FROM upload WHERE account=? AND day=? AND state<>'released'",
                          (account, day)).fetchone()[0]
        return bool(checked and checked[0]), used

    @staticmethod
    def _cost_state(db, scope, day):
        return db.execute("SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM cost WHERE scope=? AND day=?",
                          (scope, day)).fetchone()[0]

    def snapshot(self, account, day, *, cost_scope="kiya"):
        day = _day(day)
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.execute("BEGIN")
            checked, uploads = self._publish_state(db, account, day)
            booked = self._cost_state(db, cost_scope, day)
        return {"day": day, "history_reconciled": checked, "publication_slots_used": uploads,
                "publication_slots_available": max(0, DAILY_PUBLISH_LIMIT - uploads) if checked else 0,
                "booked_fen": booked, "available_fen": max(0, DAILY_COST_FEN - booked)}

    def reserve_upload(self, account, day, upload_id):
        day = _day(day)
        if not upload_id:
            raise ValueError("投稿标识不能为空")
        with self._write() as db:
            old = db.execute("SELECT day,state FROM upload WHERE account=? AND id=?",
                             (account, upload_id)).fetchone()
            if old:
                return "already_reserved" if old == (day, "reserved") else "existing_" + old[1]
            checked, used = self._publish_state(db, account, day)
            if not checked:
                return "history_unverified"
            if used >= DAILY_PUBLISH_LIMIT:
                return "daily_limit"
            db.execute("INSERT INTO upload VALUES (?,?,?,'reserved')", (account, upload_id, day))
            return "reserved"

    def record_upload(self, account, upload_id, state, *, receipt=None):
        """Keep uncertain uploads booked until a positive or negative reconciliation."""
        if state not in ("unknown", "confirmed", "released"):
            raise ValueError("未知投稿状态")
        with self._write() as db:
            row = db.execute("SELECT day,state FROM upload WHERE account=? AND id=?",
                             (account, upload_id)).fetchone()
            if not row or row[1] in ("confirmed", "released"):
                raise ValueError("该投稿没有可更新的预约")
            if state == "confirmed" and receipt and receipt != upload_id:
                remote = db.execute("SELECT day,state FROM upload WHERE account=? AND id=?",
                                    (account, receipt)).fetchone()
                if remote and remote != (row[0], "confirmed"):
                    raise ValueError("平台回执与预约日期或状态冲突")
                if not remote:
                    db.execute("INSERT INTO upload VALUES (?,?,?,'confirmed')", (account, receipt, row[0]))
                # Keep the local ID as a tombstone, and count only the platform receipt.
                state = "released"
            db.execute("UPDATE upload SET state=? WHERE account=? AND id=?", (state, account, upload_id))

    def reserve_cost(self, scope, day, attempt_id, maximum_fen):
        day, maximum_fen = _day(day), _amount(maximum_fen)
        if not attempt_id or maximum_fen == 0:
            raise ValueError("付费请求需要唯一尝试 ID 和正数预留")
        with self._write() as db:
            old = db.execute("SELECT day,reserved,actual FROM cost WHERE scope=? AND id=?",
                             (scope, attempt_id)).fetchone()
            if old:
                if old[:2] != (day, maximum_fen):
                    raise ValueError("同一请求标识的日期或金额发生变化")
                return "already_reserved" if old[2] is None else "already_settled"
            if self._cost_state(db, scope, day) + maximum_fen > DAILY_COST_FEN:
                return "daily_limit"
            db.execute("INSERT INTO cost VALUES (?,?,?,?,NULL)", (scope, attempt_id, day, maximum_fen))
            return "reserved"

    def settle_cost(self, scope, attempt_id, actual_fen):
        """Use a billable amount even on failures. Leave unresolved attempts reserved."""
        actual_fen = _amount(actual_fen)
        with self._write() as db:
            row = db.execute("SELECT actual FROM cost WHERE scope=? AND id=?", (scope, attempt_id)).fetchone()
            if row is None or row[0] not in (None, actual_fen):
                raise ValueError("缺少对应预留或实际费用不一致")
            db.execute("UPDATE cost SET actual=? WHERE scope=? AND id=?", (actual_fen, scope, attempt_id))


def plan_inventory(rows, snapshot, recent_events=(), recent_topics=()):
    """Plan from reviewed metadata. Planning itself neither spends nor reserves."""
    used_events, recent_topics = set(recent_events), set(recent_topics)
    used_topics = set()
    slots = snapshot["publication_slots_available"]
    result = {"would_publish": [], "keep_in_inventory": [], "needs_review": [], "skip": []}
    priority = {"反差", "出丑", "抽象", "灵机一动"}
    ranked = sorted(rows, key=lambda r: (-bool(r.get("time_sensitive")),
                    -bool(priority.intersection(r.get("traits", []))), -r.get("score", 0), r["id"]))
    for row in ranked:
        item = {"id": row["id"], "event": row.get("event"), "topic": row.get("topic")}
        if row.get("decision") == "reject":
            key, reason = "skip", "精排判为低价值或不推荐"
        elif row.get("decision") != "qualified" or not row.get("event") or not row.get("topic"):
            key, reason = "needs_review", "内容或证据尚未达标"
        elif row["event"] in used_events:
            key, reason = "skip", "同一事件已有投稿或已入选"
        elif not snapshot["history_reconciled"]:
            key, reason = "keep_in_inventory", "当天账号发布记录未核对"
        elif slots <= 0:
            key, reason = "keep_in_inventory", "当天发布名额不足"
        elif row["topic"] in used_topics or (row["topic"] in recent_topics and not row.get("time_sensitive")):
            key, reason = "keep_in_inventory", "近期或当天已有同类题材"
        else:
            key, reason = "would_publish", "素材达标且能补充当天的题材组合"
            slots -= 1
            used_events.add(row["event"])
            used_topics.add(row["topic"])
        result[key].append({**item, "reason": reason})
    return result


def schedule_inventory(rows, snapshot, now, recent_events=(), recent_topics=()):
    """Propose times on the current Beijing day. Past slots stay closed."""
    local = now.astimezone(BEIJING)
    if snapshot["day"] != beijing_day(now):
        raise ValueError("排期日期与账本不一致")
    # Keep uploads apart and let timely, higher-ranked candidates take the first available slot.
    slots = [datetime.combine(local.date(), time(h, m), BEIJING)
             for h, m in ((12, 15), (18, 30), (21, 0))]
    slots = [slot for slot in slots if slot > local]
    available = min(snapshot["publication_slots_available"], len(slots))
    plan = plan_inventory(rows, {**snapshot, "publication_slots_available": available},
                          recent_events, recent_topics)
    for row, slot in zip(plan["would_publish"], slots):
        row["planned_at"] = slot.isoformat()
    return plan
