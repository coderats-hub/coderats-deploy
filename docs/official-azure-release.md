# Official CodeRats Azure release — 2026-11-11

The live `https://coderats.club` hostname has moved to the **new** Azure Container Apps environment. Use only the protected `migration-target` GitHub Actions environment for application releases. It holds the existing destination Azure OIDC binding and GitHub App access; do **not** re-create or print secret values.

| Service | Canonical workflow | Source branch | Azure Container App |
| --- | --- | --- | --- |
| Web | `deploy-private-dev.yml` (`service=web`) | `web-coderats-base@develop` | `ca-cdmstd-web-c26m0911` |
| API | `deploy-private-dev.yml` (`service=api`) | `api-coderats-base@develop` | `ca-cdmstd-api-c26m0911` |
| Grader | `release-grader-languages-dev.yml` | `api-grader-coderats@develop` | `ca-cdmstd-grader-c26m0911` |

**Azure scope**: resource group `rg-coderats-migration-c26m0911`, ACR `crcoderatsmigc26m0911`, Container Apps Environment `cae-coderatsmig-wp-c26m0911`, Brazil South. The API and Grader ingress must remain private; only Web is externally reachable.

## Release

1. Open GitHub Actions in `coderats-hub/coderats-deploy`, select **Deploy CodeRats web and API to official Azure (CodeRats Club)**, then **Run workflow** on `main`. Select `api` or `web`, and enter `DEPLOY_DEV`.
2. Deploy API **before** Web when changing OAuth/callback configuration. The central flow checks out the current `develop` SHA, verifies tests, builds the Docker image and deploys only the exact target Container App. API runtime changes preserve all existing OAuth secrets, PostgreSQL credentials, media mounts and grader integration secrets.
3. For Grader, use **Release CodeRats Grader to official Azure** manually. This is a separate rollout and preserves existing runtime secrets and private ingress.
4. Verify the Actions job summary: deployed source SHA and the latest ready Azure revision. Web health must be HTTP 200, anonymous API session must be 401, OAuth must redirect to GitHub with `redirect_uri=https://coderats.club/login/oauth2/code/github`. Validate browser login and cookie storage manually too.

The Web image must be **rebuilt** with `NEXT_PUBLIC_API_URL=https://coderats.club` and `API_PROXY_TARGET` pointing to the new private API HTTPS ingress FQDN. Setting the container variable alone does not recompile Next.js rewrites or the browser's environment values. The Web proxy must serve `/api/v1/*`, `/oauth2/authorization/github` and `/login/oauth2/code/github` from the first-party domain, keeping the secure `JSESSIONID` cookie first-party. Ensure the same callback is registered in the existing GitHub App.

## Safety and migration state

- The old Azure subscription, resource group `rg-coderats-base-dev` and Container Apps are **not** deployment destinations. Historical candidate workflows `deploy-optimizations-release.yml`, `deploy-profile-release-preview.yml` and `restore-grader-ai-dev.yml` are intentionally retired and cannot deploy.
- The new target GET job `caj-coderatsmig-get-wp-c26m0911` was provisioned **inert, without database credentials**. The canonical deploy does not start it on API updates, has no automatic `*/15` schedule, and does not enable processing. Enable it only after an explicit, separately verified, credential-safe data processing rollout. This leaves GET projections pending.
- The deployed API runs Flyway on startup. Check migration compatibility with the restored DB before each API release; a container rollback does not undo schema changes.
- Do not deploy via Terraform, copy the database again, expose private logs into this public repository or rotate credentials for this URL correction.
- Existing unrelated infra and source-repo cron/auto-deploy workflows are **not** modified by these central workflow changes; audit them separately before considering all duplicate deploy schedules eliminated.
