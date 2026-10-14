# Staging delivery on Kapsule

The `kapsule-staging.yml` workflow validates Helm and builds images on hosted
GitHub runners without secrets for pull requests. When enabled, it publishes to
`rg.fr-par.scw.cloud/assistant-rh-staging` and deploys immutable image digests.
It targets the new staging cluster only.

## Activation

The separate GitHub environment `kapsule-staging` needs
`SCW_REGISTRY_SECRET_KEY`: a dedicated IAM application key with
`ContainerRegistryFullAccess` on the Assistant RH project. The operator currently
lacks IAM rights to create this identity. Do not copy the personal operator key.
Provision the Kubernetes `scw-registry` secret with registry pull credentials.
Application secrets must already exist in Kubernetes; do not pass them through
Helm values, logs or GitHub artifacts.

Publishing uses hosted GitHub runners. Deployment uses a dedicated private runner
with labels `self-hosted`, `linux`, `x64`, `conversations-staging-deploy`, restricted
to trusted repository workflows. **Never run pull request jobs on this runner.**
Install `git`, a `node` version compatible with checkout v5, `bash`, `kubectl`, and
Helm 3.19.0. Its kubeconfig must target exactly
`https://fce3ddcb-b9ff-49fb-8b7d-aca605272b0a.api.k8s.fr-par.scw.cloud:6443`,
with the cluster CA and an identity scoped to the namespace. Network egress must
use staging gateway `51.15.255.192`, already allowed by the Kubernetes API ACL.
No runner is registered yet. The namespace RBAC in `deploy/ci-rbac.yaml` was
applied to staging on 2026-10-13 and its permissions were verified.

Keep repository variable `KAPSULE_STAGING_CD_ENABLED=false` until all prerequisites
are met. Then use `workflow_dispatch` with `deploy=true` for the first delivery;
enable the variable only after validation. Manual dispatch becomes available
once the workflow is merged into the default branch. The GitHub environment
restricts deployment to `staging`.

The script checks the exact cluster, required objects, rollout and backend health.
Helm `--atomic` rolls back the application release on failure; **it does not undo
database migrations**. Use backwards-compatible migrations and take a backup
before destructive migrations. For a deliberate rollback, inspect `helm history`
and schema compatibility before running `helm rollback` in the same namespace.

## Application configuration

Both manual and automatic delivery target `staging`. The branch does not exist
yet; create it from the validated main revision before activation. The Assistant RH
individual identity changes have been merged into main and integrated here.

The namespace and release are `conversations`. `deploy/kapsule/staging.yaml`
disables public, admin and media Ingress until the domain and certificate are
chosen. Before public access, configure Django hosts, CSRF and OIDC settings,
point DNS to `51.159.113.113`, provision the TLS secret and review the new values.
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
using the scanned production image and verified PostgreSQL TLS. The hook remains
responsible for future migrations. No existing user or conversation was copied.
Automatic superuser creation is disabled; provision the first administrator
separately. Chart defaults preserve existing behavior for other deployments.

## Verification

Each pull request runs Helm validation and Docker builds without publishing.
Before publication, Trivy rejects every HIGH/CRITICAL vulnerability in each image.
The published digest identifies the image that passed this scan.
Before activation: verify private PostgreSQL connectivity and certificates,
initialize the schema, verify users and grants, provision secrets and connect
the runner, then complete a manual deployment. A heartbeat alone does not prove
a real conversation: also test authentication, `/v1/models`, a streamed exchange
and individual access controls from Conversations.

The backend security upgrade passes all 1419 tests against disposable local
PostgreSQL and S3 services. Tests cover permission revocation before delegation
and block unexpected external requests through both HTTPX and HTTPX2. These tests
do not replace the authenticated staging conversation checks above.

The inherited Helm chart publication workflow is restricted to `main`.
Its separate `gh-pages` destination must be provisioned before publishing charts;
Kapsule deployment uses the chart from the repository and does not require it.

JIT runners are prepared by `assistant-rh-infra/scripts/start-staging-runner.py`.
Both namespace probes passed: tools, GitHub connectivity and RBAC isolation.
Registration occurs only for a queued staging deployment and the runner expires
after its job. Fully automatic renewal still needs ARC and a dedicated GitHub
App; keep `KAPSULE_STAGING_CD_ENABLED=false` until then.
