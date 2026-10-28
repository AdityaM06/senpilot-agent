# Verification record

Checked on October 27, 2026. This records observed behavior, including checks that
remain outstanding. No Git commits existed when these checks were performed.

## Automated checks

- Local Python 3.12: 43 unit/integration tests passed after adding the Vertex AI
  parser; three opt-in live cases skipped. The new checks cover malformed output,
  invented identifiers, Cloud identity configuration, and real SDK serialization
  against a simulated HTTP transport. An OAuth logging regression check also
  verifies callback codes do not enter INFO logs. These simulated checks do not
  establish real model access.
- Ruff checks passed for source, tests and evaluation scripts.
- Linux `cs136`: the earlier 29-test suite passed, with three opt-in live cases skipped.
- GCP Ubuntu 24.04 VM: all 43 routine tests passed, plus Ruff, dependency checks,
  and systemd service-template validation.
- Four state-store tests passed with their temporary state placed inside the
  project's `var/verification/state-tests` directory on the server's Ceph storage.
  This checks basic persistence/transactions, not a guarantee against storage outages.
- Outbound TLS/HTTP2 connectivity succeeded to Pub/Sub, Gmail, OAuth and OpenAI API
  endpoints without credentials. This does not establish authenticated API access.

## Public website checks on Linux

Environment: Python 3.12.3, Playwright 1.64.1, matching Chromium build.

- Full local-workflow retrieval for M12205 / Other Documents succeeded with ten PDFs
  and a ZIP. Counts were 13 Exhibits, 6 Key Documents, 43 Other Documents, no
  Transcripts, and no Recordings. Deduplication produced 56 listed source IDs from
  62 category entries. Source IDs are distinct from file-content hashes.
- The source filename was verified for every attached numbered document.
- Opt-in live tests passed for M12205 / Exhibits, M12383 / Key Documents, and
  M12205 / Transcripts. The last test exercised a zero-document result.
- The exhibit case recovered from a transient timeout using its bounded retry.
- Metadata was read from each matter; no title, date or document count is hardcoded.

The retained review ZIP is 5,570,862 bytes and contains ten source PDFs plus its
manifest. All ten SHA-256 values were checked against the archived bytes. An
independent PDF parser opened all ten files and counted 98 pages in total. The
local copy and reply preview are under `var/verification/review-result/`.

The final email-size regression check verifies that a fitting subset survives MIME
encoding, rather than relying only on the ZIP's raw size. Reply headers also reject
malformed reference IDs and normalize line breaks in incoming subjects.

The second matter exposed a legitimate exhibit ID containing a space (`A -5`) and
a short-list viewport with no active scrollbar. Those cases were fixed before the
live checks passed. Earlier incomplete results were explicitly marked unverified.

## Extraction evaluation

The regex baseline ran against all 24 frozen synthetic requests and matched 22
expected results, with zero unsafe acceptances on this small dataset. This is
91.7% exact correctness, not evidence that regex is generally sufficient.

Gemini 3.8 Flash was measured through Vertex AI using the VM's attached service
account on the same 24 synthetic requests, with one trial per request. It matched
23 expected results (95.8%), with zero API errors and zero unsafe acceptances on
this small dataset. Median call duration was 2.32 seconds. Case `i04` returned
`clarify` rather than the expected `unsupported`; it did not execute the request.
The baseline matched 22/24 in the same run. OpenAI and Claude remain unmeasured.
One small run does not establish a general provider ranking or production error
rate. Costs are unknown because checked rates were not supplied. The report is
retained privately at `var/deployment/gemini-eval.json` locally and
`var/verification/gemini-eval/report.json` on the VM.

## Gmail authorization

- The dedicated mailbox completed local OAuth authorization. Its identity matched
  the configured agent address, a refresh token was saved with private permissions,
  and an explicit token refresh plus profile check succeeded.
- The transferred token also refreshed on the GCP VM and matched the agent mailbox.
- The operator confirmed OAuth publishing status is Testing. The operational plan
  is to reauthorize on October 30 so its seven-day window covers evaluation through
  November 4. Publishing and brand verification are not prerequisites for this
  plan; no claim of indefinite token validity is made.

## GCP deployment

- The Ubuntu 24.04 x86 VM has Python 3.12.3 and uses its attached worker service
  account with the cloud-platform access scope. No service-account key was copied.
- The VM identity passed a permission check for consuming the configured Pub/Sub
  subscription. A real Vertex structured-extraction smoke request succeeded.
- Chromium retrieved a public PDF for M12205 / Other Documents and packaged it
  with a manifest on the VM. It found the same complete source-ID inventories
  and unique count of 56 as the earlier school-host checks.
- The worker is installed as a user systemd service, with user lingering enabled.
  Initial startup was healthy with zero restarts, and Gmail watch registration
  and history synchronization completed. A real VM reboot completed and the
  service started automatically afterward, active with zero restarts. Mailbox
  synchronization resumed against the persisted checkpoint after boot.

## Live email-to-ZIP check

- A fresh request from a separate Outlook mailbox was picked up by the running
  service and interpreted through Vertex AI. The persisted job completed on its
  first attempt without a processing error. Gmail accepted the reply 65 seconds
  after accepting the incoming email.
- The actual Sent copy was retrieved for inspection. Its sole recipient matched
  the original sender. The attached ZIP was 5,570,862 bytes and contained ten PDFs
  plus `manifest.json`. ZIP CRC, all ten PDF signatures, file sizes, and SHA-256
  values passed. No download failures, budget omissions, or inventory errors were
  reported. Counts were 13 Exhibits, 6 Key Documents, 43 Other Documents, and zero
  Transcripts/Recordings, with 56 unique source identifiers.
- The live request exposed leading whitespace in Outlook's Message-ID. It caused
  the first reply's RFC reference headers to be omitted, although Gmail kept the
  reply in the original thread. Input normalization and reply reference validation
  now trim outer whitespace while still rejecting malformed IDs.
- Gmail also replaced our supplied outgoing RFC Message-ID. Recovery now has a
  second lookup using a unique `X-Senpilot-Delivery-ID` header in the original
  request's thread, requiring the SENT label. Three regression cases verify that
  incoming or unrelated markers do not count as successful delivery.
- A single automated self-email verified that Gmail preserves the delivery marker
  and normalized reference header. Its RFC Message-ID was rewritten, and the live
  reconciliation lookup still found the Sent message. No new documents were
  requested by this automated check.
- Private local review artifacts are under `var/verification/email-test/` and
  include the actual reply ZIP, reply text, and a sanitized verification report.
- The requester confirmed receipt in the originating mailbox and successfully
  opened the ZIP attachment, completing the recipient-side check.

## Outstanding before submission

- Check nonempty Transcript and Recording tabs when suitable test matters are available.
- Reauthorize the Testing mailbox on October 30 for the evaluation window.
  Validate subsequent watch renewal in the deployed environment. The school host
  remains a development environment; its global service configuration was not changed.
- Run the complete mailbox-to-ZIP demo and verify the shareable video and repository links.

No submission email, GitHub publication, or commit was sent
or created as part of these checks.
