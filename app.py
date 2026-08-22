# V92.6.101 - Sua chinh ta nhan MENU: Dang ky nghi (2026-08-23)
"""VERA SPA V92.6.101.

Loader nhe de giu nguyen toan bo app V92.6.99 va chi doi nhan hien thi MENU.
Route, PAGE_FEATURE_KEYS, PAGE_SLUGS, phan quyen va nghiep vu khong thay doi.
"""
from pathlib import Path as _Path

_core_path_v926101 = _Path(__file__).with_name("app_v92699_core.py")
_source_v926101 = _core_path_v926101.read_text(encoding="utf-8")

_old_menu_map_v926101 = '_MENU_DISPLAY_LABELS_V92699 = {"🧾 Log Book": "Log Book"}'
_new_menu_map_v926101 = '''_MENU_DISPLAY_LABELS_V92699 = {
    "📅 Đăng ký nghỉ phép": "📅 Đăng ký nghỉ",
    "📘 Hướng dẫn sử dụng": "📘 Hướng dẫn",
    "⚙️ Giao diện tùy chỉnh": "⚙️ Giao diện",
    "🔐 Phân quyền chức năng": "🔐 Phân quyền",
    "🏖️ Phép năm - Làm đẹp": "🏖️ Phép năm",
    "⏰ Quản lý ca làm việc": "⏰ Quản lý ca",
    "🏷️ Trạng thái nhân viên": "🏷️ Trạng thái NV",
    "🔐 Khóa đăng ký LNP": "🔐 Khóa đăn ký",
    "🧾 Log Book": "Log Book",
}'''

if _old_menu_map_v926101 not in _source_v926101:
    raise RuntimeError("V92.6.101: khong tim thay diem gan nhan MENU V92.6.99.")

_source_v926101 = _source_v926101.replace(
    _old_menu_map_v926101,
    _new_menu_map_v926101,
    1,
)
_first_line_v926101, _sep_v926101, _rest_v926101 = _source_v926101.partition("\n")
_source_v926101 = (
    "# V92.6.101 - Sua chinh ta nhan MENU: Dang ky nghi (2026-08-23)\n"
    + _rest_v926101
)

exec(
    compile(_source_v926101, str(_core_path_v926101), "exec"),
    globals(),
    globals(),
)
