# Durable Job Queue & Google Drive Archive Storage

Comfy Colab Kit persists generation jobs, reference inputs, and rendered outputs (images and videos) in a durable SQLite job queue backed by either **local disk** (default) or **Google Drive**.

Because Google Colab VMs are ephemeral, `/_gate/jobs`, `/prompt`, `/upload/image`, and MCP generation tools are intercepted and tracked by the Gate so jobs can be submitted while the GPU is sleeping, automatically rehydrated and resumed if a VM disconnects, and viewed or downloaded later even when the GPU is off.

---

## 1. Architecture Overview

- **Metadata & Queue (`gate.db`)**: Local SQLite running in WAL mode (`STATE_DIR/gate.db`) stores `jobs`, `assets`, and `job_assets`.
- **Hot Snapshots (`conn.backup()`)**: On job transitions, after archival, and during shutdown, the Gate uses SQLite's online `conn.backup()` API to create an atomic consistent snapshot (`gate.db` + `gate.db.meta.json`) and uploads it to `db/` in Google Drive (debounced by `DB_BACKUP_DEBOUNCE_S`). On a fresh host with a missing `gate.db`, startup restores the snapshot from Drive and verifies `PRAGMA quick_check == "ok"` before marking any previously `running` jobs as `interrupted`.
- **Resumable Output Streaming**: Outputs are streamed directly from ComfyUI `/view` into Google Drive API v3 Resumable Uploads in **4 MiB (`4 * 1024 * 1024`) chunks** (a multiple of Drive's 256 KiB chunk requirement) without buffering large videos in RAM.
- **Local Thumbnail Cache & Input Dedup**:
  - `THUMBS_DIR` stores lightweight `<= 384px` WebP/JPEG thumbnails (via Pillow for images or `ffmpeg` frame-0 extraction for videos) so the Archive UI loads instantaneously while sleeping.
  - Uploaded inputs are SHA-256 deduplicated in `inputs/<sha[:2]>/<sha><ext>` and cached locally so interrupted jobs automatically re-upload required inputs onto a replacement Colab VM before re-dispatching.

---

## 2. Why Personal Google Accounts Must Use OAuth (Not Service Accounts)

Consumer Google accounts (`@gmail.com`) **must** authenticate with OAuth 2.0 user credentials (`client_id`, `client_secret`, `refresh_token`) rather than a GCP Service Account JSON key:

1. **Service Accounts have zero personal Drive storage quota**: Uploading a file as a Service Account into a personal My Drive folder fails with HTTP `403 storageQuotaExceeded` (`Service Accounts do not have storage quota`). Service Accounts can only write to Google Workspace **Shared Drives**, which are unavailable on free/consumer Google accounts.
2. **Publish the OAuth Consent Screen to Production**: In Google Cloud Console (**APIs & Services -> OAuth consent screen**), set the Publishing Status to **In Production** (do **not** leave it in **Testing**). Refresh tokens issued in `Testing` mode expire automatically after **7 days**. Because this app is self-hosted and uses the `drive.file` scope (no restricted/sensitive broad Drive scopes), publishing for personal use does not require Google app verification—just dismiss the unverified-app warning during your one-time `ckit gdrive-auth` login.
3. **Least-Privilege Scope (`drive.file`)**: `ckit gdrive-auth` requests `https://www.googleapis.com/auth/drive.file` by default, which grants access **only** to folders and files created by Comfy Colab Kit itself (it cannot read or modify unrelated files in your Google Drive).

---

## 3. Setup Walkthrough (`ckit gdrive-auth`)

### Step 1: Create OAuth Client in Google Cloud Console
1. Open Google Cloud Console, select or create a project, and enable the **Google Drive API**.
2. Configure **OAuth consent screen** (User Type: **External**), add your account, select scope `https://www.googleapis.com/auth/drive.file`, and click **Publish App** so refresh tokens do not expire after 7 days.
3. Under **Credentials -> Create Credentials -> OAuth client ID**, choose **Desktop app** (supports `http://127.0.0.1:<port>/callback` loopback redirects). Note your Client ID and Client Secret.

### Step 2: Generate `secrets/gdrive_oauth.json`
Run `ckit gdrive-auth` on your machine (it starts a one-shot loopback HTTP callback on `127.0.0.1:0`, opens your browser, exchanges the code, writes `0600` JSON, and prints only the output path):

```bash
export GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export GOOGLE_CLIENT_SECRET="your-client-secret"
python3 -m ckit gdrive-auth --out secrets/gdrive_oauth.json
```

On a headless VPS (use SSH port forwarding `ssh -L <port>:127.0.0.1:<port> vps` or copy the `?code=...` parameter from the redirect URL):

```bash
python3 -m ckit gdrive-auth --no-browser --out secrets/gdrive_oauth.json
# Or paste the authorization code directly:
python3 -m ckit gdrive-auth --no-browser --code "4/0A..." --out secrets/gdrive_oauth.json
```

### Step 3: Enable in `.env` and `deploy/docker-compose.yml`
Set `GDRIVE_OAUTH_FILE` in `.env` and uncomment the read-only bind mount in `deploy/docker-compose.yml`:

```yaml
volumes:
  - ../secrets/gdrive_oauth.json:/run/secrets/gdrive_oauth.json:ro
```

Ensure host permissions allow the container user (`uid 10001`):

```bash
chmod 600 secrets/gdrive_oauth.json
sudo chown 10001:10001 secrets/gdrive_oauth.json
```

---

## 4. Environment Variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `GDRIVE_OAUTH_FILE` | `""` | Path to `gdrive_oauth.json`. When empty or unreadable, the Gate uses `LocalArchiveStore`. |
| `GDRIVE_ROOT_FOLDER_ID` | `""` | Optional Google Drive folder ID. When empty, auto-creates/uses a `comfy-colab-kit` folder in My Drive. |
| `ARCHIVE_DIR` | `<STATE_DIR parent>/archive` | Local archive directory (used when Drive is off, or as local input cache for Drive mode). |
| `THUMBS_DIR` | `<STATE_DIR parent>/thumbs` | Local directory for cached `<= 384px` WebP/JPEG thumbnails. |
| `ARCHIVE_MAX_GB` | `20.0` | Maximum local archive cache budget in GB for pruning unpinned blobs. |
| `QUEUE_INTERCEPT_ENABLED` | `true` | Intercept ComfyUI `/prompt`, `/upload/image`, `/upload/mask`, and `/history` when ready. |
| `QUEUE_AUTO_RETRY` | `true` | Automatically retry `interrupted` jobs (e.g., VM crash/preemption) when GPU returns `ready`. |
| `QUEUE_MAX_RETRIES` | `2` | Maximum automatic retries per interrupted job (CUDA OOM / node errors never auto-retry). |
| `DB_BACKUP_DEBOUNCE_S` | `15.0` | Debounce window in seconds before uploading a hot `gate.db` snapshot to Drive. |

---

## 5. Job Queue & Archive HTTP API Summary

All routes require standard Gate authentication (Cloudflare Access JWT or Agent Bearer Token).

| Method & Path | Description |
|---|---|
| `GET /_gate/storage/status` | Storage backend (`gdrive` \| `local`), root folder ID, last DB backup timestamp, and errors. |
| `POST /_gate/storage/backup` | Trigger an immediate hot `conn.backup()` snapshot and upload to storage. |
| `GET /_gate/archive/profiles` | List profiles present in the archive with job counts. |
| `GET /_gate/jobs` | List jobs (`?profile=`, `?status=`, `?q=`, `?pinned=1`, `?limit=`, `?offset=`). |
| `POST /_gate/jobs` | Enqueue a ComfyUI prompt graph (`{"prompt": {...}, "profile_id": "..."}`), works while sleeping. |
| `POST /_gate/jobs/upload` | Upload and SHA-256 deduplicate an input image/file while sleeping or ready. |
| `GET /_gate/jobs/{job_id}` | Fetch full job details, prompt graph, inputs, outputs, and error metadata. |
| `POST /_gate/jobs/{job_id}/retry` | Re-queue a failed, canceled, interrupted, or completed job. |
| `POST /_gate/jobs/{job_id}/cancel` | Cancel a queued/interrupted job or interrupt a running job. |
| `POST /_gate/jobs/{job_id}/pin` | Pin or unpin a job (`{"pinned": true \| false}`). |
| `DELETE /_gate/jobs/{job_id}` | Delete job record (`?delete_files=1` also deletes unshared output blobs). |
| `GET /_gate/archive/assets/{asset_id}` | Stream archived output (supports HTTP `Range` `206` seeking and `?download=1`). |
| `GET /_gate/archive/thumbs/{asset_id}` | Serve cached local thumbnail image for a job output asset. |

From the CLI, use `ckit jobs [--profile ID] [--status S] [--pinned] [--limit N] [--json]` to inspect jobs directly.
