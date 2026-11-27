# DGAFP Conversations delivery

Changes enter protected `main` through pull requests. Merging runs CI only.
Deployment uses immutable, protected tags on commits already integrated into
`main`; upstream tags remain separate under `refs/upstream/tags/*`.

| Tag | Target | Images |
| --- | --- | --- |
| `vX.Y.Z-rc.N` | Staging | Build and scan the exact tagged source |
| `vX.Y.Z` | Production | Copy the validated RC's exact backend/frontend digests |

Production requires a validated RC of the **same version and same commit**.
It never rebuilds images. Reviewed environment values may differ. Versions are
independent of upstream releases and the API repository.

## Delivery and validation

1. Merge the change, wait for backend, frontend/E2E, security and Helm CI, then
   create an RC tag on the reviewed commit. Use the next unused DGAFP version.
2. `Kapsule staging` validates the tag without credentials. Its successful
   `workflow_run` starts `Tagged Conversations delivery`, whose privileged
   definition always comes from protected `main`. Failed runs, PRs, branch
   pushes and tags outside `main` ancestry cannot publish.
3. GitHub scans both images, seals their digests and the source commit's
   environment values into an OCI Helm chart, then publishes to Scaleway.
   Flux watches the registry from inside Kubernetes; GitHub has no cluster key.
4. Flux must reconcile that exact chart, finish the rollout and pass Helm tests.
   An authorized operator then runs the authenticated recipe and records its
   proof with the command below. Publication alone never means deployment success.
5. After staging validation, create the stable tag on the **same commit**.
   Production copies and rescans the RC images, then requires its own observed
   rollout and authenticated recipe before being marked successful.

The recipe must cover login, an actual streamed answer and user isolation against
Assistant RH. Record the test report in the issue/PR associated with the delivery.
Use an operator token with repository contents/deployments write access and a
read-only-capable kubeconfig for the target cluster. Neither belongs in Git.
The operator's GitHub login must appear in the `RELEASE_VALIDATORS` variable.

Example `recipe.json`, filled only after the checks actually pass:

```json
{
  "tag": "v0.1.0-rc.1",
  "source_sha": "<exact 40-character tagged SHA>",
  "target": "staging",
  "checks": {"authentication": true, "sse": true, "isolation": true},
  "report_url": "https://github.com/DGAFP/conversations/issues/<report>"
}
```

```bash
export GH_TOKEN="$(gh auth token)"
export RELEASE_VALIDATORS=pcasad
python3 deploy/kapsule/release.py confirm \
  --tag v0.1.0-rc.1 --recipe /private/recipe.json \
  --kubeconfig /private/kubeconfig
```

`confirm` reads Kubernetes only. It checks the canonical cluster, current Flux
observed generations and chart digest, successful Helm tests, source SHA and
ready backend/frontend deployments with the expected image digests. The receipt
also records effective Helm overrides and the API images used during validation.
The Release holds `reservation.json`, the sealed chart, `delivery.json` and an
immutable `receipt.json`; only a receipt uploaded by an allowed human validator
is accepted. To invalidate a proof, upload `revocation.json` to that Release and
open a corrective delivery; do not overwrite the receipt or move the tag.

## Serialization and recovery

One GitHub concurrency group holds publication through observed validation.
A durable GitHub Deployment record also blocks a new delivery after a timeout,
cancellation or failed publication. The job waits up to 30 minutes for proof;
timeout leaves the delivery unresolved. Inspect Flux and complete the recipe,
then manually run `Tagged Conversations delivery` on **main** with action
`resume` and the existing tag. A resumed delivery reuses immutable artifacts.

To roll back, run the same workflow on **main** with action `redeploy` and a
previously validated tag. It republishes the known image digests in a new chart
with a higher technical version; it does not rebuild or move a release tag.
The workflow run ID identifies the recovery. Record its new recipe using
`confirm --delivery-id <run-id>`; use that ID when resuming it too. Recovery
files are prefixed `redelivery-<run-id>-`. The unresolved delivery is marked
inactive only after the recovery is validated. No successful known release yet:
keep publication stopped and resolve the first adoption with an operator.

For a hotfix, branch from the production tag, review and test it in a PR targeting
`main`, merge **without squash** to retain that tested commit, then tag that
commit as a new RC and promote it normally. Never retag an existing version.

Technical chart versions are `0.<next registry sequence>.0`, above existing legacy
versions. They deliberately differ from release tags so retries and recovery
work with Flux's existing `>=0.1.0 <1.0.0` selector. Existing chart content is
never replaced by a retry. Disable `TAGGED_RELEASES_ENABLED` to stop new jobs;
this does not stop already published charts. An emergency Helm rollback requires
an operator to suspend Flux first and reconcile the desired chart before resuming.

## GitHub activation

- Keep `KAPSULE_STAGING_CD_ENABLED=false`; legacy branch publication is removed.
- Enable `TAGGED_RELEASES_ENABLED=true`, set `RELEASE_VALIDATORS` explicitly.
- Restrict `v*` tag creation to repository administrators; separately prohibit
  updates/deletions without a bypass. Protect `main` with PRs and signed commits.
- Environment `kapsule-staging`: allow workflow runs from **main** only and keep
  the existing `SCW_REGISTRY_SECRET_KEY` scoped to that environment. The trusted
  `workflow_run` runs on main, not on the triggering tag.
- Keep `KAPSULE_PRODUCTION_CD_ENABLED=false` until production is provisioned.
  Before its first RC, review `deploy/kapsule/production.yaml`, a distinct registry
  and cluster, the `kapsule-production` environment restricted to main, its target
  registry secret and staging read-only `SCW_STAGING_REGISTRY_READ_KEY`.
  Set `CONVERSATIONS_PRODUCTION_REGISTRY` and
  `CONVERSATIONS_PRODUCTION_API_SERVER`, verify the same Flux/Helm-test contract,
  then explicitly enable production. Missing configuration fails closed.

## Runtime prerequisites

The existing staging Flux controllers, OCIRepository `conversations-delivery`
and HelmRelease `conversations` live in namespace `conversations`. Helm uses the
existing `github-deploy` service account and rollback remediation. The new flow
needs no controller change, private runner or cluster credential in GitHub.

`deploy/kapsule/staging.yaml` remains the canonical configuration. The frontend
uses the same-origin API. Existing namespace Secrets supply database/Redis TLS,
Django credentials, registry authentication and identity keys; the LLM ConfigMap
routes to Assistant RH. Never embed secret values in charts or receipts.
The pre-upgrade hook checks for pending migrations; schema changes require a
separate approved operation. Helm smoke tests check health/config only and do
not replace the authenticated recipe.
