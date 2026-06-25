# Runbook — IntelliJ IDEA + WSL Ubuntu Setup (Windows 11)

> **Status:** Reference doc · linked from README
>
> The complete, click-by-click guide to setting up a local development environment for this
> project on **Windows 11 + WSL Ubuntu 24.04 + IntelliJ IDEA**: cloning the project, installing
> the CLI tools, creating the Python virtualenv, installing a JDK for the PySpark tests,
> authenticating the Databricks CLI, and wiring IntelliJ's interpreter, terminal, and run
> configurations.

## Contents

- [Themes worth knowing up front](#themes-worth-knowing-up-front)
- [Prerequisites and versions](#prerequisites-and-versions)
- [Step 0 — Install WSL Ubuntu 24.04 (PowerShell as Administrator)](#step-0--install-wsl-ubuntu-2404-powershell-as-administrator)
- [Step 1 — Install IntelliJ IDEA on Windows](#step-1--install-intellij-idea-on-windows)
- [Step 2 — Place the project inside the WSL filesystem](#step-2--place-the-project-inside-the-wsl-filesystem)
- [Step 3 — Install CLI tools, the venv, and a JDK (inside WSL)](#step-3--install-cli-tools-the-venv-and-a-jdk-inside-wsl)
  - [3.1 — Skip-if-present check](#31--skip-if-present-check)
  - [3.2 — Base packages](#32--base-packages)
  - [3.3 — `yq` (optional but recommended)](#33--yq-optional-but-recommended)
  - [3.4 — Databricks CLI](#34--databricks-cli)
  - [3.5 — Project virtualenv (run from the project root)](#35--project-virtualenv-run-from-the-project-root)
  - [3.6 — JDK for the PySpark integration test](#36--jdk-for-the-pyspark-integration-test)
- [Step 4 — Authenticate the Databricks CLI (OAuth — primary method)](#step-4--authenticate-the-databricks-cli-oauth--primary-method)
  - [Every fresh session — the start-of-work check](#every-fresh-session--the-start-of-work-check)
  - [Later — personal access token (PAT), for headless & CI only](#later--personal-access-token-pat-for-headless--ci-only)
  - [Multiple environments](#multiple-environments)
- [Step 5 — Open the project in IntelliJ via WSL](#step-5--open-the-project-in-intellij-via-wsl)
- [Step 6 — Configure the Python interpreter (as a **WSL** interpreter)](#step-6--configure-the-python-interpreter-as-a-wsl-interpreter)
- [Step 7 — Configure the integrated WSL terminal](#step-7--configure-the-integrated-wsl-terminal)
- [Step 8 — Plugins (optional)](#step-8--plugins-optional)
- [Step 9 — Run configurations](#step-9--run-configurations)
- [Step 10 — Final validation](#step-10--final-validation)
- [Quick reference](#quick-reference)
- [Troubleshooting](#troubleshooting)
- [Cross-references](#cross-references)

---

## Themes worth knowing up front

- **Everything runs inside WSL Ubuntu, not Windows.** Python, the Databricks CLI, the venv,
  git, and the tests all live in Ubuntu. IntelliJ runs on Windows but points at the WSL
  interpreter and uses the WSL shell as its terminal.
- **Keep the project on the Linux filesystem** (`~/projects/...`), never `/mnt/c/...`.
  Cross-filesystem access is dramatically slower for the many small files Python, pip, and
  git touch.
- **Ubuntu 24.04 ships Python 3.12 with PEP 668** ("externally-managed-environment"). The fix
  is a project **virtualenv** — inside an activated venv, `pip install` just works.
- **The only platform prerequisite is a Premium-tier Databricks workspace.** Premium is
  required for the role-based access control this project uses; Standard tier does not allow
  it. Everything else below is local tooling.

---

## Prerequisites and versions

| Component | Version assumed |
|---|---|
| Windows | 11 |
| WSL distro | Ubuntu 24.04 LTS |
| Python (Ubuntu system) | 3.12 |
| Java (for PySpark tests) | OpenJDK 17 |
| IntelliJ IDEA | 2024.1+ (Community or Ultimate) |
| Databricks CLI | v0.2xx+ |

---

## Step 0 — Install WSL Ubuntu 24.04 (PowerShell as Administrator)

```powershell
wsl --install -d Ubuntu-24.04     # sets a UNIX username + password on first launch
wsl --set-default Ubuntu-24.04
wsl --list --verbose              # confirm Ubuntu-24.04, VERSION 2, and the * default marker
```

If a different Ubuntu is already default, install 24.04 explicitly (above) and set it default.
Open the **Ubuntu** app from the Start menu to get a shell, then:

```bash
lsb_release -a                    # confirm "Ubuntu 24.04"
sudo apt update && sudo apt upgrade -y
```

---

## Step 1 — Install IntelliJ IDEA on Windows

Download IntelliJ IDEA from JetBrains and install it **on Windows** (not inside WSL). Launch it
once to complete first-run setup. The Python plugin is bundled with Ultimate; on Community,
install it in Step 6.

---

## Step 2 — Place the project inside the WSL filesystem

```bash
# in WSL Ubuntu
sudo apt install -y git            # ships with 24.04; install if missing
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/<your-username>/vic-suburbs-dwh.git
cd vic-suburbs-dwh
ls                                 # databricks.yml, config/, src/, docs/, deployment/, ...
```

From Windows, this location is reachable as a UNC path you'll use during IntelliJ setup:

```
\\wsl.localhost\Ubuntu-24.04\home\<your-wsl-username>\projects\vic-suburbs-dwh
```

---

## Step 3 — Install CLI tools, the venv, and a JDK (inside WSL)

### 3.1 — Skip-if-present check

```bash
which databricks python3 git jq yq java
databricks --version 2>/dev/null
```

`apt` is idempotent, so re-installing anything already present is harmless.

### 3.2 — Base packages

```bash
sudo apt update
sudo apt install -y jq unzip zip make python3-pip python3-venv python3-full
```

### 3.3 — `yq` (optional but recommended)

**Do you need `yq`?** Not for the application — the pipeline and generator read YAML with
**PyYAML** (a Python dependency in `requirements.txt`), and the shell scripts (`bootstrap.sh`,
`destroy.sh`) don't parse YAML. `yq` is a convenience for **inspecting or editing the config
YAML from the shell** (e.g. `yq '.entities[].name' config/entities.yaml`). Install it if you
like working with config from the terminal; the snap build is outdated, so install the binary:

```bash
sudo wget -qO /usr/local/bin/yq \
  https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
sudo chmod +x /usr/local/bin/yq
yq --version        # expect 4.x
```

### 3.4 — Databricks CLI

```bash
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh
databricks --version     # expect v0.2xx+
```

> If `databricks` isn't found afterwards, ensure `/usr/local/bin` is on `PATH` and reopen the
> shell.

### 3.5 — Project virtualenv (run from the project root)

Ubuntu 24.04's PEP 668 protection blocks system-wide `pip install`. Use a venv:

```bash
cd ~/projects/vic-suburbs-dwh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .                  # editable install of the vic_suburbs package
```

The interpreter is now at `~/projects/vic-suburbs-dwh/.venv/bin/python` — **note this path**;
you'll point IntelliJ at it in Step 6. Inside an activated venv, `pip install` needs no
`--break-system-packages`. (The `make install` target uses `--break-system-packages` so it
also works in the CI container, which has no venv — for local work, prefer the venv above.)

### 3.6 — JDK for the PySpark integration test

The `tests/integration/` smoke test spins up a local `SparkSession`, which needs a JVM.
Without one you'll see `JAVA_HOME is not set` / `Java gateway process exited`. (The unit tests
don't need Java and still pass.)

```bash
sudo apt install -y openjdk-17-jdk
java -version                              # expect "openjdk version 17.x"
readlink -f "$(which java)"                # e.g. /usr/lib/jvm/java-17-openjdk-amd64/bin/java

# persist JAVA_HOME + SPARK_LOCAL_IP (quietens a WSL loopback warning)
echo 'export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64' >> ~/.bashrc
echo 'export PATH=$JAVA_HOME/bin:$PATH'                     >> ~/.bashrc
echo 'export SPARK_LOCAL_IP=127.0.0.1'                      >> ~/.bashrc
source ~/.bashrc
echo "$JAVA_HOME"
```

> **System-wide vs. venv — which is which?** The JDK above is a **JVM, not a Python package**:
> it installs system-wide and is found via `JAVA_HOME`, so it does **not** matter whether the
> venv is active when you install it. **PySpark, by contrast, is a Python package and belongs
> in the venv** — but you don't install it separately here: `pyspark` is already declared in
> `requirements-dev.txt`, so Step 3.5 put it in `.venv` (on Python 3.12). Just activate and run:

```bash
cd ~/projects/vic-suburbs-dwh && source .venv/bin/activate
pytest tests/integration -v                # the smoke test should pass
```

> On Python 3.13+ the `requirements-dev.txt` marker skips PySpark (no stable wheels there yet).
> If that's your interpreter, install it into the **activated** venv first: `pip install pyspark`.

> **IntelliJ note:** run configurations inherit the environment IntelliJ launched with, so
> after editing `~/.bashrc` you must **fully restart IntelliJ** for it to see `JAVA_HOME`
> (or add `JAVA_HOME`/`SPARK_LOCAL_IP` to a run config's *Environment variables* field).
> Terminal runs pick them up as soon as you `source ~/.bashrc`.

---

## Step 4 — Authenticate the Databricks CLI (OAuth — primary method)

For local development, authenticate with **OAuth** — it's Databricks' recommended method, it
doesn't make you choose API scopes, and it persists across sessions (see below). Run it once:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com
```

> This logs you in at the **workspace** level — enough for deploying, running, and all day-to-day
> work. The **account-level** RBAC groups are a separate concern: `deployment/bootstrap.sh` runs a
> one-time account login for them on its first run (it prompts for your **Account ID** and caches a
> `vic-account` profile). You don't run that by hand — just be an **account admin** with your
> Account ID ready when you bootstrap. See the [deployment guide](deployment-guide.md).

A browser opens; sign in. The CLI then:

- writes a **profile** to `~/.databrickscfg` with the host and `auth_type = databricks-cli`
  (note: **no token in this file** — OAuth keeps the token elsewhere), and
- caches an **access token + refresh token** at `~/.databricks/token-cache.json`.

Name the profile when prompted (e.g. `vic-dev`) and make it the default so commands don't need
`-p`:

```ini
; ~/.databrickscfg
[__settings__]
default_profile = vic-dev
```

Verify auth **from your home directory**, so the CLI isn't also loading the project's
`databricks.yml`:

```bash
cd ~                              # verify auth in isolation, away from the project bundle
databricks current-user me        # prints your user — auth works
databricks catalogs list          # confirms Unity Catalog access
```

(Inside the project directory the CLI is "bundle-aware" — it loads `databricks.yml` and its
`include`d resource files to resolve the target workspace, which mixes bundle validation into
what should be a simple auth check. Running from `~` keeps the two separate.)

### Every fresh session — the start-of-work check

You do **not** normally re-authenticate every time. The refresh token cached at
`~/.databricks/token-cache.json` survives closing your WSL session and closing/reopening the
IntelliJ project, and the CLI **auto-refreshes** the short-lived (≈1 h) access token from it
silently. So when you open a fresh WSL terminal or reopen the project, just run a one-line
check before working:

```bash
cd ~ && databricks current-user me
```

- **Prints your user** → you're authenticated; carry on (`cd` back into the project and work).
- **Auth / expired error** (the refresh token expired, the cache was cleared, or your workspace
  enforces a short session) → re-run the login **once**, then you're set for the session:

  ```bash
  databricks auth login --host https://<your-workspace>.cloud.databricks.com
  ```

In short: **log in once; re-login only when the check fails.** If your workspace's session
policy is short, that may be about once a day at the start of work — a single command, then
proceed.

> **Optional convenience** — add a `dbcheck` shortcut so you can confirm auth at a glance each
> session (no per-shell network delay, since it only runs when you call it):
> ```bash
> echo "alias dbcheck='databricks current-user me >/dev/null 2>&1 && echo \"OK: Databricks auth valid\" || echo \"Re-auth: databricks auth login --host https://<your-workspace>.cloud.databricks.com\"'" >> ~/.bashrc
> source ~/.bashrc
> ```
> Then just type `dbcheck` when you sit down to work.

### Later — personal access token (PAT), for headless & CI only

You don't need this for local development; OAuth above covers it. Reach for a **PAT** later,
when you need **unattended** auth (CI pipelines, scheduled scripts, a service principal).
Unlike OAuth, a PAT *does* live in `~/.databrickscfg`.

1. Generate it: **Settings → Developer → Access tokens → Generate new token**; copy the `dapi…`
   value (shown once).
2. Put it under a named profile — just **two lines**, `host` and `token` (PAT auth is inferred
   from `token`, so no `auth_type`; `account_id` / `workspace_id` aren't needed for workspace
   operations):

   ```ini
   [__settings__]
   default_profile = vic-dev

   [vic-dev]
   host  = https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
   token = dapixxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

3. Test: `databricks current-user me`.

Or skip the file and export env vars (what CI uses):

```bash
export DATABRICKS_HOST=https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
export DATABRICKS_TOKEN=dapixxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
databricks current-user me
```

**Scopes.** The token UI makes you choose **All APIs** (flagged *not recommended*) or a
hand-picked set under **Other APIs**:

- *All APIs* is fine for a personal, short-lived token (the warning targets long-lived, shared,
  or service tokens) — set a short lifetime.
- To least-privilege it, select the families this project uses, confirming exact names with
  `databricks api get /api/2.0/token-scopes` (miss one and a command fails mid-run):

  | Project operation | Scope |
  |---|---|
  | Catalog / schema / volume / grants, drop catalog | `unity-catalog` |
  | Create RBAC groups | `scim` |
  | Bundle deploy — upload workspace files | workspace / `files` |
  | Create + run jobs | `jobs` |
  | Create + run the DLT pipeline | `pipelines` |
  | Run SQL / list warehouses (`dbsql.sh`) | `sql` |

> **Two real auth prerequisites** (more common than scope issues): (1) PAT auth must be
> **enabled for the workspace** — on by default, Premium required; if disabled, use OAuth.
> (2) The identity must hold the permissions the commands need — `make bootstrap` creates the
> catalog in Unity Catalog (workspace admin) and the RBAC groups at the **account** level, so you
> must also be an **account admin**. `bootstrap.sh` runs the one-time account login for you and
> prompts for your Account ID. On your own Premium account you are both admins.
>
> **Running the shell scripts directly** (not via `make`)? Make them executable once:
> `find . -type f -name "*.sh" -exec chmod +x {} +`.

> **Pitfall (both methods):** `host` must be a clean URL — exactly
> `https://dbc-….cloud.databricks.com`, with no Markdown link formatting, no `<your-workspace>`
> placeholder, and no trailing `/...`. A malformed host produces
> `parse "https://…": invalid character` errors.

### Multiple environments

Add one profile per env and select it with `-p` on any command:

```ini
[vic-dev]
host = https://<dev-workspace>.cloud.databricks.com
[vic-prod]
host = https://<prod-workspace>.cloud.databricks.com
```
```bash
databricks current-user me -p vic-prod      # confirms the profile resolves; deploys come later
```

You'll use `-p <profile>` later when deploying/running per environment — see
[`deployment-guide.md`](deployment-guide.md). Don't deploy anything yet; the project isn't set
up until you finish the steps below.

---

## Step 5 — Open the project in IntelliJ via WSL

1. **File → Open**.
2. Paste the UNC path from Step 2:
   `\\wsl.localhost\Ubuntu-24.04\home\<your-wsl-username>\projects\vic-suburbs-dwh`
3. Open it as a project. IntelliJ detects the git repo automatically.

(Alternatively, from the WSL terminal run `idea .` if the JetBrains shell launcher is
installed.)

---

## Step 6 — Configure the Python interpreter (as a **WSL** interpreter)

The venv lives **inside WSL**, so IntelliJ must use it through its **WSL** target — **not** as a
"Local Machine" interpreter pointed at a `\\wsl.localhost\…` path. A Linux venv's `python` is a
symlink to the Linux system Python and can't run as a Windows-local interpreter; adding it that
way fails with **"Invalid Python SDK / The SDK seems invalid"** or **"Cannot Detect SDK Version …
is corrupt."**

First confirm the venv is healthy, in the WSL terminal:

```bash
cd ~/projects/vic-suburbs-dwh
ls -l .venv/bin/python*           # python -> python3 -> /usr/bin/python3.12 (symlinks)
.venv/bin/python --version        # Python 3.12.x
```

If `.venv` is missing or that errors, recreate it (Step 3.5):

```bash
rm -rf .venv && python3 -m venv .venv
source .venv/bin/activate && pip install -r requirements-dev.txt && pip install -e .
```

Then add it as a **WSL** interpreter:

1. **Settings → Project: vic-suburbs-dwh → Python Interpreter → Add Interpreter → On WSL.**
   (Or from **Project Structure → Platform Settings → SDKs → + → Add Python SDK**, then in the
   *Add Python Interpreter* dialog switch the target dropdown from **Local Machine** to **WSL**.)
2. **Linux distribution:** `Ubuntu-24.04`; let it introspect the distro.
3. Choose **Select existing**, Type **Python**, and set the interpreter to the **Linux path**
   (not a `\\wsl.localhost\…` UNC path):
   ```
   /home/<your-wsl-username>/projects/vic-suburbs-dwh/.venv/bin/python
   ```
   e.g. `/home/tummala/projects/vic-suburbs-dwh/.venv/bin/python`
4. **OK / Apply.** The `vic_suburbs` package resolves because it was installed editable
   (`pip install -e .`).

> **Don't** pick a pre-detected `WSL (Ubuntu-24.04): …/projects/<other-project>/…` entry from
> the SDK list — that's a leftover interpreter from a different project's venv. Add the
> `vic-suburbs-dwh/.venv` interpreter explicitly.

If Community Edition lacks Python support: **Settings → Plugins → Marketplace → install
"Python"**, restart, then repeat.

---

## Step 7 — Configure the integrated WSL terminal

So IntelliJ's built-in terminal *is* your Ubuntu shell:

1. **File → Settings → Tools → Terminal.**
2. **Shell path:** `wsl.exe -d Ubuntu-24.04`
3. Apply, open **View → Tool Windows → Terminal**, confirm with `lsb_release -a`.

Optional — auto-activate the venv when the terminal opens:

```bash
echo 'cd ~/projects/vic-suburbs-dwh && source .venv/bin/activate 2>/dev/null' >> ~/.bashrc
```

---

## Step 8 — Plugins (optional)

- **Databricks** (JetBrains Marketplace) — bundle awareness, notebook sync, cluster browsing.
  Convenient, not required; everything here works from the terminal.
- **Makefile Language** — syntax + run-gutter for the `Makefile` targets.

---

## Step 9 — Run configurations

Create these via **Run → Edit Configurations → + **. They make the common loops one click.
Set **Working directory** to the project root for all of them.

| # | Type | Name | Module/Script & params |
|---|---|---|---|
| 1 | Python | Seed universe | module `vic_suburbs.generator.seed` |
| 2 | Python | Emit (mixed) | module `vic_suburbs.generator.emit`, params `--mode mixed --landing .local/landing` |
| 3 | Python tests → pytest | Unit tests | target `tests/unit` |
| 4 | Shell Script | Ruff check | script text: `ruff check . && ruff format --check .` |
| 5 | Shell Script | Deploy dev | script text: `databricks bundle deploy -t dev` |

For #1–#3, select the **`.venv` interpreter** from Step 6. For the Spark integration test, add
`JAVA_HOME` and `SPARK_LOCAL_IP` to the config's *Environment variables* (or restart IntelliJ
after Step 3.6 so they're inherited).

---

## Step 10 — Final validation

From the IntelliJ WSL terminal:

```bash
# 1. we're in WSL Ubuntu
lsb_release -a | grep 24.04

# 2. venv active
source .venv/bin/activate && which python      # -> .../.venv/bin/python

# 3. CLIs on PATH
databricks --version && jq --version

# 4. deps installed + package importable
python -c "import vic_suburbs, yaml, pandas, numpy; print('imports OK')"

# 5. unit tests pass (no workspace needed)
make test            # or: pytest tests/unit

# 6. CLI authenticated
databricks current-user me
```

IDE-side: open `src/vic_suburbs/common/dq.py` — no unresolved-import highlights means the
interpreter is wired correctly.

---

## Quick reference

```
Project path (WSL)     ~/projects/vic-suburbs-dwh
Project path (Windows) \\wsl.localhost\Ubuntu-24.04\home\<user>\projects\vic-suburbs-dwh
Interpreter            /home/<user>/projects/vic-suburbs-dwh/.venv/bin/python   (add via WSL target, not Local Machine)
Terminal shell path    wsl.exe -d Ubuntu-24.04
JAVA_HOME              /usr/lib/jvm/java-17-openjdk-amd64
Auth                   databricks auth login --host https://<workspace-url>
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: externally-managed-environment` on `pip install` | You're outside the venv. `source .venv/bin/activate` first (Step 3.5). |
| `databricks: command not found` in the IntelliJ terminal | IntelliJ isn't using the WSL shell — recheck Step 7, reopen the terminal. |
| `JAVA_HOME is not set` / Java gateway exited (integration test) | Install JDK 17 and persist `JAVA_HOME` (Step 3.6); restart IntelliJ for run configs. |
| `cannot configure default credentials` | Re-run `databricks auth login`, or export `DATABRICKS_HOST`/`DATABRICKS_TOKEN`. |
| `Invalid Python SDK` / `Cannot Detect SDK Version … is corrupt` when adding the interpreter | You're adding the WSL venv as a **Local Machine** interpreter via a `\\wsl.localhost\…` path. Add it as a **WSL** interpreter using the Linux path `/home/<user>/projects/vic-suburbs-dwh/.venv/bin/python` (Step 6). |
| `PERMISSION_DENIED` on catalog operations | Your workspace must be Premium with Unity Catalog enabled, and your user a workspace admin (or in `role_deployer`). Creating the **account-level** RBAC groups additionally needs **account-admin** rights. |
| Slow pip/git/test | The project is on `/mnt/c/...`; move it under `~/` (Step 2). |

---

## Cross-references
- After setup → [`local-development.md`](local-development.md), then [`deployment-guide.md`](deployment-guide.md)
- Repo layout → [`repository-tour.md`](repository-tour.md)
