# Central source event orchestration (opt-in rollout)

> **DEV is the live coderats.club.** Infrastructure, GET replay and data-repair workflows are not covered by automatic source releases. The new watcher is deliberately **disabled by default**.

## Why polling?
GitHub Actions `push` / `pull_request` events are repository-local. To remove automatic source-repository Actions *entirely*, we cannot use local forwarding workflows. The central repo uses a scheduled read-only GitHub App scan every five minutes. GitHub may delay/drop scheduled runs. This is not instant webhooks and does not guarantee an event for unreachable force-pushed commits.

## Release matrix

| Source | Branch | Central workflow |
|---|---|---|
| web-coderats-base | develop | deploy-private-dev.yml (web) |
| api-coderats-base | develop | deploy-private-dev.yml (api) |
| api-grader-coderats | develop | deploy-grader-dev.yml |
| those same three | main | validate-private-source.yml (CI only) |
| those same three | internal PR targeting develop/main | validate-private-source.yml (CI only) |
| any repo | PR code before merge | **never auto-deployed** |
| coderats-base-infra | main | **no automatic Terraform apply**; GET batch remains in central deploy |
| Education / Enterprise | main | no deploy: no current verified runtime/release target |

**Main does not map to a configured production environment.** The only live environment is DEV, whose operational source is `develop`. A `main` push is detected and validated but must not deploy over DEV with an older or conflicting source. Creating a real production stage needs separate infrastructure, credentials and policy.

## How the watcher works

1. `.github/workflows/watch-private-sources.yml` uses the existing protected GitHub App credential in central `dev`. The app needs **Contents: Read** and **Pull requests: Read** on only the three private deployed repositories.
2. `scripts/watch-source-events.py` reads `main`, `develop`, and open internal PR head SHAs. It persists only SHA identifiers in central GitHub Actions Variables. It never clones private code.
3. On first encounter, it baselines each branch (no retroactive deploy); open PRs are validated when the gate is enabled.
4. With `CODERATS_AUTO_RELEASES_ENABLED=true` in central environment `dev`, each reachable new commit schedules its service workflow. Older queued develop commits are rejected before Azure publication if branch HEAD advanced. The latest HEAD is the only one that can reach Azure.
5. Successful GitHub dispatch advances the last-observed SHA. A failed deploy is not automatically retried (avoids unbounded production loops). Inspect run and manually repair/release.
6. PR updates schedule exact-SHA validation; no untrusted fork code and no PR auto-release. Validation workflows suppress private output on public logs.
7. Source repos remove duplicate deployments. The API database maintenance scheduler and Terraform-specific validations remain source-owned exceptions; **they do not release applications**.

## Activation checklist (human review required)

1. Merge **central PR first**. Verify GitHub Actions accepts the YAML and the unit tests pass.
2. Confirm the App installation includes all three private repos and has Contents + Pull Requests read; the central `dev` environment has GitHub App ID and key. Never move secrets to the public workflow source.
3. Merge disabling PRs for API/web/grader and retire the duplicate GET cron in infra, ensuring no legacy local automatic deploy remains.
4. Check source repo branch protection rules: central PR validation does not produce check results on the source PR. Retain compatible local **non-deploy CI** if required, or redesign the required-check mechanism.
5. Run `Watch private CodeRats source changes` manually while the flag is false and inspect the state variables. This is baseline-only and not a release.
6. Set environment variable `CODERATS_AUTO_RELEASES_ENABLED=true` **only after** the above are complete and operators explicitly accept that future develop pushes/merges will deploy to the real site without per-deploy confirmation. No repo variable should bypass this environment flag.
7. Verify the first new commit on a test branch/PR, then a deliberately approved develop change. Watch validation, deployment SHA, health/readiness and GET job. Confirm no duplicate local actions.

## Observability and risks

- Source watch schedule may run with delays and can miss force-pushed commits no longer reachable; it **fails closed** on non-fast-forward branch changes or truncated commit comparisons.
- Branch state updates after dispatch acceptance, **not** deploy success. Treat CI failure and failed deploy as incidents to resolve, not silent retrials.
- Multiple commits may queue validations/releases; stale builds may fail as intended. No older SHA should replace newer live content.
- Shared GET cron already runs every 15 minutes from central. Disable duplicate infra schedule only after confirming central health.
- The public deploy repo must never emit private code, test bodies, build logs or secret values.
- GitHub Actions private-source public-runner policies, plan quotas and token scopes require organization review.
- Do not deploy from `main` into DEV, auto-apply Terraform, or deploy Education/Enterprise without separate approved release targets.

## Further hardening
An org-level GitHub App webhook receiver can replace polling when approved; it must authenticate webhook signatures, restrict repository/branch/events, and safely dispatch central workflows without exposing private payloads.
