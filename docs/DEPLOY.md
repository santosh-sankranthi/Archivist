# Deploying Archivist

Three ways to run or reach Archivist, from "reproduce on your machine" to
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
- **Container image** — a `Dockerfile` + `compose.yaml` bake (app + FalkorDB)
  is the M5 next step; it makes any of the above one command.

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
- The Ask endpoint returns an honest `503` until an LLM key is configured —
  the public demo runs without one, so it cannot spend anyone's tokens.
