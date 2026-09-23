"""Fuyao (THS) data API client: rate limiting, envelope handling, retry with backoff."""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from .logging_setup import get_logger

CST = timezone(timedelta(hours=8))  # Asia/Shanghai, no DST
log = get_logger("fuyao")


class FuyaoError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"fuyao code={code}: {message}")
        self.code = code
        self.message = message


def now_cst() -> datetime:
    return datetime.now(CST)


class FuyaoClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://fuyao.aicubes.cn",
        min_interval: float = 0.15,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = httpx.Client(
            headers={"X-api-key": api_key},
            timeout=timeout,
            trust_env=False,
            transport=transport,
        )
        self._throttle_lock = threading.Lock()
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        with self._throttle_lock:
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _get(self, path: str, params: dict) -> dict:
        delay = self.retry_delay
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                log.warning("扶摇重试 %d/%d path=%s", attempt, self.max_retries, path)
                time.sleep(delay + random.uniform(0, 0.4))
                delay *= 3
            self._throttle()
            try:
                resp = self._client.get(f"{self.base_url}{path}", params=params)
            except httpx.TransportError as e:
                last_err = e
                continue
            if resp.status_code == 429:
                last_err = FuyaoError(4001, "rate limited (HTTP 429)")
                continue
            data = resp.json()
            code = data.get("code", -1)
            if code == 4001:
                last_err = FuyaoError(4001, data.get("message", "rate limited"))
                continue
            if code != 0:
                log.error("扶摇错误 code=%s msg=%s path=%s", code, data.get("message", "unknown error"), path)
                raise FuyaoError(code, data.get("message", "unknown error"))
            log.debug("扶摇请求 path=%s", path)
            return data.get("data") or {}
        log.error("扶摇请求失败(重试%d次) path=%s: %s", self.max_retries, path, last_err)
        raise last_err if last_err else FuyaoError(-1, "unreachable")

    @staticmethod
    def _norm_bar(b: dict) -> dict:
        return {
            "date_ms": b["date_ms"],
            "open": b["open_price"],
            "high": b["high_price"],
            "low": b["low_price"],
            "close": b["close_price"],
            "volume": b.get("volume") or 0.0,
            "turnover": b.get("turnover"),
        }

    # ---- endpoints ----
    def snapshot(self, thscodes: list[str]) -> list[dict]:
        data = self._get("/api/a-share/prices/snapshot", {"thscodes": ",".join(thscodes)})
        return data.get("item", [])

    def stock_historical(self, thscode: str, start_ms: int, end_ms: int, adjust: str = "forward") -> list[dict]:
        data = self._get(
            "/api/a-share/prices/historical",
            {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms, "adjust": adjust},
        )
        return [self._norm_bar(b) for b in data.get("item", [])]

    def index_historical(self, thscode: str, start_ms: int, end_ms: int) -> list[dict]:
        data = self._get(
            "/api/a-share-index/prices/historical",
            {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms},
        )
        return [self._norm_bar(b) for b in data.get("item", [])]

    def index_snapshot(self, thscodes: list[str]) -> list[dict]:
        data = self._get("/api/a-share-index/prices/snapshot", {"thscodes": ",".join(thscodes)})
        return data.get("item", [])

    def tickers_page(self, asset_type: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        data = self._get("/api/meta/tickers/list", {"asset_type": asset_type, "limit": limit, "offset": offset})
        return data.get("item", [])

    def tickers_all(self, asset_type: str, page: int = 1000, max_pages: int = 30) -> list[dict]:
        out: list[dict] = []
        for i in range(max_pages):
            page_items = self.tickers_page(asset_type, limit=page, offset=i * page)
            out.extend(page_items)
            if len(page_items) < page:
                break
        return out

    def ticker_search(self, q: str, limit: int = 10) -> list[dict]:
        data = self._get("/api/meta/tickers/search", {"q": q, "limit": limit})
        return data.get("item", [])

    def ths_index_list(self, tag: str) -> list[dict]:
        data = self._get("/api/a-share-index/catalog/ths-index-list", {"tag": tag})
        return data.get("item", [])

    def index_constituents(self, thscode: str) -> list[dict]:
        data = self._get("/api/a-share-index/constituents/ths-stock-list", {"thscode": thscode})
        return data.get("item", [])

    def trading_days(self) -> list[dict]:
        data = self._get("/api/a-share/calendar/trading-days", {})
        return data.get("item", [])


def last_completed_trading_day(now: datetime, trading_days: list[dict]) -> int | None:
    """Latest trading day whose session is fully closed (after 15:30 CST)."""
    today_ms = int(datetime(now.year, now.month, now.day, tzinfo=CST).timestamp() * 1000)
    cutoff_ms = int(datetime(now.year, now.month, now.day, 15, 30, tzinfo=CST).timestamp() * 1000)
    closed_through = today_ms if now.timestamp() * 1000 >= cutoff_ms else today_ms - 86_400_000
    best: int | None = None
    for d in trading_days:
        ms = d["date_ms"]
        if ms <= closed_through and (best is None or ms > best):
            best = ms
    return best
