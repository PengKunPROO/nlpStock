"""日志子系统测试：截断 / 脱敏 / 按天与按大小滚动 / 保留清理。"""
import logging
import os
import time

from backend.logging_setup import DailySizeRotatingHandler, prune_logs, redact, truncate


def test_truncate():
    assert truncate("short", limit=100) == "short"
    out = truncate("x" * 5000, limit=100)
    assert out.startswith("x" * 100) and "截断4900字" in out


def test_redact():
    out = redact("key=sk-test-abcdef123456 正常文字")
    assert "abcdef123456" not in out
    assert "sk-te****" in out
    assert redact("无敏感信息") == "无敏感信息"


def test_size_rollover(tmp_path):
    h = DailySizeRotatingHandler(str(tmp_path), max_bytes=200)
    rec = logging.LogRecord("app", logging.INFO, "", 0, "hello world " * 10, None, None)
    for _ in range(30):
        h.emit(rec)
    h.close()
    names = os.listdir(str(tmp_path))
    rotated = [n for n in names if n.startswith("app.log.")]
    assert len(rotated) >= 1
    assert "app.log" in names


def test_daily_rotate_manual(tmp_path):
    h = DailySizeRotatingHandler(str(tmp_path), max_bytes=10_000_000)
    h.emit(logging.LogRecord("app", logging.INFO, "", 0, "x", None, None))
    h._rotate("2026-01-02", seq=False)
    h.close()
    assert os.path.exists(os.path.join(str(tmp_path), "app.log.2026-01-02"))


def test_prune_by_age(tmp_path):
    logdir = str(tmp_path)
    old = os.path.join(logdir, "app.log.2000-01-01")
    recent = os.path.join(logdir, "app.log.2026-01-01")
    open(old, "w").write("old")
    open(recent, "w").write("recent")
    old_ts = time.time() - 40 * 86400
    os.utime(old, (old_ts, old_ts))
    prune_logs(logdir, retention_days=30, total_cap_bytes=10 ** 12)
    assert not os.path.exists(old)
    assert os.path.exists(recent)


def test_prune_by_size(tmp_path):
    logdir = str(tmp_path)
    f1 = os.path.join(logdir, "app.log.2026-01-01")
    f2 = os.path.join(logdir, "app.log.2026-01-02")
    with open(f1, "wb") as f:
        f.write(b"a" * 1000)
    with open(f2, "wb") as f:
        f.write(b"b" * 1000)
    os.utime(f1, (time.time() - 100, time.time() - 100))
    prune_logs(logdir, retention_days=30, total_cap_bytes=1500)
    assert not os.path.exists(f1)
    assert os.path.exists(f2)


def test_setup_logging_via_child_logger_injects_request_id(tmp_path):
    import logging

    from backend.logging_setup import get_logger, set_request_id, setup_logging

    root = logging.getLogger("app")
    root.handlers.clear()
    try:
        logger = setup_logging(str(tmp_path))
        set_request_id("abc12345")
        get_logger("test").info("任务开始 job=x")
        for h in logger.handlers:
            h.flush()
        content = open(os.path.join(str(tmp_path), "app.log"), encoding="utf-8").read()
        assert "任务开始 job=x" in content
        assert "abc12345" in content
    finally:
        root.handlers.clear()
