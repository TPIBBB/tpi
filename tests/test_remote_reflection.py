"""引擎的遠端反思模式（API 用）必須和本機模式跑出完全一樣的結果。"""

import json
import re

import pytest

from civsim.agents import LLMGovernment
from civsim.agents.llm import ScriptedClient
from civsim.world import SimulationEngine
from civsim.world.government import GovernmentActionType, GovernmentDecision


class ListRecorder:
    def __init__(self):
        self.rows = []

    def record_tick(self, world, events, metrics):
        self.rows.append(("tick", world.tick, [(e.tick, e.kind) for e in events], metrics))

    def record_disaster(self, report):
        self.rows.append(("disaster", report))

    def record_memory(self, version, text):
        self.rows.append(("memory", version, text))

    def close(self):
        pass


def respond(system, user):
    if user.startswith("## 這次天災"):
        disaster = re.search(r"第 (\d+) 次", user).group(1)
        result = "失敗" if "- 結果：失敗" in user else "成功"
        return json.dumps({"result": result, "worked": [], "failed": [],
                           "try_next": [f"天災 {disaster} 的經驗"]}, ensure_ascii=False)
    return json.dumps({"actions": [{"type": "import_food", "params": {"qty": 2}}]})


def local_engine(config):
    return SimulationEngine(config, government=LLMGovernment(ScriptedClient(respond)), recorder=ListRecorder())


def remote_engine(config):
    """和 gateway/client.py 一樣：遠端拿到報告後，用同一種政府反思，再把記憶送回引擎。"""
    gov = LLMGovernment(ScriptedClient(respond))
    engine = SimulationEngine(config, government=None, recorder=ListRecorder())
    engine.remote_reflection = True
    while engine.world.tick < config.total_ticks:
        if engine.pending_reflection is not None:
            engine.submit_memory(gov.reflect(engine.pending_reflection, engine.memory))
        else:
            engine.step([GovernmentDecision(GovernmentActionType.IMPORT_FOOD, {"qty": 2})])
    return engine


def test_remote_reflection_matches_local(small_config):
    local = local_engine(small_config)
    local.run()
    remote = remote_engine(small_config)

    assert remote.history == local.history
    assert remote.reports == local.reports
    assert remote.memory_versions == local.memory_versions and len(local.memory_versions) == 3
    assert "天災 2 的經驗" in local.memory_versions[2] and "- 第 2 次" in local.memory_versions[2]
    assert remote.recorder.rows == local.recorder.rows  # 紀錄檔的內容與順序都一樣


def test_step_refused_while_waiting(small_config):
    engine = SimulationEngine(small_config, government=None)
    engine.remote_reflection = True
    while engine.pending_reflection is None:
        engine.step([])
    tick = engine.world.tick
    with pytest.raises(RuntimeError, match="submit_memory"):
        engine.step([])
    assert engine.world.tick == tick == engine.schedule[0].window_end
    engine.submit_memory(None)
    assert engine.world.tick == tick + 1
    with pytest.raises(RuntimeError, match="沒有等待反思"):
        engine.submit_memory(None)
