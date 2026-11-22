# CodeRats observability

Type: Runbook · Scope: coderats-deploy · Verified: 2026-11-21 @ coderats-deploy c6dd66b

**Not usable.** Every workflow here uses the retired GitHub environment `dev` and resource group
`rg-coderats-base-dev`. They cannot reach production (`migration-target`) until they are ported; do not run
them, and do not point them at production without a separate change.

## Contents

| Item | What it does |
| --- | --- |
| `workbooks/*.json`, `workbooks/manifest.json` | Four Azure Workbook templates (`CodeRats \| Saúde e volume`, `API Base e dependências`, `Frontend`, `Grader`) querying `AppRequests`, `AppDependencies`, `AppMetrics`, `AppExceptions`. Fixed GUIDs make publishing idempotent. |
| `publish-workbooks-dev.yml` + `scripts/publish_workbooks.py` | Creates the workbooks in the single Log Analytics workspace of the configured resource group. Dispatch input `PUBLISH_WORKBOOKS_DEV`. |
| `benchmark-dev.yml` + `scripts/benchmark_dev.py` | Read-only latency baseline per service (`web`, `api`, `grader`): 3 warm-up + 15 sequential `GET /health` (API: `/actuator/health`) requests; publishes an aggregate (count, error rate, p50/p95/mean, status counts) to the job summary and a 14-day JSON artifact. Dispatch input `BENCHMARK_DEV`. Measures availability/latency only, not request reduction. |
| `export-azure-observability-dev.yml` + `scripts/export_azure_observability_dev.py` | Exports observability and cost data for 7/14/30 days. |

Triggers: `workflow_dispatch` on `main`, and also `push` to `main` when a commit message starts with
`ci(dev): provision observability and benchmark` (workbooks, benchmark) or `ci(observability): run azure export`
(export) and the push changes the matching paths.

## Porting checklist

- Switch environment and resource group to production and grant the OIDC identity
  `Microsoft.Insights/workbooks/write` plus Log Analytics read; import existing workbooks into Terraform rather
  than duplicating them if Terraform takes ownership.
- Benchmark targets are Container App FQDNs; the script accepts only `*.azurecontainerapps.io` and rejects
  `coderats.club`.
- Dashboards stay empty until services export telemetry (role names, requests, dependencies, custom metrics).
- Never log personal data, auth headers, private source or user prompts.
