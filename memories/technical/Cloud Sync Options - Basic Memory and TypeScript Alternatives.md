# Cloud Sync Options - Basic Memory and TypeScript Alternatives

A comprehensive guide to how Basic Memory syncs to the cloud and what tools to use for similar functionality in TypeScript projects.

## How Basic Memory Syncs to Cloud

## Observations

- [architecture] Basic Memory has two sync layers: local file sync and optional cloud sync
- [tool] Cloud sync uses **rclone bisync** for bidirectional synchronization
- [product] Basic Memory Cloud is a separate paid service ($14.25/month early bird pricing)
- [feature] Cloud sync enables multi-device access across desktop, web, tablet, phone
- [open-source] Open-source core remains unchanged - cloud is optional sync layer
- [status] Cloud CLI sync via rclone bisync is work in progress (as of Dec 2025)

### Local Sync (Core Feature)

- [mechanism] File watcher monitors markdown files for changes
- [database] SQLite index updated when files change
- [command] `basic-memory sync` - one-time sync
- [command] `basic-memory sync --watch` - continuous file watching
- [checksum] SHA256 checksums detect file changes without reading content
- [speed] 43% faster sync and indexing in v0.15.1
- [performance] 10-100x faster directory operations

### Cloud Sync (Paid Cloud Service)

- [tool] **rclone bisync** - bidirectional sync between local and cloud
- [architecture] Cloud service provides remote storage + web UI + multi-LLM access
- [workflow] Local files ↔ rclone bisync ↔ Cloud storage ↔ Web UI
- [benefit] Access memories from Claude, ChatGPT, Gemini across devices
- [status] Work in progress for CLI control
- [issue] Known issues with bisync not syncing to cloud (database connection problems)

## What is rclone bisync?

- [definition] rclone is a command-line tool for syncing files with cloud storage
- [feature] bisync provides bidirectional synchronization (like Dropbox)
- [comparison] Similar to rsync but supports 70+ cloud storage providers
- [providers] Works with Google Drive, Dropbox, OneDrive, S3, WebDAV, SFTP, etc.
- [algorithm] Tracks changes on both sides and syncs bidirectionally
- [conflict] Handles conflict resolution when same file modified on both sides
- [documentation] https://rclone.org/bisync/

### rclone bisync Example

```bash
# Initial setup - configure remote
rclone config

# First sync requires --resync
rclone bisync ~/basic-memory remote:basic-memory --resync

# Subsequent syncs (bidirectional)
rclone bisync ~/basic-memory remote:basic-memory

# Dry run to see what would change
rclone bisync ~/basic-memory remote:basic-memory --dry-run
```

- [flag] --resync - Force sync on first run or after conflicts
- [flag] --dry-run - Preview changes without executing
- [behavior] Detects changes on both local and remote
- [behavior] Syncs new/modified/deleted files in both directions

## Alternative: rsync (Unidirectional)

- [definition] rsync is a file synchronization tool for Unix-like systems
- [limitation] Only syncs one direction (not bidirectional like bisync)
- [use-case] Good for backups, not ideal for multi-device editing
- [speed] Very fast, efficient delta transfers
- [ssh] Works over SSH for remote syncing

### rsync Example

```bash
# Sync local to remote
rsync -avz ~/basic-memory/ user@server:/path/to/backup/

# Flags:
# -a archive mode (recursive, preserve permissions)
# -v verbose
# -z compress during transfer

# Sync from remote to local
rsync -avz user@server:/path/to/backup/ ~/basic-memory/

# Delete files on destination not in source
rsync -avz --delete ~/basic-memory/ user@server:/backup/
```

- [limitation] Must choose direction each time
- [limitation] No automatic conflict detection
- [risk] --delete flag can cause data loss if used incorrectly
- [benefit] Simpler than rclone for one-way backups

## Cloud Sync Options for TypeScript Projects

### 1. rclone (Best Overall - Like Basic Memory Uses)

- [recommendation] Same tool Basic Memory Cloud uses
- [language] Go binary, works with any language/project
- [providers] 70+ cloud storage providers supported
- [mode] bisync for bidirectional sync
- [installation] `brew install rclone` (macOS) or download from rclone.org

#### TypeScript Integration Example

