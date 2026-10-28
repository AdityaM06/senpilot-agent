import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from senpilot_agent.config import Settings
from senpilot_agent.gmail import HistoryExpired, MailboxChanges
from senpilot_agent.intake import GmailPushListener, InboxSynchronizer
from senpilot_agent.store import StateStore


class FakeGmail:
    def profile(self):
        return {"emailAddress": "agent@example.com", "historyId": "100"}

    def watch_inbox(self, topic):
        return {
            "historyId": "100",
            "expiration": str(int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)),
        }

    def list_inbox_since(self, timestamp):
        return ["before-watch"]

    def list_changes(self, checkpoint):
        return MailboxChanges(["after-watch"], "110")


def synchronizer(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    sync = InboxSynchronizer(FakeGmail(), store, "agent@example.com", "topic")
    sync.start()
    return sync, store


def test_initial_scan_and_history_cover_both_sides_of_watch(tmp_path):
    sync, store = synchronizer(tmp_path)
    assert {job.gmail_id for job in store.jobs()} == {"before-watch", "after-watch"}
    sync.renew_watch()
    assert store.mailbox()["history_id"] == "110"


def test_expired_history_rescans_without_duplicating_jobs(tmp_path):
    sync, store = synchronizer(tmp_path)

    def expired(checkpoint):
        raise HistoryExpired

    sync.gmail.list_changes = expired
    sync.gmail.profile = lambda: {"emailAddress": "agent@example.com", "historyId": "200"}
    sync.sync()
    assert len(store.jobs()) == 2
    assert store.mailbox()["history_id"] == "200"


def test_notification_ack_only_after_successful_sync(tmp_path):
    sync, store = synchronizer(tmp_path)
    events = []
    message = SimpleNamespace(
        data=json.dumps({"emailAddress": "agent@example.com", "historyId": "999"}).encode(),
        ack=lambda: events.append("ack"),
        nack=lambda: events.append("nack"),
    )
    listener = GmailPushListener(Settings(agent_email="agent@example.com"), store)
    assert listener.handle_notification(sync, message)
    assert events == ["ack"]
    assert store.mailbox()["history_id"] == "110"

    def broken():
        raise OSError("offline")

    sync.sync = broken
    assert not listener.handle_notification(sync, message)
    assert events == ["ack", "nack"]


def test_wrong_authorized_mailbox_is_rejected(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    sync = InboxSynchronizer(FakeGmail(), store, "wrong@example.com", "topic")
    with pytest.raises(ValueError, match="does not match"):
        sync.start()
