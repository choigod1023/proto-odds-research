# Small web edge + existing collector

Status: opt-in implementation, not deployed. Existing Fly config, DB, prediction
policy, frozen ledger, and watchdog origin are unchanged. No Kubernetes required.

## Boundary

Browser → independent Nginx web machine → existing collector's prepared HTTP responses.
Cache hits and previously cached stale responses do not wait for collection/model work.
The collector still builds compressed responses under its existing memory coordination;
this is not a full removal of HTTP/render preparation from the collector and does not
fix its upstream collection failures or OOM. No SQLite files are shared between hosts.

- One Nginx worker, 256 connections, 2MiB cache index, 64MiB disk-cache target.
  Cache manager limits are not instantaneous hard RAM/disk guarantees.
- Only existing public read routes are cached; only HTTP 200 is stored.
- Five-second edge freshness; expiry triggers background refresh on the next request,
  not an idle polling job. Browser timestamps are excluded from the key, but scope,
  detail key/revision and gzip representation remain separate. Browser no-cache still
  reaches the edge; it does not force a collector rebuild on every click.
- 500/502/503/504, transport failures and refresh-in-progress may return the last
  successful body unchanged. Original generated_at/revision and origin Date remain.
  X-Response-Cache exposes HIT/MISS/STALE/UPDATING; X-Origin-Date is fetch/response time,
  NOT model generation time. Existing UI timestamps remain authoritative.
- Stale fallback has no strict maximum age while a cached entry stays active. It is
  availability fallback, not fresh-data proof. Monitor collector /health separately.
  The edge can serve yesterday's dated response across midnight during an outage;
  it never relabels it as today's. Do not interpret HTTP 200 as a current prediction.
- Cached detail is keyed by exact revision. A new/unknown revision must reach origin;
  409/404 is not replaced with another revision. Unvisited details and cold scopes
  remain unavailable while origin is down. This is NOT universal zero downtime.
- /health is uncached collector health. /readyz checks only edge process readiness.
  Existing watchdog keeps the collector URL. Check both hosts after rollout.
- /anonymous-bets is uncached pass-through, with no automatic retry. No queued writes
  or credential forwarding. No prediction, betting, or monthly loss-limit changes.

## Cache persistence and memory limits

Fly config intentionally creates no paid volume. Cache on the container filesystem
survives an Nginx process restart, but is NOT guaranteed across Fly replacement/deploy.
Do not promise restart protection on an empty replacement machine. Seed recent,
performance, all and required artifact routes after deployment, verify a second HIT,
and only then change the frontend. A future persistent snapshot store/volume requires
separate approval and testing. 256MiB is a candidate, not measured production sizing.

## Tests (no production traffic or DB mutation)

`NGINX_BIN=/usr/sbin/nginx python tests/test_web_edge.py -v`

Uses a local fake collector and real Nginx with this config, replacing only local
paths, bind port, and upstream TLS with fixture HTTP. Checks cache-buster coalescing,
scope/detail/revision/encoding isolation, no write caching, 503 fallback with unchanged
body, uncached health failure, recovery on the next refresh, and process restart with
retained cache. CI additionally builds the Linux image and runs `nginx -t` in it.
Local Windows results are not a Linux memory benchmark or production SLO.

## Approval-gated rollout

1. Verify CI and review PR. Merge only with approval. Record current collector
   timestamps, image, API responses, frontend origin and baseline latency.
2. Confirm Tokyo account pricing for one shared CPU/256MiB machine, IPv4 allocation,
   egress and any storage. Public table's $2.19/month is a reference, not a quote.
   Create/deploy exactly ONE web machine only after cost approval; do not scale collector.
3. From repo root deploy with `fly deploy -c deploy/web/fly.toml --ha=false` after
   provisioning the approved app/network. App name availability must be checked.
4. Warm read routes with both supported encodings; verify gzip, CORS, original body
   timestamps, detail revision, and /health against the direct collector. Check Linux
   RSS/cgroup peak and disk use under representative traffic before accepting 256MiB.
5. Set repository variable VITE_MATCH_API_ORIGIN to the verified web origin and run
   build-site. The default stays on collector until then. All browser API consumers
   now use that single setting, including live scores and anonymous submissions.
6. Wait for a real automatic collector update: compare list view revision, selected
   detail revision, artifact generation times and collector /health. Inspect both
   hosts' OOM/restart logs; fast stale responses are not successful refreshes.
7. Roll back frontend variable to collector and rebuild if mismatch/pressure occurs.
   Do not delete the DB or restart the collector as part of rollback.

References: https://nginx.org/en/docs/http/ngx_http_proxy_module.html
and https://fly.io/pricing/ . No claim of tested cost savings or resolved upstream OOM.
