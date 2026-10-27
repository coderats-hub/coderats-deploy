#!/usr/bin/env python3
"""Summarize availability of App Insights and Container Apps log tables; never fetch log records."""
import json
import os
import subprocess

RG = "rg-coderats-base-dev"
TABLES = ("AppRequests", "AppDependencies", "AppMetrics", "AppExceptions",
          "ContainerAppConsoleLogs_CL", "ContainerAppConsoleLogs",
          "ContainerAppSystemLogs_CL", "ContainerAppSystemLogs")
def az(*args):
    p = subprocess.run(["az", *args, "-o", "json"], text=True,
                       capture_output=True, check=False, timeout=45)
    if p.returncode:
        return None
    try:
        return json.loads(p.stdout)
    except (ValueError, TypeError):
        return None

def main():
    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID")
    account = az("account", "show")
    if not subscription or not account or account["id"].lower() != subscription.lower():
        raise SystemExit("Wrong or missing Azure subscription")
    workspaces = az("monitor", "log-analytics", "workspace", "list", "-g", RG)
    if not isinstance(workspaces, list) or len(workspaces) != 1:
        raise SystemExit("Cannot safely identify single DEV workspace")
    customer_id = workspaces[0].get("customerId")
    if not customer_id:
        raise SystemExit("DEV workspace has no queryable customer ID")
    print("DEV Log Analytics ingestion summary for last 24h (aggregate counts only):")
    for table in TABLES:
        data = az("monitor", "log-analytics", "query",
                  "--workspace", customer_id,
                  "--analytics-query", f"{table} | where TimeGenerated > ago(24h) | summarize Total=count()")
        if isinstance(data, list) and data and isinstance(data[0], dict) and "Total" in data[0]:
            print(f"  {table}: {data[0]['Total']}")
        else:
            print(f"  {table}: UNAVAILABLE (not necessarily zero; missing table or query permission)")

if __name__ == "__main__":
    main()
