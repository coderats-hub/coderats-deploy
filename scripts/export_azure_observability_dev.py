#!/usr/bin/env python3
"""Read-only Azure cost and observability export. No raw log messages or user data.

This public repository exports ONLY aggregated operational data, excluding
raw logs, HTTP URLs and user identifiers. Artifacts are publicly readable
by signed-in GitHub users with repository read access.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

UTC = dt.timezone.utc
RESOURCE_TYPES = {
    "microsoft.app/containerapps": "container-app",
    "microsoft.app/jobs": "container-job",
    "microsoft.dbforpostgresql/flexibleservers": "postgresql",
    "microsoft.storage/storageaccounts": "storage",
    "microsoft.servicebus/namespaces": "service-bus",
    "microsoft.containerregistry/registries": "registry",
    "microsoft.operationalinsights/workspaces": "log-analytics",
}
METRIC_WORDS = (
    "cpu", "memory", "working", "request", "replica", "restart",
    "latenc", "response", "connection", "storage", "transaction",
    "queue", "message", "throughput", "network", "egress",
    "ingress", "success", "fail", "error", "execution", "io",
    "size", "availability", "utiliz", "thrott", "active", "count",
)
COUNTER_WORDS = (
    "request", "restart", "transaction", "message", "execution",
    "success", "fail", "error", "count", "network", "egress",
    "ingress", "thrott",
)
LOG_QUERIES = {
    "requests_daily": """AppRequests
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize requests=count(), failures=countif(Success == false),
    avg_duration_ms=round(avg(DurationMs),2),
    p50_duration_ms=round(percentile(DurationMs,50),2),
    p95_duration_ms=round(percentile(DurationMs,95),2),
    p99_duration_ms=round(percentile(DurationMs,99),2),
    estimated_billed_bytes=sum(tolong(column_ifexists("_BilledSize",0)))
  by day=bin(TimeGenerated,1d), role=tostring(AppRoleName), status_code=tostring(ResultCode)
| order by day asc""",
    "dependencies_daily": """AppDependencies
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize dependencies=count(), failures=countif(Success == false),
    avg_duration_ms=round(avg(DurationMs),2),
    p95_duration_ms=round(percentile(DurationMs,95),2)
  by day=bin(TimeGenerated,1d), role=tostring(AppRoleName), dependency_type=tostring(Type)
| order by day asc""",
    "exceptions_daily": """AppExceptions
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize exceptions=count() by day=bin(TimeGenerated,1d), role=tostring(AppRoleName)
| order by day asc""",
    "traces_daily": """AppTraces
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize traces=count(),
    estimated_billed_bytes=sum(tolong(column_ifexists("_BilledSize",0)))
  by day=bin(TimeGenerated,1d), role=tostring(AppRoleName), severity_level=tostring(SeverityLevel)
| order by day asc""",
    "custom_metrics_daily": """AppMetrics
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize records=count(), metric_value_sum=sum(todouble(column_ifexists("Sum",0)))
  by day=bin(TimeGenerated,1d), role=tostring(AppRoleName), metric=tostring(Name)
| order by day asc""",
    "console_logs_daily": """ContainerAppConsoleLogs_CL
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize log_entries=count(),
    error_indicators=countif(tostring(column_ifexists("Log_s","")) has_any ("error","exception","failed")),
    estimated_billed_bytes=sum(tolong(column_ifexists("_BilledSize",0)))
  by day=bin(TimeGenerated,1d), app=tostring(column_ifexists("ContainerAppName_s","unknown"))
| order by day asc""",
    "system_logs_daily": """ContainerAppSystemLogs_CL
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize system_events=count(),
    estimated_billed_bytes=sum(tolong(column_ifexists("_BilledSize",0)))
  by day=bin(TimeGenerated,1d), app=tostring(column_ifexists("ContainerAppName_s","unknown"))
| order by day asc""",
    "ingestion_daily": """Usage
| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})
| summarize reported_quantity=sum(Quantity)
  by day=bin(TimeGenerated,1d), data_type=tostring(DataType), unit=tostring(QuantityUnit)
