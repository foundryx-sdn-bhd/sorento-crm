# Runbook: n8n reaches the CRM over the tailnet (HA-N8N-PRIVATE-PATH)

Owner-run. Nothing here is automated, and nothing in the repo touches prod. Plan:
`documentation/plans/ops/PLAN-n8n-private-path-14oct.md`. Companion runbook:
`documentation/runbooks/prod-edge-lockdown.md` (its step numbers are written "edge step N" below).

## Why

On 26 Aug the Cloudflare to origin leg stalled. Bot reply p95 went from 10 s to 327 s while the
origin itself answered in 32 ms. n8n reaches the CRM three ways today, and only one of them is
private:

| What | Today | After this runbook |
| --- | --- | --- |
| CRM API (about 151 files in the repo cache, about 29 live workflows) | `https://<CRM_DOMAIN>/...`, through Cloudflare | same URL, same TLS certificate, but the name resolves to `<CRM_TS_IP>` inside the n8n containers |
| MCP (`MCP Client` / `MCP Client1` nodes) | `http://<CRM_ORIGIN>:8765/mcp`, plain http over the internet | `http://crm-private.internal:8765/mcp`, inside WireGuard |
| CRM DB (`sorento-crm-db` credential, BL-049) | `<CRM_ORIGIN>:6432` over the internet | `crm-private.internal:6432`, inside WireGuard |

The trick is Docker's `extra_hosts`: it writes lines into each n8n container's `/etc/hosts`. The
API keeps its real hostname, so SNI, the certificate check and nginx's `server_name` all still
match, and none of those workflows change. Only the MCP nodes, the DB credential and the few
HTTPS nodes on the raw address (step 4) change, because they name the raw address today.

Everyone else (browsers, Respond.io, AutoCount, Cloudflare) is untouched: public DNS does not
change.

## Placeholders

Never write the real values into the repo, a PR or a comment.

| Placeholder | Meaning |
| --- | --- |
| `<CRM_DOMAIN>` | the CRM's public hostname (proxied by Cloudflare) |
| `<CRM_ORIGIN>` | public IPv4 of the CRM box |
| `<N8N_HOST>` | public IPv4 of the n8n box |
| `<CRM_TS_IP>` | the CRM box's Tailscale address (`tailscale ip -4` on the CRM box) |
| `<N8N_TS_IP>` | the n8n box's Tailscale address (`tailscale ip -4` on the n8n box) |
| `<OWNER_TS_IP>` | your laptop's Tailscale address |
| `<N8N_MAIN>` | the main n8n container's name on the n8n box (step 0.4) |
| `<N8N_DIR>` | the directory holding the n8n box's `docker-compose.yml` (step 3 finds it) |
| `crm-private.internal` | a name that exists only in the n8n containers' `/etc/hosts`. Not a secret, and not a real DNS name: `.internal` is reserved for private use and never resolves publicly. |

## Order and time

| Step | What | Time | Breaks anything if wrong? |
| --- | --- | --- | --- |
| 0 | Pre-checks on the CRM box (nginx listen, certificate, pgbouncer binding), and an export of the live n8n workflows | 20 min | no, read-only |
| 1 | Tailscale policy, then both boxes join | 15 min | no, if the policy keeps `autogroup:member` as written |
| 2 | CRM box: let tailnet traffic in (nginx 8765 allowlist, ufw, pgbouncer) | 10 min | no, adds access only |
| 3 | n8n box: `extra_hosts` | 10 min | yes, n8n restarts; keep the rollback block open |
| 4 | n8n UI: MCP nodes, the `sorento-crm-db` credential, `family-fetch`, `post-inject` | 15 min | yes, per node; each has its own rollback |
| 5 | Checks: timing curl, one test-number chatbot turn | 10 min | no |
| 6 | After 48 h with no rollback: delete the public 6432 / 8765 rules in Hostinger | 5 min | yes; rollback is re-adding two rules |

Do steps 3 to 5 outside office hours. n8n restarts once in step 3.

## Step 0. Pre-checks (read-only on prod)

The prod nginx config is hand-edited on the box and not in git (only the upstreams file is
written by `scripts/blue_green_deploy.sh:84-94`, into `/etc/nginx/conf.d/sorento_upstreams.conf`).
So these facts can only be read on the box.

### 0.1 Does the `<CRM_DOMAIN>` vhost listen on every interface?

```bash
sudo nginx -T 2>/dev/null | grep -nE '^\s*(server_name|listen|ssl_certificate)\b'
```

- Check: the server block whose `server_name` is `<CRM_DOMAIN>` has `listen 443 ssl` (or
  `listen [::]:443 ssl` plus `listen 443 ssl`) with **no address in front of the port**. A bare
  port binds every interface, `tailscale0` included, so nothing needs changing.
- If it reads `listen <CRM_ORIGIN>:443 ssl`, change it to `listen 443 ssl` (step 2.1). Do NOT add
  `listen <CRM_TS_IP>:443`: after a reboot nginx can start before tailscaled has that address, and
  nginx then refuses to start at all, taking the public site down with it.
- Check the same for the `listen 8765` block (MCP). It should also have no address in front.
- Rollback: nothing to roll back (read-only).

### 0.1b Anything in nginx keyed on n8n's public address?

```bash
sudo nginx -T 2>/dev/null | grep -nE '<N8N_HOST>|limit_req|geo '
```

- Check: any `allow`, `geo` or rate-limit exemption that names `<N8N_HOST>` needs a twin line with
  `<N8N_TS_IP>` in step 2.1; after the switch n8n arrives from its tailnet address.
