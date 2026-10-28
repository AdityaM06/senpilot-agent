# Architecture

```text
Gmail -> Pub/Sub -> intake -> SQLite jobs -> worker -> request parser
                                          |        -> board website
                                          |        -> ZIP + manifest
                                          +---------> Gmail reply
```

## Responsibilities

| Module | Responsibility |
|---|---|
| `models` | Validated request, source inventory, download result and job records |
| `config` | Environment configuration; secrets are not logged |
| `parser` | One bounded structured extraction call; rejects invented matter IDs |
| `uarb` | Browser navigation, complete inventories, source-ID deduplication and downloads |
| `delivery` | File manifest, ZIP budget and plain-text MIME reply construction |
| `gmail` | Dedicated-mailbox OAuth, history, message reading and sending |
| `store` | Transactions, incoming message uniqueness and persistent job state |
| `intake` | Streaming notifications, synchronization and watch maintenance |
| `worker` | Request orchestration, cleanup and uncertain-delivery recovery |
| `__main__` | Commands, process locking, signals and component wiring |

## Concurrency

The intake thread owns its Gmail API client and serializes synchronization. Pub/Sub
callbacks enqueue unacknowledged notifications; the intake thread acknowledges them
after jobs and a history checkpoint are committed. Library flow control bounds the
number outstanding. Browser work runs sequentially on the worker thread with a
separate Gmail client. SQLite opens one connection per operation.

The browser adapter presents a synchronous method to the worker and uses an event
loop internally so download completion and the complete retrieval can be bounded.
It reuses the browser process and isolates requests in fresh browser contexts.

## Recovery

`jobs.gmail_id` is unique. Duplicate notifications and overlapping history pages
cannot create a second job for the same incoming message. A second email for the
same matter is a distinct request.

Job creation and Gmail checkpoint advancement happen in one transaction. Initial
synchronization captures a watch baseline before scanning the Inbox. Expired history
is recovered by capturing a new baseline before a rescan from activation time.
Renewing a watch never advances the processed checkpoint.

```text
queued -> running -> sending -> replied
           |           +-----> needs_review
           +-----------------> ignored / failed
```

An interrupted `running` job can be restarted within its retry allowance. Retrieval
starts afresh. A `sending` job is reconciled against Sent mail using its saved RFC
message ID. An absent or uncertain result is flagged for inspection; external email
sending is not part of a SQLite transaction and exactly-once delivery is not claimed.

## Data boundaries

The model receives the latest email's subject and text, not credentials or browser
tools. Source counts and metadata are extracted from the board site and formatted
without a model-generated factual summary. Missing source IDs make the unique total
unverified. ZIP manifests distinguish successful files, failures and size omissions.

SQLite stores a mailbox checkpoint and job progress. Downloads are temporary for
email jobs. Local `fetch` output is retained for inspection. Runtime state is private
and excluded from the repository.
