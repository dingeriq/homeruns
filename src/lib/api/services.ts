// Centralized API service layer for DingerIQ.
// One function per backend endpoint. All calls go through `apiFetch`, which
// automatically flips the app into demo mode on failure. Callers should use
// `withFallback` (see ./queries) or handle errors as needed.
//
// Backend contract (development: http://localhost:8000):
//   GET /health              -> { status: "ok" }
//   GET /games/today         -> GameSummary[]
//   GET /players             -> PlayerSummary[]
//   GET /teams               -> Team[]
//   GET /predictions/today   -> RankingDto[]  (see ./queries)

import { queryOptions } from "@tanstack/react-query";
import { apiFetch } from "./client";
import { mockGamesToday, mockPlayers, mockTeams, mockTopCandidates } from "./mock-data";
import type { RankingDto } from "./queries";

// ---- Response DTOs ----

export type HealthDto = { status: string; version?: string; time?: string };

export type GameSummary = {
  game_id: string;
  date: string;          // ISO date
  first_pitch: string;   // ISO datetime
  home_team: string;     // team code, e.g. "NYY"
  away_team: string;
  park: string;
  status?: "scheduled" | "in_progress" | "final";
};

export type PlayerSummary = {
  player_id: string;
  name: string;
  team: string;
  position?: string;
  bats?: "L" | "R" | "S";
  throws?: "L" | "R";
};

export type Team = {
  team_id: string;   // e.g. "NYY"
  name: string;      // "New York Yankees"
  league: "AL" | "NL";
  division: "E" | "C" | "W";
  park: string;
};

// ---- Endpoint functions ----

async function withFallback<T>(fn: () => Promise<T>, fallback: () => T): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    console.warn("[api] falling back to mock data:", err);
    return fallback();
  }
}

export const getHealth = () => apiFetch<HealthDto>("/health");
export const getGamesToday = () => apiFetch<GameSummary[]>("/games/today");
export const getPlayers = () => apiFetch<PlayerSummary[]>("/players");
export const getTeams = () => apiFetch<Team[]>("/teams");
export const getPredictionsToday = () =>
  apiFetch<RankingDto[]>("/predictions/today");

// ---- React Query option factories (with mock fallback) ----

export const gamesTodayQuery = () =>
  queryOptions({
    queryKey: ["games", "today"],
    queryFn: () => withFallback(getGamesToday, mockGamesToday),
    staleTime: 60_000,
  });

export const playersQuery = () =>
  queryOptions({
    queryKey: ["players"],
    queryFn: () => withFallback(getPlayers, mockPlayers),
    staleTime: 5 * 60_000,
  });

export const teamsQuery = () =>
  queryOptions({
    queryKey: ["teams"],
    queryFn: () => withFallback(getTeams, mockTeams),
    staleTime: 60 * 60_000,
  });

export const predictionsTodayQuery = () =>
  queryOptions({
    queryKey: ["predictions", "today", "all"],
    queryFn: () => withFallback(getPredictionsToday, () => mockTopCandidates(25)),
    staleTime: 60_000,
  });