- Never add `100.64.0.0/10` (the tailnet range) to the backend's `TRUSTED_PROXIES`: nginx already
  passes the n8n box's real tailnet address on, and the backend's resolver
  (`sorento_crm_backend/app/services/client_ip.py`) handles it as it is.
- Rollback: nothing to roll back (read-only).

### 0.2 Is the certificate publicly trusted?

n8n will check the certificate itself now, with nobody in between, so it must be one that a
normal client trusts.

Already known (crew, 15 Oct): a direct TLS handshake to the origin, around Cloudflare, showed
`<CRM_DOMAIN>` and the two other Cloudflare-proxied hosts on the CRM box all serving a
browser-trusted Let's Encrypt certificate (not a Cloudflare Origin CA one), expiring 14 Dec,
5 Dec and 24 Nov. So this path is feasible; the check below confirms it on the day you run it:

```bash
# what nginx actually serves for <CRM_DOMAIN>, read off the wire
echo | openssl s_client -connect 127.0.0.1:443 -servername <CRM_DOMAIN> 2>/dev/null \
  | openssl x509 -noout -issuer -subject -enddate -ext subjectAltName
```

- Check: the issuer is Let's Encrypt (`O = Let's Encrypt`), the SAN list contains `<CRM_DOMAIN>`,
  and `notAfter` is in the future. Then the same certificate serves the tailnet path with no
  change.
- If the issuer is **Cloudflare Origin CA**: stop here and tell crew. That certificate is only
  trusted by Cloudflare, so n8n would reject it. The fix is a Let's Encrypt certificate with a
  DNS-01 challenge, which edge step 5.2 already offers; do not pick the Origin CA option there
  while n8n uses this path.
- Check: `sudo certbot renew --dry-run` passes. The tailnet path uses the same file, so renewal
  covers it automatically.
- Let's Encrypt renewal must keep working for as long as n8n uses this path. The tailnet vhost
  serves the same certificate files, so a renewal that stops (or a switch to Origin CA) breaks
  n8n's calls the day the old certificate expires, not before. After each renewal, rerun the
  `s_client` check above (it must show the new `notAfter`) and step 5.1 (it must still print
  `status=401` or `403` with no certificate error). If certbot was set up with `certonly`, make
  sure a deploy hook reloads nginx (`--deploy-hook 'systemctl reload nginx'`), or nginx keeps
  serving the old file.
- Rollback: nothing to roll back (read-only).
- Later hardening warning: edge runbook "Later" plans Authenticated Origin Pulls
  (`ssl_verify_client on`). n8n sends no client certificate, so that would reject every n8n call
  on this path. When that lands, use `ssl_verify_client optional;` and refuse only requests that
  are neither verified nor from the tailnet, for example inside the `<CRM_DOMAIN>` server block:
  `set $aop_block 0;`, `if ($ssl_client_verify != SUCCESS) { set $aop_block 1; }`,
  `if ($remote_addr = <N8N_TS_IP>) { set $aop_block 0; }`, `if ($aop_block) { return 403; }`.

### 0.3 How is pgbouncer published?

```bash
docker ps --format '{{.Names}}\t{{.Ports}}' | grep 6432
```

- `0.0.0.0:6432->6432/tcp`: it already answers on `tailscale0`. Step 2.3 has nothing to do.
- `127.0.0.1:6432->6432/tcp` (after edge step 4): step 2.3 forwards it onto the tailnet.

- Rollback: nothing to roll back.

### 0.4 Export the live n8n workflows and re-check what names the origin

Step 4's list of nodes comes from crew's grep of the `sorento_crm_n8n` repo on 15 Oct, and that
repo is a cache at least five weeks old. Live n8n is the authority, so export it now, before
anything is edited, and re-run the grep on the export. The export is also the rollback copy for
step 4.

On the n8n box (`<N8N_MAIN>` is the main n8n container, not a worker; list them with
`docker ps --format '{{.Names}}\t{{.Image}}' | grep -i n8n`):

```bash
umask 077                                            # new files private; the chmod below covers docker cp's copied modes
D=~/n8n-export-$(date +%Y%m%d-%H%M)                  # set once, reused below
docker exec -u node <N8N_MAIN> sh -c 'rm -rf /tmp/wf && mkdir -p /tmp/wf && n8n export:workflow --all --separate --output=/tmp/wf/'
docker cp <N8N_MAIN>:/tmp/wf "$D"
docker exec -u node <N8N_MAIN> rm -rf /tmp/wf
chmod -R go-rwx "$D"
cd "$D" && ls | wc -l       # one file per workflow
umask 022                    # back to the default for the rest of the session
```

The export holds no credential secrets, but nodes may carry literal header values and pinned
data (customer messages, phone numbers), and it contains `<CRM_ORIGIN>`: treat the folder as
secret. Do not run `export:credentials`. Keep the folder only on the n8n box (or in your password
manager's file store), never on a laptop or in any git repo. Delete it once step 6 has held for
48 hours with no rollback: `rm -rf ~/n8n-export-<date>-<time>`.

Re-check, from inside that folder (each line prints the workflow name, its `active` flag and the
node name):

```bash
read -r CRM_ORIGIN && export CRM_ORIGIN    # paste <CRM_ORIGIN>, Enter (keeps it out of shell history)
python3 -I - <<'PY'
import glob, json, os
ORIGIN = os.environ["CRM_ORIGIN"]
assert not ORIGIN.startswith("<"), "put the real origin address in CRM_ORIGIN"
files = sorted(glob.glob("*.json")); assert files, "run this inside ~/n8n-export-<date>-<time>"
for f in files:
    wf = json.load(open(f))
    for n in wf.get("nodes", []):
        body = json.dumps(n)
        hits = []
        if ORIGIN in body: hits.append("names-origin")
        if "mcpClient" in n.get("type", ""): hits.append("mcp-client")
        if '"sorento-crm-db"' in body: hits.append("sorento-crm-db")
        if '"allowUnauthorizedCerts": true' in body: hits.append("ignore-ssl")
        if ":6432" in body or ":5432" in body: hits.append("db-port")
        if hits:
            print(f"{wf.get('name')!r:45} active={wf.get('active')!s:5} node={n.get('name')!r:25} {','.join(hits)}")
