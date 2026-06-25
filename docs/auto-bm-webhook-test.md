# Auto BM webhook test

This documentation-only file exists to exercise the Auto BM GitHub webhook path.

The expected flow is:

1. Open a small pull request with this file.
2. Merge the pull request.
3. Let the GitHub `pull_request` webhook call the Hermes Auto BM endpoint.
4. Confirm Basic Memory records a project-update note for the merged PR.

No runtime code or product behavior changes here.
