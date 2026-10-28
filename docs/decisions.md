# Design decisions

These are implementation choices, not claims that every alternative was benchmarked.

| Decision | Alternatives and rationale | Revisit when |
|---|---|---|
| Python | TypeScript and mixed-language components are viable. Python supports the required browser, Google, ZIP and model integrations. Language consistency is secondary to capability. | A demonstrated limitation makes another tool materially better. |
| Playwright and Chromium | Selenium and Puppeteer can automate this site. Playwright offers Python support and explicit download events. Plain HTTP does not reproduce the interactive WebDirect workflow. Model-directed navigation adds variability to a fixed task. | A supported API becomes available or deployed browser checks fail. |
| WebDirect | The official FileMaker Data API would be preferable with supported access. Its availability, database permissions (`fmrest`), and attachment access are unconfirmed. A read-only `productInfo` check on October 27 returned HTTP 502, which does not establish permanent unavailability. | The board publishes or grants suitable API access. |
| Pub/Sub streaming | Polling and IMAP IDLE are simpler viable choices. Streaming delivery provides buffering/redelivery without a public webhook endpoint. Gmail history synchronization still supplies the actual messages. | Provisioning or operating the integration proves disproportionate. |
| SQLite | Memory loses progress; JSON needs coordinated writes and duplicate handling. SQLite provides local transactions and uniqueness without another server. It does not make external email delivery atomic. | Multiple worker hosts or unsuitable persistent storage. |
| Bounded model use | AI interprets natural-language requests; Python validates them and owns recipients, retrieval and facts. General autonomous browser planning is unnecessary here. | A concrete task requires broader reasoning. |
| Gemini through Vertex AI | Keeps the deployed worker's model access and billing in GCP, using its attached identity instead of another API key. OpenAI remains an optional adapter, and Gemini Developer API is an optional evaluation path. This is an operational choice; extraction quality still requires measurement. | Measured quality, latency, cost or access makes another provider preferable. |
| Agent framework | Google's ADK supports tools, workflows, sessions and evaluation, and is a viable alternative. The current workflow needs one structured extraction followed by fixed steps; an ADK migration has no demonstrated benefit for those requirements. Managed agent hosting is a separate choice and would require validating Chromium, mailbox intake and persistence. | Standardized agent tools, conversational sessions or managed agent operations solve a concrete need. |
| Small deployment | Python and GCP Pub/Sub already overlap with the JD. React, Kubernetes, PostgreSQL and ClickHouse have no current requirement. Temporal could replace custom orchestration but introduces another service for this short workflow. | UI, distributed execution, long-running workflows or analytics become actual requirements. |
| GCP hosting | Compute Engine is the intended host: one persistent Linux VM matches the streaming subscriber, Chromium process and local SQLite state. A request-driven serverless deployment would require adapting worker lifecycle and persistence. The school server remains useful for development and verification. | Deployment requirements justify managed distributed workers or shared state. |

## Evidence and limitations

- Planning exploration reached M12205 and downloaded source document 102674 as a valid PDF.
- Its category counts differed from the supplied screenshots: live counts must be read.
- Source document 102674 occurred under both Key Documents and Other Documents.
- Standalone headless browser and hosted email behavior must be verified during implementation.
- The model comparison will report measured results; no provider winner is assumed.
- Live implementation found that virtual rows are recycled out of DOM order and
  switching file filters can invalidate cached rows. Inventory traversal sorts by
  rendered position and rechecks the top if the displayed count is not covered.
  Selection uses newest filing date and source-ID tie-breaking for repeatability.
- Exhibits use identifiers such as `H-1`, including letter and parenthesis suffixes.
  The public inventory report and download filename confirm those identifiers.
  Counts deduplicate listed source identifiers, not identical file contents.
- The Vaadin grid wrapper has zero dimensions; download lookup uses its scroller
  viewport, falling back to the page boundary for short lists with no active scrollbar.
  A second matter also exposed a valid exhibit label with a space (`A -5`).
  Downloaded filenames are checked against known source IDs.

## References

- https://playwright.dev/python/docs/downloads
- https://help.claris.com/en/data-api-guide/content/enable-access.html
- https://developers.google.com/workspace/gmail/api/guides/push
- https://docs.cloud.google.com/pubsub/docs/pull
- https://www.sqlite.org/whentouse.html
- https://adk.dev/
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale
