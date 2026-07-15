// Mock data for MLB HR prediction dashboard.
// In production these come from the FastAPI service (Phase 10).

export type Ranking = {
  rank: number;
  playerId: string;
  player: string;
  team: string;
  opp: string;
  pitcher: string;
  park: string;
  pHr: number;
  confidence: number;
  drivers: { name: string; value: number }[];
  negatives: { name: string; value: number }[];
};

export const rankings: Ranking[] = [
  { rank: 1, playerId: "judge-aaron", player: "Aaron Judge", team: "NYY", opp: "BOS", pitcher: "Kutter Crawford", park: "Yankee Stadium", pHr: 0.284, confidence: 0.91, drivers: [{ name: "Barrel% 14d", value: 0.052 }, { name: "Park HR factor", value: 0.038 }, { name: "Pitcher HR/9", value: 0.031 }, { name: "Wind to CF", value: 0.022 }, { name: "xwOBA 30d", value: 0.019 }], negatives: [{ name: "vs RHP wOBA", value: -0.009 }, { name: "Humidity", value: -0.006 }, { name: "Batting order 3", value: -0.003 }] },
  { rank: 2, playerId: "ohtani-shohei", player: "Shohei Ohtani", team: "LAD", opp: "SF", pitcher: "Logan Webb", park: "Dodger Stadium", pHr: 0.241, confidence: 0.88, drivers: [{ name: "ISO 30d", value: 0.044 }, { name: "Exit Velo 14d", value: 0.036 }, { name: "Pull% FB", value: 0.028 }, { name: "xSLG 30d", value: 0.024 }, { name: "Hard Hit%", value: 0.021 }], negatives: [{ name: "Pitcher GB%", value: -0.014 }, { name: "Wind in", value: -0.008 }] },
  { rank: 3, playerId: "alonso-pete", player: "Pete Alonso", team: "NYM", opp: "PHI", pitcher: "Ranger Suárez", park: "Citi Field", pHr: 0.226, confidence: 0.82, drivers: [{ name: "HR/PA 60d", value: 0.041 }, { name: "vs LHP ISO", value: 0.033 }, { name: "Fly Ball%", value: 0.027 }, { name: "Barrel% 7d", value: 0.023 }], negatives: [{ name: "Park HR factor", value: -0.011 }, { name: "Temp 62°F", value: -0.007 }] },
  { rank: 4, playerId: "schwarber-kyle", player: "Kyle Schwarber", team: "PHI", opp: "NYM", pitcher: "Sean Manaea", park: "Citi Field", pHr: 0.218, confidence: 0.79, drivers: [{ name: "vs LHP ISO", value: 0.048 }, { name: "Pull% FB", value: 0.031 }, { name: "Barrel% 30d", value: 0.024 }], negatives: [{ name: "Park HR factor", value: -0.013 }] },
  { rank: 5, playerId: "soto-juan", player: "Juan Soto", team: "NYY", opp: "BOS", pitcher: "Kutter Crawford", park: "Yankee Stadium", pHr: 0.209, confidence: 0.86, drivers: [{ name: "xwOBA 30d", value: 0.039 }, { name: "Park HR factor", value: 0.034 }, { name: "Wind to CF", value: 0.021 }], negatives: [{ name: "GB% recent", value: -0.010 }] },
  { rank: 6, playerId: "acuna-ronald", player: "Ronald Acuña Jr.", team: "ATL", opp: "MIA", pitcher: "Jesús Luzardo", park: "Truist Park", pHr: 0.198, confidence: 0.74, drivers: [{ name: "Exit Velo 14d", value: 0.033 }, { name: "HR/PA 60d", value: 0.028 }], negatives: [{ name: "vs LHP wOBA", value: -0.012 }] },
  { rank: 7, playerId: "harper-bryce", player: "Bryce Harper", team: "PHI", opp: "NYM", pitcher: "Sean Manaea", park: "Citi Field", pHr: 0.191, confidence: 0.81, drivers: [{ name: "Barrel% 14d", value: 0.029 }, { name: "xSLG 30d", value: 0.024 }], negatives: [{ name: "Wind in", value: -0.009 }] },
  { rank: 8, playerId: "riley-austin", player: "Austin Riley", team: "ATL", opp: "MIA", pitcher: "Jesús Luzardo", park: "Truist Park", pHr: 0.184, confidence: 0.72, drivers: [{ name: "Pull% FB", value: 0.026 }, { name: "Hard Hit%", value: 0.021 }], negatives: [] },
  { rank: 9, playerId: "olson-matt", player: "Matt Olson", team: "ATL", opp: "MIA", pitcher: "Jesús Luzardo", park: "Truist Park", pHr: 0.176, confidence: 0.77, drivers: [{ name: "Barrel% 30d", value: 0.028 }], negatives: [{ name: "vs LHP wOBA", value: -0.011 }] },
  { rank: 10, playerId: "guerrero-vlad", player: "Vladimir Guerrero Jr.", team: "TOR", opp: "TB", pitcher: "Zack Littell", park: "Rogers Centre", pHr: 0.168, confidence: 0.7, drivers: [{ name: "xwOBA 30d", value: 0.024 }], negatives: [] },
  { rank: 11, playerId: "witt-bobby", player: "Bobby Witt Jr.", team: "KC", opp: "CLE", pitcher: "Tanner Bibee", park: "Kauffman Stadium", pHr: 0.161, confidence: 0.68, drivers: [{ name: "Exit Velo 14d", value: 0.022 }], negatives: [{ name: "Park HR factor", value: -0.018 }] },
  { rank: 12, playerId: "trout-mike", player: "Mike Trout", team: "LAA", opp: "SEA", pitcher: "Luis Castillo", park: "Angel Stadium", pHr: 0.155, confidence: 0.66, drivers: [{ name: "Barrel% 7d", value: 0.02 }], negatives: [] },
  { rank: 13, playerId: "devers-rafael", player: "Rafael Devers", team: "BOS", opp: "NYY", pitcher: "Gerrit Cole", park: "Yankee Stadium", pHr: 0.149, confidence: 0.73, drivers: [{ name: "Park HR factor", value: 0.028 }], negatives: [{ name: "vs RHP wOBA", value: -0.008 }] },
  { rank: 14, playerId: "seager-corey", player: "Corey Seager", team: "TEX", opp: "HOU", pitcher: "Framber Valdez", park: "Minute Maid Park", pHr: 0.144, confidence: 0.71, drivers: [], negatives: [{ name: "Pitcher GB%", value: -0.019 }] },
  { rank: 15, playerId: "tucker-kyle", player: "Kyle Tucker", team: "HOU", opp: "TEX", pitcher: "Nathan Eovaldi", park: "Minute Maid Park", pHr: 0.139, confidence: 0.75, drivers: [{ name: "Pull% FB", value: 0.022 }], negatives: [] },
  { rank: 16, playerId: "buxton-byron", player: "Byron Buxton", team: "MIN", opp: "CWS", pitcher: "Garrett Crochet", park: "Target Field", pHr: 0.134, confidence: 0.62, drivers: [{ name: "Exit Velo 14d", value: 0.019 }], negatives: [] },
  { rank: 17, playerId: "raleigh-cal", player: "Cal Raleigh", team: "SEA", opp: "LAA", pitcher: "Reid Detmers", park: "Angel Stadium", pHr: 0.129, confidence: 0.69, drivers: [{ name: "vs LHP ISO", value: 0.021 }], negatives: [] },
  { rank: 18, playerId: "henderson-gunnar", player: "Gunnar Henderson", team: "BAL", opp: "TB", pitcher: "Shane Baz", park: "Camden Yards", pHr: 0.124, confidence: 0.67, drivers: [], negatives: [] },
  { rank: 19, playerId: "yordan-alvarez", player: "Yordan Álvarez", team: "HOU", opp: "TEX", pitcher: "Nathan Eovaldi", park: "Minute Maid Park", pHr: 0.121, confidence: 0.78, drivers: [{ name: "xwOBA 30d", value: 0.026 }], negatives: [] },
  { rank: 20, playerId: "betts-mookie", player: "Mookie Betts", team: "LAD", opp: "SF", pitcher: "Logan Webb", park: "Dodger Stadium", pHr: 0.118, confidence: 0.72, drivers: [], negatives: [{ name: "Pitcher GB%", value: -0.013 }] },
  { rank: 21, playerId: "carroll-corbin", player: "Corbin Carroll", team: "ARI", opp: "SD", pitcher: "Yu Darvish", park: "Petco Park", pHr: 0.114, confidence: 0.6, drivers: [], negatives: [{ name: "Park HR factor", value: -0.022 }] },
  { rank: 22, playerId: "machado-manny", player: "Manny Machado", team: "SD", opp: "ARI", pitcher: "Zac Gallen", park: "Petco Park", pHr: 0.11, confidence: 0.64, drivers: [], negatives: [] },
  { rank: 23, playerId: "cruz-oneil", player: "Oneil Cruz", team: "PIT", opp: "CIN", pitcher: "Hunter Greene", park: "Great American Ball Park", pHr: 0.107, confidence: 0.7, drivers: [{ name: "Park HR factor", value: 0.031 }, { name: "Exit Velo 14d", value: 0.024 }], negatives: [] },
  { rank: 24, playerId: "de-la-cruz-elly", player: "Elly De La Cruz", team: "CIN", opp: "PIT", pitcher: "Paul Skenes", park: "Great American Ball Park", pHr: 0.104, confidence: 0.65, drivers: [{ name: "Park HR factor", value: 0.03 }], negatives: [{ name: "Pitcher K%", value: -0.017 }] },
  { rank: 25, playerId: "chourio-jackson", player: "Jackson Chourio", team: "MIL", opp: "STL", pitcher: "Sonny Gray", park: "American Family Field", pHr: 0.101, confidence: 0.58, drivers: [], negatives: [] },
];

