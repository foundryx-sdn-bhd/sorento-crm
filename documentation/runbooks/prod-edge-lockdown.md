# Runbook: prod edge lockdown (HA-SEC-EDGE)

Owner-run. Nothing here is automated, and nothing in the repo touches prod. Plan:
`documentation/plans/security/PLAN-prod-edge-lockdown-14oct.md`.

Public checks on 14 Oct found: pgbouncer 6432 answers from the internet, the CRM origin answers
direct traffic (Cloudflare can be bypassed), and MCP 8765 is plain http on the raw origin
address.

## Placeholders

Never write the real values into the repo, a PR or a comment.

| Placeholder | Meaning |
| --- | --- |
| `<CRM_ORIGIN>` | public IPv4 of the CRM box |
| `<CRM_DOMAIN>` | the CRM's public hostname (proxied by Cloudflare) |
| `<CRM_TS_IP>` | the CRM box's Tailscale address (`tailscale ip -4` on the box) |
| `<N8N_HOST>` | public IPv4 of the n8n box (`curl -4 ifconfig.me` run ON the n8n box) |
| `<SS_HOST>` | public IPv4 of the shared-service box |
| `<OWNER_IP>` | your current public IPv4 |
| `<PROD_COMPOSE>` | the on-box compose: `/opt/sorento-crm2/docker-compose.yml` |

## When

| Step | When | Time |
| --- | --- | --- |
| 0. See who connects today | tonight, first | 2 min |
| 1. Provider firewall: 6432 from n8n only | **tonight** | **5 min** |
| 2. Provider firewall + nginx: 8765 from n8n only | tonight, if step 0 shows only n8n on 8765 | 5 min |
| 3. SSH key-only, root password login off | tonight or next release | 10 min |
| 4. Compose binding on the box (the real fix: docker bypasses ufw) | next release | 15 min |
| 5. Provider firewall: 80/443 from Cloudflare only, `TRUSTED_PROXIES` | next release | 20 min |
| 6. Same review on the n8n box and the shared-service box | next release | 20 min each |
| 7. Verify from outside | after each step above | 2 min |

Before step 1, open the provider's firewall page and read how it treats a box with rules: many
VPS firewalls turn into **default-deny the moment the first rule exists**. If yours does, add
the allow rules for 22 (from anywhere for now, see step 3), 80 and 443 (from anywhere until
step 5) and 8765 (from anywhere until step 2) **in the same change**, or you lock yourself and
the users out. Keep an SSH session open the whole time.

## Who uses these ports from outside the box (repo evidence, 14 Oct)

- **6432 / 5432 (Postgres, pgbouncer): n8n.** The n8n workflows `live-respond-send-user`,
  `live-respond-close-convo` and the human-intervened timeout sweeper still hold a Postgres
  credential and run SQL against the prod DB (hazard H53, port-out tracked as BL-049,
  `documentation/backlogs/backlog.md:135-137`, `documentation/plans/chatbot/PLAN-chatbot-turn-engine.md:1044`).
  So **closing 6432 to everyone breaks those workflows**: allow `<N8N_HOST>`.
  The chatbot's own `sub-get-rag` Postgres read moved into the CRM (AC-604), so it no longer needs
  the DB once the CRM lane is on (`documentation/plans/chatbot/n8n-changes.md:818-821`).
- **8765 (MCP): n8n** `MCP Client` / `MCP Client1` nodes call `http://<CRM_ORIGIN>:8765/mcp`
  (`documentation/plans/chatbot/n8n-changes.md:822-825`, `sorento_crm_backend/app/services/chatbot/lanes/business/services.py:230`).
  The deploy's own check calls `http://127.0.0.1:8765/mcp` on the box (`.github/workflows/deploy.yml:2116`).
