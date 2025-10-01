---
title: 'SPEC-8: TigrisFS Integration for Tenant API'
Date: September 22, 2025
Status: Phase 3.6 Complete - Tenant Mount API Endpoints Ready for CLI Implementation
Priority: High
Goal: Replace Fly volumes with Tigris bucket provisioning in production tenant API
permalink: spec-8-tigris-fs-integration
---

## Executive Summary

Based on SPEC-7 Phase 4 POC testing, this spec outlines productizing the TigrisFS/rclone implementation in the Basic Memory Cloud tenant API. 
We're moving from proof-of-concept to production integration, replacing Fly volume storage with Tigris bucket-per-tenant architecture.

## Current Architecture (Fly Volumes)

### Tenant Provisioning Flow
```python
# apps/cloud/src/basic_memory_cloud/workflows/tenant_provisioning.py
async def provision_tenant_infrastructure(tenant_id: str):
    # 1. Create Fly app
    # 2. Create Fly volume  ← REPLACE THIS
    # 3. Deploy API container with volume mount
    # 4. Configure health checks
```

### Storage Implementation
- Each tenant gets dedicated Fly volume (1GB-10GB)
- Volume mounted at `/app/data` in API container
- Local filesystem storage with Basic Memory indexing
- No global caching or edge distribution

## Proposed Architecture (Tigris Buckets)

### New Tenant Provisioning Flow
```python
async def provision_tenant_infrastructure(tenant_id: str):
    # 1. Create Fly app
    # 2. Create Tigris bucket with admin credentials  ← NEW
    # 3. Store bucket name in tenant record  ← NEW
    # 4. Deploy API container with TigrisFS mount using admin credentials
    # 5. Configure health checks
```

### Storage Implementation
- Each tenant gets dedicated Tigris bucket
- TigrisFS mounts bucket at `/app/data` in API container
- Global edge caching and distribution
- Configurable cache TTL for sync performance

## Implementation Plan

### Phase 1: Bucket Provisioning Service

**✅ IMPLEMENTED: StorageClient with Admin Credentials**
```python
# apps/cloud/src/basic_memory_cloud/clients/storage_client.py
class StorageClient:
    async def create_tenant_bucket(self, tenant_id: UUID) -> TigrisBucketCredentials
    async def delete_tenant_bucket(self, tenant_id: UUID, bucket_name: str) -> bool
    async def list_buckets(self) -> list[TigrisBucketResponse]
    async def test_tenant_credentials(self, credentials: TigrisBucketCredentials) -> bool
```

**Simplified Architecture Using Admin Credentials:**
- Single admin access key with full Tigris permissions (configured in console)
- No tenant-specific IAM user creation needed
- Bucket-per-tenant isolation for logical separation
- Admin credentials shared across all tenant operations

**Integrate with Provisioning workflow:**
```python
# Update tenant_provisioning.py
async def provision_tenant_infrastructure(tenant_id: str):
    storage_client = StorageClient(settings.aws_access_key_id, settings.aws_secret_access_key)
    bucket_creds = await storage_client.create_tenant_bucket(tenant_id)
    await store_bucket_name(tenant_id, bucket_creds.bucket_name)
    await deploy_api_with_tigris(tenant_id, bucket_creds)
```

### Phase 2: Simplified Bucket Management

**✅ SIMPLIFIED: Admin Credentials + Bucket Names Only**

Since we use admin credentials for all operations, we only need to track bucket names per tenant:

1. **Primary Storage (Fly Secrets)**
   ```bash
   flyctl secrets set -a basic-memory-{tenant_id} \
     AWS_ACCESS_KEY_ID="{admin_access_key}" \
     AWS_SECRET_ACCESS_KEY="{admin_secret_key}" \
     AWS_ENDPOINT_URL_S3="https://fly.storage.tigris.dev" \
     AWS_REGION="auto" \
     BUCKET_NAME="basic-memory-{tenant_id}"
   ```

2. **Database Storage (Bucket Name Only)**
   ```python
   # apps/cloud/src/basic_memory_cloud/models/tenant.py
   class Tenant(BaseModel):
       # ... existing fields
       tigris_bucket_name: Optional[str] = None  # Just store bucket name
       tigris_region: str = "auto"
       created_at: datetime
   ```

