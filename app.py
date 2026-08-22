# V92.7.0 - PostgreSQL Phase 2 + MENU display labels (2026-08-23)
"""VERA SPA V92.7.0.

Giữ nguyên app V92.6.99 và các bản vá MENU V92.6.101.
Bổ sung PostgreSQL Phase 2 theo kiểu an toàn:
- sheets: hành vi hiện tại.
- dual: Google Sheets vẫn authoritative, PostgreSQL lưu durable primary snapshot.
- postgres: đọc PostgreSQL primary; khi dataset bị stale/missing thì đồng bộ lại
  từ nguồn Google Sheets hiện tại rồi tiếp tục dùng PostgreSQL.

Route, PAGE_FEATURE_KEYS, PAGE_SLUGS, phân quyền và nghiệp vụ không thay đổi.
Hai Google Sheet cũ và ID của chúng không bị thay đổi.
"""
from pathlib import Path as _Path
import os as _os

# Phase 2 được cài trước khi core import vera_postgres, nên toàn bộ các call
# vpg.load_dataset / invalidate_dataset hiện có tự động dùng lớp chuyển tiếp mới.
# Nếu module Phase 2 có lỗi import, app vẫn chạy bằng lớp PostgreSQL V75/Phase 1.
try:
    import vera_postgres as _vpg_phase2
    from vera_postgres_phase2 import install as _install_vpg_phase2

    # Khi DB đã được cấu hình nhưng chưa chọn backend, tự bắt đầu Phase 2 ở
    # chế độ dual an toàn: Google Sheets vẫn authoritative, PostgreSQL chỉ mirror.
    if (
        callable(getattr(_vpg_phase2, "is_enabled", None))
        and _vpg_phase2.is_enabled()
        and not str(_os.getenv("VERA_DATA_BACKEND", "") or "").strip()
    ):
        _os.environ["VERA_DATA_BACKEND"] = "dual"

    _install_vpg_phase2(_vpg_phase2)
except Exception:
    _vpg_phase2 = None

_core_path_v9270 = _Path(__file__).with_name("app_v92699_core.py")
_source_v9270 = _core_path_v9270.read_text(encoding="utf-8")

_old_menu_map_v9270 = '_MENU_DISPLAY_LABELS_V92699 = {"🧾 Log Book": "Log Book"}'
_new_menu_map_v9270 = """_MENU_DISPLAY_LABELS_V92699 = {
    "📅 Đăng ký nghỉ phép": "📅 Đăng ký nghỉ",
    "📘 Hướng dẫn sử dụng": "📘 Hướng dẫn",
    "⚙️ Giao diện tùy chỉnh": "⚙️ Giao diện",
    "🔐 Phân quyền chức năng": "🔐 Phân quyền",
    "🏖️ Phép năm - Làm đẹp": "🏖️ Phép năm",
    "⏰ Quản lý ca làm việc": "⏰ Quản lý ca",
    "🏷️ Trạng thái nhân viên": "🏷️ Trạng thái NV",
    "🔐 Khóa đăng ký LNP": "🔐 Khóa đăng ký",
    "🧾 Log Book": "Log Book",
}"""

if _old_menu_map_v9270 not in _source_v9270:
    raise RuntimeError("V92.7.0: khong tim thay diem gan nhan MENU V92.6.99.")

_source_v9270 = _source_v9270.replace(
    _old_menu_map_v9270,
    _new_menu_map_v9270,
    1,
)
_source_v9270 = _source_v9270.replace("MENU CHỨC NĂNG", "MENU")
_first_line_v9270, _sep_v9270, _rest_v9270 = _source_v9270.partition("\n")
_source_v9270 = (
    "# V92.7.0 - PostgreSQL Phase 2 + MENU display labels (2026-08-23)\n"
    + _rest_v9270
)

exec(
    compile(_source_v9270, str(_core_path_v9270), "exec"),
    globals(),
    globals(),
)
