# Runbook — IntelliJ + WSL + Databricks CLI Setup

Everything needed to drive this project from an **IntelliJ IDEA** terminal running **WSL
Ubuntu** on Windows 11 — installing the Databricks CLI, pointing IntelliJ at the WSL shell,
and authenticating so `databricks bundle …` works from that terminal.

The only platform prerequisite is a **Premium-tier Databricks workspace** (Premium is
required for the role-based access control this project uses; Standard tier does not allow
it). Everything else below is local tooling.

---

## 1. WSL Ubuntu 24.04 (one-time, in PowerShell as Administrator)

```powershell
wsl --install -d Ubuntu-24.04     # creates a UNIX user/password on first launch
wsl --set-default Ubuntu-24.04
wsl --list --verbose              # confirm Ubuntu-24.04 shows VERSION 2 and the * default marker
```

Then open the Ubuntu shell and update:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl jq unzip make
```

Keep the repository on the **Linux** filesystem (`~/projects/…`), never `/mnt/c/…` — the
latter is dramatically slower for pip, git, and shell scripts.

---

## 2. Install the Databricks CLI (inside WSL)

```bash
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh
databricks --version        # expect v0.2xx+
```

> If `databricks` isn't found afterwards, ensure `/usr/local/bin` is on your `PATH`
> (`echo $PATH`); reopen the shell if you just installed it.

---

## 3. Point IntelliJ's terminal at WSL Ubuntu

So the built-in IntelliJ terminal *is* your Ubuntu shell:

1. **File → Settings → Tools → Terminal**.
2. Set **Shell path** to:
   ```
   wsl.exe -d Ubuntu-24.04
   ```
3. Apply, then open **View → Tool Windows → Terminal**. The prompt should be your Ubuntu
   user. `lsb_release -a` confirms Ubuntu 24.04.

Open the project from the Linux path: **File → Open →** `\\wsl$\Ubuntu-24.04\home\<you>\projects\vic-suburbs-dwh`
(or run `idea .` from the WSL terminal if the IntelliJ shell launcher is installed).

> Optional: install the **Databricks** plugin (Settings → Plugins → Marketplace) for bundle
> awareness and notebook sync. It's a convenience, not a requirement — everything here works
> from the terminal alone.

---

## 4. Authenticate the CLI (OAuth user-to-machine)

From the IntelliJ WSL terminal:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com
```

This opens a browser, you log in once, and a profile is written to `~/.databrickscfg`. Name
the profile when prompted (e.g. `DEFAULT` or `vic-dev`). Verify:

```bash
databricks current-user me        # prints your user — auth works
databricks catalogs list          # confirms Unity Catalog access
```

The project's `make` targets are wrappers, so the same commands work:

```bash
make auth HOST=https://<your-workspace>.cloud.databricks.com
```

### Alternative: personal access token (headless / CI)

```bash
# generate a PAT in the workspace UI: Settings → Developer → Access tokens
export DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...
databricks current-user me
```

CI uses these two environment variables (stored as secrets), not the interactive login.

### Multiple environments

Add one profile per environment in `~/.databrickscfg` and select it with `-p`:

```ini
[vic-dev]
host  = https://<dev-workspace>.cloud.databricks.com

[vic-prod]
host  = https://<prod-workspace>.cloud.databricks.com
```

```bash
databricks bundle deploy -t prod -p vic-prod
```

---

## 5. Verify the toolchain end-to-end

```bash
cd ~/projects/vic-suburbs-dwh
make install                      # Python deps + editable install
databricks bundle validate -t dev # CLI auth + bundle both good
```

If both succeed, you're ready for [`deployment-guide.md`](deployment-guide.md) and the
quickstart in the main `README.md`.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `databricks: command not found` in IntelliJ terminal | IntelliJ isn't using the WSL shell — recheck step 3, reopen the terminal. |
| `cannot configure default credentials` | Re-run `databricks auth login`, or export `DATABRICKS_HOST`/`DATABRICKS_TOKEN`. |
| `PERMISSION_DENIED` on catalog ops | Your user isn't a workspace/account admin or lacks UC privileges; the workspace must be Premium with Unity Catalog enabled. |
| Slow installs / git | The repo is on `/mnt/c/…`; move it under `~/` in the Linux filesystem. |
