# Verification record

Checked on October 27, 2026. This records observed behavior, including checks that
remain outstanding. No Git commits existed when these checks were performed.

## Automated checks

- Local Python 3.12: 37 unit/integration tests passed after adding the Vertex AI
  parser; three opt-in live cases skipped. The new checks cover malformed output,
  invented identifiers, Cloud identity configuration, and real SDK serialization
  against a simulated HTTP transport. These simulated checks do not establish
  real model access.
- Ruff checks passed for source, tests and evaluation scripts.
- Linux `cs136`: the earlier 29-test suite passed, with three opt-in live cases skipped.
- GCP Ubuntu 24.04 VM: all 37 routine tests passed, plus Ruff, dependency checks,
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

## Outstanding before submission

- Verify a fresh email-to-ZIP exchange and recipient-side receipt.
- Check nonempty Transcript and Recording tabs when suitable test matters are available.
- Reauthorize the Testing mailbox on October 30 for the evaluation window.
  Validate subsequent watch renewal in the deployed environment. The school host
  remains a development environment; its global service configuration was not changed.
- Run the complete mailbox-to-ZIP demo and verify the shareable video and repository links.

No submission email, GitHub publication, or commit was sent
or created as part of these checks.
