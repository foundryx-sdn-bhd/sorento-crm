# PLAN: trusted-proxy client IP (SORENTO-CLIENT-IP)

Status: PR #1563 open, review done, merge-ready pending CI (small fix track: backend resolver + call-site swap + FE header forward, no migration, no RBAC change)

## Problem

`app/services/client_ip.py` returned the LEFTMOST `X-Forwarded-For` entry, which is whatever the
caller typed. Used by the public file-link rate limit (`api/v1/public/file_links.py`), the e-sign
signer IP evidence (`api/v1/public/quotation_sign.py`) and a private copy in
`api/v1/projects/quotation_documents.py` (document `ip_address`). `services/logging.py` had the
same pattern. A caller rotating fake XFF values escapes the file-link bucket and plants whatever
signer IP it likes in the e-sign evidence.

The login / sign-in throttles and auth audit (`api/v1/auth.py` login, signup, reset-password,
phone request-code, phone verify) keyed on `request.client.host`. Behind the proxy that is the
proxy address, and login / signup / reset / phone-verify are called server-to-server by the
Next.js server, so every user shared one bucket: a lockout DoS.

## Deploy topology (evidence)

- Prod: host nginx terminates the public domain and proxies to containers published on
  `127.0.0.1:<port>` (`scripts/blue_green_deploy.sh:5-11`, upstreams at `:80-82`). Inside the
  container the peer is the docker bridge gateway (RFC1918). UNVERIFIED: the host nginx site
  config is not in the repo (`/etc/nginx/...` on the server), so whether it sets
  `X-Forwarded-For $proxy_add_x_forwarded_for` is now confirmed (see crew facts below).
- Compose image: the frontend image's own nginx sets `X-Forwarded-For
  $proxy_add_x_forwarded_for` for `/api/auth` and `/api/v1/` (`sorento_crm_frontend/nginx.conf:32,48`)
  and proxies to `backend:8000` on the compose bridge network (`sorento_crm/docker-compose.yml:93-96`).
- Next.js server calls the backend directly (`backendBaseUrl()`,
  `app/api/auth/[...nextauth]/auth-options.ts:29-36`; `lib/api-proxy.ts`), from inside the
  docker network, so its peer address is private too.
- Gunicorn `UvicornWorker` (`gunicorn.conf.py:45`) only rewrites the peer from XFF for
  `FORWARDED_ALLOW_IPS` (default 127.0.0.1); the docker peer is not 127.0.0.1, so the app sees
  the raw proxy peer.

Crew facts (10 Oct): `fe-sorento.foundryx.my` resolves straight to the prod server address (`<prod-host>`) and answers with
`server: nginx/1.24.0 (Ubuntu)`, no `cf-ray` / `via`: NO CDN, the host nginx is the edge.
Confirmed from the owner's `nginx -T` (10 Oct): every location for `fe-sorento.foundryx.my` to
`sorento_frontend` and to `sorento_backend` (and the default `server_name _` block to `sorento_mcp`)
sets `X-Real-IP $remote_addr`, `X-Forwarded-For $proxy_add_x_forwarded_for` and
`X-Forwarded-Proto $scheme`. So deploy prerequisite step 1 is already met; only step 2 remains.

Resolved IP per prod path (pinned in `tests/test_client_ip.py`, `PROD_PATHS`):
- A, browser to host nginx to backend (every browser `/api/v1` call: prod builds carry no
  `/api/v1` rewrite, `next.config.mjs:54-58`, and `NEXT_PUBLIC_API_URL` is built empty,
  `Dockerfile:22`): peer = docker gateway, XFF = `<typed>, <client>`, resolved = `<client>`.
- B, browser to host nginx to frontend (image nginx appends the gateway when it is in the path)
  to the Next server to backend (sign-in, phone verify, signup, reset; Next forwards XFF
  unchanged): peer = the frontend container, XFF = `<typed>, <client>[, <gateway>]`, resolved =
  `<client>`.
- With `TRUSTED_PROXIES` unset both resolve to the peer (one shared bucket, not forgeable).

UNVERIFIED, one residual: what `FASTAPI_INTERNAL_URL` the prod frontend container carries. Unset
means `http://backend:8000` over the docker network (path B above). If it points at the public
domain instead, the Next hop goes back out through the host nginx, which appends the server's own
address (`<prod-host>`) and that becomes the resolved IP for sign-in: one shared bucket again,
safe but not per client. Fix in that case: point it at the backend container, or add
`<prod-host>/32` to `TRUSTED_PROXIES`.

