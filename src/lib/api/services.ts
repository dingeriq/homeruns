// Centralized API service layer for DingerIQ.
// One function per backend endpoint. Backend payloads (FastAPI/Pydantic) are
// adapted here into the DTO shapes the UI already renders, so no component
// changes are needed when the backend schema differs.
//
// Backend contract (development: http://localhost:8000):
//   GET /health              -> { status, service }
//   GET /games/today         -> BackendGame[]
//   GET /players             -> BackendPlayer[]
//   GET /teams               -> BackendTeam[]
//   GET /predictions/today   -> BackendPrediction[]

import { queryOptions } from "@tanstack/react-query";
import { apiFetch } from "./client";
import { mockGamesToday, mockPlayers, mockTeams, mockTopCandidates } from "./mock-data";
import { withFallback } from "./fallback";
import { logApi } from "./log";
import type { RankingDto } from "./queries";

// ---- Frontend DTOs (UI contract — unchanged) ----

export type HealthDto = { status: string; service?: string; version?: string; time?: string };

export type GameSummary = {
  game_id: string;
  date: string;          // ISO date
  first_pitch: string;   // ISO datetime
  home_team: string;
  away_team: string;
  park: string;
  status?: string;
  home_probable_pitcher?: string | null;
  away_probable_pitcher?: string | null;
};

export type PlayerSummary = {
  player_id: string;
  name: string;
  team: string;
  position?: string;
  bats?: string;
  throws?: string;
};

export type Team = {
  team_id: string;
  name: string;
  league: string;
  division: string;
  park: string;
};

// ---- Backend (Pydantic) payload shapes ----

type BackendGame = {
  game_id: number | string;
  game_date: string;
  home_team: string;
  away_team: string;
  venue: string;
  status: string;
  home_probable_pitcher?: string | null;
  away_probable_pitcher?: string | null;
};

type BackendPlayer = {
  id: number | string;
  full_name: string;
  team_id: number | string;
  team_abbreviation: string;
  position: string;
  bats: string;
  throws: string;
};

type BackendTeam = {
  id: number | string;
  abbreviation: string;
  name: string;
  league: string;
  division: string;
};

export type BackendPrediction = {
  player_id: number | string;
  player_name: string;
  game_id: number | string;
  hr_probability: number;
  confidence: number;
};

// ---- Adapters (backend schema -> UI DTO) ----

export function adaptGame(g: BackendGame): GameSummary {
  return {
    game_id: String(g.game_id),
    date: (g.game_date ?? "").slice(0, 10),
    first_pitch: g.game_date,
    home_team: g.home_team,
    away_team: g.away_team,
    park: g.venue,
    status: g.status,
    home_probable_pitcher: g.home_probable_pitcher ?? null,
    away_probable_pitcher: g.away_probable_pitcher ?? null,
  };
}

export function adaptPlayer(p: BackendPlayer): PlayerSummary {
  return {
    player_id: String(p.id),
    name: p.full_name,
    team: p.team_abbreviation,
    position: p.position,
    bats: p.bats,
    throws: p.throws,
  };
}

export function adaptTeam(t: BackendTeam): Team {
  return {
    team_id: t.abbreviation || String(t.id),
    name: t.name,
    league: t.league,
    division: t.division,
    park: "",
  };
}

// ---- Endpoint functions (always live) ----

export const getHealth = () => apiFetch<HealthDto>("/health");

export async function getGamesToday(): Promise<GameSummary[]> {
  const raw = await apiFetch<BackendGame[]>("/games/today");
  const rows = raw.map(adaptGame);
  logApi("GET /games/today", rows.length, "postgres");
  return rows;
}

export async function getPlayers(limit = 500): Promise<PlayerSummary[]> {
  const raw = await apiFetch<BackendPlayer[]>(`/players?limit=${limit}`);
  const rows = raw.map(adaptPlayer);
  logApi("GET /players", rows.length, "postgres");
  return rows;
}

export async function getTeams(): Promise<Team[]> {
  const raw = await apiFetch<BackendTeam[]>("/teams");
  const rows = raw.map(adaptTeam);
  logApi("GET /teams", rows.length, "postgres");
  return rows;
}

export async function getPredictionsToday(): Promise<BackendPrediction[]> {
  const raw = await apiFetch<BackendPrediction[]>("/predictions/today");
  logApi("GET /predictions/today", raw.length, "postgres");
  return raw;
}

/** Probable starters for today, derived from the games table. */
export async function getProbablePitchers(): Promise<{ game_id: string; team: string; pitcher: string }[]> {
  const games = await getGamesToday();
  const out: { game_id: string; team: string; pitcher: string }[] = [];
  for (const g of games) {
    if (g.home_probable_pitcher) out.push({ game_id: g.game_id, team: g.home_team, pitcher: g.home_probable_pitcher });
    if (g.away_probable_pitcher) out.push({ game_id: g.game_id, team: g.away_team, pitcher: g.away_probable_pitcher });
  }
  logApi("derived: probable pitchers", out.length, "postgres");
  return out;
}

// ---- React Query option factories (mock only while demo mode is active) ----

export const gamesTodayQuery = () =>
  queryOptions({
    queryKey: ["games", "today"],
    queryFn: () => withFallback("GET /games/today", getGamesToday, mockGamesToday),
    staleTime: 60_000,
  });

export const playersQuery = () =>
  queryOptions({
    queryKey: ["players"],
    queryFn: () => withFallback("GET /players", () => getPlayers(), mockPlayers),
    staleTime: 5 * 60_000,
  });

export const teamsQuery = () =>
  queryOptions({
    queryKey: ["teams"],
    queryFn: () => withFallback("GET /teams", getTeams, mockTeams),
    staleTime: 60 * 60_000,
  });

export const predictionsTodayQuery = () =>
  queryOptions({
    queryKey: ["predictions", "today", "all"],
    queryFn: () =>
      withFallback<RankingDto[]>(
        "GET /predictions/today",
        async () => {
          const [preds, games] = await Promise.all([getPredictionsToday(), getGamesToday()]);
          return adaptPredictions(preds, games);
        },
        () => mockTopCandidates(25),
      ),
    staleTime: 60_000,
  });

/** Map backend predictions + today's games into the ranking rows the UI renders.
 *  `players` (optional) resolves each batter's real team by player_id — the
 *  prediction payload has no team field, so without it we can only fall back
 *  to the game's home team. */
export function adaptPredictions(
  preds: BackendPrediction[],
  games: GameSummary[],
  players?: PlayerSummary[],
): RankingDto[] {
  const byGame = new Map(games.map((g) => [g.game_id, g]));
  const teamByPlayer = new Map((players ?? []).map((p) => [p.player_id, p.team]));
  return [...preds]
    .sort((a, b) => b.hr_probability - a.hr_probability)
    .map((p, i) => {
      const g = byGame.get(String(p.game_id));
      const team = teamByPlayer.get(String(p.player_id)) ?? g?.home_team ?? "";
      const isAway = !!g && team === g.away_team;
      const opp = g ? (isAway ? g.home_team : g.away_team) : "";
      const pitcher =
        (isAway ? g?.home_probable_pitcher : g?.away_probable_pitcher) ??
        g?.home_probable_pitcher ??
        g?.away_probable_pitcher ??
        "TBD";
      return {
        rank: i + 1,
        player_id: String(p.player_id),
        player: p.player_name,
        team,
        opp,
        pitcher,
        park: g?.park ?? "",
        p_hr: p.hr_probability,
        confidence: p.confidence,
        drivers: [],
        negatives: [],
      };
    });
}

