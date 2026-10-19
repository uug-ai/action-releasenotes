#!/usr/bin/env python3
"""
Generate release notes by comparing releases across multiple repositories using AI.
"""
import sys
import requests
import argparse
import json
import os
import re
from datetime import datetime
import openai
from openai import AzureOpenAI

SAMPLE_PROMPT = """
Write a brief summary of the key changes in this release. Be concise, complete, and avoid redundancy. Each bullet point should cover a distinct change.

The release comparison is between "v1.0.0" and "v1.1.0" for repository "example/repo" and the following changes took place:

Changes in file src/main.py: @@ -10,6 +10,10 @@ def main():
     print("Starting application")
+    # Added new logging feature
+    setup_logging()
+    logger.info("Application started")
     run_app()

Changes in file src/config.py: @@ -5,3 +5,8 @@ DEFAULT_CONFIG = {
     "debug": False,
+    "log_level": "INFO",
+    "log_file": "app.log",
 }
"""

GOOD_SAMPLE_RESPONSE = """
- Added comprehensive logging system with configurable log levels and file output
- Enhanced application startup with proper initialization sequence
- New configuration options: `log_level` and `log_file`
"""

TEST_PLAN_PROMPT = """
You are a QA engineer creating a brief test plan for a front-end application.
Based on the application context and code changes (diff), list only the key test scenarios.

Rules:
- Keep it SHORT and CONCISE
- NO title, NO headers - start directly with the bullet list
- Only include test scenarios directly related to the changes
- Use a simple bullet list with checkboxes
- Each test case: one line with action → expected result
- Maximum 10 test cases total
"""

TEST_PLAN_SAMPLE_PROMPT = """
Create a test plan based on the following:

## Application Context:
The application is a simple dashboard with:
- Login page: Users can log in with email/password
- Dashboard page: Shows user statistics and recent activity

## Code Changes:
Changes in file src/components/Login.tsx: @@ -15,6 +15,10 @@ 
     const handleLogin = async () => {
+      if (password.length < 8) {
+        setError("Password must be at least 8 characters");
+        return;
+      }
       await loginUser(email, password);
"""

TEST_PLAN_SAMPLE_RESPONSE = """
- [ ] Login with password < 8 chars → Should show error "Password must be at least 8 characters"
- [ ] Login with password = 8 chars → Should proceed with login
- [ ] Login with valid credentials → Should redirect to dashboard
- [ ] Error clears when typing valid password → Error message should disappear
"""

COMPLETION_PROMPT = """
Write a brief summary of the release changes.
Focus on the most important changes: key new features, significant improvements, and critical bug fixes.
Be concise - each bullet point should be one line and cover a distinct change without overlapping with others.
Be complete but not extensive. Do NOT include section headers, just bullet points.
Go straight to the point. The following changes took place:
"""

PR_SAMPLE_PROMPT = """
Write a brief summary of the key changes in this release. Be concise, complete, and avoid redundancy. Each bullet point should cover a distinct change.

Repository: example/repo
Comparing: v1.0.0 → v1.1.0

### PR #41: Add configurable log levels
Labels: enhancement
Areas: src/logging (3), src/config (1)
Description:
- Adds `log_level` and `log_file` settings so operators can tune verbosity and write logs to disk.
- Defaults to INFO; existing deployments keep their current output.

### PR #43: Retry uploads after transient storage errors
Areas: src/upload (2)
Description:
- Uploads that fail with a 5xx from storage are retried up to three times with backoff instead of being dropped.

### PR #44: Refactor upload tests
Areas: src/upload (4)
Description:
- Moves upload fixtures into a shared helper. No behavior change.
"""

PR_GOOD_SAMPLE_RESPONSE = """
- Added configurable log levels and optional log-file output, defaulting to the existing behavior (#41)
- Uploads now retry automatically after temporary storage errors instead of failing (#43)
"""

PR_CITATION_RULES = """
The changes below are listed as merged pull requests. Rules:
- End every bullet with the pull request number(s) it is based on, e.g. "(#123)" or "(#123, #130)".
- Only cite pull request numbers that appear below.
- Combine related pull requests into one bullet; leave out pull requests with no user-visible effect.
- Describe only what the titles and descriptions support. Do not speculate.
"""


def get_compare_diff(github_api_url: str, repo: str, from_release: str, to_release: str, 
                     authorization_header: dict) -> tuple[str, dict]:
    """
    Get the diff between two releases/tags for a repository.
    Returns the diff content and statistics.
    """
    compare_url = f"{github_api_url}/repos/{repo}/compare/{from_release}...{to_release}"
    
    print(f"Fetching comparison for {repo}: {from_release} -> {to_release}")
    
    response = requests.get(compare_url, headers=authorization_header)
    
    if response.status_code != requests.codes.ok:
        print(f"Failed to get comparison for {repo}: {response.status_code}")
        print(f"Response: {response.text}")
        return None, None
    
    compare_data = response.json()
    
    # Extract statistics
    stats = {
        "total_commits": len(compare_data.get("commits", [])),
        "files_changed": len(compare_data.get("files", [])),
        "additions": compare_data.get("total_commits", 0),
        "deletions": 0,
    }
    
    # Calculate additions/deletions from files
    total_additions = 0
    total_deletions = 0
    
    diff_content = f"\n### Repository: {repo}\n"
    diff_content += f"**Comparing:** {from_release} → {to_release}\n\n"
    
    # File patterns to exclude (non-business code)
    excluded_patterns = [
        '.github/',           # GitHub Actions, workflows
        '.devcontainer/',     # Dev container configurations
        'Dockerfile',         # Container images
        'docker-compose',     # Docker compose files
        '.dockerignore',      # Docker ignore files
        'Jenkinsfile',        # Jenkins pipelines
        '.gitlab-ci',         # GitLab CI
        '.travis',            # Travis CI
        'azure-pipelines',    # Azure DevOps
        '.circleci/',         # CircleCI
        'bitbucket-pipelines', # Bitbucket pipelines
        'Makefile',           # Build automation
        '.yml',               # Generic YAML configs (often CI/CD)
        '.yaml',              # Generic YAML configs (often CI/CD)
    ]
    
    # File extensions to always include (business code)
    included_extensions = [
        '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs', '.rb', '.php',
        '.cs', '.cpp', '.c', '.h', '.hpp', '.swift', '.kt', '.scala', '.vue',
        '.sql', '.graphql', '.proto', '.json', '.xml', '.html', '.css', '.scss',
        '.txt', '.md', '.rst',  # Documentation and text files
    ]
    
    def is_icon_file(filename: str) -> bool:
        """Check if file is an icon or image asset."""
        filename_lower = filename.lower()
        
        # Specific icon files
        icon_files = ['icons.js', 'icons.ts', 'icons.jsx', 'icons.tsx']
        if any(filename_lower.endswith(f) for f in icon_files):
            return True
        
        # Image asset files
        icon_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp']
        for ext in icon_extensions:
            if filename_lower.endswith(ext):
                return True
        
        return False
    
    def is_helm_chart_file(filename: str) -> bool:
        """Check if file is part of a Helm chart."""
        filename_lower = filename.lower()
        helm_patterns = [
            'chart.yaml', 'chart.yml',
            'values.yaml', 'values.yml',
            '/templates/',
            '/charts/',
            'helmfile.yaml', 'helmfile.yml',
        ]
        return any(pattern in filename_lower for pattern in helm_patterns)
    
    def should_include_file(filename: str) -> bool:
        """Check if file should be included in release notes."""
        filename_lower = filename.lower()
        
        # Exclude files matching excluded patterns
        for pattern in excluded_patterns:
            if pattern.lower() in filename_lower:
                return False
        
        # Include files with business code extensions
        for ext in included_extensions:
            if filename_lower.endswith(ext):
                return True
        
        # Also include icon files and helm chart files
        if is_icon_file(filename) or is_helm_chart_file(filename):
            return True
        
        # Exclude other files by default
        return False
    
    files = compare_data.get("files", [])
    
    def extract_new_icons_from_diff(patch: str) -> list:
        """Extract only newly added icon names from icons.js diff."""
        new_icons = []
        for line in patch.split('\n'):
            # Only look at added lines (starting with +, but not ++)
            if line.startswith('+') and not line.startswith('++'):
                # Common patterns for icon definitions:
                # export const iconName = ...
                # iconName: ...
                # "iconName": ...
                # 'iconName': ...
                import re
                # Match export const/let/var iconName
                match = re.search(r'export\s+(?:const|let|var)\s+(\w+)', line)
                if match:
                    new_icons.append(match.group(1))
                    continue
                # Match object key patterns: iconName: or "iconName": or 'iconName':
                match = re.search(r'["\']?(\w+(?:-\w+)*)["\']?\s*:\s*[\'"`<{]', line)
                if match:
                    icon_name = match.group(1)
                    # Filter out common non-icon keys
                    if icon_name not in ['viewBox', 'fill', 'stroke', 'width', 'height', 'd', 'path', 'xmlns', 'class', 'style']:
                        new_icons.append(icon_name)
        return new_icons

    # Track special file changes for user awareness
    icon_changes = []
    new_icons_added = []  # Track newly added icon names
    helm_chart_changes = []
    
    for file_info in files:
        filename = file_info.get("filename", "unknown")
        patch = file_info.get("patch", "")
        
        # Track icon changes and extract new icons
        if is_icon_file(filename):
            icon_changes.append(filename)
            # For icons.js/ts files, extract newly added icon names
            if filename.lower().endswith(('.js', '.ts', '.jsx', '.tsx')):
                new_icons = extract_new_icons_from_diff(patch)
                new_icons_added.extend(new_icons)
        
        # Track helm chart changes
        if is_helm_chart_file(filename):
            helm_chart_changes.append(filename)
        
        # Skip non-business code files
        if not should_include_file(filename):
            continue
        
        status = file_info.get("status", "modified")
        additions = file_info.get("additions", 0)
        deletions = file_info.get("deletions", 0)
        
        total_additions += additions
        total_deletions += deletions
        
        # For icon files, only show summary of new icons instead of full diff
        if is_icon_file(filename) and filename.lower().endswith(('.js', '.ts', '.jsx', '.tsx')):
            new_icons = extract_new_icons_from_diff(patch)
            if new_icons:
                diff_content += f"Changes in file {filename} ({status}, +{additions}/-{deletions}): New icons added: {', '.join(new_icons)}\n"
            else:
                diff_content += f"Changes in file {filename} ({status}, +{additions}/-{deletions}): Icon content updated (no new icons added)\n"
        elif patch:
            diff_content += f"Changes in file {filename} ({status}, +{additions}/-{deletions}): {patch}\n"
    
    # Add notes about special file changes that require user attention
    if icon_changes or helm_chart_changes:
        diff_content += "\n### Additional Updates Required:\n"
        if icon_changes:
            diff_content += f"\n**Icon/Image changes detected** (may require asset updates):\n"
            for icon_file in icon_changes:
                diff_content += f"- {icon_file}\n"
            if new_icons_added:
                diff_content += f"\n**New icons added:** {', '.join(set(new_icons_added))}\n"
        if helm_chart_changes:
            diff_content += f"\n**Helm chart changes detected** (may require chart version updates):\n"
            for helm_file in helm_chart_changes:
                diff_content += f"- {helm_file}\n"
    
    stats["additions"] = total_additions
    stats["deletions"] = total_deletions
    stats["icon_changes"] = icon_changes
    stats["helm_chart_changes"] = helm_chart_changes
    
    # Also include commit messages as context
    commits = compare_data.get("commits", [])
    total_commits_count = compare_data.get("total_commits", len(commits))
    
    if commits:
        diff_content += "\nCommit messages:\n"
        for commit in commits:
            commit_message = commit.get("commit", {}).get("message", "").split("\n")[0]
            diff_content += f"- {commit_message}\n"
        
        # Note if there are more commits than returned (GitHub API limits to 250 per compare)
        if total_commits_count > len(commits):
            diff_content += f"\n(Note: Showing {len(commits)} of {total_commits_count} total commits)\n"
    
    return diff_content, stats


