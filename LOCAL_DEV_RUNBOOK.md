# LOCAL_DEV_RUNBOOK.md — Local Docker Desktop & OpenEMR Stack

> **Related docs:** [`RUNBOOK.md`](./RUNBOOK.md) (production disaster recovery — different scope) · [`CLAUDE.md`](./CLAUDE.md) (testing & dev commands) · [`docker/development-easy/README.md`](./docker/development-easy/README.md) (stack overview)

**Audience:** the developer whose Docker Desktop just white-screened, or who edited a PHP/Twig file and needs the changes reflected on `localhost:8300`.

**Scope:** local Windows dev only. For production droplet recovery, see [`RUNBOOK.md`](./RUNBOOK.md).

**Terminals:** every command in this runbook works in either **PowerShell** or **Git Bash**. Where syntax differs (path separators, Windows process commands), both are shown side-by-side. The `docker` and `docker compose` commands themselves are identical in both.

---

## When to use which procedure

| Symptom | Go to |
|---|---|
| Don't have a terminal open / not sure where to start | §0 — open a terminal and get into the stack |
| Docker Desktop UI is white/blank/frozen | §1 — full Docker Desktop restart |
| Docker Desktop runs but `docker ps` hangs or errors | §1 (the WSL backend is hung) |
| Containers running but PHP code changes not visible | §3 — bounce the `openemr` container + clear caches |
| Module/Twig/JS changes not visible after rebuild | §3 + §4 — clear caches and hard-refresh |
| Need to run a command *inside* the openemr container | §0c — exec into the container |
| `docker compose up` fails on port already in use | §5 — port-conflict diagnosis |
| Want to wipe everything and start clean (loses local DB data) | §6 — nuclear reset |

---

## §0. Open a terminal and get into the stack

You don't need a terminal *inside* Docker Desktop — its built-in terminal is convenient but not required. Any terminal on your host (PowerShell, Git Bash, Windows Terminal, VS Code's integrated terminal) talks to Docker the same way.

### 0a. Open a terminal at the repo root

