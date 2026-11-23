from dataclasses import replace

import pytest

from civsim.config import DisasterConfig, EquilibriumConfig, SimConfig


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """測試一律不讀開發者的 .env：否則 cli.main() 會補上真的 LLM 設定與金鑰，測試就可能連到外部 API。"""
    monkeypatch.setattr("civsim.agents.llm_client.load_dotenv", lambda *a, **k: None)


@pytest.fixture
def small_config() -> SimConfig:
    """縮短天數的設定，讓測試跑得快。"""
    return replace(
        SimConfig(),
        disasters=DisasterConfig(count=2, stable_days=8, stable_jitter=0, disaster_days=3, evaluation_days=6),
        equilibrium=EquilibriumConfig(calibration_days=30, burn_in_days=10),
    )
