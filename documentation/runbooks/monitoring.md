# Runbook: outside monitoring, heartbeats, Cloudflare switch (HA-MONITOR)

Owner-run. Nothing here is automated against prod, and nothing in the repo touches prod. Plan:
`documentation/plans/observability/PLAN-ha-monitor-14oct.md`.

What this sets up, and why each piece exists:

| Piece | Runs on | Catches |
| --- | --- | --- |
| Uptime Kuma | office Mac mini (outside our servers) | a public host down, a wrong answer, a TLS certificate about to expire |
| Healthchecks.io (free plan) | Healthchecks' cloud | a job that silently stopped: the CRM scheduler, the n8n bot path, backups, the restore test, and the Mac mini itself |
| `scripts/ops/cf-proxy.py` | the Mac mini or your laptop | Cloudflare itself being the outage |

**Alerts never go through our own WhatsApp / respond.io path.** That path is one of the things
being watched; if it is down it cannot tell you so.

## Placeholders

Never write the real values into the repo, a PR or a comment.

| Placeholder | Meaning |
| --- | --- |
| `<CRM_ORIGIN>` | public IPv4 of the CRM box |
| `<N8N_HOST>` | public IPv4 of the n8n box |
| `<SS_HOST>` | public IPv4 of the shared-service box |
| `<PROD_COMPOSE>` | the CRM box's compose: `/opt/sorento-crm2/docker-compose.yml` |
| `<N8N_COMPOSE>` | the n8n box's compose file |
| `<HC_SCHEDULER_URL>` | ping URL of the Healthchecks check `sorento-scheduler` |
| `<HC_N8N_URL>` | ping URL of the Healthchecks check `sorento-n8n-bot-path` |
| `<HC_KUMA_URL>` | ping URL of the Healthchecks check `kuma-mac-mini` |
| `<HC_BACKUP_URL>` / `<HC_RESTORE_URL>` | ping URLs of the backup and restore-test checks |
| `<MCP_URL>` | the MCP URL n8n's bot workflow already uses (copy it from its MCP node) |

A Healthchecks ping URL looks like `https://hc-ping.com/<uuid>`. Treat it like a password: anyone
holding it can fake a heartbeat. The CRM code never logs it.

## Order

| Step | Time |
| --- | --- |
| 1. Healthchecks account, alert channel, checks | 15 min |
| 2. Uptime Kuma on the Mac mini, monitors, push to Healthchecks | 30 min |
| 3. CRM scheduler heartbeat (env var, next release) | 5 min |
| 4. n8n heartbeat workflow | 10 min |
| 5. Backup and restore-test pings (when those jobs exist) | 5 min each |
| 6. respond.io "webhook disabled" email alert | 10 min |
| 7. Cloudflare switch: token now, use only in an emergency | 10 min now |

---

## 1. Healthchecks.io

1. Sign up at https://healthchecks.io (free plan: 20 checks, enough here).
2. Add the alert channel you picked in "Alert routing" below (Integrations tab). Send its test
   notification. Check: it arrives on your phone.
3. Create these checks (Checks tab, "Add Check"). Set each check's time zone to
   `Asia/Kuala_Lumpur`. Tick your alert channel on every check.

| Check name | Schedule | Grace | Pinged by |
| --- | --- | --- | --- |
| `sorento-scheduler` | Simple, period **5 minutes** | **10 minutes** | the CRM worker's scheduler (step 3) |
| `sorento-n8n-bot-path` | Simple, period **5 minutes** | **10 minutes** | the n8n heartbeat workflow (step 4) |
| `kuma-mac-mini` | Simple, period **1 minute** | **3 minutes** | Uptime Kuma's push monitor (step 2) |
| `sorento-nightly-backup` | Cron, the backup job's own cron line | **1 hour** | the end of the nightly backup script (step 5) |
| `sorento-restore-test` | Cron, the restore test's own cron line | **1 day** | the end of the restore test (step 5) |

The scheduler period is fixed in code (`HC_SCHEDULER_PING_MINUTES = 5` in
`sorento_crm_backend/app/scheduler/task_scheduler.py`); keep the check at 5 minutes.

- Check: all five checks show as "new" (grey). They turn green on the first ping.
- Rollback: delete the checks. Nothing on our side depends on them.

## Alert routing (owner chooses)

Pick one primary and one backup. All of these work without our servers.

