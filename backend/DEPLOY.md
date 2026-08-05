# Deploying the DingerIQ backend to Railway

## Deploying from GitHub (recommended)

1. Railway → New Project → Deploy from GitHub repo.
2. Service Settings → **Root Directory** = `backend`.
3. Railway reads `backend/railway.json` + `backend/Procfile` + `backend/nixpacks.toml`,
   so the start command is detected automatically:
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add Postgres (`+ New` → Database → Postgres) — `DATABASE_URL` is injected.
5. Set the variables in step 3 below, then Deploy. Healthcheck hits `/health`.

## CLI alternative

Everything in this folder is Railway-ready. You run these commands (Railway
requires your own account/credentials, so this step can't be automated from
Lovable).


## 1. Install + login

```bash
npm i -g @railway/cli
railway login
```

## 2. Create the project and Postgres

```bash
cd backend
railway init                 # name it "dingeriq-api"
railway add --database postgres
```

Railway automatically exposes `DATABASE_URL` to the service. The app
normalizes `postgres://` / `postgresql://` to the psycopg3 driver, so no
editing is needed.

## 3. Set environment variables

```bash
railway variables \
  --set "ENVIRONMENT=production" \
  --set "MLB_SEASON=2026" \
  --set "STATCAST_LOOKBACK_DAYS=2" \
  --set "STATCAST_REFRESH_HOUR=9" \
  --set "STATCAST_REFRESH_MINUTE=30" \
  --set "DAILY_REFRESH_HOUR=8" \
  --set "DAILY_REFRESH_MINUTE=0" \
  --set "OPENWEATHER_API_KEY=<your key>" \
  --set "ODDS_API_KEY=<your key>" \
  --set "CORS_ORIGINS=https://homeruns.lovable.app"
```

`DATABASE_URL` is injected by the Postgres plugin — do not set it manually.
`OPENWEATHER_API_KEY` / `ODDS_API_KEY` are optional today (no weather/odds
ingestion is wired yet) but are read from the environment when added.

## 4. Deploy + get the URL

```bash
railway up
railway domain          # prints e.g. https://dingeriq-api-production.up.railway.app
```

`/health` returns 200 immediately once uvicorn is accepting requests — it never
waits on the database, MLB sync, Statcast, or the scheduler (all of that runs in
a background task after startup). Use `/ready` (200 when Postgres answers, 503
otherwise, plus sync progress) for readiness. Railway's healthcheck stays on
`/health`.


## 5. Verify the endpoints

```bash
API=https://<your-railway-domain>
curl -s $API/health
curl -s "$API/games/today"
curl -s "$API/teams" | head -c 400
curl -s "$API/players?limit=5"
curl -s -X POST $API/admin/sync
curl -s -X POST $API/admin/statcast-sync
```

## 6. Point the frontend at it

Either paste the URL into the **Connect** field in the demo banner (instant,
no rebuild), or set it permanently in the project root `.env`:

```
VITE_API_BASE_URL=https://<your-railway-domain>
```

Once `GET /health` returns 200, the health monitor exits Demo Mode
automatically within 30s and re-fetches every query against live Postgres data.

---

# Alternative hosts

## Fly.io

```bash
cd backend && fly launch --copy-config && fly postgres create && fly deploy
```

## Render

`render.yaml` in this folder is a ready blueprint — point Render at the repo.

## Quick tunnel (testing only)

```bash
cloudflared tunnel --url http://localhost:8000
```
