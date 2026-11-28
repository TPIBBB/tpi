// 對應後端 world-api 的 /world/* 回傳格式。
// 後端改格式時要同步更新這裡（後端：src/civsim/api/server.py、src/civsim/analysis/metrics.py）。

export type CitizenType = "producer_trader" | "producer_self" | "consumer" | "dependent";
export type Phase = "stable" | "disaster" | "evaluation";

export interface CitizenView {
  id: string;
  type: CitizenType;
  alive: boolean;
  food: number;
  money: number;
  hungry_days: number;
}

export interface EquilibriumBand {
  mean: number;
  std: number;
  low: number;
  high: number;
  k: number;
  samples: number;
}

export interface WorldState {
  tick: number;
  phase: Phase;
  disaster_id: number | null;
  market: {
    price: number;
    customary_price: number;
    price_cap: number | null;
    price_cap_until: number | null;
  };
  government: {
    budget: number;
    food_reserve: number;
  };
  band: EquilibriumBand | null;
  citizens: CitizenView[];
}

export interface TickMetrics {
  tick: number;
  day: number;
  disaster_id: number | null;
  phase: Phase;
  food_multiplier: number;
  price: number;
  traded: boolean;
  volume: number;
  demand: number;
  supply: number;
  price_capped: boolean;
  customary_price: number;
  alive: number;
  hungry: number;
  deaths: number;
  citizen_food: number;
  citizen_money: number;
  money_gini: number;
  gov_budget: number;
  gov_reserve: number;
  gov_accepted: number;
  gov_rejected: number;
  rejected_actions: number;
}

export interface SimEvent {
  tick: number;
  kind: string; // action:buy / world:disaster_start / gov:release_reserve ...
  actor: string | null;
  success: boolean;
  payload: Record<string, unknown>;
  message: string;
}

export const PHASE_LABEL: Record<Phase, string> = {
  stable: "平穩期",
  disaster: "天災期",
  evaluation: "評估期",
};

// ---- 實驗紀錄 disasters.jsonl：每次天災一筆報告 ----
export interface GovDecision {
  tick: number;
  type: string;
  params: Record<string, unknown>;
  reason: string;
  replayed: boolean;
  accepted: boolean;
  result?: Record<string, unknown>;
  rejected_reason?: string;
}

export interface DisasterReport {
  disaster_id: number;
  kind: string;
  window_start: number;
  window_end: number;
  band: EquilibriumBand;
  success: boolean;
  recovered_day: number | null;
  capped_days: number;
  peak_price: number;
  hungry_person_days: number;
  deaths: number;
  decisions: GovDecision[];
  memory_version?: number;
}

export const DECISION_LABEL: Record<string, string> = {
  idle: "不行動",
  distribute_food: "發放糧食",
  release_reserve: "釋出儲備糧",
  buy_reserve: "買進儲備糧",
  import_food: "進口糧食",
  cash_transfer: "發放現金",
  price_cap: "設定價格上限",
  collect_tax: "收稅",
  issue_currency: "發行貨幣",
  seize_food: "徵收糧食",
  execute_citizen: "處決平民",
};