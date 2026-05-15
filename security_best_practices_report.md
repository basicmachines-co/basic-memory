# Basic Memory Security Best Practices Review

Date: 2026-05-15

## Executive Summary

I found no confirmed critical issue in this static pass. The most important risk is exposure of the unauthenticated MCP HTTP/SSE transport through the Docker defaults: the container binds to `0.0.0.0`, publishes port `8000`, and the MCP server is created without an application-level auth configuration in this repo. If a user runs that compose file on a reachable host, an unauthenticated network caller can reach tools that read and write configured knowledge files.

The next highest issues are local supply-chain risk in the rclone installer fallback, cloud API keys being written into the general config file without an explicit restrictive chmod path, and import endpoints that parse uploaded exports without an application-level size limit.

This was a static source review against the Python/FastAPI and TypeScript/React security guidance. I did not run a live dependency advisory scan or exploit tests.

Follow-up fix status: findings 2, 3, and 4 have been addressed in the working tree after this report was written. Findings 1 and 5 are intentionally left for the broader Docker and tool UI removal decisions.

## Scope

- Backend: Python 3.12+, FastAPI, FastMCP, Typer, SQLAlchemy, Pydantic.
- Frontend: small TypeScript/React MCP tool UI bundle under `ui/tool-ui-react`.
- Reviewed surfaces: Docker/CLI server exposure, MCP/FastAPI routing, auth/token handling, file boundaries, upload/import handling, subprocess usage, outbound HTTP, raw SQL patterns, and frontend message/XSS sinks.

## Critical Findings

None confirmed in this pass.

## High Severity

### 1. Docker and HTTP MCP defaults can expose an unauthenticated file-access tool server

Rule ID: FASTAPI-AUTH-001 / deployment boundary

Location:
- `Dockerfile`, MCP command, lines 51-52
- `docker-compose.yml`, port publication and command, lines 47-53
- `src/basic_memory/cli/commands/mcp.py`, HTTP host default and server run, lines 28-31 and 109-115
- `src/basic_memory/mcp/server.py`, FastMCP construction, lines 144-147
- `SECURITY.md`, intended localhost posture, lines 68-69 and 77

Evidence:
```text
Dockerfile:52 CMD ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
docker-compose.yml:49 - "8000:8000"
docker-compose.yml:52 # IMPORTANT: The SSE and streamable-http endpoints are not secured
docker-compose.yml:53 command: ["basic-memory", "mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
mcp.py:29-30 host defaults to "0.0.0.0"
server.py:144-147 mcp = FastMCP(name="Basic Memory", lifespan=lifespan)
SECURITY.md:68-69 says Basic Memory does not open network ports by default and the optional REST API is intended for localhost use.
```

Impact: If Docker is run on a host reachable from a LAN or the internet, unauthenticated callers can access MCP tools that read, write, move, or delete files inside mounted project directories.

Fix: Change Docker and compose defaults to bind localhost only, for example `127.0.0.1:8000:8000` and `--host 127.0.0.1`. Consider making non-local binding require an explicit opt-in flag with a loud warning, or add bearer-token auth for HTTP/SSE transports before advertising web deployment.

Mitigation: Document that users must put the service behind a trusted reverse proxy or tunnel with authentication before exposing it beyond localhost. Add a startup warning when HTTP/SSE is bound to `0.0.0.0`.

False positive notes: This is not an issue for the default `stdio` transport. It becomes a real exposure when the Docker defaults or HTTP/SSE command are used on a reachable interface.

### 2. rclone installer fallback executes an unauthenticated remote install script as root

Rule ID: FASTAPI-INJECT-002 / supply-chain execution

Location:
- `src/basic_memory/cli/commands/cloud/rclone_installer.py`, macOS fallback, line 72
- `src/basic_memory/cli/commands/cloud/rclone_installer.py`, Linux fallback, line 106

Evidence:
```text
run_command(["sh", "-c", "curl https://rclone.org/install.sh | sudo bash"])
```

Impact: If the download endpoint, DNS path, TLS trust chain, or script source is compromised, the installer executes attacker-controlled code with root privileges.

Fix: Avoid `curl | sudo bash`. Prefer supported package managers only, or download a pinned release artifact, verify its checksum/signature, and install using list-form subprocess arguments without a shell.

Mitigation: Keep the current package-manager paths as primary, but replace the fallback with manual instructions until a verified artifact install path exists.

False positive notes: The command string is hardcoded, so this is not user-input command injection. The concern is privileged remote-code execution and supply-chain trust.

## Medium Severity

### 3. Cloud API keys are stored in the general config file without explicit restrictive permissions

Rule ID: secret storage / local credential exposure

Location:
- `src/basic_memory/config.py`, `cloud_api_key` config field, lines 489-492
- `src/basic_memory/cli/commands/cloud/core_commands.py`, save/create writes key into config, lines 237-240 and 276-280
- `src/basic_memory/config.py`, config write helper, lines 1045-1050
- Contrast: `src/basic_memory/cli/auth.py`, OAuth token file chmod, lines 191-195