SKIPPED_PR_TITLE = re.compile(
    r"^(?:(?:chore|ci|build|test|tests|docs)(?:\([^)]*\))?!?:|bump\b|release\s+v?\d)",
    re.IGNORECASE,
)
SKIPPED_COMMIT_SUBJECT = re.compile(
    r"^(?:Merge (?:branch|remote-tracking branch|pull request)\b|bump\b|release\s+v?\d)",
    re.IGNORECASE,
)
# PR description sections that never matter to release readers, written as a
# heading ("### Tests"), a top-level bullet ("- Tests") or a bare label line.
INTERNAL_BODY_SECTION = (
    r"(?:live environment|implementation(?: details| notes)?|tests?|testing|test plan|"
    r"checklist|screenshots?|how to test)"
)
INTERNAL_BODY_LABEL = re.compile(
    rf"^(?:#{{1,6}}\s*|[-*]\s+)?\**{INTERNAL_BODY_SECTION}\**\s*:?\s*$", re.IGNORECASE
)
MARKDOWN_HEADING = re.compile(r"^#{1,6}\s")
TOP_LEVEL_BULLET = re.compile(r"^[-*]\s")
CITATION_GROUP = re.compile(
    r"\s*\(\s*((?:PRs?\s*)?#\d+(?:\s*(?:,|and)\s*(?:PRs?\s*)?#\d+)*)\s*\)\s*\.?\s*$", re.IGNORECASE
)

# generate_ai_summary truncates the whole prompt at 32000 characters; keep the
# pull request context below that so nothing is cut from the end.
PR_CONTEXT_BUDGET = 28000
MAX_PR_BODY = 4000
MIN_USEFUL_PR_BODY = 200
MAX_DIRECT_COMMITS = 40


def graphql_url(github_api_url: str) -> str:
    """Map the REST API URL to the GraphQL endpoint (github.com and GHES)."""
    url = github_api_url.rstrip("/")
    if url.endswith("/v3"):
        return url[:-len("v3")] + "graphql"
    return url + "/graphql"


def run_graphql(github_api_url: str, authorization_header: dict, query: str, variables: dict):
    response = requests.post(
        graphql_url(github_api_url),
        headers=authorization_header,
        json={"query": query, "variables": variables},
    )
    if response.status_code != requests.codes.ok:
        print(f"GraphQL request failed: {response.status_code}")
        print(f"Response: {response.text}")
        return None
    payload = response.json()
    if payload.get("errors"):
        print(f"GraphQL errors: {payload['errors']}")
    return payload.get("data")


def list_range_commits(github_api_url: str, repo: str, from_release: str, to_release: str,
                       authorization_header: dict):
    """List every commit between two refs, paging past the 250-commit compare limit."""
    compare_url = f"{github_api_url}/repos/{repo}/compare/{from_release}...{to_release}"
    commits = []
    seen = set()
    page = 1
    while True:
        response = requests.get(
            compare_url, headers=authorization_header, params={"per_page": 100, "page": page}
        )
        if response.status_code != requests.codes.ok:
            print(f"Failed to list commits for {repo}: {response.status_code}")
            print(f"Response: {response.text}")
            return None
        compare_data = response.json()
        batch = compare_data.get("commits", [])
        for commit in batch:
            if commit["sha"] not in seen:
                seen.add(commit["sha"])
                commits.append(commit)
        if not batch or len(commits) >= compare_data.get("total_commits", 0) or page >= 20:
            return commits
        page += 1


def find_merged_pull_requests(github_api_url: str, repo: str, commits: list,
                              authorization_header: dict):
    """
    Map the commits of a release range to the pull requests that merged them.
    A pull request belongs to the range when its merge commit is in the range,
    which covers merge, squash and rebase merges alike.
    Returns (pull request numbers, commits that did not arrive through a pull request).
    """
    owner, name = repo.split("/", 1)
    range_shas = {commit["sha"] for commit in commits}
    numbers = set()
    direct_commits = []

    for start in range(0, len(commits), 50):
        chunk = commits[start:start + 50]
        fields = "\n".join(
            f'c{index}: object(oid: "{commit["sha"]}") {{ ... on Commit {{ '
            f'associatedPullRequests(first: 10) {{ nodes {{ number mergeCommit {{ oid }} }} }} }} }}'
            for index, commit in enumerate(chunk)
        )
        query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
        data = run_graphql(github_api_url, authorization_header, query, {"owner": owner, "name": name})
        if data is None:
            return None, None
        repository = data.get("repository") or {}

        for index, commit in enumerate(chunk):
            nodes = ((repository.get(f"c{index}") or {}).get("associatedPullRequests") or {}).get("nodes") or []
            merged_here = [
                node["number"] for node in nodes
                if node and (node.get("mergeCommit") or {}).get("oid") in range_shas
            ]
            if merged_here:
                numbers.update(merged_here)
            elif len(commit.get("parents", [])) < 2:
                direct_commits.append(commit)

    return sorted(numbers), direct_commits


