"""政府 client 經過 digiRunner 時的身分驗證：換 token、token 過期、帳密錯誤、沒有權限。

用一個假的 digiRunner（FakeGateway）擋在 world-api 前面，不需要真的開 Docker。
"""

import pytest

pytest.importorskip("fastapi")
import httpx
from fastapi.testclient import TestClient

from civsim.agents import NoGovernment
from civsim.api.server import create_app
from civsim.gateway.client import HttpWorldClient, RunInvalid, basic_credentials, run_government
from civsim.world import SimulationEngine

RUN = "test-run"
CLIENT_ID, SECRET = "civsim-gov", "s3cret-pw"


@pytest.fixture
def engine(small_config):
    return SimulationEngine(small_config, government=None)


@pytest.fixture
def server(engine):
    return TestClient(create_app(engine, run_id=RUN))


class FakeGateway:
    """模擬 digiRunner：/oauth/token 發 token；其他路徑要帶有效的 Bearer token 才轉給 world-api。

    expire_after：每張 token 用幾次後過期（None = 不過期）
    forbidden：這張卡沒有權限的路徑開頭，回 403
    """

    def __init__(self, server, expire_after=None, forbidden=()):
        self.server = server
        self.expire_after = expire_after
        self.forbidden = forbidden
        self.issued = 0
        self.valid = None
        self.uses = 0
        self.seen_auth = []  # 轉給 world-api 的請求帶了什麼 Authorization

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            ok = (request.headers.get("authorization") == f"Basic {basic_credentials(CLIENT_ID, SECRET)}"
                  and request.content == b"grant_type=client_credentials")
            if not ok:
                return httpx.Response(401, json={"error": "Unauthorized"})
            self.issued += 1
            self.valid, self.uses = f"token-{self.issued}", 0
            return httpx.Response(200, json={"access_token": self.valid, "expires_in": 86399, "token_type": "bearer"})

        if self.valid is None or request.headers.get("authorization") != f"Bearer {self.valid}":
            return httpx.Response(401, json={"error": "Unauthorized"})
        if self.expire_after is not None and self.uses >= self.expire_after:
            self.valid = None  # 過期
            return httpx.Response(401, json={"error": "Unauthorized"})
        self.uses += 1
        if request.url.path.startswith(self.forbidden or ("\0",)):
            return httpx.Response(403, json={"error": "Forbidden"})

        self.seen_auth.append(request.headers.get("authorization"))
        r = self.server.request(request.method, request.url.raw_path.decode(), content=request.content,
                                headers={"content-type": request.headers.get("content-type", "")})
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    def http(self):
        return httpx.Client(transport=httpx.MockTransport(self.handle), base_url="http://digirunner")


def gov_client(gw, **kw):
    kw.setdefault("client_id", CLIENT_ID)
    kw.setdefault("client_secret", SECRET)
    return HttpWorldClient(http=gw.http(), backoff=0, **kw)


def test_basic_credentials_matches_digirunner_docs():
    """digiRunner 文件 Client Credentials Mode 的範例：帳號 tspclient、密碼 tsp123abcd。"""
    assert basic_credentials("tspclient", "tsp123abcd") == "dHNwY2xpZW50OmRITndNVEl6WVdKalpBPT0="


def test_gets_token_once_and_runs_whole_run(server, engine):
    gw = FakeGateway(server)
    c = gov_client(gw)
    summary = run_government(c, NoGovernment())
    assert summary["days"] == engine.config.total_ticks and summary["reflections"] == len(engine.schedule)
    assert gw.issued == 1 and c.token_requests == 1
    assert set(gw.seen_auth) == {"Bearer token-1"}  # 每個轉給 world-api 的請求都帶著 token


def test_refreshes_expired_token_mid_run(server, engine):
    """token 用到一半過期：自動重新申請，世界每一天只前進一次（沒有因為重送多跑）。"""
    gw = FakeGateway(server, expire_after=25)
    c = gov_client(gw)
    summary = run_government(c, NoGovernment())
    assert summary["days"] == engine.config.total_ticks
    assert engine.world.tick == engine.config.total_ticks
    assert gw.issued > 1 and c.token_requests == gw.issued


def test_wrong_secret_invalidates_run(server, engine):
    gw = FakeGateway(server)
    with pytest.raises(RunInvalid, match="帳密"):
        gov_client(gw, client_secret="wrong")
    assert gw.issued == 0 and engine.world.tick == 0


def test_401_without_credentials_is_invalid(server, monkeypatch):
    monkeypatch.delenv("DIGIRUNNER_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIRUNNER_CLIENT_SECRET", raising=False)
    with pytest.raises(RunInvalid, match="DIGIRUNNER_CLIENT_ID"):
        HttpWorldClient(http=FakeGateway(server).http(), backoff=0)


def test_403_invalidates_run(server, engine):
    """例如政府的卡去敲沒授權的路徑：重試也沒用，run 作廢。"""
    gw = FakeGateway(server, forbidden=(f"/runs/{RUN}/government/observation",))
    c = gov_client(gw, run_id=RUN)
    with pytest.raises(RunInvalid, match="403"):
        c.observe()
    assert engine.world.tick == 0


def test_errors_never_contain_secret_or_token(server):
    gw = FakeGateway(server, forbidden=(f"/runs/{RUN}/government/observation",))
    c = gov_client(gw, run_id=RUN)
    c.runs()  # 先換到 token
    with pytest.raises(RunInvalid) as e:
        c.observe()
    assert SECRET not in str(e.value) and c.token not in str(e.value)


def test_direct_to_world_api_never_requests_token(server, engine):
    """直連 world-api（沒有 digiRunner）時不會收到 401，也就不會去申請 token。"""
    c = HttpWorldClient(http=server, backoff=0, client_id=CLIENT_ID, client_secret=SECRET)
    run_government(c, NoGovernment())
    assert c.token_requests == 0 and engine.world.tick == engine.config.total_ticks