| Option | Healthchecks | Uptime Kuma | Notes |
| --- | --- | --- | --- |
| **Telegram bot** | built-in "Telegram" integration (talk to `@HealthchecksBot`) | built-in "Telegram" (make a bot with `@BotFather`, paste token + your chat id) | fast push on the phone, free, works abroad; recommended primary |
| **Email** | built-in, free | SMTP (e.g. a Gmail app password) | good backup; slow to notice at night |
| **Phone push** (ntfy or Pushover) | built-in | built-in | ntfy is free; Pushover is a one-off fee; can bypass Do Not Disturb |

Do not pick WhatsApp, respond.io, or anything that is delivered by n8n.

## 2. Uptime Kuma on the Mac mini

Install (once):

```bash
docker run -d --restart=always -p 127.0.0.1:3001:3001 -v uptime-kuma:/app/data --name uptime-kuma louislam/uptime-kuma:1
```

- Check: http://localhost:3001 on the Mac mini opens the setup page. Create the admin user.
- Rollback: `docker rm -f uptime-kuma` (the `uptime-kuma` volume keeps the settings).

Port 3001 is bound to localhost only; Kuma is not exposed to the LAN or the internet. This needs
Docker Desktop (or colima) running on the Mac mini and starting at login; `docker ps` shows
`uptime-kuma` after a reboot.

Add your alert channel under Settings > Notifications, tick "Default enabled", and press "Test".

### Monitors

Create each with "Add New Monitor". "Retries" is the alert threshold: Kuma alerts after that many
consecutive failures, so `Retries 2` at a 60 s interval alerts after about 3 minutes.
For every HTTP monitor also tick **"Certificate Expiry Notification"** (Settings > TLS expiry
days: `21, 14, 7`), which is the TLS-expiry monitor for that host.

| Name | Type | URL | Interval | Keyword / expected | Retries (threshold) |
| --- | --- | --- | --- | --- | --- |
| `CRM API health` | HTTP(s) - Keyword | `https://fe-sorento.foundryx.my/health` | 60 s | keyword `"status":"healthy"` (HTTP 200) | 2 |
| `CRM frontend` | HTTP(s) - Keyword | `https://fe-sorento.foundryx.my/` | 60 s | HTTP 200-299, keyword `<title` | 2 |
| `n8n healthz` | HTTP(s) - Keyword | `https://automate-sorento.foundryx.my/healthz` | 60 s | keyword `"status":"ok"` | 2 |
| `Shared service health` | HTTP(s) | `https://ss.foundryx.my/health` | 60 s | HTTP 200-299 | 2 |
| `Chat host` | HTTP(s) | `https://chat.foundryx.my/` | 120 s | HTTP 200-399 | 2 |
| `TLS fe-sorento` | (the "Certificate Expiry Notification" box on `CRM API health`) | | | alert at 21 / 14 / 7 days | |
| `TLS automate-sorento` | (the box on `n8n healthz`) | | | 21 / 14 / 7 days | |
| `TLS ss` | (the box on `Shared service health`) | | | 21 / 14 / 7 days | |
| `TLS chat` | (the box on `Chat host`) | | | 21 / 14 / 7 days | |

Why a keyword on `/health`: the CRM's `/health` answers `{"status":"unhealthy",...}` with 503
when the DB is unreachable. The keyword includes `"status":` so `unhealthy` does not match.

Before saving each one, run the same request from the Mac mini and compare:

```bash
curl -s https://fe-sorento.foundryx.my/health ; echo
curl -s https://automate-sorento.foundryx.my/healthz ; echo
curl -s -o /dev/null -w '%{http_code}\n' https://ss.foundryx.my/health
curl -s -o /dev/null -w '%{http_code}\n' https://chat.foundryx.my/
```

- Check: each answer matches the "Keyword / expected" column. If the shared service's `/health`
  prints a body with a status word, add it as a keyword. If `fe-sorento /health` does not print
  `"status":"healthy"`, stop and tell crew (the nginx route on the box may differ).
- Check: every monitor is green within 2 minutes.
- Rollback: pause or delete the monitor.

What the TLS boxes see: through Cloudflare, Kuma sees the **edge** certificate (Cloudflare renews
it). The origin's Let's Encrypt certificate is behind the proxy and is checked with the switch
pre-flight in step 7 instead.

### Watch the watcher

If the Mac mini, its power, its network or Kuma dies, nothing above alerts. So Kuma pushes to
Healthchecks every minute, and Healthchecks alerts when the pushes stop.

