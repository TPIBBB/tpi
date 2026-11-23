"""前端用的 /world/* 路徑。世界只會因為政府的路徑前進（docs/api.md 的 D9）。"""

from dataclasses import asdict

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from civsim.api.server import create_app
from civsim.world import SimulationEngine

RUN = "test-run"
DEC = f"/runs/{RUN}/government/decisions"
MEM = f"/runs/{RUN}/government/memory"


@pytest.fixture
def engine(small_config):
    return SimulationEngine(small_config, government=None)


@pytest.fixture
def client(engine):
    return TestClient(create_app(engine, run_id=RUN))


def decide(client, tick, *actions):
    return client.post(DEC, json={"tick": tick, "actions": list(actions)}).json()


def test_world_state_follows_government(client, small_config):
    decide(client, 0, {"type": "idle"})
    state = client.get("/world/state").json()
    assert state["tick"] == 1 and state["band"]["low"] < state["band"]["high"]
    assert len(state["citizens"]) == small_config.n_citizens
    assert len(client.get("/world/metrics").json()) == 1


def test_world_events_include_government_days(client):
    """政府經 API 推進世界時，事件也要出現在 /world/events（以前只有 /admin/step 會寫入）。"""
    decide(client, 0, {"type": "import_food", "params": {"qty": 1}})
    decide(client, 1)
    events = client.get("/world/events").json()
    assert len(events) > 0
    assert any(e["kind"] == "gov:import_food" and e["tick"] == 0 for e in events)
    assert {e["tick"] for e in events} == {0, 1}
    assert {e["tick"] for e in client.get("/world/events?since_tick=1").json()} == {1}


def test_world_events_include_reflection_and_reset(client, engine):
    tick = 0
    while True:
        body = decide(client, tick)
        if body["status"] != "awaiting_decision":
            break
        tick = body["next_tick"]
    client.post(MEM, json={"memory": "# 記憶"})
    kinds = [e["kind"] for e in client.get("/world/events").json()]
    assert "world:disaster_report" in kinds
    # 評估期最後一天的事件、記憶的結果、調回均衡狀態，都只出現一次（沒有重複寫入）
    assert kinds.count("world:disaster_report") == 1
    assert kinds.count("gov:memory_updated") == 1 and kinds.count("world:reset") == 1


def test_world_disasters_is_the_schedule(client, engine):
    disasters = client.get("/world/disasters").json()
    assert disasters == [asdict(d) for d in engine.schedule]
    assert set(disasters[0]) == {"id", "kind", "cycle_start", "start_tick", "end_tick", "window_end", "food_multiplier"}


def test_admin_step_is_gone(client, engine):
    """D9：前端沒有使用，停用。世界只能經由政府的路徑前進。"""
    assert client.post("/admin/step").status_code in (404, 405)
    assert engine.world.tick == 0


def test_cors(client):
    r = client.get("/world/state", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
