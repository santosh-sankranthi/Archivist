# Deploying Archivist

Four ways to run or reach Archivist, from "reproduce on your machine" to
"someone else clicks a link".

## 1. Local (the quickstart path)

```bash
docker run -p 6379:6379 --name falkordb -d falkordb/falkordb:v4.22.1
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
git clone https://github.com/pallets/flask data/flask
python scripts/ingest.py                 # -> graph "repo:flask"
python scripts/serve.py                  # -> http://127.0.0.1:8787
```

This is the full product: ingest, agent loop (`scripts/ask.py`), crew
(`scripts/crew.py`), web console. Code graphs are read-only through the web
layer; the only writes are memory episodes/facts and, from M6 on, accounts
under `data/`. The Ask panel needs an LLM key (any OpenAI-compatible
endpoint) in `.env` — or a visitor's own key at request time (M7).

Accounts (M6) live in `data/accounts/{users,sessions}.json` (0600, atomic
writes) with per-user registries in `data/users/<uid>/`. `serve.py` flags:
`--data-dir` (default `<repo>/data`), `--demo-graph`, `--signup open|closed`,
`--max-repos` (default 2). Anonymous visitors stay on the shared demo graph;
a signed-in user may only name repo ids from their own registry.

### Console v2 (`/console/`, M4R)

The redesigned console builds from `console/` (Vite + React + TS strict):

```bash
cd console && npm ci && npm run build   # node 24+; ~1s build
```

`archivist.webapp` serves the build at `/console/` (SPA fallback for
client routes; a tree without the build returns an honest 503). The root
`/` redirects (302) to `/console/` — v2 is the product's face; the v1
shell stays reachable at `/classic/`. Static rebuilds need no service
restart; `webapp.py` changes do (`systemctl --user restart
archivist-console` on the demo host).

## 2. Public demo (current) — Cloudflare quick tunnel

The demo host serves the console through a
[cloudflared quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/):
no account, HTTPS, but **the URL is ephemeral** — it changes every time the
tunnel process restarts (e.g. after a VM restart).

Get the current URL:

```bash
journalctl --user -u archivist-tunnel | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
```

How it runs (dev box): two systemd user units —

- `archivist-console.service` — `scripts/serve.py` on `127.0.0.1:8787`
- `archivist-tunnel.service` — `cloudflared tunnel --url http://127.0.0.1:8787`

```bash
systemctl --user restart archivist-console archivist-tunnel
systemctl --user status archivist-console archivist-tunnel
```

Quick tunnels are demo-grade (no uptime guarantee, shared rate limits). For
a stable URL, see the upgrade paths below.

## 3. Stable URL upgrades (planned)

- **Tailscale Funnel** — same box, stable `https://santoshslegion.taild2fe23.ts.net`,
  no new account. Needs a one-time tailnet admin enablement (Serve/Funnel),
  then `tailscale funnel --bg 8787`. Pending: admin click from the operator.
- **A deploy host** (Fly.io / Render / a VPS) with a `GH_TOKEN`-created repo
  and registry token. Pending: operator accounts/tokens.
- **Container image** — `Dockerfile` + `compose.yaml` in the repo bake the
  full stack (app + FalkorDB, Console v2 built inside the image); any of the
  above can run the same image (see §4).

## 4. Docker Compose (self-host, one command)

`compose.yaml` runs the whole stack: FalkorDB (`v4.22.1`, the pinned test
version) plus the console/agent server — Console v2 built inside the image.

```bash
docker compose up -d --build     # -> http://127.0.0.1:8787/console/
docker compose run --rm app python scripts/ingest.py --repo /data/flask --graph repo:flask
```

`data/` is mounted **read-write** at `/data`: it holds the demo checkout plus
the account store and per-user registry files (`ARCHIVIST_DATA_DIR=/data`).
Any other checkout works read-only with `-v /abs/path:/repo:ro --repo /repo`.
Serve a different graph name with `ARCHIVIST_GRAPH=repo:mytool docker compose up -d`
(queries run live, so re-ingest needs no restart — only a graph *name*
change does). Graph data lives in the `falkordb-data` volume and survives
rebuilds. First run: ingest a repository before exploring — until then the
read APIs report that the graph doesn't exist yet.

Knobs: `ARCHIVIST_PORT` (console host port, default 8787), `FALKORDB_PORT`
(database host port, default 6379 — set it if a local FalkorDB already holds
6379), `ARCHIVIST_GRAPH` / `ARCHIVIST_DEMO_GRAPH` (graphs served),
`ARCHIVIST_SIGNUP=open|closed`, `ARCHIVIST_MAX_REPOS` (default 2). Both
published ports bind to `127.0.0.1` only. The ask room needs a key — `ARCHIVIST_LLM_*` (or
`OPENAI_*`) in `.env` next to `compose.yaml`; without one it stays an honest
503. `serve.py` itself reads `FALKORDB_HOST` / `FALKORDB_PORT` /
`ARCHIVIST_GRAPH` / `ARCHIVIST_BIND` / `ARCHIVIST_PORT` as flag defaults.

## Security notes (honest)

- **Accounts (M6):** anonymous visitors only ever read the shared demo
  graph. Signed-in users can only name repo ids from their own registry;
  raw graph names from a client are refused (404). Passwords are scrypt
  hashes, session tokens are stored as SHA-256 hashes, cookies are
  HttpOnly/SameSite=Lax (+Secure behind an HTTPS proxy), and auth mutations
  require JSON + same-origin. Login/signup are rate-limited per client ip —
  the key is `CF-Connecting-IP` through the tunnel and the socket peer
  otherwise, so exposing the port directly makes that header spoofable.
  Limits are in-process and reset on restart. Graph reads are still
  `ro_query`-only; the web layer's writes are auth state and memory
  episodes/facts, and memory writes are refused for signed-in users on the
  shared demo graph. Accounts data is not encrypted at rest — it is a
  single-box demo, documented rather than pretended otherwise.
- Guards in place: request body cap (1 MB), per-query result caps with
  visible notes, in-flight request cap (`MAX_INFLIGHT = 48`, excess
  connections get `503 + Retry-After` instead of unbounded threads), and a
  15 s socket timeout for stalled clients.
- FalkorDB itself is never exposed: it stays bound to loopback.
- The Ask endpoint returns an honest `503` until an LLM key is configured.
  The public demo currently runs **with** a key (opencode-go) so visitors can
  try Ask live — usage draws on the operator's token pool (48 in-flight cap);
  removing the key returns the demo to keyless mode.
