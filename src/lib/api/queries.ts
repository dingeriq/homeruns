import { queryOptions } from "@tanstack/react-query";
import { apiFetch } from "./client";
import {
  mockBacktest,
  mockFeaturedMatchup,
  mockFeatureImportance,
  mockModelPerformance,
  mockPlayer,
  mockSlateSummary,
  mockTopCandidates,
} from "./mock-data";

// Response types mirror the Phase 10 FastAPI service.
export type RankingDto = {
  rank: number;
  player_id: string;
  player: string;
  team: string;
  opp: string;
  pitcher: string;
  park: string;
  p_hr: number;
  confidence: number;
  drivers: { name: string; value: number }[];
  negatives: { name: string; value: number }[];
};

export type PlayerDto = RankingDto & {
  stats: {
    barrel_14: number;
    barrel_30: number;
    hard_hit: number;
    exit_velo: number;
    fly_ball: number;
    pull: number;
    iso: number;
    hr_per_pa: number;
    x_slg: number;
    x_woba: number;
  };
  last_30: { day: number; p_hr: number; actual: number }[];
};

export type GameDto = {
  game_id: string;
  home: string;
  away: string;
  park: string;
  first_pitch: string;
  weather: { temp_f: number; humidity: number; wind_mph: number; wind_dir: string; conditions: string };
  vegas: { total: number; home_total: number; away_total: number };
  batter: RankingDto & { hand: "L" | "R"; order: number; vs_hand_woba: number; vs_hand_iso: number };
  pitcher: { name: string; hand: "L" | "R"; hr_per_9: number; barrel_pct_allowed: number; fb_pct_allowed: number; season_x_era: number };
  park_factors: { hr_factor_r: number; hr_factor_l: number };
  pitch_types: { pitch: string; batter_x_slg: number; league_x_slg: number }[];
};

export type ModelPerformanceDto = {
  models: { name: string; log_loss: number; brier: number; roc_auc: number; pr_auc: number; ece: number }[];
  calibration: { predicted: number; observed: number }[];
  rolling_auc: { week: string; auc: number; log_loss: number }[];
  headline: { roc_auc: number; pr_auc: number; log_loss: number; brier: number; ece: number; champion: string };
};

export type FeatureImportanceDto = {
  feature: string;
  group: string;
  gain: number;
  permutation: number;
  shap: number;
}[];

export type BacktestDto = {
  months: { label: string; month: string; year: number; hit_rate: number; roi: number; bets: number }[];
  summary: { total_bets: number; hit_rate: number; roi: number; sharpe: number; max_drawdown: number };
};

export type SlateSummaryDto = {
  games: number;
  hitters_scored: number;
  top_prob: number;
  top_prob_player: string;
  avg_confidence: number;
};

// Every queryFn transparently falls back to mock data when the API is down.
// `apiFetch` calls `enableDemoMode(...)` on failure, so the banner/badge react
// automatically. The UI stays fully functional in Demo Mode.
async function withFallback<T>(fn: () => Promise<T>, fallback: () => T): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    console.warn("[api] falling back to mock data:", err);
    return fallback();
  }
}

// ---- Query option factories ----

export const topCandidatesQuery = (limit = 25) =>
  queryOptions({
    queryKey: ["predictions", "today", { limit }],
    queryFn: () =>
      withFallback(
        () => apiFetch<RankingDto[]>(`/top-home-run-candidates?limit=${limit}`),
        () => mockTopCandidates(limit),
      ),
    staleTime: 60_000,
  });

export const slateSummaryQuery = () =>
  queryOptions({
    queryKey: ["predictions", "slate-summary"],
    queryFn: () =>
      withFallback(
        () => apiFetch<SlateSummaryDto>("/predictions/today/summary"),
        () => mockSlateSummary(),
      ),
    staleTime: 60_000,
  });

export const playerQuery = (playerId: string) =>
  queryOptions({
    queryKey: ["player", playerId],
    queryFn: () =>
      withFallback(
        () => apiFetch<PlayerDto>(`/player/${encodeURIComponent(playerId)}`),
        () => mockPlayer(playerId),
      ),
    staleTime: 60_000,
  });

export const featuredMatchupQuery = () =>
  queryOptions({
    queryKey: ["predictions", "featured-matchup"],
    queryFn: () =>
      withFallback(
        () => apiFetch<GameDto>("/predictions/game/featured"),
        () => mockFeaturedMatchup(),
      ),
    staleTime: 60_000,
  });

export const modelPerformanceQuery = () =>
  queryOptions({
    queryKey: ["model", "performance"],
    queryFn: () =>
      withFallback(
        () => apiFetch<ModelPerformanceDto>("/model/performance"),
        () => mockModelPerformance(),
      ),
    staleTime: 5 * 60_000,
  });

export const featureImportanceQuery = () =>
  queryOptions({
    queryKey: ["model", "feature-importance"],
    queryFn: () =>
      withFallback(
        () => apiFetch<FeatureImportanceDto>("/model/feature-importance"),
        () => mockFeatureImportance(),
      ),
    staleTime: 10 * 60_000,
  });

export const backtestQuery = () =>
  queryOptions({
    queryKey: ["model", "backtest"],
    queryFn: () =>
      withFallback(
        () => apiFetch<BacktestDto>("/model/backtest"),
        () => mockBacktest(),
      ),
    staleTime: 10 * 60_000,
  });
