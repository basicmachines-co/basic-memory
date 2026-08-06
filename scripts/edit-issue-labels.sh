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

# The triage bot owns only the four type labels and the cloud component label. Preserve every
# label outside that owned set while replacing prior triage output.
labels=()
while IFS= read -r label; do
    case "$label" in
        bug|enhancement|documentation|question|cloud) ;;
        "") ;;
        *) labels+=("$label") ;;
    esac
done < <(gh api "repos/$repository/issues/$issue_number" --jq '.labels[].name')

labels+=("$type_label")
if [[ "$component" == "cloud" ]]; then
    labels+=("cloud")
fi

api_args=(--method PUT "$labels_url")
for label in "${labels[@]}"; do
    api_args+=(-f "labels[]=$label")
done

gh api "${api_args[@]}" --silent
echo "Set triage labels: type=$type_label component=$component"