PY
```

- Check: the output matches the list in step 4. Crew's 15 Oct cache grep found:

  | Workflow | Node | Finding |
  | --- | --- | --- |
  | `sub-get-results` (live) | `MCP Client` (`mcpClientTool`) | `http://<CRM_ORIGIN>:8765/mcp` |
  | `sub-get-results` (live) | `MCP Client1` (`mcpClient`) | `http://<CRM_ORIGIN>:8765/mcp` |
  | the TEST and CS-BUILD forks of `sub-get-results` | the same two nodes | same raw address |
  | `live-respond-close-convo`, `live-respond-send-user`, `sub-get-rag-live`, `sub-sendmsg` | their Postgres nodes | credential `sorento-crm-db` |
  | `sub-miss-suggest-live` | `family-fetch` | `https://<CRM_ORIGIN>/api/v1/master-data/products`, Ignore SSL Issues ON |
  | `live-spine-sorento-consume-main` (old spine, replaced by `spine-rs-1a` on 1 Sep) | `family-fetch` | same, probably inactive |
  | `live-poller` | `post-inject` | URL is an expression; see step 4.4 |

  No workflow names port 6432 or 5432 directly (the DB is reached only through the credential),
  and about 29 workflows call `<CRM_DOMAIN>`, which step 3 moves with no edit.
- Any extra `names-origin`, `mcp-client`, `ignore-ssl`, `db-port` or `sorento-crm-db` line that
  the table does not have: add it to your step 4 list (same edit as its row), and tell crew on the PR
  (workflow and node name only, never the resolved URL).
  Test copies on credential `n8n_test-db` are not part of this runbook.
- Rollback: nothing to roll back. Prefer the field rollbacks in 4.1 to 4.4. As a last resort only,
  undo a step 4 edit from this copy, one file at a time, never the folder:
  `docker cp <file> <N8N_MAIN>:/tmp/ && docker exec -u node <N8N_MAIN> n8n import:workflow --input=/tmp/<file>`.
  The import replaces the whole workflow, so it also reverts any other edit made to it since the
  export, and it may leave the workflow inactive: re-activate it in the UI only if the step 0.4
  check printed `active=True` for it. After the import, re-apply the 4.3 / 4.4 URL and Ignore SSL
  Issues OFF (the copy predates them).

## Step 1. Tailscale on both boxes, behind a policy

Traffic inside the tailnet never passes the Hostinger firewall, and docker-published ports skip
ufw (edge step 4). So **the tailnet policy is the only thing that decides what the n8n box can
reach on the CRM box** (pgbouncer, SSH, Redis if it is published, MCP with its write tools), and
what it can reach elsewhere on your tailnet (your laptop). It is set first, before either box
joins, and it is not optional.

### 1.1 The policy