def fetch_pull_requests(github_api_url: str, repo: str, numbers: list, authorization_header: dict) -> list:
    """Fetch title, description, labels, touched paths and commit headlines for pull requests."""
    owner, name = repo.split("/", 1)
    pull_requests = []

    for start in range(0, len(numbers), 20):
        chunk = numbers[start:start + 20]
        fields = "\n".join(
            f"p{number}: pullRequest(number: {number}) {{ number title body url "
            f"author {{ login __typename }} labels(first: 20) {{ nodes {{ name }} }} "
            f"files(first: 100) {{ nodes {{ path }} }} "
            f"commits(first: 30) {{ nodes {{ commit {{ messageHeadline }} }} }} }}"
            for number in chunk
        )
        query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
        data = run_graphql(github_api_url, authorization_header, query, {"owner": owner, "name": name})
        if data is None:
            return None
        repository = data.get("repository") or {}

        for number in chunk:
            node = repository.get(f"p{number}")
            if not node:
                print(f"Warning: could not fetch PR #{number} in {repo}")
                continue
            pull_requests.append({
                "number": node["number"],
                "title": node.get("title") or "",
                "body": node.get("body") or "",
                "url": node.get("url") or "",
                "author": node.get("author") or {},
                "labels": [label["name"] for label in (node.get("labels") or {}).get("nodes") or []],
                "paths": [file["path"] for file in (node.get("files") or {}).get("nodes") or []],
                "commits": [
                    item["commit"]["messageHeadline"]
                    for item in (node.get("commits") or {}).get("nodes") or []
                ],
            })

    return pull_requests


def pull_request_skip_reason(pull_request: dict, exclude_labels: set):
    """Return why a pull request should stay out of the release notes, or None."""
    author = pull_request.get("author") or {}
    if author.get("__typename") == "Bot":
        return f"bot author {author.get('login')}"
    matched_labels = {label.lower() for label in pull_request["labels"]} & exclude_labels
    if matched_labels:
        return f"label {', '.join(sorted(matched_labels))}"
    if SKIPPED_PR_TITLE.match(pull_request["title"].strip()):
        return "title"
    return None


