#!/usr/bin/env bash

# Limit automated issue triage to labels on the issue that triggered the workflow.
set -euo pipefail

issue_number=$(jq -r '.issue.number // empty' "${GITHUB_EVENT_PATH:?GITHUB_EVENT_PATH not set}")
if ! [[ "$issue_number" =~ ^[0-9]+$ ]]; then
    echo "Error: no issue number in event payload" >&2
    exit 1
fi

labels=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --add-label)
            if [[ $# -lt 2 ]]; then
                echo "Error: --add-label requires a label" >&2
                exit 1
            fi
            labels+=("$2")
            shift 2
            ;;
        *)
            echo "Error: only --add-label is accepted" >&2
            exit 1
            ;;
    esac
done

if [[ ${#labels[@]} -eq 0 ]]; then
    echo "Error: at least one label is required" >&2
    exit 1
fi

valid_labels=$(gh label list --limit 500 --json name --jq '.[].name')
filtered_labels=()
for label in "${labels[@]}"; do
    if grep -qxF "$label" <<<"$valid_labels"; then
        filtered_labels+=("$label")
    else
        echo "Ignoring unknown label: $label" >&2
    fi
done

if [[ ${#filtered_labels[@]} -eq 0 ]]; then
    exit 0
fi

repository=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY not set}
labels_url="repos/$repository/issues/$issue_number/labels"
api_args=(--method POST "$labels_url")
for label in "${filtered_labels[@]}"; do
    api_args+=(-f "labels[]=$label")
done

gh api "${api_args[@]}" --silent
echo "Added: ${filtered_labels[*]}"
