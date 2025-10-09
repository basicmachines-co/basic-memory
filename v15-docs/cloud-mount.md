# Cloud Mount Commands

**Status**: New Feature
**PR**: #306
**Requires**: Active subscription, rclone installation

## What's New

v0.15.0 introduces cloud mount commands that let you access cloud storage as a local filesystem using rclone mount. This provides direct file access for browsing, editing, and working with cloud files.

## Quick Start

### Mount Cloud Storage

```bash
# Mount cloud storage at ~/basic-memory-cloud
bm cloud mount

# Storage now accessible as local directory
ls ~/basic-memory-cloud
cd ~/basic-memory-cloud/my-project
vim notes.md
```

### Unmount

```bash
# Unmount when done
bm cloud unmount
```

## How It Works

### rclone Mount

Basic Memory uses rclone to mount your cloud bucket as a FUSE filesystem:

```
Cloud Storage (S3)              rclone mount              Local Filesystem
┌─────────────────┐                                      ┌──────────────────┐
│ s3://bucket/    │            <───────────>             │ ~/basic-memory-  │
│  tenant-id/     │          (FUSE filesystem)           │  cloud/          │
│   ├── project-a/│                                      │   ├── project-a/ │
│   ├── project-b/│                                      │   ├── project-b/ │
│   └── notes/    │                                      │   └── notes/     │
└─────────────────┘                                      └──────────────────┘
```

### Mount vs Bisync

| Feature | Mount | Bisync |
|---------|-------|--------|
| **Access** | Direct cloud access | Synced local copy |
| **Latency** | Network dependent | Instant (local files) |
| **Offline** | Requires connection | Works offline |
| **Storage** | No local storage | Uses local disk |
| **Use Case** | Quick access, browsing | Primary workflow, offline work |

**Key difference:** Mount directory (`~/basic-memory-cloud`) and bisync directory (`~/basic-memory-cloud-sync`) must be **different locations**.

## Commands

### bm cloud mount

Mount cloud storage to local filesystem.

```bash
# Basic mount (default: ~/basic-memory-cloud)
bm cloud mount

# Custom mount point
bm cloud mount --mount-point ~/my-cloud-mount

# Background mode
bm cloud mount --daemon

# With verbose logging
bm cloud mount --verbose
```

**What happens:**
1. Authenticates with cloud (uses stored JWT)
2. Generates scoped S3 credentials
3. Configures rclone remote
4. Mounts cloud bucket via FUSE
5. Makes files accessible at mount point

### bm cloud unmount

Unmount cloud storage.

```bash
# Unmount default location
bm cloud unmount

# Unmount custom location
bm cloud unmount --mount-point ~/my-cloud-mount

# Force unmount (if busy)
bm cloud unmount --force
```

**What happens:**
1. Flushes pending writes
2. Unmounts FUSE filesystem
3. Cleans up mount point

### bm cloud status

Check mount status.

```bash
bm cloud status
```

**Shows:**
```
Cloud Mount Status
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property       ┃ Value                      ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Status         │ ✓ Mounted                  │
│ Mount Point    │ ~/basic-memory-cloud       │
│ Remote         │ s3://bucket/tenant-id      │
│ Read/Write     │ Yes                        │
└────────────────┴────────────────────────────┘
```

## Mount Point Structure

### Default Layout

```bash
~/basic-memory-cloud/              # Mount point (configurable)
├── project-a/                     # Cloud projects visible as directories
│   ├── notes/
│   │   └── meeting-notes.md
│   └── ideas/
│       └── brainstorming.md
├── project-b/
│   └── documents/
└── shared-notes/
```

### Important: Separate from Bisync

**Mount point:** `~/basic-memory-cloud` (direct cloud access)
**Bisync directory:** `~/basic-memory-cloud-sync` (synced local copy)

**These MUST be different directories:**
```bash
# ✓ Correct - different directories
MOUNT: ~/basic-memory-cloud
BISYNC: ~/basic-memory-cloud-sync

# ✗ Wrong - same directory (will error)
MOUNT: ~/basic-memory-cloud
BISYNC: ~/basic-memory-cloud
```

## Usage Workflows

### Quick File Access

```bash
# Mount
bm cloud mount

# Browse files
ls ~/basic-memory-cloud
cd ~/basic-memory-cloud/work-project

# View a file
cat ideas/new-feature.md

# Edit directly
vim notes/meeting.md

# Unmount when done
bm cloud unmount
```

### Read-Only Browsing

```bash
# Mount for reading
bm cloud mount

# Search for files
grep -r "authentication" ~/basic-memory-cloud

# View recent files
find ~/basic-memory-cloud -type f -mtime -7

# Unmount
bm cloud unmount
```

### Working with Obsidian

```bash
# Mount cloud storage
bm cloud mount

# Open mount point in Obsidian
# Obsidian vault: ~/basic-memory-cloud/my-project

# Work directly on cloud files
# Changes saved immediately to cloud

# Unmount when done (close Obsidian first)
bm cloud unmount
```

### Temporary Access on Another Device

```bash
# Device B (no local sync setup)
bm cloud login
bm cloud mount

# Access files directly
cd ~/basic-memory-cloud
vim project/notes.md

# Unmount and logout
bm cloud unmount
bm cloud logout
```