**Benefits of Simplified Approach:**
- No credential encryption/decryption needed
- Admin credentials managed centrally in environment
- Only bucket names stored in database (not sensitive)
- Simplified backup/restore scenarios
- Reduced security attack surface

### Phase 3: API Container Updates

**Update API container configuration:**
```dockerfile
# apps/api/Dockerfile
# Add TigrisFS installation
RUN curl -L https://github.com/tigrisdata/tigrisfs/releases/latest/download/tigrisfs-linux-amd64 \
    -o /usr/local/bin/tigrisfs && chmod +x /usr/local/bin/tigrisfs
```

**Startup script integration:**
```bash
# apps/api/tigrisfs-startup.sh (already exists)
# Mount TigrisFS → Start Basic Memory API
exec python -m basic_memory_cloud_api.main
```

**Fly.toml environment (optimized for < 5s startup):**
```toml
# apps/api/fly.tigris-production.toml
[env]
  TIGRISFS_MEMORY_LIMIT = '1024'     # Reduced for faster init
  TIGRISFS_MAX_FLUSHERS = '16'       # Fewer threads for faster startup
  TIGRISFS_STAT_CACHE_TTL = '30s'    # Balance sync speed vs startup
  TIGRISFS_LAZY_INIT = 'true'        # Enable lazy loading
  BASIC_MEMORY_HOME = '/app/data'

# Suspend optimization for wake-on-network
[machine]
  auto_stop_machines = "suspend"     # Faster than full stop
  auto_start_machines = true
  min_machines_running = 0
```

### Phase 4: Local Access Features

**CLI automation for local mounting:**
```python
# New CLI command: basic-memory cloud mount
async def setup_local_mount(tenant_id: str):
    # 1. Fetch bucket credentials from cloud API
    # 2. Configure rclone with scoped IAM policy
    # 3. Mount via rclone nfsmount (macOS) or FUSE (Linux)
    # 4. Start Basic Memory sync watcher
```

**Local mount configuration:**
```bash
# rclone config for tenant
rclone mount basic-memory-{tenant_id}: ~/basic-memory-{tenant_id} \
  --nfs-mount \
  --vfs-cache-mode writes \
  --cache-dir ~/.cache/rclone/basic-memory-{tenant_id}
```

### Phase 5: TigrisFS Cache Sync Solutions

**Problem**: When files are uploaded via CLI/bisync, the tenant API container doesn't see them immediately due to TigrisFS cache (30s TTL) and lack of inotify events on mounted filesystems.

**Multi-Layer Solution:**

**Layer 1: API Sync Endpoint** (Immediate)
```python
# POST /sync - Force TigrisFS cache refresh
# Callable by CLI after uploads
subprocess.run(["sync", "fsync /app/data"], check=True)
```

**Layer 2: Tigris Webhook Integration** (Real-time)
https://www.tigrisdata.com/docs/buckets/object-notifications/#webhook
```python
# Webhook endpoint for bucket changes
@app.post("/webhooks/tigris/{tenant_id}")
async def handle_bucket_notification(tenant_id: str, event: TigrisEvent):
    if event.eventName in ["OBJECT_CREATED_PUT", "OBJECT_DELETED"]:
        await notify_container_sync(tenant_id, event.object.key)
```

**Layer 3: CLI Sync Notification** (User-triggered)
```bash
# CLI calls container sync endpoint after successful bisync
basic-memory cloud bisync  # Automatically notifies container
curl -X POST https://basic-memory-{tenant-id}.fly.dev/sync
```

**Layer 4: Periodic Sync Fallback** (Safety net)
```python
# Background task: fsync /app/data every 30s as fallback
# Ensures eventual consistency even if other layers fail
```

**Implementation Priority:**
1. Layer 1 (API endpoint) - Quick testing capability
2. Layer 3 (CLI integration) - Improved UX
3. Layer 4 (Periodic fallback) - Safety net
4. Layer 2 (Webhooks) - Production real-time sync


## Performance Targets

