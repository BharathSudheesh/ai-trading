"""SQLite persistence for profiles, paper-trading accounts, recommendations and alerts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .schemas import (
    Notification,
    Order,
    Position,
    Recommendation,
    RecStatus,
    UserProfile,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    profile_id INTEGER PRIMARY KEY,
    starting_cash REAL NOT NULL,
    cash REAL NOT NULL,
    peak_equity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    profile_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    PRIMARY KEY (profile_id, symbol)
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    comment TEXT,
    outcome_return REAL,
    outcome_correct INTEGER,
    outcome_date TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rec_profile ON recommendations(profile_id, created_at);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    rec_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fees REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    rec_id INTEGER,
    symbol TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    """Thin repository layer. A short-lived connection is opened per operation."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ profiles
    def create_profile(self, profile: UserProfile, starting_cash: float) -> UserProfile:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO profiles (payload, created_at) VALUES (?, ?)",
                (profile.model_dump_json(exclude={"id"}), utcnow().isoformat()),
            )
            pid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO accounts (profile_id, starting_cash, cash, peak_equity) "
                "VALUES (?, ?, ?, ?)",
                (pid, starting_cash, starting_cash, starting_cash),
            )
        return profile.model_copy(update={"id": pid})

    def get_profile(self, profile_id: int) -> UserProfile | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        return self._profile(row) if row else None

    def list_profiles(self) -> list[UserProfile]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        return [self._profile(r) for r in rows]

    def update_profile(self, profile_id: int, profile: UserProfile) -> UserProfile:
        with self._conn() as conn:
            conn.execute(
                "UPDATE profiles SET payload=? WHERE id=?",
                (profile.model_dump_json(exclude={"id"}), profile_id),
            )
        return profile.model_copy(update={"id": profile_id})

    @staticmethod
    def _profile(row: sqlite3.Row) -> UserProfile:
        return UserProfile.model_validate_json(row["payload"]).model_copy(update={"id": row["id"]})

    # ------------------------------------------------------------------ account
    def get_account(self, profile_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return dict(row)

    def set_cash(self, profile_id: int, cash: float) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE accounts SET cash=? WHERE profile_id=?", (cash, profile_id))

    def update_peak(self, profile_id: int, equity: float) -> float:
        with self._conn() as conn:
            conn.execute(
                "UPDATE accounts SET peak_equity=MAX(peak_equity, ?) WHERE profile_id=?",
                (equity, profile_id),
            )
            row = conn.execute(
                "SELECT peak_equity FROM accounts WHERE profile_id=?", (profile_id,)
            ).fetchone()
        return float(row["peak_equity"])

    # ---------------------------------------------------------------- positions
    def get_positions(self, profile_id: int) -> dict[str, Position]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT symbol, qty, avg_price FROM positions WHERE profile_id=? AND qty>0",
                (profile_id,),
            ).fetchall()
        return {r["symbol"]: Position(**dict(r)) for r in rows}

    def set_position(self, profile_id: int, symbol: str, qty: float, avg_price: float) -> None:
        with self._conn() as conn:
            if qty <= 0:
                conn.execute(
                    "DELETE FROM positions WHERE profile_id=? AND symbol=?", (profile_id, symbol)
                )
            else:
                conn.execute(
                    "INSERT INTO positions (profile_id, symbol, qty, avg_price) VALUES (?,?,?,?) "
                    "ON CONFLICT(profile_id, symbol) DO UPDATE SET qty=excluded.qty, "
                    "avg_price=excluded.avg_price",
                    (profile_id, symbol, qty, avg_price),
                )

    # ------------------------------------------------------------------- orders
    def add_order(self, order: Order) -> Order:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO orders (profile_id, rec_id, symbol, side, qty, price, fees, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (order.profile_id, order.rec_id, order.symbol, order.side, order.qty,
                 order.price, order.fees, order.created_at.isoformat()),
            )
        return order.model_copy(update={"id": int(cur.lastrowid)})

    def list_orders(self, profile_id: int, limit: int = 100) -> list[Order]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE profile_id=? ORDER BY id DESC LIMIT ?",
                (profile_id, limit),
            ).fetchall()
        return [Order.model_validate(dict(r)) for r in rows]

    # ---------------------------------------------------------- recommendations
    def add_recommendation(self, rec: Recommendation) -> Recommendation:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO recommendations (profile_id, symbol, action, status, as_of, created_at,"
                " payload) VALUES (?,?,?,?,?,?,?)",
                (rec.profile_id, rec.symbol, rec.action.value, rec.status.value,
                 rec.as_of.isoformat(), rec.created_at.isoformat(),
                 rec.model_dump_json(exclude={"id"})),
            )
        return rec.model_copy(update={"id": int(cur.lastrowid)})

    def get_recommendation(self, rec_id: int) -> Recommendation | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM recommendations WHERE id=?", (rec_id,)).fetchone()
        return self._rec(row) if row else None

    def list_recommendations(
        self, profile_id: int, status: str | None = None, limit: int = 200
    ) -> list[Recommendation]:
        sql = "SELECT * FROM recommendations WHERE profile_id=?"
        args: list = [profile_id]
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._rec(r) for r in rows]

    def expire_pending(self, profile_id: int, symbol: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recommendations SET status=? WHERE profile_id=? AND symbol=? AND status=?",
                (RecStatus.expired.value, profile_id, symbol, RecStatus.pending.value),
            )

    def set_decision(self, rec_id: int, status: RecStatus, comment: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recommendations SET status=?, decided_at=?, comment=? WHERE id=?",
                (status.value, utcnow().isoformat(), comment, rec_id),
            )

    def set_outcome(self, rec_id: int, ret: float, correct: bool | None, on: date) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE recommendations SET outcome_return=?, outcome_correct=?, outcome_date=? "
                "WHERE id=?",
                (ret, None if correct is None else int(correct), on.isoformat(), rec_id),
            )

    @staticmethod
    def _rec(row: sqlite3.Row) -> Recommendation:
        rec = Recommendation.model_validate_json(row["payload"])
        return rec.model_copy(update={
            "id": row["id"],
            "status": RecStatus(row["status"]),
            "decided_at": datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
            "comment": row["comment"],
            "outcome_return": row["outcome_return"],
            "outcome_correct": None if row["outcome_correct"] is None else bool(row["outcome_correct"]),
            "outcome_date": date.fromisoformat(row["outcome_date"]) if row["outcome_date"] else None,
        })

    # ------------------------------------------------------------ notifications
    def add_notification(self, n: Notification) -> Notification:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO notifications (profile_id, rec_id, symbol, message, created_at)"
                " VALUES (?,?,?,?,?)",
                (n.profile_id, n.rec_id, n.symbol, n.message, n.created_at.isoformat()),
            )
        return n.model_copy(update={"id": int(cur.lastrowid)})

    def list_notifications(self, profile_id: int, limit: int = 30) -> list[Notification]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE profile_id=? ORDER BY id DESC LIMIT ?",
                (profile_id, limit),
            ).fetchall()
        return [
            Notification.model_validate({**dict(r), "is_read": bool(r["is_read"])}) for r in rows
        ]

    def mark_notifications_read(self, profile_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE notifications SET is_read=1 WHERE profile_id=?", (profile_id,))
