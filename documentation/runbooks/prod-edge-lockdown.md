# Runbook: prod edge lockdown (HA-SEC-EDGE)

Owner-run. Nothing here is automated, and nothing in the repo touches prod. Plan:
`documentation/plans/security/PLAN-prod-edge-lockdown-14oct.md`.

Public checks on 14 Oct found three problems:

- pgbouncer 6432 answers from the internet.
- The CRM origin answers direct traffic, so Cloudflare can be bypassed.
- MCP 8765 is plain http on the raw origin address.

**MCP 8765 has no login of its own.** The MCP server checks nothing on an incoming call. It adds
the integration key itself on every backend call (`sorento_crm_mcp/sorento_crm_mcp/http_client.py:56`).
It also always registers write tools: close complaint, cancel order, approve or reject a purchase
request (`sorento_crm_mcp/sorento_crm_mcp/record_actions.py:115-177`). So anyone who reaches 8765
can read the CRM and run those actions as the integration user. The firewall is the only control
there today. That is why 8765 is closed tonight together with 6432.

## Placeholders

Never write the real values into the repo, a PR or a comment.

| Placeholder | Meaning |
| --- | --- |
| `<CRM_ORIGIN>` | public IPv4 of the CRM box |
| `<CRM_ORIGIN_V6>` | public IPv6 of the CRM box, if it has one (`ip -6 addr show scope global` on the box; empty output = none) |
| `<CRM_DOMAIN>` | the CRM's public hostname (proxied by Cloudflare) |
| `<CRM_TS_IP>` | the CRM box's Tailscale address (`tailscale ip -4` on the box) |
| `<N8N_HOST>` | public IPv4 of the n8n box (`curl -4 ifconfig.me` run ON the n8n box) |
| `<SS_HOST>` | public IPv4 of the shared-service box |
| `<OWNER_TS_IP>` | your laptop's Tailscale address |
| `<PROD_COMPOSE>` | the on-box compose: `/opt/sorento-crm2/docker-compose.yml` |

## When

| Step | When | Time |
| --- | --- | --- |
| 0. See who connects today | tonight, first | 5 min |
| 1. Provider firewall: 6432 and 8765 from n8n only | **tonight** | **5 min** |
| 2. nginx allowlist on 8765, and no MCP on 80/443 | tonight | 5 min |
| 3. SSH key-only, root password login off, fail2ban | tonight or next release | 10 min |
| 4. Compose binding on the box (the real fix: docker bypasses ufw) | next release | 15 min |
| 5. Origin answers Cloudflare only (80/443), nginx real client IP | next release | 30 min |
| 6. Same review on the n8n box and the shared-service box | next release | 20 min each |
| 7. Verify from outside | after each step above | 2 min |

Before step 1, open the provider's firewall page and read how it treats rules:

- **Default-deny once the first rule exists** (common on VPS panels). Add every allow rule in the
  same change: 22 from anywhere (see step 3), 80 and 443 from anywhere (until step 5), and the
  6432 / 8765 rules below. Otherwise you lock yourself and the users out.
- **Default-allow.** There may be no "from anywhere" rule to remove. Add an explicit deny for
  `0.0.0.0/0` and `::/0` on the port, below the allow for `<N8N_HOST>`.

IPv4 and IPv6 rules are usually separate. Every step names both. Keep an SSH session open the
whole time. If you lose both sessions, the provider's web console still gets you in.

## Who uses these ports from outside the box (repo evidence, 14 Oct)

**6432 / 5432 (pgbouncer, Postgres): n8n.**

- The n8n workflows `live-respond-send-user` and `live-respond-close-convo`, and the
  human-intervened timeout sweeper, still hold a Postgres credential. They run SQL against the
  prod DB (hazard H53; moving them out is BL-049,
  `documentation/backlogs/backlog.md:135-137`, `documentation/plans/chatbot/PLAN-chatbot-turn-engine.md:1044`).
- So closing 6432 to everyone breaks those workflows. Allow `<N8N_HOST>`.
- The chatbot's `sub-get-rag` Postgres read has moved into the CRM (AC-604,
  `documentation/plans/chatbot/n8n-changes.md:818-821`).