### Sync Latency
- **Target**: < 5 seconds local→cloud→container
- **Configuration**: `TIGRISFS_STAT_CACHE_TTL = '5s'`
- **Monitoring**: Track sync metrics in production

### Container Startup
- **Target**: < 5 seconds including TigrisFS mount
- **Fast retry**: 0.5s intervals for mount verification
- **Fallback**: Container fails fast if mount fails

### Memory Usage
- **TigrisFS cache**: 2GB memory limit per container
- **Concurrent uploads**: 32 flushers max
- **VM sizing**: shared-cpu-2x (2048mb) minimum

## Security Considerations

### Bucket Isolation
- Each tenant has dedicated bucket
- IAM policies prevent cross-tenant access
- No shared bucket with subdirectories

### Credential Security
- Fly secrets for runtime access
- Encrypted database backup for disaster recovery
- Credential rotation capability

### Data Residency
- Tigris global edge caching
- SOC2 Type II compliance
- Encryption at rest and in transit

## Operational Benefits

### Scalability
- Horizontal scaling with stateless API containers
- Global edge distribution
- Better resource utilization

### Reliability
- No cold starts between tenants
- Built-in redundancy and caching
- Simplified backup strategy

### Cost Efficiency
- Pay-per-use storage pricing
- Shared infrastructure benefits
- Reduced operational overhead

## Risk Mitigation

### Data Loss Prevention
- Dual credential storage (Fly + database)
- Automated backup workflows to R2/S3
- Tigris built-in redundancy

### Performance Degradation
- Configurable cache settings per tenant
- Monitoring and alerting on sync latency
- Fallback to volume storage if needed

### Security Vulnerabilities
- Bucket-per-tenant isolation
- Regular credential rotation
- Security scanning and monitoring

## Success Metrics

### Technical Metrics
- Sync latency P50 < 5 seconds
- Container startup time < 5 seconds
- Zero data loss incidents
- 99.9% uptime per tenant

### Business Metrics
- Reduced infrastructure costs vs volumes
- Improved user experience with faster sync
- Enhanced enterprise security posture
- Simplified operational overhead

## Open Questions

1. **Tigris rate limits**: What are the API limits for bucket creation?
2. **Cost analysis**: What's the break-even point vs Fly volumes?
3. **Regional preferences**: Should enterprise customers choose regions?
4. **Backup retention**: How long to keep automated backups?

## Implementation Checklist

### Phase 1: Bucket Provisioning Service ✅ COMPLETED
- [x] **Research Tigris bucket API** - Document bucket creation and S3 API compatibility
- [x] **Create StorageClient class** - Implemented with admin credentials and comprehensive integration tests
- [x] **Test bucket creation** - Full test suite validates API integration with real Tigris environment
- [x] **Add bucket provisioning to DBOS workflow** - Integrated StorageClient with tenant_provisioning.py

### Phase 2: Simplified Bucket Management ✅ COMPLETED
- [x] **Update Tenant model** with tigris_bucket_name field (replaced fly_volume_id)
- [x] **Implement bucket name storage** - Database migration and model updates completed
- [x] **Test bucket provisioning integration** - Full test suite validates workflow from tenant creation to bucket assignment
- [x] **Remove volume logic from all tests** - Complete migration from volume-based to bucket-based architecture

### Phase 3: API Container Integration ✅ COMPLETED
- [x] **Update Dockerfile** to install TigrisFS binary in API container with configurable version
- [x] **Optimize tigrisfs-startup.sh** with production-ready security and reliability improvements
- [x] **Create production-ready container** with proper signal handling and mount validation
- [x] **Implement security fixes** based on Claude code review (conditional debug, credential protection)
- [x] **Add proper process supervision** with cleanup traps and error handling
- [x] **Remove debug artifacts** - Cleaned up all debug Dockerfiles and test scripts

### Phase 3.5: IAM Access Key Management ✅ COMPLETED
- [x] **Research Tigris IAM API** - Documented create_policy, attach_user_policy, delete_access_key operations
- [x] **Implement bucket-scoped credential generation** - StorageClient.create_tenant_access_keys() with IAM policies
- [x] **Add comprehensive security test suite** - 5 security-focused integration tests covering all attack vectors
- [x] **Verify cross-bucket access prevention** - Scoped credentials can ONLY access their designated bucket
- [x] **Test credential lifecycle management** - Create, validate, delete, and revoke access keys
- [x] **Validate admin vs scoped credential isolation** - Different access patterns and security boundaries
- [x] **Test multi-tenant isolation** - Multiple tenants cannot access each other's buckets