- **443 direct to the origin: n8n** `family-fetch` is an httpRequest node on
  `https://<CRM_ORIGIN>/api/v1/master-data/products` with `allowUnauthorizedCerts: true`
  (`documentation/plans/chatbot/n8n-changes.md:929-932`). Until it is gone, step 5 must also
  allow `<N8N_HOST>` on 443. The SLA escalation workflow uses `<CRM_DOMAIN>`, which goes through
  Cloudflare and is unaffected (`documentation/n8n/sla-scheduled-escalation.workflow.json`).
- **22: GitHub Actions.** The release job runs on a GitHub-hosted runner and SSHes to the box
  (`.github/workflows/deploy.yml:1971`, `:2039-2085`), from GitHub's changing address ranges. So
  22 cannot be narrowed to your IPs without first moving that job onto Tailscale (step 3).
- **6379 (Redis): nobody outside the box** that this repo knows of. Every worker and the API run
  on the CRM box.
- **Not checked (needs the n8n repo `sorento_crm_n8n`, not visible from this lane):** every
  n8n credential and node. Run this in that repo before step 1 and add anything it finds:

  ```bash
  grep -rnE '6432|5432|6379|8765|<CRM_ORIGIN>|"type": *"n8n-nodes-base.postgres"|redis' .
  ```

## Step 0. See who connects today (read-only)

On the CRM box:

```bash
sudo ss -tnH state established '( sport = :6432 or sport = :5432 or sport = :6379 or sport = :8765 )' \
  | awk '{print $4, $5}' | sort | uniq -c
sudo ss -ltnp | grep -E ':(5432|6432|6379|8765|8000|8010|3000|3010|8766|8776) '
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E '0\.0\.0\.0|:::'
```

- Check: the first command lists the remote addresses. Anything other than `127.0.0.1`, a
  `172.x` docker address or `<N8N_HOST>` is a consumer this runbook does not know about: stop
  and tell crew before step 1.
- The second tells you who owns 6432: `docker-proxy` = a published container port (step 4 is
  the compose edit), `pgbouncer` = a host install (edit `listen_addr` in
  `/etc/pgbouncer/pgbouncer.ini`, ufw then works).
- Rollback: nothing to roll back.

## Step 1. Provider firewall: 6432 from n8n only (TONIGHT, 5 minutes)

In the VPS provider's firewall for the CRM box (it sits in front of the box, so docker's
iptables rules cannot bypass it):

1. Add: TCP 6432, source `<N8N_HOST>/32`, allow.
2. Remove (or change to deny) any rule that allows 6432 from anywhere. If 5432 or 6379 showed up
   as published in step 0, do the same for them (5432: `<N8N_HOST>/32` only if step 0 showed n8n
   on it; 6379: no source at all).

- Check: step 7 "6432" from a machine that is not the n8n box times out; from the n8n box it
  connects. Then open n8n, run one `live-respond-*` execution (or wait for the sweeper's next
  tick) and confirm it is green.
- Rollback: re-add TCP 6432 from `0.0.0.0/0`.

## Step 2. 8765 from n8n only (tonight if step 0 is clean)

MCP is served by the host nginx on 8765 (`server_name _` block, upstream `sorento_mcp`), with the
containers on loopback (`scripts/blue_green_deploy.sh:9-11`). Cloudflare cannot proxy port 8765,
so this port stays direct to the origin and needs its own allowlist.

1. Provider firewall: TCP 8765, source `<N8N_HOST>/32` only.
2. Belt and braces in the host nginx, inside the `listen 8765` server block:

   ```nginx
   allow 127.0.0.1;        # the deploy's own check
   allow 172.16.0.0/12;    # containers on this box (the CRM's AI assistant, if it uses this port)
   allow <N8N_HOST>;
   deny  all;
   ```

   `sudo nginx -t && sudo nginx -s reload`.

- Check: step 7 "8765". From the n8n box
  `curl -s -o /dev/null -w '%{http_code}\n' http://<CRM_ORIGIN>:8765/mcp` prints `406` (MCP wants
  an SSE Accept header; 406 means it was reached). Ask the chatbot one product question on the
  test number and confirm it answers.
