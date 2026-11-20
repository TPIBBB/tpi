import json
from dataclasses import replace

import pytest

from civsim.agents.llm_client import llm_settings, unlocked_settings
from civsim.agents.government import GOV_PROMPT_VERSION, LLMGovernment
from civsim.agents.llm import ScriptedClient
from civsim.analysis.llm_stats import format_summary, summarize_run, write_csv
from civsim.cli import main
from civsim.config import SocialRuleConfig
from civsim.experiments.runner import run_meta
from civsim.storage import JsonlRecorder
from civsim.world import SimulationEngine


@pytest.fixture
def run_dir(small_config, tmp_path):
    """B 組（record 模式）的小 run：政府第一天處決平民，之後一直進口糧食，有一天回應壞掉。"""
    replies = iter(
        [{"actions": [{"type": "execute_citizen", "params": {"target": "c00"}}]}, "壞掉的回應"]
        + [{"actions": [{"type": "import_food", "params": {"qty": 1}}]}] * 1000
    )

    def respond(system, user):
        if "current_memory" in user:
            return json.dumps({"memory": "# 經驗\n- 進口有用"})
        r = next(replies)
        return r if isinstance(r, str) else json.dumps(r)

    cfg = replace(small_config, social_rules=SocialRuleConfig(path="rules/v0.yaml", mode="record"))
    gov = LLMGovernment(ScriptedClient(respond), call_policy="on_change", price_tolerance=10.0)
    out = tmp_path / "run"
    SimulationEngine(cfg, government=gov, recorder=JsonlRecorder(out, meta=run_meta(cfg))).run()
    return out


def test_summarize_run_counts_calls_and_decisions(run_dir):
    s = summarize_run(run_dir)
    c = s["calls"]
    assert c["decide_days"] == c["decide_llm"] + c["decide_skipped"] and c["decide_skipped"] > 0
    assert c["errors"] == 1 and c["fallback"] == 1 and c["reflect"] == 2

    imports = s["decisions"]["import_food"]
    assert imports["total"] == imports["llm"] + imports["replayed"] and imports["replayed"] > 0
    assert imports["llm"] == sum(imports[f"llm_{p}"] for p in ("stable", "disaster", "evaluation"))
    assert s["rule_violations"] == {"no_execution": {"llm": 1, "replayed": 0}}  # record 模式照樣記錄違規

    first = s["disasters"][0]
    assert first["llm_types"]["execute_citizen"] == 1
    assert first["llm_decisions"] + first["replayed_decisions"] > 0


def test_format_and_csv(run_dir, tmp_path):
    s = summarize_run(run_dir)
    text = format_summary(s)
    assert "【LLM 呼叫】" in text and "import_food" in text and "no_execution" in text
    path = tmp_path / "stats.csv"
    write_csv([s], path)
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("run,name,type") and any(",import_food," in r for r in rows[1:])


def test_cli_llm_stats(run_dir, capsys):
    main(["llm-stats", str(run_dir)])
    assert "【決策】" in capsys.readouterr().out


def test_summarize_run_rejects_wrong_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="events.jsonl"):
        summarize_run(tmp_path)


# ---- 正式實驗前的設定檢查 ----
LOCKED = {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-3.6-flash-001", "LLM_TEMPERATURE": "0", "LLM_SEED": "45"}


def set_env(monkeypatch, values):
    for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_TEMPERATURE", "LLM_SEED", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_locked_settings_have_no_problems(monkeypatch):
    set_env(monkeypatch, LOCKED)
    assert unlocked_settings() == []
    assert llm_settings()["seed"] == 45 and "api_key" not in llm_settings()


@pytest.mark.parametrize("change, expected", [
    ({"LLM_MODEL": ""}, "LLM_MODEL"),
    ({"LLM_MODEL": "gemini-flash-latest"}, "固定版本"),
    ({"LLM_TEMPERATURE": "0.7"}, "LLM_TEMPERATURE"),
    ({"LLM_SEED": ""}, "LLM_SEED"),
])
def test_unlocked_settings_are_reported(monkeypatch, change, expected):
    set_env(monkeypatch, {k: v for k, v in {**LOCKED, **change}.items() if v})
    assert any(expected in p for p in unlocked_settings())


def test_run_meta_records_llm_settings(monkeypatch, small_config):
    set_env(monkeypatch, LOCKED)
    small_config.government.controller = "llm"
    meta = run_meta(small_config)
    assert meta["llm"]["model"] == "gemini-3.6-flash-001" and meta["llm"]["prompt_version"] == GOV_PROMPT_VERSION
    assert meta["llm"]["unlocked"] == [] and "commit" in meta["git"]


def test_strict_refuses_unlocked_settings(monkeypatch, tmp_path):
    # conftest 的 no_dotenv 已經擋掉 .env，開發者 .env 裡的設定不會補上缺的變數
    set_env(monkeypatch, {"LLM_PROVIDER": "scripted-test"})  # 不存在的供應商：萬一沒擋下來也連不到網路
    run = []
    monkeypatch.setattr("civsim.experiments.runner.run_one", lambda *a, **k: run.append(1))
    with pytest.raises(SystemExit):
        main(["run", "--config", "configs/group_a.yaml", "--strict", "--out", str(tmp_path)])
    assert not run  # 模擬沒有開始
