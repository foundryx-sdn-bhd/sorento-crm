# PLAN: trusted-proxy client IP (SORENTO-CLIENT-IP)

Status: in progress (small fix track: backend resolver + call-site swap + FE header forward, no migration, no RBAC change; reviewer + security reviewer per lane brief)

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
  `X-Forwarded-For $proxy_add_x_forwarded_for` and whether a CDN sits in front is UNVERIFIED.
- Compose image: the frontend image's own nginx sets `X-Forwarded-For
  $proxy_add_x_forwarded_for` for `/api/auth` and `/api/v1/` (`sorento_crm_frontend/nginx.conf:32,48`)
  and proxies to `backend:8000` on the compose bridge network (`sorento_crm/docker-compose.yml:93-96`).
- Next.js server calls the backend directly (`backendBaseUrl()`,
  `app/api/auth/[...nextauth]/auth-options.ts:29-36`; `lib/api-proxy.ts`), from inside the
  docker network, so its peer address is private too.
- Gunicorn `UvicornWorker` (`gunicorn.conf.py:45`) only rewrites the peer from XFF for
  `FORWARDED_ALLOW_IPS` (default 127.0.0.1); the docker peer is not 127.0.0.1, so the app sees
  the raw proxy peer.

Prod prerequisites (UNVERIFIED until someone runs `sudo nginx -T | grep -n -i forwarded` on the
server and records the result here):
1. Every host nginx `location` for `sorento_backend` / `sorento_frontend` sets
   `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` (or `$remote_addr`), never
   `$http_x_forwarded_for`. If it passes the header through untouched, the client chooses the
   per-IP key; the per-email login ceiling below is the floor that still holds.
2. No CDN / public-address proxy in front of the host nginx. If there is one, `TRUSTED_PROXIES`
   must list its ranges PLUS loopback and the docker subnet (the value replaces the default).
3. Published container ports bind `127.0.0.1` only (`blue_green_deploy.sh:9-11` says so).
4. The Next port is reachable only through the image's own nginx, which appends.

So the default trust set is loopback + RFC1918 + IPv6 loopback/ULA/link-local, overridable with
`TRUSTED_PROXIES` (comma-separated CIDRs) when the topology differs (e.g. a CDN in front).

## Design

One resolver, `app.services.client_ip.client_ip(request)` (mirrors foundryx-shared-service
#120 `throttle.client_ip`):

1. Peer = `request.client.host`. If the peer is not a trusted proxy, return it; XFF is ignored.
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
- `TRUSTED_PROXIES` override honoured;
- two users behind the proxy get separate login throttle buckets (login route, via TestClient);
- quotation_documents no longer carries its own `_client_ip`;
- staff signature, customer acceptance and the API request log store the proxied client IP;
- per-email ceiling locks whatever IP the attempts claim; IPv6 /64 shares a bucket;
- bad `TRUSTED_PROXIES` raises at `Settings` construction;
- FE: `authorize()` (credentials, phone-otp) and `proxyToFastAPI` forward `X-Forwarded-For`.