Evidence:
```text
config.py:489-492 stores cloud_api_key in BasicMemoryConfig.
core_commands.py:239-240 sets config.cloud_api_key and calls config_manager.save_config(config).
config.py:1049-1050 dumps the config and writes it with file_path.write_text(...).
auth.py:191-195 writes OAuth tokens and then calls os.chmod(self.token_file, 0o600).
```

Impact: On systems with permissive umasks, shared workstations, or mounted config volumes, another local user or process may be able to read a long-lived Basic Memory Cloud API key.

Fix: After every config write, set the config file to `0o600` and the config directory to `0o700` where the platform supports it. Longer term, store `cloud_api_key` in the same protected token path or an OS keychain instead of the general JSON config.

Mitigation: Document that config files contain credentials and should be treated as secrets. Add a startup or `bm cloud status` warning if config file permissions are group/world readable.

False positive notes: Existing user umask may already create `0600` files on some machines. The code does not enforce that guarantee, while it does enforce it for OAuth tokens.

### 4. Import endpoints parse uploaded files without an application-level size limit

Rule ID: FASTAPI-LIMITS-001 / FASTAPI-UPLOAD-001

Location:
- `src/basic_memory/api/v2/routers/importer_router.py`, upload routes, lines 30-35, 55-60, 80-85, and 105-110
- `src/basic_memory/api/v2/routers/importer_router.py`, memory JSON read, lines 127-135
- `src/basic_memory/api/v2/routers/importer_router.py`, generic import helper, lines 150-167

Evidence:
```text
file: UploadFile
file_bytes = await file.read()
file_str = file_bytes.decode("utf-8")
json_data = json.load(file.file)
result = await importer.import_data(json_data, destination_directory)
```

Impact: A caller who can reach these endpoints can force the process to read and parse very large request bodies, causing memory or CPU exhaustion. This is especially relevant if the API is exposed through cloud/proxy layers or Docker HTTP transport.

Fix: Add a configurable maximum import size, enforce `Content-Length` when present, and stream-read with a hard byte cap when it is absent. Return `413 Payload Too Large` for oversized uploads and `400 Bad Request` for invalid JSON.

Mitigation: Enforce upload limits at the reverse proxy/load balancer and keep Starlette/FastAPI on patched versions for multipart handling. Add tests for oversized uploads.

False positive notes: Local-only CLI/API use lowers exploitability, but the code itself has no visible in-app size guard.

## Low Severity

### 5. Tool UI iframes accept render data from any message sender

Rule ID: REACT cross-window messaging

Location:
- `ui/tool-ui-react/src/note-preview.tsx`, message listener and wildcard parent messages, lines 60-83
- `ui/tool-ui-react/src/search-results.tsx`, message listener and wildcard parent messages, lines 98-127

Evidence:
```text
window.addEventListener("message", handleMessage);
window.parent?.postMessage({ type: "ui-lifecycle-iframe-ready" }, "*");
window.parent?.postMessage({ type: "ui-request-render-data" }, "*");
```

Impact: Another frame or window with access to the iframe can spoof `ui-lifecycle-iframe-render-data` messages and control what the UI displays. React escapes the displayed text, so I did not find a direct XSS path here; the concern is message-boundary integrity.

Fix: Check `event.source === window.parent`, validate message shape with a small schema, and use a known `targetOrigin` where the MCP Apps host can provide one. If the host origin is intentionally variable, document that boundary and keep payloads non-sensitive.

Mitigation: Continue rendering values through React text interpolation, not raw HTML. Avoid adding `dangerouslySetInnerHTML` or direct DOM sinks to these components.

False positive notes: The outbound wildcard messages only send readiness/request signals, not note content. This is why the severity is low.

## Reviewed But Not Flagged

- Path traversal controls are present on resource file paths: `valid_project_path_value()` blocks absolute paths, traversal segments, home expansion, and control characters, and `validate_project_path()` resolves the final path and checks `is_relative_to()` (`src/basic_memory/utils.py:670-716`). Resource routes call this before reading/writing project files (`src/basic_memory/api/v2/routers/resource_router.py:83-91`, `149-159`, and `274-283`).
- I did not find CORS middleware, cookie sessions, WebSocket routes, `FileResponse`, `StaticFiles`, frontend `dangerouslySetInnerHTML`, frontend DOM injection sinks, `localStorage` token storage, or frontend dynamic navigation sinks in the searched source.
- The raw SQL I spot-checked uses SQLAlchemy parameters for untrusted values. Some SQL strings are assembled with generated placeholder lists or dialect fragments; I did not find a clear user-input SQL injection path in this pass.
- The formatter subprocess path avoids `shell=True` and uses `shlex.split()` plus `asyncio.create_subprocess_exec()` (`src/basic_memory/file_utils.py:238-250`). The `find`-based sync optimizations also use `asyncio.create_subprocess_exec()` with argument lists.

## Suggested Fix Order

1. Lock down Docker/HTTP MCP exposure first: bind localhost by default and add explicit warnings or auth for non-local HTTP/SSE.
2. Replace `curl | sudo bash` rclone install fallback with a verified artifact or manual instructions.
3. Enforce restrictive config permissions for files that can contain `cloud_api_key`.
4. Add import upload size limits and regression tests.
5. Tighten iframe message origin/source validation in the React tool UI.
