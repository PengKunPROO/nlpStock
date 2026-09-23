"""日志子系统：按天滚动 + 单文件大小上限 + 保留清理 + 脱敏/截断（纯标准库）。

预算：5GB 存储上限（总量 4GB 兜底删除最旧，留 1GB 余量）；保留 30 天。
"""
from __future__ import annotations

import contextvars
import logging
import os
import re
import time
from datetime import datetime

MAX_LINE = 1000                                   # 单条消息截断长度
FILE_MAX_BYTES = 200 * 1024 * 1024                # 单文件上限 200MB
RETENTION_DAYS = 30                               # 保留天数
TOTAL_CAP_BYTES = 4 * 1024 * 1024 * 1024          # 总量兜底 4GB

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{4,})")
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def truncate(msg: object, limit: int = MAX_LINE) -> str:
    text = str(msg)
    if len(text) > limit:
        return text[:limit] + f"...[截断{len(text) - limit}字]"
    return text


def redact(msg: object) -> str:
    return _SECRET_RE.sub(lambda m: m.group(1)[:5] + "****", str(msg))


class SafeFormatter(logging.Formatter):
    """先脱敏再截断（堆栈 traceback 不受截断影响，完整保留）。"""

    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact(truncate(record.getMessage()))
        record.args = None
        return super().format(record)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class DailySizeRotatingHandler(logging.FileHandler):
    """当前写 app.log；每天零点或单文件超 max_bytes 时滚动为 app.log.YYYY-MM-DD[.N]。"""

    def __init__(self, log_dir: str, max_bytes: int = FILE_MAX_BYTES, encoding: str = "utf-8"):
        self.log_dir = os.fspath(log_dir)
        self.max_bytes = max_bytes
        os.makedirs(self.log_dir, exist_ok=True)
        self._day = datetime.now().strftime("%Y-%m-%d")
        super().__init__(os.path.join(self.log_dir, "app.log"), encoding=encoding)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._day:
                self._rotate(today, seq=False)
            elif self.stream is not None and self.stream.tell() >= self.max_bytes:
                self._rotate(today, seq=True)
        except Exception:
            self.handleError(record)
        super().emit(record)

    def _rotate(self, today: str, seq: bool) -> None:
        try:
            self.close()
        except Exception:
            pass
        suffix = today
        if seq:
            n = 1
            while os.path.exists(os.path.join(self.log_dir, f"app.log.{today}.{n}")):
                n += 1
            suffix = f"{today}.{n}"
        try:
            os.replace(self.baseFilename, os.path.join(self.log_dir, f"app.log.{suffix}"))
        except OSError:
            pass
        self._day = today
        self.stream = self._open()


def prune_logs(log_dir: str, retention_days: int = RETENTION_DAYS, total_cap_bytes: int = TOTAL_CAP_BYTES) -> None:
    log_dir = os.fspath(log_dir)
    if not os.path.isdir(log_dir):
        return

    def _rotated():
        return sorted(
            [
                os.path.join(log_dir, f)
                for f in os.listdir(log_dir)
                if f.startswith("app.log.") and os.path.isfile(os.path.join(log_dir, f))
            ],
            key=os.path.getmtime,
        )

    rotated = _rotated()
    cutoff = time.time() - retention_days * 86400
    for f in rotated:
        if os.path.getmtime(f) < cutoff:
            try:
                os.remove(f)
            except OSError:
                pass

    rotated = _rotated()
    active = os.path.join(log_dir, "app.log")
    total = os.path.getsize(active) if os.path.exists(active) else 0
    total += sum(os.path.getsize(f) for f in rotated)
    i = 0
    while total > total_cap_bytes and i < len(rotated):
        f = rotated[i]
        try:
            total -= os.path.getsize(f)
            os.remove(f)
        except OSError:
            pass
        i += 1


def setup_logging(log_dir: str = "data/logs", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("app")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    prune_logs(log_dir)
    fmt = SafeFormatter("%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] [%(request_id)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    rid_filter = RequestIdFilter()
    fh = DailySizeRotatingHandler(log_dir)
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    fh.addFilter(rid_filter)
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    ch.addFilter(rid_filter)
    logger.addHandler(ch)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"app.{name}")
