# PDF.js — Vendored Bundle

These files MUST be present before deploying the HITL review feature.
No CDN fallback is shipped in production code.

## Required files

| File | Description |
|------|-------------|
| `pdf.min.js` | PDF.js main thread library (UMD) |
| `pdf.worker.min.js` | PDF.js worker (must be on same origin) |

## Version pinned: 3.11.174 (last UMD release)

PDF.js **4.x is ESM-only** (`.mjs` files, requires `<script type="module">`).
The HITL review modal loads PDF.js via a classic `<script>` tag and uses
`window.pdfjsLib`, so the UMD build is required. **3.11.174** is the last
stable release that ships UMD `.js` files in `legacy/build/`.

If the modal is ever migrated to ESM imports, bump to the latest 4.x and
delete this version-lock note.

## How to vendor (or re-vendor)

```bash
# In any temp directory (do NOT commit node_modules):
npm install pdfjs-dist@3.11.174

# Copy the two required files into this folder:
cp node_modules/pdfjs-dist/legacy/build/pdf.min.js        ./pdf.min.js
cp node_modules/pdfjs-dist/legacy/build/pdf.worker.min.js ./pdf.worker.min.js
```

## Verified checksums (SHA-256, pdfjs-dist@3.11.174 legacy build)

```
pdf.min.js
  SHA-256: 978fd1b2d134a98e98966186a97777bebf87d8e770dadab1ece3687e21a5aa6c

pdf.worker.min.js
  SHA-256: 38cde5311957b86bc3669f93e7d2566de333a90055ed6635bef60d9bf00e96f2
```

Verify on Linux/macOS / Git Bash:
```bash
sha256sum pdf.min.js pdf.worker.min.js
```

Verify on Windows (PowerShell):
```powershell
Get-FileHash pdf.min.js, pdf.worker.min.js -Algorithm SHA256
```

## Worker URL configuration

`hitl-review.js` sets `pdfjsLib.GlobalWorkerOptions.workerSrc` at runtime
by resolving the `pdf.worker.min.js` path relative to its own `<script src>`
— no hardcoded absolute path. This means the worker will always be found
even if the OpenEMR webroot is not `/`.

## File sizes (informational)

| File | Size |
|------|------|
| pdf.min.js | ~369 KB |
| pdf.worker.min.js | ~1.1 MB |

Total bundle: ~1.5 MB on disk. Acceptable for a chart-side modal that
lazy-loads on the first review-modal open.
