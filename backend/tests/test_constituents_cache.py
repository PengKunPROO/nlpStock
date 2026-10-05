"""Tests for index/sector constituents caching in DataService (TTL 1 day)."""
import pytest

from backend.data_service import DataService
from backend.storage import Storage

CONSTITUENTS = [
    {"thscode": "600519.SH", "name": "贵州茅台"},
    {"thscode": "000858.SZ", "name": "五粮液"},
]


class CountingClient:
    def __init__(self, constituents):
        self.constituents = constituents
        self.calls = 0

    def index_constituents(self, code):
        self.calls += 1
        return self.constituents

    def tickers_all(self, asset_type):
        if asset_type == "a-share":
            return [
                {"thscode": "600000.SH", "name": "浦发银行", "asset_type": "a-share", "exchange": "SH"},
                {"thscode": "000001.SZ", "name": "平安银行", "asset_type": "a-share", "exchange": "SZ"},
            ]
        return []

    def ths_index_list(self, kind):
        return []


@pytest.fixture()
def svc(tmp_path):
    client = CountingClient(CONSTITUENTS)
    storage = Storage(tmp_path / "t.db")
    return DataService(client, storage), client, storage


def test_index_constituents_cached_within_ttl(svc):
    ds, client, _ = svc
    universe = {"type": "index", "code": "000300.SH"}

    codes1, names1, label1 = ds.resolve_universe(universe)
    assert client.calls == 1  # first call hits upstream

    codes2, names2, label2 = ds.resolve_universe(universe)
    assert client.calls == 1  # second call served from cache, no upstream

    assert codes2 == codes1 == ["600519.SH", "000858.SZ"]
    assert names2 == names1 == {"600519.SH": "贵州茅台", "000858.SZ": "五粮液"}
    assert label2 == label1


def test_sector_uses_same_cache_path(svc):
    ds, client, _ = svc
    universe = {"type": "sector", "code": "881101.TI"}

    codes, names, _ = ds.resolve_universe(universe)
    assert client.calls == 1
    codes2, names2, _ = ds.resolve_universe(universe)
    assert client.calls == 1
    assert codes2 == codes == ["600519.SH", "000858.SZ"]


def test_cache_refresh_after_ttl_expiry(svc):
    ds, client, storage = svc
    universe = {"type": "index", "code": "000300.SH"}

    ds.resolve_universe(universe)
    assert client.calls == 1

    # age the cached row beyond the 1-day TTL
    with storage._conn() as c:
        c.execute(
            "UPDATE index_constituents SET updated_at=? WHERE thscode=?",
            ("2000-01-01T00:00:00", "000300.SH"),
        )

    codes, names, _ = ds.resolve_universe(universe)
    assert client.calls == 2  # stale cache triggers a fresh upstream pull
    assert codes == ["600519.SH", "000858.SZ"]


def test_preheat_index_warms_constituents_cache(svc):
    ds, client, _ = svc
    universe = {"type": "index", "code": "000300.SH"}
    log = []
    n = ds.preheat(universe, progress_cb=lambda d, t, c: log.append((d, t, c)))
    assert n == len(CONSTITUENTS)
    assert client.calls == 1  # warmed during preheat
    assert log == [(1, 1, "000300.SH")]

    ds.resolve_universe(universe)
    assert client.calls == 1  # post-preheat resolution hits cache, no upstream


def test_preheat_all_syncs_tickers_and_warms_major_indices(svc):
    from backend.data_service import MAJOR_INDICES

    ds, client, storage = svc
    n = ds.preheat({"type": "all"})
    assert n == 2  # two a-share tickers synced
    assert len(storage.all_tickers("a-share")) == 2
    assert client.calls == len(MAJOR_INDICES)  # every major index warmed once

    codes, names, _ = ds.resolve_universe({"type": "index", "code": "000300.SH"})
    assert client.calls == len(MAJOR_INDICES)  # served from warmed cache, no upstream
    assert codes == ["600519.SH", "000858.SZ"]
