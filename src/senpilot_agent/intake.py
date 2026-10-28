from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import UTC, datetime

from .config import Settings
from .gmail import GmailClient, HistoryExpired
from .store import StateStore

log = logging.getLogger(__name__)


class InboxSynchronizer:
    def __init__(self, gmail: GmailClient, store: StateStore, email: str, topic: str):
        self.gmail = gmail
        self.store = store
        self.email = email
        self.topic = topic

    def start(self) -> None:
        profile = self.gmail.profile()
        if profile["emailAddress"].casefold() != self.email.casefold():
            raise ValueError("Gmail authorization does not match AGENT_EMAIL")
        self.store.initialize_mailbox(self.email)
        watch = self.renew_watch()
        state = self.store.mailbox()
        if state["history_id"] is None:
            # Capture the baseline before scanning: arrivals during the scan are replayed later.
            ids = self.gmail.list_inbox_since(datetime.fromisoformat(state["activated_at"]))
            self.store.commit_discovery(ids, watch["historyId"], None)
        self.sync()

    def renew_watch(self) -> dict:
        result = self.gmail.watch_inbox(self.topic)
        self.store.save_watch_expiration(
            datetime.fromtimestamp(int(result["expiration"]) / 1000, UTC)
        )
        return result

    def sync(self) -> None:
        state = self.store.mailbox()
        checkpoint = state["history_id"]
        if checkpoint is None:
            raise ValueError("Mailbox watch is not initialized")
        try:
            changes = self.gmail.list_changes(checkpoint)
            ids, new_checkpoint = changes.message_ids, changes.history_id
        except HistoryExpired:
            new_checkpoint = self.gmail.profile()["historyId"]
            ids = self.gmail.list_inbox_since(datetime.fromisoformat(state["activated_at"]))
        self.store.commit_discovery(ids, new_checkpoint, checkpoint)
        if ids:
            log.info("Mailbox sync discovered %d message references", len(ids))


class GmailPushListener:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store

    def handle_notification(self, synchronizer: InboxSynchronizer, message) -> bool:
        try:
            payload = json.loads(message.data)
            valid = (
                isinstance(payload, dict)
                and isinstance(payload.get("emailAddress"), str)
                and payload["emailAddress"].casefold() == self.settings.agent_email.casefold()
                and str(payload.get("historyId", "")).isdigit()
            )
        except (ValueError, TypeError, UnicodeDecodeError):
            valid = False
        if not valid:
            log.warning("Discarding a malformed or unexpected mailbox notification")
            message.ack()
            return True
        try:
            synchronizer.sync()
        except Exception:
            log.exception("Mailbox synchronization failed; notification will be redelivered")
            message.nack()
            return False
        else:
            message.ack()
            return True

    def run(self, stop: threading.Event) -> None:
        from google.cloud import pubsub_v1

        # Only this thread uses the intake Gmail client. Browser work uses another client.
        synchronizer = InboxSynchronizer(
            GmailClient(self.settings.oauth_token_file),
            self.store,
            self.settings.agent_email,
            self.settings.pubsub_topic,
        )
        synchronizer.start()
        pending = queue.Queue()
        subscriber = pubsub_v1.SubscriberClient()
        future = subscriber.subscribe(
            self.settings.pubsub_subscription,
            callback=pending.put,
            flow_control=pubsub_v1.types.FlowControl(max_messages=10),
        )
        last_sync = last_watch = time.monotonic()
        try:
            while not stop.is_set():
                if future.done():
                    future.result()
                    raise RuntimeError("The Pub/Sub stream stopped unexpectedly")
                try:
                    message = pending.get(timeout=1)
                except queue.Empty:
                    message = None
                if message is not None:
                    if not self.handle_notification(synchronizer, message):
                        stop.wait(5)
                now = time.monotonic()
                try:
                    if now - last_watch >= 86400:
                        synchronizer.renew_watch()
                        last_watch = now
                    if now - last_sync >= self.settings.reconciliation_seconds:
                        synchronizer.sync()
                        last_sync = now
                except Exception:
                    log.exception("Scheduled mailbox maintenance failed")
                    stop.wait(10)
        finally:
            future.cancel()
            try:
                future.result(timeout=10)
            except Exception:
                log.debug("Pub/Sub stopped")
            subscriber.close()
