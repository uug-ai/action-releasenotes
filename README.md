# `uug-ai/action-releasenotes` GitHub Action

Generate comprehensive release notes by comparing releases across multiple repositories using (Azure) OpenAI! 🚀

## What does it do?

`uug-ai/action-releasenotes` is a GitHub Action that compares releases (tags) between multiple repositories and uses the power of AI to generate professional release notes. It's perfect for:

- **Monorepo releases**: Generate unified release notes from multiple services
- **Platform releases**: Aggregate changes from multiple repositories into one release document
- **Automated changelogs**: Create user-friendly release notes automatically
- **Custom diffs**: Include raw diff content from any source (e.g., configuration changes, external systems)

The Action will:
1. Fetch the diff between two releases for each configured repository
2. Process any raw diffs provided directly
3. Send the changes to OpenAI/Azure OpenAI to generate a summary
4. Combine all summaries into one comprehensive release notes document
5. Add the release notes to the GitHub Actions job summary

## How can you use it?

### Prerequisites

1. Create an account on OpenAI or Azure OpenAI and get your API key
2. Add the API key as a [secret](https://docs.github.com/en/actions/security-guides/encrypted-secrets) in your repository's settings
3. Ensure your GitHub token has read access to the repositories you want to compare

### Basic Usage

Create a workflow file, e.g. `.github/workflows/release-notes.yml`:

```yaml
name: Generate Release Notes

on:
  workflow_dispatch:
    inputs:
      repositories:
        description: 'JSON array of repository configurations'
        required: true
        default: '[{"repo": "owner/repo1", "from_release": "v1.0.0", "to_release": "v1.1.0"}]'

jobs:
  generate-release-notes:
    runs-on: ubuntu-22.04

    steps:
      - uses: uug-ai/action-releasenotes@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          repositories: ${{ github.event.inputs.repositories }}
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
```

### Multi-Repository Example

```yaml
name: Generate Platform Release Notes

on:
  workflow_dispatch:

jobs:
  generate-release-notes:
    runs-on: ubuntu-22.04

    steps:
      - uses: uug-ai/action-releasenotes@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          repositories: |
            [
              {"repo": "myorg/frontend", "from_release": "v2.0.0", "to_release": "v2.1.0"},
              {"repo": "myorg/backend", "from_release": "v1.5.0", "to_release": "v1.6.0"},
              {"repo": "myorg/api", "from_release": "v3.0.0", "to_release": "v3.1.0"}
            ]
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          release_title: "Platform Release v2.1.0"
```

### Using Azure OpenAI

```yaml
- uses: uug-ai/action-releasenotes@main
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    repositories: '[{"repo": "owner/repo", "from_release": "v1.0.0", "to_release": "v1.1.0"}]'
    azure_openai_api_key: ${{ secrets.AZURE_OPENAI_API_KEY }}
    azure_openai_endpoint: ${{ secrets.AZURE_OPENAI_ENDPOINT }}
    azure_openai_version: "2024-02-15-preview"
    openai_model: "gpt-4o"
```

### Release Trigger Example

Automatically generate release notes when a new release is published:

```yaml
name: Auto Release Notes

on:
  release:
    types: [published]

jobs:
  generate-release-notes:
    runs-on: ubuntu-22.04

    steps:
      - name: Get previous release tag
        id: prev_release
        run: |
          PREV_TAG=$(gh release list --repo ${{ github.repository }} --limit 2 | tail -1 | cut -f1)
          echo "tag=$PREV_TAG" >> $GITHUB_OUTPUT
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - uses: uug-ai/action-releasenotes@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          repositories: |
            [{"repo": "${{ github.repository }}", "from_release": "${{ steps.prev_release.outputs.tag }}", "to_release": "${{ github.event.release.tag_name }}"}]
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          release_title: "Release ${{ github.event.release.tag_name }}"
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `github_token` | The GitHub token for accessing repositories | Yes | - |
| `repositories` | JSON array of repository configurations (see format below) | No | `[]` |
| `raw_diffs` | JSON array of raw diff objects (see format below) | No | `[]` |
| `openai_api_key` | OpenAI API key (leave empty if using Azure OpenAI) | No | `''` |
| `azure_openai_api_key` | Azure OpenAI API key (leave empty if using OpenAI) | No | `''` |
| `azure_openai_endpoint` | Azure OpenAI endpoint | No | `''` |
| `azure_openai_version` | Azure OpenAI API version | No | `2024-02-15-preview` |
| `openai_model` | OpenAI model to use | No | `gpt-4o` |
| `max_tokens` | Maximum tokens for the completion | No | `2000` |
| `temperature` | Temperature for the model (0-2) | No | `0.6` |
| `release_title` | Title for the combined release notes | No | `Release Notes` |
| `include_diff_stats` | Include diff statistics in output | No | `true` |
| `custom_prompt` | Custom prompt for generating release notes | No | `''` |
| `frontend_context_file` | Path to a .txt file with frontend app description for test plan generation | No | `''` |
| `generate_test_plan` | Generate a test plan based on changes and frontend context | No | `false` |

**Note:** At least one of `repositories` or `raw_diffs` must be provided.

### Repository Configuration Format

Each repository in the `repositories` array should have:

```json
{
  "repo": "owner/repo-name",
  "from_release": "v1.0.0",
  "to_release": "v1.1.0"
}
```

- `repo`: The repository in `owner/repo` format
- `from_release`: The source release tag/version
- `to_release`: The destination release tag/version

### Raw Diffs Configuration Format

Each item in the `raw_diffs` array should have:

```json
{
  "name": "config.yaml",
  "diff": "@@ -1,3 +1,5 @@\n setting1: value1\n+setting2: value2\n+setting3: value3"
}
```

- `name`: A filename or identifier for the diff
- `diff`: The raw diff content (unified diff format recommended)

### Example with Raw Diffs

```yaml
name: Generate Release Notes with Raw Diffs

on:
  workflow_dispatch:

jobs:
  generate-release-notes:
    runs-on: ubuntu-22.04

    steps:
      - uses: uug-ai/action-releasenotes@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          repositories: |
            [
              {"repo": "myorg/backend", "from_release": "v1.0.0", "to_release": "v1.1.0"}
            ]
          raw_diffs: |
            [
              {"name": "helm-values.yaml", "diff": "@@ -10,6 +10,8 @@\n replicas: 2\n+resources:\n+  limits:\n+    memory: 512Mi"},
              {"name": "terraform-config.tf", "diff": "@@ -1,4 +1,6 @@\n resource \"aws_instance\" \"main\" {\n+  instance_type = \"t3.medium\"\n }"}
            ]
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          release_title: "Platform Release v1.1.0"
```

### Example with Only Raw Diffs

You can also use the action with only raw diffs (no repository comparisons):

```yaml
- uses: uug-ai/action-releasenotes@main
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    raw_diffs: |
      [
        {"name": "deployment.yaml", "diff": "..."},
        {"name": "service.yaml", "diff": "..."}
      ]
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
```

### Example with Test Plan Generation

Generate a test plan based on your frontend application context and code changes:

1. First, create a frontend context file (e.g., `frontend-context.txt`) that describes your application:

```txt
# My Application - Frontend Context

## Pages

### Login Page (/login)
- Email/password authentication
- "Forgot password" link
- "Remember me" checkbox
- OAuth login buttons (Google, GitHub)

### Dashboard (/dashboard)
- Overview statistics cards
- Recent activity feed
- Quick action buttons
- Charts showing usage trends

### Settings Page (/settings)
- Profile settings (name, email, avatar)
- Notification preferences
- Security settings (password change, 2FA)
- Theme preferences (light/dark mode)

### User Management (/admin/users)
- User list with search and filters
- Add/edit/delete users
- Role assignment
- Bulk actions

## Components

### Navigation
- Top navbar with user menu
- Sidebar with main navigation links
- Breadcrumbs

### Forms
- Form validation on all inputs
- Loading states during submission
- Error message display
```

2. Use the action with test plan generation enabled:

```yaml
- name: Checkout repository
  uses: actions/checkout@v4

- uses: uug-ai/action-releasenotes@main
  id: release_notes
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    repositories: |
      [
        {"repo": "myorg/frontend", "from_release": "v2.0.0", "to_release": "v2.1.0"}
      ]
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
    frontend_context_file: "./frontend-context.txt"
    generate_test_plan: "true"
    release_title: "Frontend Release v2.1.0"
```

The generated test plan will include:
- **Affected Features**: Which pages/components are affected by the changes
- **Test Scenarios**: Specific test cases with expected behavior
- **Regression Tests**: Areas that might be indirectly affected
- **Priority Levels**: Critical, High, Medium, Low prioritization

## Outputs

| Output | Description |
|--------|-------------|
| `release_notes` | The generated release notes in Markdown format |
| `summary` | A brief summary of all changes |
| `test_plan` | The generated test plan (if `frontend_context_file` is provided and `generate_test_plan` is `true`) |

### Using Outputs

```yaml
steps:
  - uses: uug-ai/action-releasenotes@main
    id: release_notes
    with:
      github_token: ${{ secrets.GITHUB_TOKEN }}
      repositories: '[{"repo": "owner/repo", "from_release": "v1.0.0", "to_release": "v1.1.0"}]'
      openai_api_key: ${{ secrets.OPENAI_API_KEY }}

  - name: Create Release
    uses: softprops/action-gh-release@v1
    with:
      body: ${{ steps.release_notes.outputs.release_notes }}
```

## Example Output

The generated release notes will look something like this:

```markdown
# Release Notes

*Generated on: 12345678*

## Overview

This release includes changes from the following repositories:

- **myorg/frontend**: v2.0.0 → v2.1.0
- **myorg/backend**: v1.5.0 → v1.6.0

## Statistics

| Repository | Commits | Files Changed | Additions | Deletions |
|------------|---------|---------------|-----------|----------|
| myorg/frontend | 15 | 23 | +456 | -123 |
| myorg/backend | 8 | 12 | +234 | -56 |

---

## myorg/frontend

**Release:** v2.0.0 → v2.1.0

### What's New

#### New Features
- **Dark Mode**: Added support for dark theme across all components
- **Search Enhancement**: Implemented fuzzy search for better results

#### Improvements
- Optimized bundle size by 15%
- Enhanced accessibility for screen readers

---

## myorg/backend

**Release:** v1.5.0 → v1.6.0

### What's New

#### New Features
- **API Rate Limiting**: Added configurable rate limiting per endpoint

#### Bug Fixes
- Fixed memory leak in connection pooling
- Resolved timezone handling issues

---
```

## Permissions

The GitHub token needs:
- `contents: read` permission on all repositories being compared
- For private repositories, use a PAT with appropriate access

## Troubleshooting

### `403` error when fetching repository data

Ensure your GitHub token has read access to the repositories. For private repositories or cross-organization access, you may need to use a Personal Access Token (PAT) instead of `GITHUB_TOKEN`.

### Token limits exceeded

If you're comparing releases with many changes, the diff might exceed token limits. The action will automatically truncate the prompt, but you may want to:
- Compare smaller release ranges
- Increase `max_tokens` for longer outputs
- Use a model with higher token limits (e.g., `gpt-4-turbo`)

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