### Phase 3.6: Tenant Mount API Endpoints ✅ COMPLETED
- [x] **Implement GET /tenant/mount/info** - Returns mount info without exposing credentials
- [x] **Implement POST /tenant/mount/credentials** - Creates new bucket-scoped credentials for CLI mounting
- [x] **Implement DELETE /tenant/mount/credentials/{cred_id}** - Revoke specific credentials with proper cleanup
- [x] **Implement GET /tenant/mount/credentials** - List active credentials without exposing secrets
- [x] **Add TenantMountCredentials database model** - Tracks credential metadata (no secret storage)
- [x] **Create comprehensive test suite** - 28 tests covering all scenarios including multi-session support
- [x] **Implement multi-session credential flow** - Multiple active credentials per tenant supported
- [x] **Secure credential handling** - Secret keys never stored, returned once only for immediate use
- [x] **Add dependency injection for StorageClient** - Clean integration with existing API architecture
- [x] **Fix Tigris configuration for cloud service** - Added AWS environment variables to fly.template.toml
- [x] **Update tenant machine configurations** - Include AWS credentials for TigrisFS mounting with clear credential strategy

**Security Test Results:**
```
✅ Cross-bucket access prevention - PASS
✅ Deleted credentials access revoked - PASS
✅ Invalid credentials rejected - PASS
✅ Admin vs scoped credential isolation - PASS
✅ Multiple scoped credentials isolation - PASS
```

**Implementation Details:**
- Uses Tigris IAM managed policies (create_policy + attach_user_policy)
- Bucket-scoped S3 policies with Actions: GetObject, PutObject, DeleteObject, ListBucket
- Resource ARNs limited to specific bucket: `arn:aws:s3:::bucket-name` and `arn:aws:s3:::bucket-name/*`
- Access keys follow Tigris format: `tid_` prefix with secure random suffix
- Complete cleanup on deletion removes both access keys and associated policies

### Phase 4: Local Access CLI
- [x] **Design local mount CLI command** for automated rclone configuration
- [x] **Implement credential fetching** from cloud API for local setup
- [x] **Create rclone config automation** for tenant-specific bucket mounting
- [x] **Test local→cloud→container sync** with optimized cache settings
- [x] **Document local access setup** for beta users

### Phase 5: Webhook Integration (Future)
- [ ] **Research Tigris webhook API** for object notifications and payload format
- [ ] **Design webhook endpoint** for real-time sync notifications
- [ ] **Implement notification handling** to trigger Basic Memory sync events
- [ ] **Test webhook delivery** and sync latency improvements

## Success Metrics
- [ ] **Container startup < 5 seconds** including TigrisFS mount and Basic Memory init
- [ ] **Sync latency < 5 seconds** for local→cloud→container file changes
- [ ] **Zero data loss** during bucket provisioning and credential management
- [ ] **100% test coverage** for new TigrisBucketService and credential functions
- [ ] **Beta deployment** with internal users validating local-cloud workflow



## Implementation Notes

## Phase 4.1: Bidirectional Sync with rclone bisync (NEW)

### Problem Statement
During testing, we discovered that some applications (particularly Obsidian) don't detect file changes over NFS mounts. Rather than building a custom sync daemon, we can leverage `rclone bisync` - rclone's built-in bidirectional synchronization feature.

### Solution: rclone bisync
Use rclone's proven bidirectional sync instead of custom implementation:

**Core Architecture:**
```bash
# rclone bisync handles all the complexity
rclone bisync ~/basic-memory-{tenant_id} basic-memory-{tenant_id}:{bucket_name} \
  --create-empty-src-dirs \
  --conflict-resolve newer \
  --resilient \
  --check-access
```