### Trust is opt-in (safe default)

`TRUSTED_PROXIES` unset (the default) = no proxy is trusted, `X-Forwarded-For` is ignored
everywhere, and every per-IP limit / IP record uses the socket peer. Behind nginx that is the
proxy's one address (the pre-lane behaviour for the throttles), but nothing a client sends can
change it. So a missing nginx line can never make the key spoofable.

### Deploy prerequisite (owner, in this order)

1. Host nginx, in every `location` that proxies to `sorento_backend` or `sorento_frontend`:

   ```nginx
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_set_header X-Real-IP       $remote_addr;
   ```

   Never `$http_x_forwarded_for` (that passes the typed value through). Check with
   `sudo nginx -T | grep -n -i -e forwarded -e real-ip`, then `sudo nginx -t && sudo nginx -s reload`.
2. Only then set, in the backend's env in the prod compose (`/opt/sorento-crm2/docker-compose.yml`,
   edited by hand on the server):
   `TRUSTED_PROXIES=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10`
   (`app.services.client_ip.RECOMMENDED_TRUSTED_PROXIES`: loopback plus the private ranges the
   docker network and its gateway use). Recreate the backend container.
3. Keep: published container ports on `127.0.0.1` only (`blue_green_deploy.sh:9-11`), and the Next
   port reachable only through the image's own nginx, which appends (`nginx.conf:32,48`).
4. A CDN added later must have its ranges added to `TRUSTED_PROXIES` (the value is the whole list).

Merging and deploying with step 2 skipped is safe: behaviour stays per proxy address, with the
per-email login ceiling on top.

## Design

One resolver, `app.services.client_ip.client_ip(request)` (mirrors foundryx-shared-service
#120 `throttle.client_ip`):

1. Peer = `request.client.host`. If no proxy is configured, or the peer is not a trusted proxy,
   return it; XFF is ignored.
2. Otherwise walk the `X-Forwarded-For` hops right to left. The first hop that is not a trusted
   proxy is the client. A malformed hop stops the walk: the last trusted address is returned.
3. Every hop trusted: return the leftmost (an internal caller).

Call sites switched to it: file links, quotation sign, quotation documents (duplicate
`_client_ip` deleted), `services/logging.py`, and the auth routes (login, signup, reset, phone
request-code, phone verify; the throttle key and the session/audit IP).

Frontend: the Next.js server forwards the browser request's `X-Forwarded-For` (as nginx set it)
on its server-to-server auth calls (`authorize()` for credentials + phone OTP, and
`proxyToFastAPI` for signup / reset), so the backend sees the real client behind the trusted
Next server.

Security review round: the login throttle gains an IP-independent per-email ceiling (40 failures
per 15 minutes, `login_throttle.EMAIL_MAX_ATTEMPTS`) next to the per-(email, ip) bucket, and IPv6
addresses count per /64, so a caller who can vary the IP still hits a floor. A bad
`TRUSTED_PROXIES` value fails at startup (`Settings` validator). The dead, left-most
`getClientIP` in `sorento_crm_frontend/lib/api.ts` is deleted.

Out of scope (named, not built): session/impersonation audit IP in `dependencies.py`,
`impersonation.py`, `portal.py`, `approval.py`, logging middleware; they keep the peer address.
Trigger to do them: a report or audit needing per-client IP there.

## Tests (red first)

`tests/test_client_ip.py`:
- forged XFF from an untrusted peer is ignored (peer returned);
- proxied request: rightmost untrusted hop returned, a forged leftmost hop ignored;
- multi-proxy chain of trusted hops skipped;
- malformed hop stops the walk;
- `TRUSTED_PROXIES` unset: XFF ignored from every peer, loopback included;
- `TRUSTED_PROXIES` is the whole trust list;
- two users behind the proxy get separate login throttle buckets (login route, via TestClient);
- quotation_documents no longer carries its own `_client_ip`;
- staff signature, customer acceptance and the API request log store the proxied client IP;
- per-email ceiling locks whatever IP the attempts claim; IPv6 /64 shares a bucket;
- bad `TRUSTED_PROXIES` raises at `Settings` construction;
- FE: `authorize()` (credentials, phone-otp) and `proxyToFastAPI` forward `X-Forwarded-For`.
