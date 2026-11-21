"""實驗控制：同一組條件用不同 seed 重複執行，比較每次天災的結果。

負責人：實驗 / 分析組。
"""

from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from civsim.config import SimConfig
from civsim.storage import JsonlRecorder
from civsim.world import SimulationEngine


def run_meta(config: SimConfig) -> dict:
    """meta.json 的內容：config、git commit，以及 LLM 政府的模型與 prompt 版本。用來重現與比較實驗。"""
    meta = {**asdict(config), "git": _git_state()}
    if config.government.controller == "llm":
        from civsim.agents.government import GOV_PROMPT_VERSION
        from civsim.agents.llm_client import llm_settings, unlocked_settings

        settings = llm_settings()
        meta["llm"] = {**settings, "prompt_version": GOV_PROMPT_VERSION, "unlocked": unlocked_settings(settings)}
    return meta


def _git_state() -> dict:
    def git(*args: str) -> str | None:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None}


def run_one(config: SimConfig, out_dir: Path | None = None) -> SimulationEngine:
    recorder = JsonlRecorder(out_dir, meta=run_meta(config)) if out_dir else None
    engine = SimulationEngine(config, recorder=recorder)
    engine.run()
    if out_dir:
        (out_dir / "summary.json").write_text(
            json.dumps(engine.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
    return engine


def run_batch(configs: list[SimConfig], repeats: int, out_root: Path) -> Path:
    """每組條件 × 每個 seed × 每次天災一列，輸出 summary.csv。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = out_root / f"batch-{stamp}"
    rows = []
    for config in configs:
        for i in range(repeats):
            cfg = replace(config, seed=config.seed + i)
            engine = run_one(cfg, batch_dir / config.name / f"seed{cfg.seed}")
            for r in engine.reports:
                rows.append({
                    "experiment": config.name, "seed": cfg.seed, "disaster_id": r["disaster_id"],
                    "success": r["success"], "recovered_day": r["recovered_day"],
                    "peak_price": r["peak_price"], "hungry_person_days": r["hungry_person_days"],
                    "deaths": r["deaths"], "band_low": r["band"]["low"], "band_high": r["band"]["high"],
                })

    batch_dir.mkdir(parents=True, exist_ok=True)
    summary = batch_dir / "summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["experiment"])
        writer.writeheader()
        writer.writerows(rows)
    return summary
