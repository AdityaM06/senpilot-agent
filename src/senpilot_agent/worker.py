from __future__ import annotations

import logging
import tempfile
import threading
from email.utils import make_msgid

from .config import Settings
from .delivery import build_package, compose_reply, result_body
from .gmail import GmailClient, MessageGone
from .models import Job, JobState
from .parser import RequestParser
from .store import StateStore

log = logging.getLogger(__name__)


class JobWorker:
    def __init__(
        self,
        settings: Settings,
        store: StateStore,
        gmail: GmailClient,
        parser: RequestParser,
        board,
    ):
        self.settings = settings
        self.store = store
        self.gmail = gmail
        self.parser = parser
        self.board = board

    def recover(self) -> None:
        self.store.recover_interrupted()
        for job in self.store.jobs(JobState.SENDING):
            try:
                sent = self.gmail.find_sent_message(job.outbound_id, job.gmail_id)
            except Exception:
                sent = None
            if sent:
                self.store.mark(job.gmail_id, JobState.REPLIED, sent_gmail_id=sent)
            else:
                self.store.mark(
                    job.gmail_id,
                    JobState.NEEDS_REVIEW,
                    error="Delivery was interrupted or uncertain; inspect Sent before retrying",
                )

    def run_forever(self, stop: threading.Event) -> None:
        self.recover()
        while not stop.is_set():
            job = self.store.claim_next_job()
            if job is None:
                stop.wait(1)
            else:
                self.process(job)

    def process(self, job: Job) -> None:
        log.info("Starting job %s attempt %d", job.gmail_id, job.attempts)
        try:
            incoming = self.gmail.fetch_email(job.gmail_id)
        except MessageGone:
            self.store.mark(job.gmail_id, JobState.IGNORED, error="Incoming message was removed")
            return
        except ValueError:
            self.store.mark(job.gmail_id, JobState.IGNORED, error="Invalid incoming message")
            return
        except Exception as exc:
            self.store.mark(job.gmail_id, JobState.FAILED, error=type(exc).__name__)
            log.exception("Could not fetch job %s", job.gmail_id)
            return

        if incoming.automated or incoming.sender.casefold() == self.settings.agent_email.casefold():
            self.store.mark(job.gmail_id, JobState.IGNORED)
            return

        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="job-", dir=self.settings.data_dir) as temp:
            from pathlib import Path

            directory = Path(temp)
            package = None
            processing_error = None
            try:
                parsed = self.parser.parse(incoming)
                if parsed.status == "ready":
                    request = parsed.request()
                    self.store.save_request(job.gmail_id, request)
                    result = self.board.fetch(request, directory / "downloads")
                    package = build_package(
                        result, directory / "package", self.settings.email_attachment_budget
                    )
                    body = result_body(result, package)
                else:
                    body = (
                        "I can retrieve up to ten public documents for one matter and category.\n\n"
                        + parsed.explanation
                        + "\n\nExample: Please send Other Documents for M12205."
                    )
            except Exception as exc:
                processing_error = type(exc).__name__
                log.exception("Retrieval/interpretation failed for job %s", job.gmail_id)
                body = (
                    "I could not complete this request, "
                    "so I am not claiming a successful retrieval. "
                    "Please check the matter number and document category, or try again later. "
                    "If this continues, the agent operator will need to check the service."
                )
            outbound_id = make_msgid(domain=self.settings.agent_email.rsplit("@", 1)[-1])
            message = compose_reply(incoming, self.settings.agent_email, outbound_id, body, package)
            if len(message.as_bytes()) > self.settings.max_message_bytes:
                message = compose_reply(
                    incoming,
                    self.settings.agent_email,
                    outbound_id,
                    "The result exceeds the email message-size budget and could not be attached. "
                    "Please request a smaller document category.",
                )
                processing_error = "MessageSizeLimit"
            self.store.begin_send(job.gmail_id, outbound_id)
            try:
                sent_id = self.gmail.send(message, incoming.thread_id)
            except Exception as exc:
                self.store.mark(
                    job.gmail_id,
                    JobState.NEEDS_REVIEW,
                    error=f"Uncertain email delivery: {type(exc).__name__}",
                )
                log.exception(
                    "Uncertain delivery for job %s; not automatically resending", job.gmail_id
                )
            else:
                self.store.mark(
                    job.gmail_id, JobState.REPLIED, error=processing_error, sent_gmail_id=sent_id
                )
                log.info("Replied to job %s", job.gmail_id)
