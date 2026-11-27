# Staging delivery on Kapsule

The `kapsule-staging.yml` workflow validates Helm and builds images on hosted
GitHub runners without secrets for pull requests. When enabled, it publishes to
`rg.fr-par.scw.cloud/assistant-rh-staging` and deploys immutable image digests.
It targets the new staging cluster only.

## Proposed activation (not applied)

Pushes to `staging` will deploy automatically once
`KAPSULE_STAGING_CD_ENABLED=true`. Until the approved activation, keep it `false`.
The gate requires successful backend, frontend/E2E, security and Helm workflows
for that exact push SHA. Publishing builds and scans both images before pushing;
deployment uses the published digests and rejects obsolete staging revisions.
No per-delivery GitHub approval is requested after initial activation.

The existing `kapsule-staging` environment is restricted to `staging` and already
contains the dedicated registry secret. Kubernetes application, registry and CA
secrets have been provisioned. Never pass their values through Helm or logs.

Automatic deployment uses Flux inside the existing application namespace.
GitHub only publishes scanned images and an OCI Helm archive to the dedicated
Scaleway registry; it has no Kubernetes access. No private GitHub runner, GitHub
App or new Scaleway IAM permission is needed. The proposed source-controller and
helm-controller watch only `conversations`, with namespace Roles. Helm operations
impersonate the existing `github-deploy` service account.

`package-staging.py` seals Helm defaults, staging overrides and immutable image
digests into version `0.<run_number>.<run_attempt>`, with the source SHA recorded
as `appVersion`. Publishing this chart is the final CI action. The bundle also
contains a read-only Helm test for backend/frontend health and public config.
The registry already contains the required credentials; no secret enters the chart.

Flux manifests are prepared under
`assistant-rh-infra/deliveries/staging-cicd-activation-2026-10-14/flux`, not applied.
Both OCIRepository and HelmRelease start suspended. The existing Helm release
name, target and storage namespace are retained. The release waits for jobs and
workload readiness; subsequent test or upgrade failures use Helm rollback remediation. The first
adoption needs a recorded previous Helm revision and an operator rollback
fallback because Flux does not yet have its own remediation history. The migration
hook remains `migrate --check --no-input`; pending schema changes block delivery
until a separately approved migration is performed.

A green GitHub run proves publication; deployment is confirmed through Flux
`Ready=True`, the expected OCI revision and successful Helm tests. To stop delivery,
disable the CI variable and suspend the HelmRelease, including already published
charts. Suspend Flux before an operator Helm rollback; publish a corrective chart
before resuming. The temporary smoke users are not re-enabled automatically.

Manual dispatch requires the workflow on the default branch. Initial activation
uses a `staging` push, without merging into `main` or triggering inherited Docker
Hub, chart or translation publishing. The proposed staging branch is created from
main, then receives the reviewed delivery PR; subsequent changes target staging.

## Application configuration

Both manual and automatic delivery target `staging`. The branch does not exist
yet; create it from main and integrate the reviewed delivery PR before activation. The Assistant RH
individual identity changes have been merged into main and integrated here.

The namespace and release are `conversations`. `deploy/kapsule/staging.yaml`
disables public, admin and media Ingress until the domain and certificate are
chosen. Before public access, configure Django hosts, CSRF and OIDC settings,
route a domain or the public IP `51.159.113.113`, provision the matching TLS certificate and review the new values.
The frontend uses the same origin for its API (`API_ORIGIN` is empty).

Prerequisites in this namespace:

- Secret `conversations-runtime`: `DB_PASSWORD` for `conversations_app`,
  `DJANGO_SECRET_KEY` and the Redis URLs below. Staging uses `Production` settings
  with the opt-in Assistant RH password login; OIDC is not required in that mode.
  `DJANGO_ALLOWED_HOSTS` is supplied by the staging values. Dedicated S3 credentials
  remain necessary before document features can be enabled.
- Secret `conversations-identity`: `signing.pem` (Ed25519) and a separate
  `audit.key`, mounted read-only. The API holds only the public verification key.
  Admission and ministry permissions must be granted to individual users.
- `REDIS_URL` and `CELERY_BROKER_URL`: authenticated `rediss://` URLs pointing to
  `10.10.4.3:6379`, with URL-encoded passwords and certificate verification
  (`ssl_cert_reqs=required&ssl_ca_certs=/etc/scaleway-ca/redis.pem`). Set
  `CELERY_RESULT_BACKEND` if required by the delivered version. Verify each
  client's SSL options during the first runtime test.
- Secret `scaleway-database-ca`: `postgres.pem` and `redis.pem` certificates from
  the Scaleway services. PostgreSQL uses `PGSSLMODE=verify-ca` and
  `PGSSLROOTCERT`; do not disable TLS verification.
- Existing ConfigMap `conversations-llm-configuration`, key `config.json`, using
  the format in `docs/llm-configuration.md`. Assistant RH models use
  `http://assistant-rh-api.assistant-rh-api.svc.cluster.local:8000/v1` and the
  delivered identity contract. Private signing keys belong in Secrets mounted
  with `backend.extraVolumes` and `extraVolumeMounts`.

The migration Job is a pre-install/pre-upgrade hook with a 300-second deadline.
Its secrets, certificates and LLM ConfigMap must exist before Helm runs.
The new staging database was initialized with all Django migrations on 2026-10-14
using the scanned production image and verified PostgreSQL TLS. The hook only checks for pending migrations; applying them requires a separate approved operation. No existing user or conversation was copied.
Automatic superuser creation is disabled; provision the first administrator
separately. Chart defaults preserve existing behavior for other deployments.

## Verification

Each pull request runs Helm validation and Docker builds without publishing.
Before publication, Trivy rejects every HIGH/CRITICAL vulnerability in each image.
The published digest identifies the image that passed this scan.
Before activation: verify private PostgreSQL connectivity and certificates,
initialize the schema, verify users and grants, provision secrets, validate Flux permissions and complete the first controlled adoption. A heartbeat alone does not prove
a real conversation: also test authentication, `/v1/models`, a streamed exchange
and individual access controls from Conversations.

The backend security upgrade passes all 1419 tests against disposable local
PostgreSQL and S3 services. Tests cover permission revocation before delegation
and block unexpected external requests through both HTTPX and HTTPX2. These tests
do not replace the authenticated staging conversation checks above.

The inherited Helm chart publication workflow is restricted to `main`.
Its separate `gh-pages` destination must be provisioned before publishing charts;
Kapsule deployment uses the chart from the repository and does not require it.

The canonical staging values include the approved runtime corrections:
`LOGIN_REDIRECT_URL=/`, `LOGOUT_REDIRECT_URL=/`, one worker with concurrency 1,
Celery Beat disabled, document/presentation/web/data.gouv features disabled and
schema checking without migration. A real authenticated HTTP recipe passed on
2026-10-14 (SSE, persisted messages, individual isolation, logout); its temporary
accounts are now disabled. That result does not validate the future Flux controllers
or the next image revision. No public application route is currently active.
