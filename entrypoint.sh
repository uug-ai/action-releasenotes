#!/bin/bash

set -eu

echo "Starting Release Notes Generator..."

# Debug: Print environment variables (without sensitive data)
echo "GitHub API URL: $GITHUB_API_URL"
echo "Repositories config received: ${INPUT_REPOSITORIES:0:100}..."
echo "Raw diffs config received: ${INPUT_RAW_DIFFS:0:100}..."

# Debug: Check which API keys are provided (without revealing values)
if [ -n "${INPUT_OPENAI_API_KEY:-}" ]; then
    echo "OpenAI API key: provided (length: ${#INPUT_OPENAI_API_KEY})"
else
    echo "OpenAI API key: not provided"
fi

if [ -n "${INPUT_AZURE_OPENAI_API_KEY:-}" ]; then
    echo "Azure OpenAI API key: provided (length: ${#INPUT_AZURE_OPENAI_API_KEY})"
else
    echo "Azure OpenAI API key: not provided"
fi

if [ -n "${INPUT_AZURE_OPENAI_ENDPOINT:-}" ]; then
    echo "Azure OpenAI endpoint: provided"
else
    echo "Azure OpenAI endpoint: not provided"
fi

/action/generate_releasenotes.py \
  --github-api-url "$GITHUB_API_URL" \
  --github-token "$INPUT_GITHUB_TOKEN" \
  --repositories "${INPUT_REPOSITORIES:-[]}" \
  --raw-diffs "${INPUT_RAW_DIFFS:-[]}" \
  --openai-api-key "${INPUT_OPENAI_API_KEY:-}" \
  --azure-openai-api-key "${INPUT_AZURE_OPENAI_API_KEY:-}" \
  --azure-openai-endpoint "${INPUT_AZURE_OPENAI_ENDPOINT:-}" \
  --azure-openai-version "${INPUT_AZURE_OPENAI_VERSION:-2024-02-15-preview}" \
  --openai-model "${INPUT_OPENAI_MODEL:-gpt-4o}" \
  --max-tokens "${INPUT_MAX_TOKENS:-2000}" \
  --temperature "${INPUT_TEMPERATURE:-0.6}" \
  --release-title "${INPUT_RELEASE_TITLE:-Release Notes}" \
  --include-diff-stats "${INPUT_INCLUDE_DIFF_STATS:-true}" \
  --custom-prompt "${INPUT_CUSTOM_PROMPT:-}" \
  --frontend-context-file "${INPUT_FRONTEND_CONTEXT_FILE:-}" \
  --generate-test-plan "${INPUT_GENERATE_TEST_PLAN:-false}"