| order by day asc""",
}


class AzureCommandError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def az(*parts: str, body: dict | None = None, timeout: int = 100) -> Any:
    cmd = ["az", *parts, "--only-show-errors", "--output", "json"]
    if body is not None:
        cmd += ["--body", json.dumps(body, separators=(",", ":"))]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AzureCommandError("timeout") from exc
    if result.returncode:
        # Azure CLI errors may contain tokens, resource paths and query text.
        # Only sanitized categories are ever emitted into the artifact/logs.
        msg = (result.stderr or "").lower()
        if "authorization" in msg or "forbidden" in msg or "permission" in msg:
            code = "permission_denied"
        elif "not found" in msg or "resourcenotfound" in msg:
            code = "not_found"
        elif "too many" in msg or "throttl" in msg or "429" in msg:
            code = "rate_limited"
        elif "table" in msg or "semantic" in msg or "failed to resolve" in msg:
            code = "table_or_schema_unavailable"
        else:
            code = "azure_query_failed"
        raise AzureCommandError(code)
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise AzureCommandError("invalid_response") from exc


def write_csv(path: Path, rows: list[dict], default_fields: list[str]) -> None:
    fields = list(dict.fromkeys(default_fields + [key for row in rows for key in row]))
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def status(states: list[dict], source: str, state: str, rows: int = 0, detail: str = "") -> None:
    states.append({"source": source, "status": state, "rows": rows, "detail": detail})


def log_queries(workspace_guid: str, start: str, end: str, dest: Path, states: list[dict]) -> None:
    for name, template in LOG_QUERIES.items():
        query = template.format(start=start, end=end)
        try:
            raw = az("monitor", "log-analytics", "query", "--workspace", workspace_guid,
                     "--analytics-query", query, "--timespan", "P31D", timeout=120)
            # Depending on Azure CLI version, query results may be a list or a tables object.
            if isinstance(raw, list):
                rows = raw
            elif isinstance(raw, dict) and "tables" in raw:
                table = (raw.get("tables") or [{}])[0]
                cols = [col.get("name") for col in table.get("columns", [])]
                rows = [dict(zip(cols, values)) for values in table.get("rows", [])]
            else:
                rows = []
            rows = [r for r in rows if isinstance(r, dict)]
            write_csv(dest / f"{name}.csv", rows, ["day"])
            status(states, f"kql:{name}", "ok" if rows else "no_rows", len(rows))
        except AzureCommandError as exc:
            write_csv(dest / f"{name}.csv", [], ["day"])
            status(states, f"kql:{name}", exc.code)


def workspace_for_group(resource_group: str) -> str | None:
    workspaces = az("monitor", "log-analytics", "workspace", "list", "-g", resource_group)
    if not isinstance(workspaces, list) or not workspaces:
        return None
    # Refuse to silently combine different workspaces (and potentially other products).
    if len(workspaces) != 1:
        raise AzureCommandError("multiple_workspaces_require_explicit_selection")
    return workspaces[0].get("customerId")


def export_metrics(resources: list[dict], start: str, end: str, dest: Path, states: list[dict]) -> None:
    rows: list[dict] = []
    catalog: list[dict] = []
    for resource in resources:
        resource_id = resource.get("id", "")
        resource_type = str(resource.get("type", "")).lower()
        category = RESOURCE_TYPES.get(resource_type)
        if not category:
            continue
        resource_name = str(resource.get("name", ""))
        try:
            definitions = az("monitor", "metrics", "list-definitions", "--resource", resource_id, timeout=75)
        except AzureCommandError as exc:
            status(states, f"metrics_catalog:{resource_name}", exc.code)
            continue
        if not isinstance(definitions, list):
            status(states, f"metrics_catalog:{resource_name}", "invalid_response")
            continue
        candidates = []
        for definition in definitions:
            name = str((definition.get("name") or {}).get("value", ""))
            if not name or not any(word in name.lower() for word in METRIC_WORDS):
                continue
            aggregates = definition.get("supportedAggregationTypes") or []
            preferred = ("Total", "Average", "Maximum") if any(
                word in name.lower() for word in COUNTER_WORDS
            ) else ("Average", "Maximum", "Total")
            aggregation = next((a for a in preferred if a in aggregates), None)
            if aggregation:
                candidates.append((name, aggregation, definition.get("unit", "")))
        # Prevent overly expensive or high-cardinality broad collection.
        candidates = sorted(candidates, key=lambda item: item[0].lower())[:16]
        for name, aggregation, unit in candidates:
            catalog.append({"resource": resource_name, "category": category,
                            "metric": name, "aggregation": aggregation, "unit": unit})
            try:
                data = az("monitor", "metrics", "list", "--resource", resource_id,
                          "--metrics", name, "--aggregation", aggregation,
                          "--start-time", start, "--end-time", end,
                          "--interval", "PT1H", timeout=100)
                count = 0
                for metric in (data or {}).get("value", []):
                    for series_index, series in enumerate(metric.get("timeseries", [])):
                        for sample in series.get("data", []):
                            value = sample.get(aggregation.lower())
                            if value is not None:
                                rows.append({
                                    "resource": resource_name,
                                    "category": category,
                                    "metric": name,
                                    "timestamp_utc": sample.get("timeStamp"),
                                    "aggregation": aggregation,
                                    "value": value,
                                    "unit": unit,
                                    "series_index": series_index,
                                })
                                count += 1
                status(states, f"metric:{resource_name}:{name}",
                       "ok" if count else "no_rows", count)
            except AzureCommandError as exc:
                status(states, f"metric:{resource_name}:{name}", exc.code)
    write_csv(dest / "azure_metrics_hourly.csv", rows, [
        "resource", "category", "metric", "timestamp_utc",
        "aggregation", "value", "unit", "series_index"
    ])
    write_csv(dest / "azure_metrics_catalog.csv", catalog, [
        "resource", "category", "metric", "aggregation", "unit"
    ])


def export_costs(subscription: str, resource_group: str, start: str, end: str,
                 dest: Path, states: list[dict]) -> None:
    scope = f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
    endpoint = ("https://management.azure.com" + scope +
                "/providers/Microsoft.CostManagement/query?api-version=2025-03-01")
    period = {"from": start, "to": end}
    for name, groupby in (
        ("cost_by_resource_daily", "ResourceId"),
        ("cost_by_service_daily", "ServiceName"),
    ):
        query = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": period,
            "dataset": {
                "granularity": "Daily",
                "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
                "grouping": [{"type": "Dimension", "name": groupby}],
            },
        }
        rows: list[dict] = []
        next_url = endpoint
        try:
            for _ in range(20):
                if not next_url.startswith("https://management.azure.com/"):
                    raise AzureCommandError("unexpected_pagination_url")
                response = az("rest", "--method", "post", "--url", next_url,
                              body=query, timeout=120)
                if not isinstance(response, dict):
                    break
                properties = response.get("properties") or {}
                columns = [x.get("name", "") for x in properties.get("columns", [])]
                for raw in properties.get("rows", []):
                    row = dict(zip(columns, raw))
                    # Never expose subscription/resource-group paths in a public artifact.
                    if "ResourceId" in row:
                        row["Resource"] = str(row.pop("ResourceId") or "").rsplit("/", 1)[-1]
                    rows.append(row)
                next_url = properties.get("nextLink")
                if not next_url:
                    break
            else:
                status(states, f"cost:{name}", "pagination_limit", len(rows))
            write_csv(dest / (name + ".csv"), rows, ["UsageDate", "Currency", "PreTaxCost"])
            status(states, f"cost:{name}", "ok" if rows else "no_rows", len(rows))
        except AzureCommandError as exc:
            write_csv(dest / (name + ".csv"), rows, ["UsageDate", "Currency", "PreTaxCost"])
            status(states, f"cost:{name}", exc.code, len(rows),
                   "Cost Management Reader or equivalent billing access may be required.")


def export_get_jobs(resource_group: str, start: dt.datetime, dest: Path,
                    states: list[dict]) -> None:
    rows: list[dict] = []
    try:
        jobs = az("containerapp", "job", "list", "-g", resource_group, timeout=90)
    except AzureCommandError as exc:
        write_csv(dest / "job_executions.csv", [], [
            "job", "started_utc", "ended_utc", "duration_seconds", "status"
        ])
        status(states, "get_jobs", exc.code)
        return
    if not isinstance(jobs, list):
        status(states, "get_jobs", "invalid_response")
        return
    for job in jobs:
        name = str(job.get("name", ""))
        if not re.search(r"(get|grader|worker)", name, re.IGNORECASE):
            continue
        try:
            executions = az("containerapp", "job", "execution", "list",
                            "-g", resource_group, "-n", name, timeout=100)
            count = 0
            for execution in executions or []:
                props = execution.get("properties") or {}
                started = str(props.get("startTime") or "")
                try:
                    started_at = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if started_at < start:
                        continue
                except ValueError:
                    continue
                ended = str(props.get("endTime") or "")
                duration = ""
                try:
                    if ended:
                        ended_at = dt.datetime.fromisoformat(ended.replace("Z", "+00:00"))
                        duration = round((ended_at - started_at).total_seconds(), 2)
                except ValueError:
                    pass
                rows.append({"job": name, "started_utc": started, "ended_utc": ended,
                             "duration_seconds": duration,
                             "status": str(props.get("status") or "unknown")})
                count += 1
            status(states, f"job:{name}", "ok" if count else "no_rows", count,
                   "Execution API listing may have limited historical coverage.")
        except AzureCommandError as exc:
            status(states, f"job:{name}", exc.code)
    write_csv(dest / "job_executions.csv", rows, [
        "job", "started_utc", "ended_utc", "duration_seconds", "status"
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", choices=["dev"], required=True)
    parser.add_argument("--days", choices=[7, 14, 30], type=int, default=30)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    dest = Path(args.output)
    dest.mkdir(parents=True, exist_ok=True)
    end = dt.datetime.now(UTC).replace(microsecond=0)
    start = end - dt.timedelta(days=args.days)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    rg = "rg-coderats-base-dev"
    states: list[dict] = []
    account = az("account", "show")
    subscription = str(account.get("id", ""))
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", subscription):
        raise RuntimeError("Azure subscription was not verified.")
    # Hard fail on absent resource group: a report on the wrong Azure scope is misleading.
    group = az("group", "show", "-n", rg)
    if str(group.get("name", "")).lower() != rg.lower():
        raise RuntimeError("Unexpected Azure resource group.")
    resources = az("resource", "list", "-g", rg)
    if not isinstance(resources, list):
        raise RuntimeError("Azure resource list unavailable.")
    workspace_guid = None
    try:
        workspace_guid = workspace_for_group(rg)
        if workspace_guid:
            log_queries(workspace_guid, start_str, end_str, dest, states)
        else:
            status(states, "log_analytics_workspace", "not_found")
    except AzureCommandError as exc:
        status(states, "log_analytics_workspace", exc.code)
    export_metrics(resources, start_str, end_str, dest, states)
    export_costs(subscription, rg, start_str, end_str, dest, states)
    export_get_jobs(rg, start, dest, states)
    write_csv(dest / "source_coverage.csv", states,
              ["source", "status", "rows", "detail"])
    report = {
        "environment": args.environment,
        "resource_group": rg,
        "window_start_utc": start_str,
        "window_end_utc": end_str,
        "requested_days": args.days,
        "resource_count": len(resources),
        "log_analytics_workspace_detected": bool(workspace_guid),
        "sources": {
            "ok": sum(s["status"] == "ok" for s in states),
            "no_rows": sum(s["status"] == "no_rows" for s in states),
            "unavailable": sum(s["status"] not in ("ok", "no_rows") for s in states),
        },
        "limitations": [
            "No raw log messages, PII, request URLs, token values or source code exported.",
            "No rows does not prove zero activity; instrumentation, retention or permissions may be missing.",
            "Cost Management permission and billing data lag can prevent or delay cost results.",
            "Azure Monitor metrics are hourly aggregate samples; not all resources expose every metric.",
            "Job executions may not cover the full window; they are not equivalent to GET check-ins.",
            "User count and external AI/GitHub billing cannot be inferred from Azure metrics.",
        ],
    }
    (dest / "manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = [
        "# CodeRats — Azure infrastructure and observability export",
        "",
        f"Environment: {args.environment}",
        f"Window UTC: {start_str} → {end_str}",
        f"Azure resources discovered: {len(resources)}",
        "",
        "## Source availability",
        "",
        "| Status | Count |",
        "| --- | ---: |",
        *[f"| {key} | {value} |" for key, value in report["sources"].items()],
        "",
        "The artifact includes CSV files for available KQL datasets, hourly Azure Monitor "
        "metric samples, discovered metric catalog, GET job executions, resource/service "
        "daily costs (if permitted), source coverage and this manifest.",
        "",
        "**Zero rows != zero usage.** Absence can mean no instrumentation, retention "
        "expiry, missing access or nonexistent telemetry. AI provider invoices are separate.",
    ]
    (dest / "README.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"Export finished: {len(resources)} Azure resources; "
          f"{report['sources']['ok']} sources with data; "
          f"{report['sources']['no_rows']} empty; "
          f"{report['sources']['unavailable']} unavailable.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AzureCommandError, RuntimeError) as exc:
        # Hard failures still cannot print Azure CLI stderr or credentials.
        print(f"Export cannot start safely: {str(exc)}", file=sys.stderr)
        sys.exit(1)
