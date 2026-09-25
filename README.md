# Vessel diversion monitor — setup and rollout

This project reads a Google Sheet watchlist, requests Kpler position history for each voyage, estimates possible changes in route, records position traces and flags in the sheet, and posts **state-change alerts** to Telegram. `app.py` displays a read-only route map and watchlist from the sheet. The worker is scheduled separately from the dashboard.

**Status:** Code and mocked tests are ready. The Windows operator has confirmed live Google Sheets and Telegram connectivity, one successful live sheet update, and three successful Kpler dry runs spanning about five minutes. A run after more than ten minutes with the dedicated Kpler browser profile closed is still needed before scheduling. Treat all vessel flags as analyst prompts, not verified destinations.

## 1. Check access before building infrastructure

1. Ask your team's Kpler administrator whether your account is authorized to automate calls to the inherited `terminal.kpler.com/api/vessels/{id}/positions` endpoint and refresh-token grant. This is an inherited terminal interface, and its behavior, access rights and response format have **not** been verified live. If Kpler offers your organization a supported API, use its documented method instead.
2. Arrange access to a Kpler login that is authorized for this monitoring job. The uploaded old token and password-bearing files were shared in a previous transfer. **Change the disclosed password and revoke those sessions** before continuing. Do not put those old credentials in the new deployment. The worker refreshes and saves its own tokens. `reauthorize_kpler.py` performs a separate, interactive password/MFA login when an operator starts it in a terminal; it never stores a password or MFA code. The scheduled worker does not prompt for passwords or MFA. If the refresh token is rejected, the worker exits with an error and sends a generic Telegram health notice if Telegram is working.
3. Agree on where voyage and cargo data may be hosted. Kpler-derived tracks and commercially sensitive cargoes should only be put into a Google Sheet, Telegram chat and Streamlit hosting approved by your team. Use a private GitHub repository and private Streamlit app with explicitly invited viewers.

## 2. Prepare the watchlist

Create one Google spreadsheet and a tab named `Watchlist`. Paste these exact headers into row 1, left to right:

```text
Last_Updated	IMO	Name	KPLER_ID	Departure	Coord_Trace	Diversion_Flag	Original_Dest	Original_Dest_Lat	Original_Dest_Long	Cargo
```

For your first test, add **one active voyage**. Enter its IMO, name, Kpler vessel ID, loading-port departure date in `YYYY-MM-DD` format, expected destination name and its latitude/longitude, and cargo description. Leave `Last_Updated`, `Coord_Trace` and `Diversion_Flag` blank. A checkbox for `Diversion_Flag` is also supported. Use the intended voyage's real departure date; an old departure date can hit the Kpler 5,000-position limit. Remove completed voyages from the watchlist after review. If you place blank rows between vessels, the worker retains their physical row positions.

Example *structure* (check the real data before entering it):

| IMO | Name | KPLER_ID | Departure | Original_Dest | Original_Dest_Lat | Original_Dest_Long | Cargo |
|---|---|---:|---|---|---:|---:|---|
| 9579509 | Almi Galaxy | 89161 | 2026-06-16 | Singapore | 1.20045 | 103.590085 | 150kt LSFO |

The 2026 departure in that example is historical and should not be used for the first live test. The algorithm treats destinations within 50 nautical miles of the Singapore reference point as Singapore-bound. A missing destination coordinate is an error.

## 3. Create Google credentials