**8765 (MCP): n8n.**

- The `MCP Client` / `MCP Client1` nodes call `http://<CRM_ORIGIN>:8765/mcp`
  (`documentation/plans/chatbot/n8n-changes.md:822-825`,
  `sorento_crm_backend/app/services/chatbot/lanes/business/services.py:230`).
- The release's own check calls `http://127.0.0.1:8765/mcp` on the box
  (`.github/workflows/deploy.yml:2116`).

**443 direct to the origin: n8n.**

- `family-fetch` is an httpRequest node on `https://<CRM_ORIGIN>/api/v1/master-data/products`
  with `allowUnauthorizedCerts: true` (`documentation/plans/chatbot/n8n-changes.md:929-932`).
  Until it is gone, step 5 must also allow `<N8N_HOST>` on 443.
- The SLA escalation workflow uses `<CRM_DOMAIN>`, through Cloudflare, so it is unaffected
  (`documentation/n8n/sla-scheduled-escalation.workflow.json`).

**22: GitHub Actions.**

- The release job runs on a GitHub-hosted runner and SSHes to the box from GitHub's changing
  address ranges (`.github/workflows/deploy.yml:1971`, `:2039-2085`).
- So 22 stays open until that job moves onto Tailscale (owner ruling 14 Oct, follow-up at the end).

**6379 (Redis): nobody outside the box** that this repo knows of. Every worker and the API run on
the CRM box.

**Not verified from this lane.** Step 0's log check settles these before step 5:

- The n8n repo `sorento_crm_n8n` was not visible from here. Run this in that repo before step 1,
  and add anything it finds:

  ```bash
  grep -rnE '6432|5432|6379|8765|<CRM_ORIGIN>|"type": *"n8n-nodes-base.postgres"|redis' .
  ```

- The shared-service box (Claude connector, ideation). Its contract uses `<CRM_DOMAIN>`, but its
  deployed config is not in this repo.
- The AutoCount ESB integration and Respond.io webhooks: they call in over HTTPS. Whether they use
  `<CRM_DOMAIN>` or the origin address is not in this repo.

## Step 0. See who connects today (read-only)

Run these on the CRM box. A connection to a published container port is NATed straight into the
container, so it never shows in the host's `ss`. Read the kernel's connection table instead. n8n
connects on and off, so run the first block a few times over ten minutes.

```bash
sudo apt install -y conntrack >/dev/null 2>&1 || true
for p in 5432 6432 6379 8765; do
  echo "== $p"
  sudo conntrack -L -p tcp --dport $p 2>/dev/null \
    | awk '{for(i=1;i<=NF;i++) if($i ~ /^src=/){print $i; break}}' | sort | uniq -c
done
sudo ss -ltnp | grep -E ':(5432|6432|6379|8765) '
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E '0\.0\.0\.0|:::'
```

- Check: each `src=` is a caller.
  - Anything other than `127.0.0.1`, a `172.x` docker address or `<N8N_HOST>` is a consumer
    this runbook does not know about. Add it to the allowlist in steps 1 and 2; do not leave
    the port open for it. Then tell crew.
  - For pgbouncer you can also run `SHOW CLIENTS;` in its admin console.
- Check: the `ss` line shows who owns each port.
  - `docker-proxy`: a published container port; step 4 is the compose edit.
  - `pgbouncer`: a host install. Edit `listen_addr` in `/etc/pgbouncer/pgbouncer.ini`; ufw then
    applies to it.
  - `nginx` on 8765: the expected MCP front.

Who calls 443 without Cloudflare? Run this before step 5 changes anything. It reads the access
log and prints every caller that is not a Cloudflare edge, a private address or loopback:

