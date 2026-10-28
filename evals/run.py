from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from providers import BaselineParser, ClaudeParser, GeminiParser

from senpilot_agent.models import InboundEmail
from senpilot_agent.parser import GeminiRequestParser, OpenAIRequestParser


def main():
    cli = argparse.ArgumentParser(
        description="Compare request extraction on frozen synthetic emails"
    )
    cli.add_argument(
        "--providers",
        nargs="+",
        choices=["baseline", "openai", "claude", "gemini"],
        default=["baseline", "openai", "claude", "gemini"],
    )
    cli.add_argument("--repeats", type=int, choices=range(1, 6), default=3)
    cli.add_argument("--output", type=Path, default=Path("var/evals"))
    cli.add_argument(
        "--prices", type=Path, help="Optional checked pricing JSON; see pricing.example.json"
    )
    args = cli.parse_args()
    load_dotenv(Path.cwd() / ".env")
    cases = json.loads(Path(__file__).with_name("requests.json").read_text())
    prices = json.loads(args.prices.read_text()) if args.prices else {}
    providers = {"baseline": BaselineParser()}
    skipped = {}
    configuration = [
        (
            "openai",
            "OPENAI_API_KEY",
            OpenAIRequestParser,
            os.getenv("OPENAI_MODEL", "gpt-5.4-mini-2026-03-17"),
        ),
        (
            "claude",
            "ANTHROPIC_API_KEY",
            ClaudeParser,
            os.getenv("CLAUDE_MODEL", "claude-haiku-5-5"),
        ),
        ("gemini", "GEMINI_API_KEY", GeminiParser, os.getenv("GEMINI_MODEL", "gemini-3.8-flash")),
    ]
    for name, key, factory, model in configuration:
        if name not in args.providers:
            continue
        if name == "gemini" and not os.getenv(key) and os.getenv("GOOGLE_CLOUD_PROJECT"):
            providers[name] = GeminiRequestParser(
                model,
                project=os.environ["GOOGLE_CLOUD_PROJECT"],
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            )
            continue
        if not os.getenv(key):
            skipped[name] = f"{key} is not configured"
        else:
            providers[name] = factory(os.environ[key], model)
    rows, summary = [], []
    for name in args.providers:
        if name not in providers:
            continue
        parser = providers[name]
        results = []
        for repeat in range(1 if name == "baseline" else args.repeats):
            for case in cases:
                started = time.perf_counter()
                error, actual = None, None
                parser.last_usage = None
                try:
                    actual = parser.parse(
                        InboundEmail(
                            gmail_id=case["id"],
                            sender="synthetic@example.com",
                            subject=case["subject"],
                            text=case["text"],
                        )
                    ).model_dump(mode="json")
                except Exception as exc:
                    error = type(exc).__name__
                expected = case["expected"]
                correct = actual is not None and all(
                    actual.get(k) == v for k, v in expected.items()
                )
                usage = parser.last_usage
                rate = prices.get(name, {})
                cost = None
                if (
                    usage
                    and rate.get("input_per_million_usd") is not None
                    and (rate.get("output_per_million_usd") is not None)
                ):
                    cost = (
                        usage["input_tokens"] * rate["input_per_million_usd"]
                        + usage["output_tokens"] * rate["output_per_million_usd"]
                    ) / 1_000_000
                row = {
                    "provider": name,
                    "model": parser.model,
                    "case": case["id"],
                    "repeat": repeat + 1,
                    "expected": expected,
                    "actual": actual,
                    "correct": correct,
                    "unsafe_accept": bool(
                        actual and actual["status"] == "ready" and expected["status"] != "ready"
                    ),
                    "error": error,
                    "seconds": time.perf_counter() - started,
                    "usage": usage,
                    "estimated_cost_usd": cost,
                }
                rows.append(row)
                results.append(row)
        costs = [r["estimated_cost_usd"] for r in results if r["estimated_cost_usd"] is not None]
        summary.append(
            {
                "provider": name,
                "model": parser.model,
                "trials": len(results),
                "accuracy": sum(r["correct"] for r in results) / len(results),
                "unsafe_accepts": sum(r["unsafe_accept"] for r in results),
                "errors": sum(r["error"] is not None for r in results),
                "median_seconds": statistics.median(r["seconds"] for r in results),
                "estimated_cost_usd": sum(costs) if len(costs) == len(results) else None,
            }
        )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "cases": len(cases),
        "summary": summary,
        "skipped": skipped,
        "pricing": prices,
        "trials": rows,
        "limitations": "Small synthetic extraction evaluation, not a general provider ranking. "
        "Timing includes network calls and retries. Cost is an estimate from "
        "supplied rates; no price means unknown, not free. Recipient and website "
        "restrictions are enforced separately in application code.",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"summary": summary, "skipped": skipped}, indent=2))


if __name__ == "__main__":
    main()
