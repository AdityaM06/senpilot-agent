from __future__ import annotations

import argparse
import fcntl
import logging
import signal
import sys
import threading
from pathlib import Path

from .config import Settings
from .models import DocumentRequest, DocumentType, utc_now


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve and email public regulatory filings")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Retrieve locally without email or model credentials")
    fetch.add_argument("matter")
    fetch.add_argument("--type", required=True, type=DocumentType.parse)
    fetch.add_argument("--output", type=Path)
    fetch.add_argument("--limit", type=int, choices=range(0, 11), default=10)
    fetch.add_argument("--browser-channel", help="Use an installed browser, e.g. chrome")
    fetch.add_argument("--headed", action="store_true")
    commands.add_parser("auth", help="Authorize the dedicated Gmail account")
    commands.add_parser("run", help="Start Gmail intake and document processing")
    commands.add_parser("status", help="Inspect persisted job state")
    return parser.parse_args()


def run_worker(settings: Settings) -> None:
    from .gmail import GmailClient
    from .intake import GmailPushListener
    from .parser import create_request_parser
    from .store import StateStore
    from .uarb import UarbClient
    from .worker import JobWorker

    settings.require_worker()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with (settings.data_dir / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another worker is already using this data directory") from exc
        store = StateStore(settings.data_dir / "state.sqlite3")
        store.initialize_mailbox(settings.agent_email)
        gmail = GmailClient(settings.oauth_token_file)
        if gmail.profile()["emailAddress"].casefold() != settings.agent_email.casefold():
            raise ValueError("Gmail authorization does not match AGENT_EMAIL")
        stop = threading.Event()
        errors = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())

        def intake():
            try:
                GmailPushListener(settings, store).run(stop)
            except Exception as exc:
                errors.append(type(exc).__name__)
                logging.exception("Email intake stopped")
                stop.set()

        listener = threading.Thread(target=intake, name="gmail-intake", daemon=True)
        listener.start()
        board = UarbClient(
            headless=settings.headless,
            browser_channel=settings.browser_channel,
            diagnostic_dir=settings.data_dir / "diagnostics",
        )
        try:
            worker = JobWorker(
                settings,
                store,
                gmail,
                create_request_parser(settings),
                board,
            )
            worker.run_forever(stop)
        finally:
            stop.set()
            board.close()
            listener.join(timeout=45)
        if errors:
            raise RuntimeError(f"Email intake stopped: {errors[0]}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = arguments()
    settings = Settings.load()
    try:
        if args.command == "fetch":
            from .delivery import build_package, result_body
            from .uarb import UarbClient

            request = DocumentRequest(matter_number=args.matter, document_type=args.type)
            output = args.output or settings.data_dir / "results" / (
                request.matter_number + "-" + utc_now().strftime("%Y%m%dT%H%M%SZ")
            )
            board = UarbClient(
                headless=False if args.headed else settings.headless,
                browser_channel=args.browser_channel or settings.browser_channel,
                max_documents=args.limit,
                diagnostic_dir=settings.data_dir / "diagnostics",
            )
            try:
                result = board.fetch(request, output / "downloads")
                package = build_package(result, output, settings.email_attachment_budget)
                (output / "result.json").write_text(result.model_dump_json(indent=2))
                (output / "reply.txt").write_text(result_body(result, package))
                print(result_body(result, package))
                print(f"\nZIP: {package.zip_path.resolve()}")
                return 2 if result.failures or result.snapshot.inventory_errors else 0
            finally:
                board.close()
        if args.command == "auth":
            from .gmail import authorize

            authorize(settings.oauth_client_file, settings.oauth_token_file, settings.agent_email)
            print("Gmail authorization saved locally. Keep the token file private.")
        elif args.command == "status":
            from .store import StateStore

            print(StateStore(settings.data_dir / "state.sqlite3").status())
        elif args.command == "run":
            run_worker(settings)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