**Option A — Git Bash (recommended if you're used to Linux commands):**
1. Right-click anywhere inside the `C:\Dev\GauntletAI\AgentForge` folder in File Explorer.
2. Choose **"Open Git Bash here"** (or **"Show more options"** → **"Open Git Bash here"** on Windows 11).

**Option B — PowerShell:**
1. Right-click inside the folder → **"Open in Terminal"**.
2. Or: Start menu → "PowerShell" → then `cd C:\Dev\GauntletAI\AgentForge`.

**Option C — VS Code:**
1. Open VS Code at the repo root → **Ctrl + `** opens the integrated terminal.
2. Use the dropdown in the terminal panel to pick "Git Bash" or "PowerShell".

### 0b. Navigate to the docker compose directory

```bash
# Git Bash (forward slashes)
cd docker/development-easy
```

```powershell
# PowerShell (either slash works, but backslash is conventional)
cd docker\development-easy
```

Verify you're in the right place — both shells:

```bash
ls
# Should show: docker-compose.yml, docker-compose.override.yml, README.md
```

### 0c. Get a shell *inside* the openemr container

When the runbook says "run X inside the container," or you need to poke around the container's filesystem, open a shell inside it:

```bash
# Works in both Git Bash and PowerShell
docker compose exec openemr bash
```

You're now at a `#` (root) prompt **inside** the container — the filesystem, PHP version, and installed tools here are the container's, not your host's. The OpenEMR code lives at `/var/www/localhost/htdocs/openemr`. Type `exit` (or Ctrl + D) to return to your host shell.

> **Git Bash gotcha:** if `docker compose exec openemr bash` errors with `the input device is not a TTY`, prefix it with `winpty`:
> ```bash
> winpty docker compose exec openemr bash
> ```
> This only affects interactive shells. Non-interactive `exec` commands (e.g. `docker compose exec openemr /root/devtools php-log`) work without `winpty`.

### 0d. Quick sanity check the stack is alive

```bash
docker compose ps
```

All services should show **`running`** / **`healthy`**. If something is missing or restarting, jump to §2.

---

## §1. Restart Docker Desktop (white-screen / hung backend)

### 1a. Force-close Docker Desktop

1. **Ctrl + Shift + Esc** → opens Task Manager.
2. In the **Processes** tab, **End task** on each of these (right-click → End task):
   - `Docker Desktop`
   - `Docker Desktop Backend` (or `com.docker.backend`)
   - `Docker Desktop Service` (the Process; leave the actual Windows *Service* in the Services tab alone for now)
   - Any `vpnkit` / `wsl` / `dockerd` processes that linger
3. Switch to the **Details** tab and end any leftover `Docker*.exe`.
4. Wait ~10 seconds so Windows releases file handles.

### 1b. If Task Manager won't kill it

Open PowerShell **as Administrator** and run:

```powershell
Get-Process *docker* | Stop-Process -Force
wsl --shutdown
```

Or in **Git Bash** (no admin required for `taskkill`, but `wsl --shutdown` may need an elevated shell):

```bash
taskkill //F //IM "Docker Desktop.exe"
taskkill //F //IM "com.docker.backend.exe"
wsl --shutdown
```

> Git Bash uses double slashes (`//F`, `//IM`) for Windows-style flags — single slashes get rewritten as paths. Annoying but harmless.

`wsl --shutdown` is the one that usually unsticks a white-screened Docker Desktop — its WSL2 backend is hung and Docker Desktop is waiting on it.

### 1c. Restart Docker Desktop

1. Start menu → **Docker Desktop**.
2. Wait for the whale icon in the system tray to go from *animated* → *steady*.
3. Hover the tray icon — it should say **"Docker Desktop is running"**. If it sits at "starting" for >2 min, repeat §1a + the `wsl --shutdown`.
4. Sanity check from any terminal (PowerShell or Git Bash):

   ```bash
   docker version
   docker ps
   ```

   `docker ps` should return without error (an empty list is fine — containers haven't been started yet).

---

## §2. Start (or restart) the OpenEMR stack

From a terminal at the repo root (see §0 if you don't have one open):

```bash
# Git Bash
cd docker/development-easy
docker compose down
docker compose up --detach --wait
```

```powershell
# PowerShell
cd docker\development-easy
docker compose down
docker compose up --detach --wait
```

- `down` cleanly stops + removes the containers. **Volumes are preserved** — your DB and sites data are safe.
- `up --detach --wait` starts everything and waits for health checks before returning. First boot after a Docker Desktop restart takes 1–3 minutes.

To verify:

```bash
docker compose ps
```

All services (`mysql`, `openemr`, `qdrant`, `phpmyadmin`, `couchdb`, `openldap`, `mailpit`, `selenium`) should show **`running`** / **`healthy`**.

In a browser:

- OpenEMR: http://localhost:8300/ → login `admin` / `pass`
- phpMyAdmin: http://localhost:8310/

---

## §3. Just bounce the OpenEMR container (PHP code changes)

You don't need a full stack restart for most code edits. The `openemr` container mounts the repo, so PHP changes are picked up immediately — but **OPcache, Smarty cache, and Twig cache** can hide them.

From the `docker/development-easy` directory (works in both Git Bash and PowerShell):

```bash
docker compose restart openemr
docker compose exec openemr /root/devtools clear-smarty-cache
```

Then **hard-refresh** the browser: **Ctrl + F5** (bypasses browser cache).

For module changes (e.g. `interface/modules/custom_modules/oe-module-clinical-copilot/`):

```bash
docker compose exec openemr /root/devtools clear-smarty-cache
docker compose restart openemr
```

If a module's `composer.json` or autoloader changed, also run:

```bash
docker compose exec openemr composer dump-autoload -d /var/www/localhost/htdocs/openemr
```

---

## §4. Tail logs when something looks wrong

Works in both Git Bash and PowerShell:

```bash
# Live OpenEMR container log
docker compose logs -f openemr

# PHP error log (most useful for white-screen / 500 errors)
docker compose exec openemr /root/devtools php-log

# All services at once
docker compose logs -f
```

Press **Ctrl + C** to stop tailing.

For the agent service (when running locally outside Docker):

```bash
# Git Bash
tail -f agent/logs/*.log
```

```powershell
# PowerShell
Get-Content agent\logs\*.log -Wait -Tail 50
```

---

## §5. Port conflicts on `docker compose up`

If you see `bind: address already in use` (replace `8300` with the conflicting port):

```bash
# Git Bash — netstat ships with Windows; grep is from Git
netstat -ano | grep ":8300"
# The last column is the PID. Look it up:
tasklist //FI "PID eq <PID>"
```

```powershell
# PowerShell
netstat -ano | Select-String ":8300"
Get-Process -Id <PID>
```

Common culprits:

- A previous `docker` run that didn't clean up → `docker compose down` first.
- IIS or another local web server on 80/443 → stop it via `services.msc` (search "World Wide Web Publishing Service").
- Another Docker project using the same port → stop it: `docker ps`, then `docker stop <container>`.

---

## §6. Nuclear reset (loses local DB data)

**Only when the stack is genuinely broken and a full reinstall is faster than debugging.** This **deletes** your local OpenEMR database, sites volume, and Qdrant collections.

From the `docker/development-easy` directory (Git Bash or PowerShell):

```bash
docker compose down --volumes        # removes containers AND named volumes
docker compose up --detach --wait    # rebuilds from scratch
```

Then re-seed:

```bash
# Re-import synthetic patients (uses Synthea fixtures from the repo)
docker compose exec openemr /root/devtools import-random-patients

# If you have agent corpus to re-ingest, run the corpus ingestion script
# (see agent/README.md for the current command)
```

---

## §7. Common gotchas

- **Module changes not loading** → almost always a Smarty cache issue. Run `clear-smarty-cache` and hard-refresh.
- **"Class not found" after editing module composer.json** → run `composer dump-autoload` inside the container (§3).
- **Database schema changes not applied** → if you added a Doctrine migration, run it via devtools or `bin/console`. Editing `sql/*.sql` files alone does nothing on an existing volume.
- **Qdrant collection missing after restart** → expected if you used `down --volumes`. Re-run the agent corpus ingestion.
- **`docker compose` vs `docker-compose`** → use `docker compose` (space, modern Docker CLI plugin). The old `docker-compose` (hyphen) is deprecated.
- **WSL2 eating disk space** → over time, `%LOCALAPPDATA%\Docker\wsl\` grows. If your C: drive is full, run `wsl --shutdown` then in Docker Desktop → Settings → Resources → "Clean / Purge data".

---

## §8. When this runbook is not enough

- **Production droplet issues** → [`RUNBOOK.md`](./RUNBOOK.md) (DO snapshot restore, agent_log restore, full host loss).
- **Test failures inside Docker** → [`CLAUDE.md`](./CLAUDE.md) §Testing has the devtools commands (`unit-test`, `api-test`, `e2e-test`, `clean-sweep-tests`).
- **First-time setup** → [`CONTRIBUTING.md`](./CONTRIBUTING.md) and [`docker/development-easy/README.md`](./docker/development-easy/README.md).