1. In Kuma: Add New Monitor > Type **HTTP(s)**, name `heartbeat to healthchecks`, URL
   `<HC_KUMA_URL>`, interval **60 s**, Retries 0. Do **not** attach any notification to it
   (it only exists to make the request).
2. Check: in Healthchecks, `kuma-mac-mini` turns green within 1 minute.
3. Test once: `docker stop uptime-kuma` on the Mac mini; within about 4 minutes (1 min period +
   3 min grace) the Healthchecks alert arrives. Then `docker start uptime-kuma`; the "up" message
   follows.
- Rollback: delete the Kuma monitor and the check.

Also turn off sleep on the Mac mini (System Settings > Energy: "Prevent automatic sleeping") and
tick "Start up automatically after a power failure".

## 3. CRM scheduler heartbeat

Code: `sorento_crm_backend/app/scheduler/task_scheduler.py` (`_healthchecks_scheduler_tick`, job
`healthchecks_scheduler_ping`) and `app/services/healthchecks_ping.py`. Every 5 minutes the
scheduler reads the DB once and pings `HC_SCHEDULER_URL`; a failed DB read pings `<url>/fail`,
which alerts at once. A stopped scheduler sends nothing, which alerts after period + grace. With
the variable unset, nothing happens. The ping has a 5 s timeout, runs on its own thread and never
raises, so a Healthchecks outage cannot affect the worker.

On the CRM box, add the variable to the **`worker`** service only (the one with
`ENABLE_SCHEDULER=true`; `worker_fast` and the backends run no scheduler):

```bash
sudo cp <PROD_COMPOSE> <PROD_COMPOSE>.bak-hc
sudo nano <PROD_COMPOSE>     # under services: worker: environment: add
#   - HC_SCHEDULER_URL=<HC_SCHEDULER_URL>
cd /opt/sorento-crm2 && sudo docker compose up -d worker
```

This needs the release that contains this change. Before that, the variable does nothing.

- Check: `sudo docker compose exec worker printenv HC_SCHEDULER_URL` prints the URL, and the
  Healthchecks check `sorento-scheduler` turns green within 5 minutes.
- Check: `sudo docker compose logs worker --since 10m | grep -i healthchecks` shows no
  `Healthchecks ping failed` line.
- Rollback: restore `<PROD_COMPOSE>.bak-hc`, `docker compose up -d worker`, and pause the check.

## 4. n8n heartbeat (the bot's real path)

On 11 Aug n8n's `/healthz` was green while the bot was dead for 18 hours. `/healthz` only proves
the n8n process answers. This workflow proves the path the bot really uses: every 5 minutes it
runs one DB read through the `sorento-crm-db` credential and one MCP tool call
(`crm_master_products_list`, `limit 1`), and pings Healthchecks only when both worked. A failure
anywhere means no ping, and Healthchecks alerts after period + grace (about 15 minutes).

The workflow: `documentation/runbooks/n8n-heartbeat-workflow.json`.

Its check node also reads the MCP answer's text: when the CRM backend is down the MCP server still
answers `isError: false` with `{"error": ...}` inside the text (seen locally on 14 Oct), so a
plain HTTP 200 check would pass while the bot is broken.

1. On the n8n box, give n8n the ping URL (n8n reads `$env` only with env access allowed):

   ```bash
   sudo cp <N8N_COMPOSE> <N8N_COMPOSE>.bak-hc
   sudo nano <N8N_COMPOSE>     # under the n8n service's environment: add
   #   - HC_N8N_URL=<HC_N8N_URL>
   #   - N8N_BLOCK_ENV_ACCESS_IN_NODE=false     (only if it is not already set)
   sudo docker compose up -d n8n
   ```

   If you would rather not allow env access, skip this and in step 4 paste `<HC_N8N_URL>` into
   the "Ping Healthchecks" node's URL field instead of `{{ $env.HC_N8N_URL }}`.
2. n8n > Workflows > Add workflow > the "..." menu > **Import from File** > pick
   `n8n-heartbeat-workflow.json` (download it from the repo).
3. Node **DB read**: in Credential, pick the existing `sorento-crm-db`.
4. Node **MCP call**: set URL to `<MCP_URL>` (open the bot workflow's MCP node and copy its
   endpoint exactly). If the bot's MCP node sends a header (for example an API key), add the
   same header here.
