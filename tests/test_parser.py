from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from senpilot_agent.config import Settings
from senpilot_agent.models import DocumentRequest, DocumentType, InboundEmail, ParseResult
from senpilot_agent.parser import (
    GeminiRequestParser,
    OpenAIRequestParser,
    create_request_parser,
    latest_text,
    validate_extraction,
)


def ready(matter="M12205"):
    return ParseResult(
        status="ready",
        matter_number=matter,
        document_type=DocumentType.OTHER_DOCUMENTS,
        explanation="Requested",
    )


def test_missing_or_multiple_matters_cannot_execute():
    assert validate_extraction(ready(), "Other Documents please").status == "clarify"
    assert validate_extraction(ready(), "M12205 or M12383 Other Documents").status == "clarify"


def test_quoted_matter_is_not_used_to_fill_latest_request():
    email = InboundEmail(
        gmail_id="a",
        sender="user@example.com",
        subject="Documents",
        text="Please get Other Documents.\nOn Monday Alice wrote:\nM12205",
    )
    assert "M12205" not in latest_text(email)
    assert validate_extraction(ready(), latest_text(email)).status == "clarify"


def test_normalization_preserves_five_digit_ids():
    request = DocumentRequest(matter_number=" m01234 ", document_type="Exhibits")
    assert request.matter_number == "M01234"
    assert DocumentType.parse("other-documents") == DocumentType.OTHER_DOCUMENTS


def test_model_cannot_invent_an_identifier():
    class Responses:
        def parse(self, **kwargs):
            assert kwargs["store"] is False
            return SimpleNamespace(output_parsed=ready("M12383"))

    parser = OpenAIRequestParser("unused", "test", SimpleNamespace(responses=Responses()))
    email = InboundEmail(gmail_id="a", sender="user@example.com", text="M12205 Other Documents")
    assert parser.parse(email).status == "clarify"


def gemini_parser_with_response(text):
    def generate_content(**kwargs):
        assert kwargs["config"]["response_schema"] is ParseResult
        assert kwargs["config"]["thinking_config"]["thinking_level"] == "LOW"
        assert "tools" not in kwargs["config"]
        assert "sender" not in kwargs["contents"]
        return SimpleNamespace(
            text=text,
            usage_metadata=SimpleNamespace(prompt_token_count=12, total_token_count=30),
        )

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return GeminiRequestParser("test", client=client)


def test_gemini_preserves_validation_and_tracks_usage():
    email = InboundEmail(gmail_id="a", sender="sender@example.com", text="M12205 Other Documents")
    parser = gemini_parser_with_response(ready().model_dump_json())
    assert parser.parse(email) == ready()
    assert parser.last_usage == {"input_tokens": 12, "output_tokens": 18}
    invented = gemini_parser_with_response(ready("M12383").model_dump_json())
    assert invented.parse(email).status == "clarify"


@pytest.mark.parametrize("response", [None, "", "not JSON", '{"status": "ready"}'])
def test_gemini_rejects_empty_or_invalid_output(response):
    parser = gemini_parser_with_response(response)
    email = InboundEmail(gmail_id="a", sender="user@example.com", text="M12205 Other Documents")
    with pytest.raises(ValueError):
        parser.parse(email)


def test_vertex_worker_requires_project_instead_of_model_api_key(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("{}")
    settings = Settings(
        agent_email="agent@example.com",
        pubsub_topic="topic",
        pubsub_subscription="subscription",
        oauth_token_file=token,
    )
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        settings.require_worker()
    settings.google_cloud_project = "project"
    settings.require_worker()
    settings.model_provider = "openai"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings.require_worker()
    with pytest.raises(ValidationError):
        Settings(model_provider="unknown")


def test_default_factory_uses_vertex_identity_without_an_api_key(monkeypatch):
    from google import genai

    options = {}

    def client(**kwargs):
        options.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(genai, "Client", client)
    settings = Settings(google_cloud_project="project")
    parser = create_request_parser(settings)
    assert isinstance(parser, GeminiRequestParser)
    assert options["vertexai"] is True
    assert options["project"] == "project"
    assert options["location"] == "global"
    assert "api_key" not in options
    assert options["http_options"]["retry_options"]["attempts"] == 2


def test_installed_sdk_accepts_schema_without_network_access():
    import json

    import httpx
    from google import genai
    from google.oauth2.credentials import Credentials

    expected = ready()

    def handle(request):
        payload = json.loads(request.content)
        generation = payload["generationConfig"]
        assert generation["responseMimeType"] == "application/json"
        assert "responseSchema" in generation
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": expected.model_dump_json()}],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 12, "totalTokenCount": 30},
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        genai.Client(
            vertexai=True,
            project="offline-test",
            location="global",
            credentials=Credentials(token="offline-test-token"),
            http_options={"api_version": "v1", "httpx_client": http},
        ) as client,
    ):
        parser = GeminiRequestParser("gemini-3.8-flash", client=client)
        email = InboundEmail(
            gmail_id="test", sender="synthetic@example.com", text="M12205 Other Documents"
        )
        assert parser.parse(email) == expected