```typescript
import { exec } from 'child_process';
import { promisify } from 'util';

const execAsync = promisify(exec);

async function syncToCloud(localPath: string, remoteName: string, remotePath: string) {
  try {
    // Dry run first
    const dryRun = await execAsync(
      `rclone bisync "${localPath}" "${remoteName}:${remotePath}" --dry-run`
    );
    console.log('Dry run:', dryRun.stdout);

    // Actual sync
    const result = await execAsync(
      `rclone bisync "${localPath}" "${remoteName}:${remotePath}"`
    );
    console.log('Sync complete:', result.stdout);
  } catch (error) {
    console.error('Sync failed:', error);
  }
}

// Usage
await syncToCloud('./memories', 'gdrive', 'basic-memory-backup');
```

- [integration] Shell out to rclone CLI
- [monitoring] Parse stdout/stderr for sync status
- [scheduling] Use cron or node-schedule for automatic syncing

### 2. chokidar + Custom Sync (File Watching)

- [package] chokidar - Fast file watcher for Node.js
- [use-case] Detect file changes and trigger sync
- [similar] Similar to Basic Memory's file watching
- [installation] `npm install chokidar`

#### TypeScript Example with chokidar

```typescript
import chokidar from 'chokidar';
import crypto from 'crypto';
import fs from 'fs/promises';

interface FileChecksum {
  path: string;
  checksum: string;
  timestamp: number;
}

class FileWatcher {
  private checksums = new Map<string, string>();

  watch(directory: string) {
    const watcher = chokidar.watch(directory, {
      ignored: /(^|[\/\\])\../, // ignore dotfiles
      persistent: true,
      ignoreInitial: false
    });

    watcher
      .on('add', path => this.handleFileChange(path, 'added'))
      .on('change', path => this.handleFileChange(path, 'changed'))
      .on('unlink', path => this.handleFileDelete(path));

    console.log(`Watching ${directory} for changes...`);
  }

  private async handleFileChange(path: string, event: string) {
    const checksum = await this.calculateChecksum(path);
    const previousChecksum = this.checksums.get(path);

    if (checksum !== previousChecksum) {
      console.log(`File ${event}: ${path}`);
      this.checksums.set(path, checksum);

      // Trigger sync to cloud
      await this.syncFile(path);
    }
  }

  private async calculateChecksum(filePath: string): Promise<string> {
    const content = await fs.readFile(filePath);
    return crypto.createHash('sha256').update(content).digest('hex');
  }

  private async syncFile(path: string) {
    // Implement your sync logic here
    // Could use rclone, S3 SDK, etc.
  }

  private handleFileDelete(path: string) {
    console.log(`File deleted: ${path}`);
    this.checksums.delete(path);
  }
}

// Usage
const watcher = new FileWatcher();
watcher.watch('./memories');
```

- [pattern] Calculate checksums to detect real changes (like Basic Memory)
- [pattern] Debounce rapid changes to avoid excessive syncs
- [integration] Combine with rclone or cloud provider SDK

### 3. AWS S3 + AWS SDK (TypeScript Native)

- [package] @aws-sdk/client-s3
- [benefit] Full TypeScript support, no shell commands
- [use-case] If already using AWS infrastructure
- [cost] Pay per storage and data transfer

#### TypeScript S3 Example

```typescript
import { S3Client, PutObjectCommand, GetObjectCommand } from '@aws-sdk/client-s3';
import fs from 'fs/promises';
import path from 'path';

class S3Sync {
  private client: S3Client;
  private bucket: string;

  constructor(bucket: string) {
    this.client = new S3Client({ region: 'us-east-1' });
    this.bucket = bucket;
  }

  async uploadFile(localPath: string, s3Key: string) {
    const fileContent = await fs.readFile(localPath);

    await this.client.send(new PutObjectCommand({
      Bucket: this.bucket,
      Key: s3Key,
      Body: fileContent,
      ContentType: 'text/markdown'
    }));

    console.log(`Uploaded ${localPath} to s3://${this.bucket}/${s3Key}`);
  }

  async downloadFile(s3Key: string, localPath: string) {
    const response = await this.client.send(new GetObjectCommand({
      Bucket: this.bucket,
      Key: s3Key
    }));

    const content = await response.Body?.transformToByteArray();
    if (content) {
      await fs.writeFile(localPath, content);
      console.log(`Downloaded s3://${this.bucket}/${s3Key} to ${localPath}`);
    }
  }

  async syncDirectory(localDir: string, s3Prefix: string) {
    const files = await this.getFilesRecursively(localDir);

    for (const file of files) {
      const relativePath = path.relative(localDir, file);
      const s3Key = path.join(s3Prefix, relativePath).replace(/\\/g, '/');
      await this.uploadFile(file, s3Key);
    }
  }

  private async getFilesRecursively(dir: string): Promise<string[]> {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    const files = await Promise.all(entries.map((entry) => {
      const fullPath = path.join(dir, entry.name);
      return entry.isDirectory() ? this.getFilesRecursively(fullPath) : [fullPath];
    }));
    return files.flat();
  }
}

