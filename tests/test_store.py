import pytest

from senpilot_agent.models import JobState
from senpilot_agent.store import StateStore


def initialized(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.initialize_mailbox("agent@example.com")
    return store


def test_repeated_events_and_reopen_keep_one_job(tmp_path):
    store = initialized(tmp_path)
    store.commit_discovery(["a", "b"], "10", None)
    store.commit_discovery(["a"], "11", "10")
    reopened = StateStore(store.path)
    assert [j.gmail_id for j in reopened.jobs()] == ["a", "b"]
    assert reopened.mailbox()["history_id"] == "11"


def test_stale_checkpoint_cannot_insert_jobs(tmp_path):
    store = initialized(tmp_path)
    store.commit_discovery(["a"], "10", None)
    with pytest.raises(ValueError, match="checkpoint"):
        store.commit_discovery(["b"], "11", None)
    assert [j.gmail_id for j in store.jobs()] == ["a"]
    assert store.mailbox()["history_id"] == "10"


def test_claim_and_restart_do_not_requeue_uncertain_sends(tmp_path):
    store = initialized(tmp_path)
    store.commit_discovery(["a", "b"], "10", None)
    a = store.claim_next_job()
    store.begin_send(a.gmail_id, "<reply-a@example.com>")
    assert store.claim_next_job().gmail_id == "b"
    assert store.claim_next_job() is None
    store.recover_interrupted()
    assert store.claim_next_job().gmail_id == "b"
    assert store.jobs(JobState.SENDING)[0].gmail_id == "a"


def test_database_cannot_be_reused_for_another_mailbox(tmp_path):
    store = initialized(tmp_path)
    with pytest.raises(ValueError, match="different mailbox"):
        store.initialize_mailbox("someone-else@example.com")
