# ARK ASA Server Manager (Windows)

[![Wiki](https://img.shields.io/badge/Wiki-Server%20Configuration-0078D6?logo=gitbook&logoColor=white)](https://ark.wiki.gg/wiki/Server_configuration)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/github/license/Ch4r0ne/ARK-Ascended-Server-Manager)
![Issues](https://img.shields.io/github/issues/Ch4r0ne/ARK-Ascended-Server-Manager)
[![Downloads](https://img.shields.io/github/downloads/Ch4r0ne/ARK-Ascended-Server-Manager/total)](https://github.com/Ch4r0ne/ARK-Ascended-Server-Manager/releases/latest)
[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?logo=paypal)](https://www.paypal.com/donate/?business=leinich%40me.com&currency_code=EUR)

**ARK ASA Server Manager for Windows** manage **ARK: Survival Ascended (ASA) dedicated servers** with **safe start/stop**, **reliable RCON**, and a clean **staging-based INI workflow**.

> **Disclaimer:** Not affiliated with Studio Wildcard / Snail Games.

---

## Quickstart (recommended)

1. Download the latest EXE: [Releases (latest)](https://github.com/Ch4r0ne/ARK-Ascended-Server-Manager/releases/latest)
2. Start `ARK-ASA-Manager.exe`
3. Run **First Install** once (**Administrator recommended**)
4. Configure **Paths**, **Server Settings**, **Operations**
5. Click **Start Server**
6. Use **Stop Server (Safe)** for controlled shutdown (optional backup + baseline restore)

> The tool is designed to do the safe thing by default: deterministic start-lines, safe stop sequencing, and config handling that prevents half-applied changes.

---

## Preview

![Server Tab](docs/img/ASA_Server_Manager_Preview_6.png)

---

## Core concept: Staging + Baseline

This manager separates *edit time* and *runtime*:

- **Staging**
  - your edits live here
  - applied to the server folder on **Start**
- **Baseline**
  - created once from the server’s original INIs
  - restored into the server folder on **Stop (Safe)**

> Result: no “half-edited live INIs”, fewer drift issues, and safe rollback without losing your intended changes.

---

## What you get

### First install automation (Admin recommended)
- Installs prerequisites:
  - Visual C++ 2015–2022 Redistributable (x64)
  - DirectX Legacy Runtime (web installer)
  - Amazon certificates (helps on hardened hosts / strict TLS chains)
- Installs **SteamCMD** automatically
- Downloads/updates the ASA dedicated server via SteamCMD

### Safe operations
- **Deterministic start-line generation**  
  Map, ports, session, mods, BattleEye, RCON, cluster, and advanced flags are generated consistently.
- **Safe stop sequence**  
  `SaveWorld` → `DoExit`

### Reliable RCON
- Uses Python **`rcon` (Source RCON)**
- Saved commands + fast execution
- Output written into the shared **Console**

### INI Editor (staging workflow)
- booleans via `True/False` selector
- numeric values via slider + tick marks
- staged writes are debounced to avoid noisy updates

![INI Editor](docs/img/ASA_Server_Manager_Preview_5.png)

### Dashboard and cluster wizard
- **Dashboard** tab: every server at a glance, grouped by cluster (plus standalone servers), with status, current task, players, ports, uptime and install folder; it refreshes by itself
- Per server: Start, Stop, Restart, Update & Restart, Back Up, Open (switch the other tabs to it) and Cluster... (set up or join)
- Per cluster: **Start All** (stopped maps one after another, a configurable delay apart), **Stop All**, **Restart All** one map at a time so the rest stays up, **Update All** (running maps take turns and come back up; stopped installed maps are just updated), **Back Up All**, **Broadcast** a chat message to every running map, Rename and Start Delay
- **New Cluster** wizard: pick a name and the maps, where the installs go, and the shared settings (admin and join password, max players, mods, crossplay). It previews every profile it will create with its install folder and ports, then creates one profile per map with its own install, all in one cluster with a shared data folder, and can install them right away
- **Add Maps to Cluster**: the same wizard for an existing cluster; maps already in it are greyed out and the cluster's settings are prefilled
- Other settings (backups, auto-saves, updates, Discord, advanced start options) are copied from a template profile you pick; game rates stay per map in each map's INI Editor
- Cluster names and start delays are stored in the global config; which profiles belong to a cluster still comes from each profile's own cluster settings, so existing clusters show up without any migration

### Planned restarts, warnings, moving clusters, resource use
- **Daily restart** (Server tab, Update Options): restarts a running server every day at a set time (safe: save, stop, start); it never starts a stopped one, and is skipped when the nightly update restarts the server at that same time
- **Warn players first**: in-game chat countdowns over RCON (default 15, 5 and 1 minutes) before the daily restart, the nightly update and, if you choose, a Restart All. The countdown starts early, so the restart itself happens at the time set. The Dashboard and status bar show it; stopping the server or Cancel Countdown on the Dashboard calls it off
- **Export / Import Cluster**: a cluster file holds its name, start delay and each map's settings and staged INIs, without passwords, the Discord webhook or folders of this machine. Import recreates it on another machine as a new cluster: you pick the install folder, data folder and passwords, every map gets free ports and its INI settings staged for its first start, and it can install them right away
- The Dashboard shows **CPU and memory** of every running server, and the memory of each cluster

### Cluster health, copying settings, reconnecting
- **Health Check** (Dashboard, per cluster) lists what keeps a cluster from working: maps on different data folders, a folder kept inside one install, different mod lists or crossplay settings, ports another profile also uses, maps with the same server name, maps sharing one install, RCON off, maps not installed yet. Most have a one-click fix; Fix All applies the safe ones (ports, server names, platform) after one confirmation, while mod list and data folder fixes are done one at a time. The mod fix only ever adds the mods a map lacks, never removes any. Fixes change the saved profiles and apply on each map's next restart
- The cluster row on the Dashboard shows how many issues it has, and **Start All** asks before starting a cluster with problems
- **Copy Settings To...** (Dashboard, per server, and the INI Editor) copies one server's staged GameUserSettings.ini / Game.ini onto other servers: rates, taming, breeding, structures and so on. Each target keeps its own name, ports, passwords, RCON, mod list and any setting only it has; map-specific spawn container entries stay out unless asked for. Applies on each target's next start
- **Reconnect on launch**: servers left running when the manager closed are found again by the process ID the manager recorded when it started them, with the ports and RCON login they really run on (even if the profile was edited since); servers started outside the manager are matched by install folder and game port and use the RCON login of the install's live GameUserSettings.ini. They show as running and can be saved, stopped, backed up, sent RCON and auto-restarted after a crash as usual; only their console output is not available. Stopping a server the manager did not start leaves its live INIs as they are
- Only one manager runs per storage folder, so two windows never save, restart or update the same servers twice

### Several servers in one window
- Every profile has its own runtime: servers run side by side, and the profile selector only picks which one the window shows and edits. Switching never stops anything, and running servers are marked in the selector
- Start, stop, restart, update, back up and send RCON to the server on screen while others keep running; each server runs one task at a time, different servers run theirs in parallel
- Auto-saves and the nightly Auto Update & Restart run for every profile that has them on, not just the one on screen. The nightly update only touches servers that are running (it never starts a stopped one or installs into an unused profile), waits for a busy server instead of skipping the night, and servers take turns with SteamCMD while they keep running; a failed update brings the server back on the installed version
- Starting refuses a game, query or RCON port another running server already uses
- The console tags every line with its server and can show only the selected one; each server also logs to `servers/<id>/logs/server.log` (logs rotate at 20 MB, 3 kept)
- Backups made in the same second by different servers get their own zip file
- Closing the window while servers run offers three choices: **keep running in the background** (the window hides, and auto-saves, nightly updates, crash restarts and countdowns go on; opening the manager again shows the window), stop every server safely and quit, or quit and leave them running (the manager reconnects to them when it is opened again)

### Clusters
- **Set Up / Join Cluster...** (Advanced Start Args tab) turns an existing standalone server into the first map of a new cluster, or joins it to a cluster another profile already runs
- Sets the cluster ID and gives the cluster one shared data folder (by default `<manager storage>\clusters\<id>`), so transfers work across separate installs; without a shared folder each install keeps uploads in its own `Saved\clusters` and other maps never see them
- The world save, survivors, tribes and INI settings are not moved or changed; a running server keeps running and is offered a safe restart to join
- Optionally copies transfer data the server already has into the cluster folder (existing files there are kept) and backs the server up first
- Joining warns when mod lists differ, when two maps share one install folder, or when ports clash with another profile
- **Leave Cluster** takes a server back out; its world and the cluster folder are kept
- Ticking *Enable Cluster* by hand fills in an ID and a shared folder; the tab shows the cluster status and warns about settings that silently break transfers (no shared folder, spaces in the path)
- Full backups (on stop and manual) include the shared cluster folder (`ClusterData/<id>/` in the zip); auto-saves leave it out, and restores never write it back, since every map in the cluster shares it
- INI Editor **Cluster & Transfers** tab: upload/download restrictions, upload slots and expiry
- Copied transfer data moves from the old cluster ID's folder to the new one's, so it shows up under the new ID; if the server is running it is saved and stopped for the copy, then started again, so nothing can be downloaded twice. A folder other maps still use is never copied
- Starting asks first when another server already runs from the same install folder (they would share its INIs); updating refuses, since SteamCMD cannot replace files a running server holds, and Update & Restart checks this before stopping anything
- Cluster folders must be full paths outside the install, and may not contain the install or the backup folder
- *Custom Path* only applies while *Enable Cluster* is on: a profile that had a custom path with the cluster off no longer passes `-ClusterDirOverride`

### Advanced start arguments (grouped)
- Cluster configuration
- Dino modes (mutually exclusive)
- Logs
- Mechanics / performance flags

### Backups + retention
- Optional backup on stop
- Optional **auto-saves** every N minutes (minimum 5) while the server is running: the world is saved over RCON first, then zipped as `ASA_AutoSave_*.zip`; if RCON is off or fails, the last save on disk is zipped. Auto-saves keep their own count (default 12), separate from backup retention, so they never push manual or on-stop backups out. They count from when the server starts, wait for any running task, and log failures instead of popping up a dialog. The Server tab shows the next run and how far back the kept auto-saves reach. Not to be confused with ARK's own Auto-Save Interval in the INI Editor, which only writes the world to disk
- Zip retention policy, counted per server: profiles that share a backup folder no longer prune each other's zips
- **Manage / Restore Backups** window: lists this server's zips (full, before-import and before-restore), makes a new one, deletes old ones, and restores a world save and/or its `GameUserSettings.ini` / `Game.ini`
- A restore rebuilds the map folder beside the live one and swaps it in, so a failure part way leaves the current save untouched; the current save and settings are zipped first by default
- Restored settings replace the INI staging too, so the next start applies exactly what the backup held; server name, passwords, RCON and max players still come from the profile
- Each zip records which server made it, and the window warns before restoring another server's backup

### Single player import
- Auto-detects the game folder across every Steam library, plus `%LOCALAPPDATA%` and Game Pass installs
- The folder you pick is used as given — point at the game folder, at `Saved`, at `SavedArksLocal`, or straight at a single map folder
- Copies the selected world (plus profiles and tribes) into the server save folder, honouring `AltSaveDirectoryName`
- Your survivor does not come across with the world — the server rebuilds player data for its own accounts on its first save. The import screen walks through moving it with an obelisk upload/download, and shows the exact `cheat TakeTribe <id>` for the selected save to take back the base and tames
- Zips the server save it is about to overwrite into the backup folder first
- Optionally merges the single player `GameUserSettings.ini` / `Game.ini` into staging; server name, passwords, RCON, ports and max players are kept
- Cleans up ARK's float spew (`0.00999999978` becomes `0.01`), and optionally rounds off its slider noise (`0.999989986` becomes `1`)
- Optionally copies the mod list: the client's `ActiveMods` becomes the server's Mods field, so they are passed with `-mods=` on the next start

### Auto update & restart
- Time-based update/validate + safe restart
- Skips triggers while the app is busy
- Add the 'Auto Start' option to the last profile on startup.

### CLI flags
- `--no-admin-prompt` (alias: `-no-admin`)
  - Skips the "Administrator rights recommended" dialog
  - Starts directly in non-admin/limited mode
  - No UAC relaunch attempt is made

### Windows autostart
Startup folder runs **at user logon**. For a reliable autostart, prefer **Task Scheduler**.

Shortcut (Startup folder):
```text
"C:\Path\ARK-ASA-Manager.exe" --no-admin-prompt
```

Task Scheduler (recommended, GUI visible):
- Trigger: **At log on**
- General: **Run only when user is logged on**
- Action:
  - Program/script:
    ```text
    C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
    ```
  - Add arguments (replace the paths):
    ```text
    -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "Start-Process -FilePath '<EXE_FULL_PATH>' -ArgumentList '--no-admin-prompt'"
    ```

---

Option B: Build EXE (PyInstaller)
#### Build command
```powershell
pyinstaller --noconfirm --clean --onefile --windowed `
  --name "ARK-ASA-Manager" `
  --icon ".\assets\app.ico" `
  --add-data ".\assets;assets" `
  --collect-all rcon `
  ".\ARK-Ascended-Server-Manager.py"
```

---

## Trust & integrity (recommended)

Unsigned Windows executables (especially Python GUI + networking features) can trigger SmartScreen/Defender heuristics.

#### Verify SHA256 locally (PowerShell)
```powershell
Get-FileHash -Algorithm SHA256 ".\ARK-ASA-Manager.exe"
```

> Best practice: compare the SHA256 from the Release notes/assets with your local hash before running the binary.

---

## Upgrading from an earlier version

Existing profiles keep working; a few things behave differently:
- **The nightly Auto Update & Restart only updates servers that are running.** It no longer starts a server that was stopped (on purpose or by a crash); update a stopped server with Update / Validate
- **In-game restart warnings are on by default** (15, 5 and 1 minutes, over RCON). With the nightly update at 03:00, players see the first warning at 02:45 and the update happens at 03:00. Untick *Warn players first* on the Server tab to turn them off
- **-ClusterDirOverride is only passed while *Enable Cluster* is on.** A profile with a custom cluster path but the cluster off no longer gets it
- **Running servers are asked for their player count over RCON once a minute**, for the Dashboard
- **Only one manager runs at a time.** Opening it a second time shows a message instead of a second window
- **Two profiles never run the same world**: starting a profile is refused while another server runs the same map from the same install with the same save folder
- Closing the window while servers run now asks whether to keep managing them in the background, stop them, or leave them running; opening the manager again while it runs in the background shows its window

## Running a cluster, step by step

### Turn the server you already run into a cluster
1. Open the server's profile, go to **Advanced Start Args** and click **Set Up / Join Cluster...**
2. Pick **Start a new cluster with this server**. The ID and a shared data folder are filled in; keep them unless you have a reason not to
3. Leave **Back up this server first** ticked and click **Create Cluster**. The world, players and INI settings stay as they are
4. If the server is running, accept the safe restart when asked (or restart it later). Players can download what they had uploaded once it is back

### Add more maps
1. On the **Dashboard**, select the cluster and click **Add Maps to Cluster...**
2. Tick the maps, choose where their installs go, and check the preview of profiles, folders and ports
3. Click **Add Maps**. With *install right away* ticked, the maps copy the server files of an existing install (or the first one downloads and the rest copy it), so this is quick
4. Set each map's rates in its **INI Editor**, or set them on one map and use **Copy Settings To...** for the others

### Start a cluster from scratch
- On the **Dashboard**, click **New Cluster...**: name it, tick the maps, pick the install folder and the shared settings, and create it. It works like Add Maps, for a new cluster

### Run it every day
- **Start All** on the Dashboard starts the stopped maps one after another; **Stop All** saves and stops them all
- Turn on **Auto Update & Restart** and/or a **Daily restart** per map; with **Warn players first** on, players get chat countdowns before each one
- Run **Health Check...** after changing things: it finds mismatched mod lists, data folders, ports or server names, and fixes most of them in one click
- Forward each map's game and query ports (UDP) on the router, as listed on the Dashboard

### Move it to another PC
- **Export...** on the Dashboard writes the cluster to a file (no passwords or local folders in it); **Import Cluster...** on the other PC recreates it with new installs and free ports

## Usage notes

### Multi-instance hosting
- Use **unique ports per instance** (game/query/RCON)
- Give each map that runs at the same time **its own install folder**: the INI files live inside the install, so two servers on one install overwrite each other's settings
- Use `AltSaveDirectoryName` per instance to keep saves separated
- For clusters: use **Set Up / Join Cluster...** on each map so they share one cluster ID and one cluster data folder

> Rule of thumb: treat each instance as a separate “service unit” (ports + save path + config), even if they share the same host.

---

## Networking

### Typical defaults (depends on your config)
- **Game Port (UDP):** `7777`
- **Query Port (UDP):** `27015`
- **RCON Port (TCP):** `27020` *(only if RCON enabled)*

### Router / NAT (port forwarding)
Forward ports to the server host:
- `7777/UDP`
- `27015/UDP`
- `27020/TCP` *(optional, only for RCON)*

### Windows Firewall (PowerShell)
#### Allow inbound ports
```powershell
New-NetFirewallRule -DisplayName "ARK ASA Game Port (UDP 7777)"   -Direction Inbound -Action Allow -Protocol UDP -LocalPort 7777
New-NetFirewallRule -DisplayName "ARK ASA Query Port (UDP 27015)" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 27015
New-NetFirewallRule -DisplayName "ARK ASA RCON Port (TCP 27020)"  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 27020
```

### Validate listening ports
#### netstat checks
```powershell
netstat -aon | findstr :7777
netstat -aon | findstr :27015
netstat -aon | findstr :27020
```

---

## Security

### RCON
Do **not** expose RCON to the public internet.

### Credentials
- Keep your Admin/Join password private
- Avoid committing `config.json` or secrets to public repos

---

## Troubleshooting

### “First Install” fails
Run the EXE **as Administrator**. Installers and certificate store writes may fail without elevation.

> Hardened Windows builds often enforce stricter certificate chain rules and installer restrictions.

---

## Dependencies (Official Download Sources)

| Category | Component | Purpose | Source |
|---|---|---|---|
| Runtime | SteamCMD | Install / update ASA dedicated server | [steamcmd.zip](https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip) |
| Runtime | VC++ 2015–2022 (x64) | Microsoft runtime libraries | [vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| Runtime | DirectX Web Setup | Legacy DirectX components | [dxwebsetup.exe](https://download.microsoft.com/download/1/7/1/1718CCC4-6315-4D8E-9543-8E28A4E18C4C/dxwebsetup.exe) |
| Trust | AmazonRootCA1 | TLS trust chain (hardened hosts / strict chains) | [AmazonRootCA1.cer](https://www.amazontrust.com/repository/AmazonRootCA1.cer) |
| Trust | Amazon R2M02 | TLS trust chain (hardened hosts / strict chains) | [r2m02.cer](https://crt.r2m02.amazontrust.com/r2m02.cer) |

> **Note:** “First Install” may download these components from official vendor endpoints (listed).

<details>
<summary><b>Star History</b></summary>
<br>

[![Star History Chart](https://api.star-history.com/svg?repos=Ch4r0ne/ARK-Ascended-Server-Manager&type=date&legend=bottom-right)](https://www.star-history.com/#Ch4r0ne/ARK-Ascended-Server-Manager&type=date&legend=bottom-right)

</details>
