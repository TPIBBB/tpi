"""改了政府的 prompt 卻忘記升 GOV_PROMPT_VERSION 時，這個測試會失敗。

每筆 LLM 呼叫都記下 prompt 版本（llm_calls.jsonl、meta.json），版本號沒跟著改，
之後就分不清哪些數據是用哪一版 prompt 跑出來的，不同版本的結果也會被混在一起比較。

prompt 改了之後：
1. 把 agents/government.py 的 GOV_PROMPT_VERSION 加 1，並在旁邊註明改了什麼
2. 把測試失敗訊息裡的新指紋，加進下面的 PROMPT_FINGERPRINTS（舊版本的保留不要刪）
"""

import hashlib

from civsim.agents.government import (
    GOV_PROMPT_VERSION,
    build_prompt,
    build_reflect_system_prompt,
    build_reflection_prompt,
    build_system_prompt,
)
from civsim.world.government import GovernmentObservation

PROMPT_FINGERPRINTS = {
    "gov-v4": "a6e7fe4ffa98aa42",
}


def prompt_fingerprint() -> str:
    """把所有送給 LLM 的固定文字，加上一份範例觀察與反思輸入，算成一個雜湊值。"""
    obs = GovernmentObservation(
        tick=12, prices={"food": 8.5}, memory="# 經驗\n- 範例", rules=["不能處決平民"],
        budget=500.0, food_reserve=40.0, population=20, deaths=1, hungry=3,
        price_cap={"price": 9.0, "until": 15}, citizens=[{"id": "c01", "type": "consumer"}],
        recent_decisions=[{"tick": 11, "type": "idle", "params": {}, "reason": "", "replayed": False,
                           "accepted": True, "result": {}}],
        price_history=[{"tick": 10, "price": 5.0}, {"tick": 11, "price": 8.5}],
    )
    parts = [
        build_system_prompt(),
        build_prompt(obs),
        build_reflect_system_prompt(),
        build_reflection_prompt({"disaster_id": 1, "success": False}, "# 經驗"),
    ]
    return hashlib.sha256("\n\x00\n".join(parts).encode("utf-8")).hexdigest()[:16]


def test_prompt_version_matches_prompt_content():
    actual = prompt_fingerprint()
    known = {v: k for k, v in PROMPT_FINGERPRINTS.items()}
    if actual in known:
        assert known[actual] == GOV_PROMPT_VERSION, (
            f"prompt 內容和 {known[actual]} 相同，GOV_PROMPT_VERSION 卻是 {GOV_PROMPT_VERSION}")
        return
    expected = PROMPT_FINGERPRINTS.get(GOV_PROMPT_VERSION)
    assert expected is None, (
        f"prompt 改了，但 GOV_PROMPT_VERSION 還是 {GOV_PROMPT_VERSION}。"
        f"請把版本號加 1，再把 \"<新版本>\": \"{actual}\" 加進 PROMPT_FINGERPRINTS")
    raise AssertionError(
        f"新的 prompt 版本 {GOV_PROMPT_VERSION} 還沒登記，請把 \"{GOV_PROMPT_VERSION}\": \"{actual}\" "
        f"加進 tests/test_prompt_version.py 的 PROMPT_FINGERPRINTS")