- Rollback: delete the four lines, reload nginx; re-open 8765 from `0.0.0.0/0` in the provider.
- Next release, not tonight: the API key still crosses the internet in clear text on this port.
  Move n8n to `http://<CRM_TS_IP>:8765/mcp` over Tailscale (if the n8n box is on the tailnet)
  and close 8765 on the public interface entirely.

## Step 3. SSH: key-only, no root password

On the CRM box, keep one SSH session open the whole time and test from a second one.

```bash
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
sudo sshd -t && sudo systemctl reload ssh
```

`00-` matters: sshd takes the FIRST value it reads, and a cloud image's
`50-cloud-init.conf` often sets `PasswordAuthentication yes`. Use `PermitRootLogin no` instead
if the release's `SSH_USER` secret is not `root` (check before: `grep -n SSH_USER` in your
secret store, or `last -n 5` after a release shows which user logged in).

- Check: `sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin)'`
  prints `no`, `no`, `prohibit-password` (or `no`). From your laptop:
  `ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password <user>@<CRM_ORIGIN>`
  answers `Permission denied (publickey)`. Your normal `ssh` still works.
- Rollback: `sudo rm /etc/ssh/sshd_config.d/00-hardening.conf && sudo systemctl reload ssh`.
- Port 22 itself stays open to everyone for now (crew ruling 14 Oct), because the release job
  SSHes from GitHub's runners (see "Who uses these ports"). Narrowing it is the follow-up at the
  end of this runbook.
- fail2ban, with a short ban so a GitHub runner that shares an address with someone else's
  failures is never locked out for long (a provider-side ban of runner addresses after repeated
  SSH failures has broken a release before). A release logs in with its key on the first try, so
  it never counts as a failure itself:

  ```bash
  sudo apt install -y fail2ban
  sudo tee /etc/fail2ban/jail.d/sshd-local.conf >/dev/null <<'EOF'
  [sshd]
  enabled  = true
  maxretry = 5
  findtime = 10m
  bantime  = 10m
  ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10 <OWNER_IP>
  EOF
  sudo systemctl restart fail2ban && sudo fail2ban-client status sshd
  ```

  Never add GitHub's ranges (or any runner address) to a deny rule, in fail2ban, ufw or the
  provider panel. Check: the next release's SSH steps pass. Rollback:
  `sudo systemctl disable --now fail2ban`.

## Step 4. Compose binding on the box (next release; the real fix)

**Docker-published ports bypass ufw.** `ports: ["6432:6432"]` makes docker write its own
iptables NAT rule ahead of ufw's chains, so `ufw deny 6432` does nothing for a container port.
Only the bind address in the compose (or the `DOCKER-USER` chain, or the provider firewall)
holds. The repo's own compose files now all bind `127.0.0.1` (pinned by
`sorento_crm_backend/tests/test_prod_edge_bindings.py`), but `<PROD_COMPOSE>` is gitignored and
edited by hand (`.github/workflows/deploy.yml:2044-2048`), so this edit is yours.

```bash
cd /opt/sorento-crm2 && cp docker-compose.yml docker-compose.yml.bak-$(date +%Y%m%d)
grep -nE '^\s+- *"?[0-9$]' docker-compose.yml     # every published port
```

Edit each published port that is not already `127.0.0.1:` like this:

```yaml
  # Redis, Postgres, and the colour containers: loopback only
  redis:
    ports:
      - "127.0.0.1:6379:6379"     # was "6379:6379"
  postgres:                       # if it publishes at all
    ports:
      - "127.0.0.1:5432:5432"
  pgbouncer:
    ports:
      - "127.0.0.1:6432:6432"     # was "6432:6432"
      # n8n still needs it until BL-049 lands. Pick ONE:
      # (a) n8n box on the tailnet:  - "<CRM_TS_IP>:6432:6432"   and point n8n's Postgres
      #     credential at <CRM_TS_IP>:6432, then remove the step 1 provider rule.
      # (b) not on the tailnet: keep "6432:6432" and rely on the step 1 provider rule.
```

