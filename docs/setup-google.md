# Google setup and deployment

## Accounts

Use your personal Google account to administer the Cloud project, and a dedicated
Gmail address as the agent mailbox. These have different jobs:

- OAuth authorizes the Gmail mailbox for reading and sending.
- A Cloud principal authorizes the worker to consume the Pub/Sub subscription.

A service-account JSON key alone does not authorize a personal Gmail mailbox.
Reviewers only send email; they do not log into or authorize this application.

## Cloud project and Pub/Sub

Create a project and enable the Gmail, Cloud Pub/Sub, and Vertex AI APIs. Check the project's
billing prerequisites, available Google Cloud credits, and costs before enabling
resources.

In Cloud Shell, substitute your actual project ID:

```sh
gcloud config set project YOUR_PROJECT
gcloud services enable gmail.googleapis.com pubsub.googleapis.com aiplatform.googleapis.com
gcloud pubsub topics create gmail-events
gcloud pubsub topics add-iam-policy-binding gmail-events \
  --member='serviceAccount:gmail-api-push@system.gserviceaccount.com' \
  --role='roles/pubsub.publisher'
gcloud pubsub subscriptions create gmail-events-worker \
  --topic=gmail-events --ack-deadline=60 --message-retention-duration=7d
```

Authorize a worker principal to use that subscription. For a dedicated service
account, grant `roles/pubsub.subscriber` on this subscription rather than project
Owner. On an external Linux host, configure Application Default Credentials or place
the separately provisioned principal's credential file in the private `credentials/`
directory and set `GOOGLE_APPLICATION_CREDENTIALS` to it. Never commit or paste keys
into the README or chat.

Set `PUBSUB_TOPIC` and `PUBSUB_SUBSCRIPTION` in `.env` to their complete resource names.
The watch topic must belong to the same Cloud project as the Gmail OAuth client.

## Gemini through Vertex AI

The default request parser uses `google-genai` with Vertex AI and Application
Default Credentials (ADC). Set `MODEL_PROVIDER=vertex`, `GOOGLE_CLOUD_PROJECT` to
your project ID, `GOOGLE_CLOUD_LOCATION=global`, and
`GEMINI_MODEL=gemini-3.8-flash`. The model supports structured output; low thinking
effort and bounded output are used for this small extraction task.

Grant the worker's service account `roles/aiplatform.user` on the project as well
as its subscription-level Pub/Sub role. On Compute Engine, attach that identity
with the `cloud-platform` access scope and let ADC use it. No model API key or
downloaded service-account key is needed. This permission does not authorize the
Gmail mailbox; its OAuth token remains separate.

For local development with the Google Cloud CLI installed, use
`gcloud auth application-default login`, then
`gcloud auth application-default set-quota-project YOUR_PROJECT`. Authorize the
project administrator for this Cloud identity, independently of mailbox OAuth.
Cloud Shell's existing identity can also run a synthetic model smoke test.

Check actual model access and quota before deployment. Run the frozen evaluation
with `--providers baseline gemini --repeats 1` before claiming extraction quality.
The Gemini evaluation uses the same parser through Vertex when
`GOOGLE_CLOUD_PROJECT` is configured and no `GEMINI_API_KEY` is supplied. A Gemini
Developer API key is an optional evaluation path with separate access and billing.

References:
- https://googleapis.github.io/python-genai/
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash

## Gmail OAuth

1. Configure Google Auth Platform for the project. A personal Gmail mailbox uses an
   External audience. During testing, add the dedicated mailbox as a test user.
2. Configure `gmail.readonly` and `gmail.send` permissions.
3. Create an OAuth client of type **Desktop app** and download its JSON file into
   `credentials/oauth-client.json`.
4. Set `AGENT_EMAIL` and run the local `auth` command. Sign in as the dedicated
   mailbox, not the Cloud project administrator.
5. Keep `credentials/gmail-token.json` private. The application verifies that the
   authorized mailbox matches `AGENT_EMAIL` before saving it.

External Testing refresh tokens can expire after seven days. Keeping the app in
Testing is sufficient for this challenge if the operator reauthorizes before
expiration. For the October 28 submission and evaluation through November 4,
repeat authorization on October 30, securely replace the VM token, and restart the
worker after confirming it is idle. Verify a real request afterward. Watch renewal
does not extend the OAuth refresh token's lifetime.

Publishing is an alternative to this manual renewal plan, not a prerequisite for
the demo. Personal-use verification exemptions and publishing status are separate
from brand verification. Do not start branding or verification work solely for
this single-mailbox challenge. If publishing status changes, authorize again and
verify token refresh; other revocation and expiration conditions still apply.

References:
- https://developers.google.com/workspace/gmail/api/guides/push
- https://developers.google.com/identity/protocols/oauth2/
- https://support.google.com/cloud/answer/13464323?hl=en

## Deployment

The intended unattended host is a Google Cloud Compute Engine VM running a
supported x86 Linux distribution, with persistent disk storage for SQLite and
artifacts. A VM fits the existing long-running Pub/Sub subscriber and Chromium
worker without changing the execution model. See the verification record for the
actual deployment status.

Attach a dedicated service account to the VM and grant it subscription-level
`roles/pubsub.subscriber` and project-level `roles/aiplatform.user`. Use Application Default Credentials from the attached
identity rather than downloading a service-account key. Gmail still requires the
dedicated mailbox's separate OAuth token. Enable the Compute Engine API when
provisioning the host, and review its billing estimate before creating it.

Work on `cs136` stays inside `~/Desktop/senpilot-agent`. Its browser and Python
dependencies are installed in that folder. The initial preflight found Python 3.12,
Chromium libraries, and board-site connectivity. Its Desktop is on Ceph network
storage and user-service lingering is disabled. It remains a development and
verification host. No global service configuration has been changed.

Install the application and matching browser, copy only the intended private
credential files, configure `.env`, and test a local `fetch` before starting email.
On an Ubuntu 24.04 VM, `bash ops/bootstrap-vm.sh` installs the locked Python packages
and matching browser under the project, using sudo for system dependencies. It
does not start the worker or send email. Keep the deployment SSH private key on the
local computer; install only its public key on the VM. Never transfer the local
virtual environment or the entire `credentials/` directory. Transfer the mailbox
token and reviewed `.env` separately over SSH with private permissions.
Use `chmod 600 .env credentials/*.json`. Set `PLAYWRIGHT_BROWSERS_PATH` consistently
for browser installation and service execution if using a project-local browser.

`ops/worker.service` is a user-service template. Review its paths before installing
it. A user service must survive logout and restart; having `systemctl --user`
available does not establish this. The template is not installed automatically.
After the VM checks pass, `bash ops/install-service.sh` installs and starts the
service from `~/Desktop/senpilot-agent`, enabling user lingering so it runs after
logout and at boot. This changes the deployment user's service configuration and
starts processing new incoming requests. Inspect it with
`systemctl --user status senpilot-worker` and
`journalctl --user -u senpilot-worker`. Stop it with
`systemctl --user stop senpilot-worker` before a manual worker run or maintenance.

Before submission, test a fresh external email, restart recovery, OAuth refresh,
watch renewal, and Gmail's Sent folder. Keep the worker running through November 4,
including the planned October 30 mailbox reauthorization when staying in Testing.

Hosting references:
- https://docs.cloud.google.com/compute/docs/instances/create-start-instance
- https://docs.cloud.google.com/compute/docs/access/service-accounts
