# Official Azure release workflows

Type: Runbook · Scope: coderats-deploy · Verified: 2026-11-21 @ coderats-deploy c6dd66b

Mechanics of the workflows that release CodeRats to production (`https://coderats.club`). Approval,
preflight, post-release verification and rollback are owned by the workspace runbook
[`docs/workflows/deploy.md`](https://github.com/coderats-hub/coderats-workspace/blob/main/docs/workflows/deploy.md)
(coderats-hub/coderats-workspace); follow it for every release.

## Workflows

| Service | Workflow (Actions name) | Inputs | Source checkout | Target Container App |
| --- | --- | --- | --- | --- |
| Web | `deploy-private-dev.yml` (**Deploy CodeRats web and API to official Azure (CodeRats Club)**) | `service=web`, `confirmation=DEPLOY_DEV` | `web-coderats-base@develop` HEAD | `ca-cdmstd-web-c26m0911` |
| API | same workflow | `service=api`, `confirmation=DEPLOY_DEV` | `api-coderats-base@develop` HEAD | `ca-cdmstd-api-c26m0911` |
| Grader | `release-grader-languages-dev.yml` (**Release CodeRats Grader to official Azure**) | none | `api-grader-coderats@develop` HEAD | `ca-cdmstd-grader-c26m0911` |

Common to both workflows:

- Run on `main` only (`github.ref == refs/heads/main`, repository `coderats-hub/coderats-deploy`); any other
  ref skips the job. Source branch is fixed in the workflow, not an input.
- GitHub environment `migration-target` (Azure OIDC + read-only GitHub App token for exactly one source repo).
  The `dev` environment is retired and must never be used.
- Azure scope: resource group `rg-coderats-migration-c26m0911`, ACR `crcoderatsmigc26m0911`, Container Apps
  Environment `cae-coderatsmig-wp-c26m0911` (Brazil South). Only Web ingress is external; API and Grader are internal.
- `ubuntu-latest` GitHub-hosted runner. Private test/build output goes to runner temp files; only sanitized
  failure categories are printed. Image tag: `<acr>.azurecr.io/<source-repo>:<source-sha>-<run-id>`.
- ACR auth: `az acr login`; if that fails, a push-scoped ACR token `coderats-official-release-push` with a
  1-day password (masked).
- Success = Container App image equals the new tag and `latestRevisionName == latestReadyRevisionName`
  (polled 45 × 10 s). The job summary records the ready revision and the source SHA.
- Concurrency, `cancel-in-progress: false`: Web/API group `coderats-official-<service>` (web and api can run in
  parallel, same service queues); Grader group `coderats-grader-dev-release`.

## Web/API job (`deploy-private-dev.yml`)

1. Validate `confirmation == DEPLOY_DEV` and `service ∈ {web, api}`; check the environment has
   `CODERATS_DEPLOY_APP_ID`, `CODERATS_DEPLOY_APP_PRIVATE_KEY`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID` before any private checkout.
2. Checks on the source:
   - Web (Node 24): `npm ci`, `npm test`, `npm run test:ws` (WebSocket gateway), `npx tsc --noEmit`,
     `npm run build -- --webpack`.
   - API (Java 21 Temurin): `./mvnw --batch-mode verify`.
3. Destination guard: pinned subscription and tenant; RG, ACR and CAE default domain exist; API ingress is
   internal, Web ingress external, Grader ingress internal with an `api-grader-coderats` image and a ready
   revision. Derives the private API and Grader HTTPS origins from their ingress FQDNs. Refuses to replace an
   app whose current image is from another repository.
4. Build and update:
   - Web image built with `--build-arg NEXT_PUBLIC_API_URL=https://coderats.club` and
     `--build-arg API_PROXY_TARGET=<private API ingress origin>` (both are compiled into the Next.js build;
     setting the container variable alone does not change rewrites). The same two variables are also set on the
     revision. `API_PROXY_TARGET` is server-only and must never be the Web origin.
   - API revision env set by the job: `FRONTEND_URL=https://coderats.club`,
     `GITHUB_OAUTH_REDIRECT_URI=https://coderats.club/login/oauth2/code/github`,
     `CORS_ALLOWED_ORIGINS=https://coderats.club,https://www.coderats.club`,
     `SERVER_SERVLET_SESSION_COOKIE_SECURE=true`, `GRADER_API_URL=<private Grader ingress origin>`,
     `GRADER_POLLING_ENABLED=true`. Other env vars and secrets (OAuth, database, media, grader token) are
     preserved. After the revision is ready, the job fails unless the effective `GRADER_API_URL` and
     `GRADER_POLLING_ENABLED=true` match.
5. Post-release checks (both services): Web Container App `/health` = 200; `/api/v1/auth/me` = 401 on the Web
   origin and on `https://coderats.club`; `https://coderats.club/oauth2/authorization/github` redirects (302/303)
   to `github.com/login/oauth/authorize` with `redirect_uri=https://coderats.club/login/oauth2/code/github` and a
   `state`. Headers are parsed in a temp file, never printed.
6. Always: delete the source checkout and `az logout`.

The API runs Flyway on startup; a container rollback does not undo migrations. The job does not touch the GET
job (`caj-coderatsmig-get-wp-c26m0911`), which stays inert.

## Grader job (`release-grader-languages-dev.yml`)

No inputs and no confirmation string. Checks: Python 3.12, `pip install -r requirements.txt`,
`compileall` of `web`, `autograder`, `sandbox_manager`, and the grader's language-validation pytest subset (not
the full suite). Destination guard: pinned subscription, RG, ACR, app exists, ingress internal, current image
from `api-grader-coderats`. Builds `Dockerfile.cloud` and updates only the image (`az containerapp update
--image`); runtime env and secrets are untouched. No HTTP checks after the rollout.

## Push-by-commit-message trigger

Both workflows also run on `push` to `main`, but only when:

- the push changes the workflow file itself (`paths` filter: `deploy-private-dev.yml` or
  `release-grader-languages-dev.yml`), **and**
- the push's head commit message is **exactly** `ci(migration): release target web`,
  `ci(migration): release target api` or `ci(migration): release target grader` (string equality: a body or
  trailer such as `Co-Authored-By` makes it not match, and the job is skipped).

On such a push the Web/API workflow derives `service` from the message and treats the confirmation as
`DEPLOY_DEV`. This is a deploy with no input prompt: it needs the same explicit per-service approval as a
manual dispatch (workspace runbook). Prefer `workflow_dispatch`.

## Not release paths

- Retired stubs that only fail with a pointer to the workflows above: `deploy-optimizations-release.yml`,
  `deploy-profile-release-preview.yml`, `restore-grader-ai-dev.yml`. Do not restore their old content.
- `dev-container.yml`, `dev-get-worker.yml` (reusable, environment `dev`) and `examples/*-private-caller.yml`:
  an earlier private-caller design for the retired `dev` environment. There is no GET worker deploy path.
- Every other workflow bound to environment `dev` (benchmarks, diagnostics, validations, observability) targets
  the retired infrastructure and is unusable.
- `*migration*` workflows: one-time tooling from the move to the current subscription. Several can change
  production resources; do not run them as part of a release.
- `validate-templates.yml`: lints this repository's own YAML/scripts on push and PR; no secrets, no deploy.