The `backend_*`, `frontend_*` and `mcp_*` colours already publish on `127.0.0.1`
(`scripts/blue_green_deploy.sh:9-11`); confirm while you are in the file.

```bash
docker compose config -q && docker compose up -d --no-deps redis pgbouncer   # the services you edited
```

Recreating Redis drops in-flight RQ state for a second and pgbouncer drops open pool connections;
do it outside office hours. Do not recreate the colour containers by hand: the next release does.

- Check: `docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E '0\.0\.0\.0|:::'` prints nothing
  (or only the pgbouncer line under option (b)); step 7 for 5432 / 6379.
- Rollback: `cp docker-compose.yml.bak-<date> docker-compose.yml && docker compose up -d --no-deps <service>`.

## Step 5. Origin answers Cloudflare only (next release)

Prereqs, in this order:

1. Cloudflare DNS record for `<CRM_DOMAIN>` is proxied (orange cloud), SSL/TLS mode **Full
   (strict)**. Check `curl -sI https://<CRM_DOMAIN>/ | grep -i cf-ray` prints a line.
2. The origin certificate keeps renewing without direct access: either a Cloudflare Origin CA
   certificate on nginx (15-year, no renewal), or certbot with a DNS-01 challenge. Plain HTTP-01
   renewal through Cloudflare usually still works, but test it: `sudo certbot renew --dry-run`
   after the firewall change.