## Performance Considerations

### Network Latency

Mount performance depends on network:
- **Local network:** Fast, near-native performance
- **Remote/internet:** Slower, noticeable latency
- **Offline:** Not accessible (returns errors)

### Caching

rclone provides some caching:
```bash
# Mount with enhanced caching
rclone mount basic-memory-remote:bucket ~/basic-memory-cloud \
  --vfs-cache-mode writes \
  --vfs-write-back 5s
```

### When to Use Mount vs Bisync

**Use Mount for:**
- Quick file access
- Temporary access on other devices
- Read-only browsing
- Low disk space situations

**Use Bisync for:**
- Primary workflow
- Offline access
- Better performance
- Regular file operations

## Mount Options

### Foreground vs Daemon

**Foreground (default):**
```bash
bm cloud mount
# Runs in foreground, shows logs
# Ctrl+C to unmount
```

**Daemon (background):**
```bash
bm cloud mount --daemon
# Runs in background
# Use 'bm cloud unmount' to stop
```

### Read-Only Mount

```bash
# Mount as read-only
bm cloud mount --read-only

# Prevents accidental changes
# Good for browsing/searching
```

### Custom Mount Point

```bash
# Use different directory
bm cloud mount --mount-point ~/cloud-kb

# Files at ~/cloud-kb/
ls ~/cloud-kb
```

## Troubleshooting

### Mount Failed

**Problem:** Can't mount cloud storage

```bash
$ bm cloud mount
Error: mount failed: transport endpoint not connected
```

**Solutions:**
1. Check authentication: `bm cloud login`
2. Verify rclone installed: `which rclone`
3. Check mount point exists: `mkdir -p ~/basic-memory-cloud`
4. Ensure not already mounted: `bm cloud unmount`

### Directory Busy

**Problem:** Can't unmount, directory in use

```bash
$ bm cloud unmount
Error: device is busy
```

**Solutions:**
```bash
# Check what's using it
lsof | grep basic-memory-cloud

# Close applications using mount
# cd out of mount directory
cd ~

# Force unmount
bm cloud unmount --force

# Or use system unmount
umount -f ~/basic-memory-cloud
```

### Permission Denied

**Problem:** Can't access mounted files

```bash
$ ls ~/basic-memory-cloud
Permission denied
```

**Solutions:**
1. Check credentials: `bm cloud login`
2. Verify subscription: `bm cloud status`
3. Remount: `bm cloud unmount && bm cloud mount`

### Slow Performance

**Problem:** Files load slowly

**Solutions:**
1. Use bisync for regular work instead
2. Enable write caching (advanced)
3. Check network connection
4. Consider local-first workflow

### Conflicts with Bisync

**Problem:** Trying to use same directory

```bash
$ bm cloud mount --mount-point ~/basic-memory-cloud-sync
Error: Cannot use bisync directory for mount
```

**Solution:** Use different directories
```bash
MOUNT: ~/basic-memory-cloud
BISYNC: ~/basic-memory-cloud-sync
```

## Advanced Usage

### Manual rclone Mount

For advanced users, mount directly:

```bash
# List configured remotes
rclone listremotes

# Manual mount with options
rclone mount basic-memory-{tenant-id}:{bucket} ~/mount-point \
  --vfs-cache-mode full \
  --vfs-cache-max-age 1h \
  --daemon

# Unmount
fusermount -u ~/mount-point  # Linux
umount ~/mount-point         # macOS
```

### Mount with Specific Options

```bash
# Read-only with caching
rclone mount remote:bucket ~/mount \
  --read-only \
  --vfs-cache-mode full

# Write-back for better performance
rclone mount remote:bucket ~/mount \
  --vfs-cache-mode writes \
  --vfs-write-back 30s
```

## Platform-Specific Notes

### macOS

**Requires:** macFUSE
```bash
# Install macFUSE
brew install --cask macfuse

# Mount
bm cloud mount
```

**Unmount:**
```bash
# Basic
bm cloud unmount

# Or system unmount
umount ~/basic-memory-cloud
```

### Linux

**Requires:** FUSE
```bash
# Install FUSE (usually pre-installed)
sudo apt-get install fuse  # Debian/Ubuntu
sudo yum install fuse      # RHEL/CentOS

# Mount
bm cloud mount
```

**Unmount:**
```bash
# Basic
bm cloud unmount

# Or system unmount
fusermount -u ~/basic-memory-cloud
```

### Windows

**Requires:** WinFsp
```bash
# Install WinFsp from https://winfsp.dev/

# Mount
bm cloud mount

# Mounted as drive letter (e.g., Z:)
dir Z:\
```

## Security

### Credentials

- Mount uses scoped S3 credentials (tenant-isolated)
- Credentials expire after session
- No plain-text secrets stored

### File Access

- All traffic encrypted (HTTPS/TLS)
- Same permissions as cloud API
- Respects tenant isolation

### Unmount on Logout

```bash
# Good practice: unmount before logout
bm cloud unmount
bm cloud logout
```

## See Also

- `cloud-bisync.md` - Bidirectional sync (recommended for primary workflow)
- `cloud-authentication.md` - Required authentication setup
- `cloud-mode-usage.md` - Using CLI tools with cloud
- rclone documentation - Advanced mount options
