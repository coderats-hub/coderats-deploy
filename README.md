# CodeRats Deploy — central CI/CD repository

**This public repository owns and executes DEV deployments.** The private CodeRats
application repositories are **source checkouts**, never workflow callers.

[Run the central DEV deployment](https://github.com/coderats-hub/coderats-deploy/actions/workflows/deploy-private-dev.yml)

## How a release works

```text
coderats-hub/coderats-deploy (PUBLIC, workflow_dispatch, protected dev)
  ├─ short-lived GitHub App token (read-only, one repository)
  ├─ authenticated checkout (private develop/main into app-src)
  ├─ tests + Docker build on a dedicated, isolated self-hosted runner
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
as public artifacts. Private logs are written to the runner's local temp
directory and are **not** uploaded to Actions. Source checkout is removed at
the end. Treat the runner disk as sensitive and destroy it after each job.

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
5. Provision an **ephemeral single-purpose Linux x64 self-hosted runner**
   with Docker/BuildKit, Azure CLI + Container Apps extension, Node 24,
   Java 21, `jq`, `curl` and Git. Label it `coderats-public-deploy`.
   Use dedicated VM images and recreate the VM after *every* job.
   Restrict the runner group to this specific repository **and, where
   supported, this precise deployment workflow**.
6. Protect public `main` against unreviewed modifications; limit public
   workflow changes to trusted reviewers. **Never** enable `pull_request`,
   `pull_request_target`, `repository_dispatch`, arbitrary ref
   inputs or fork-triggered code on this runner.
7. Run [the central workflow](https://github.com/coderats-hub/coderats-deploy/actions/workflows/deploy-private-dev.yml)
   with `web` + `DEPLOY_DEV`; check successful tests, image SHA, DEV
   ready revision and health. Then test TRACES manually with real beta data.
   For API and GET worker, execute their choices separately.

### Security warning and runner terms

**This approach does not get unlimited free GitHub-hosted minutes for
private CodeRats builds.** GitHub's terms restrict GitHub-hosted runner activity
unrelated to the project associated with the public repository. For this
reason this workflow **requires a self-hosted runner**, which may incur VM
costs. There is no supported switch to `ubuntu-latest` for private builds
solely to bypass private-repository quota or spending limits.

GitHub itself discourages self-hosted runners on **public repositories**,
because an untrusted PR can compromise persistent runners. The manual-only,
protected-environment workflow and ephemeral runner are risk-mitigation
requirements, not a guarantee of security. If those controls cannot be
provided, use a **private orchestration repository**, Azure remote build
system, or another trusted CI instead.

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
