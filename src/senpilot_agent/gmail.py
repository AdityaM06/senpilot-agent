from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import InboundEmail

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class HistoryExpired(Exception):
    pass


class MessageGone(Exception):
    pass


@dataclass(frozen=True)
class MailboxChanges:
    message_ids: list[str]
    history_id: str


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def decode_email(message_id: str, payload: dict) -> InboundEmail:
    raw = base64.urlsafe_b64decode(payload["raw"] + "===")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    senders = getaddresses(message.get_all("From", []))
    if len(senders) != 1 or not senders[0][1] or "@" not in senders[0][1]:
        raise ValueError("Incoming email needs exactly one valid From address")
    body = message.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body and body.get_content_type() == "text/html":
        parser = _TextOnly()
        parser.feed(text)
        text = "".join(parser.parts)
    automated = (
        message.get("Auto-Submitted", "no").casefold() != "no"
        or message.get("Precedence", "").casefold() in {"bulk", "junk", "list"}
        or bool(message.get("List-Id"))
        or message.get("Return-Path", "").strip() == "<>"
        or message.get_content_type() == "multipart/report"
    )
    return InboundEmail(
        gmail_id=message_id,
        thread_id=payload.get("threadId", ""),
        message_id=str(message.get("Message-ID", "")),
        sender=senders[0][1],
        subject=str(message.get("Subject", "")),
        text=text[:50_000],
        received_at=datetime.fromtimestamp(int(payload["internalDate"]) / 1000, UTC),
        automated=automated,
    )


def authorize(client_file: Path, token_file: Path, expected_email: str) -> None:
    if not expected_email:
        raise ValueError("Set AGENT_EMAIL before authorizing Gmail")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    actual = service.users().getProfile(userId="me").execute(num_retries=2)["emailAddress"]
    if actual.casefold() != expected_email.casefold():
        raise ValueError("The authorized mailbox does not match AGENT_EMAIL")
    if not credentials.refresh_token:
        raise ValueError("Google did not grant offline access; repeat authorization")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(credentials.to_json())
    token_file.chmod(0o600)


class GmailClient:
    def __init__(self, token_file: Path | None = None, *, service=None):
        if service is None:
            credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
            if not credentials.valid:
                if not credentials.refresh_token:
                    raise ValueError("Gmail offline authorization is missing")
                credentials.refresh(Request())
            service = build(
                "gmail",
                "v1",
                cache_discovery=False,
                http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)),
            )
        self.service = service

    def profile(self) -> dict:
        return self.service.users().getProfile(userId="me").execute(num_retries=2)

    def fetch_email(self, message_id: str) -> InboundEmail:
        try:
            data = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute(num_retries=2)
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise MessageGone(message_id) from exc
            raise
        return decode_email(message_id, data)

    def list_changes(self, history_id: str) -> MailboxChanges:
        ids = set()
        token = None
        checkpoint = history_id
        while True:
            try:
                data = (
                    self.service.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=history_id,
                        historyTypes=["messageAdded"],
                        labelId="INBOX",
                        pageToken=token,
                    )
                    .execute(num_retries=2)
                )
            except HttpError as exc:
                if exc.resp.status == 404:
                    raise HistoryExpired from exc
                raise
            for item in data.get("history", []):
                for entry in item.get("messagesAdded", []):
                    ids.add(entry["message"]["id"])
            checkpoint = data.get("historyId", checkpoint)
            token = data.get("nextPageToken")
            if not token:
                return MailboxChanges(sorted(ids), checkpoint)

    def list_inbox_since(self, timestamp: datetime) -> list[str]:
        ids = []
        token = None
        while True:
            data = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=["INBOX"],
                    q=f"after:{int(timestamp.timestamp())}",
                    pageToken=token,
                    maxResults=100,
                )
                .execute(num_retries=2)
            )
            ids.extend(message["id"] for message in data.get("messages", []))
            token = data.get("nextPageToken")
            if not token:
                return ids

    def watch_inbox(self, topic: str) -> dict:
        return (
            self.service.users()
            .watch(
                userId="me",
                body={
                    "topicName": topic,
                    "labelIds": ["INBOX"],
                    "labelFilterBehavior": "INCLUDE",
                },
            )
            .execute(num_retries=2)
        )

    def find_sent_message(self, outbound_id: str) -> str | None:
        data = (
            self.service.users()
            .messages()
            .list(userId="me", q=f"in:sent rfc822msgid:{outbound_id.strip('<>')}", maxResults=2)
            .execute(num_retries=2)
        )
        messages = data.get("messages", [])
        return messages[0]["id"] if messages else None

    def send(self, message: EmailMessage, thread_id: str) -> str:
        body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
        if thread_id:
            body["threadId"] = thread_id
        # Sending is intentionally not automatically retried: a timeout can follow acceptance.
        result = self.service.users().messages().send(userId="me", body=body).execute(num_retries=0)
        return result["id"]
