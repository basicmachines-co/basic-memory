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

## Why

Current basic-memory-cloud architecture uses Fly volumes for tenant file storage, which creates several limitations:

1. **Storage Scalability**: Fly volumes require pre-provisioning and don't auto-scale with usage
2. **Cost Model**: Volume pricing vs object storage pricing may be less favorable at scale
3. **Local Development**: No way for users to mount their cloud tenant files locally for real-time editing
4. **Multi-Region**: Volumes are region-locked, limiting global deployment flexibility
5. **Backup/Disaster Recovery**: Object storage provides better durability and replication options

The core insight is that Basic Memory requires POSIX filesystem semantics but could benefit from object storage durability and accessibility. By combining:
- **Tigris object storage** for file persistence (via rclone mount)
- **Turso/libSQL** for SQLite indexing (replacing local .db files)

We could enable a revolutionary user experience: **local editing of cloud-stored files** while maintaining Basic Memory's existing filesystem assumptions.

## What

This specification defines a proof-of-concept to validate the technical feasibility of the Tigris/Turso architecture for basic-memory-cloud tenants.

**Affected Areas:**
- **Storage Architecture**: Replace Fly volumes with Tigris object storage
- **Database Architecture**: Replace local SQLite with Turso remote database
- **Container Setup**: Add rclone mounting in tenant containers
- **Local Development**: Enable local mounting of cloud tenant data
- **Basic Memory Core**: Validate unchanged operation over mounted filesystems

**Key Components:**
- **Tigris Storage**: S3-compatible object storage via Fly.io integration
- **rclone NFS Mount**: Native NFS mounting without FUSE dependencies
- **Turso Database**: Hosted libSQL for SQLite replacement
- **Single-Tenant Model**: One bucket + one database per tenant (simplified isolation)

## How (High Level)

### Phase 1: Local POC Validation
- [ ] Set up Tigris bucket with test data
- [ ] Configure rclone NFS mount locally
- [ ] Test Basic Memory operations over mounted filesystem
- [ ] Measure performance characteristics and identify issues
- [ ] Validate file watching, sync operations, and concurrent access patterns

### Phase 2: Database Migration
- [ ] Set up Turso account and test database
- [ ] Modify Basic Memory to accept external DATABASE_URL
- [ ] Test all operations with remote SQLite via Turso
- [ ] Validate performance and functionality parity

### Phase 3: Container Integration
- [ ] Create container image with rclone + NFS support
- [ ] Implement tenant-specific credential management
- [ ] Test container startup with automatic mounting
- [ ] Validate isolation between tenant containers

### Phase 4: Local Access Validation
- [ ] Test local rclone mounting of tenant data
- [ ] Validate real-time file editing experience
- [ ] Test conflict resolution and sync behavior
- [ ] Measure latency impact on user experience

### Architecture Overview
```
Local Development:
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Local rclone    │───▶│ Tigris Bucket   │◀───│ Tenant Container│
│ NFS Mount       │    │ (S3 storage)    │    │ rclone mount    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                                              │
         ▼                                              ▼
┌─────────────────┐                            ┌─────────────────┐
│ Basic Memory    │                            │ Basic Memory    │
│ (local files)   │                            │ (mounted files) │
└─────────────────┘                            └─────────────────┘
         │                                              │
         ▼                                              ▼
┌─────────────────┐                            ┌─────────────────┐
│ Turso Database  │◀───────────────────────────│ Turso Database  │
│ (shared index)  │                            │ (shared index)  │
└─────────────────┘                            └─────────────────┘
```

## How to Evaluate

### Success Criteria
- [ ] **Filesystem Compatibility**: Basic Memory operates without modification over rclone-mounted Tigris storage
- [ ] **Performance Acceptable**: File operations complete within 2x local filesystem latency
- [ ] **Database Functionality**: All Basic Memory features work with Turso remote SQLite
- [ ] **Container Reliability**: Tenant containers start successfully with automatic mounting
- [ ] **Local Access**: Users can mount and edit cloud files locally with real-time sync
- [ ] **Data Isolation**: Tenant data remains properly isolated using bucket/database separation

### Testing Procedure
1. **Local Filesystem Test**:
   ```bash
   # Mount Tigris bucket locally
   rclone nfsmount tigris:test-bucket ~/tigris-test --vfs-cache-mode writes

   # Run Basic Memory operations
   cd ~/tigris-test && basic-memory sync --watch
   # Test: create notes, search, file watching, bulk operations
   ```

2. **Database Migration Test**:
   ```bash
   # Configure Turso connection
   export DATABASE_URL="libsql://test-db.turso.io?authToken=..."

   # Test all MCP tools with remote database
   basic-memory tools # Test each tool functionality
   ```

3. **Container Integration Test**:
   ```dockerfile
   # Test container with rclone mounting
   FROM python:3.12
   RUN apt-get update && apt-get install -y rclone nfs-common
   # ... test startup and mounting process
   ```

4. **Performance Benchmarking**:
   - File creation/read/write operations (target: <2x local latency)
   - Search query performance (target: comparable to local SQLite)
   - File watching responsiveness (target: events within 1-2 seconds)
   - Concurrent operation handling

### Risk Assessment
**High Risk Items**:
- [ ] NFS-over-S3 performance may be insufficient for real-time operations
- [ ] File watching (`inotify`) over NFS may be unreliable
- [ ] Network interruptions could cause filesystem errors
- [ ] Concurrent access patterns might hit S3 rate limits

**Mitigation Strategies**:
- Comprehensive performance testing before committing to architecture
- Fallback plan to S3-native storage backend if filesystem approach fails
- Extensive error handling and retry logic for network issues

### Metrics to Track
- **Latency**: File operation response times (read/write/watch)
- **Reliability**: Success rate of file operations over time
- **Throughput**: Concurrent file operations and search queries
- **User Experience**: Perceived performance for local mounting use case

## Notes

### Key Architectural Decisions
- **Single tenant per bucket/database**: Simplifies isolation and credential management
- **Maintain POSIX compatibility**: Preserve Basic Memory's existing filesystem assumptions
- **NFS over FUSE**: Better compatibility and performance characteristics
- **Turso for SQLite**: Leverages specialized remote SQLite expertise

### Alternative Approaches Considered
- **S3-native storage backend**: Would require Basic Memory architecture changes
- **Hybrid approach**: Local files + cloud sync (adds complexity)
- **FUSE mounting**: More platform dependencies and kernel requirements

### Integration Points
- [ ] Fly.io Tigris integration for bucket provisioning
- [ ] Turso account setup and database provisioning
- [ ] Container image modifications for rclone support
- [ ] Credential management for tenant isolation

## Observations

- [architecture] Tigris/Turso split cleanly separates file storage from indexing concerns #storage-separation
- [user-experience] Local mounting of cloud files could be revolutionary for knowledge management #local-cloud-hybrid
- [compatibility] Maintaining POSIX filesystem assumptions preserves Basic Memory's local/cloud compatibility #architecture-preservation
- [simplification] Single tenant per bucket eliminates complex multi-tenancy in storage layer #tenant-isolation
- [risk] NFS-over-S3 performance characteristics are unproven for real-time operations #performance-risk
- [benefit] Object storage pricing model could be more favorable than volume pricing #cost-optimization
- [innovation] Real-time local editing of cloud-stored files addresses major SaaS limitation #competitive-advantage

## Relations

- implements [[SPEC-6 Explicit Project Parameter Architecture]]
- requires [[Fly.io Tigris Integration]]
- enables [[Local Cloud File Access]]
- alternative_to [[Fly Volume Storage]]