export const modelPerformance = {
  models: [
    { name: "XGBoost", logLoss: 0.284, brier: 0.081, rocAuc: 0.742, prAuc: 0.187, ece: 0.014 },
    { name: "LightGBM", logLoss: 0.286, brier: 0.082, rocAuc: 0.739, prAuc: 0.184, ece: 0.015 },
    { name: "CatBoost", logLoss: 0.285, brier: 0.081, rocAuc: 0.741, prAuc: 0.186, ece: 0.013 },
    { name: "Random Forest", logLoss: 0.298, brier: 0.086, rocAuc: 0.712, prAuc: 0.161, ece: 0.028 },
    { name: "Logistic Reg.", logLoss: 0.305, brier: 0.089, rocAuc: 0.694, prAuc: 0.148, ece: 0.022 },
  ],
  calibration: Array.from({ length: 10 }, (_, i) => {
    const bin = (i + 0.5) / 10;
    return { predicted: bin, observed: Math.max(0, Math.min(1, bin + (Math.sin(i) * 0.02))) };
  }),
  rollingAuc: Array.from({ length: 24 }, (_, i) => ({
    week: `W${i + 1}`,
    auc: 0.72 + Math.sin(i / 3) * 0.02 + Math.random() * 0.01,
    logLoss: 0.29 + Math.cos(i / 4) * 0.01,
  })),
};

