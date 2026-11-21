"""統計一個 run 裡政府的 LLM 呼叫與決策。負責人：Agent。

用法：python -m civsim llm-stats runs/group_c/seed42 [更多 run 資料夾] [--csv 輸出.csv]

讀取 JsonlRecorder 寫出的檔案：
- llm_calls.jsonl：呼叫幾次、跳過幾次（on_change）、失敗幾次、token 用量
- events.jsonl：每種決策做了幾次、被拒絕幾次與理由、違反哪些社會規則
- metrics.jsonl：每天屬於穩定期、天災還是評估期
- disasters.jsonl：每次天災的結果
- meta.json：模型、prompt 版本、還沒鎖定的設定

決策分成「LLM 當天親自做的」和「重播的」（on_change 跳過 LLM 時沿用上次的決策，replayed = true）。
B 組人工判斷哪些決策算「亂搞」時，應該只看 LLM 親自做的決策，重播的另外計算。
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from civsim.world.government import GOV_ID

PHASES = ("stable", "disaster", "evaluation")
PHASE_NAMES = {"stable": "穩定期", "disaster": "天災", "evaluation": "評估期"}
TOKEN_FIELDS = ("prompt_tokens", "output_tokens", "thinking_tokens", "total_tokens")


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize_run(run_dir: str | Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    if not (run_dir / "events.jsonl").is_file():
        raise FileNotFoundError(f"{run_dir} 裡沒有 events.jsonl，請指到 run 的資料夾（例如 runs/group_c/seed42）")
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    calls = load_jsonl(run_dir / "llm_calls.jsonl")
    events = load_jsonl(run_dir / "events.jsonl")
    phases = {m["tick"]: m.get("phase") for m in load_jsonl(run_dir / "metrics.jsonl")}
    disasters = load_jsonl(run_dir / "disasters.jsonl")
    return {
        "run": str(run_dir),
        "name": meta.get("name"),
        "llm": meta.get("llm"),
        "calls": summarize_calls(calls),
        **summarize_decisions(events, phases),
        "disasters": [summarize_disaster(r) for r in disasters],
    }


def summarize_calls(calls: list[dict]) -> dict[str, Any]:
    decide = [c for c in calls if c.get("trigger") != "reflect"]
    reflect = [c for c in calls if c.get("trigger") == "reflect"]
    used = [c for c in calls if c.get("used_llm")]
    tokens = {k: sum((c.get("usage") or {}).get(k, 0) or 0 for c in used) for k in TOKEN_FIELDS}
    return {
        "decide_days": len(decide),
        "decide_llm": sum(1 for c in decide if c.get("used_llm")),
        "decide_skipped": sum(1 for c in decide if not c.get("used_llm")),  # on_change 沿用上次的決策
        "reflect": len(reflect),
        "errors": sum(1 for c in used if c.get("error")),
        "fallback": sum(1 for c in used if c.get("used_fallback")),
        "reflect_errors": sum(1 for c in reflect if c.get("error")),
        "parse_skipped_items": sum(len(c.get("skipped") or []) for c in used),  # 格式錯誤被略過的個別決策
        "error_samples": [c["error"] for c in used if c.get("error")][:5],
        "tokens": tokens,
        "tokens_per_llm_call": round(tokens["total_tokens"] / len(used), 1) if used else 0,
    }


def summarize_decisions(events: list[dict], phases: dict[int, str | None]) -> dict[str, Any]:
    def new_row() -> dict[str, Any]:
        return {"total": 0, "llm": 0, "replayed": 0, "accepted": 0, "rejected": 0,
                **{f"llm_{p}": 0 for p in PHASES}}

    by_type: dict[str, dict[str, Any]] = defaultdict(new_row)
    reasons: Counter[str] = Counter()
    violations: dict[str, dict[str, int]] = defaultdict(lambda: {"llm": 0, "replayed": 0})

    for e in events:
        if e.get("actor") != GOV_ID:
            continue
        kind, payload = e.get("kind", ""), e.get("payload") or {}
        replayed = bool(payload.get("replayed", False))
        if kind == "rule:violation":
            for rule in payload.get("rules", []):
                violations[rule]["replayed" if replayed else "llm"] += 1
            continue
        if not kind.startswith("gov:") or kind.startswith("gov:memory"):
            continue
        if kind == "gov:rejected":
            type_, accepted = payload.get("type", "?"), False
            reasons[e.get("message", "")] += 1
        else:
            type_, accepted = kind[len("gov:"):], True
        row = by_type[type_]
        row["total"] += 1
        row["accepted" if accepted else "rejected"] += 1
        if replayed:
            row["replayed"] += 1
        else:
            row["llm"] += 1
            phase = phases.get(e.get("tick"))
            if phase in PHASES:
                row[f"llm_{phase}"] += 1

    return {
        "decisions": dict(sorted(by_type.items(), key=lambda kv: -kv[1]["total"])),
        "rejection_reasons": reasons.most_common(),
        "rule_violations": dict(violations),
    }


def summarize_disaster(report: dict) -> dict[str, Any]:
    decisions = report.get("decisions") or []
    return {
        "disaster_id": report.get("disaster_id"),
        "success": report.get("success"),
        "recovered_day": report.get("recovered_day"),
        "peak_price": report.get("peak_price"),
        "memory_version": report.get("memory_version"),
        "llm_decisions": sum(1 for d in decisions if not d.get("replayed")),
        "replayed_decisions": sum(1 for d in decisions if d.get("replayed")),
        "rejected": sum(1 for d in decisions if d.get("accepted") is False),
        # LLM 親自做的決策種類（不含 idle），用來看每次天災的策略有沒有改變
        "llm_types": dict(Counter(d["type"] for d in decisions if not d.get("replayed") and d["type"] != "idle")),
    }


# ---- 輸出 ----
def format_summary(s: dict[str, Any]) -> str:
    lines = [f"=== {s['run']}（{s['name'] or '未知組別'}）==="]
    llm = s.get("llm")
    if llm:
        lines.append(f"模型：{llm.get('provider')} / {llm.get('model')}，temperature={llm.get('temperature')}，"
                     f"seed={llm.get('seed')}，prompt {llm.get('prompt_version')}")
        for p in llm.get("unlocked") or []:
            lines.append(f"  ⚠ {p}")

    c = s["calls"]
    lines += [
        "",
        "【LLM 呼叫】",
        f"決策日 {c['decide_days']} 天：呼叫 LLM {c['decide_llm']} 次、沿用上次決策（跳過）{c['decide_skipped']} 次；反思 {c['reflect']} 次",
        f"失敗 {c['errors']} 次（其中改用 fallback {c['fallback']} 次、反思失敗 {c['reflect_errors']} 次），"
        f"格式錯誤被略過的決策 {c['parse_skipped_items']} 個",
        f"token：輸入 {c['tokens']['prompt_tokens']:,}、輸出 {c['tokens']['output_tokens']:,}、"
        f"思考 {c['tokens']['thinking_tokens']:,}、合計 {c['tokens']['total_tokens']:,}（每次呼叫平均 {c['tokens_per_llm_call']:,}）",
    ]
    lines += [f"  錯誤範例：{err[:150]}" for err in c["error_samples"]]

    lines += ["", "【決策】LLM 親自做的 / 重播的；穩定期 / 天災 / 評估期只算 LLM 親自做的"]
    if s["decisions"]:
        lines.append(f"{'類型':<18}{'總數':>6}{'LLM':>6}{'重播':>6}{'通過':>6}{'拒絕':>6}"
                     f"{'穩定期':>8}{'天災':>6}{'評估期':>8}")
        for type_, r in s["decisions"].items():
            lines.append(f"{type_:<18}{r['total']:>6}{r['llm']:>6}{r['replayed']:>6}{r['accepted']:>6}"
                         f"{r['rejected']:>6}{r['llm_stable']:>8}{r['llm_disaster']:>6}{r['llm_evaluation']:>8}")
    else:
        lines.append("（沒有政府決策）")

    if s["rejection_reasons"]:
        lines += ["", "【被拒絕的理由】"]
        lines += [f"{n:>4} 次  {reason}" for reason, n in s["rejection_reasons"]]
    if s["rule_violations"]:
        lines += ["", "【違反社會規則】（B 組的 record 模式也會記錄）"]
        lines += [f"{rule}：LLM 親自做的 {v['llm']} 次、重播 {v['replayed']} 次"
                  for rule, v in s["rule_violations"].items()]

    if s["disasters"]:
        lines += ["", "【每次天災】"]
        for d in s["disasters"]:
            result = "成功" if d["success"] else "失敗"
            day = f"評估期第 {d['recovered_day']} 天回到區間" if d["recovered_day"] else "沒有回到區間"
            types = "、".join(f"{t}×{n}" for t, n in d["llm_types"].items()) or "只有 idle"
            lines.append(f"天災 {d['disaster_id']}：{result}（{day}），記憶第 {d['memory_version']} 版；"
                         f"LLM 決策 {d['llm_decisions']} 個、重播 {d['replayed_decisions']} 個、拒絕 {d['rejected']} 個；"
                         f"LLM 用了：{types}")
    return "\n".join(lines)


def write_csv(summaries: list[dict[str, Any]], path: str | Path) -> None:
    """每個 run × 每種決策一列，方便丟進試算表比較不同組別。"""
    fields = ["run", "name", "type", "total", "llm", "replayed", "accepted", "rejected",
              *(f"llm_{p}" for p in PHASES)]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig：Excel 開中文才不會亂碼
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for s in summaries:
            for type_, r in s["decisions"].items():
                writer.writerow({"run": s["run"], "name": s["name"], "type": type_, **r})
