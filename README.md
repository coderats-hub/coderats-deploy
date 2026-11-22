# CodeRats Deploy

Public repository that builds and releases CodeRats to production (`https://coderats.club`). The private
application repositories are only source checkouts: they never call or trigger these workflows.

## Release workflows

| Service | Workflow |
| --- | --- |
| Web, API | [`deploy-private-dev.yml`](https://github.com/coderats-hub/coderats-deploy/actions/workflows/deploy-private-dev.yml) — choose `service`, type `DEPLOY_DEV` |
| Grader | [`release-grader-languages-dev.yml`](https://github.com/coderats-hub/coderats-deploy/actions/workflows/release-grader-languages-dev.yml) |

Both run on `main` only, in the GitHub environment `migration-target`, check out `develop` HEAD of the service
repository with a short-lived read-only GitHub App token, run the service's checks, push an image to the
production registry and update one Azure Container App. There is no `dev` environment and no GET worker
deploy path.

- Workflow mechanics (inputs, targets, checks, build args, push trigger, retired workflows):
  [docs/official-azure-release.md](docs/official-azure-release.md).
- Release process (approval, verification, rollback): workspace
  [`docs/workflows/deploy.md`](https://github.com/coderats-hub/coderats-workspace/blob/main/docs/workflows/deploy.md).
- Azure Workbooks and benchmarks: [observability/README.md](observability/README.md) (not usable yet).

## Configuration

Administered outside this repository by organization admins:

- A GitHub App with **Contents: read-only**, installed only on the source repositories the workflows check
  out (web, API, grader). Environment variable `CODERATS_DEPLOY_APP_ID`, environment secret
  `CODERATS_DEPLOY_APP_PRIVATE_KEY`.
- Azure OIDC: environment secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and a
  federated credential for `repo:coderats-hub/coderats-deploy:environment:migration-target` limited to the
  production registry push and Container Apps update.
- Standard GitHub-hosted `ubuntu-latest` runners; no self-hosted runner. Jobs fail before any private checkout
  if a required value is missing.
- Protect `main`: only trusted maintainers change workflows. Never add `pull_request`, `pull_request_target`,
  `repository_dispatch`, fork-triggered code or arbitrary ref inputs to a workflow that uses secrets.
  `validate-templates.yml` (no secrets) is the only workflow that runs on pull requests.

## Security and GitHub Actions terms

Workflows run on standard GitHub-hosted Ubuntu runners in this public repository. GitHub currently treats
standard runner minutes on public repositories as free. This does **not** guarantee that every use of a public
runner to process private source is permitted: GitHub's [Additional Product
Terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
restrict GitHub-hosted compute to production, testing, deployment or publication of the **software project
associated with the repository**. This repository is the CodeRats project's deployment tooling; keep that
connection and never use it as a generic build farm. If that use is not approved for the organization, build
from the private repository or a funded CI service instead.

The public UI exposes run names, steps, summaries and metadata: private repository names and commit SHAs are
visible. No source, tokens, raw test/build logs or artifacts from private code are uploaded or printed;
failures print only sanitized categories. Never print private source, GitHub App credentials or runtime
secrets, and never configure persistent PATs.

- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub App token action](https://github.com/actions/create-github-app-token)
- [Private checkout](https://github.com/actions/checkout)
