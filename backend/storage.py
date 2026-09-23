"""SQLite storage: settings, versioned strategies, kline cache, tickers, trading days, jobs."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategy_versions(
  strategy_id INTEGER NOT NULL, version INTEGER NOT NULL, config_json TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(strategy_id, version));
CREATE TABLE IF NOT EXISTS klines(
  thscode TEXT NOT NULL, date_ms INTEGER NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume REAL NOT NULL, turnover REAL,
  PRIMARY KEY(thscode, date_ms));
CREATE TABLE IF NOT EXISTS tickers(
  thscode TEXT PRIMARY KEY, name TEXT NOT NULL, asset_type TEXT NOT NULL,
  exchange TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trading_days(date_ms INTEGER PRIMARY KEY, date TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT NOT NULL,
  progress_json TEXT, result_json TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_klines_code ON klines(thscode);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path = "data/app.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    # ---- settings ----
    def get_settings(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                out[r["key"]] = r["value"]
        return out

    def update_settings(self, partial: dict) -> dict:
        with self._write_lock, self._conn() as c:
            for k, v in partial.items():
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v, ensure_ascii=False)),
                )
        return self.get_settings()

    # ---- strategies ----
    def create_strategy(self, config: dict) -> dict:
        now = _now()
        with self._write_lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO strategies(name, created_at, updated_at) VALUES(?, ?, ?)",
                (config["name"], now, now),
            )
            sid = cur.lastrowid
            c.execute(
                "INSERT INTO strategy_versions(strategy_id, version, config_json, created_at) VALUES(?, 1, ?, ?)",
                (sid, json.dumps(config, ensure_ascii=False), now),
            )
        return {"id": sid, "version": 1, "created_at": now, **config}

    def update_strategy(self, sid: int, config: dict) -> dict:
        now = _now()
        with self._write_lock, self._conn() as c:
            row = c.execute("SELECT MAX(version) AS v FROM strategy_versions WHERE strategy_id=?", (sid,)).fetchone()
            if row is None or row["v"] is None:
                raise KeyError(f"strategy {sid} not found")
            version = row["v"] + 1
            c.execute(
                "INSERT INTO strategy_versions(strategy_id, version, config_json, created_at) VALUES(?, ?, ?, ?)",
                (sid, version, json.dumps(config, ensure_ascii=False), now),
            )
            c.execute("UPDATE strategies SET name=?, updated_at=? WHERE id=?", (config["name"], now, sid))
        return {"id": sid, "version": version, "created_at": now, **config}

    def restore_version(self, sid: int, version: int) -> dict:
        config = self.get_version(sid, version)
        return self.update_strategy(sid, config)

    def get_version(self, sid: int, version: int) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT config_json FROM strategy_versions WHERE strategy_id=? AND version=?",
                (sid, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"strategy {sid} version {version} not found")
        return json.loads(row["config_json"])

    def get_strategy(self, sid: int) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT * FROM strategies WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise KeyError(f"strategy {sid} not found")
            versions = c.execute(
                "SELECT version, created_at FROM strategy_versions WHERE strategy_id=? ORDER BY version",
                (sid,),
            ).fetchall()
        latest = versions[-1]["version"]
        return {
            "id": sid,
            "version": latest,
            "current": self.get_version(sid, latest),
            "versions": [dict(v) for v in versions],
        }

    def list_strategies(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            with self._conn() as c2:
                vrows = c2.execute(
                    "SELECT version, config_json, created_at FROM strategy_versions WHERE strategy_id=? ORDER BY version",
                    (r["id"],),
                ).fetchall()
            latest = vrows[-1]
            cfg = json.loads(latest["config_json"])
            out.append({
                "id": r["id"],
                "name": cfg.get("name", r["name"]),
                "description": cfg.get("description", ""),
                "version": latest["version"],
                "updated_at": r["updated_at"],
                "parse_engine": cfg.get("parse_engine", "llm"),
                "entry_count": len(cfg.get("entry", {}).get("conditions", [])),
                "exit_count": len(cfg.get("exit", {}).get("conditions", [])),
            })
        return out

    def delete_strategy(self, sid: int) -> bool:
        with self._write_lock, self._conn() as c:
            cur = c.execute("DELETE FROM strategies WHERE id=?", (sid,))
            count = cur.rowcount
            c.execute("DELETE FROM strategy_versions WHERE strategy_id=?", (sid,))
        return count > 0

    # ---- klines ----
    def upsert_klines(self, thscode: str, bars: list[dict]) -> None:
        rows = [
            (thscode, b["date_ms"], b["open"], b["high"], b["low"], b["close"], b["volume"], b.get("turnover"))
            for b in bars
        ]
        with self._write_lock, self._conn() as c:
            c.executemany(
                """INSERT INTO klines(thscode, date_ms, open, high, low, close, volume, turnover)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(thscode, date_ms) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     close=excluded.close, volume=excluded.volume, turnover=excluded.turnover""",
                rows,
            )

    def get_klines(self, thscode: str, start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
        q = "SELECT date_ms, open, high, low, close, volume, turnover FROM klines WHERE thscode=?"
        args: list = [thscode]
        if start_ms is not None:
            q += " AND date_ms>=?"
            args.append(start_ms)
        if end_ms is not None:
            q += " AND date_ms<=?"
            args.append(end_ms)
        q += " ORDER BY date_ms"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def last_kline_date(self, thscode: str) -> int | None:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date_ms) AS m FROM klines WHERE thscode=?", (thscode,)).fetchone()
        return row["m"] if row else None

    def cached_codes(self) -> set[str]:
        with self._conn() as c:
            rows = c.execute("SELECT DISTINCT thscode FROM klines").fetchall()
        return {r["thscode"] for r in rows}

    # ---- tickers ----
    def upsert_tickers(self, items: list[dict]) -> None:
        now = _now()
        rows = [
            (t["thscode"], t["name"], t["asset_type"], t.get("exchange"), now) for t in items
        ]
        with self._write_lock, self._conn() as c:
            c.executemany(
                """INSERT INTO tickers(thscode, name, asset_type, exchange, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(thscode) DO UPDATE SET
                     name=excluded.name, asset_type=excluded.asset_type,
                     exchange=excluded.exchange, updated_at=excluded.updated_at""",
                rows,
            )

    def get_ticker(self, thscode: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tickers WHERE thscode=?", (thscode,)).fetchone()
        return dict(row) if row else None

    def search_tickers(self, q: str, limit: int = 20) -> list[dict]:
        like = f"%{q}%"
        with self._conn() as c:
            rows = c.execute(
                """SELECT thscode, name, asset_type, exchange FROM tickers
                   WHERE thscode LIKE ? OR name LIKE ? ORDER BY thscode LIMIT ?""",
                (like, like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_tickers(self, asset_type: str | None = None) -> list[dict]:
        with self._conn() as c:
            if asset_type:
                rows = c.execute(
                    "SELECT thscode, name, asset_type, exchange FROM tickers WHERE asset_type=? ORDER BY thscode",
                    (asset_type,),
                ).fetchall()
            else:
                rows = c.execute("SELECT thscode, name, asset_type, exchange FROM tickers ORDER BY thscode").fetchall()
        return [dict(r) for r in rows]

    # ---- trading days ----
    def upsert_trading_days(self, items: list[dict]) -> None:
        rows = [(d["date_ms"], d["date"]) for d in items]
        with self._write_lock, self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO trading_days(date_ms, date) VALUES(?, ?)", rows
            )

    def trading_days_list(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT date_ms, date FROM trading_days ORDER BY date_ms").fetchall()
        return [dict(r) for r in rows]

    def latest_trading_day(self, at_ms: int | None = None) -> int | None:
        with self._conn() as c:
            if at_ms is None:
                row = c.execute("SELECT MAX(date_ms) AS m FROM trading_days").fetchone()
            else:
                row = c.execute(
                    "SELECT MAX(date_ms) AS m FROM trading_days WHERE date_ms<=?", (at_ms,)
                ).fetchone()
        return row["m"] if row else None

    # ---- jobs ----
    def create_job(self, job_id: str, job_type: str) -> None:
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO jobs(id, type, status, created_at, updated_at) VALUES(?, ?, 'running', ?, ?)",
                (job_id, job_type, now, now),
            )

    def update_job(
        self,
        job_id: str,
        status: str | None = None,
        progress: dict | None = None,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        sets, args = ["updated_at=?"], [_now()]
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if progress is not None:
            sets.append("progress_json=?")
            args.append(json.dumps(progress, ensure_ascii=False))
        if result is not None:
            sets.append("result_json=?")
            args.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            sets.append("error=?")
            args.append(error)
        args.append(job_id)
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", args)

    def get_job(self, job_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["progress"] = json.loads(out.pop("progress_json") or "null")
        out["result"] = json.loads(out.pop("result_json") or "null")
        return out