**Key Benefits:**
- ✅ **Battle-tested**: Production-proven rclone functionality
- ✅ **MIT licensed**: Open source with permissive licensing
- ✅ **No custom code**: Zero maintenance burden for sync logic
- ✅ **Built-in safety**: max-delete protection, conflict resolution
- ✅ **Simple installation**: Works with Homebrew rclone (no FUSE needed)
- ✅ **File watcher compatible**: Works with Obsidian and all applications
- ✅ **Offline support**: Can work offline and sync when connected

### bisync Conflict Resolution Options

**Built-in conflict strategies:**
```bash
--conflict-resolve none     # Keep both files with .conflict suffixes (safest)
--conflict-resolve newer    # Always pick the most recently modified file
--conflict-resolve larger   # Choose based on file size
--conflict-resolve path1    # Always prefer local changes
--conflict-resolve path2    # Always prefer cloud changes
```

### Sync Profiles Using bisync

**Profile configurations:**
```python
BISYNC_PROFILES = {
    "safe": {
        "conflict_resolve": "none",      # Keep both versions
        "max_delete": 10,                # Prevent mass deletion
        "check_access": True,            # Verify sync integrity
        "description": "Safe mode with conflict preservation"
    },
    "balanced": {
        "conflict_resolve": "newer",     # Auto-resolve to newer file
        "max_delete": 25,
        "check_access": True,
        "description": "Balanced mode (recommended default)"
    },
    "fast": {
        "conflict_resolve": "newer",
        "max_delete": 50,
        "check_access": False,           # Skip verification for speed
        "description": "Fast mode for rapid iteration"
    }
}
```

### CLI Commands

**Manual sync commands:**
```bash
basic-memory cloud bisync                    # Manual bidirectional sync
basic-memory cloud bisync --dry-run          # Preview changes
basic-memory cloud bisync --profile safe     # Use specific profile
basic-memory cloud bisync --resync           # Force full baseline resync
```

**Watch mode (Step 1):**
```bash
basic-memory cloud bisync --watch            # Long-running process, sync every 60s
basic-memory cloud bisync --watch --interval 30s  # Custom interval
```

**System integration (Step 2 - Future):**
```bash
basic-memory cloud bisync-service install    # Install as system service
basic-memory cloud bisync-service start      # Start background service
basic-memory cloud bisync-service status     # Check service status
```

### Implementation Strategy

**Phase 4.1.1: Core bisync Implementation**
- [ ] Implement `run_bisync()` function wrapping rclone bisync
- [ ] Add profile-based configuration (safe/balanced/fast)
- [ ] Create conflict resolution and safety options
- [ ] Test with sample files and conflict scenarios

**Phase 4.1.2: Watch Mode**
- [ ] Add `--watch` flag for continuous sync
- [ ] Implement configurable sync intervals
- [ ] Add graceful shutdown and signal handling
- [ ] Create status monitoring and progress indicators

**Phase 4.1.3: User Experience**
- [ ] Add conflict reporting and resolution guidance
- [ ] Implement dry-run preview functionality
- [ ] Create troubleshooting and diagnostic commands
- [ ] Add filtering configuration (.gitignore-style)

**Phase 4.1.4: System Integration (Future)**
- [ ] Generate platform-specific service files (launchd/systemd)
- [ ] Add service management commands
- [ ] Implement automatic startup and recovery
- [ ] Create monitoring and logging integration

### Technical Implementation

**Core bisync wrapper:**
```python
def run_bisync(
    tenant_id: str,
    bucket_name: str,
    profile: str = "balanced",
    dry_run: bool = False
) -> bool:
    """Run rclone bisync with specified profile."""

    local_path = Path.home() / f"basic-memory-{tenant_id}"
    remote_path = f"basic-memory-{tenant_id}:{bucket_name}"
    profile_config = BISYNC_PROFILES[profile]

    cmd = [
        "rclone", "bisync",
        str(local_path), remote_path,
        "--create-empty-src-dirs",
        "--resilient",
        f"--conflict-resolve={profile_config['conflict_resolve']}",
        f"--max-delete={profile_config['max_delete']}",
        "--filters-file", "~/.basic-memory/bisync-filters.txt"
    ]

    if profile_config.get("check_access"):
        cmd.append("--check-access")

    if dry_run:
        cmd.append("--dry-run")

    return subprocess.run(cmd, check=True).returncode == 0
```