3. `TRUSTED_PROXIES` gains Cloudflare's ranges, so per-client rate limits and audit IPs see the
   real client and not a Cloudflare edge. Build the value on the box (the list lives at
   <https://www.cloudflare.com/ips/>; never copy it from an old note):

   ```bash
   CF4=$(curl -fsS https://www.cloudflare.com/ips-v4 | paste -sd, -)
   CF6=$(curl -fsS https://www.cloudflare.com/ips-v6 | paste -sd, -)
   echo "TRUSTED_PROXIES=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10,${CF4},${CF6}"
   ```

   Put that value in the environment of `backend_blue`, `backend_green`, `worker` and
   `worker_fast` in `<PROD_COMPOSE>`; it takes effect on the next release (or recreate the
   active colour). The host nginx already appends `X-Forwarded-For` (confirmed 10 Oct,
   `documentation/plans/security/PLAN-trusted-proxy-client-ip-10oct.md`). Pinned by
   `tests/test_client_ip.py::test_cloudflare_path_resolves_to_the_client`.
   Check: `docker compose exec backend_<colour> printenv TRUSTED_PROXIES`; sign in once and see
   your own address on the session row (Settings, Sessions). Rollback: the old value (private
   ranges only, or empty); both are safe, just per-proxy.
4. Optional, only if an nginx `allow`/`deny` must see the real client (the planned
   `/analytics/` allowlist, `documentation/plans/analytics/CARD-claude-connector-09oct.md:56-60`):
   generate `/etc/nginx/conf.d/cloudflare_realip.conf` with one `set_real_ip_from <range>;` per
   Cloudflare range plus `real_ip_header CF-Connecting-IP;`. It only rewrites the address for
   connections that come from Cloudflare, so a direct hit cannot fake it.

Then the firewall, in the provider panel:

- TCP 443 and 80: allow from each Cloudflare range (same two lists), plus `<N8N_HOST>/32` on 443
  while n8n's `family-fetch` still calls the origin directly. Remove the "from anywhere" rules.
- If the provider panel cannot hold ~22 ranges, use ufw on the box instead: the host nginx is
  not a container, so ufw does apply to 80/443:

  ```bash
  for r in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do
    sudo ufw allow proto tcp from "$r" to any port 80,443
  done
  sudo ufw allow proto tcp from <N8N_HOST> to any port 443
  sudo ufw delete allow 80/tcp; sudo ufw delete allow 443/tcp; sudo ufw delete allow 'Nginx Full'
  ```

- Check: step 7 "origin bypass" times out; `https://<CRM_DOMAIN>/` still loads; n8n's
  product-family fallback still answers; `sudo certbot renew --dry-run` passes.
- Rollback: re-add TCP 80/443 from `0.0.0.0/0` (and `::/0`).

Firewalling to Cloudflare's ranges still lets another Cloudflare customer's zone reach the
origin. The stronger version, later: Cloudflare Authenticated Origin Pulls (mTLS,
`ssl_verify_client on` in nginx). WAF: none of the repos hold WAF rules; in the Cloudflare
dashboard turn on the Managed Ruleset and add a rate-limit rule on `/api/v1/auth/` as a start.

## Step 6. The n8n box and the shared-service box

Same three questions on each box:

```bash
sudo ss -ltnp | awk 'NR==1 || $4 !~ /^(127\.|\[::1\])/'     # everything listening off-loopback
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E '0\.0\.0\.0|:::'
sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin)'
```

- Every database, Redis and admin port (n8n's own Postgres, Redis, 5678 if a proxy fronts it)
  binds `127.0.0.1` in that box's compose, as in step 4, and the provider firewall allows only
  22 and 443 (plus 80 for the redirect).
- n8n's editor (5678 or its proxy) should not be open to the internet without its own login;
  restrict it to `<OWNER_IP>` / the tailnet if you can.
- Apply step 3 (SSH) on both boxes.
- Check: step 7 against `<N8N_HOST>` and `<SS_HOST>` for every port step 0 found.
- Rollback: the `.bak` compose copy and the provider rule, as in steps 1 and 4.

## Step 7. Verify from outside

Run from a machine that is NOT on any allowlist (phone hotspot, or a throwaway cloud shell), one
connect per port; `nc` exits non-zero on a timeout, which is the pass:

```bash
for p in 5432 6432 6379 8765; do
  nc -z -w 5 <CRM_ORIGIN> $p && echo "OPEN  $p  <- FAIL" || echo "closed $p"
done
# origin bypass (step 5): expect 000 (timeout), not 200/301
curl -sk -o /dev/null -w '%{http_code}\n' --max-time 8 --resolve <CRM_DOMAIN>:443:<CRM_ORIGIN> https://<CRM_DOMAIN>/
# through Cloudflare: expect 200 and a cf-ray header
curl -sI https://<CRM_DOMAIN>/ | grep -iE '^(HTTP|cf-ray)'
# SSH (step 3): expect "Permission denied (publickey)"
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password -o ConnectTimeout=8 root@<CRM_ORIGIN>
```

And from the n8n box, the ports it still needs connect:

```bash
nc -z -w 5 <CRM_ORIGIN> 6432 && echo ok-6432
curl -s -o /dev/null -w '%{http_code}\n' http://<CRM_ORIGIN>:8765/mcp   # 406
```

The origin address has been in this repo's history and in public DNS history, so assume it is
known: the firewall is the control, not keeping the address secret.

## Follow-up (not this runbook's change): the release job over Tailscale, then narrow 22

Its own lane, because it changes the release path. Shape:

1. Tailscale admin: an OAuth client with the `auth_keys` scope and a tag such as `tag:ci`; an
   ACL that lets `tag:ci` reach only `<CRM_TS_IP>:22`.
2. `.github/workflows/deploy.yml`, job `build-and-deploy`: a `tailscale/github-action` step
   (pinned to a commit SHA) before the scp step, with the OAuth client id and secret as
   `production`-environment secrets and `tags: tag:ci`, so each run joins as an ephemeral node.
3. `SSH_HOST` secret becomes `<CRM_TS_IP>`.
4. One release on the new path; only after it is green, provider firewall: TCP 22 from
   `<OWNER_IP>/32` only (Tailscale traffic does not need a public 22 rule).

- Check: the release's scp and ssh steps pass; from a non-allowlisted machine
  `nc -z -w 5 <CRM_ORIGIN> 22` times out.
- Rollback: `SSH_HOST` back to `<CRM_ORIGIN>`, provider rule 22 back to `0.0.0.0/0`.