// Usage
const sync = new S3Sync('my-memories-bucket');
await sync.syncDirectory('./memories', 'backups/memories');
```

- [benefit] Native TypeScript, no external binaries
- [benefit] Works cross-platform (Windows, macOS, Linux)
- [limitation] Unidirectional sync (need custom logic for bidirectional)

### 4. Google Drive API (TypeScript Native)

- [package] googleapis
- [benefit] Free 15GB storage with Google account
- [use-case] If users have Google accounts
- [complexity] More complex auth setup than S3

#### TypeScript Google Drive Example

```typescript
import { google } from 'googleapis';
import fs from 'fs';

class GoogleDriveSync {
  private drive;

  constructor(credentials: any) {
    const auth = new google.auth.GoogleAuth({
      credentials,
      scopes: ['https://www.googleapis.com/auth/drive.file']
    });

    this.drive = google.drive({ version: 'v3', auth });
  }

  async uploadFile(localPath: string, fileName: string) {
    const fileMetadata = { name: fileName };
    const media = {
      mimeType: 'text/markdown',
      body: fs.createReadStream(localPath)
    };

    const response = await this.drive.files.create({
      requestBody: fileMetadata,
      media: media,
      fields: 'id'
    });

    console.log(`Uploaded to Google Drive: ${response.data.id}`);
    return response.data.id;
  }

  async downloadFile(fileId: string, localPath: string) {
    const response = await this.drive.files.get(
      { fileId, alt: 'media' },
      { responseType: 'stream' }
    );

    const dest = fs.createWriteStream(localPath);
    response.data.pipe(dest);

    return new Promise((resolve, reject) => {
      dest.on('finish', resolve);
      dest.on('error', reject);
    });
  }
}
```

- [auth] Requires OAuth2 setup and credentials
- [limitation] Need to implement bidirectional sync logic manually
- [benefit] Free storage for personal use

### 5. Syncthing (P2P Sync - No Cloud Required)

- [type] Peer-to-peer synchronization (no cloud provider needed)
- [architecture] Direct sync between devices
- [privacy] Data never goes through third-party servers
- [installation] Standalone application, not a library
- [use-case] Sync between personal devices without cloud storage

#### Integration with TypeScript

```typescript
// Syncthing runs as a separate service
// TypeScript app just writes to synced folder

import fs from 'fs/promises';
import path from 'path';

// Write to syncthing-watched directory
async function saveNote(content: string, filename: string) {
  const syncedDir = process.env.SYNCTHING_DIR || './synced-memories';
  const filePath = path.join(syncedDir, filename);

  await fs.writeFile(filePath, content, 'utf-8');
  console.log(`Saved to synced folder: ${filePath}`);
  // Syncthing automatically syncs to other devices
}
```

- [benefit] No cloud subscription costs
- [benefit] True peer-to-peer, privacy-focused
- [limitation] Devices must be online simultaneously (or use relay)
- [setup] Requires Syncthing installed on all devices

## Recommended Approach for TypeScript Project

### Option A: Use rclone (Like Basic Memory)

- [recommendation] Best match for Basic Memory's architecture
- [setup] Install rclone, configure remote provider
- [code] Shell out from TypeScript using child_process
- [scheduling] Use node-schedule or cron for automatic syncs
- [monitoring] Use chokidar to watch files and trigger rclone

#### Complete Example

```typescript
import chokidar from 'chokidar';
import { exec } from 'child_process';
import { promisify } from 'util';
import schedule from 'node-schedule';