def clean_pull_request_body(body: str) -> str:
    """Strip comments, images and internal sections (preview links, implementation, tests) from a PR body."""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"<img\b[^>]*>", "", body, flags=re.IGNORECASE)

    # skip_scope records what opened the skipped section, and so what closes it:
    # a heading section runs to the next heading, a label section to the next
    # unindented paragraph, a bullet section to the next top-level bullet.
    kept = []
    skip_scope = None
    for line in body.replace("\r\n", "\n").splitlines():
        stripped = line.strip()
        internal = bool(INTERNAL_BODY_LABEL.match(stripped))
        if MARKDOWN_HEADING.match(stripped):
            skip_scope = "heading" if internal else None
        elif TOP_LEVEL_BULLET.match(line):
            if internal and skip_scope is None:
                skip_scope = "bullet"
            elif not internal and skip_scope == "bullet":
                skip_scope = None
        elif stripped and line == line.lstrip():
            if internal and skip_scope != "heading":
                skip_scope = "label"
            elif not internal and skip_scope in ("label", "bullet"):
                skip_scope = None
        if skip_scope is None:
            kept.append(line.rstrip())

    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def summarize_paths(paths: list, limit: int = 6) -> str:
    counts = {}
    for path in paths:
        directory = os.path.dirname(path) or "."
        counts[directory] = counts.get(directory, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return ", ".join(f"{directory} ({count})" for directory, count in ranked)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    cut = text[:limit]
    newline = cut.rfind("\n")
    if newline > limit * 0.6:
        cut = cut[:newline]
    return cut.rstrip() + "\n(…truncated)"


def allocate_budgets(lengths: list, budget: int) -> list:
    """Split a character budget so short texts stay whole and long ones share the rest evenly."""
    allocation = [0] * len(lengths)
    remaining = max(budget, 0)
    order = sorted(range(len(lengths)), key=lambda index: lengths[index])
    for position, index in enumerate(order):
        share = remaining // (len(order) - position)
        allocation[index] = min(lengths[index], share)
        remaining -= allocation[index]
    return allocation


def build_pull_request_context(pull_requests: list, direct_subjects: list,
                               budget: int = PR_CONTEXT_BUDGET) -> str:
    """Render pull requests (and commits pushed without one) as prompt context within a budget."""
    headers = []
    details = []
    for pull_request in pull_requests:
        header = f"### PR #{pull_request['number']}: {pull_request['title']}\n"
        if pull_request["labels"]:
            header += f"Labels: {', '.join(pull_request['labels'])}\n"
        areas = summarize_paths(pull_request["paths"])
        if areas:
            header += f"Areas: {areas}\n"
        headers.append(header)

        body = clean_pull_request_body(pull_request["body"])
        detail = f"Description:\n{body}\n" if body else ""
        if len(body) < MIN_USEFUL_PR_BODY and pull_request["commits"]:
            detail += "Commits:\n" + "".join(f"- {headline}\n" for headline in pull_request["commits"])
        details.append(truncate_text(detail, MAX_PR_BODY))

    direct_section = ""
    if direct_subjects:
        direct_section = "\n### Commits pushed without a pull request (cite nothing for these)\n"
        direct_section += "".join(f"- {subject}\n" for subject in direct_subjects[:MAX_DIRECT_COMMITS])

    detail_budget = budget - sum(len(header) + 1 for header in headers) - len(direct_section)
    allocations = allocate_budgets([len(detail) for detail in details], detail_budget)

    context = ""
    for header, detail, allocation in zip(headers, details, allocations):
        context += "\n" + header + truncate_text(detail, allocation)
    return context + direct_section


def get_pull_request_context(github_api_url: str, repo: str, from_release: str, to_release: str,
                             authorization_header: dict, exclude_labels: set):
    """
    Build summary context from the pull requests merged between two releases.
    Returns (context, {number: pull request}, commit count), or (None, None, None) when
    the pull requests could not be resolved and the caller should fall back to the diff.
    """
    print(f"Collecting pull requests for {repo}: {from_release} -> {to_release}")
    commits = list_range_commits(github_api_url, repo, from_release, to_release, authorization_header)
    if commits is None:
        return None, None, None

    numbers, direct_commits = find_merged_pull_requests(github_api_url, repo, commits, authorization_header)
    if numbers is None:
        return None, None, None

    fetched = fetch_pull_requests(github_api_url, repo, numbers, authorization_header)
    if fetched is None:
        return None, None, None

    included = []
    for pull_request in fetched:
        reason = pull_request_skip_reason(pull_request, exclude_labels)
        if reason:
            print(f"  Skipping PR #{pull_request['number']} ({reason}): {pull_request['title']}")
        else:
            included.append(pull_request)

    direct_subjects = []
    for commit in direct_commits:
        subject = commit.get("commit", {}).get("message", "").split("\n")[0]
        if subject and not SKIPPED_COMMIT_SUBJECT.match(subject):
            direct_subjects.append(subject)

    print(
        f"  {len(commits)} commits, {len(numbers)} merged PRs, {len(included)} included, "
        f"{len(direct_subjects)} commits without a PR"
    )

    if not included and not direct_subjects:
        return "", {}, len(commits)

    context = PR_CITATION_RULES + build_pull_request_context(included, direct_subjects)
    print(f"  Pull request context: {len(context)} characters")
    return context, {pull_request["number"]: pull_request for pull_request in included}, len(commits)


def link_citations(summary: str, citation_group, parse_ids, links: dict, kind: str) -> str:
    """
    Turn a bullet's trailing citation group into links ({id: (text, url)}).
    Bullets that only cite sources outside the release range are dropped.
    """
    lines = []
    for line in summary.splitlines():
        stripped = line.strip()
        if not re.match(r"^[-*]\s+", stripped):
            lines.append(line)
            continue

        match = citation_group.search(line)
        if not match:
            print(f"Warning: bullet without a {kind} citation kept: {stripped}")
            lines.append(line)
            continue

        cited = list(dict.fromkeys(parse_ids(match.group(1))))
        valid = [source for source in cited if source in links]
        if not valid:
            print(f"Warning: dropping bullet citing {kind}s outside the range {cited}: {stripped}")
            continue

        rendered = ", ".join(f"[{links[source][0]}]({links[source][1]})" for source in valid)
        lines.append(f"{line[:match.start()]} ({rendered})")

    return "\n".join(lines)


def link_pull_request_citations(summary: str, pull_requests: dict) -> str:
    """Turn trailing "(#12, #15)" citations into links to the source pull requests."""
    links = {number: (f"#{number}", pull_request["url"]) for number, pull_request in pull_requests.items()}
    return link_citations(
        summary, CITATION_GROUP,
        lambda group: [int(number) for number in re.findall(r"\d+", group)],
        links, "pull request",
    )


def link_commit_citations(summary: str, commits: dict) -> str:
    """Turn trailing "(3f2a9c1, 8d41e07)" citations into links to the source commits ({short sha: url})."""
    links = {short_sha: (short_sha, url) for short_sha, url in commits.items()}
    return link_citations(
        summary, COMMIT_CITATION_GROUP,
        lambda group: [sha[:SHORT_SHA_LENGTH].lower() for sha in re.findall(r"[0-9a-fA-F]{7,40}", group)],
        links, "commit",
    )


# --- Shared code filtering (commit fallback and code context) ---------------

# Paths whose changes never describe product behavior: tests, CI, vendored,
# lock and generated files.
EXCLUDED_CODE_PATH = re.compile(
    r"(?:^|/)(?:\.github|\.devcontainer|\.vscode|tests?|__tests__|__mocks__|testdata|fixtures|"
    r"vendor|node_modules|dist)/"
    r"|_test\.go$|\.(?:spec|test)\.[cm]?[jt]sx?$|(?:^|/)test_[^/]*\.py$|_test\.py$"
    r"|(?:^|/)(?:go\.sum|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock|composer\.lock)$"
    r"|\.min\.(?:js|css)$|\.pb\.go$|_gen\.go$|\.generated\.|\.snap$|\.map$"
    r"|(?:^|/)docs/(?:docs\.go|swagger\.(?:json|ya?ml))$",
    re.IGNORECASE,
)
# Translation catalogs: only the English source catalog says what changed.
TRANSLATION_CATALOG = re.compile(r"(?:^|/)i18n/[^/]+\.json$", re.IGNORECASE)


def is_reviewable_code(path: str) -> bool:
    if EXCLUDED_CODE_PATH.search(path):
        return False
    if TRANSLATION_CATALOG.search(path) and not path.lower().endswith("/en.json"):
        return False
    return True


def format_file_patch(file_info: dict, limit: int) -> str:
    header = (
        f"File: {file_info.get('filename', 'unknown')} ({file_info.get('status', 'modified')}, "
        f"+{file_info.get('additions', 0)}/-{file_info.get('deletions', 0)})\n"
    )
    return header + truncate_text(file_info.get("patch") or "", max(limit - len(header), 0)) + "\n"


def pack_sections(sections: list, budget: int) -> list:
    """Greedily pack sections, in order, into chunks of at most budget characters."""
    chunks = []
    current = ""
    for section in sections:
        section = truncate_text(section, budget)
        if current and len(current) + len(section) + 1 > budget:
            chunks.append(current)
            current = ""
        current += ("\n" if current else "") + section
    if current:
        chunks.append(current)
    return chunks


# --- Commit context ---------------------------------------------------------

COMMIT_SAMPLE_PROMPT = """
Write a brief summary of the key changes in this release. Be concise, complete, and avoid redundancy. Each bullet point should cover a distinct change.

Repository: example/repo
Comparing: v1.0.0 → v1.1.0

### Commit 3f2a9c1: Add configurable log levels
Adds `log_level` and `log_file` settings so operators can tune verbosity and
write logs to disk. Defaults to INFO; existing deployments keep their output.

### Commit 8d41e07: Retry uploads after transient storage errors
Uploads that fail with a 5xx from storage are retried up to three times with
backoff instead of being dropped.

### Commit b77c210: Move upload fixtures into a shared helper
No behavior change.

### Commit e09a5f3: Fix crash
No description; code changes:
File: src/player.ts (modified, +2/-1)
@@ -40,7 +40,8 @@
-  const first = items[0].url;
+  if (!items.length) return;
+  const first = items[0].url;
"""

COMMIT_GOOD_SAMPLE_RESPONSE = """
- Added configurable log levels and optional log-file output, defaulting to the existing behavior (3f2a9c1)
- Uploads now retry automatically after temporary storage errors instead of failing (8d41e07)
- The player no longer crashes on a recording without media items (e09a5f3)
"""

COMMIT_CITATION_RULES = """
The changes below are the commits in this release, oldest first, each with its message. Commits without a description show their code changes instead. Rules:
- End every bullet with the commit id(s) it is based on, e.g. "(3f2a9c1)" or "(3f2a9c1, 8d41e07)".
- Only cite commit ids that appear below.
- Combine related commits into one bullet; leave out commits with no user-visible effect, such as tests, refactors and tooling.
- Later commits can refine or undo earlier ones; describe the end result.
- Describe only what the messages and code support. Do not speculate.
"""

SHORT_SHA_LENGTH = 7
COMMIT_CITATION_GROUP = re.compile(
    r"\s*\(\s*((?:commits?\s*)?[0-9a-fA-F]{7,40}(?:\s*(?:,|and)\s*[0-9a-fA-F]{7,40})*)\s*\)\s*\.?\s*$",
    re.IGNORECASE,
)
COMMIT_TRAILER = re.compile(
    r"^(?:co-authored-by|signed-off-by|reviewed-by|acked-by|tested-by|change-id|cc):", re.IGNORECASE
)
GENERATED_WITH_LINE = re.compile(r"generated with \[?claude code", re.IGNORECASE)
CODING_AGENT_LOGIN = re.compile(r"^(?:copilot|claude|devin)", re.IGNORECASE)

COMMIT_CONTEXT_BUDGET = 28000
MAX_COMMIT_BODY = 4000
# Commits whose cleaned body is shorter than this get their code instead.
MIN_USEFUL_COMMIT_BODY = 40
MAX_CODE_FALLBACKS = 20
COMMIT_CODE_LIMIT = 3000


def clean_commit_message(message: str):
    """Split a commit message into its subject and a body without trailers."""
    lines = message.replace("\r\n", "\n").split("\n")
    subject = lines[0].strip()
    body = [
        line.rstrip() for line in lines[1:]
        if not COMMIT_TRAILER.match(line.strip()) and not GENERATED_WITH_LINE.search(line)
    ]
    return subject, re.sub(r"\n{3,}", "\n\n", "\n".join(body)).strip()


def commit_skip_reason(commit: dict):
    """Return why a commit should stay out of the release notes, or None."""
    if len(commit.get("parents", [])) > 1:
        return "merge commit"
    author = commit.get("author") or {}
    login = author.get("login") or ""
    # Coding agents such as Copilot are bots too, but write real changes.
    if (author.get("type") == "Bot" or login.endswith("[bot]")) and not CODING_AGENT_LOGIN.match(login):
        return f"bot author {login}"
    subject = (commit.get("commit", {}).get("message") or "").split("\n")[0].strip()
    if not subject or SKIPPED_COMMIT_SUBJECT.match(subject) or SKIPPED_PR_TITLE.match(subject):
        return "subject"
    return None


def fetch_commit_code(github_api_url: str, repo: str, sha: str, authorization_header: dict,
                      limit: int = COMMIT_CODE_LIMIT) -> str:
    """The reviewable part of one commit's diff, cut to limit characters."""
    response = requests.get(f"{github_api_url}/repos/{repo}/commits/{sha}", headers=authorization_header)
    if response.status_code != requests.codes.ok:
        print(f"  Could not fetch code for commit {sha[:SHORT_SHA_LENGTH]}: {response.status_code}")
        return ""
    files = [
        file_info for file_info in response.json().get("files", [])
        if file_info.get("patch") and is_reviewable_code(file_info.get("filename", ""))
    ]
    code = "".join(format_file_patch(file_info, limit) for file_info in files)
    return truncate_text(code, limit)


def build_commit_context(commits: list, budget: int = COMMIT_CONTEXT_BUDGET) -> str:
    """Render commits ({sha, subject, body, code}) as prompt context within a budget."""
    headers = [f"### Commit {commit['sha'][:SHORT_SHA_LENGTH]}: {commit['subject']}\n" for commit in commits]
    details = []
    for commit in commits:
        if commit["body"]:
            detail = commit["body"] + "\n"
        elif commit.get("code"):
            detail = "No description; code changes:\n" + commit["code"]
        else:
            detail = ""
        details.append(truncate_text(detail, MAX_COMMIT_BODY))

    detail_budget = budget - sum(len(header) + 1 for header in headers)
    allocations = allocate_budgets([len(detail) for detail in details], detail_budget)

    context = ""
    for header, detail, allocation in zip(headers, details, allocations):
        context += "\n" + header + truncate_text(detail, allocation)
    return context


def get_commit_context(github_api_url: str, repo: str, from_release: str, to_release: str,
                       authorization_header: dict):
    """
    Build summary context from the full messages of the commits between two releases.
    Commits without a useful description contribute their code instead.
    Returns (context, {short sha: url}, commit count), or (None, None, None) when the
    commits could not be listed and the caller should fall back to the diff.
    """
    print(f"Collecting commits for {repo}: {from_release} -> {to_release}")
    commits = list_range_commits(github_api_url, repo, from_release, to_release, authorization_header)
    if commits is None:
        return None, None, None

    included = []
    code_fallbacks = 0
    for commit in commits:
        reason = commit_skip_reason(commit)
        subject, body = clean_commit_message(commit.get("commit", {}).get("message") or "")
        if reason:
            if reason != "merge commit":
                print(f"  Skipping commit {commit['sha'][:SHORT_SHA_LENGTH]} ({reason}): {subject}")
            continue
        entry = {"sha": commit["sha"], "subject": subject, "body": body, "url": commit.get("html_url") or ""}
        if len(body) < MIN_USEFUL_COMMIT_BODY:
            entry["body"] = ""
            if code_fallbacks < MAX_CODE_FALLBACKS:
                entry["code"] = fetch_commit_code(github_api_url, repo, commit["sha"], authorization_header)
                code_fallbacks += 1
        included.append(entry)

    print(f"  {len(commits)} commits, {len(included)} included, {code_fallbacks} described by their code")
    if not included:
        return "", {}, len(commits)

    context = COMMIT_CITATION_RULES + build_commit_context(included)
    print(f"  Commit context: {len(context)} characters")
    links = {
        entry["sha"][:SHORT_SHA_LENGTH].lower(): entry["url"] or f"https://github.com/{repo}/commit/{entry['sha']}"
        for entry in included
    }
    return context, links, len(commits)


# --- Code context (two-step summary) ----------------------------------------

CODE_REVIEW_SYSTEM = (
    "You are a senior engineer reading code changes so that release notes can be written from them. "
    "Be factual and brief."
)
CODE_REVIEW_PROMPT = """
Below is part {part} of {parts} of the code changes in {repo} between {from_release} and {to_release}.
List what changed in behavior, one short line per change, naming the file(s) involved:
- new or changed API endpoints, settings, environment variables, UI, stored data, permissions and fixes
- a renamed or moved function, a changed comment or annotation, formatting or a refactor is not a behavior change; skip it unless it changes what users or operators see
- state only what the diff shows; do not guess at intent
If nothing in this part changes behavior, answer exactly "No behavior changes."
"""
CODE_MERGE_PROMPT = """
Merge these notes about code changes in {repo} into one shorter list. Combine notes about the same change and drop duplicates, but keep every distinct behavior change. One short line per change.
"""
CODE_SUMMARY_RULES = """
The changes below are notes from reading the code changes of this release part by part. Combine notes about the same change into one bullet and leave out changes with no user-visible effect.
"""
CODE_SAMPLE_PROMPT = """
Write a brief summary of the key changes in this release. Be concise, complete, and avoid redundancy. Each bullet point should cover a distinct change.

Repository: example/repo
Comparing: v1.0.0 → v1.1.0

Part 1:
- src/config.py: new `log_level` and `log_file` settings, default INFO
- src/logging.py: logs are also written to the file named by `log_file` when set
Part 2:
- src/upload.py: uploads that fail with a 5xx are retried 3 times with exponential backoff
- src/upload.py: retry helper moved into its own module (no behavior change)
"""
CODE_GOOD_SAMPLE_RESPONSE = """
- Added configurable log levels and optional log-file output, defaulting to the existing behavior
- Uploads now retry automatically after temporary storage errors instead of failing
"""
NO_BEHAVIOR_CHANGES = re.compile(r"^\W*no behavior changes\W*$", re.IGNORECASE)

CODE_CHUNK_BUDGET = 24000
CODE_NOTES_BUDGET = 28000
MAX_CODE_CHUNKS = 40
MAX_MERGE_ROUNDS = 3


def condense_notes(notes: list, repo: str, model_settings: dict, budget: int = CODE_NOTES_BUDGET) -> str:
    """Merge per-part notes with the model until they fit the budget."""
    for _ in range(MAX_MERGE_ROUNDS):
        if len("\n".join(notes)) <= budget or len(notes) < 2:
            break
        groups = pack_sections(notes, budget)
        if len(groups) == len(notes):
            # Every note already fills a group alone; merge them pairwise instead.
            groups = ["\n".join(notes[index:index + 2]) for index in range(0, len(notes), 2)]
        merged = []
        for group in groups:
            result = complete_chat([
                {"role": "system", "content": CODE_REVIEW_SYSTEM},
                {"role": "user", "content": CODE_MERGE_PROMPT.format(repo=repo) + "\n" + truncate_text(group, budget)},
            ], model_settings)
            merged.append(result.strip() if result else truncate_text(group, budget // len(groups)))
        notes = merged
    return truncate_text("\n".join(notes), budget)


def get_code_context(github_api_url: str, repo: str, from_release: str, to_release: str,
                     authorization_header: dict, model_settings: dict):
    """
    Build summary context by reading the whole code diff in parts: each part is
    summarized into behavior notes, and the notes are merged until they fit one prompt.
    Returns (context, stats), or (None, None) when the comparison could not be fetched.
    """
    print(f"Reading code changes for {repo}: {from_release} -> {to_release}")
    response = requests.get(
        f"{github_api_url}/repos/{repo}/compare/{from_release}...{to_release}",
        headers=authorization_header, params={"per_page": 1},
    )
    if response.status_code != requests.codes.ok:
        print(f"Failed to get comparison for {repo}: {response.status_code}")
        print(f"Response: {response.text}")
        return None, None
    compare_data = response.json()
    files = sorted(compare_data.get("files", []), key=lambda file_info: file_info.get("filename", ""))
    stats = {
        "total_commits": compare_data.get("total_commits", 0),
        "files_changed": len(files),
        "additions": sum(file_info.get("additions", 0) for file_info in files),
        "deletions": sum(file_info.get("deletions", 0) for file_info in files),
    }

    reviewable = [file_info for file_info in files if is_reviewable_code(file_info.get("filename", ""))]
    sections = [format_file_patch(file_info, CODE_CHUNK_BUDGET) for file_info in reviewable if file_info.get("patch")]
    without_patch = [
        f"{file_info['filename']} (+{file_info.get('additions', 0)}/-{file_info.get('deletions', 0)})"
        for file_info in reviewable if not file_info.get("patch")
    ]
    chunks = pack_sections(sections, CODE_CHUNK_BUDGET)
    print(
        f"  {len(files)} files, {len(reviewable)} reviewable, {len(without_patch)} without a diff, "
        f"{sum(len(section) for section in sections)} characters in {len(chunks)} part(s)"
    )
    if len(chunks) > MAX_CODE_CHUNKS:
        print(f"Warning: reading only the first {MAX_CODE_CHUNKS} of {len(chunks)} parts for {repo}")
        chunks = chunks[:MAX_CODE_CHUNKS]

    notes = []
    for part, chunk in enumerate(chunks, 1):
        prompt = CODE_REVIEW_PROMPT.format(
            part=part, parts=len(chunks), repo=repo, from_release=from_release, to_release=to_release
        )
        result = complete_chat([
            {"role": "system", "content": CODE_REVIEW_SYSTEM},
            {"role": "user", "content": prompt + "\n" + chunk},
        ], model_settings)
        if result and not NO_BEHAVIOR_CHANGES.match(result.strip()):
            notes.append(f"Part {part}:\n{result.strip()}")
    if without_patch:
        notes.append("Files changed without a viewable diff (too large or binary): " + ", ".join(without_patch))

    if not notes:
        return "", stats

    context = CODE_SUMMARY_RULES + "\n" + condense_notes(notes, repo, model_settings)
    print(f"  Code notes context: {len(context)} characters")
    return context, stats


def complete_chat(messages: list, model_settings: dict, max_tokens: int = None) -> str:
    """Run one chat completion against OpenAI or Azure OpenAI; returns "" on missing configuration."""
    openai_api_key = (model_settings.get("openai_api_key") or "").strip()
    azure_openai_api_key = (model_settings.get("azure_openai_api_key") or "").strip()
    azure_openai_endpoint = (model_settings.get("azure_openai_endpoint") or "").strip()
    if openai_api_key:
        client = openai.OpenAI(api_key=openai_api_key)
    elif azure_openai_api_key:
        if not azure_openai_endpoint:
            print("Error: Azure OpenAI endpoint is required when using Azure OpenAI API key")
            return ""
        client = AzureOpenAI(
            api_key=azure_openai_api_key,
            azure_endpoint=azure_openai_endpoint,
            api_version=(model_settings.get("azure_openai_version") or "").strip() or "2024-02-15-preview",
        )
    else:
        print("Error: No API key provided (OpenAI or Azure OpenAI)")
        print("Please ensure you have set either 'openai_api_key' or 'azure_openai_api_key' input")
        return ""

    response = client.chat.completions.create(
        model=model_settings.get("model"),
        messages=messages,
        max_completion_tokens=max_tokens or model_settings.get("max_tokens"),
    )
    return response.choices[0].message.content or ""


def generate_ai_summary(diff_content: str, repo: str, from_release: str, to_release: str,
                        openai_api_key: str, azure_openai_api_key: str, 
                        azure_openai_endpoint: str, azure_openai_version: str,
                        model: str, max_tokens: int, temperature: float,
                        custom_prompt: str, example_prompt: str = SAMPLE_PROMPT,
                        example_response: str = GOOD_SAMPLE_RESPONSE) -> str:
    """
    Generate AI summary for the diff content.
    """
    completion_prompt = custom_prompt if custom_prompt else COMPLETION_PROMPT
    completion_prompt += f"\n\nRepository: {repo}\nComparing: {from_release} → {to_release}\n\n"
    completion_prompt += diff_content
    
    # Truncate if too long (approximate token limit)
    max_allowed_tokens = 8000
    characters_per_token = 4
    max_allowed_characters = max_allowed_tokens * characters_per_token
    if len(completion_prompt) > max_allowed_characters:
        completion_prompt = completion_prompt[:max_allowed_characters]
        print(f"Warning: Prompt truncated to {max_allowed_characters} characters for {repo}")
    
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant who writes brief, concise release summaries. Each bullet point should cover a distinct change without overlapping with others. Be complete but not extensive. No headers, no lengthy explanations - just the key changes."
        },
        {"role": "user", "content": example_prompt},
        {"role": "assistant", "content": example_response},
        {"role": "user", "content": completion_prompt},
    ]

    print(f"Generating summary for {repo}...")
    return complete_chat(messages, {
        "openai_api_key": openai_api_key,
        "azure_openai_api_key": azure_openai_api_key,
        "azure_openai_endpoint": azure_openai_endpoint,
        "azure_openai_version": azure_openai_version,
        "model": model,
        "max_tokens": max_tokens,
    })


def read_frontend_context(file_path: str) -> str:
    """
    Read the frontend context from a file.
    """
    if not file_path or not file_path.strip():
        return ""
    
    file_path = file_path.strip()
    
    if not os.path.exists(file_path):
        print(f"Warning: Frontend context file not found: {file_path}")
        return ""
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"Read frontend context from {file_path} ({len(content)} characters)")
        return content
    except Exception as e:
        print(f"Error reading frontend context file: {e}")
        return ""


