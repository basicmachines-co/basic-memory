---
title: 'SPEC-7: POC to spike Tigris/Turso for local access to cloud data'
type: spec
permalink: specs/spec-7-poc-tigris-turso-local-access-cloud-data
tags:
- poc
- tigris
- turso
- cloud-storage
- architecture
- proof-of-concept
---

# SPEC-7: POC to spike Tigris/Turso for local access to cloud data

> **Status Update**: ✅ **Phase 1 COMPLETE** (September 20, 2025)
> TigrisFS mounting validated successfully in containerized environments. Container startup, filesystem mounting, and Fly.io integration all working correctly. Ready for Phase 2 (Turso database integration).
> See: [`SPEC-7-PHASE-1-RESULTS.md`](./SPEC-7-PHASE-1-RESULTS.md)

## Why

Current basic-memory-cloud architecture uses Fly volumes for tenant file storage, which creates several limitations:

We could enable a revolutionary user experience: **local editing (or at least view access) of cloud-stored files** while maintaining Basic Memory's existing filesystem assumptions.

1. **Storage Scalability**: Fly volumes require pre-provisioning and don't auto-scale with usage
2. **Single Instance**: Volumes can only be mounted to one fly machine instance
3. **Cost Model**: Volume pricing vs object storage pricing may be less favorable at scale
4. **Local Development**: No way for users to mount their cloud tenant files locally for real-time editing
5. **Multi-Region**: Volumes are region-locked, limiting global deployment flexibility
6. **Backup/Disaster Recovery**: Object storage provides better durability and replication options

Basic Memory requires POSIX filesystem semantics but could benefit from object storage durability and accessibility. By combining:
- **Tigris object storage and TigrisFS** for file persistence in bucket stoage via a POSIX filesystem on the tenant instance
- **Turso/libSQL** for SQLite indexing (replacing local .db files). Sqlite on NFS volumes is disouraged. 

## What

This specification defines a proof-of-concept to validate the technical feasibility of the Tigris/Turso architecture for basic-memory-cloud tenants.

**Affected Areas:**
- **Storage Architecture**: Replace Fly volumes with Tigris object storage
- **Database Architecture**: Replace local SQLite with Turso remote database
- **Container Setup**: Add TigrisFS mounting in tenant containers
- **Local Development**: Enable local mounting of cloud tenant data
- **Basic Memory Core**: Validate unchanged operation over mounted filesystems

**Key Components:**
- **Tigris Storage**: Globally caching S3-compatible object storage via Fly.io integration
- **TigrisFS**: Purpose-built FUSE filesystem with intelligent caching
- **Turso Database**: Hosted libSQL for SQLite replacement
- **Single-Tenant Model**: One bucket + one database per tenant (simplified isolation)

## Architectural Overview & Key Insights

### TigrisFS

Unlike standard S3 mounting approaches, **TigrisFS is a purpose-built FUSE filesystem** optimized for object storage with several critical advantages:

1. **Eliminates Fly Volume Limitations**
   - No single-machine attachment constraints
   - No pre-provisioning of storage capacity
   - Enables horizontal scaling and zero-downtime deployments
   - Automatic global CDN caching at Fly.io edge locations

2. **Intelligent Caching Architecture**
   - 1-4GB+ configurable memory cache for read/write operations
   - Write-back caching for improved performance
   - Metadata cache to reduce API calls
   - "Close to Redis speed" for small object retrieval

3. **Cost-Effective Model**
   - Pay only for storage used and transferred
   - No wasted capacity from over-provisioning
   - Automatic global replication included
   - S3 durability with CDN performance

### API-Driven Architecture Eliminates File Watching Concerns

**Critical Insight**: All file access (reads/writes) in basic-memory-cloud go through the API layer:
- **MCP Tools → API**: All Basic Memory operations use FastAPI endpoints
- **Web App → API**: Frontend uses API for all data modifications
- **File watching is NOT required** for cloud operations, unlike local BM which uses the WatchService to monitor file changes.

This means:
- **Cloud Operations**: Manual sync after API writes is sufficient
- **Local Development**: File watching only matters for local editing experience
- **Performance Risk**: Dramatically reduced since we're not dependent on inotify over network filesystems

### Realistic Local Access Expectations

**Baseline Functionality (Guaranteed):**
- Read-only mounting for browsing cloud files
- Easy download/upload of entire projects
- File copying via standard filesystem operations

**Stretch Goal (Test in POC):**
- Live editing with eventual consistency (1-5 second delays acceptable)
- Automatic sync for local changes
- Not required for core functionality - pure upside if it works

### Production Deployment Advantages

1. **Multi-Region Deployment**: Tigris handles global replication automatically
2. **Zero-Downtime Updates**: No volume detach/attach during deployments
3. **Tenant Migrations**: Simply update credentials, no data movement
4. **Disaster Recovery**: Built into S3 durability model (99.999999999% durability)
5. **Auto-Scaling**: Storage scales with usage, no capacity planning needed


