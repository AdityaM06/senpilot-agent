import base64
from email.message import EmailMessage

import pytest

from senpilot_agent.gmail import decode_email


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