def generate_test_plan(diff_content: str, frontend_context: str,
                       openai_api_key: str, azure_openai_api_key: str,
                       azure_openai_endpoint: str, azure_openai_version: str,
                       model: str, max_tokens: int, temperature: float) -> str:
    """
    Generate a test plan based on the diff content and frontend application context.
    """
    if not frontend_context:
        print("No frontend context provided, skipping test plan generation")
        return ""
    
    prompt = TEST_PLAN_PROMPT + "\n\n"
    prompt += "## Application Context:\n"
    prompt += frontend_context + "\n\n"
    prompt += "## Code Changes:\n"
    prompt += diff_content
    
    # Truncate if too long (approximate token limit)
    max_allowed_tokens = 12000
    characters_per_token = 4
    max_allowed_characters = max_allowed_tokens * characters_per_token
    if len(prompt) > max_allowed_characters:
        # Prioritize keeping the frontend context, truncate the diff
        context_portion = frontend_context[:max_allowed_characters // 2]
        diff_portion = diff_content[:max_allowed_characters // 2]
        prompt = TEST_PLAN_PROMPT + "\n\n"
        prompt += "## Application Context:\n"
        prompt += context_portion + "\n\n"
        prompt += "## Code Changes:\n"
        prompt += diff_portion
        print(f"Warning: Test plan prompt truncated to {max_allowed_characters} characters")
    
    messages = [
        {
            "role": "system",
            "content": "You are a QA engineer. Create a brief test plan with only the essential test scenarios. No title, no headers - start directly with the bullet list. Keep it short - max 10 bullet points total."
        },
        {"role": "user", "content": TEST_PLAN_SAMPLE_PROMPT},
        {"role": "assistant", "content": TEST_PLAN_SAMPLE_RESPONSE},
        {"role": "user", "content": prompt},
    ]
    
    generated_test_plan = ""
    
    # Check for non-empty API keys
    has_openai_key = openai_api_key and openai_api_key.strip()
    has_azure_key = azure_openai_api_key and azure_openai_api_key.strip()
    
    if has_openai_key:
        print("Generating test plan using OpenAI API...")
        client = openai.OpenAI(api_key=openai_api_key.strip())
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_tokens,
        )
        generated_test_plan = response.choices[0].message.content
        
    elif has_azure_key:
        print("Generating test plan using Azure OpenAI API...")
        if not azure_openai_endpoint or not azure_openai_endpoint.strip():
            print("Error: Azure OpenAI endpoint is required for test plan generation")
            return ""
        client = AzureOpenAI(
            api_key=azure_openai_api_key.strip(),
            azure_endpoint=azure_openai_endpoint.strip(),
            api_version=azure_openai_version.strip() if azure_openai_version else "2024-02-15-preview"
        )
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=max_tokens,
        )
        generated_test_plan = response.choices[0].message.content
    else:
        print("Error: No API key provided for test plan generation")
        return ""
    
    return generated_test_plan


