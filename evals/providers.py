from __future__ import annotations

import re

from senpilot_agent.models import DocumentType, InboundEmail, ParseResult
from senpilot_agent.parser import (
    SYSTEM_PROMPT,
    GeminiRequestParser,
    latest_text,
    validate_extraction,
)


class BaselineParser:
    model = "regex-baseline"
    last_usage = None

    def parse(self, email: InboundEmail) -> ParseResult:
        text = latest_text(email)
        matters = set(re.findall(r"\bM\d{5}\b", text.upper()))
        categories = [
            category
            for category in DocumentType
            if re.search(rf"\b{re.escape(category.value)}\b", text, re.I)
        ]
        if len(matters) == 1 and len(categories) == 1:
            return ParseResult(
                status="ready",
                matter_number=next(iter(matters)),
                document_type=categories[0],
                explanation="Exact match",
            )
        status = "clarify" if matters or categories else "unsupported"
        return ParseResult(
            status=status,
            matter_number=None,
            document_type=None,
            explanation="The exact fields were not both unambiguous",
        )


class ClaudeParser:
    def __init__(self, key: str, model: str):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=key, timeout=30, max_retries=1)
        self.model = model
        self.last_usage = None

    def parse(self, email: InboundEmail) -> ParseResult:
        schema = ParseResult.model_json_schema()
        schema["additionalProperties"] = False
        text = latest_text(email)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        self.last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        content = "".join(block.text for block in response.content if block.type == "text")
        return validate_extraction(ParseResult.model_validate_json(content), text)


class GeminiParser(GeminiRequestParser):
    def __init__(self, key: str, model: str):
        super().__init__(model, api_key=key)