Tailscale admin console, **Access controls** (<https://login.tailscale.com/admin/acls>). Click
**Download** first to keep a copy, or note that the console's history can restore it.

1. Add the two tags (merge into an existing `tagOwners` block if there is one):

   ```jsonc
   "tagOwners": {
     "tag:crm": ["autogroup:admin"],
     "tag:n8n": ["autogroup:admin"],
   },
   ```

2. Find the allow-all rule. In a new tailnet it is one of these:

   ```jsonc
   {"action": "accept", "src": ["*"], "dst": ["*:*"]}      // inside "acls"
   {"src": ["*"], "dst": ["*"], "ip": ["*"]}               // inside "grants"
   ```

   Change its `"src": ["*"]` to `"src": ["autogroup:member"]`. Your own devices (laptop, phone,
   any untagged machine you logged in) keep reaching everything exactly as before; only TAGGED
   machines lose the blanket access. If there is no allow-all rule, you already keep a curated
   policy: leave your rules as they are.

3. Add the one rule the n8n box gets (create `"grants"` if the file has only `"acls"`; both may
   live in one file):

   ```jsonc
   "grants": [
     // n8n box -> CRM box: the API, MCP and pgbouncer only. Nothing else, and nothing to tag:n8n.
     {"src": ["tag:n8n"], "dst": ["tag:crm"], "ip": ["tcp:443", "tcp:8765", "tcp:6432"]},
   ],
   ```

4. **Save**. The console validates the file and refuses a broken one.

- Check: the save succeeds, and your laptop still makes a real connection it made before, for
  example `ssh` to a tailnet machine you normally reach that way, or `nc -z -w 5 <that machine's
  100.x address> <a port it serves>`. (`tailscale ping` is not a test here: the policy does not
  filter it.)
- Rollback: **Access controls**, the history (clock) icon, restore the previous version.

### 1.2 Join the boxes

On the CRM box:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --accept-dns=false --ssh=false --advertise-tags=tag:crm
```

On the n8n box, the same with `--advertise-tags=tag:n8n`.

- `--accept-dns=false` keeps Tailscale away from `/etc/resolv.conf`, so Docker's DNS on both boxes
  stays exactly as it is.
- The command prints a login URL; open it and approve the machine.
- A tagged machine's key does not expire, so it cannot drop off silently after 180 days.
- Never run `tailscale funnel` on either box. Funnel publishes a port to the whole internet, and
  MCP on 8765 has no login of its own.

- Check, on each box: `tailscale ip -4` prints a `100.x.y.z` address. Write them down as
  `<CRM_TS_IP>` and `<N8N_TS_IP>` (in your password manager, not in the repo).
- Check: the admin console's **Machines** page shows the two boxes with `tag:crm` and `tag:n8n`.
- Check, on the n8n box: `tailscale ping -c 5 <CRM_TS_IP>`. The last line should say
  `via <CRM_ORIGIN>:<port>` (direct; the port is often 41641 but may differ), not `via DERP(...)`. A DERP relay cannot read the traffic
  (it stays WireGuard-encrypted end to end), but it adds a hop through Tailscale's servers, and
  latency and availability on that hop are what this runbook is removing. If it says DERP, add
  ONE Hostinger rule on the CRM box, scoped to the n8n box only: hPanel, **VPS**, the CRM server,
  **Security**, **Firewall**, the active group, **Edit**, **Add rule**: Action **Accept**,
  Protocol **UDP**, Port **41641**, Source **Custom** `<N8N_HOST>`. Wait two minutes and ping
  again.
- Check: `tailscale funnel status` on each box prints that nothing is funneled.
- Rollback for a retry: `sudo tailscale down` on either box. Rollback for good: `sudo tailscale logout` on both boxes, then **Machines**, each box's `...` menu, **Remove**; also remove the UDP
  rule if you added it. (`down` alone leaves a valid key that can rejoin at any time.)

## Step 2. CRM box: let tailnet traffic in

### 2.1 nginx

Save the config first: `sudo nginx -T > ~/nginx-T-before-tailnet.txt 2>&1`.

1. Only if step 0.1 found an address in front of 443: change that line to `listen 443 ssl;`
   (keep `http2` or any other flag it had).
2. In the `listen 8765` server block, the edge step 2 allowlist gets one more line, above
   `deny all;`:

   ```nginx
   allow <N8N_TS_IP>;      # n8n over the tailnet (n8n-private-path.md)
   ```

3. `sudo nginx -t && sudo nginx -s reload`

- Check, from the n8n box host (not the container yet):
  `curl -s -o /dev/null -w '%{http_code}\n' http://<CRM_TS_IP>:8765/mcp` prints `200`, `405` or
  `406` (the same three the release check accepts, `.github/workflows/deploy.yml:2117-2121`).
- Check, from the n8n box host:
  `curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' --resolve <CRM_DOMAIN>:443:<CRM_TS_IP> https://<CRM_DOMAIN>/`
  prints a `2xx` or `3xx` code with no certificate error.
- Rollback: delete the `allow <N8N_TS_IP>;` line, restore the `listen` line from
  `~/nginx-T-before-tailnet.txt` if you changed it, then `sudo nginx -t && sudo nginx -s reload`.

### 2.2 ufw: normally nothing to do

Tailscale puts its own `ts-input` chain ahead of ufw and accepts `tailscale0` there, and
docker-published ports (pgbouncer) skip ufw anyway. So ufw is not what gates this path; the step 1
policy is. Only if step 2.1's curls time out AND `sudo ufw status | head -1` prints
`Status: active`, allow the host nginx ports:

```bash
sudo ufw allow in on tailscale0 from <N8N_TS_IP> to any port 443,8765 proto tcp
```

- Check: the two curls in 2.1 pass.
- Rollback: `sudo ufw delete allow in on tailscale0 from <N8N_TS_IP> to any port 443,8765 proto tcp`.

### 2.3 pgbouncer (only if step 0.3 showed `127.0.0.1:6432`)

Forward the loopback port onto the tailnet. Do not bind pgbouncer to `<CRM_TS_IP>` in the
compose: docker can start before tailscaled after a reboot, and pgbouncer would then fail to
start, taking the backend with it (edge step 4 says the same).

```bash
tailscale serve --help | grep -A2 -- '--tcp'      # confirm the flag on your version first
sudo tailscale serve --bg --tcp 6432 tcp://127.0.0.1:6432
```

- Check, from the n8n box host: `nc -z -w 5 <CRM_TS_IP> 6432 && echo ok-6432`.
- Check: `tailscale serve status` lists only TCP 6432, and `tailscale funnel status` lists
  nothing.
- Rollback: `sudo tailscale serve --tcp 6432 off` (or `sudo tailscale serve reset`).

### 2.4 The policy holds

From the n8n box host, after 2.1 to 2.3:

```bash
for p in 443 8765 6432; do nc -z -w 5 <CRM_TS_IP> $p && echo "open   $p (expected)" || echo "CLOSED $p <- FAIL"; done
for p in 22 6379 5432 8000 3000; do nc -z -w 5 <CRM_TS_IP> $p && echo "OPEN   $p <- FAIL" || echo "closed $p (expected)"; done
```

- Check: the first three open, the rest closed. An `OPEN ... FAIL` line means the step 1.1 policy
  is not in force (most often: the allow-all rule still has `"src": ["*"]`). Fix the policy
  before step 3.
- Rollback: nothing to roll back.

## Step 3. n8n box: `extra_hosts`

Find the compose project that runs n8n, and every n8n service in it (queue mode has a main, one or
more workers and maybe a webhook service; **every one of them** needs the lines, because any of
them can run a workflow):

```bash
docker ps --format '{{.Names}}\t{{.Image}}' | grep -i n8n
docker inspect <one n8n container> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
```

The second command prints `<N8N_DIR>`. Then:

```bash
cd <N8N_DIR>
cp docker-compose.yml docker-compose.yml.bak-$(date +%Y%m%d)
cp .env .env.bak-$(date +%Y%m%d) 2>/dev/null || true
grep -q '^CRM_PRIVATE_IP=' .env 2>/dev/null || printf '\nCRM_PRIVATE_IP=%s\n' '<CRM_TS_IP>' >> .env
grep -c '^CRM_PRIVATE_IP=' .env      # must print 1
```

In `docker-compose.yml`, add this block to **each** n8n service (`n8n`, `n8n-worker`, and so on):

```yaml
    extra_hosts:
      - "<CRM_DOMAIN>:${CRM_PRIVATE_IP:?CRM_PRIVATE_IP unset}"
      - "crm-private.internal:${CRM_PRIVATE_IP:?CRM_PRIVATE_IP unset}"
```

If a service already has `extra_hosts`, add the two lines to it instead of a second block. Then
recreate (a plain `restart` does not rewrite `/etc/hosts`):

```bash
docker compose config | grep -A3 extra_hosts       # both lines, with the 100.x address filled in
docker compose up -d                                # recreates only the services whose config changed
```

- Check, for each n8n container: `docker exec <container> getent hosts <CRM_DOMAIN>` prints
  `<CRM_TS_IP>`, and `docker exec <container> getent hosts crm-private.internal` prints the same.
- Check: the n8n editor opens, and the executions list shows new runs succeeding (the API path is
  now live for every workflow).
- Rollback: `cp docker-compose.yml.bak-<date> docker-compose.yml && docker compose up -d`. Every
  workflow is back on Cloudflare within a minute. The `CRM_PRIVATE_IP` line in `.env` is harmless
  without the compose lines; leave it or delete it. Once step 4 is done, roll back step 4 FIRST:
  without `extra_hosts`, `crm-private.internal` stops resolving and the MCP nodes and the DB
  credential fail.

## Step 4. n8n UI: the nodes and the credential that name the origin

These name `<CRM_ORIGIN>`, so `extra_hosts` alone does not move them. The list is crew's 15 Oct
grep, confirmed (or extended) by your step 0.4 export. Edit in the live n8n editor; save, and
publish if your n8n version separates the two.

Do not press **Execute workflow** on any of these to test them: several send real WhatsApp
messages. Check with step 5's test-number turn and the **Executions** list instead.

### 4.1 MCP: `MCP Client` and `MCP Client1` in `sub-get-results`

Open workflow `sub-get-results`. In node `MCP Client` (MCP Client Tool) and in node
`MCP Client1` (MCP Client), set the endpoint field (**Endpoint**, or **SSE Endpoint** on older
versions) from `http://<CRM_ORIGIN>:8765/mcp` to

```
http://crm-private.internal:8765/mcp
```

Make the same edit in the TEST and CS-BUILD forks of `sub-get-results`. They are not on the live
path, but after step 6 a fork still on the raw address stops working.

- Check: step 5.2's chatbot turn; in **Executions**, that turn's `sub-get-results` run is green
  and `MCP Client1` shows output.
- Rollback: set both endpoints back to `http://<CRM_ORIGIN>:8765/mcp` and save. Only if that is
  not possible, re-import the step 0.4 copy (step 0.4 rollback); the import comes back INACTIVE
  and drops any edit since the export, so re-activate `sub-get-results` in the UI only if step
  0.4 printed `active=True` for it, and confirm its next execution is green.

### 4.2 The `sorento-crm-db` credential

One edit moves all four live workflows that use it: `live-respond-close-convo`,
`live-respond-send-user`, `sub-get-rag-live` and `sub-sendmsg`.

n8n, **Credentials** (left sidebar, or **Overview**, **Credentials**), open `sorento-crm-db`.
Change **Host** from `<CRM_ORIGIN>` to `crm-private.internal`. Leave the port at `6432` and
everything else as it is. Click **Save**; n8n tests the connection on save.

- Check: the save shows "Connection tested successfully".
- Check: in **Executions**, the next natural run of each of the four workflows is green (a
  test-number turn reaches `sub-sendmsg` and `live-respond-send-user`; the human-intervened
  sweeper's next tick reaches `live-respond-close-convo`).
- Rollback: set **Host** back to `<CRM_ORIGIN>` and save.

### 4.3 `family-fetch`: the hostname, with TLS verification ON

Workflow `sub-miss-suggest-live`, node `family-fetch` (HTTP Request). The origin certificate is a
browser-trusted Let's Encrypt one (step 0.2), so this node no longer needs to skip the check:

1. **URL**: change `https://<CRM_ORIGIN>/api/v1/master-data/products` to
   `https://<CRM_DOMAIN>/api/v1/master-data/products` (keep the query parameters as they are).
2. **Options**, **Ignore SSL Issues**: OFF (or remove the option).
3. Leave the header credential as it is. Save.

After step 3 the hostname resolves to the tailnet inside the container, so this call no longer
goes through Cloudflare either.

`live-spine-sorento-consume-main` has a second `family-fetch`. That spine was replaced by
`spine-rs-1a` on 1 Sep. If step 0.4 shows it `active=False`, leave it and note it; if it is
`active=True`, make the same edit there.

- Check: ask the test number for a product the bot does not stock under that exact name (so the
  miss-suggest lane runs). In **Executions**, `sub-miss-suggest-live` is green and `family-fetch`
  shows output, and step 5.2's nginx `tail` shows the request arriving from `<N8N_TS_IP>` (if it
  is missing, step 3's `extra_hosts` line is missing for that container and the call went through
  Cloudflare). A certificate error here means the origin certificate fails step 0.2 (issuer, SAN or
  expiry): remove only the `<CRM_DOMAIN>` line from `extra_hosts` and `docker compose up -d` (as
  in step 5.1), so the call goes through Cloudflare; then rerun step 0.2's `s_client` check and
  tell crew.
- Rollback: keep `https://<CRM_DOMAIN>` and Ignore SSL Issues OFF; rolling back step 3 sends the
  call through Cloudflare, which needs no URL change. Never turn Ignore SSL Issues back on.

### 4.4 `post-inject` in `live-poller`

Its URL is an expression, so the grep cannot tell where it points. Open `live-poller`, open the
latest green execution, click `post-inject` and read the URL field's resolved value.

- `https://<CRM_DOMAIN>/...`: nothing to do; step 3 already moved it.
- `<CRM_ORIGIN>` anywhere in it: change the part of the expression that supplies the host to
  `<CRM_DOMAIN>` (HTTPS) and turn **Ignore SSL Issues** OFF if it is on, as in 4.3. Tell crew on
  the PR (workflow and node name only, not the resolved URL).
- Anything else (another service): out of scope; leave it.

- Check: the next `live-poller` execution is green.
- Rollback: if the host was `<CRM_DOMAIN>` already, nothing to undo. If you changed it, keep
  `<CRM_DOMAIN>` with Ignore SSL Issues OFF, as in 4.3; never restore a raw-origin host.

## Step 5. Checks

### 5.1 The API goes over the tailnet, and how fast

The n8n image may not ship `curl`, so this uses the Node.js that every n8n image has. Run it in
one n8n container:

```bash
docker exec -i <n8n container> node - <<'EOF'
const https = require('https');
const t0 = Date.now(); let tConn, tTls, peer;
const req = https.get('https://<CRM_DOMAIN>/api/v1/master-data/products?limit=1', res => {
  res.resume();
  res.on('end', () => console.log(
    `status=${res.statusCode} peer=${peer} cf-ray=${res.headers['cf-ray'] || 'none'}`,
    `connect=${tConn - t0}ms tls=${tTls - t0}ms total=${Date.now() - t0}ms`));
});
req.on('socket', s => {
  s.on('connect', () => { tConn = Date.now(); peer = s.remoteAddress; });
  s.on('secureConnect', () => { tTls = Date.now(); });
});
req.on('error', e => { console.error('ERROR', e.message); process.exit(1); });
EOF
```

- Check: `peer=<CRM_TS_IP>` and `cf-ray=none` (Cloudflare was not in the path).
- Check: `status=401` (or `403`) is expected with no key, and proves the backend answered. `total` should be
  in the tens of milliseconds; a few hundred on the first try is the TLS handshake plus a cold
  connection, so run it three times.
- Check: an error mentioning a certificate means step 0.2 is not satisfied. Remove only the
  `<CRM_DOMAIN>` line from `extra_hosts` (keep `crm-private.internal`, which MCP and the DB use)
  and `docker compose up -d`; the API is back on Cloudflare.
- For comparison, the Cloudflare path from the n8n HOST (no `extra_hosts` there):
  `curl -s -o /dev/null -w '%{remote_ip} %{time_connect}s %{time_appconnect}s %{time_total}s\n' 'https://<CRM_DOMAIN>/api/v1/master-data/products?limit=1'`.
- If the same request works from the n8n HOST with `--resolve <CRM_DOMAIN>:443:<CRM_TS_IP>` but
  hangs at TLS from the container, it is the tailnet's smaller MTU (1280) against Docker's 1500.
  Clamp it on the n8n box, then retry:
  `sudo iptables -t mangle -A FORWARD -o tailscale0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu`
  (rollback: the same command with `-D` instead of `-A`; make it permanent with
  `iptables-persistent` only once it is proven needed).

MCP and the DB, from the same container:

```bash
docker exec -i <n8n container> node -e "
const net=require('net');
for (const p of [8765, 6432]) { const t0=Date.now();
  const s=net.connect(p,'crm-private.internal',()=>{console.log(p,'ok',Date.now()-t0+'ms',s.remoteAddress);s.end();});
  s.on('error',e=>console.log(p,'ERROR',e.message)); }"
```

- Check: both lines print `ok` with `<CRM_TS_IP>`.
- Rollback: nothing to roll back (read-only).

### 5.2 One chatbot turn on the test number

1. On the CRM box, watch for n8n's calls: `sudo tail -f /var/log/nginx/access.log | grep <N8N_TS_IP>`
   (your access log path may differ; `sudo nginx -T | grep access_log` shows it).
2. From the WhatsApp test number, ask the bot one product question (for example a stock or price
   question you have asked it before).

- Check: the bot replies in about the time it did before the change (single-digit seconds), and
  the reply is a real answer, not the error fallback.
- Check: the `tail` shows `/api/v1/...` lines from `<N8N_TS_IP>` during the turn.
- Check: in n8n, the turn's executions are green, including the MCP node.
- Rollback: step 4's rollback (4.4, 4.3, 4.2, 4.1), then step 3's.

## Step 6. Delete the public 6432 and 8765 rules (after 48 hours with no rollback)

Once n8n has run on the tailnet for 48 hours, nothing outside the box needs the public 6432 and
8765 rules any more.

First confirm nothing still uses them, on the CRM box (same method as edge step 0; run it a few
times over ten minutes):

```bash
for p in 6432 8765; do echo "== $p"
  sudo conntrack -L -p tcp --dport $p 2>/dev/null \
    | awk '{for(i=1;i<=NF;i++) if($i ~ /^src=/){print $i; break}}' | sort | uniq -c
done
```

- Check: no `src=<N8N_HOST>` line. (`100.x` lines are the tailnet; `127.0.0.1` and `172.x` are
  the box itself.) If `<N8N_HOST>` still shows, a node or credential still names `<CRM_ORIGIN>`;
  rerun step 0.4 on a fresh export before going on.

Then, in hPanel:

1. **VPS**, pick the CRM server, **Security**, **Firewall**.
2. On the active group (edge step 1 named it `crm-edge`), open the menu, **Edit**.
3. Delete the rule **TCP 6432, source `<N8N_HOST>`** (edge rule 4): its row's delete (bin) icon,
   then confirm.
4. Delete the rule **TCP 8765, source `<N8N_HOST>`** (edge rule 5) the same way.
5. Keep edge rule 6 (8765 from `<CRM_ORIGIN>`) unless edge step 0 showed the CRM calls MCP by a
   container name or `localhost`; it is about the box calling itself, not n8n.
6. If the page shows the group out of sync, click **Synchronize**. Wait two minutes.

- Check, from the n8n box HOST: `nc -z -w 5 <CRM_ORIGIN> 6432; echo $?` prints `1` (timed out),
  and `nc -z -w 5 <CRM_TS_IP> 6432 && echo ok` prints `ok`.
- Check: edge step 7's outside checks still show 6432 and 8765 closed.
- Check: one more chatbot turn on the test number (step 5.2).
- Rollback: **Add rule** twice: Accept, TCP, port 6432, source Custom `<N8N_HOST>`; and the same
  for 8765.

Then tidy the two leftovers that named the n8n box's public address:

- In the nginx `listen 8765` block, delete `allow <N8N_HOST>;` (edge step 2), then
  `sudo nginx -t && sudo nginx -s reload`. Check: from the n8n box host,
  `curl -s -o /dev/null -w '%{http_code}\n' http://<CRM_TS_IP>:8765/mcp` still prints `200`, `405`
  or `406` (a `403` means the tailnet line was removed instead).
  Rollback: put the line back and reload.
- If edge step 5.4 added `sudo ufw allow proto tcp from <N8N_HOST> to any port 8765,6432`, delete
  it: `sudo ufw delete allow proto tcp from <N8N_HOST> to any port 8765,6432`. Rollback: the
  same command without `delete`.

**Rollback order once step 6 is done.** Step 3 and steps 4.1 / 4.2 roll back to `<CRM_ORIGIN>`
(4.3 and 4.4 never do), which only works while the public rules exist. So after step 6, a
rollback of step 3, 4.1 or 4.2 starts with step 6's rollback. Prefer fixing the tailnet
(`tailscale status`, `tailscale ping`) over reopening 6432 / 8765: reopening brings back the clear-text, login-less MCP port on the internet.

Also, once `family-fetch` (step 4.3) no longer names `<CRM_ORIGIN>`, edge step 5.4 does not need its
"443 from `<N8N_HOST>`" rule either.

## Failover (Tier 1 standby box)

Everything n8n knows about where the CRM lives is ONE line in `<N8N_DIR>/.env`:

```
CRM_PRIVATE_IP=<CRM_TS_IP>
```

When a standby CRM box exists, join it to the tailnet with `--advertise-tags=tag:crm` (step 1.2;
the tag puts it under the same policy, so the policy needs no edit) and give it the same nginx,
certificate and pgbouncer setup (steps 0 and 2). To fail n8n over:

```bash
cd <N8N_DIR>
sed -i 's/^CRM_PRIVATE_IP=.*/CRM_PRIVATE_IP=<STANDBY_TS_IP>/' .env
docker compose up -d          # recreate so /etc/hosts is rewritten
```

- Check: step 5.1 shows `peer=<STANDBY_TS_IP>`.
- Rollback: the same two commands with `<CRM_TS_IP>`.

Both the API name and `crm-private.internal` follow that one value, so MCP and the DB move with
it. (Public traffic is a separate switch, the Cloudflare DNS record.)

## Alternative: plain WireGuard instead of Tailscale

Use this only if Tailscale is not wanted. It has no relay fallback and no admin console, and you
own the keys.

On each box:

```bash
sudo apt install -y wireguard
sudo sh -c 'umask 077; cd /etc/wireguard && wg genkey | tee privatekey | wg pubkey > publickey'
```

The addresses below are in `100.64.0.0/10` on purpose. A `10.x` address would fall inside the
backend's trusted-proxy list (`sorento_crm_backend/app/services/client_ip.py`), and n8n's own
`X-Forwarded-For` would then be believed. Do not use this range if Tailscale also runs on the box.

CRM box `/etc/wireguard/wg0.conf`:

```ini
[Interface]
Address = 100.77.0.1/30
ListenPort = 51820
PrivateKey = <CRM_WG_PRIVATE_KEY>

[Peer]
PublicKey = <N8N_WG_PUBLIC_KEY>
AllowedIPs = 100.77.0.2/32
```

n8n box `/etc/wireguard/wg0.conf`:

```ini
[Interface]
Address = 100.77.0.2/30
PrivateKey = <N8N_WG_PRIVATE_KEY>

[Peer]
PublicKey = <CRM_WG_PUBLIC_KEY>
Endpoint = <CRM_ORIGIN>:51820
AllowedIPs = 100.77.0.1/32
PersistentKeepalive = 25
```

On the CRM box only, make `wg0` start after Docker. The `PostUp` lines below insert into Docker's
`DOCKER-USER` chain, which does not exist until Docker has started; without this, a reboot can
leave the tunnel down. (Rollback: delete the file and `sudo systemctl daemon-reload`.)

```bash
sudo mkdir -p /etc/systemd/system/wg-quick@wg0.service.d
printf '[Unit]\nAfter=docker.service\nWants=docker.service\n' \
  | sudo tee /etc/systemd/system/wg-quick@wg0.service.d/after-docker.conf
sudo systemctl daemon-reload
```

Then `sudo systemctl enable --now wg-quick@wg0` on both, and in Hostinger on the CRM box add
**Accept, UDP, 51820, source Custom `<N8N_HOST>`**. Everything else is this runbook with
`<CRM_TS_IP>` = `100.77.0.1`, `<N8N_TS_IP>` = `100.77.0.2` and `tailscale0` = `wg0`. Step 2.3's
`tailscale serve` has no WireGuard equivalent: either keep pgbouncer on `0.0.0.0:6432` (the
Hostinger rule removal in step 6 still closes it publicly), or add a small `socat` systemd unit
from `100.77.0.1:6432` to `127.0.0.1:6432` that starts after `wg-quick@wg0`.

- Check: `sudo wg show` on each box shows a recent `latest handshake`, and
  `nc -z -w 5 100.77.0.1 443 && echo ok` works from the n8n box (ping is dropped by the policy below).
- Policy: WireGuard has no ACL, so the CRM box limits `wg0` itself. Add these to the CRM box's
  `[Interface]` section; they apply whenever `wg0` is up (reboots included) and are removed with
  it, so the rollback below covers them:

  ```ini
  PostUp   = iptables -I INPUT -i wg0 -j DROP; iptables -I INPUT -i wg0 -p tcp -m multiport --dports 443,8765,6432 -j ACCEPT
  PostUp   = iptables -I DOCKER-USER -i wg0 -j DROP; iptables -I DOCKER-USER -i wg0 -p tcp --dport 6432 -j ACCEPT
  PostDown = iptables -D INPUT -i wg0 -p tcp -m multiport --dports 443,8765,6432 -j ACCEPT; iptables -D INPUT -i wg0 -j DROP
  PostDown = iptables -D DOCKER-USER -i wg0 -p tcp --dport 6432 -j ACCEPT; iptables -D DOCKER-USER -i wg0 -j DROP
  ```

  Check: step 2.4's port loop (with `100.77.0.1`) shows only 443, 8765 and 6432 open.
- Rollback: `sudo systemctl disable --now wg-quick@wg0` on both, and delete the UDP rule. For
  good: also delete `/etc/wireguard/wg0.conf` and both key files on each box.

## Repo side (what this lane checked)

- The CRM's own MCP calls use `settings.ai_assistant_mcp_url`
  (`sorento_crm_backend/app/config.py:333`): an env-only setting (`AI_ASSISTANT_MCP_URL`) whose
  default is `http://localhost:8765/mcp`. No raw address in code, and it already takes any
  hostname, so nothing changes here. `tests/chatbot/test_s6b_fetch_lane.py` already pins that the
  chatbot lane reads the setting and never a literal host.
- The release's MCP check (`.github/workflows/deploy.yml:2116`) curls `http://127.0.0.1:8765/mcp`
  ON the CRM box over SSH. That is loopback, not the origin, and it tests the host nginx this
  runbook keeps, so it stays as is.
- `scripts/blue_green_deploy.sh:5` still describes the public MCP path in a comment. It is left
  alone so this PR stays docs-only; update it when step 6 is done.

## For crew: grep the n8n repo

Done by crew on 15 Oct against the local cache; the results are the table in step 0.4, and
step 0.4 re-runs the check on a fresh live export. Kept for reference:

The n8n workflow repo (`sorento_crm_n8n`) was not readable from the cloud sandbox. Run this at its
root and attach the output (file and node names only, never the matched values) to the lane:

```bash
# 1. Anything naming the raw origin or its ports. Each hit is a node step 4 must repoint
#    (MCP -> http://crm-private.internal:8765/mcp, Postgres -> the credential, HTTPS -> <CRM_DOMAIN>).
grep -rlnE '<CRM_ORIGIN>|:8765|:6432|:5432' --include='*.json' .
# 2. Files holding an MCP client node (open each and read its endpoint field).
grep -rnE '"type": *"@n8n/n8n-nodes-langchain\.mcpClient(Tool)?"' --include='*.json' . | cut -d: -f1 | sort -u
# 3. Every node that uses the Postgres credential, by credential name.
grep -rln '"sorento-crm-db"' --include='*.json' .
# 4. Nodes that wave the certificate through. After step 3 they get a real certificate, so the
#    flag can go; with the raw IP (family-fetch,
#    documentation/plans/chatbot/n8n-changes.md:929-932) they need repointing to
#    https://<CRM_DOMAIN>/... first.
grep -rln '"allowUnauthorizedCerts": *true' --include='*.json' .
# 5. Count of API calls through the hostname (expect about 151 files): these need NO change.
grep -rl '<CRM_DOMAIN>' --include='*.json' . | wc -l
```
