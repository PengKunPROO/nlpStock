"""API layer tests: TestClient with fake data service and fake aligner."""
import time

import pytest
from fastapi.testclient import TestClient

from backend.api import Core
from backend.app import create_app
from backend.schema import REFERENCE_STRATEGY
from backend.storage import Storage

DAY = 86_400_000
BASE_MS = 1_760_000_000_000


class ApiFakeData:
    def __init__(self):
        self.bars = self._make_bars()

    @staticmethod
    def _make_bars():
        bars = []
        price = 10.0
        for i in range(130):
            price = 10.0 + (0.2 * (i - 100) if i > 100 else 0.0)
            vol = 500.0 if i in (105, 106) else 100.0
            bars.append({
                "date_ms": BASE_MS - 130 * DAY + i * DAY,
                "open": price * 0.999, "high": price * 1.005, "low": price * 0.994,
                "close": price, "volume": vol, "turnover": price * vol,
            })
        return bars

    def resolve_universe(self, u):
        if u["type"] == "custom":
            return list(u["codes"]), {c: f"股{c[:6]}" for c in u["codes"]}, "自选"
        return ["600001.SH"], {"600001.SH": "平安银行"}, "测试池"

    def kind_for(self, code):
        return "stock"

    def get_bars(self, code, kind="stock", end_ms=None, count=250):
        bars = self.bars
        if end_ms is not None:
            bars = [b for b in bars if b["date_ms"] <= end_ms]
        return bars[-count:]

    def get_bars_range(self, code, kind, start_ms, end_ms, warmup_bars=250):
        return [b for b in self.bars if b["date_ms"] <= end_ms]

    def search(self, q, limit=20):
        return [
            {"thscode": "600519.SH", "name": "贵州茅台", "asset_type": "a-share", "exchange": "SH", "kline_available": True},
            {"thscode": "881101.TI", "name": "种植业", "asset_type": "ths-index", "exchange": None, "kline_available": True},
        ][:limit]

    def universe_options(self):
        return {
            "indices": [{"code": "000300.SH", "name": "沪深300", "count": None}],
            "sectors": [{"code": "881101.TI", "name": "种植业", "count": None}],
        }


class FakeAligner:
    instances = []

    def __init__(self, base_url, api_key, model="deepseek-chat", **kw):
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.responses = FakeAligner.instances
        FakeAligner.instances.append(self)

    def align(self, messages):
        import copy

        cfg = copy.deepcopy(REFERENCE_STRATEGY)
        cfg["source_text"] = messages[0]["content"]
        cfg["parse_engine"] = "llm"
        return {
            "type": "config",
            "config": cfg,
            "summary": "已量化",
            "warnings": ["止损默认8%"],
        }

    def close(self):
        pass


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUYAO_API_KEY", "sk-fuyao-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-llm-test")
    monkeypatch.setattr("backend.api.DeepSeekAligner", FakeAligner)
    FakeAligner.instances = []
    app = create_app(db_path=tmp_path / "t.db", static_dir=tmp_path / "no_frontend")
    return TestClient(app)


@pytest.fixture()
def client_fake_data(tmp_path, monkeypatch):
    """App whose Core.data is the fake data service (jobs actually run, no network)."""
    monkeypatch.setenv("FUYAO_API_KEY", "sk-fuyao-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-llm-test")
    monkeypatch.setattr("backend.api.DeepSeekAligner", FakeAligner)
    FakeAligner.instances = []

    class PatchedCore(Core):
        def __init__(self):
            super().__init__(Storage(tmp_path / "t2.db"), "sk-test")
            self.data = ApiFakeData()

    app = create_app(db_path=tmp_path / "t2.db", static_dir=tmp_path / "no_frontend", core=PatchedCore())
    return TestClient(app)


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job timeout")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db_ok"] is True and body["fuyao_ok"] is True
    assert body["version"]


def test_settings_masked_and_update(client_fake_data):
    c = client_fake_data
    body = c.get("/api/settings").json()
    assert body["fuyao_api_key_set"] is True
    assert body["fuyao_api_key"].startswith("sk-fu") and "****" in body["fuyao_api_key"]
    assert body["llm_available"] is True
    assert body["llm_model"] == "deepseek-chat"
    assert body["llm_base_url"] == "https://api.deepseek.com"

    r = c.put("/api/settings", json={"llm_api_key": "sk-newkey-123456789"})
    assert r.status_code == 200
    assert c.get("/api/settings").json()["llm_api_key_set"] is True

    r = c.put("/api/settings", json={"llm_api_key": ""})
    assert c.get("/api/settings").json()["llm_api_key_set"] is False
    # restore for later tests
    c.put("/api/settings", json={"llm_api_key": "sk-test"})