export const featureImportance = [
  { feature: "Barrel% 14d", group: "Hitter", gain: 0.128, permutation: 0.041, shap: 0.052 },
  { feature: "Park HR factor", group: "Ballpark", gain: 0.101, permutation: 0.033, shap: 0.044 },
  { feature: "Pitcher HR/9", group: "Pitcher", gain: 0.094, permutation: 0.03, shap: 0.041 },
  { feature: "xwOBA 30d", group: "Hitter", gain: 0.087, permutation: 0.028, shap: 0.038 },
  { feature: "Wind to CF (mph)", group: "Weather", gain: 0.072, permutation: 0.024, shap: 0.032 },
  { feature: "Exit Velo 14d", group: "Hitter", gain: 0.068, permutation: 0.022, shap: 0.03 },
  { feature: "Barrel% allowed 30d", group: "Pitcher", gain: 0.061, permutation: 0.019, shap: 0.027 },
  { feature: "vs Pitcher-hand ISO", group: "Matchup", gain: 0.058, permutation: 0.018, shap: 0.025 },
  { feature: "Pull% FB", group: "Hitter", gain: 0.052, permutation: 0.016, shap: 0.023 },
  { feature: "Fly Ball% allowed", group: "Pitcher", gain: 0.047, permutation: 0.014, shap: 0.021 },
  { feature: "Vegas team total", group: "Vegas", gain: 0.044, permutation: 0.013, shap: 0.02 },
  { feature: "HR/PA 60d", group: "Hitter", gain: 0.041, permutation: 0.012, shap: 0.019 },
  { feature: "Temperature (°F)", group: "Weather", gain: 0.036, permutation: 0.011, shap: 0.017 },
  { feature: "Batting order slot", group: "Opportunity", gain: 0.033, permutation: 0.01, shap: 0.015 },
  { feature: "Expected PA", group: "Opportunity", gain: 0.031, permutation: 0.009, shap: 0.014 },
  { feature: "Pitch-type xSLG", group: "Matchup", gain: 0.028, permutation: 0.008, shap: 0.013 },
  { feature: "Humidity", group: "Weather", gain: 0.021, permutation: 0.006, shap: 0.01 },
  { feature: "Hard Hit% 14d", group: "Hitter", gain: 0.019, permutation: 0.006, shap: 0.009 },
];

export const backtest = {
  months: Array.from({ length: 12 }, (_, i) => {
    const roi = -5 + Math.sin(i / 2) * 8 + i * 0.7;
    return {
      month: ["Apr","May","Jun","Jul","Aug","Sep","Apr","May","Jun","Jul","Aug","Sep"][i],
      year: i < 6 ? 2024 : 2025,
      label: `${["Apr","May","Jun","Jul","Aug","Sep"][i % 6]} ${i < 6 ? "'24" : "'25"}`,
      hitRate: 0.18 + Math.sin(i / 3) * 0.04,
      roi,
      bets: 240 + Math.floor(Math.random() * 60),
    };
  }),
  summary: {
    totalBets: 3120,
    hitRate: 0.204,
    roi: 12.4,
    sharpe: 1.38,
    maxDrawdown: -8.6,
  },
};

export function getPlayer(id: string) {
  const p = rankings.find((r) => r.playerId === id) ?? rankings[0];
  return {
    ...p,
    stats: {
      barrel14: 0.185, barrel30: 0.172, hardHit: 0.512, exitVelo: 92.4,
      flyBall: 0.44, pull: 0.41, iso: 0.284, hrPerPa: 0.061, xSlg: 0.581, xwOba: 0.402,
    },
    last30: Array.from({ length: 30 }, (_, i) => ({
      day: i + 1,
      pHr: 0.09 + Math.sin(i / 4) * 0.04 + Math.random() * 0.02,
      actual: Math.random() > 0.85 ? 1 : 0,
    })),
  };
}
