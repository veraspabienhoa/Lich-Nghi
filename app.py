# V92.8.0 - PostgreSQL Phase 3 normalized CRUD + MENU display labels (2026-08-23)
"""VERA SPA V92.8.0.

Giữ nguyên app V92.6.99, các bản vá MENU V92.6.101 và PostgreSQL Phase 2.
Bổ sung PostgreSQL Phase 3 theo kiểu an toàn:
- credentials được đồng bộ vào bảng chuẩn hóa employees.
- leave_primary được đồng bộ vào bảng chuẩn hóa leave_records.
- dual: Google Sheets vẫn authoritative/write-through; PostgreSQL là mirror CRUD
  chuẩn hóa và được đánh dấu stale sau các thao tác ghi/xóa hiện có.
- postgres: vẫn dùng durable primary dataset của Phase 2; Phase 3 duy trì bảng
  chuẩn hóa để chuẩn bị chuyển từng write-path sang PostgreSQL-primary.

Route, PAGE_FEATURE_KEYS, PAGE_SLUGS, phân quyền và nghiệp vụ không thay đổi.
Hai Google Sheet cũ và ID của chúng không bị thay đổi.
"""
from pathlib import Path as _Path
import os as _os

# Phase 2 + Phase 3 được cài trước khi core import vera_postgres. Vì vậy toàn bộ
# call vpg.load_dataset / invalidate_dataset / write_dataset hiện có tự động đi
# qua lớp chuyển tiếp mà không cần sửa app_v92699_core.py.
_vpg_runtime = None
try:
    import vera_postgres as _vpg_runtime
    from vera_postgres_phase2 import install as _install_vpg_phase2

    # Khi DB đã được cấu hình nhưng chưa chọn backend, tự bắt đầu ở dual an toàn:
    # Google Sheets vẫn authoritative, PostgreSQL mirror dữ liệu.
    if (
        callable(getattr(_vpg_runtime, "is_enabled", None))
        and _vpg_runtime.is_enabled()
        and not str(_os.getenv("VERA_DATA_BACKEND", "") or "").strip()
    ):
        _os.environ["VERA_DATA_BACKEND"] = "dual"

    _install_vpg_phase2(_vpg_runtime)
except Exception:
    _vpg_runtime = None

# Phase 3 là lớp bổ sung; nếu import/khởi tạo lỗi, Phase 2 và app hiện tại vẫn chạy.
if _vpg_runtime is not None:
    try:
        from vera_postgres_phase3 import install as _install_vpg_phase3
        _install_vpg_phase3(_vpg_runtime)
    except Exception:
        pass

_core_path_v9280 = _Path(__file__).with_name("app_v92699_core.py")
_source_v9280 = _core_path_v9280.read_text(encoding="utf-8")

_old_menu_map_v9280 = '_MENU_DISPLAY_LABELS_V92699 = {"🧾 Log Book": "Log Book"}'
_new_menu_map_v9280 = """_MENU_DISPLAY_LABELS_V92699 = {
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

if _old_menu_map_v9280 not in _source_v9280:
    raise RuntimeError("V92.8.0: khong tim thay diem gan nhan MENU V92.6.99.")

_source_v9280 = _source_v9280.replace(
    _old_menu_map_v9280,
    _new_menu_map_v9280,
    1,
)
_source_v9280 = _source_v9280.replace("MENU CHỨC NĂNG", "MENU")
_first_line_v9280, _sep_v9280, _rest_v9280 = _source_v9280.partition("\n")
_source_v9280 = (
    "# V92.8.0 - PostgreSQL Phase 3 normalized CRUD + MENU display labels (2026-08-23)\n"
    + _rest_v9280
)

exec(
    compile(_source_v9280, str(_core_path_v9280), "exec"),
    globals(),
    globals(),
)
