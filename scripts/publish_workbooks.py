#!/usr/bin/env python3
"""Publish CodeRats Azure Workbooks via OIDC-authenticated Azure CLI.
No Terraform state changes; idempotent PUTs, scoped to the verified DEV resource group.
"""
import json, os, pathlib, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
MANIFEST=json.loads((ROOT/"observability/workbooks/manifest.json").read_text())
PLACEHOLDER="__LOG_ANALYTICS_WORKSPACE_ID__"
def az(*args):
    p=subprocess.run(["az",*args,"-o","json"],capture_output=True,text=True,check=True)
    return json.loads(p.stdout)
def main():
    rg=os.environ.get("CODERATS_AZURE_RESOURCE_GROUP","rg-coderats-base-dev")
    if rg!="rg-coderats-base-dev": sys.exit("Only the known DEV resource group is permitted")
    subscription=os.environ.get("AZURE_SUBSCRIPTION_ID","")
    if not subscription: sys.exit("AZURE_SUBSCRIPTION_ID is required")
    account=az("account","show")
    if account["id"].lower()!=subscription.lower(): sys.exit("Wrong Azure subscription")
    workspaces=az("monitor","log-analytics","workspace","list","-g",rg)
    if len(workspaces)!=1: sys.exit(f"Expected exactly one DEV Log Analytics workspace; found {len(workspaces)}")
    ws_id=workspaces[0]["id"]
    api="2023-06-01"
    for spec in MANIFEST["workbooks"]:
        template=(ROOT/"observability/workbooks"/(spec["file"]+".json")).read_text()
        if PLACEHOLDER not in template: sys.exit("Template missing workspace placeholder")
        notebook=json.loads(template.replace(PLACEHOLDER,ws_id))
        resource=f"/subscriptions/{subscription}/resourceGroups/{rg}/providers/Microsoft.Insights/workbooks/{spec['guid']}"
        payload={"location":workspaces[0]["location"],"kind":"shared","properties":{"displayName":spec["displayName"],"category":"workbook","serializedData":json.dumps(notebook,separators=(",",":")),"sourceId":ws_id},"tags":{"app":"coderats","environment":"dev","managed-by":"coderats-deploy"}}
        with tempfile.NamedTemporaryFile(mode="w",suffix=".json") as f:
            json.dump(payload,f);f.flush()
            az("rest","--method","put","--url",f"https://management.azure.com{resource}?api-version={api}","--body","@"+f.name)
        print("Workbook deployed:",spec["displayName"])
if __name__=="__main__": main()
