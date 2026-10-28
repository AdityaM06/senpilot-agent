import base64
import logging
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from senpilot_agent.gmail import GmailClient, authorize, decode_email


def payload(message):
    return {
        "raw": base64.urlsafe_b64encode(message.as_bytes()).decode(),
        "internalDate": "1790000000000",
        "threadId": "thread",
    }


def test_decode_uses_from_address_and_plain_body_not_attachments():
    message = EmailMessage()
    message["From"] = "Example User <user@example.com>"
    message["Reply-To"] = "another@example.com"
    message["Subject"] = "M12205 Other Documents"
    message.set_content("Please send the filings")
    message.add_attachment(b"binary", maintype="application", subtype="pdf", filename="x.pdf")
    decoded = decode_email("id", payload(message))
    assert decoded.sender == "user@example.com"
    assert decoded.text.strip() == "Please send the filings"
    assert decoded.thread_id == "thread"
    assert not decoded.automated


def test_html_only_body_and_autoreply_are_supported():
    message = EmailMessage()
    message["From"] = "user@example.com"
    message["Auto-Submitted"] = "auto-replied"
    message.set_content("<p>M12205</p><div>Other Documents</div>", subtype="html")
    decoded = decode_email("id", payload(message))
    assert "M12205\nOther Documents" in decoded.text
    assert decoded.automated


def test_multiple_senders_are_rejected():
    message = EmailMessage()
    message["From"] = "a@example.com, b@example.com"
    message.set_content("Hello")
    with pytest.raises(ValueError, match="exactly one"):
        decode_email("id", payload(message))


def test_decode_normalizes_outlook_message_id_whitespace():
    message = EmailMessage()
    message["From"] = "user@example.com"
    message["Message-ID"] = " <outlook@example.com>"
    message.set_content("M12205 Other Documents")
    assert decode_email("id", payload(message)).message_id == "<outlook@example.com>"


@pytest.mark.parametrize(
    ("labels", "marker", "expected"),
    [
        (["SENT"], " <local@example.com> ", "sent-id"),
        (["INBOX"], "<local@example.com>", None),
        (["SENT"], "<unrelated@example.com>", None),
    ],
)
def test_reconciliation_handles_rewritten_rfc_id_using_only_sent_marker(labels, marker, expected):
    def response(value):
        return SimpleNamespace(execute=lambda **_: value)

    messages = SimpleNamespace(
        list=lambda **_: response({"messages": []}),
        get=lambda **_: response({"threadId": "request-thread"}),
    )

    def get_thread(**kwargs):
        assert kwargs["id"] == "request-thread"
        assert kwargs["format"] == "metadata"
        return response(
            {
                "messages": [
                    {
                        "id": "sent-id",
                        "labelIds": labels,
                        "payload": {
                            "headers": [
                                {"name": "Message-ID", "value": "<rewritten@mail.gmail.com>"},
                                {"name": "X-Senpilot-Delivery-ID", "value": marker},
                            ]
                        },
                    }
                ]
            }
        )

    service = SimpleNamespace(
        users=lambda: SimpleNamespace(
            messages=lambda: messages, threads=lambda: SimpleNamespace(get=get_thread)
        )
    )
    client = GmailClient(service=service)
    assert client.find_sent_message("<local@example.com>", "incoming-id") == expected


def test_authorization_does_not_log_callback_codes(monkeypatch, tmp_path, caplog):
    from senpilot_agent import gmail

    logger = logging.getLogger("google_auth_oauthlib.flow")
    previous_level = logger.level

    def run_local_server(**kwargs):
        assert kwargs["timeout_seconds"] == 300
        logger.info("GET /?code=one-time-oauth-code")
        return SimpleNamespace(refresh_token="test-token", to_json=lambda: "{}")

    flow = SimpleNamespace(run_local_server=run_local_server)
    monkeypatch.setattr(gmail.InstalledAppFlow, "from_client_secrets_file", lambda *_: flow)
    service = SimpleNamespace(
        users=lambda: SimpleNamespace(
            getProfile=lambda **_: SimpleNamespace(
                execute=lambda **_: {"emailAddress": "agent@example.com"}
            )
        )
    )
    monkeypatch.setattr(gmail, "build", lambda *_, **__: service)
    with caplog.at_level(logging.INFO):
        authorize(tmp_path / "client.json", tmp_path / "token.json", "agent@example.com")
    assert "one-time-oauth-code" not in caplog.text
    assert logger.level == previous_level
    assert (tmp_path / "token.json").stat().st_mode & 0o777 == 0o600
