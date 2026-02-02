#!/bin/bash

set -eu

echo "Starting Release Notes Generator..."

# Debug: Print environment variables (without sensitive data)
echo "GitHub API URL: $GITHUB_API_URL"
echo "Repositories config received: ${INPUT_REPOSITORIES:0:100}..."

/action/generate_releasenotes.py \
  --github-api-url "$GITHUB_API_URL" \
  --github-token "$INPUT_GITHUB_TOKEN" \
  --repositories "$INPUT_REPOSITORIES" \
  --openai-api-key "${INPUT_OPENAI_API_KEY:-}" \
  --azure-openai-api-key "${INPUT_AZURE_OPENAI_API_KEY:-}" \
  --azure-openai-endpoint "${INPUT_AZURE_OPENAI_ENDPOINT:-}" \
  --azure-openai-version "${INPUT_AZURE_OPENAI_VERSION:-2024-02-15-preview}" \
  --openai-model "${INPUT_OPENAI_MODEL:-gpt-4o}" \
  --max-tokens "${INPUT_MAX_TOKENS:-2000}" \
  --temperature "${INPUT_TEMPERATURE:-0.6}" \
  --release-title "${INPUT_RELEASE_TITLE:-Release Notes}" \
  --include-diff-stats "${INPUT_INCLUDE_DIFF_STATS:-true}" \
  --custom-prompt "${INPUT_CUSTOM_PROMPT:-}"