```bash
sudo python3 - <<'EOF'
import collections, ipaddress, urllib.request
cf = [ipaddress.ip_network(l) for u in ("https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6")
      for l in urllib.request.urlopen(u).read().decode().split()]
import glob, gzip
seen = collections.Counter()
for path in glob.glob("/var/log/nginx/*access*.log*"):          # rotated and .gz too: weekly callers
    with (gzip.open if path.endswith(".gz") else open)(path, "rt", errors="replace") as fh:
        seen.update(l.split()[0] for l in fh if l.strip())
for ip, n in seen.most_common():
    try: a = ipaddress.ip_address(ip)
    except ValueError: continue
    if a.is_private or a.is_loopback or any(a in net for net in cf if net.version == a.version): continue
    print(n, ip)
EOF
```

- Check: the list is empty, or only `<N8N_HOST>`. Each other line is a direct-origin caller that
  step 5 would cut off. Find out who it is first.
- Rollback: nothing to roll back.

## Step 1. Provider firewall: 6432 and 8765 from n8n only (TONIGHT, 5 minutes)

The provider firewall sits in front of the box, so docker's iptables rules cannot bypass it. In
the VPS provider's firewall for the CRM box, do all of this in one change:

1. TCP 6432: allow from `<N8N_HOST>/32`. Remove every "allow from anywhere" rule for 6432, IPv4
   and IPv6, or add an explicit deny for `0.0.0.0/0` and `::/0` below the allow.
2. TCP 8765: the same, allowing `<N8N_HOST>/32` only.
3. If step 0 showed 5432 or 6379 published, treat them the same way. 5432 allows `<N8N_HOST>/32`
   only if step 0 saw n8n on it. 6379 allows no source at all.
4. Any other caller step 0 found gets its own `/32` allow rule on that port.

- Check: run step 7. From a machine that is not on the allowlist, 6432 and 8765 time out, on IPv4
  and IPv6. From the n8n box they connect.
