"""V84.4 - Cloud Run Job chỉ đồng bộ snapshot TimeSoft.

Job này được Cloud Scheduler gọi định kỳ (hiện mỗi 5 phút). Nó tái sử dụng
pipeline ổn định trong timesoft_sync_job.py nhưng ép Auto Update sang PAUSED,
vì Auto Update phạt đã được tách sang auto_penalty_daily_job.py chạy lúc 20:00.
"""
from __future__ import annotations

import sys
import timesoft_sync_job as ts

_original_load_auto_penalty_config = ts.load_auto_penalty_config


def _snapshot_only_config(client):
    """Đọc ngưỡng thật nhưng luôn khóa phần ghi phạt cho job snapshot."""
    cfg = _original_load_auto_penalty_config(client)
    cfg = dict(cfg or {})
    cfg["paused"] = True
    cfg["status"] = "SNAPSHOT_ONLY"
    return cfg


def main() -> int:
    ts.load_auto_penalty_config = _snapshot_only_config
    ts._log("V84.4 SNAPSHOT-ONLY: đồng bộ TimeSoft/PostgreSQL; không ghi Auto Update phạt.")
    return int(ts.run_sync())


if __name__ == "__main__":
    sys.exit(main())
