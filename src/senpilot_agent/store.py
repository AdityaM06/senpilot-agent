from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import DocumentRequest, Job, JobState, utc_now


class StateStore:
    """Open a connection per operation so intake and the worker do not share one."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mailbox_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    email TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    history_id TEXT,
                    watch_expires_at TEXT,
                    last_sync_at TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    gmail_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    request_json TEXT,
                    outbound_id TEXT UNIQUE,
                    sent_gmail_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize_mailbox(self, email: str) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO mailbox_state(id, email, activated_at) VALUES(1, ?, ?)",
                (email.casefold(), utc_now().isoformat()),
            )
            if db.execute("SELECT email FROM mailbox_state").fetchone()[0] != email.casefold():
                raise ValueError("This state directory belongs to a different mailbox")

    def mailbox(self) -> dict:
        with self._db() as db:
            row = db.execute("SELECT * FROM mailbox_state WHERE id=1").fetchone()
            if row is None:
                raise ValueError("Mailbox state is not initialized")
            return dict(row)

    def commit_discovery(
        self, ids: list[str], history_id: str, expected_history: str | None
    ) -> None:
        now = utc_now().isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT history_id FROM mailbox_state WHERE id=1").fetchone()
            if current is None or current[0] != expected_history:
                raise ValueError("Mailbox checkpoint changed during synchronization")
            db.executemany(
                "INSERT OR IGNORE INTO jobs(gmail_id, created_at, updated_at) VALUES(?, ?, ?)",
                [(message_id, now, now) for message_id in ids],
            )
            db.execute(
                "UPDATE mailbox_state SET history_id=?, last_sync_at=? WHERE id=1",
                (history_id, now),
            )

    def save_watch_expiration(self, expiration: datetime) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE mailbox_state SET watch_expires_at=? WHERE id=1", (expiration.isoformat(),)
            )

    def claim_next_job(self) -> Job | None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, gmail_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? "
                "WHERE gmail_id=?",
                (utc_now().isoformat(), row["gmail_id"]),
            )
            return self._job(dict(row) | {"state": "running", "attempts": row["attempts"] + 1})

    def save_request(self, gmail_id: str, request: DocumentRequest) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET request_json=? WHERE gmail_id=?",
                (request.model_dump_json(), gmail_id),
            )

    def begin_send(self, gmail_id: str, outbound_id: str) -> None:
        with self._db() as db:
            result = db.execute(
                "UPDATE jobs SET state='sending', outbound_id=?, updated_at=? "
                "WHERE gmail_id=? AND state='running'",
                (outbound_id, utc_now().isoformat(), gmail_id),
            )
            if result.rowcount != 1:
                raise ValueError("Only a running job can begin sending")

    def mark(
        self,
        gmail_id: str,
        state: JobState,
        *,
        error: str | None = None,
        sent_gmail_id: str | None = None,
    ) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET state=?, last_error=?, sent_gmail_id=?, updated_at=? "
                "WHERE gmail_id=?",
                (state, error, sent_gmail_id, utc_now().isoformat(), gmail_id),
            )

    def recover_interrupted(self) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET state=CASE WHEN attempts < 2 THEN 'queued' ELSE 'failed' END, "
                "last_error='Worker stopped during processing', updated_at=? WHERE state='running'",
                (utc_now().isoformat(),),
            )

    def jobs(self, state: JobState | None = None) -> list[Job]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM jobs " + ("WHERE state=? " if state else "") + "ORDER BY created_at",
                (state,) if state else (),
            ).fetchall()
            return [self._job(dict(row)) for row in rows]

    @staticmethod
    def _job(row: dict) -> Job:
        return Job.model_validate({name: row[name] for name in Job.model_fields})

    def status(self) -> str:
        return json.dumps([job.model_dump(mode="json") for job in self.jobs()], indent=2)