def test_parse_strategy_not_configured(client_fake_data):
    client_fake_data.put("/api/settings", json={"llm_api_key": ""})
    r = client_fake_data.post("/api/parse-strategy", json={"messages": [{"role": "user", "content": "test"}]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "llm_not_configured"
    client_fake_data.put("/api/settings", json={"llm_api_key": "sk-test"})


def test_parse_strategy_config_flow(client_fake_data):
    msgs = [{"role": "user", "content": "阴跌之后等急跌"}]
    r = client_fake_data.post("/api/parse-strategy", json={"messages": msgs})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "config"
    assert body["config"]["name"] == REFERENCE_STRATEGY["name"]
    assert body["config"]["source_text"] == "阴跌之后等急跌"
    assert body["warnings"] == ["止损默认8%"]


def test_parse_strategy_bad_messages(client_fake_data):
    r = client_fake_data.post("/api/parse-strategy", json={"messages": []})
    assert r.status_code == 422  # pydantic min_length


def test_parse_strategy_llm_parse_failed_returns_400(client_fake_data, monkeypatch):
    from backend.llm_align import LLMParseError

    def bad_align(self, messages):
        raise LLMParseError("LLM 响应体非 JSON", raw="garbage")

    monkeypatch.setattr(FakeAligner, "align", bad_align)
    r = client_fake_data.post("/api/parse-strategy", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "llm_parse_failed"


def test_strategy_crud_flow(client_fake_data):
    c = client_fake_data
    import copy

    cfg = copy.deepcopy(REFERENCE_STRATEGY)
    r = c.post("/api/strategies", json={"config": cfg})
    assert r.status_code == 200
    created = r.json()
    sid, v = created["id"], created["version"]
    assert v == 1

    lst = c.get("/api/strategies").json()["items"]
    assert lst[0]["id"] == sid and lst[0]["version"] == 1 and lst[0]["entry_count"] == 8

    cfg["name"] = "改名"
    r = c.put(f"/api/strategies/{sid}", json={"config": cfg})
    assert r.json()["version"] == 2

    detail = c.get(f"/api/strategies/{sid}").json()
    assert detail["version"] == 2 and [v["version"] for v in detail["versions"]] == [1, 2]

    v1 = c.get(f"/api/strategies/{sid}/versions/1").json()
    assert v1["config"]["name"] == REFERENCE_STRATEGY["name"]

    restored = c.post(f"/api/strategies/{sid}/restore/1").json()
    assert restored["version"] == 3

    assert c.delete(f"/api/strategies/{sid}").json()["ok"] is True
    assert c.get(f"/api/strategies/{sid}").status_code == 404
    assert c.get(f"/api/strategies/{sid}").json()["error"]["code"] == "not_found"


def test_screen_job_flow(client_fake_data):
    c = client_fake_data
    import copy

    cfg = copy.deepcopy(REFERENCE_STRATEGY)
    cfg["universe"] = {"type": "custom", "codes": ["600001.SH"]}
    r = c.post("/api/screen", json={"config": cfg})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = wait_job(c, job_id)
    assert job["status"] == "done", job.get("error")
    res = job["result"]
    assert res["evaluated"] == 1
    assert res["universe"]["name"] == "自选"
    assert "matched_count" in res and "as_of" in res


def test_screen_universe_override(client_fake_data):
    c = client_fake_data
    import copy

    cfg = copy.deepcopy(REFERENCE_STRATEGY)
    cfg["universe"] = {"type": "custom", "codes": ["600001.SH"]}
    r = c.post("/api/screen", json={"config": cfg, "universe": {"type": "custom", "codes": ["600001.SH"]}})
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done"


def test_backtest_job_flow(client_fake_data):
    c = client_fake_data
    import copy

    cfg = copy.deepcopy(REFERENCE_STRATEGY)
    cfg["universe"] = {"type": "custom", "codes": ["600001.SH"]}
    r = c.post("/api/backtest", json={
        "config": cfg,
        "start": "2025-01-01", "end": "2026-12-31",
        "initial_cash": 500000, "position_pct": 50,
    })
    job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job.get("error")
    res = job["result"]
    assert res["params"]["initial_cash"] == 500000
    assert res["metrics"]["final_equity"] > 0
    assert len(res["equity_curve"]) > 0
    assert isinstance(res["trades"], list)


def test_backtest_bad_date_rejected(client_fake_data):
    c = client_fake_data
    import copy

    cfg = copy.deepcopy(REFERENCE_STRATEGY)
    r = c.post("/api/backtest", json={"config": cfg, "start": "2025/01/01"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_kline_endpoint(client_fake_data):
    c = client_fake_data
    r = c.get("/api/kline?thscode=600519.SH&period=1d&count=60")
    assert r.status_code == 200
    body = r.json()
    assert body["thscode"] == "600519.SH"
    assert body["asset_type"] == "a-share"
    assert len(body["bars"]) == 60
    last = body["bars"][-1]
    for k in ("open", "high", "low", "close", "volume", "vratio", "vol_state", "ma5", "ma20"):
        assert k in last
    assert set(body["volume_summary"]) == {"latest_vratio", "latest_vol_state", "trend", "note"}

    r2 = c.get("/api/kline?thscode=600519.SH&period=1w")
    assert r2.status_code == 200 and r2.json()["period"] == "1w"
    assert len(r2.json()["bars"]) < len(body["bars"])

    r3 = c.get("/api/kline?thscode=600519.SH&period=1h")
    assert r3.status_code == 400 and r3.json()["error"]["code"] == "bad_request"


def test_search_and_universe_options(client_fake_data):
    c = client_fake_data
    hits = c.get("/api/search?q=茅台").json()["items"]
    assert hits[0]["thscode"] == "600519.SH"
    opts = c.get("/api/universe/options").json()
    assert opts["indices"][0]["code"] == "000300.SH"
    assert opts["sectors"][0]["code"] == "881101.TI"


def test_job_not_found(client_fake_data):
    r = client_fake_data.get("/api/jobs/j_missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_screen_requires_config_or_id(client_fake_data):
    r = client_fake_data.post("/api/screen", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"