## How (High Level)

### POC Approach: Server-First Validation

**Rationale**: Start with server-side TigrisFS mounting because:
- Local access is meaningless if cloud containers can't mount TigrisFS reliably
- Container startup and API performance are critical path blockers
- TigrisFS compatibility with Basic Memory operations must be proven first
- Each phase gates the next - no point testing local access if server-side fails

### Phase 1: Server-Side TigrisFS Validation (Critical Foundation) ✅ COMPLETE
- [x] Set up Tigris bucket with test data via Fly.io integration
- [x] Create container image with TigrisFS support and dependencies
- [x] Test TigrisFS mounting in containerized environment
- [x] Run Basic Memory API operations over mounted TigrisFS
- [x] Validate all filesystem operations work correctly
- [x] Measure container startup time and resource usage

**Production Validation Results**: Container successfully deployed and operated for 42+ minutes serving real MCP requests with repository queries, knowledge graph navigation, and full Basic Memory API functionality over TigrisFS-mounted storage.

### Phase 2: Database Migration to Turso
- [ ] Set up Turso account and test database
- [ ] Modify Basic Memory to accept external DATABASE_URL
- [ ] Test all MCP tools with remote SQLite via Turso
- [ ] Validate performance and functionality parity
- [ ] Test API write → manual sync workflow in container

### Phase 3: Production Container Integration
- [ ] Implement tenant-specific credential management for buckets
- [x] Test container startup with automatic TigrisFS mounting
- [ ] Validate isolation between tenant containers
- [ ] Test API operations under realistic load
- [ ] Measure performance vs current Fly volume setup

### Phase 4: Local Access Validation (Bonus Feature)
- [ ] Test local TigrisFS mounting of tenant data
- [ ] Validate read-only access for browsing/downloading
- [ ] Test file copying and upload workflows
- [ ] Measure latency impact on user experience
- [ ] Test live editing if file watching works (stretch goal)

### Architecture Overview
```
Local Development:
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Local TigrisFS  │───▶│ Tigris Bucket   │◀───│ Tenant Container│
│ Mount           │    │ (Global CDN)    │    │ TigrisFS mount  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                                              │
         ▼                                              ▼
┌─────────────────┐                            ┌─────────────────┐
│ Basic Memory    │                            │ Basic Memory    │
│ (local files)   │                            │ API + mounted   │
└─────────────────┘                            └─────────────────┘
         │                                              │
         ▼                                              ▼
┌─────────────────┐                            ┌─────────────────┐
│ Turso Database  │◀───────────────────────────│ Turso Database  │
│ (shared index)  │                            │ (shared index)  │
└─────────────────┘                            └─────────────────┘

Flow: API writes → Manual sync → Index update
Local: File watching (if available) → Auto sync
```

## How to Evaluate

### Success Criteria
- [x] **Filesystem Compatibility**: Basic Memory operates without modification over TigrisFS-mounted storage
- [x] **Performance Acceptable**: API-driven operations perform within acceptable latency (target: <500ms for typical operations)
- [ ] **Database Functionality**: All Basic Memory features work with Turso remote SQLite
- [x] **Container Reliability**: Tenant containers start successfully with automatic TigrisFS mounting
- [ ] **Local Access Baseline**: Users can mount cloud files locally for read-only browsing and file copying
- [x] **Data Isolation**: Tenant data remains properly isolated using bucket/database separation
- [ ] **Local Access Stretch**: Live editing with eventual sync (1-5 second delays acceptable)

### Testing Procedure

#### Phase 1: Server-Side Foundation Testing
1. **Container TigrisFS Test**:
   ```dockerfile
   # Test container with TigrisFS mounting
   FROM python:3.12
   RUN apt-get update && apt-get install -y tigrisfs

   # Test startup script
   #!/bin/bash
   tigrisfs --memory-limit 2048 $TIGRIS_BUCKET /app/data --daemon
   cd /app/data && basic-memory sync
   basic-memory-api --data-dir /app/data
   ```

2. **API Operations Validation**:
   ```bash
   # Test all MCP operations over TigrisFS
   curl -X POST /api/write_note -d '{"title":"test","content":"content"}'
   curl -X GET /api/read_note/test
   curl -X GET /api/search_notes?q=content
   # Measure: response times, error rates, data consistency
   ```

#### Phase 2: Database Integration Testing
3. **Turso Integration Test**:
   ```bash
   # Configure Turso connection in container
   export DATABASE_URL="libsql://test-db.turso.io?authToken=..."

   # Test all MCP tools with remote database
   basic-memory tools # Test each tool functionality
   # Test API write → manual sync workflow
   ```

#### Phase 3: Production Readiness Testing
4. **Performance Benchmarking**:
   - Container startup time with TigrisFS mounting
   - API operation response times (target: <500ms for typical operations)
   - Search query performance with Turso (target: comparable to local SQLite)
   - TigrisFS cache hit rates and memory usage
   - Concurrent tenant isolation

