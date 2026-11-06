#!/usr/bin/env python3
"""Central polling of private source branches/PRs; no code output or unsafe refs.

Authentication:
 - PRIVATE_READ_TOKEN is a GitHub App installation token, contents/read and pulls/read.
 - CENTRAL_TOKEN is the central repo's GITHUB_TOKEN, actions/write.
The state is repository Actions Variables (public SHA identifiers only).
No source workflows, webhook receiver, PATs or private logging.
"""
from __future__ import annotations
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

SERVICES = {
    "web": "web-coderats-base",
    "api": "api-coderats-base",
    "grader": "api-grader-coderats",
}
BRANCHES = ("develop", "main")
SOURCE_OWNER = "coderats-hub"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def source_repo(service: str) -> str:
    return f"{SOURCE_OWNER}/{SERVICES[service]}"


def variable_name(service: str, branch: str) -> str:
    return f"CODERATS_LAST_{service.upper()}_{branch.upper()}"


def changed_commits(old: str, new: str, compare: dict) -> list[str]:
    """Reject force-pushes or incomplete comparison results, preserve commit order."""
    if not SHA_PATTERN.fullmatch(old) or not SHA_PATTERN.fullmatch(new):
        raise ValueError("Invalid source SHA")
    if old == new:
        return []
    if compare.get("status") != "ahead":
        raise ValueError("Source branch is no longer a fast-forward; manual reconciliation required")
    commits = [entry.get("sha", "") for entry in compare.get("commits", [])]
    if not commits or not all(SHA_PATTERN.fullmatch(x) for x in commits):
        raise ValueError("Source comparison returned no valid commit list")
    if compare.get("ahead_by") != len(commits):
        raise ValueError("Comparison truncated (more than API returned); manual reconciliation required")
    if commits[-1] != new:
        raise ValueError("Comparison does not end at current branch HEAD")
    return commits


def route_event(service: str, branch: str) -> str:
    """No unconfigured prod deploys: main goes to validation, develop to release."""
    if service not in SERVICES or branch not in BRANCHES:
        raise ValueError("Unexpected service/branch")
    if branch == "main":
        return "validate-private-source.yml"
    return "deploy-grader-dev.yml" if service == "grader" else "deploy-private-dev.yml"


class API:
    def __init__(self, central_token: str, private_token: str, central_repo: str):
        self.central_token = central_token
        self.private_token = private_token
        self.central_repo = central_repo

    def call(self, path: str, *, private: bool = False, method: str = "GET", data=None,
             allow_not_found: bool = False):
        token = self.private_token if private else self.central_token
        url = "https://api.github.com/" + path.lstrip("/")
        body = json.dumps(data).encode() if data is not None else None
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "coderats-central-watch/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, headers=headers, method=method, data=body)
        try:
            with urlopen(request, timeout=25) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            if allow_not_found and error.code == 404:
                return None
            # Never log a raw API response body: GitHub payloads may contain private data.
            raise RuntimeError(f"GitHub API {method} failed with HTTP {error.code}") from None

    def state(self, name: str):
        row = self.call(f"repos/{self.central_repo}/actions/variables/{name}",
                        allow_not_found=True)
        return None if row is None else row["value"]

    def save(self, name: str, sha: str):
        if not SHA_PATTERN.fullmatch(sha):
            raise ValueError("Invalid source SHA for state")
        current = self.state(name)
        if current is None:
            self.call(f"repos/{self.central_repo}/actions/variables",
                      method="POST", data={"name": name, "value": sha})
        else:
            self.call(f"repos/{self.central_repo}/actions/variables/{name}",
                      method="PATCH", data={"name": name, "value": sha})

    def dispatch(self, workflow: str, service: str, sha: str):
        if not SHA_PATTERN.fullmatch(sha):
            raise ValueError("Invalid source SHA for dispatch")
        if workflow == "deploy-private-dev.yml":
            inputs = {"service": service, "source_sha": sha, "confirmation": "AUTO_DEPLOY_DEV"}
        elif workflow == "deploy-grader-dev.yml":
            inputs = {"source_sha": sha, "confirmation": "AUTO_DEPLOY_DEV"}
        elif workflow == "validate-private-source.yml":
            inputs = {"service": service, "source_sha": sha}
        else:
            raise ValueError("Unapproved central workflow")
        self.call(f"repos/{self.central_repo}/actions/workflows/{workflow}/dispatches",
                  method="POST", data={"ref": "main", "inputs": inputs})