- Check: in n8n, run one `live-respond-*` execution (or wait for the sweeper's next tick) and
  confirm it is green.
- Check: ask the chatbot one product question on the test number and confirm it answers.
- Rollback: re-add TCP 6432 and 8765 from `0.0.0.0/0` and `::/0`.

## Step 2. nginx: allowlist on 8765, and MCP never on 80/443 (tonight)

MCP is served by the host nginx on 8765 (upstream `sorento_mcp`), with the containers on loopback
(`scripts/blue_green_deploy.sh:12-13`). Cloudflare cannot proxy port 8765, so this port stays
direct to the origin.

0. Save the current config first: `sudo nginx -T > ~/nginx-T-before.txt 2>&1`.
1. Inside the `listen 8765` server block:

   ```nginx
   allow 127.0.0.1;        # the release's own check
   allow 172.16.0.0/12;    # containers on this box (the CRM's AI assistant, if it uses this port)
   allow <N8N_HOST>;
   deny  all;
   ```

2. Make sure no server on 80/443 proxies to MCP. After step 5, any Cloudflare customer can reach
   80/443 with any Host header, so the `server_name _` default must not lead to MCP.

   ```bash
   sudo nginx -T 2>/dev/null | grep -nE 'listen|server_name|sorento_mcp|:876[67]|:8776'
   ```

   `sorento_mcp` (or a direct `127.0.0.1:8766` / `8776`) may appear only under a `listen 8765` block. If it appears under 80 or 443,
   delete that `location`.

3. Run `sudo nginx -t && sudo nginx -s reload`.

- Check: from the n8n box,
  `curl -s -o /dev/null -w '%{http_code}\n' http://<CRM_ORIGIN>:8765/mcp` prints `200`, `405` or
  `406`. MCP wants an SSE Accept header, so any of those means it was reached; the release check
  accepts the same three (`deploy.yml:2117-2121`).
- Check: step 7's `/mcp` lines do not reach MCP.
- Rollback: delete the four lines (and restore any removed `location` from
  `~/nginx-T-before.txt`), then reload nginx.

Follow-up, blocker-class and not this lane: MCP needs a credential of its own. Have the server
check a bearer token on every call, and have n8n send it. Until then the firewall is the only
control. Also, n8n's traffic to 8765 crosses the internet in clear text. Moving it to
`http://<CRM_TS_IP>:8765/mcp` over Tailscale closes that.

## Step 3. SSH: key-only, no root password, fail2ban

On the CRM box, keep one SSH session open the whole time and test from a second one.

First confirm your own login is a key: `ssh -o PasswordAuthentication=no <user>@<CRM_ORIGIN> true`
must succeed from your laptop.

```bash
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
sudo sshd -t && sudo systemctl reload ssh
```

The `00-` prefix matters. sshd keeps the FIRST value it reads, and a cloud image's
`50-cloud-init.conf` often sets `PasswordAuthentication yes`.

If the release's `SSH_USER` is not `root`, use `PermitRootLogin no` instead. To see which user
the release logs in as, run `last -n 5` after a release.

- Check: `sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin)'`
  prints `no`, `no`, `prohibit-password` (or `no`).
- Check: step 7's ssh line answers `Permission denied (publickey)`, and your normal `ssh` still
  works.
- Rollback: `sudo rm /etc/ssh/sshd_config.d/00-hardening.conf && sudo systemctl reload ssh`.

Port 22 itself stays open to everyone for now (owner ruling 14 Oct). The release job SSHes from
GitHub's runners; narrowing 22 is the follow-up at the end of this runbook.

fail2ban uses a short ban. A provider-side ban of runner addresses after repeated SSH failures
has broken a release before, and a GitHub runner can share an address with someone else's
failures. A release logs in with its key on the first try, so it never counts as a failure
itself.

```bash
sudo apt install -y fail2ban
sudo tee /etc/fail2ban/jail.d/sshd-local.conf >/dev/null <<'EOF'
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 10m
ignoreip = 127.0.0.1/8 ::1 <OWNER_TS_IP>
EOF
sudo systemctl restart fail2ban && sudo fail2ban-client status sshd
```

Never add GitHub's ranges, or any runner address, to a deny rule in fail2ban, ufw or the provider
panel.

- Check: `fail2ban-client status sshd` lists the jail, and the next release's SSH steps pass.
- Rollback: `sudo systemctl disable --now fail2ban`.

## Step 4. Compose binding on the box (next release; the real fix)

**Docker-published ports bypass ufw.** `ports: ["6432:6432"]` makes docker write its own iptables
NAT rule ahead of ufw's chains, so `ufw deny 6432` does nothing for a container port. Only these
hold:

- the bind address in the compose;
- the `DOCKER-USER` chain;
- the provider firewall.

The repo's own compose files now all bind `127.0.0.1`, pinned by
`sorento_crm_backend/tests/test_prod_edge_bindings.py`. But `<PROD_COMPOSE>` is gitignored and
edited by hand (`.github/workflows/deploy.yml:2044-2048`), so this edit is yours.

Before Docker Engine 28, hosts on the same L2 segment could reach a container port even when it
was bound to loopback. On some VPS networks that means other customers. Check `docker version`.
Below 28, upgrade, and keep the step 1 provider rules either way: they stay the outer layer.

**Pre-check: the containers reach Redis and pgbouncer over the docker network, not the host.**
If one of them uses the host's address, loopback binding cuts it off. This prints the host part
with the password masked:

```bash
cd /opt/sorento-crm2
for s in $(docker compose ps --services); do
  echo "== $s"
  docker compose exec -T "$s" sh -c 'printenv DATABASE_URL REDIS_URL' 2>/dev/null | sed -E 's#://[^@]*@#://***@#'
done
```

- Check: every host is a compose service name (for example `pgbouncer`, `redis`). If one is a
  public address, `172.17.0.1` or `host.docker.internal`, change it to the service name first.
- Check: every `DATABASE_URL` names a host. `start.sh` now refuses to start without one; it used
  to fall back to a hard-coded address.

Then edit:

```bash
cp docker-compose.yml docker-compose.yml.bak-$(date +%Y%m%d)
grep -nE '^\s+- *"?[0-9$\[]' docker-compose.yml     # every published port
```

Change each published port that is not already `127.0.0.1:`:

```yaml
  redis:
    ports:
      - "127.0.0.1:6379:6379"     # was "6379:6379"
  postgres:                       # if it publishes at all
    ports:
      - "127.0.0.1:5432:5432"
  pgbouncer:
    ports:
      # n8n still needs 6432 until BL-049 lands, so for now keep the binding it has
      # and rely on the step 1 provider rule:
      - "6432:6432"
      # Only once BL-049 has landed and step 0 shows no outside caller:
      # - "127.0.0.1:6432:6432"
```

Do not bind pgbouncer to `<CRM_TS_IP>`. After a reboot docker can start before tailscaled has
that address, and pgbouncer then fails to start, taking the backend with it.

If the n8n box is on the tailnet and you want 6432 off the public interface before BL-049:

1. Bind pgbouncer to `127.0.0.1:6432:6432`.
2. Forward it onto the tailnet from the host:
   `sudo tailscale serve --bg --tcp 6432 tcp://127.0.0.1:6432`. Check the syntax with
   `tailscale serve --help` on your version.
3. Point n8n's Postgres credential at `<CRM_TS_IP>:6432`.
4. Remove the step 1 provider rule for 6432.

The `backend_*`, `frontend_*` and `mcp_*` colours already publish on `127.0.0.1`
(`scripts/blue_green_deploy.sh:12-13`). Confirm that while you are in the file.

```bash
docker compose config -q && docker compose up -d --no-deps redis   # only the services you edited
```

Recreating Redis drops in-flight RQ state for a moment, and recreating pgbouncer drops open pool
connections, so do it outside office hours. Do not recreate the colour containers by hand; the
next release does that.

- Check: `docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E '0\.0\.0\.0|:::'` prints only the
  pgbouncer line, or nothing once 6432 is loopback or tailnet only.
- Check: step 7 for 5432 and 6379.
- Rollback: `cp docker-compose.yml.bak-<date> docker-compose.yml && docker compose up -d --no-deps <service>`.

## Step 5. Origin answers Cloudflare only (next release)

### 5.1 Cloudflare proxies the domain

The DNS record for `<CRM_DOMAIN>` is proxied (orange cloud), and SSL/TLS mode is **Full (strict)**.

- Check: `curl -sI https://<CRM_DOMAIN>/ | grep -i cf-ray` prints a line.

### 5.2 The origin certificate keeps renewing without direct access

Use either:

- a Cloudflare Origin CA certificate on nginx (15 years, no renewal); or
- certbot with a DNS-01 challenge.

Plain HTTP-01 renewal through Cloudflare usually still works, but test it after the firewall
change.

- Check: `sudo certbot renew --dry-run` passes.

### 5.3 nginx takes the real client from Cloudflare (mandatory)

Behind Cloudflare, nginx sees a Cloudflare edge as the peer. Two settings could fix that; only
one is safe.

- **Unsafe: adding Cloudflare's ranges to `TRUSTED_PROXIES`.** Every Cloudflare customer sends
  from those ranges. A Worker, or another zone pointed at the origin, arrives from a Cloudflare
  address with an `X-Forwarded-For` it wrote itself. The backend would then believe a typed
  address in:
  - the login, signup, reset and phone throttles;
  - the e-sign evidence;
  - the file-link limit.
- **Safe: nginx takes the client from `CF-Connecting-IP`.** Cloudflare sets that header and a
  Worker cannot.

So `TRUSTED_PROXIES` stays the private ranges (`RECOMMENDED_TRUSTED_PROXIES` in
`sorento_crm_backend/app/services/client_ip.py`), and nginx does the Cloudflare part:

```bash
{
  for r in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do
    echo "set_real_ip_from $r;"
  done
  echo "real_ip_header CF-Connecting-IP;"
} | sudo tee /etc/nginx/conf.d/cloudflare_realip.conf >/dev/null
sudo nginx -t && sudo nginx -s reload
```

Leave `real_ip_recursive` off. Cloudflare's list lives at <https://www.cloudflare.com/ips/>;
regenerate this file from it, and never copy the list from an old note.

The rewrite applies only to connections that come from a Cloudflare range, so a direct hit cannot
fake it. nginx then appends the real client to `X-Forwarded-For`. The backend's resolver stops on
that address, because it is not a trusted proxy. Pinned by
`tests/test_client_ip.py::test_cloudflare_path_with_nginx_real_ip_resolves_to_the_client` and
`::test_a_cloudflare_hop_is_never_trusted`.

The backend's `TRUSTED_PROXIES` must be the private-only value from the 10 Oct plan
(`documentation/plans/security/PLAN-trusted-proxy-client-ip-10oct.md`). Check it with
`docker compose exec backend_<colour> printenv TRUSTED_PROXIES`.

- Check: sign in once, and the session row (Settings, Sessions) shows your own address, not a
  Cloudflare one.
- Rollback: `sudo rm /etc/nginx/conf.d/cloudflare_realip.conf && sudo nginx -s reload`. You then
  get one rate-limit bucket per Cloudflare edge; that is safe, just coarse.

One residual from the 10 Oct plan: if the prod frontend's `FASTAPI_INTERNAL_URL` is the public
domain, the Next server's hop goes out through Cloudflare and comes back with the box's own
address as the client. Check with `docker compose exec frontend_<colour> printenv FASTAPI_INTERNAL_URL`.
It should be the backend container, not `<CRM_DOMAIN>`.

### 5.4 Firewall: 80/443 from Cloudflare only

In the provider panel:

- TCP 443 and 80: allow from each Cloudflare range, IPv4 and IPv6 (the same two lists).
- TCP 443: also allow `<N8N_HOST>/32` while n8n's `family-fetch` still calls the origin directly,
  plus any other direct caller from step 0's log check.
- Remove the "from anywhere" rules for 80 and 443, IPv4 and IPv6.

If the panel cannot hold the roughly 22 ranges, use ufw on the box instead. The host nginx is not
a container, so ufw does apply to 80/443. First check that ufw is actually on:

```bash
sudo ufw status verbose     # expect "Status: active" and "Default: deny (incoming)"
# ufw takes rules while it is off, so every allow goes in BEFORE it is enabled (no outage).
for r in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do
  sudo ufw allow proto tcp from "$r" to any port 80,443
done
sudo ufw allow proto tcp from <N8N_HOST> to any port 443
# Only if it was inactive, still before enabling:
#   sudo ufw allow OpenSSH
#   sudo ufw allow proto tcp from <N8N_HOST> to any port 8765,6432
#   sudo ufw allow in on tailscale0        # the tailnet (follow-up, tailscale serve)
#   sudo ufw allow in on docker0           # containers reaching host nginx on 8765
#   sudo ufw default deny incoming && sudo ufw enable
sudo ufw delete allow 80/tcp; sudo ufw delete allow 443/tcp; sudo ufw delete allow 'Nginx Full'
```

- Check: step 7's "origin bypass" lines time out on IPv4 and IPv6.
- Check: `https://<CRM_DOMAIN>/` still loads, and n8n's product-family fallback still answers.
- Check: `sudo certbot renew --dry-run` passes.
- Rollback: re-add TCP 80/443 from `0.0.0.0/0` and `::/0`.

### Later

Firewalling to Cloudflare's ranges still lets any other Cloudflare customer reach the origin. The
fix is Authenticated Origin Pulls with a **per-zone** certificate you upload, plus
`ssl_verify_client on` and `ssl_client_certificate <that CA>` in nginx. Cloudflare's default
origin-pull certificate is shared by every customer, so it does not stop other tenants.

WAF: none of the repos hold WAF rules. In the Cloudflare dashboard, turn on the Managed Ruleset
and add a rate-limit rule on `/api/v1/auth/` as a start.

## Step 6. The n8n box and the shared-service box

Ask the same three questions on each box:

```bash
sudo ss -ltnp | awk 'NR==1 || $4 !~ /^(127\.|\[::1\])/'     # everything listening off-loopback
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E '0\.0\.0\.0|:::'
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'
```

- Every database, Redis and admin port binds `127.0.0.1` in that box's compose, as in step 4.
  That includes n8n's own Postgres, Redis, and 5678 if a proxy fronts it.
- The provider firewall allows only 22 and 443, plus 80 for the redirect, on IPv4 and IPv6.
- n8n's editor (5678 or its proxy) should not be open to the internet without its own login.
  Restrict it to the tailnet if you can.
- Apply step 3 (SSH and fail2ban) on both boxes.

- Check: step 7 against `<N8N_HOST>` and `<SS_HOST>` for every port the `ss` line listed.
- Rollback: the `.bak` compose copy and the provider rule, as in steps 1 and 4.

## Step 7. Verify from outside

Run this from a machine that is NOT on any allowlist: a phone hotspot or a throwaway cloud shell.
Each port gets one connect. `nc` exits non-zero on a timeout, which is a pass. Drop the `-6`
lines if the box has no IPv6.

```bash
for p in 5432 6432 6379 8765; do
  nc -4 -z -w 5 <CRM_ORIGIN> $p && echo "OPEN  v4 $p  <- FAIL" || echo "closed v4 $p"
  nc -6 -z -w 5 <CRM_ORIGIN_V6> $p && echo "OPEN  v6 $p  <- FAIL" || echo "closed v6 $p"
done
# MCP must not be reachable on 80/443 under any Host (step 2): expect 000, 404 or 403, never 200/405/406
curl -sk -o /dev/null -w '%{http_code}\n' --max-time 8 https://<CRM_ORIGIN>/mcp
curl -s  -o /dev/null -w '%{http_code}\n' --max-time 8 http://<CRM_ORIGIN>/mcp
# origin bypass (step 5): expect 000 (timeout), not 200/301
curl -sk -o /dev/null -w '%{http_code}\n' --max-time 8 --resolve <CRM_DOMAIN>:443:<CRM_ORIGIN> https://<CRM_DOMAIN>/
curl -sk -o /dev/null -w '%{http_code}\n' --max-time 8 --resolve "<CRM_DOMAIN>:443:[<CRM_ORIGIN_V6>]" https://<CRM_DOMAIN>/
# through Cloudflare: expect 200 and a cf-ray header
curl -sI https://<CRM_DOMAIN>/ | grep -iE '^(HTTP|cf-ray)'
# SSH (step 3): expect "Permission denied (publickey)"
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password -o ConnectTimeout=8 root@<CRM_ORIGIN>
```

From the n8n box, the ports it still needs must connect:

```bash
nc -z -w 5 <CRM_ORIGIN> 6432 && echo ok-6432
curl -s -o /dev/null -w '%{http_code}\n' http://<CRM_ORIGIN>:8765/mcp   # 200, 405 or 406
```

The origin address has been in this repo's history and in public DNS history, so assume it is
known. The firewall is the control, not keeping the address secret.

## Follow-up (not this runbook's change): the release job over Tailscale, then narrow 22

This needs its own lane, because it changes the release path. The shape:

1. In the Tailscale admin, create an OAuth client with the `auth_keys` scope and a tag such as
   `tag:ci`. Add an ACL that lets `tag:ci` reach only `<CRM_TS_IP>:22`.
2. In `.github/workflows/deploy.yml`, job `build-and-deploy`, add a `tailscale/github-action`
   step before the scp step:
   - pinned to a commit SHA;
   - the OAuth client id and secret stored as `production`-environment secrets;
   - `tags: tag:ci`, so each run joins as an ephemeral node.
3. Change the `SSH_HOST` secret to `<CRM_TS_IP>`.
4. Run one release on the new path. Only after it is green, set the provider firewall so TCP 22
   is open to nothing public. Tailscale traffic does not need a public 22 rule. Keep the
   provider's web console as the way back in.

- Check: the release's scp and ssh steps pass, and from a non-allowlisted machine
  `nc -z -w 5 <CRM_ORIGIN> 22` times out.
- Rollback: set `SSH_HOST` back to `<CRM_ORIGIN>`, and set the provider rule for 22 back to
  `0.0.0.0/0` and `::/0`.
