"""Cache-aware data service: kline fetch/backfill, universe resolution, ticker sync."""
from __future__ import annotations

from .fuyao import FuyaoClient, FuyaoError, CST, last_completed_trading_day, now_cst
from .storage import Storage

DAY_MS = 86_400_000
MAJOR_INDICES = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("000300.SH", "沪深300"),
    ("000016.SH", "上证50"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
]


def _today_ms() -> int:
    n = now_cst()
    from datetime import datetime as _dt

    return int(_dt(n.year, n.month, n.day, tzinfo=CST).timestamp() * 1000)


class DataService:
    def __init__(self, client: FuyaoClient, storage: Storage):
        self.client = client
        self.storage = storage

    # ---- trading days ----
    def _trading_days(self) -> list[dict]:
        days = self.storage.trading_days_list()
        today = _today_ms()
        if not days or days[-1]["date_ms"] < today - 5 * DAY_MS:
            try:
                fresh = self.client.trading_days()
                if fresh:
                    self.storage.upsert_trading_days(fresh)
                    return fresh
            except FuyaoError:
                pass
        return days

    def _fresh_through_ms(self) -> int | None:
        return last_completed_trading_day(now_cst(), self._trading_days())

    # ---- klines ----
    def _fetch(self, thscode: str, kind: str, start_ms: int, end_ms: int) -> list[dict]:
        if kind == "index":
            return self.client.index_historical(thscode, start_ms, end_ms)
        return self.client.stock_historical(thscode, start_ms, end_ms)

    def get_bars(self, thscode: str, kind: str = "stock", end_ms: int | None = None, count: int = 250) -> list[dict]:
        target_end = end_ms if end_ms is not None else (self._fresh_through_ms() or _today_ms())
        cached = self.storage.get_klines(thscode, end_ms=target_end)
        have_last = cached[-1]["date_ms"] if cached else None
        if end_ms is None:
            fresh_through = self._fresh_through_ms()
            tail_ok = fresh_through is not None and have_last is not None and have_last >= fresh_through
        else:
            tail_ok = have_last is not None and have_last >= target_end - 10 * DAY_MS
        if tail_ok and len(cached) >= count:
            return cached[-count:]
        if cached and len(cached) >= count:
            start = have_last - 5 * DAY_MS  # incremental tail refresh
        else:
            start = target_end - 3 * count * DAY_MS
        bars = self._fetch(thscode, kind, max(start, 0), target_end)
        if bars:
            self.storage.upsert_klines(thscode, bars)
            cached = self.storage.get_klines(thscode, end_ms=target_end)
        return cached[-count:]

    def get_bars_range(self, thscode: str, kind: str, start_ms: int | None, end_ms: int, warmup_bars: int = 250) -> list[dict]:
        needed_start = start_ms - int(warmup_bars * 1.6) * DAY_MS
        cached = self.storage.get_klines(thscode, start_ms=needed_start, end_ms=end_ms)
        fresh_through = self._fresh_through_ms()
        if end_ms is None:
            tail_target = fresh_through
        else:
            tail_target = min(end_ms, fresh_through) if fresh_through else end_ms
        head_ok = bool(cached) and cached[0]["date_ms"] <= needed_start + 40 * DAY_MS
        tail_ok = bool(cached) and tail_target is not None and cached[-1]["date_ms"] >= tail_target - 10 * DAY_MS
        if head_ok and tail_ok:
            return cached
        bars = self._fetch(thscode, kind, needed_start, end_ms)
        if bars:
            self.storage.upsert_klines(thscode, bars)
            cached = self.storage.get_klines(thscode, start_ms=needed_start, end_ms=end_ms)
        return cached

    # ---- tickers / search ----
    def sync_tickers(self) -> int:
        total = 0
        for asset_type in ("a-share", "a-share-index"):
            items = self.client.tickers_all(asset_type)
            if items:
                self.storage.upsert_tickers(items)
                total += len(items)
        try:
            for t in self.client.ths_index_list("industry"):
                self.storage.upsert_tickers([{
                    "thscode": t["thscode"], "name": t["name"], "asset_type": "ths-index", "exchange": None,
                }])
                total += 1
        except FuyaoError:
            pass
        self.storage.update_settings({"tickers_synced_at": now_cst().isoformat(timespec="seconds")})
        return total

    def _ensure_tickers(self) -> None:
        if not self.storage.all_tickers():
            self.sync_tickers()

    def search(self, q: str, limit: int = 20) -> list[dict]:
        self._ensure_tickers()
        items = self.storage.search_tickers(q, limit)
        for it in items:
            if it["thscode"].endswith(".TI"):
                it["asset_type"] = "ths-index"
            it["kline_available"] = True
        return items

    def kind_for(self, thscode: str) -> str:
        t = self.storage.get_ticker(thscode)
        if t:
            return "index" if t["asset_type"] in ("a-share-index", "ths-index") else "stock"
        if thscode.endswith(".TI"):
            return "index"
        self._ensure_tickers()
        t = self.storage.get_ticker(thscode)
        if t:
            return "index" if t["asset_type"] in ("a-share-index", "ths-index") else "stock"
        return "stock"

    def name_for(self, thscode: str) -> str | None:
        t = self.storage.get_ticker(thscode)
        return t["name"] if t else None

    # ---- universes ----
    def resolve_universe(self, universe: dict) -> tuple[list[str], dict[str, str], str]:
        utype = universe.get("type", "index")
        if utype in ("index", "sector"):
            code = universe["code"]
            items = self.client.index_constituents(code)
            codes = [i["thscode"] for i in items]
            names = {i["thscode"]: i["name"] for i in items}
            label = self.name_for(code) or code
            return codes, names, label
        if utype == "custom":
            codes = list(universe.get("codes", []))
            names = {c: (self.name_for(c) or c) for c in codes}
            return codes, names, "自选"
        self._ensure_tickers()
        items = self.storage.all_tickers("a-share")
        return [i["thscode"] for i in items], {i["thscode"]: i["name"] for i in items}, "全市场"

    def universe_options(self) -> dict:
        self._ensure_tickers()
        sectors = [
            {"code": t["thscode"], "name": t["name"], "count": None}
            for t in self.storage.all_tickers("ths-index")
        ]
        return {
            "indices": [{"code": c, "name": n, "count": None} for c, n in MAJOR_INDICES],
            "sectors": sectors,
        }