Follow the current [gspread service-account instructions](https://docs.gspread.org/en/master/oauth2.html) to create a Google Cloud project, enable the Sheets API, create a service account and download **a fresh JSON key**. Share the spreadsheet with the service account's `client_email` as an Editor; otherwise opening it by ID fails. A dedicated read-only account for the dashboard is a good later improvement. Never upload the JSON key to GitHub or paste it into this chat.

The spreadsheet ID is the text between `/d/` and `/edit` in the spreadsheet URL. The tab name is `Watchlist` unless you chose another exact name.

## 4. Create a test Telegram destination

Use BotFather to create or reuse an appropriately scoped bot. Put it in a **test group** first, give it permission to post, and obtain that group's numeric chat ID. You can use the group ID from your UKMTO setup if the same bot is added and permitted, but keep test alerts away from the production group until approved. Telegram's `sendMessage` API accepts a numeric chat ID or a public channel username. Bot tokens must stay secret.

## 5. Configure on a Linux VPS

Use a non-root user with access to Python 3.11+ and `flock`. Copy only the files in this project to a private VPS directory (example below uses `$HOME/shiptracking`). Do **not** copy the uploaded `secrets.env` or `kpler_tokens.json` from the predecessor. From the project folder:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-worker.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python configure.py
.venv/bin/python provision_token.py
chmod 700 .
chmod 600 secrets.env kpler_tokens.json .streamlit/secrets.toml
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
The worker requires the `tzdata` package there because Windows does not
normally ship the IANA `Asia/Singapore` time-zone database. For a project
folder extracted before this dependency was added, run
`.\.venv\Scripts\python.exe -m pip install tzdata` and rerun the tests.

`configure.py` asks for the fresh Google key file path, sheet ID, tab name, Telegram bot token and test chat ID, and optionally a dashboard URL/password. It creates `secrets.env` and local Streamlit secrets without printing their contents. If your Kpler account is authorized to use the inherited terminal login and password grant, run `reauthorize_kpler.py` interactively instead of copying a refresh token from Chrome. It prompts for email, a hidden password and, when requested, an authenticator or email MFA code. It saves only the new refresh token. `provision_token.py` remains available for an independently issued, authorized refresh token. The worker rotates the saved refresh token atomically. Keep one worker process per token file.

The inherited Auth0 client ID is a default in `kpler_handler.py`. If Kpler
authorizes a refresh token for a different application, set `KPLER_CLIENT_ID`
in your private `secrets.env` to the **client ID for that same application**.
Never put an access token in that setting: `provision_token.py` accepts only
the refresh token. Browser-session refresh tokens may rotate when the browser
renews its session; do not use another person's browser session as a scheduled
worker credential. The interactive login attempts to obtain a fresh token
chain directly from Auth0. It does not open or scrape Chrome. It requires the
password grant to be enabled for the authorized account. If that grant is
blocked, request a supported sign-in method from your Kpler administrator.
A rejected token reports only the HTTP status and a recognized error code,
never the response body or credential values.

**Interactive Windows login, after rotating any credentials already exposed:**

```powershell
.\.venv\Scripts\python.exe .\reauthorize_kpler.py
```

Enter the approved account's email and password when prompted, then select
the available MFA method. **Do not paste credentials into commands, Git, or
chat.** The script replaces
`kpler_tokens.json` only after a complete, successful login with a fresh
refresh token. It will not overwrite an existing token file if authentication
fails. `HTTP 401` or `invalid_grant` at the password step may mean the login
grant is unavailable, the credential was revoked, or the credentials were
rejected; it is not fixed by changing the 10-minute access-token lifetime.

Next run a dry run, wait more than 10 minutes, and run it again. The log should
show HTTP 200 for the token and position requests on **both** runs, with no
new token pasted or provisioned in between. With the current worker, check
the log line `Kpler refresh token: returned=True changed=True` to confirm a
different token was received. The file's modified time only confirms that
the file was written; it does not prove the value changed. Never print the
contents of the token file:

```powershell
$env:DRY_RUN = "1"
.\.venv\Scripts\python.exe .\shipment_update_flagging_workflow.py
Get-Item .\kpler_tokens.json | Select-Object Name, LastWriteTime
```

Run the same two commands again after more than 10 minutes. Never share the
token file or its contents. If browser activity still invalidates this new
login's token, this account cannot reliably serve both sessions; ask the
account owner/administrator for a separate authorized monitoring login. Do
not schedule the worker while either dry run fails.

On Windows, Explorer's **Copy as path** may paste a quoted path. The current
`configure.py` accepts those quotes; an older extracted copy requires you to
remove the surrounding quotation marks before pressing Enter. To find the
Telegram group ID, send `/start@YourBotUsername` in the group and run
`.venv\Scripts\python.exe get_chat_id.py`. It asks for the token without showing
it and prints only group names and numeric IDs. If no group is listed, send
the command again and rerun the helper.

After configuration, on Windows run
`.\.venv\Scripts\python.exe check_setup.py` to read the watchlist and check the
bot's access to the configured group. Run the same command with `--send-test`
to post one labeled connectivity message. These checks do not contact Kpler or
write to the spreadsheet. If you previously disclosed the bot token, replace
it through BotFather and update `TELEGRAM_BOT_KEY` before this check.

**First live API check, no sheet writes or Telegram messages:**

```bash
DRY_RUN=1 ./scraper.sh
```

This dry run still reads the Google Sheet, calls Kpler and may rotate `kpler_tokens.json`. Read the output carefully: confirm the Kpler response is current, origin coordinates are correct, one row is processed, and the proposed flag makes sense. If the worker reports an authentication or endpoint error, stop here and resolve Kpler access. Avoid copying token values or response headers into logs or chat.

**Controlled write/send test:** ensure the bot points to the *test* Telegram group, then run:

```bash
./scraper.sh
```

Confirm three things: `Last_Updated` and `Coord_Trace` are populated, `Diversion_Flag` matches the assessment, and a Telegram alert arrives **if and only if** the flag changed. A normal first vessel may generate no alert; do not force a false diversion by manipulating a production voyage. Verify a transition on a historical or controlled test case later.

## 6. Schedule only after the manual run works

### Windows Task Scheduler (Keith's VS Code computer)

The three Kpler dry runs on 25 September succeeded at 14:54, 14:57 and 14:58 and updated `kpler_tokens.json`. They establish that consecutive refreshes work, but do not yet cover the original failure after approximately ten minutes. Before creating a scheduled task, keep the browser profile used to obtain this token **closed**, wait more than ten minutes from the last successful run, and run one more dry run without reauthorizing or replacing the token:

```powershell
$env:DRY_RUN = "1"
.\.venv\Scripts\python.exe .\shipment_update_flagging_workflow.py
Get-Item .\kpler_tokens.json | Select-Object Name, LastWriteTime
```

Both Kpler HTTP requests should say `200 OK`, and the worker should report whether Kpler returned a different refresh token. `DRY RUN: ... no writes or alerts` is expected. If the refresh request instead returns `401` or `403 invalid_grant`, do not enable the task: check that no browser session or other worker is rotating the same token, and resolve the conflict or expiry first. A short access-token lifetime by itself does not require a run every ten minutes; a valid refresh-token chain is renewed when the worker starts. Keep this project's token file exclusive to this worker. If an exclusively used token still fails after less than an hour idle, an hourly cloud schedule will fail too.

Once that check succeeds, the included `run_worker_windows.cmd` runs the worker from this folder, forces live mode, appends output and exit codes to `worker.log`, and returns the worker's exit code to Task Scheduler. It expects `.venv\Scripts\python.exe`, `secrets.env`, and `kpler_tokens.json` already in **this same folder**. Do not extract an update over those local files. A task run can update the sheet and send Telegram alerts if a vessel changes state.

1. Open **Task Scheduler** in Windows, select **Create Task...**, and name it `Ship diversion monitor`.
2. On **General**, select your own Windows account (the account that owns this project). Choose **Run whether user is logged on or not** if your account and Windows configuration permit background tasks; Windows may ask for your Windows password when saving. If you cannot use that option, **Run only when user is logged on** works while you remain signed in. Do not run it as SYSTEM.
3. On **Triggers**, click **New...**; choose **On a schedule**, **Daily**, a start time a few minutes in the future; tick **Repeat task every** and choose `1 hour`, set **for a duration of** `1 day`, ensure **Enabled** is checked, and click **OK**. Hourly is an example monitoring cadence; choose a frequency appropriate to your watchlist and access limits.
4. On **Actions**, click **New...** and select **Start a program**. In **Program/script**, enter `C:\Windows\System32\cmd.exe`. In **Add arguments**, enter `/c "C:\Users\keith\Downloads\shiptracking_project\run_worker_windows.cmd"`. In **Start in (optional)**, enter `C:\Users\keith\Downloads\shiptracking_project` (no quotes). If you moved the project, update **both** paths. Click **OK**.
5. On **Conditions**, if this is a laptop and you want runs on battery, clear **Start the task only if the computer is on AC power**. Under **Network**, require an available internet connection if the option is offered. Review **Settings**: enable **Run task as soon as possible after a scheduled start is missed**, and choose **Do not start a new instance** for **If the task is already running**. Avoid starting the worker by hand while the scheduled task is running.
6. Save the task. Select it and click **Run** once. Wait for its status to become **Ready**; inspect the last lines of its log using `Get-Content .\worker.log -Tail 40` from the project folder. You want two Kpler `200 OK` results, `Done...`, and `Scheduled run exit code: 0`. Task Scheduler's **Last Run Result** should be `0x0`. The Telegram group receives a vessel alert only when the diversion flag changes. The dry run setting in your existing PowerShell window does not control the scheduled run.
7. Check **Next Run Time** in Task Scheduler and verify the log gets another entry at the next scheduled hour. If it shows `401`, the browser or another process may be consuming the same token chain. If it shows a missing Python message, verify the `.venv` folder is still alongside the new `.cmd`. An offline or sleeping computer will miss monitoring windows; use an approved always-on host for reliable coverage.

`worker.log` grows with every run; periodically archive or rotate it after you have confirmed operation. The task settings stop duplicate runs of *this task*, while manually starting the worker is a separate process; keep to one worker at a time.

### Railway cron job (cloud alternative to your laptop)

Your predecessor's `scraper.sh` activated a Python environment, changed into `/root/shiptracking/`, and ran `shipment_update_flagging_workflow.py`; the saved `cron_log.txt` shows a 04:00 run with no watchlist data. This is consistent with a server cron setup, but the actual crontab entry was not in the files you received. Railway can perform the scheduling itself: use **one cron service** that starts, runs the worker once, then exits. The included `Dockerfile` installs only `requirements-worker.txt` and copies only the three worker modules. The `.dockerignore` excludes local credentials and token files.

**The rotating refresh token must survive redeploys.** Railway's normal deployment filesystem is temporary; attach one persistent volume at `/data`, set `KPLER_TOKEN_FILE=/data/kpler_tokens.json`, and set `WORKER_STATE_DIR=/data` to retain failure notices between runs. The worker writes the replacement token atomically beside the old one on this volume. Do not mount a volume over `/app`, which contains the application code. One volume and one job should own this token chain. Railway schedules in **UTC**: `0 * * * *` runs hourly at the top of each hour, which is also the top of each Singapore hour. Cron jobs can skip a scheduled execution if the previous execution is still active.

1. Keep the original Windows setup working until the last dry run more than ten minutes apart has passed. Close the dedicated Kpler browser profile, and plan a brief transfer: stop the Windows Task Scheduler task if you created one, and do not run the Windows worker again after transferring its token file.
2. Create a **private** GitHub repository containing this project's source files. Before committing, inspect `git status --short`: `secrets.env`, `kpler_tokens.json`, `.streamlit/secrets.toml`, `.venv` and logs must not appear. Put `Dockerfile` and `.dockerignore` beside `shipment_update_flagging_workflow.py`. Link that repository to a new Railway project/service. Railway detects the Dockerfile. In the service **Settings**, keep **Root Directory** at the directory containing the Dockerfile (typically `/` if the repo root contains these files).
3. In Railway, attach a **volume** to the worker service with mount path `/data`. Set these service variables using the Railway **Variables** editor: `GSHEET_CREDENTIALS` = the **entire** service-account JSON as one JSON string; `SPREADSHEET_ID`, `SHEET_NAME`, `TELEGRAM_BOT_KEY`, and `TELEGRAM_CHANNEL_ID` = the same values you use locally; `KPLER_CLIENT_ID` = the client ID used by the successful local worker if you have set it; `KPLER_TOKEN_FILE=/data/kpler_tokens.json`; `WORKER_STATE_DIR=/data`; and initially `DRY_RUN=1`. Set `DASHBOARD_URL` only if you have deployed a dashboard. Do not upload `secrets.env` or the service-account JSON file to GitHub.
4. Install and sign into the Railway CLI locally, link it to **this** Railway project, and select the worker service/production environment. Once the volume is attached, copy the **latest** locally rotated token file from your VS Code project terminal to the volume. The Railway CLI will prompt you to select a volume if there is more than one:

   ```powershell
   railway volume files upload .\kpler_tokens.json /kpler_tokens.json
   railway volume files list /
   ```

   The file listing should show `kpler_tokens.json`. Do not print or paste its contents. If the CLI cannot access the volume while the service has not yet deployed, first deploy with `DRY_RUN=1` (a missing-token run can fail), upload the token, then start another run. Never re-upload the original file after Railway has run: it may contain a used-up refresh token.
5. In the service **Settings**, set **Cron Schedule** to `0 * * * *`. The Dockerfile's default start command is `python shipment_update_flagging_workflow.py`. Do not set a separate sleep loop, Streamlit command, or Windows `.cmd` command. Apply/deploy the settings. At the next hourly run, inspect the Railway service **Logs** for two Kpler `200 OK` responses and `DRY RUN: ... no writes or alerts`. Verify that the volume file is still present using `railway volume files list /`. This first run validates the transfer without changing the sheet.
6. Change only the Railway variable `DRY_RUN` to `0`, deploy the staged change, and inspect the next run. `Done. 0 transition alert(s)` is normal for a vessel that remains unflagged. Confirm the Google Sheet's trace and timestamp update. Only one location should run the worker now; leave the Windows scheduled task disabled. Keep the browser profile that issued this token closed.

Railway charges according to its current plan and resource usage, and cron timing may vary by a few minutes. Check Railway's pricing and your workspace's estimated usage before relying on it. The Streamlit dashboard is a **separate** service with separate dependencies; this cron service only updates the Sheet and sends Telegram alerts.

### Linux cron (optional VPS deployment)

Edit the crontab of the same Linux user that owns the project (`crontab -e`). Use the actual absolute paths for your server:

```cron
0 * * * * /home/YOUR_USER/shiptracking/scraper.sh >> /home/YOUR_USER/shiptracking/worker.log 2>&1
```

At minute 0 each hour, cron launches the shell script, which enters the project folder, starts its virtual environment's Python and uses `flock` to prevent overlapping runs. Its lock is `/tmp/shiptracking-worker.lock`. Replace the user/path placeholders; don't put your bot token in the crontab. Check `tail -n 100 worker.log` after the first scheduled hour. Add log rotation once it runs continuously. A generic failure notice is sent once per failure episode to the configured Telegram chat; a recovery notice follows the next success. If Telegram itself is down, read the VPS log.

## 7. Preview or deploy the optional dashboard

On Windows, after the worker has written a position trace, run these commands
from your VS Code project terminal to preview the dashboard locally:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m streamlit run .\app.py
```

Open Streamlit's localhost URL and select a vessel. The chart shows connected
recorded route segments, recent positions in another color, and an arrow at
the last reported location when Kpler reports heading or a moving course over ground.
Use **Focus on latest position** to zoom to that position and **Refresh** to
reload the sheet. Hover for timestamps; older than 48 hours shows a stale
position warning. These are last *reported* positions, not a live vessel feed.
If the arrow is absent below 1 knot, no heading was available and AIS course
may not reliably show its bow direction. Long gaps and implausible jumps are
left open; the line between two AIS messages is only an approximate path.
The worker now spreads saved points across the whole voyage while retaining
the newest 75 consecutively. Run the worker again once after upgrading to
repopulate older traces with the improved sampling. This may replace the
existing `Coord_Trace` for your watchlist vessels; use a dry run first if you
have changed their inputs.

The missing original `app.py` has been replaced with a new read-only one. It **does not** run the monitoring worker. Push only source files, examples and requirements to a **private** GitHub repo; verify `secrets.env`, `kpler_tokens.json`, `.streamlit/secrets.toml`, `.venv`, logs and `.worker_failed` are ignored. Example before committing:

```bash
git status --short
```

On Streamlit Community Cloud, connect the private repo and choose `app.py`. Paste the contents of your locally generated `.streamlit/secrets.toml` into the app's Secrets settings; do not commit that file. Set the app private, then invite only the intended viewers. The optional shared password adds one more gate, though private hosting and invited viewers should control access. Streamlit may install `requirements.txt`, which contains only frontend dependencies. After the app works, enter its URL as `DASHBOARD_URL` in the VPS `secrets.env` and restart the next worker run; the Telegram link will then appear in subsequent alerts.

## 8. Check the trading usefulness

Use historic vessel movements where you know the actual outcome. For each expected Singapore arrival, compare when the first alert would have fired with when the diversion became clear in Kpler; log true alerts, false alerts and misses. Pay attention to Cape routes, waiting at anchor, AIS gaps and the Singapore approach. The algorithm's great-circle corridors, 45-degree heading and 2-of-3 rule are **unvalidated heuristics**. Never automatically remove a cargo from the LSFO balance solely because this code flagged it.

## Changes from inherited code and remaining limits

- Reads origin from `geo.lat`/`geo.lon`, uses destination proximity for SG mode, fixes mode-dependent Telegram text, recognizes blank previous flags, keeps physical sheet row numbers, preserves flags on Telegram failure, and reports worker failures.
- Does not silently replace the watchlist's vessel data. It updates only timestamp, trace and flag. It holds the previous flag if the last AIS position is more than 48 hours old or the ship is near a listed chokepoint.
- A lost response after a successful Telegram send, or a sheet write failure after the send, can cause a duplicate next run. A production-grade exactly-once workflow would need an outbox or durable event IDs.
- Kpler position retrieval is capped at 5,000 raw entries. The worker fails rather than pretending truncated data are current. Stored trace contains at most 180 valid pings: samples spread over the complete voyage plus 75 consecutive recent pings. Map lines connect only nearby, plausible saved observations and do not prove the vessel followed the exact line between AIS messages.
- The coordinate heuristic can still produce false alerts near indirect routes. A persistent failure notice depends on Telegram being reachable. The dashboard's single shared password is optional and should not replace private hosting.