**Default filter file (~/.basic-memory/bisync-filters.txt):**
```
- .DS_Store
- .git/**
- __pycache__/**
- *.pyc
- .pytest_cache/**
- node_modules/**
- .conflict-*
- Thumbs.db
- desktop.ini
```

**Advantages Over Custom Daemon:**
- ✅ **Zero maintenance**: No custom sync logic to debug/maintain
- ✅ **Production proven**: Used by thousands in production
- ✅ **Safety features**: Built-in max-delete, conflict handling, recovery
- ✅ **Filtering**: Advanced exclude patterns and rules
- ✅ **Performance**: Optimized for various storage backends
- ✅ **Community support**: Extensive documentation and community

## Phase 4.2: NFS Mount Support (Direct Access)

### Solution: rclone nfsmount
Keep the existing NFS mount functionality for users who prefer direct file access:

**Core Architecture:**
```bash
# rclone nfsmount provides transparent file access
rclone nfsmount basic-memory-{tenant_id}:{bucket_name} ~/basic-memory-{tenant_id} \
  --vfs-cache-mode writes \
  --dir-cache-time 10s \
  --daemon
```

**Key Benefits:**
- ✅ **Real-time access**: Files appear immediately as they're created/modified
- ✅ **Transparent**: Works with any application that reads/writes files
- ✅ **Low latency**: Direct access without sync delays
- ✅ **Simple**: No periodic sync commands needed
- ✅ **Homebrew compatible**: Works with Homebrew rclone (no FUSE required)

**Limitations:**
- ❌ **File watcher compatibility**: Some apps (Obsidian) don't detect changes over NFS
- ❌ **Network dependency**: Requires active connection to cloud storage
- ❌ **Potential conflicts**: Simultaneous edits from multiple locations can cause issues

### Mount Profiles (Existing)

**Already implemented profiles from SPEC-7 testing:**
```python
MOUNT_PROFILES = {
    "fast": {
        "cache_time": "5s",
        "poll_interval": "3s",
        "description": "Ultra-fast development (5s sync)"
    },
    "balanced": {
        "cache_time": "10s",
        "poll_interval": "5s",
        "description": "Fast development (10-15s sync, recommended)"
    },
    "safe": {
        "cache_time": "15s",
        "poll_interval": "10s",
        "description": "Conflict-aware mount with backup",
        "extra_args": ["--conflict-suffix", ".conflict-{DateTimeExt}"]
    }
}
```

### CLI Commands (Existing)

**Mount commands already implemented:**
```bash
basic-memory cloud mount                     # Mount with balanced profile
basic-memory cloud mount --profile fast     # Ultra-fast caching
basic-memory cloud mount --profile safe     # Conflict detection
basic-memory cloud unmount                  # Clean unmount
basic-memory cloud mount-status             # Show mount status
```

## User Choice: Mount vs Bisync

### When to Use Each Approach

| Use Case | Recommended Solution | Why |
|----------|---------------------|-----|
| **Obsidian users** | `bisync` | File watcher support for live preview |
| **CLI/vim/emacs users** | `mount` | Direct file access, lower latency |
| **Offline work** | `bisync` | Can work offline, sync when connected |
| **Real-time collaboration** | `mount` | Immediate visibility of changes |
| **Multiple machines** | `bisync` | Better conflict handling |
| **Single machine** | `mount` | Simpler, more transparent |
| **Development work** | Either | Both work well, user preference |
| **Large files** | `mount` | Streaming access vs full download |

### Installation Simplicity

**Both approaches now use simple Homebrew installation:**
```bash
# Single installation command for both approaches
brew install rclone

# No macFUSE, no system modifications needed
# Works immediately with both mount and bisync
```

### Implementation Status

**Phase 4.1: bisync** (NEW)
- [ ] Implement bisync command wrapper
- [ ] Add watch mode with configurable intervals
- [ ] Create conflict resolution workflows
- [ ] Add filtering and safety options

**Phase 4.2: mount** (EXISTING - ✅ IMPLEMENTED)
- [x] NFS mount commands with profile support
- [x] Mount management and cleanup
- [x] Process monitoring and health checks
- [x] Credential integration with cloud API

