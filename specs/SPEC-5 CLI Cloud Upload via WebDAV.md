---
title: 'SPEC-5: CLI Cloud Upload via WebDAV'
type: spec
permalink: specs/spec-5-cli-cloud-upload-via-webdav
tags:
- cli
- webdav
- upload
- migration
- poc
---

# SPEC-5: CLI Cloud Upload via WebDAV

## Why

Existing basic-memory users need a simple migration path to basic-memory-cloud. The web UI drag-and-drop approach outlined in GitHub issue #59, while user-friendly, introduces significant complexity for a proof-of-concept:

- Complex web UI components for file upload and progress tracking
- Browser file handling limitations and CORS complexity
- Proxy routing overhead for large file transfers
- Authentication integration across multiple services

A CLI-first approach solves these issues by:

- **Leveraging existing infrastructure**: Both cloud CLI and tenant API already exist with WorkOS JWT authentication
- **Familiar user experience**: Basic-memory users are CLI-comfortable and expect command-line tools
- **Direct connection efficiency**: Bypassing the MCP gateway/proxy for bulk file transfers
- **Rapid implementation**: Building on existing `CLIAuth` and FastAPI foundations

The fundamental problem is migration friction - users have local basic-memory projects but no path to cloud tenants. A simple CLI upload command removes this barrier immediately.

## What

This spec defines a CLI-based project upload system using WebDAV for direct tenant connections.

**Affected Areas:**
- `apps/cloud/src/basic_memory_cloud/cli/main.py` - Add upload command to existing CLI
- `apps/api/src/basic_memory_cloud_api/main.py` - Add WebDAV endpoints to tenant FastAPI
- Authentication flow - Reuse existing WorkOS JWT validation
- File transfer protocol - WebDAV for cross-platform compatibility

**Core Components:**

### CLI Upload Command
```bash
basic-memory-cloud upload <project-path> --tenant-url https://basic-memory-{tenant}.fly.dev
```

### WebDAV Server Endpoints
- `GET/PUT/DELETE /webdav/*` - Standard WebDAV operations on tenant file system
- Authentication via existing JWT validation
- File operations preserve timestamps and directory structure

### Authentication Flow
```
1. User runs `basic-memory-cloud login` (existing)
2. CLI stores WorkOS JWT token (existing)
3. Upload command reads JWT from storage
4. WebDAV requests include JWT in Authorization header
5. Tenant API validates JWT using existing middleware
```

## How (High Level)

### Implementation Strategy

**Phase 1: CLI Command**
- Add `upload` command to existing Typer app
- Reuse `CLIAuth` class for token management
- Implement WebDAV client using `webdavclient3` or similar
- Rich progress bars for transfer feedback

**Phase 2: WebDAV Server**
- Add WebDAV endpoints to existing tenant FastAPI app
- Leverage existing `get_current_user` dependency for authentication
- Map WebDAV operations to tenant file system
- Preserve file modification times using `os.utime()`

**Phase 3: Integration**
- Direct connection bypasses MCP gateway and proxy
- Simple conflict resolution: overwrite existing files
- Error handling: fail fast with clear error messages

### Technical Architecture

```
basic-memory-cloud CLI → WorkOS JWT → Direct WebDAV → Tenant FastAPI
                                                    ↓
                                               Tenant File System
```

**Key Libraries:**
- CLI: `webdavclient3` for WebDAV client operations
- API: `wsgidav` or FastAPI-compatible WebDAV server
- Progress: `rich` library (already imported in CLI)
- Auth: Existing WorkOS JWT infrastructure

### WebDAV Protocol Choice

WebDAV provides:
- **Cross-platform clients**: Native support in most operating systems
- **Standardized protocol**: Well-defined for file operations
- **HTTP-based**: Works with existing FastAPI and JWT auth
- **Library support**: Good Python libraries for both client and server

### POC Constraints

**Simplifications for rapid implementation:**
- **Known tenant URLs**: Assume `https://basic-memory-{tenant}.fly.dev` format
- **Upload only**: No download or bidirectional sync
- **Overwrite conflicts**: No merge or conflict resolution prompting
- **No fallbacks**: Fail fast if WebDAV connection issues occur
- **Direct connection only**: No proxy fallback mechanism

## How to Evaluate

### Success Criteria

**Functional Requirements:**
- [ ] Transfer complete basic-memory project (100+ files) in < 30 seconds
- [ ] Preserve directory structure exactly as in source project
- [ ] Preserve file modification timestamps for proper sync behavior
- [ ] Rich progress bars show real-time transfer status (files/MB transferred)
- [ ] WorkOS JWT authentication validates correctly on WebDAV endpoints
- [ ] Direct tenant connection bypasses MCP gateway successfully

**Quality Requirements:**
- [ ] Clear error messages for authentication failures
- [ ] Graceful handling of network interruptions
- [ ] CLI follows existing command patterns and help text standards
- [ ] WebDAV endpoints integrate cleanly with existing FastAPI app

**Performance Requirements:**
- [ ] File transfer speed > 1MB/s on typical connections
- [ ] Memory usage remains reasonable for large projects
- [ ] No timeout issues with 500+ file projects

### Testing Procedure

**Unit Testing:**
1. CLI command parsing and argument validation
2. WebDAV client connection and authentication
3. File timestamp preservation during transfer
4. JWT token validation on WebDAV endpoints

**Integration Testing:**
1. End-to-end upload of test project
2. Direct tenant connection without proxy
3. File integrity verification after upload
4. Progress tracking accuracy during transfer

**User Experience Testing:**
1. Upload existing basic-memory project from local installation
2. Verify uploaded files appear correctly in cloud tenant
3. Confirm basic-memory database rebuilds properly with uploaded files
4. Test CLI help text and error message clarity

### Validation Commands

**Setup:**
```bash
# Login to WorkOS
basic-memory-cloud login

# Upload project
basic-memory-cloud upload ~/my-notes --tenant-url https://basic-memory-test.fly.dev
```

**Verification:**
```bash
# Check tenant health and file count via API
curl -H "Authorization: Bearer $JWT" https://basic-memory-test.fly.dev/health
curl -H "Authorization: Bearer $JWT" https://basic-memory-test.fly.dev/notes/search
```

### Performance Benchmarks

**Target metrics for 100MB basic-memory project:**
- Transfer time: < 30 seconds
- Memory usage: < 100MB during transfer
- Progress updates: Every 1MB or 10 files
- Authentication time: < 2 seconds

## Observations

- [implementation-speed] CLI approach significantly faster than web UI for POC development #rapid-prototyping
- [user-experience] Basic-memory users already comfortable with CLI tools #user-familiarity
- [architecture-benefit] Direct connection eliminates proxy complexity and latency #performance
- [auth-reuse] Existing WorkOS JWT infrastructure handles authentication cleanly #code-reuse
- [webdav-choice] WebDAV protocol provides cross-platform compatibility and standard libraries #protocol-selection
- [poc-scope] Simple conflict handling and error recovery sufficient for proof-of-concept #scope-management
- [migration-value] Removes primary barrier for local users migrating to cloud platform #business-value

## Relations

- depends_on [[SPEC-1: Specification-Driven Development Process]]
- enables [[GitHub Issue #59: Web UI Upload Feature]]
- uses [[WorkOS Authentication Integration]]
- builds_on [[Existing Cloud CLI Infrastructure]]
- builds_on [[Existing Tenant API Architecture]]