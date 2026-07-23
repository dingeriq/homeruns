# DingerIQ Backend

FastAPI foundation for the DingerIQ MLB home run prediction platform.

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Run with Docker

```bash
cd backend
docker compose up --build
```

API is served at http://localhost:8000. Interactive docs at `/docs`.

## Endpoints

| Method | Path                | Description                              |
| ------ | ------------------- | ---------------------------------------- |
| GET    | `/health`           | Service health check                     |
| GET    | `/games/today`      | Today's MLB games (placeholder data)     |
| GET    | `/players`          | MLB players (placeholder data)           |
| GET    | `/teams`            | MLB teams (placeholder data)             |
| GET    | `/predictions/today`| Empty array — model output lands later   |

## Structure

```
backend/
├── app/
│   ├── main.py            # FastAPI app, middleware, exception handlers
│   ├── config.py          # Env-driven settings
│   ├── logging_config.py  # Structured logging
│   ├── api/               # Routers (health, games, players, teams, predictions)
│   ├── services/          # Placeholder data providers
│   ├── database/          # DB session stub for future PostgreSQL wiring
│   └── models/            # Pydantic schemas
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```
