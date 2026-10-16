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
(`scripts/crew.py`), web console. The console is read-only over the graph;
the Ask panel needs an LLM key (any OpenAI-compatible endpoint) in `.env`.

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

Repositories under `data/` are mounted read-only at `/data` for ingesting;
any other checkout works with `-v /abs/path:/repo:ro --repo /repo`. Serve a
different graph name with `ARCHIVIST_GRAPH=repo:mytool docker compose up -d`
(queries run live, so re-ingest needs no restart — only a graph *name*
change does). Graph data lives in the `falkordb-data` volume and survives
rebuilds. First run: ingest a repository before exploring — until then the
read APIs report that the graph doesn't exist yet.

Knobs: `ARCHIVIST_PORT` (console host port, default 8787), `FALKORDB_PORT`
(database host port, default 6379 — set it if a local FalkorDB already holds
6379), `ARCHIVIST_GRAPH` (graph served). Both published ports bind to
`127.0.0.1` only. The ask room needs a key — `ARCHIVIST_LLM_*` (or
`OPENAI_*`) in `.env` next to `compose.yaml`; without one it stays an honest
503. `serve.py` itself reads `FALKORDB_HOST` / `FALKORDB_PORT` /
`ARCHIVIST_GRAPH` / `ARCHIVIST_BIND` / `ARCHIVIST_PORT` as flag defaults.

## Security notes (honest)

- The console has **no authentication by design**: it is a local,
  single-user tool. Before pointing a tunnel at a *private* repo graph, add
  auth; the public demo only ever exposes the OSS flask graph.
- Read-only: every endpoint is a projection over `ro_query`; there is no
  write path through the web layer.
- Guards in place: request body cap (1 MB), per-query result caps with
  visible notes, in-flight request cap (`MAX_INFLIGHT = 48`, excess
  connections get `503 + Retry-After` instead of unbounded threads), and a
  15 s socket timeout for stalled clients.
- FalkorDB itself is never exposed: it stays bound to loopback.
- The Ask endpoint returns an honest `503` until an LLM key is configured.
  The public demo currently runs **with** a key (opencode-go) so visitors can
  try Ask live — usage draws on the operator's token pool (48 in-flight cap);
  removing the key returns the demo to keyless mode.
