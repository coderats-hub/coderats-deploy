# CodeRats observability (DEV)

This change adds four **real Azure Workbook templates**, their idempotent publisher and a **small, read-only GitHub Actions benchmark**.

## Azure Workbooks
- Run the protected `Publish CodeRats DEV Azure Workbooks` workflow from `main`; enter `PUBLISH_WORKBOOKS_DEV`.
- Uses existing `dev` Azure OIDC credentials, finds the **single** Log Analytics workspace in `rg-coderats-base-dev`, and creates four shared Azure Workbooks.
- Open Azure Portal > Monitor > Workbooks; find `CodeRats | Saúde e volume`, `CodeRats | API Base e dependências`, `CodeRats | Frontend`, `CodeRats | Grader`.
- Requires the federated identity to have `Microsoft.Insights/workbooks/write` and Log Analytics workspace read permissions in DEV. Failures in access control are expected until these are granted.
- Provisioning is idempotent by UUID and never writes Terraform state. If later adding Terraform ownership, **import** existing workbook resources rather than creating duplicates.
- The tables query `AppRequests`, `AppDependencies`, `AppMetrics` and `AppExceptions`; data is **not created automatically** by the workbook. Instrument service role names, server/browser requests, downstream dependencies, and custom cache/business metrics before expecting full dashboards.
- Do not log personal data, authentication headers, private source or user-provided prompts.

## Safe baseline benchmark
- Set the protected `dev` environment variables `AZURE_DEV_WEB_URL`, `AZURE_DEV_API_URL` and `AZURE_DEV_GRADER_URL` to the **real DEV HTTPS Container Apps origins**. Use origins only (no paths, query strings or credentials).
- Run `CodeRats DEV API baseline benchmark` from `main`, select service and enter `BENCHMARK_DEV`.
- 3 preflight requests plus 15 measured requests, sequential, 150 ms gaps, 8s timeout. Only `GET /health` (web, grader) or `GET /actuator/health` (API). Target allowlist only `*.azurecontainerapps.io` and no custom production domain.
- A sanitized aggregate is published to Actions summary plus a 14-day JSON artifact: count, error rate, p50/p95/mean duration and status counts.
- **This does not measure reduction in requests.** It is a safe initial latency/availability baseline. To prove optimization outcomes, add isolated, authenticated synthetic journey tests with instrumented request counting, fixed seed data, pinned before/after releases and multiple repeated runs. Those cannot safely run against production or without a supported test fixture.

## Deployment/limitations
- GitHub's repository connector does **not** expose an Azure control-plane login or workflow dispatch operation. Files pushed here are not evidence that Azure workbooks have been provisioned.
- Existing infra Terraform declares workspace/App Insights; verify actual resources and ingestion in Azure.
- Dashboard chart data may be empty until proper OpenTelemetry/App Insights export is installed. Health benchmark metrics should not be interpreted as complete end-to-end performance indicators.
- Workflows deliberately avoid schedules or push triggers; a maintainer triggers from protected `main` and reviews permission scopes first.