5. Press **Test workflow**. Check: all five nodes go green, and `sorento-n8n-bot-path` in
   Healthchecks shows a ping.
6. Toggle **Active** on, and save.

- Check: the next day, the n8n executions list shows no failed runs of this workflow (the
  workflow keeps only failed executions, so the list stays small).
- Test once: deactivate it; the Healthchecks alert arrives within about 15 minutes; activate it.
- Rollback: deactivate the workflow; restore `<N8N_COMPOSE>.bak-hc` and `up -d n8n`.

## 5. Backup and restore-test pings

When the nightly backup and the restore test exist (separate lanes), add this as the **last**
line of each script, so it only runs when everything before it succeeded (`set -e`):

```bash
curl -fsS -m 10 --retry 3 -o /dev/null "<HC_BACKUP_URL>"     # restore test: <HC_RESTORE_URL>
```

Set the check's cron line to the job's own cron line.

- Check: after the next run the check is green.
- Rollback: remove the line; pause the check.

## 6. respond.io "webhook disabled" email

respond.io switches off a webhook after repeated failures and emails the workspace owner. Nothing
on our side notices: the messages simply stop arriving. Turn that email into an alert.

1. In Gmail, open the last such email. Note its exact **From** address and two words of its
   subject (for example `webhook` and `disabled`).
2. Gmail search box: `from:(<that sender>) subject:(webhook disabled)`. Check: the search
   finds that email.
3. Pick a destination, then: search options (the sliders icon) > **Create filter** > tick
   **Forward it to** and **Never send it to Spam** and **Mark it as important** > Create filter.
   - **Option A (simplest):** forward to the email address your alert routing uses (if you chose
     email), or to a mailbox your phone notifies on.
   - **Option B (same channel as everything else):** in Healthchecks create a check
     `respondio-webhook-disabled` (Simple, period 365 days), open its "Email" usage tab, turn on
     "Filter by keywords" with failure keyword `disabled`, and forward the Gmail filter to the
     check's email address. A matching email marks it down and Healthchecks alerts through your
     channel. After fixing the webhook, reset the check by opening its ping URL once.
4. Gmail sends a confirmation code to the forwarding address first (Settings > Forwarding >
   "Add a forwarding address"). For option B, the code arrives as a ping on the check; open the
   check's log, open that ping and copy the code.

- Check: forward the last real email to the destination by hand; the alert arrives.
- Rollback: Gmail > Settings > Filters and Blocked Addresses > delete the filter.

---

## 7. Cloudflare switch: `scripts/ops/cf-proxy.py`

