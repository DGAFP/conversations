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
restricts deployment to `main`.

The script checks the exact cluster, required objects, rollout and backend health.
Helm `--atomic` rolls back the application release on failure; **it does not undo
database migrations**. Use backwards-compatible migrations and take a backup
before destructive migrations. For a deliberate rollback, inspect `helm history`
and schema compatibility before running `helm rollback` in the same namespace.

## Application configuration

Both manual and automatic delivery target `main`. The Assistant RH adaptations
are on `codex/596-individual-identity`; integrate and validate that work before
expecting a real Assistant RH conversation. This workflow does not merge it.

The namespace and release are `conversations`. `deploy/kapsule/staging.yaml`
disables public, admin and media Ingress until the domain and certificate are
chosen. Before public access, configure Django hosts, CSRF and OIDC settings,
point DNS to `51.159.113.113`, provision the TLS secret and review the new values.
The frontend uses the same origin for its API (`API_ORIGIN` is empty).

Prerequisites in this namespace:

- Secret `conversations-runtime`: `DB_PASSWORD` for `conversations_app`,
  `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` including `127.0.0.1` for internal
  health checks, complete OIDC/ProConnect settings, dedicated S3 credentials and
  delegation settings required by the Assistant RH version. Do not use
  `Development` or disable authentication to bypass these requirements.
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
Automatic superuser creation is disabled; provision the first administrator
separately. Chart defaults preserve existing behavior for other deployments.

## Verification

Each pull request runs Helm validation and Docker builds without publishing.
Before activation: verify private PostgreSQL connectivity and certificates,
initialize the schema, verify users and grants, provision secrets and connect
the runner, then complete a manual deployment. A heartbeat alone does not prove
a real conversation: also test authentication, `/v1/models`, a streamed exchange
and individual access controls from Conversations.

The inherited Helm chart publication workflow is restricted to `main`.
Its separate `gh-pages` destination must be provisioned before publishing charts;
Kapsule deployment uses the chart from the repository and does not require it.
