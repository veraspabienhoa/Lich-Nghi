# V92.9.0 - PostgreSQL Phase 4 primary writes + MENU display labels (2026-08-23)
"""VERA SPA V92.9.0.

Giữ nguyên V92.6.99, MENU V92.6.101 và PostgreSQL Phase 2/3.
Phase 4 chuyển CRUD Nhân viên + Lịch nghỉ sang PostgreSQL-first, sau đó mirror
Google Sheets. Nếu mirror lỗi, PostgreSQL được compensate về trạng thái trước đó.
Đặt VERA_PHASE4_WRITE_BACKEND=sheets để quay ngay về write-path Google Sheets cũ.

Route, PAGE_FEATURE_KEYS, PAGE_SLUGS, phân quyền, giao diện và nghiệp vụ không đổi.
Hai Google Sheet cũ và ID của chúng không bị thay đổi.
"""
from pathlib import Path as _Path
import os as _os

_vpg_runtime = None
try:
    import vera_postgres as _vpg_runtime
    from vera_postgres_phase2 import install as _install_vpg_phase2

    if (
        callable(getattr(_vpg_runtime, "is_enabled", None))
        and _vpg_runtime.is_enabled()
        and not str(_os.getenv("VERA_DATA_BACKEND", "") or "").strip()
    ):
        _os.environ["VERA_DATA_BACKEND"] = "dual"
    _install_vpg_phase2(_vpg_runtime)
except Exception:
    _vpg_runtime = None

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase3 import install as _install_vpg_phase3
        _install_vpg_phase3(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase4 import install as _install_vpg_phase4
        _install_vpg_phase4(_vpg_runtime)
    except Exception:
        pass


def _phase4_call(method, mirror_fn, *args, **kwargs):
    fn = getattr(_vpg_runtime, method, None) if _vpg_runtime is not None else None
    if callable(fn):
        return fn(*args, mirror_fn=mirror_fn, **kwargs)
    return mirror_fn()


def _vera_phase4_employee_upsert(record, mirror_fn, operation="upsert"):
    return _phase4_call("phase4_employee_upsert", mirror_fn, record, operation=operation)


def _vera_phase4_employee_batch_upsert(records, mirror_fn, operation="batch_upsert"):
    return _phase4_call("phase4_employee_batch_upsert", mirror_fn, records, operation=operation)


def _vera_phase4_employee_delete(usernames, mirror_fn, operation="delete"):
    return _phase4_call("phase4_employee_delete", mirror_fn, usernames, operation=operation)


def _vera_phase4_leave_upsert(record, mirror_fn, operation="upsert"):
    return _phase4_call("phase4_leave_upsert", mirror_fn, record, operation=operation)


def _vera_phase4_leave_batch_upsert(records, mirror_fn, operation="batch_upsert"):
    return _phase4_call("phase4_leave_batch_upsert", mirror_fn, records, operation=operation)


def _vera_phase4_leave_delete(records, mirror_fn, operation="delete"):
    return _phase4_call("phase4_leave_delete", mirror_fn, records, operation=operation)


_core_path_v9290 = _Path(__file__).with_name("app_v92699_core.py")
_source_v9290 = _core_path_v9290.read_text(encoding="utf-8")

_phase4_patch_warnings_v9290 = []
try:
    from vera_postgres_phase4_patch import apply as _apply_phase4_patches
    _source_v9290, _phase4_patch_warnings_v9290 = _apply_phase4_patches(_source_v9290)
except Exception as _phase4_patch_error_v9290:
    _phase4_patch_warnings_v9290 = [f"patch_module:{type(_phase4_patch_error_v9290).__name__}"]

# Existing V92.6.101 display-only MENU patch.
_old_menu_map_v9290 = '_MENU_DISPLAY_LABELS_V92699 = {"🧾 Log Book": "Log Book"}'
_new_menu_map_v9290 = """_MENU_DISPLAY_LABELS_V92699 = {
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
if _old_menu_map_v9290 in _source_v9290:
    _source_v9290 = _source_v9290.replace(_old_menu_map_v9290, _new_menu_map_v9290, 1)
else:
    _phase4_patch_warnings_v9290.append("menu_display_labels:0")
_source_v9290 = _source_v9290.replace("MENU CHỨC NĂNG", "MENU")
_first_line_v9290, _sep_v9290, _rest_v9290 = _source_v9290.partition("\n")
_source_v9290 = (
    "# V92.9.0 - PostgreSQL Phase 4 primary writes + MENU display labels (2026-08-23)\n"
    + _rest_v9290
)

if _phase4_patch_warnings_v9290 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase4",
            "phase4_patch_warning",
            ",".join(_phase4_patch_warnings_v9290)[:1800],
        )
    except Exception:
        pass

exec(
    compile(_source_v9290, str(_core_path_v9290), "exec"),
    globals(),
    globals(),
)
