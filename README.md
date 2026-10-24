# CodeRats Deploy — public workflow templates

Public **templates only** for CodeRats DEV builds and deployments. Application sources remain in the three private repositories, and authentication stays with their respective private caller workflows.

> **No public-runner billing bypass:** The public repo's GitHub-hosted runner validates **only these public templates**. Builds of private application code happen only in the private caller repository on its approved, private **self-hosted runner**. The public repo has no private GitHub App keys, PATs, Azure secrets, private application source, or access to the private repositories.

## Contents

| File | Purpose |
| --- | --- |
| `.github/workflows/dev-container.yml` | Private-caller release for web (Node 24) or API (Java 21) to Azure Container Apps DEV |
| `.github/workflows/dev-get-worker.yml` | Isolated GET beta DEV Azure Container Apps Job, immutable API image, max 100 events |
| `.github/workflows/validate-templates.yml` | Public syntax and security sanity-check — no private checkout |
| `examples/web-private-caller.yml` | Manual web DEV dispatcher for private web repository |
| `examples/api-private-caller.yml` | Manual API DEV dispatcher for private API repository |
| `examples/worker-private-caller.yml` | Manual GET worker DEV dispatcher for private infra repository |
| `scripts/validate_templates.py` | Static checks for template restrictions |

## Setup by CodeRats organization administrator

1. Create an **ephemeral, dedicated Linux x64 self-hosted runner** with Docker + BuildKit, `az`, `jq`, `curl`, Node 24 and Java 21. Add the label `coderats-deploy`. Put it in a runner group accessible **only to** the private repositories `coderats-hub/web-coderats-base`, `coderats-hub/api-coderats-base` and `coderats-hub/coderats-base-infra`. **Never** expose this runner to this public repository, public PRs, or unrelated workflows. Self-hosted infrastructure may incur Azure/VM operating costs.
2. Copy each file under `examples/` into the matching private repository as `.github/workflows/dev-self-hosted.yml`. **Replace** `REPLACE_WITH_FULL_40_CHARACTER_PUBLIC_REPO_SHA` with the immutable current commit SHA of this repository's `main`. Do not pin a mutable branch or tag.
3. Ensure `dev` GitHub Environment secrets **in each private caller** include `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. The web caller also needs `AZURE_CONTAINER_APP` (name of DEV web Container App) and `NEXT_PUBLIC_API_URL` (the HTTPS DEV API origin) as GitHub Variables; never hardcode secrets in this repository. API target is `ca-coderats-base-api-dev`.
4. Create narrowly scoped Azure federated credentials for each **private** repository's `environment:dev` OIDC subject. The federated subject must be `repo:coderats-hub/<private-repo>:environment:dev`; not `repo:coderats-hub/coderats-deploy`. Give the identity only required DEV ACR push and Container App update permissions.
5. Only after the runner is online and secrets are configured, manually dispatch `CodeRats web DEV (self-hosted)` on private `web-coderats-base/develop`. Confirm tests, image, ready revision, health and the single TRACES proficiency disclosure (PR #70). Next dispatch API DEV on `api-coderats-base/develop`, and then GET worker on `coderats-base-infra/main`.
6. Keep existing deployment workflows from updating the same Container Apps concurrently. Start with manual dispatch only and enable automatic private-caller triggers after a successful rehearsal.

**Important:** the templates don't automatically create the runners or Azure identities, do not touch production, and do not grant the public repository access to private source. A successful workflow also does not prove all UI business rules are correct; test TRACES with a real authenticated beta participant.

## Security and cost guardrails

- Private repo is the `workflow_call` caller and checkout source; reusable workflows run using caller context and its approval/permissions.
- The shared template job has `runs-on: [self-hosted, linux, x64, coderats-deploy]`, `environment: dev` and fixed CodeRats DEV resource group and registry checks.
- Never expose secrets through workflow summaries, logs, container tags or repository files. Only masked Environment secrets; no public deployment endpoint for untrusted user inputs.
- Restrict runner-group access and wipe runner filesystem/credentials between jobs. Prefer ephemeral agents/VMs with no other workloads.
- Every caller pins immutable public template SHA. Re-pin only after review when public template changes.
- Never add `pull_request_target` or an unrestricted `repository_dispatch` handler. All deployment triggers are restricted to trusted branches.
- GitHub-hosted runner minutes for templates validation are allowed here because the checks only validate this public repo's own files. Do not use GitHub-hosted runners in this public repo to build or release unrelated private project code.

## References

- [Reusable workflows](https://docs.github.com/en/actions/sharing-automations/reusing-workflows)
- [Self-hosted runner security](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#hardening-for-self-hosted-runners)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub terms for additional products](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
