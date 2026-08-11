# Fast-FR Exam Gantry

Fast-FR is an offline-first Windows application for supervised, one-person-at-a-time facial-recognition checks at exam-hall entrances. It accepts up to four RTSP cameras, compares faces with camera-assigned exam rosters, records attendance, and gives the operator a large match/no-match display.

The recognition pipeline uses the Apache-2.0 YuNet detector and SFace recognizer through ONNX Runtime. NVIDIA CUDA is selected when its runtime libraries are available; otherwise the application starts in CPU fallback mode and clearly warns that the four-camera latency target is not guaranteed.

## Included features

- Four H.264 RTSP inputs with TCP transport, reconnect/backoff, rotation, normalized polygon ROI, latest-frame buffers, and bounded round-robin inference.
- Five-landmark alignment, normalized 128-dimensional embeddings, exact cosine search, multiple face samples per identity, blacklist precedence, and an administrator-controlled threshold.
- Two consistent votes within three eligible frames, one-person quality gates, and 15-second same-camera passage suppression.
- Whitelist roster match, known-but-unregistered, unknown, and global blacklist decisions.
- Named `admin` and `operator` accounts, Argon2id passwords, CSRF protection, authenticated WebSockets, and an append-only audit log.
- AES-GCM encrypted face crops, embeddings, RTSP URLs, Telegram credentials, and event snapshots. The master key is protected with Windows DPAPI.
- JPG/PNG/PDF bulk review, filename-derived names, face selection, quality rejection, likely-duplicate warnings, and optional immediate roster assignment.
- Dated exam sessions, per-camera assignment, automatic/manual attendance, unknown resolution, XLSX reports with thumbnails, and explicit archive/permanent-delete flows.
- Optional asynchronous Telegram alert delivery that cannot delay local recognition.

## Run from source

Requirements: Windows 10/11, Python 3.11–3.14, Node.js 22+, and optionally an NVIDIA GPU with the CUDA/cuDNN runtime required by the installed ONNX Runtime build.

```powershell
python -m pip install -e ".[dev,build]"
npm.cmd ci
python scripts/download_models.py
npm.cmd run build
python -m fastfr.launcher
```

Or run `scripts\run_local.ps1`. Fast-FR binds only to `127.0.0.1:8765` and opens the local UI in the default browser. On first run, create the administrator account. Runtime data is stored under `%LOCALAPPDATA%\FastFR` unless `FASTFR_DATA_DIR` is set for development or testing.

For split UI development, run the API with `python -m fastfr.launcher` after one production UI build, then run `npm.cmd run dev`; Vite serves `127.0.0.1:5173` and proxies `/api` and WebSockets to the local API.

## Validate

```powershell
npm.cmd run lint
npm.cmd run build
python -m ruff check backend
python -m pytest
python scripts/benchmark_index.py --identities 20000 --queries 500
```

The test suite covers encryption, filename and upload validation, similarity search, blacklist policy precedence, passage suppression, ROI handling, WAL/append-only storage, authentication and roles, CSRF, roster rules, manual matching, encrypted evidence retrieval, WebSocket delivery, bulk deletion, and XLSX content.

## Build the Windows installer

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then run:

```powershell
scripts\build_windows.ps1
```

The script uses the npm lockfile, verifies both model hashes, builds the browser UI, produces a PyInstaller one-folder application in `build\FastFR`, and—when Inno Setup is installed—creates `build\installer\FastFR-Setup.exe`. The uninstaller deliberately leaves `%LOCALAPPDATA%\FastFR` in place so attendance and biometric data are not silently destroyed.

## Operating notes

- Use fixed, front-facing gantry cameras with stable lighting and one person in the ROI. Poor-quality faces produce “Position face”; multiple eligible faces produce “One person at a time.”
- The default SFace cosine threshold is `0.450`. Do not tune it without a consented pilot dataset from the intended camera position.
- A CPU fallback is suitable for setup and smaller deployments. The four-camera acceptance target requires a validated NVIDIA/CUDA installation.
- Telegram is off by default. Internet loss affects Telegram delivery status only; recognition, attendance, and reporting remain local.
- Exported XLSX workbooks can contain biometric thumbnails and are outside the encrypted application store.
- Passive/depth liveness, crowded-scene recognition, and door control are intentionally out of scope. A trained operator makes the final admission decision.

## Performance acceptance

Before production use, run a four-hour soak test on the target laptop and cameras. The intended target is P95 decision latency below one second with four 1080p H.264 streams, 2,500 candidates per active roster, and 20,000 global identities. Recognition quality must be calibrated with consented, representative passport and gantry images; the deployment target is at least 95% true acceptance at no more than 0.1% false acceptance.