**Both approaches share:**
- [x] Credential management via cloud API
- [x] Secure rclone configuration
- [x] Tenant isolation and bucket scoping
- [x] Simple Homebrew rclone installation


Key Features:

1. Cross-Platform rclone Installation (rclone_installer.py):
- macOS: Homebrew → official script fallback
- Linux: snap → apt → official script fallback
- Windows: winget → chocolatey → scoop fallback
- Automatic version detection and verification

2. Smart rclone Configuration (rclone_config.py):
- Automatic tenant-specific config generation
- Three optimized mount profiles from your SPEC-7 testing:
- fast: 5s sync (ultra-performance)
- balanced: 10-15s sync (recommended default)
- safe: 15s sync + conflict detection
- Backup existing configs before modification

3. Robust Mount Management (mount_commands.py):
- Automatic tenant credential generation
- Mount path management (~/basic-memory-{tenant-id})
- Process lifecycle management (prevent duplicate mounts)
- Orphaned process cleanup
- Mount verification and health checking

4. Clean Architecture (api_client.py):
- Separated API client to avoid circular imports
- Reuses existing authentication infrastructure
- Consistent error handling and logging

User Experience:

One-Command Setup:
basic-memory cloud setup
```bash 
# 1. Installs rclone automatically
# 2. Authenticates with existing login
# 3. Generates secure credentials  
# 4. Configures rclone
# 5. Performs initial mount
```

Profile-Based Mounting:
basic-memory cloud mount --profile fast      # 5s sync
basic-memory cloud mount --profile balanced  # 15s sync (default)
basic-memory cloud mount --profile safe      # conflict detection

Status Monitoring:
basic-memory cloud mount-status
```bash 
# Shows: tenant info, mount path, sync profile, rclone processes
```
### local mount api 

Endpoint 1: Get Tenant Info for user
Purpose: Get tenant details for mounting
- pass in jwt
- service returns mount info

**✅ IMPLEMENTED API Specification:**

**Endpoint 1: GET /tenant/mount/info**
- Purpose: Get tenant mount information without exposing credentials
- Authentication: JWT token (tenant_id extracted from claims)

Request:
```
GET /tenant/mount/info
Authorization: Bearer {jwt_token}
```

Response:
```json
{
    "tenant_id": "434252dd-d83b-4b20-bf70-8a950ff875c4",
    "bucket_name": "basic-memory-434252dd",
    "has_credentials": true,
    "credentials_created_at": "2025-09-22T16:48:50.414694"
}
```

**Endpoint 2: POST /tenant/mount/credentials**
- Purpose: Generate NEW bucket-scoped S3 credentials for rclone mounting
- Authentication: JWT token (tenant_id extracted from claims)
- Multi-session: Creates new credentials without revoking existing ones

Request:
```
POST /tenant/mount/credentials
Authorization: Bearer {jwt_token}
Content-Type: application/json
```
*Note: No request body needed - tenant_id extracted from JWT*

Response:
```json
{
    "tenant_id": "434252dd-d83b-4b20-bf70-8a950ff875c4",
    "bucket_name": "basic-memory-434252dd",
    "access_key": "test_access_key_12345",
    "secret_key": "test_secret_key_abcdef",
    "endpoint_url": "https://fly.storage.tigris.dev",
    "region": "auto"
}
```