For when **Cloudflare itself** is the outage and the sites are down only because of it (18 Nov
2025: Cloudflare's proxy failed and its dashboard login was down too). The script talks to the
Cloudflare API only, so it works while the dashboard does not.

It changes only the address records (A / AAAA / CNAME) of four hosts in zone `foundryx.my`:
`fe-sorento`, `automate-sorento`, `chat`, `ss`. The zone holds other clients' hosts; the script
never reads or writes them, re-checks every record against that list before writing, and writes
nothing at all if the API ever returns a record outside it. It never prints the token or a
record's content (the origin IPs).

| Command | Does |
| --- | --- |
| `cf-proxy.py status` | shows each host's records: proxied or DNS only, and TTL |
| `cf-proxy.py off` | dry run: prints what `off --yes` would change |
| `cf-proxy.py off --yes` | proxied = false, TTL 60 s (traffic goes straight to the origins) |
| `cf-proxy.py on` / `on --yes` | dry run / proxied = true, TTL auto |

Exit code 0 = done, 1 = something failed or was refused (read the output), 2 = could not start
(token file, zone, network).

### Now, before any emergency (10 minutes)

1. Cloudflare dashboard > My Profile > API Tokens > Create Token > "Create Custom Token".
   Name `sorento-cf-proxy`. Permissions: **Zone / DNS / Edit** and **Zone / Zone / Read**.
   Zone Resources: **Include / Specific zone / foundryx.my**. Create, and copy the token.
2. On the Mac mini **and** your laptop (no Cloudflare dashboard needed later):

   ```bash
   mkdir -p ~/.config/sorento && chmod 700 ~/.config/sorento
   pbpaste > ~/.config/sorento/cf.token && chmod 600 ~/.config/sorento/cf.token   # token on the clipboard
   export CF_TOKEN_FILE=~/.config/sorento/cf.token
   python3 scripts/ops/cf-proxy.py status
   ```

   Check: four hosts listed, each `proxied ttl=auto`; exit code 0 (`echo $?`).
3. Dry run, to see the plan: `python3 scripts/ops/cf-proxy.py off`. Check: it says
   `Dry run: ... Nothing was written`, and `status` still shows `proxied`.
- Rollback: delete the token in Cloudflare (My Profile > API Tokens).

Do this check every 3 months too (calendar reminder): `status` still works, and the token has
not expired.

### When to use it

All of these, not one:

- Kuma shows every proxied host down at once (or Cloudflare error pages 5xx with a Cloudflare
  `cf-ray`), **and**
- https://www.cloudflarestatus.com reports a proxy / CDN incident, or the origin answers when
  asked directly (pre-flight below), **and**
- it has lasted 15 minutes or more, or business cannot wait.

Not for: an origin outage (the switch does not help, and it exposes the origin), or a DDoS (the
switch removes the protection exactly when it is needed).

### Pre-flight (2 minutes)

The switch only works if browsers can talk to the origins directly. From the Mac mini:

```bash
curl -s --resolve fe-sorento.foundryx.my:443:<CRM_ORIGIN> https://fe-sorento.foundryx.my/health ; echo
echo | openssl s_client -connect <CRM_ORIGIN>:443 -servername fe-sorento.foundryx.my 2>/dev/null \
  | openssl x509 -noout -issuer -enddate
```

- Check: the first prints `"status":"healthy"`; the second shows a Let's Encrypt issuer and an
  end date in the future. Repeat with `<N8N_HOST>` / `automate-sorento` and `<SS_HOST>` / `ss`.

What makes it work today (crew checked 15 Oct): the origin certificates are **Let's Encrypt**,
so browsers trust them, and the Hostinger firewall allows **443 from all**. If either changes,
the switch breaks:

- `prod-edge-lockdown.md` step 5.2 offers a Cloudflare **Origin CA** certificate. Browsers do not
  trust it, so with it in place `off` gives every user a certificate error. Keep Let's Encrypt
  (DNS-01 renewal) if you want to keep this switch.
- `prod-edge-lockdown.md` step 5.4 (80/443 from Cloudflare only). Once applied, first re-add
  rules 2 and 3 (80 and 443 from **Any**) in the Hostinger group, which is that step's own
  rollback, then run `off`.
- Authenticated Origin Pulls ("Later" in that runbook) makes nginx refuse anyone without
  Cloudflare's client certificate: turn `ssl_verify_client` off before `off`.

### Switch off

```bash
export CF_TOKEN_FILE=~/.config/sorento/cf.token
python3 scripts/ops/cf-proxy.py off          # read the plan
python3 scripts/ops/cf-proxy.py off --yes
python3 scripts/ops/cf-proxy.py status       # every record: DNS only  ttl=60
```

- Check: `dig +short fe-sorento.foundryx.my` returns the origin address within a few minutes
  (resolvers that cached the proxied answer can take up to 5 minutes), and the site loads.
- Rollback: `python3 scripts/ops/cf-proxy.py on --yes`.

If the Cloudflare **API** is down as well, the script cannot help (it exits 2); nothing was
changed.

### What breaks while it is off

- **No WAF, no DDoS protection, no rate limiting, no caching**: every request hits the boxes.
- **The origin IPs are public** in DNS for as long as it is off, and stay in passive-DNS
  history afterwards. After switching back, lockdown step 5.4 (Cloudflare-only firewall) is what
  stops direct traffic again.
- nginx sees real client IPs directly instead of via `CF-Connecting-IP`; IP-based logs and
  limits keep working.
- Anything configured in Cloudflare for these hosts (page rules, redirects, Access, cache rules)
  does nothing.

### Switch back

As soon as https://www.cloudflarestatus.com shows the incident resolved, and a test through
Cloudflare works:

```bash
python3 scripts/ops/cf-proxy.py on            # read the plan
python3 scripts/ops/cf-proxy.py on --yes
python3 scripts/ops/cf-proxy.py status        # every record: proxied  ttl=auto
```

- Check: `curl -sI https://fe-sorento.foundryx.my/ | grep -i cf-ray` prints a line (it may take
  up to 60 s, the TTL set by `off`).
- Rollback: `off --yes` again if Cloudflare fails again.

Afterwards: re-apply anything you relaxed in the pre-flight (Hostinger rules, origin pulls), and
consider whether the exposed origin addresses need changing.