def scan_branches(api: API, enabled: bool):
    for service in SERVICES:
        repo = source_repo(service)
        for branch in BRANCHES:
            info = api.call(f"repos/{repo}/branches/{branch}", private=True,
                            allow_not_found=True)
            if info is None:
                # A repo may not have a develop branch; do not synthesize it.
                continue
            sha = info["commit"]["sha"]
            if not SHA_PATTERN.fullmatch(sha):
                raise ValueError("Bad GitHub branch SHA")
            key = variable_name(service, branch)
            previous = api.state(key)
            if previous is None:
                api.save(key, sha)     # First run establishes baseline, no retroactive release.
                print(f"Baseline established for {service}/{branch}.")
                continue
            if previous == sha:
                continue
            if not enabled:
                api.save(key, sha)
                print(f"Tracking disabled release {service}/{branch} (audit only).")
                continue
            comp = api.call(f"repos/{repo}/compare/{previous}...{sha}", private=True)
            commits = changed_commits(previous, sha, comp)
            workflow = route_event(service, branch)
            # In this order: older runs fail closed against current branch HEAD
            # in the deployment workflow, latest SHA is the only releasable one.
            for new_sha in commits:
                api.dispatch(workflow, service, new_sha)
            api.save(key, sha)      # Save only after every dispatch was accepted.
            print(f"Queued {len(commits)} central events for {service}/{branch}.")


def scan_prs(api: API, enabled: bool):
    for service in SERVICES:
        repo = source_repo(service)
        page = 1
        while True:
            pulls = api.call(f"repos/{repo}/pulls?state=open&per_page=100&page={page}",
                             private=True)
            if not isinstance(pulls, list):
                raise ValueError("Private PR API returned unexpected payload")
            for pr in pulls:
                base = pr.get("base", {}).get("ref")
                head = pr.get("head", {})
                if base not in BRANCHES or head.get("repo", {}).get("full_name") != repo:
                    continue  # No fork code in central environment.
                sha = head.get("sha", "")
                if not SHA_PATTERN.fullmatch(sha):
                    raise ValueError("Unexpected PR SHA")
                number = pr.get("number")
                if not isinstance(number, int) or number <= 0:
                    raise ValueError("Unexpected PR number")
                key = f"CODERATS_PR_{service.upper()}_{number}"
                previous = api.state(key)
                if previous is None or previous != sha:
                    if enabled:
                        api.dispatch("validate-private-source.yml", service, sha)
                    api.save(key, sha)
                    if enabled:
                        print(f"Queued central PR validation for {service} PR #{number}.")
            if len(pulls) < 100:
                break
            page += 1
            if page > 20:
                raise ValueError("Too many open pull requests; manual review required")


def main():
    central_token = os.environ["CENTRAL_TOKEN"]
    private_token = os.environ["PRIVATE_READ_TOKEN"]
    central_repo = os.environ.get("CENTRAL_REPO")
    if central_repo != "coderats-hub/coderats-deploy":
        raise ValueError("Refusing to execute outside CodeRats central repo")
    enabled = os.environ.get("ENABLE_AUTO", "").lower() == "true"
    api = API(central_token, private_token, central_repo)
    if not enabled:
        print("Central auto-release flag is disabled; baselines only.")
    scan_branches(api, enabled=enabled)
    scan_prs(api, enabled=enabled)
    print("Private source event scan finished; no private content was printed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, ValueError) as err:
        print("::error::Source watcher failed:", str(err))
        sys.exit(1)