#### Phase 4: Local Access Testing (If Phase 1-3 Succeed)
5. **Local Access Validation**:
   ```bash
   # Test read-only access
   tigrisfs tenant-bucket ~/local-tenant
   ls -la ~/local-tenant  # Browse files
   cp ~/local-tenant/notes/* ~/backup/  # Copy files

   # Test file watching (stretch goal)
   echo "test" > ~/local-tenant/test.md
   # Check if changes sync to cloud
   ```

### Go/No-Go Criteria by Phase
- **Phase 1**: Container must start successfully and serve API requests over TigrisFS
- **Phase 2**: All MCP tools must work with Turso with <2x latency increase
- **Phase 3**: Performance must be within 50% of current Fly volume setup
- **Phase 4**: Local mounting must work reliably for read-only access

### Risk Assessment
**Moderate Risk Items (Mitigated by API-First Architecture)**:
- [ ] TigrisFS performance for local access may have higher latency than local filesystem
- [ ] File watching (`inotify`) over FUSE may be unreliable for local development
- [ ] Network interruptions could cause filesystem errors during local editing
- [ ] Write-back caching could cause data loss if container crashes during flush

**Low Risk Items (API-First Eliminates)**:
- [ ] ~~Real-time file watching~~ - Not required for cloud operations
- [ ] ~~Concurrent write consistency~~ - Single-tenant model with API coordination
- [ ] ~~S3 rate limits~~ - TigrisFS intelligent caching handles this

**Mitigation Strategies**:
- **Performance**: Comprehensive benchmarking with realistic workloads
- **Reliability**: Graceful degradation to read-only local access if live editing fails
- **Data Safety**: Regular sync intervals and write-through mode for critical operations
- **Fallback**: Keep Fly volumes as backup deployment option

### Metrics to Track
- **API Latency**: Response times for MCP tools and web operations
- **Cache Effectiveness**: TigrisFS cache hit rates and memory usage
- **Local Access Performance**: File browsing and copying speeds
- **Reliability**: Success rate of mount operations and data consistency
- **Cost**: Storage usage, API calls, and network transfer costs vs current volumes

## Notes

### Key Architectural Decisions
- **Single tenant per bucket/database**: Simplifies isolation and credential management
- **Maintain POSIX compatibility**: Preserve Basic Memory's existing filesystem assumptions
- **TigrisFS over rclone**: Purpose-built for object storage with intelligent caching
- **Turso for SQLite**: Leverages specialized remote SQLite expertise
- **API-first approach**: Eliminates file watching dependency for cloud operations

### Alternative Approaches Considered
- **S3-native storage backend**: Would require Basic Memory architecture changes
- **Hybrid approach**: Local files + cloud sync (adds complexity)
- **Standard rclone mounting**: Less optimized than TigrisFS for object storage workloads
- **Keep Fly volumes**: Maintains current limitations but proven reliability

### Integration Points
- [ ] Fly.io Tigris integration for bucket provisioning
- [ ] Turso account setup and database provisioning
- [ ] Container image modifications for TigrisFS support
- [ ] Credential management for tenant isolation
- [ ] API modification for manual sync triggers
- [ ] Local client setup documentation for TigrisFS mounting

## Observations

- [architecture] Tigris/Turso split cleanly separates file storage from indexing concerns #storage-separation
- [breakthrough] API-first architecture eliminates file watching dependency for cloud operations #api-first-advantage
- [user-experience] Local mounting of cloud files could be revolutionary for knowledge management #local-cloud-hybrid
- [compatibility] Maintaining POSIX filesystem assumptions preserves Basic Memory's local/cloud compatibility #architecture-preservation
- [simplification] Single tenant per bucket eliminates complex multi-tenancy in storage layer #tenant-isolation
- [performance] TigrisFS intelligent caching could provide near-local performance for common operations #tigrisfs-advantage
- [deployment] Zero-downtime updates become trivial without volume constraints #deployment-simplification
- [benefit] Object storage pricing model could be more favorable than volume pricing #cost-optimization
- [innovation] Read-only local access alone would address major SaaS limitation #competitive-advantage
- [risk-mitigation] API-driven sync reduces performance requirements vs real-time file watching #risk-reduction

## Relations

- implements [[SPEC-6 Explicit Project Parameter Architecture]]
- requires [[Fly.io Tigris Integration]]
- enables [[Local Cloud File Access]]
- alternative_to [[Fly Volume Storage]]

## Links
- https://fly.io/hello/tigris
- https://fly.io/docs/tigris/
- https://www.tigrisdata.com/docs/sdks/fly/data-migration-with-flyctl/
- https://www.tigrisdata.com/docs/training/tigrisfs/
- https://www.tigrisdata.com/blog/tigris-filesystem/
- https://www.tigrisdata.com/docs/quickstarts/rclone/
