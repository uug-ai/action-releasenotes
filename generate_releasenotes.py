#!/usr/bin/env python3
"""
Generate release notes by comparing releases across multiple repositories using AI.
"""
import sys
import requests
import argparse
import json
import os
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

COMPLETION_PROMPT = """
Write a brief summary of the release changes.
Focus on the most important changes: key new features, significant improvements, and critical bug fixes.
Be concise - each bullet point should be one line and cover a distinct change without overlapping with others.
Be complete but not extensive. Do NOT include section headers, just bullet points.
Go straight to the point. The following changes took place:
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
    ]
    
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
        
        # Exclude other files by default
        return False
    
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
    
    files = compare_data.get("files", [])
    
    # Track special file changes for user awareness
    icon_changes = []
    helm_chart_changes = []
    
    for file_info in files:
        filename = file_info.get("filename", "unknown")
        
        # Track icon changes
        if is_icon_file(filename):
            icon_changes.append(filename)
        
        # Track helm chart changes
        if is_helm_chart_file(filename):
            helm_chart_changes.append(filename)
        
        # Skip non-business code files
        if not should_include_file(filename):
            continue
        
        patch = file_info.get("patch", "")
        status = file_info.get("status", "modified")
        additions = file_info.get("additions", 0)
        deletions = file_info.get("deletions", 0)
        
        total_additions += additions
        total_deletions += deletions
        
        if patch:
            diff_content += f"Changes in file {filename} ({status}, +{additions}/-{deletions}): {patch}\n"
    
    # Add notes about special file changes that require user attention
    if icon_changes or helm_chart_changes:
        diff_content += "\n### Additional Updates Required:\n"
        if icon_changes:
            diff_content += f"\n**Icon/Image changes detected** (may require asset updates):\n"
            for icon_file in icon_changes:
                diff_content += f"- {icon_file}\n"
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


def generate_ai_summary(diff_content: str, repo: str, from_release: str, to_release: str,
                        openai_api_key: str, azure_openai_api_key: str, 
                        azure_openai_endpoint: str, azure_openai_version: str,
                        model: str, max_tokens: int, temperature: float,
                        custom_prompt: str) -> str:
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
        {"role": "user", "content": SAMPLE_PROMPT},
        {"role": "assistant", "content": GOOD_SAMPLE_RESPONSE},
        {"role": "user", "content": completion_prompt},
    ]
    
    generated_summary = ""
    
    # Check for non-empty API keys (strip whitespace and check for actual content)
    has_openai_key = openai_api_key and openai_api_key.strip()
    has_azure_key = azure_openai_api_key and azure_openai_api_key.strip()
    
    print(f"Debug: OpenAI API key provided: {has_openai_key}")
    print(f"Debug: Azure OpenAI API key provided: {has_azure_key}")
    
    if has_openai_key:
        print(f"Using OpenAI API for {repo}...")
        client = openai.OpenAI(api_key=openai_api_key.strip())
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        generated_summary = response.choices[0].message.content
        
    elif has_azure_key:
        print(f"Using Azure OpenAI API for {repo}...")
        if not azure_openai_endpoint or not azure_openai_endpoint.strip():
            print("Error: Azure OpenAI endpoint is required when using Azure OpenAI API key")
            return ""
        client = AzureOpenAI(
            api_key=azure_openai_api_key.strip(),
            azure_endpoint=azure_openai_endpoint.strip(),
            api_version=azure_openai_version.strip() if azure_openai_version else "2024-02-15-preview"
        )
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        generated_summary = response.choices[0].message.content
    else:
        print("Error: No API key provided (OpenAI or Azure OpenAI)")
        print("Please ensure you have set either 'openai_api_key' or 'azure_openai_api_key' input")
        return ""
    
    return generated_summary


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
        "--repositories", type=str, required=True, 
        help="JSON array of repository configurations"
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
    
    args = parser.parse_args()
    
    # Parse repositories JSON
    try:
        repositories = json.loads(args.repositories)
    except json.JSONDecodeError as e:
        print(f"Error parsing repositories JSON: {e}")
        return 1
    
    if not repositories:
        print("No repositories provided")
        return 1
    
    authorization_header = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {args.github_token}",
    }
    
    include_stats = args.include_diff_stats.lower() == "true"
    
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
        
        # Get the diff
        diff_content, stats = get_compare_diff(
            args.github_api_url, repo, from_release, to_release, authorization_header
        )
        
        if not diff_content:
            print(f"Could not get diff for {repo}, skipping...")
            continue
        
        # Store stats
        if stats:
            stats["repo"] = repo
            stats["from_release"] = from_release
            stats["to_release"] = to_release
            all_stats.append(stats)
        
        # Generate AI summary
        summary = generate_ai_summary(
            diff_content, repo, from_release, to_release,
            args.openai_api_key, args.azure_openai_api_key,
            args.azure_openai_endpoint, args.azure_openai_version,
            args.openai_model, args.max_tokens, args.temperature,
            args.custom_prompt
        )
        
        if summary:
            all_summaries.append({
                "repo": repo,
                "from_release": from_release,
                "to_release": to_release,
                "summary": summary,
                "stats": stats
            })
            brief_summary_parts.append(f"- **{repo}**: {from_release} → {to_release}")
    
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
    
    # Write outputs
    write_github_output("release_notes", combined_notes)
    write_github_output("summary", brief_summary)
    
    # Write to GitHub Actions summary
    write_github_summary(combined_notes)
    
    print("\n" + "="*60)
    print("Release notes generated successfully!")
    print("="*60)
    print(f"\nBrief summary: {brief_summary}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
