# Deploying the DingerIQ backend

The hosted dashboard (`https://homeruns.lovable.app`) runs in the visitor's
browser. `http://localhost:8000` therefore resolves to **their** machine, not
your laptop — and an HTTPS page is not allowed to call a plain HTTP address
(mixed content). The backend must be reachable at a public **HTTPS** URL.

## Option A — Fly.io (recommended)

```bash
cd backend
fly launch --no-deploy --copy-config --name dingeriq-api
fly postgres create --name dingeriq-db
fly postgres attach dingeriq-db          # sets DATABASE_URL
fly secrets set CORS_ORIGINS=https://homeruns.lovable.app
fly deploy
curl https://dingeriq-api.fly.dev/health
```

Note: `DATABASE_URL` from Fly starts with `postgres://`; the app expects
`postgresql+psycopg://`. Set it explicitly if needed:

```bash
fly secrets set DATABASE_URL='postgresql+psycopg://USER:PASS@dingeriq-db.flycast:5432/dingeriq'
```

## Option B — Render

Push the repo to GitHub, then **New → Blueprint** and pick it. `render.yaml`
provisions the web service and Postgres. Public URL:
`https://dingeriq-api.onrender.com`.

## Option C — AWS ECS/Fargate

Use the Phase 12 Terraform (`mlb_deploy.zip`): VPC, Multi-AZ RDS, ALB with
ACM certificate, Fargate service running this same image.

## Option D — Temporary tunnel (testing only)

```bash
cloudflared tunnel --url http://localhost:8000
# → https://random-words.trycloudflare.com
```

Add that origin handling by setting `CORS_ORIGINS` to your frontend URL and
restarting uvicorn.

## Point the frontend at it

1. Set `VITE_API_BASE_URL=https://dingeriq-api.fly.dev` in the project env and
   republish, **or**
2. Paste the URL into the input in the yellow "demo mode" banner — it is stored
   in `localStorage` and takes effect immediately, no rebuild.

Once `GET /health` returns 200, the health monitor calls `disableDemoMode()`,
invalidates every query, and the dashboard switches to live data automatically.

## CORS

`app/main.py` allows the origins in `CORS_ORIGINS` plus any `*.lovable.app` /
`*.lovableproject.com` host via regex, so preview and published URLs both work.
