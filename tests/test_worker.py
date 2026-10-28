from types import SimpleNamespace

from senpilot_agent.config import Settings
from senpilot_agent.models import (
    DocumentType,
    InboundEmail,
    JobState,
    MatterSnapshot,
    ParseResult,
    RetrievalResult,
)
from senpilot_agent.store import StateStore
from senpilot_agent.worker import JobWorker


def worker_setup(tmp_path, *, automated=False):
    settings = Settings(agent_email="agent@example.com", data_dir=tmp_path / "work")
    store = StateStore(tmp_path / "state.sqlite3")
    store.initialize_mailbox(settings.agent_email)
    store.commit_discovery(["incoming"], "10", None)
    incoming = InboundEmail(gmail_id="incoming", sender="user@example.com", automated=automated)
    sent = []

    def send(message, thread):
        sent.append(message)
        return "sent-id"

    gmail = SimpleNamespace(
        fetch_email=lambda _: incoming, send=send, find_sent_message=lambda *_: None
    )
    parser = SimpleNamespace(
        parse=lambda _: ParseResult(
            status="ready", matter_number="M12205", document_type="Other Documents", explanation=""
        )
    )

    def fetch(request, directory):
        return RetrievalResult(
            request=request,
            snapshot=MatterSnapshot(
                matter_number=request.matter_number,
                metadata={"Title": "Verified source title"},
                source_url="https://uarb.novascotia.ca/fmi/webd/UARB15",
                category_counts={category: 0 for category in DocumentType},
                inventories={category: [] for category in DocumentType},
            ),
        )

    worker = JobWorker(settings, store, gmail, parser, SimpleNamespace(fetch=fetch))
    return worker, store, sent


def test_success_is_persisted_and_temp_files_are_removed(tmp_path):
    worker, store, sent = worker_setup(tmp_path)
    worker.process(store.claim_next_job())
    assert store.jobs()[0].state == JobState.REPLIED
    assert len(sent) == 1
    assert sent[0]["To"] == "user@example.com"
    assert "Total unique source files: 0" in sent[0].get_body().get_content()
    assert list(worker.settings.data_dir.iterdir()) == []
    assert store.claim_next_job() is None


def test_automated_messages_do_not_trigger_another_reply(tmp_path):
    worker, store, sent = worker_setup(tmp_path, automated=True)
    worker.process(store.claim_next_job())
    assert store.jobs()[0].state == JobState.IGNORED
    assert sent == []


def test_timeout_after_send_is_not_retried(tmp_path):
    worker, store, sent = worker_setup(tmp_path)

    def uncertain(message, thread):
        sent.append(message)
        raise TimeoutError("Response lost after remote acceptance")

    worker.gmail.send = uncertain
    worker.process(store.claim_next_job())
    worker.recover()
    assert store.jobs()[0].state == JobState.NEEDS_REVIEW
    assert len(sent) == 1
    assert store.claim_next_job() is None


def test_restart_can_reconcile_an_accepted_email(tmp_path):
    worker, store, sent = worker_setup(tmp_path)
    store.claim_next_job()
    store.begin_send("incoming", "<outbound@example.com>")

    def find_sent(outbound_id, incoming_id):
        assert outbound_id == "<outbound@example.com>"
        assert incoming_id == "incoming"
        return "sent-id"

    worker.gmail.find_sent_message = find_sent
    worker.recover()
    assert store.jobs()[0].state == JobState.REPLIED
    assert sent == []


def test_retrieval_error_sends_an_explicit_failure(tmp_path):
    worker, store, sent = worker_setup(tmp_path)

    def broken(request, directory):
        raise RuntimeError("Board unavailable")

    worker.board.fetch = broken
    worker.process(store.claim_next_job())
    assert "could not complete" in sent[0].get_body().get_content()
    assert len(list(sent[0].iter_attachments())) == 0
    assert store.jobs()[0].last_error == "RuntimeError"
