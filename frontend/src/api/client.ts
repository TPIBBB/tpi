import type { DisasterReport, EquilibriumBand, SimEvent, TickMetrics, WorldState } from "./types";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path} 回傳 ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  state: () => get<WorldState>("/world/state"),
  metrics: () => get<TickMetrics[]>("/world/metrics"),
  events: (sinceTick = 0) => get<SimEvent[]>(`/world/events?since_tick=${sinceTick}`),
};

/** 離線模式：讀取後端 runs/<實驗>/seed<N>/metrics.jsonl */
export function parseJsonl<T>(text: string): T[] {
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as T);
}

// ---- 已發布的實驗結果：public/runs/<id>/，網址加 ?run=<id> 直接打開 ----
export interface PublishedRun {
  id: string;
  label: string;
}

async function getStatic(path: string): Promise<string> {
  const res = await fetch(`${import.meta.env.BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path} 回傳 ${res.status}`);
  return res.text();
}

/** public/runs/index.json 列出的實驗；新增實驗時把資料夾放進 public/runs/，再加一筆到 index.json */
export async function listRuns(): Promise<PublishedRun[]> {
  return JSON.parse(await getStatic("runs/index.json")) as PublishedRun[];
}

export async function loadRun(id: string,): Promise<{ metrics: TickMetrics[]; band: EquilibriumBand | null; reports: DisasterReport[] }> {
  const [metrics, disasters] = await Promise.all([
    getStatic(`runs/${id}/metrics.jsonl`),
    getStatic(`runs/${id}/disasters.jsonl`),
  ]);
  // 均衡區間在 run 開始時算一次，每次天災報告裡都一樣，取第一筆
  const reports = parseJsonl<DisasterReport>(disasters);
  return { metrics: parseJsonl<TickMetrics>(metrics), band: reports[0]?.band ?? null, reports };
}
