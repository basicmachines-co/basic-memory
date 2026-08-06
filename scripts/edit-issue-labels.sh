#!/usr/bin/env bash

# Apply one semantic triage classification to the issue that triggered the workflow.
set -euo pipefail

issue_number=$(jq -r '.issue.number // empty' "${GITHUB_EVENT_PATH:?GITHUB_EVENT_PATH not set}")
if ! [[ "$issue_number" =~ ^[0-9]+$ ]]; then
    echo "Error: no issue number in event payload" >&2
    exit 1
fi

type_label=""
component=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)
            if [[ $# -lt 2 ]]; then
                echo "Error: --type requires a value" >&2
                exit 1
            fi
            if [[ -n "$type_label" ]]; then
                echo "Error: --type may be provided only once" >&2
                exit 1
            fi
            type_label="$2"
            shift 2
            ;;
        --component)
            if [[ $# -lt 2 ]]; then
                echo "Error: --component requires a value" >&2
                exit 1
            fi
            if [[ -n "$component" ]]; then
                echo "Error: --component may be provided only once" >&2
                exit 1
            fi
            component="$2"
            shift 2
            ;;
        *)
            echo "Error: only --type and --component are accepted" >&2
            exit 1
            ;;
    esac
done

case "$type_label" in
    bug|enhancement|documentation|question) ;;
    "")
        echo "Error: --type is required" >&2
        exit 1
        ;;
    *)
        echo "Error: unsupported triage type: $type_label" >&2
        exit 1
        ;;
esac

case "$component" in
    cloud|none) ;;
    "")
        echo "Error: --component is required" >&2
        exit 1
        ;;
    *)
        echo "Error: unsupported triage component: $component" >&2
        exit 1
        ;;
esac

repository=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY not set}
labels_url="repos/$repository/issues/$issue_number/labels"

# The triage bot owns only the four type labels and the cloud component label. Read current
# labels only to find obsolete bot-owned values; targeted mutations leave every other label
# untouched, including labels added concurrently after this read.
if ! current_labels=$(gh api "repos/$repository/issues/$issue_number" --jq '.labels[].name'); then
    echo "Error: unable to read current issue labels" >&2
    exit 1
fi

obsolete_labels=()
while IFS= read -r label; do
    case "$label" in
        "$type_label") ;;
        cloud)
            if [[ "$component" != "cloud" ]]; then
                obsolete_labels+=("$label")
            fi
            ;;
        bug|enhancement|documentation|question) obsolete_labels+=("$label") ;;
    esac
done <<< "$current_labels"

desired_labels=("$type_label")
if [[ "$component" == "cloud" ]]; then
    desired_labels+=("cloud")
fi

api_args=(--method POST "$labels_url")
for label in "${desired_labels[@]}"; do
    api_args+=(-f "labels[]=$label")
done

# Add the desired classification before removing obsolete values. A failed additive request
# therefore leaves the prior classification intact; a later rerun can converge after a delete
# failure without losing unrelated labels.
gh api "${api_args[@]}" --silent

for label in "${obsolete_labels[@]}"; do
    gh api --method DELETE "$labels_url/$label" --silent
done

echo "Set triage labels: type=$type_label component=$component"
