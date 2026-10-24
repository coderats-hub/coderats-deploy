# CodeRats Deploy — central CI/CD repository

**This public repository owns and executes DEV deployments.** The private CodeRats
application repositories are **source checkouts**, never workflow callers.

[Run the central DEV deployment](https://github.com/coderats-hub/coderats-deploy/actions/workflows/deploy-private-dev.yml)

## How a release works

```text
coderats-hub/coderats-deploy (PUBLIC, workflow_dispatch, protected dev)
  ├─ short-lived GitHub App token (read-only, one repository)
  ├─ authenticated checkout (private develop/main into app-src)
  ├─ tests + Docker build on a standard GitHub-hosted ubuntu-latest runner
  ├─ Azure login via OIDC for coderats-deploy:environment:dev
  └─ DEV ACR push → Container Apps revision and health check
       or immutable GET DEV worker update + bounded execution
```

One workflow: `.github/workflows/deploy-private-dev.yml`. Choose `web`,
`api`, or `get-worker`; type `DEPLOY_DEV`. Only the trusted `main`
workflow code is allowed to deploy and the job uses the protected `dev`
GitHub Environment. The source checkout uses `develop` for private web/API,
`main` for private infrastructure. Both paths are **fixed**, not user input.

| Service | Private checkout | Azure DEV operation |
| --- | --- | --- |
| web | `coderats-hub/web-coderats-base@develop` | npm tests, TypeScript, Next build, Docker, ACR, web Container App and `/health` |
| api | `coderats-hub/api-coderats-base@develop` | Java 21/Maven verification, Docker, ACR, API Container App and health |
| get-worker | `coderats-hub/coderats-base-infra@main` | Resolve deployed API image to SHA-256 digest, update isolated GET Job, start bounded DEV batch |

No source, tokens, private test failures, or full Docker output are uploaded
as public artifacts. Private test/build logs are redirected to the ephemeral
runner's local temporary files and **not** uploaded as artifacts. Secret
masking and short-lived credentials are required; even with these safeguards,
public run metadata is visible and third-party action output can reveal
details. Never print private source, GitHub App credentials or runtime secrets.

## Required one-time administration

The assistant's GitHub app connector cannot configure secrets, environments,
federated Azure identities, or runners. An organization admin must do the
following **before triggering a release**:

1. Register a dedicated GitHub App, e.g. `coderats-deploy-read`, with
   **Contents: Read-only** and no unnecessary permissions. Install it on
   **selected repositories only**:
   `web-coderats-base`, `api-coderats-base`, `coderats-base-infra`.
   This public repo is not granted private access without this explicit install.
2. In this public repository create environment **`dev`**, restrict to
   branch `main`, and require review by a trusted CodeRats maintainer.
   Store its **environment secret** `CODERATS_DEPLOY_APP_PRIVATE_KEY` and
   **environment variable** `CODERATS_DEPLOY_APP_ID`.
3. In that same environment add Azure OIDC secrets
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`, plus variables
   `AZURE_WEB_CONTAINER_APP` (verified web DEV Container App name) and
   `NEXT_PUBLIC_API_URL` (HTTPS API DEV origin).
4. Create an **Azure federated identity credential** for subject
   `repo:coderats-hub/coderats-deploy:environment:dev`, with audience
   `api://AzureADTokenExchange`, trusted to update **DEV only**.
   Give only required ACR push and Container Apps update/job permissions.
   Previous credentials scoped to the three private repos **do not**
   automatically authenticate this public repository.
5. Standard GitHub-hosted **`ubuntu-latest`** runners are selected in
   `.github/workflows/deploy-private-dev.yml`; there is **no
   self-hosted runner to register**. Docker, git, curl and Azure CLI are
   available on the standard GitHub Ubuntu runner images. Node 24 and
   Java 21 are installed through setup actions. A preflight fails before
   checking out private code if required environment credentials are missing.
6. Protect public `main` against unreviewed modifications; limit public
   workflow changes to trusted reviewers. **Never** enable `pull_request`,
   `pull_request_target`, `repository_dispatch`, arbitrary ref
   inputs or fork-triggered code on this runner.
7. Run [the central workflow](https://github.com/coderats-hub/coderats-deploy/actions/workflows/deploy-private-dev.yml)
   with `web` + `DEPLOY_DEV`; check successful tests, image SHA, DEV
   ready revision and health. Then test TRACES manually with real beta data.
   For API and GET worker, execute their choices separately.

### Security and GitHub Actions terms

The workflow uses **standard GitHub-hosted Ubuntu runners** in this public
repository. GitHub currently treats standard Actions runner minutes on public
repositories as free. This does **not** guarantee that every use of a public
runner to process private source is permitted: GitHub's [Additional Product
Terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
restrict GitHub-hosted compute to production, testing, deployment or
publication of the **software project associated with the repository**.
This repository is specifically the CodeRats project's deployment tooling;
we must maintain that legitimate connection and avoid using it as a generic
third-party build farm or a way to abuse free minutes. GitHub may apply
restrictions based on its interpretation. If that use is not approved for
the organization, build from the private repo or an appropriately funded
CI/remote build service instead.

Restrict manual deployments to trusted organization maintainers and protected
`main`/environment `dev`. The public UI exposes workflow run names,
steps and metadata: private repository names/commit SHAs may be visible.
Never configure public PR triggers, arbitrary checkout refs, persistent
PATs, or uploads of private build logs and artifacts.

- [GitHub Actions terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub App token action](https://github.com/actions/create-github-app-token)
- [Private checkout](https://github.com/actions/checkout)

## Older templates

`dev-container.yml`, `dev-get-worker.yml`, and `examples/` were created
for a **previous, private-caller reusable-workflow design**. They are
retained for reference only and **are not the supported deployment entrypoint**.
The supported path is `deploy-private-dev.yml` in **this public repository**.

The `validate-templates.yml` workflow uses the public hosted runner **only**
to lint this public repository's own YAML and Bash scripts (no private source).
