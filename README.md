# Regulatory document email agent

An email-driven worker that retrieves up to ten public files for a Nova Scotia
regulatory matter and replies with a ZIP, source metadata, counts, and a manifest.

The model interprets the request. A fixed workflow controls website access,
document validation, packaging, and the reply destination.

## Local setup

Use Python 3.12 or newer. Linux is the intended deployment environment.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m playwright install chromium
```

The lock includes development and evaluation dependencies. On your own Linux VM,
use `playwright install --with-deps chromium` if system libraries are missing.
The school-server setup uses a project-local browser path, as documented below.
An older macOS release may need an already-installed supported Chrome instead:
pass `--browser-channel chrome`. This does not install or downgrade your browser.

## Fetch files without email credentials

```sh
.venv/bin/python -m senpilot_agent fetch M12205 --type 'Other Documents'
```

Output is written beneath `var/results/`: the ZIP, downloaded source files,
`result.json`, and the proposed plain-text reply. `--limit 1` is useful for a smoke
test; `--limit 0` inspects inventories without downloading source documents.
The maximum is ten. `--output PATH` selects a result directory.
Files are selected by newest filing date, with the source identifier breaking ties.

The manifest records source IDs, category membership, collection time, file sizes,
checksums, and any omitted or failed files. Category counts may overlap. The unique
total is reported only when all source-ID inventories have been verified.

## Connect the mailbox

Follow [Google setup](docs/setup-google.md). Your personal account can manage the
GCP project while a separate Gmail account receives and sends agent email.
The default parser calls Gemini through Vertex AI using the worker's Google Cloud
identity. Configure `GOOGLE_CLOUD_PROJECT`; no model API key is needed for that path.
Mailbox OAuth is separate from the worker's Cloud identity.

```sh
cp .env.example .env
chmod 600 .env
# Fill .env and place the downloaded OAuth client file in credentials/.
.venv/bin/python -m senpilot_agent auth
.venv/bin/python -m senpilot_agent run
```

Send a fresh email such as:

> Please send Other Documents for M12205.

Missing or ambiguous inputs receive a clarification reply. Each email requests one
matter and one category. The sender receives the response; the model cannot change
the recipient or website.

Inspect job states with `.venv/bin/python -m senpilot_agent status`. A
`needs_review` job requires checking Sent mail before any manual retry. A timeout
does not prove that Gmail rejected the message.

## Checks

```sh
.venv/bin/pytest -q
.venv/bin/ruff check src tests evals
```

Routine tests use fake integrations and do not send emails or call models. Live
browser checks are explicitly enabled; see the test module and verification log.
Real Gmail, Pub/Sub, and model checks require separately configured accounts.

## Request-parser evaluation

```sh
.venv/bin/python -m pip install -e '.[eval]'
.venv/bin/python evals/run.py --providers baseline
.venv/bin/python evals/run.py --providers baseline openai claude gemini --repeats 3
```

Gemini can use the same Vertex AI configuration and Cloud identity as the worker.
Other evaluation providers use their respective API keys. Missing configuration is recorded as skipped;
API failures are recorded as errors. The frozen dataset has 24 synthetic requests.
Reports go to `var/evals/`. Optional checked prices can be supplied with `--prices`;
unknown costs stay unknown. A small extraction benchmark is not a general ranking
of model providers. The example pricing file deliberately contains no invented rates.

## Design and operation

- [Architecture and recovery](docs/architecture.md)
- [Alternatives and decisions](docs/decisions.md)
- [Google and deployment setup](docs/setup-google.md)
- [Verification evidence and outstanding checks](docs/verification.md)

Credentials, model keys, mailbox state, traces, and downloaded files are excluded
from Git. SQLite stores progress, not a searchable filing archive. Use persistent
storage and one worker process per state directory.

## Limitations

Website layout changes can break selectors. An incomplete inventory is disclosed
instead of being presented as a verified total. Files that do not fit the attachment
budget are omitted explicitly. Restricted files are not bypassed. Large recordings
and documents may hit download or email limits.

Pub/Sub delivers mailbox-change signals. Gmail history supplies the messages. Daily
watch renewal and a slower reconciliation check supplement event-driven processing.
OAuth authorization must remain valid throughout evaluation.

## Development assistance

Built with AI assistance for design discussion, implementation, and test development.
The repository records the chosen boundaries, verification evidence, and remaining
limitations. Commit messages are reviewed by the candidate before commits are created.
