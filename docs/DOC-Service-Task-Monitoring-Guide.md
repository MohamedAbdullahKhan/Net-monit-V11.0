# Net-monit V11.0 — Service & Task Monitoring Quick Reference

**What this guide covers:** how to find the exact names/paths of services and scheduled tasks on your Windows or Linux machines, so you can add them to Net-monit for monitoring.

---

## Part 1: Finding Services to Monitor

### Windows — Finding Service Names

**Method 1: Using Services Manager (GUI, easiest)**

1. Press `Windows Key + R`, type `services.msc`, and press Enter.
2. The Services window opens — you'll see a table with columns: Name, Description, Status, Startup Type.
3. **The Name column is what you use in Net-monit.** For example:
   - `Spooler` — Print Spooler
   - `W3SVC` — World Wide Web Publishing Service
   - `MSSQLSERVER` — SQL Server (if installed)
   - `wuauserv` — Windows Update
4. Copy the exact text from the "Name" column (case doesn't matter, but exact spelling does).

**Method 2: Using PowerShell (if you prefer command-line)**

Open PowerShell as Administrator and run:
```powershell
Get-Service | Select-Object Name, DisplayName, Status | Format-Table -AutoSize
```

This outputs a table. The "Name" column (first column) is what goes into Net-monit. Examples from the output:
```
Name                DisplayName                                      Status
----                -----------                                      ------
AdobeARMservice     Adobe Acrobat Update Service                      Running
Appinfo             Application Information                           Running
AppMgmt             Application Management                            Stopped
```

**Examples you might commonly want to monitor:**

| Service Name | What it does | Why monitor |
|---|---|---|
| Spooler | Print Spooler | Print server down = nobody can print |
| W3SVC | IIS Web Server | Web application down |
| MSSQLSERVER | SQL Server | Database service critical |
| MySQL80 | MySQL Database | Database service critical |
| wuauserv | Windows Update | Security patches not applying |
| AudioSrv | Windows Audio | Audio services down |
| BITS | Background Intelligent Transfer | File transfers failing |

### Linux — Finding Service Names

**Method 1: List all active services (easiest)**

Open a terminal and run:
```bash
systemctl list-units --type service --all
```

This shows all services. The first column (before whitespace) is the service name. Examples:
```
  accounts-daemon.service        Accounts Service
  alsa-restore.service           Save and restore ALSA card state
  alsa-state.service             Manage Sound Card State
  apache2.service                The Apache HTTP Server
  apparmor.service               Load AppArmor profiles
  avahi-daemon.service           Avahi mDNS/DNS-SD Stack
  cups.service                   CUPS Printing Service
  docker.service                 Docker Application Container Engine
  nginx.service                  A high performance web server
  ssh.service                     OpenSSH server
  sshd.service                    OpenSSH server (alternate name)
  mysql.service                  MySQL Community Server
```

**The service name is the part BEFORE `.service`.** In Net-monit, enter:
- For `nginx.service` → enter `nginx`
- For `mysql.service` → enter `mysql`
- For `ssh.service` → enter `ssh`

**Method 2: Check if a specific service exists**

```bash
systemctl status nginx
systemctl status mysql
systemctl status docker
```

If the service exists, it will show status. If not found, you'll see `Unit ... could not be found.`

**Examples you might commonly want to monitor:**

| Service Name | What it does | Why monitor |
|---|---|---|
| nginx | Web Server | Web application down |
| apache2 | Web Server (Apache) | Web application down |
| mysql | MySQL Database | Database service critical |
| postgresql | PostgreSQL Database | Database service critical |
| docker | Container Runtime | Containers can't start/stop |
| ssh | Secure Shell | Remote access down |
| postfix | Mail Transfer Agent | Email not sending |
| prometheus | Monitoring Agent | Metrics not being collected |
| redis | In-Memory Cache | Cache service down |

---

## Part 2: Finding Scheduled Tasks to Monitor

### Windows — Finding Task Scheduler Paths

**Method 1: Using Task Scheduler GUI (easiest)**

1. Press `Windows Key + R`, type `taskschd.msc`, and press Enter.
2. Task Scheduler opens. On the left side, you'll see a folder tree.
3. Navigate to the task you want to monitor. The typical folders are:
   - `\` (root) — tasks at the top level
   - `\Microsoft\Windows\` — built-in Windows tasks (usually not worth monitoring)
   - `\Backup\` — custom backup tasks (example)
   - `\Maintenance\` — custom maintenance tasks (example)
4. Click on a task to select it. The full path appears at the top or in the status bar.

**Important: copy the full path starting with a backslash.** Examples:
- `\Backup\NightlyBackup`
- `\Maintenance\UpdateInventory`
- `\CustomTasks\SyncDatabase`
- `MyTask` (if it's at the root level, just the name is enough)

**Method 2: Using PowerShell (for a quick list)**

```powershell
Get-ScheduledTask | Select-Object TaskPath, TaskName | Format-Table -AutoSize
```

Output looks like:
```
TaskPath                                    TaskName
--------                                    --------
\                                           MyRootTask
\Backup\                                    NightlyBackup
\Backup\                                    WeeklyArchive
\Maintenance\                               UpdateInventory
\Maintenance\                               ClearLogs
\Microsoft\Windows\CertificateServicesClient\ CertEnrollOnline
```

To use in Net-monit, combine TaskPath and TaskName:
- `\Backup\` + `NightlyBackup` = `\Backup\NightlyBackup`
- `\` + `MyRootTask` = just `MyRootTask` (no leading backslash needed for root)

**Examples you might commonly want to monitor:**

| Task Path | What it does | Why monitor |
|---|---|---|
| `\Backup\NightlyBackup` | Daily backup running | Backup failing = data loss risk |
| `\Maintenance\UpdateInventory` | Inventory system refresh | Database out of sync |
| `\Reports\GenerateDailyReport` | Automated reporting | Missing critical reports |
| `\Sync\SyncToCloud` | Cloud sync job | Data not syncing to cloud |
| `DiskCleanup` | Disk space cleanup | Running out of disk |

### Linux — Finding Systemd Timers

**Systemd timers are the Linux equivalent.** They pair a `.timer` file (when to run) with a `.service` file (what to run).

**Method: List all timers**

```bash
systemctl list-timers --all
```

Output looks like:
```
NEXT                        LEFT          LAST                        PASSED   UNIT
Sun 2026-07-27 02:09:00 UTC 2h 22min left Fri 2026-07-25 02:09:10 UTC 1d 22h  backup.timer
Sun 2026-07-27 06:00:00 UTC 6h 15min left Fri 2026-07-25 06:00:02 UTC 1d 18h  inventory-update.timer
Mon 2026-07-28 00:00:00 UTC 1d 22h left   Fri 2026-07-25 00:00:01 UTC 2d 2h   cleanup.timer
```

The timer name is in the "UNIT" column, with `.timer` suffix. **In Net-monit, enter just the name before `.timer`.** For example:
- For `backup.timer` → enter `backup`
- For `inventory-update.timer` → enter `inventory-update`
- For `cleanup.timer` → enter `cleanup`

**Check what service a timer runs:**

```bash
systemctl cat backup.timer
```

Output includes the service it triggers, usually `backup.service` for `backup.timer`.

**Examples you might commonly want to monitor:**

| Timer Name | What it does | Why monitor |
|---|---|---|
| `backup` | Daily backups | Backup failing = data loss risk |
| `inventory-update` | Inventory refresh | System out of sync |
| `log-cleanup` | Log file cleanup | Running out of disk space |
| `cert-renewal` | SSL certificate renewal | Certificate expiry |
| `docker-cleanup` | Clean up old containers | Disk space issues |

---

## Part 3: Adding Services/Tasks in Net-monit

### Step-by-Step: Add a Service to Monitor

1. **Go to the Devices page** in Net-monit (left navigation menu).
2. **Click "Add Device"**.
3. **Fill in basic info:**
   - ID: something like `web-server-01`
   - Name: `Production Web Server`
   - Host: the IP address or hostname (e.g., `192.168.1.50` or `webserver.company.local`)
4. **Choose method:** `SSH` (for Linux) or `PowerShell` (for Windows).
5. **Enter credentials:**
   - **Windows/PowerShell:** username and password of a Windows admin account that can query the services
   - **Linux/SSH:** username, password (or key path if using key-based auth), port (usually 22)
6. **Click Test** to confirm the connection works.
7. **Scroll down to "Watch services / scheduled tasks"** (this section only appears for SSH/PowerShell methods).
8. **Check "Watch services"**.
9. **In the text area, enter one service/task name per line.** For example:
   ```
   nginx
   mysql
   docker
   ```
   Or for Windows:
   ```
   W3SVC
   MSSQLSERVER
   Spooler
   ```
10. **Click Save**.

Net-monit will now monitor those services. If any one stops, the whole device goes Critical.

### Step-by-Step: Add Tasks to Monitor

Same as services above, but:

- In step 7, check **"Watch scheduled tasks"** instead.
- In step 9, enter one task path per line. For example:

**Windows (full paths with backslashes):**
```
\Backup\NightlyBackup
\Maintenance\UpdateInventory
\Reports\GenerateDailyReport
```

**Linux (just the timer name, no `.timer` suffix):**
```
backup
inventory-update
log-cleanup
```

### What "Any one fails = device Critical" means

If you're monitoring a server via `ping` (reachability) **and also watching 3 services on it**, here's what happens:

- **Scenario 1:** The server pings fine, but Service A is stopped → device status = **Critical** (Service A down matters more than ping)
- **Scenario 2:** The server is unreachable AND Service B is stopped → device status = **Offline** (the server itself is gone, services don't matter)
- **Scenario 3:** All services are running → device status = **OK/Healthy**

This is by design — if you're monitoring something specific (like "we need the backup service to run"), then the service being down is a critical incident, even if the machine itself is reachable.

---

## Part 4: Troubleshooting

### "Service appears to work but the tile says 'Healthy' isn't updating"

**Likely cause:** Net-monit checks services on a schedule (usually every 60 seconds). Refresh the dashboard manually (F5 or reload in your browser) to see the latest state immediately.

### "The service name I entered doesn't work"

**Windows:**
- Open `services.msc` again and check the exact spelling (including capital letters)
- Some services have spaces in their names — that's OK, just copy the exact "Name" column
- If it still doesn't work, try using the **Display Name** instead (though Name is preferred)

**Linux:**
- Confirm the service exists: `systemctl status <service-name>`
- If not found, check the exact spelling in `systemctl list-units --type service --all`
- Systemd is case-sensitive — `MySQL` and `mysql` are different

### "Service monitoring works but I want to also monitor a task"

You can enable both on the same device — just check both checkboxes and fill in both text areas.

### "I have a task that doesn't fit the pattern"

**Windows:** If your task is NOT in Task Scheduler (it's a legacy `.bat` file run via old Windows AT command), it won't appear in Task Scheduler — you'd need to convert it to a scheduled task first, then add it.

**Linux:** If you have a plain `cron` job (not a systemd timer), Net-monit can't see it — cron doesn't record success/failure anywhere standard. Options:
- Convert the cron to a systemd timer + service (recommended for modern Linux)
- Monitor the script's output/log file differently (outside this feature)
- Use a standalone monitoring wrapper around the cron job

---

## Cheat Sheet: Copy-Paste Examples

**Windows server, PowerShell connection, services to monitor:**
```
W3SVC
MSSQLSERVER
Spooler
wuauserv
```

**Windows server, PowerShell connection, tasks to monitor:**
```
\Backup\NightlyBackup
\Maintenance\DiskCleanup
\Reports\WeeklyReport
```

**Linux server, SSH connection, services to monitor:**
```
nginx
mysql
docker
ssh
postfix
```

**Linux server, SSH connection, timers to monitor:**
```
backup
inventory-update
log-cleanup
cert-renewal
```

---

**Next step:** Once you've added services/tasks, see the Admin Guide for how to configure what happens when one stops (immediate email, or wait 30 seconds first, etc.).