**🔒 Security Notes:**
- Secret key returned ONCE only - never stored in database
- Credentials are bucket-scoped (cannot access other tenants' buckets)
- Multiple active credentials supported per tenant (work laptop + personal machine)

Implementation Notes

Security:
- Both endpoints require JWT authentication
- Extract tenant_id from JWT claims (not request body)
- Generate scoped credentials (not admin credentials)
- Credentials should have bucket-specific access only

Integration Points:
- Use your existing StorageClient from SPEC-8 implementation
- Leverage existing JWT middleware for tenant extraction
- Return same credential format as your Tigris bucket provisioning

Error Handling:
- 401 if not authenticated
- 403 if tenant doesn't exist
- 500 if credential generation fails

**🔄 Design Decisions:**

1. **Secure Credential Flow (No Secret Storage)**

Based on CLI flow analysis, we follow security best practices:
- ✅ API generates both access_key + secret_key via Tigris IAM
- ✅ Returns both in API response for immediate use
- ✅ CLI uses credentials immediately to configure rclone
- ✅ Database stores only metadata (access_key + policy_arn for cleanup)
- ✅ rclone handles secure local credential storage
- ❌ **Never store secret_key in database (even encrypted)**

2. **CLI Credential Flow**
```bash
# CLI calls API
POST /tenant/mount/credentials → {access_key, secret_key, ...}

# CLI immediately configures rclone
rclone config create basic-memory-{tenant_id} s3 \
  access_key_id={access_key} \
  secret_access_key={secret_key} \
  endpoint=https://fly.storage.tigris.dev

# Database tracks metadata only
INSERT INTO tenant_mount_credentials (tenant_id, access_key, policy_arn, ...)
```

3. **Multiple Sessions Supported**

- Users can have multiple active credential sets (work laptop, personal machine, etc.)
- Each credential generation creates a new Tigris access key
- List active credentials via API (shows access_key but never secret)

4. **Failure Handling & Cleanup**

- **Happy Path**: Credentials created → Used immediately → rclone configured
- **Orphaned Credentials**: Background job revokes unused credentials
- **API Failure Recovery**: Retry Tigris deletion with stored policy_arn
- **Status Tracking**: Track tigris_deletion_status (pending/completed/failed)

5. **Event Sourcing & Audit**

- MountCredentialCreatedEvent
- MountCredentialRevokedEvent
- MountCredentialOrphanedEvent (for cleanup)
- Full audit trail for security compliance

6. **Tenant/Bucket Validation**

- Verify tenant exists and has valid bucket before credential generation
- Use existing StorageClient to validate bucket access
- Prevent credential generation for inactive/invalid tenants

📋 **Implemented API Endpoints:**

```
✅ IMPLEMENTED:
GET    /tenant/mount/info                    # Get tenant/bucket info (no credentials exposed)
POST   /tenant/mount/credentials             # Generate new credentials (returns secret once)
GET    /tenant/mount/credentials             # List active credentials (no secrets)
DELETE /tenant/mount/credentials/{cred_id}   # Revoke specific credentials
```

**API Implementation Status:**
- ✅ **GET /tenant/mount/info**: Returns tenant_id, bucket_name, has_credentials, credentials_created_at
- ✅ **POST /tenant/mount/credentials**: Creates new bucket-scoped access keys, returns access_key + secret_key once
- ✅ **GET /tenant/mount/credentials**: Lists active credentials without exposing secret keys
- ✅ **DELETE /tenant/mount/credentials/{cred_id}**: Revokes specific credentials with proper Tigris IAM cleanup
- ✅ **Multi-session support**: Multiple active credentials per tenant (work laptop + personal machine)
- ✅ **Security**: Secret keys never stored in database, returned once only for immediate use
- ✅ **Comprehensive test suite**: 28 tests covering all scenarios including error handling and multi-session flows
- ✅ **Dependency injection**: Clean integration with existing FastAPI architecture
- ✅ **Production-ready configuration**: Tigris credentials properly configured for tenant machines

🗄️ **Secure Database Schema:**

```sql
CREATE TABLE tenant_mount_credentials (
  id UUID PRIMARY KEY,
  tenant_id UUID REFERENCES tenant(id),
  access_key VARCHAR(255) NOT NULL,
  -- secret_key REMOVED - never store secrets (security best practice)
  policy_arn VARCHAR(255) NOT NULL,           -- For Tigris IAM cleanup
  tigris_deletion_status VARCHAR(20) DEFAULT 'pending',  -- Track cleanup
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  revoked_at TIMESTAMP NULL,
  last_used_at TIMESTAMP NULL,               -- Track usage for orphan cleanup
  description VARCHAR(255) DEFAULT 'CLI mount credentials'
);
```

**Security Benefits:**
- ✅ Database breach cannot expose secrets
- ✅ Follows "secrets don't persist" security principle
- ✅ Meets compliance requirements (SOC2, etc.)
- ✅ Reduced attack surface
- ✅ CLI gets credentials once and stores securely via rclone