def write_github_output(name: str, value: str):
    """Write output to GitHub Actions output file."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            # Handle multiline values
            delimiter = "EOF"
            f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    else:
        print(f"::set-output name={name}::{value}")


def write_github_summary(content: str):
    """Write content to GitHub Actions job summary."""
    github_step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_step_summary:
        with open(github_step_summary, "a") as f:
            f.write(content)
    else:
        print("GITHUB_STEP_SUMMARY not set, printing to stdout:")
        print(content)


def main():
    parser = argparse.ArgumentParser(
        description="Generate release notes by comparing releases across multiple repositories using AI."
    )
    parser.add_argument(
        "--github-api-url", type=str, required=True, help="The GitHub API URL"
    )
    parser.add_argument(
        "--github-token", type=str, required=True, help="The GitHub token"
    )
    parser.add_argument(
        "--repositories", type=str, required=False, default="[]",
        help="JSON array of repository configurations"
    )
    parser.add_argument(
        "--raw-diffs", type=str, required=False, default="[]",
        help="JSON array of raw diff objects with 'name' and 'diff' properties"
    )
    parser.add_argument(
        "--openai-api-key", type=str, required=False, default="",
        help="The OpenAI API key"
    )
    parser.add_argument(
        "--azure-openai-api-key", type=str, required=False, default="",
        help="The Azure OpenAI API key"
    )
    parser.add_argument(
        "--azure-openai-endpoint", type=str, required=False, default="",
        help="The Azure OpenAI endpoint"
    )
    parser.add_argument(
        "--azure-openai-version", type=str, required=False, default="2024-02-15-preview",
        help="The Azure OpenAI API version"
    )
    parser.add_argument(
        "--openai-model", type=str, required=False, default="gpt-4o",
        help="The OpenAI model to use"
    )
    parser.add_argument(
        "--max-tokens", type=int, required=False, default=2000,
        help="Maximum tokens for completion"
    )
    parser.add_argument(
        "--temperature", type=float, required=False, default=0.6,
        help="Temperature for the model"
    )
    parser.add_argument(
        "--release-title", type=str, required=False, default="Release Notes",
        help="Title for the combined release notes"
    )
    parser.add_argument(
        "--include-diff-stats", type=str, required=False, default="true",
        help="Include diff statistics in output"
    )
    parser.add_argument(
        "--custom-prompt", type=str, required=False, default="",
        help="Custom prompt for generating release notes"
    )
    parser.add_argument(
        "--frontend-context-file", type=str, required=False, default="",
        help="Path to a .txt file containing frontend application context for test plan generation"
    )
    parser.add_argument(
        "--generate-test-plan", type=str, required=False, default="false",
        help="Generate a test plan based on changes and frontend context"
    )
    parser.add_argument(
        "--context-source", type=str, required=False, default="diff",
        choices=["diff", "pull_requests", "commits", "code"],
        help="Summarize repositories from the raw diff, the merged pull requests, the commit messages, "
             "or the whole code diff read in parts"
    )
    parser.add_argument(
        "--exclude-labels", type=str, required=False,
        default="dependencies,ci,chore,skip-changelog,skip-release-notes,no-release-notes",
        help="Comma-separated pull request labels to leave out (pull_requests context only)"
    )
    
    args = parser.parse_args()
    
    # Parse repositories JSON
    try:
        repositories = json.loads(args.repositories) if args.repositories else []
    except json.JSONDecodeError as e:
        print(f"Error parsing repositories JSON: {e}")
        repositories = []
    
    # Parse raw_diffs JSON
    try:
        raw_diffs = json.loads(args.raw_diffs) if args.raw_diffs else []
    except json.JSONDecodeError as e:
        print(f"Error parsing raw_diffs JSON: {e}")
        raw_diffs = []
    
    if not repositories and not raw_diffs:
        print("No repositories or raw diffs provided")
        return 1
    
    authorization_header = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {args.github_token}",
    }
    
    include_stats = args.include_diff_stats.lower() == "true"
    exclude_labels = {label.strip().lower() for label in args.exclude_labels.split(",") if label.strip()}
    model_settings = {
        "openai_api_key": args.openai_api_key,
        "azure_openai_api_key": args.azure_openai_api_key,
        "azure_openai_endpoint": args.azure_openai_endpoint,
        "azure_openai_version": args.azure_openai_version,
        "model": args.openai_model,
        "max_tokens": args.max_tokens,
    }
    print(f"Context source: {args.context_source}")
    
    # Collect all summaries
    all_summaries = []
    all_stats = []
    brief_summary_parts = []
    
    for repo_config in repositories:
        repo = repo_config.get("repo")
        from_release = repo_config.get("from_release")
        to_release = repo_config.get("to_release")
        
        if not all([repo, from_release, to_release]):
            print(f"Skipping invalid repository config: {repo_config}")
            continue
        
        print(f"\n{'='*60}")
        print(f"Processing: {repo}")
        print(f"From: {from_release} -> To: {to_release}")
        print(f"{'='*60}")
        
        summary = None
        stats = None
        context = None
        examples = None
        link = None

        if args.context_source == "pull_requests":
            context, pull_requests, commit_count = get_pull_request_context(
                args.github_api_url, repo, from_release, to_release,
                authorization_header, exclude_labels
            )
            examples = (PR_SAMPLE_PROMPT, PR_GOOD_SAMPLE_RESPONSE)
            link = lambda summary: link_pull_request_citations(summary, pull_requests)
        elif args.context_source == "commits":
            context, commit_links, commit_count = get_commit_context(
                args.github_api_url, repo, from_release, to_release, authorization_header
            )
            examples = (COMMIT_SAMPLE_PROMPT, COMMIT_GOOD_SAMPLE_RESPONSE)
            link = lambda summary: link_commit_citations(summary, commit_links)
        elif args.context_source == "code":
            context, stats = get_code_context(
                args.github_api_url, repo, from_release, to_release, authorization_header, model_settings
            )
            examples = (CODE_SAMPLE_PROMPT, CODE_GOOD_SAMPLE_RESPONSE)

        if args.context_source != "diff":
            if context is None:
                print(f"Could not build {args.context_source} context for {repo}, falling back to the diff...")
            elif not context:
                print(f"No release-relevant changes for {repo}, skipping...")
                continue
            else:
                if stats is None:
                    if include_stats:
                        _, stats = get_compare_diff(
                            args.github_api_url, repo, from_release, to_release, authorization_header
                        )
                    stats = stats or {
                        "total_commits": commit_count, "files_changed": 0, "additions": 0, "deletions": 0,
                    }
                summary = generate_ai_summary(
                    context, repo, from_release, to_release,
                    args.openai_api_key, args.azure_openai_api_key,
                    args.azure_openai_endpoint, args.azure_openai_version,
                    args.openai_model, args.max_tokens, args.temperature,
                    args.custom_prompt, *examples
                )
                if summary and link:
                    summary = link(summary)

        if context is None:
            # Get the diff
            diff_content, stats = get_compare_diff(
                args.github_api_url, repo, from_release, to_release, authorization_header
            )
            
            if not diff_content:
                print(f"Could not get diff for {repo}, skipping...")
                continue
            
            # Generate AI summary
            summary = generate_ai_summary(
                diff_content, repo, from_release, to_release,
                args.openai_api_key, args.azure_openai_api_key,
                args.azure_openai_endpoint, args.azure_openai_version,
                args.openai_model, args.max_tokens, args.temperature,
                args.custom_prompt
            )
        
        # Store stats
        if stats:
            stats["repo"] = repo
            stats["from_release"] = from_release
            stats["to_release"] = to_release
            all_stats.append(stats)
        
        if summary:
            all_summaries.append({
                "repo": repo,
                "from_release": from_release,
                "to_release": to_release,
                "summary": summary,
                "stats": stats
            })
            brief_summary_parts.append(f"- **{repo}**: {from_release} → {to_release}")
    
    # Process raw diffs
    raw_diff_file_stats = []  # Store per-file stats for raw diffs
    if raw_diffs:
        print(f"\n{'='*60}")
        print(f"Processing {len(raw_diffs)} raw diff(s)")
        print(f"{'='*60}")
        
        # Debug: print all raw diff names
        print(f"Raw diff files received:")
        for idx, raw_diff in enumerate(raw_diffs):
            diff_name = raw_diff.get("name", "unknown")
            diff_content_raw = raw_diff.get("diff", "")
            print(f"  [{idx}] {diff_name}: {len(diff_content_raw)} characters")
        
        # Helper function to extract new icons from diff (same logic as repository processing)
        def extract_new_icons_from_raw_diff(patch: str) -> list:
            """Extract only newly added icon names from icons.js diff."""
            import re
            new_icons = []
            for line in patch.split('\n'):
                # Only look at added lines (starting with +, but not ++)
                if line.startswith('+') and not line.startswith('++'):
                    # Match export const/let/var iconName
                    match = re.search(r'export\s+(?:const|let|var)\s+(\w+)', line)
                    if match:
                        new_icons.append(match.group(1))
                        continue
                    # Match object key patterns: iconName: or "iconName": or 'iconName':
                    match = re.search(r'["\']?(\w+(?:-\w+)*)["\']?\s*:\s*[\'"`<{]', line)
                    if match:
                        icon_name = match.group(1)
                        # Filter out common non-icon keys
                        if icon_name not in ['viewBox', 'fill', 'stroke', 'width', 'height', 'd', 'path', 'xmlns', 'class', 'style']:
                            new_icons.append(icon_name)
            return new_icons
        
        # Combine all raw diffs into a single diff content
        raw_diff_content = "\n### Raw Diffs\n\n"
        for raw_diff in raw_diffs:
            diff_name = raw_diff.get("name", "unknown")
            diff_content_raw = raw_diff.get("diff", "")
            
            # Check if this is an icons.js file
            is_icons_file = diff_name.lower().endswith(('icons.js', 'icons.ts', 'icons.jsx', 'icons.tsx'))
            
            if is_icons_file and diff_content_raw:
                # For icon files, only report newly added icons
                new_icons = extract_new_icons_from_raw_diff(diff_content_raw)
                if new_icons:
                    raw_diff_content += f"Changes in file {diff_name}: New icons added: {', '.join(new_icons)}\n\n"
                else:
                    raw_diff_content += f"Changes in file {diff_name}: Icon content updated (no new icons added)\n\n"
            else:
                # Include full diff for all other files (including indexes.txt)
                raw_diff_content += f"Changes in file {diff_name}:\n{diff_content_raw if diff_content_raw else '(no diff content provided)'}\n\n"
            
            # Calculate per-file statistics by counting diff lines
            additions = 0
            deletions = 0
            if diff_content_raw:
                for line in diff_content_raw.split('\n'):
                    # Count lines starting with + or - (but not ++ or --)
                    if line.startswith('+') and not line.startswith('++'):
                        additions += 1
                    elif line.startswith('-') and not line.startswith('--'):
                        deletions += 1
            
            raw_diff_file_stats.append({
                "file_name": diff_name,
                "additions": additions,
                "deletions": deletions,
            })
        
        # Generate AI summary for raw diffs
        if raw_diff_content.strip() != "\n### Raw Diffs\n":
            summary = generate_ai_summary(
                raw_diff_content, "Raw Diffs", "N/A", "N/A",
                args.openai_api_key, args.azure_openai_api_key,
                args.azure_openai_endpoint, args.azure_openai_version,
                args.openai_model, args.max_tokens, args.temperature,
                args.custom_prompt
            )
            
            if summary:
                # Calculate total stats for raw diffs
                total_additions = sum(f["additions"] for f in raw_diff_file_stats)
                total_deletions = sum(f["deletions"] for f in raw_diff_file_stats)
                raw_diff_stats = {
                    "repo": "Raw Diffs",
                    "from_release": "N/A",
                    "to_release": "N/A",
                    "total_commits": 0,
                    "files_changed": len(raw_diffs),
                    "additions": total_additions,
                    "deletions": total_deletions,
                    "file_stats": raw_diff_file_stats,  # Include per-file breakdown
                }
                all_stats.append(raw_diff_stats)
                
                # Calculate per-file change counts
                raw_diff_files_with_counts = []
                for d in raw_diffs:
                    file_name = d.get("name", "unknown")
                    diff_text = d.get("diff", "")
                    additions = diff_text.count("\n+") + (1 if diff_text.startswith("+") else 0)
                    deletions = diff_text.count("\n-") + (1 if diff_text.startswith("-") else 0)
                    total_changes = additions + deletions
                    raw_diff_files_with_counts.append({
                        "name": file_name,
                        "changes": total_changes
                    })
                
                raw_diff_files = [d.get("name", "unknown") for d in raw_diffs]
                all_summaries.append({
                    "repo": "Raw Diffs",
                    "from_release": "N/A",
                    "to_release": "N/A",
                    "summary": summary,
                    "stats": raw_diff_stats,
                    "raw_diff_files": raw_diff_files
                })
                # Add each file as a separate bullet point with change count
                for file_info in raw_diff_files_with_counts:
                    brief_summary_parts.append(f"- **{file_info['name']}**: {file_info['changes']} change(s)")
    
    if not all_summaries:
        print("No summaries generated")
        return 1
    
    # Build the combined release notes
    combined_notes = f"# {args.release_title}\n\n"
    combined_notes += f"*Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}*\n\n"
    
    # Add overview section
    combined_notes += "## Overview\n\n"
    combined_notes += "This release includes changes from the following repositories:\n\n"
    for part in brief_summary_parts:
        combined_notes += f"{part}\n"
    combined_notes += "\n"
    
    # Add statistics if enabled
    if include_stats and all_stats:
        combined_notes += "## Statistics\n\n"
        combined_notes += "| Repository | Commits | Files Changed | Additions | Deletions |\n"
        combined_notes += "|------------|---------|---------------|-----------|----------|\n"
        for stat in all_stats:
            # Check if this is a raw diff entry with per-file stats
            if stat.get('repo') == 'Raw Diffs' and stat.get('file_stats'):
                # First show the summary row for Raw Diffs
                combined_notes += f"| **{stat['repo']}** | {stat['total_commits']} | {stat['files_changed']} | +{stat['additions']} | -{stat['deletions']} |\n"
                # Then show per-file breakdown
                for file_stat in stat['file_stats']:
                    combined_notes += f"| ↳ {file_stat['file_name']} | - | 1 | +{file_stat['additions']} | -{file_stat['deletions']} |\n"
            else:
                combined_notes += f"| {stat['repo']} | {stat['total_commits']} | {stat['files_changed']} | +{stat['additions']} | -{stat['deletions']} |\n"
        combined_notes += "\n"
    
    # Add individual repository summaries
    combined_notes += "---\n\n"
    
    for item in all_summaries:
        combined_notes += f"## {item['repo']}\n\n"
        combined_notes += f"**Release:** {item['from_release']} → {item['to_release']}\n\n"
        combined_notes += item['summary']
        combined_notes += "\n\n---\n\n"
    
    # Generate brief summary
    brief_summary = f"Release notes generated for {len(all_summaries)} repositories. "
    total_commits = sum(s.get("total_commits", 0) for s in all_stats)
    total_files = sum(s.get("files_changed", 0) for s in all_stats)
    brief_summary += f"Total: {total_commits} commits, {total_files} files changed."
    
    # Generate test plan if enabled
    test_plan = ""
    should_generate_test_plan = args.generate_test_plan.lower() == "true"
    frontend_context_file = args.frontend_context_file
    
    if should_generate_test_plan and frontend_context_file:
        print("\n" + "="*60)
        print("Generating Test Plan...")
        print("="*60)
        
        # Read frontend context
        frontend_context = read_frontend_context(frontend_context_file)
        
        if frontend_context:
            # Collect all diff content for test plan generation
            all_diff_content = ""
            
            # Collect diffs from repositories
            for repo_config in repositories:
                repo = repo_config.get("repo")
                from_release = repo_config.get("from_release")
                to_release = repo_config.get("to_release")
                
                if all([repo, from_release, to_release]):
                    diff_content, _ = get_compare_diff(
                        args.github_api_url, repo, from_release, to_release, authorization_header
                    )
                    if diff_content:
                        all_diff_content += diff_content + "\n"
            
            # Collect raw diffs
            if raw_diffs:
                all_diff_content += "\n### Raw Diffs\n\n"
                for raw_diff in raw_diffs:
                    diff_name = raw_diff.get("name", "unknown")
                    diff_content_raw = raw_diff.get("diff", "")
                    all_diff_content += f"Changes in file {diff_name}:\n{diff_content_raw}\n\n"
            
            # Generate the test plan
            test_plan = generate_test_plan(
                all_diff_content, frontend_context,
                args.openai_api_key, args.azure_openai_api_key,
                args.azure_openai_endpoint, args.azure_openai_version,
                args.openai_model, args.max_tokens, args.temperature
            )
            
            if test_plan:
                # Add test plan section to the combined notes
                combined_notes += "\n# Test Plan\n\n"
                combined_notes += f"*Based on frontend context from: {frontend_context_file}*\n\n"
                combined_notes += test_plan
                combined_notes += "\n"
                
                print("Test plan generated successfully!")
            else:
                print("Warning: Failed to generate test plan")
        else:
            print(f"Warning: Could not read frontend context from {frontend_context_file}")
    elif should_generate_test_plan and not frontend_context_file:
        print("Warning: Test plan generation enabled but no frontend context file provided")
    
    # Write outputs
    write_github_output("release_notes", combined_notes)
    write_github_output("summary", brief_summary)
    write_github_output("test_plan", test_plan)
    
    # Write to GitHub Actions summary
    write_github_summary(combined_notes)
    
    print("\n" + "="*60)
    print("Release notes generated successfully!")
    print("="*60)
    print(f"\nBrief summary: {brief_summary}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