const execAsync = promisify(exec);

class CloudSync {
  private localPath: string;
  private remoteName: string;
  private remotePath: string;
  private syncing = false;

  constructor(localPath: string, remoteName: string, remotePath: string) {
    this.localPath = localPath;
    this.remoteName = remoteName;
    this.remotePath = remotePath;
  }

  // Watch files and trigger sync
  watchAndSync() {
    const watcher = chokidar.watch(this.localPath, {
      ignored: /(^|[\/\\])\../,
      persistent: true,
      awaitWriteFinish: { stabilityThreshold: 2000 }
    });

    // Debounced sync on file changes
    let syncTimeout: NodeJS.Timeout;
    watcher.on('all', (event, path) => {
      clearTimeout(syncTimeout);
      syncTimeout = setTimeout(() => this.sync(), 5000); // 5 sec debounce
    });
  }

  // Scheduled sync every hour
  scheduleSync() {
    schedule.scheduleJob('0 * * * *', () => {
      console.log('Running scheduled sync...');
      this.sync();
    });
  }

  async sync() {
    if (this.syncing) {
      console.log('Sync already in progress, skipping...');
      return;
    }

    this.syncing = true;
    try {
      const cmd = `rclone bisync "${this.localPath}" "${this.remoteName}:${this.remotePath}"`;
      const { stdout, stderr } = await execAsync(cmd);

      if (stdout) console.log('Sync output:', stdout);
      if (stderr) console.error('Sync warnings:', stderr);

      console.log('✓ Sync complete');
    } catch (error) {
      console.error('✗ Sync failed:', error);
    } finally {
      this.syncing = false;
    }
  }

  async initialSync() {
    console.log('Running initial sync with --resync...');
    const cmd = `rclone bisync "${this.localPath}" "${this.remoteName}:${this.remotePath}" --resync`;
    await execAsync(cmd);
    console.log('✓ Initial sync complete');
  }
}

// Usage
const sync = new CloudSync('./memories', 'gdrive', 'basic-memory-backup');
await sync.initialSync();
sync.watchAndSync();
sync.scheduleSync();
```

- [pattern] Combines file watching + scheduled sync
- [pattern] Debouncing prevents excessive syncs
- [pattern] Flag prevents concurrent syncs

### Option B: Pure TypeScript with Cloud SDK

- [recommendation] If you want type safety and no external dependencies
- [providers] Use @aws-sdk/client-s3, googleapis, or similar
- [complexity] Must implement bidirectional sync logic yourself
- [benefit] Full control over conflict resolution

## Comparison Table

| Tool | Bidirectional | TypeScript Native | Providers | Complexity | Like Basic Memory |
|------|---------------|-------------------|-----------|------------|-------------------|
| **rclone bisync** | ✅ | ❌ (shell out) | 70+ | Low | ✅ **Exact match** |
| **rsync** | ❌ | ❌ (shell out) | SSH/local | Low | ❌ One-way only |
| **AWS S3 SDK** | ❌ (manual) | ✅ | AWS only | Medium | ❌ Unidirectional |
| **Google Drive API** | ❌ (manual) | ✅ | Google only | Medium | ❌ Unidirectional |
| **Syncthing** | ✅ | ❌ (separate app) | P2P | Low | ⚠️ Different architecture |
| **chokidar + rclone** | ✅ | Hybrid | 70+ | Medium | ✅ **Best match** |

## Key Takeaways

- [basic-memory] Uses rclone bisync for cloud sync (paid cloud service)
- [local] Core open-source uses file watching + SQLite indexing
- [typescript] Best option: chokidar (file watch) + rclone (sync)
- [alternative] Pure TypeScript: cloud SDK + custom bidirectional logic
- [p2p] Syncthing for privacy-focused device-to-device sync
- [simple] rsync for simple one-way backups

## Relations

- related-to [[Basic Memory Technical Architecture - Deep Dive for JavaScript Rebuild]]
- related-to [[Basic Memory Discord Insights - Super User Guide]]
- uses [[rclone]]
- uses [[chokidar]]
