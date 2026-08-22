# V92.10.0 - PostgreSQL Phase 5 primary reads + Phase 4 primary writes (2026-08-23)
"""VERA SPA V92.10.0.

Giữ nguyên V92.6.99, MENU V92.6.101 và PostgreSQL Phase 2/3/4.
Phase 4: CRUD Nhân viên + Lịch nghỉ ghi PostgreSQL trước, Google Sheets mirror.
Phase 5: credentials + leave_primary đọc PostgreSQL normalized làm nguồn chính;
chỉ fallback Google Sheets một lượt khi normalized state stale/missing/lệch row-count.

Rollback tức thời:
- VERA_PHASE4_WRITE_BACKEND=sheets -> write-path cũ Google Sheets.
- VERA_PHASE5_READ_BACKEND=sheets  -> read-path cũ Google Sheets.

VERA_DATA_BACKEND vẫn mặc định dual để các dataset CHƯA migrate (TichLuy, payroll,
violation debt, config...) tiếp tục an toàn trong các phase sau.
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

# Phase 5 chỉ thay read-path credentials + leave_primary. Các dataset khác vẫn đi
# qua Phase 2/3 hiện hữu nên chưa bị cắt khỏi Google Sheets ngoài phạm vi đã migrate.
if _vpg_runtime is not None:
    try:
        from vera_postgres_phase5 import install as _install_vpg_phase5
        _install_vpg_phase5(_vpg_runtime)
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


_core_path_v92100 = _Path(__file__).with_name("app_v92699_core.py")
_source_v92100 = _core_path_v92100.read_text(encoding="utf-8")

_phase4_patch_warnings_v92100 = []
try:
    from vera_postgres_phase4_patch import apply as _apply_phase4_patches
    _source_v92100, _phase4_patch_warnings_v92100 = _apply_phase4_patches(_source_v92100)
except Exception as _phase4_patch_error_v92100:
    _phase4_patch_warnings_v92100 = [f"patch_module:{type(_phase4_patch_error_v92100).__name__}"]

# Existing V92.6.101 display-only MENU patch.
_old_menu_map_v92100 = '_MENU_DISPLAY_LABELS_V92699 = {"🧾 Log Book": "Log Book"}'
_new_menu_map_v92100 = """_MENU_DISPLAY_LABELS_V92699 = {
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
if _old_menu_map_v92100 in _source_v92100:
    _source_v92100 = _source_v92100.replace(_old_menu_map_v92100, _new_menu_map_v92100, 1)
else:
    _phase4_patch_warnings_v92100.append("menu_display_labels:0")
_source_v92100 = _source_v92100.replace("MENU CHỨC NĂNG", "MENU")
_first_line_v92100, _sep_v92100, _rest_v92100 = _source_v92100.partition("\n")
_source_v92100 = (
    "# V92.10.0 - PostgreSQL Phase 5 primary reads + Phase 4 primary writes (2026-08-23)\n"
    + _rest_v92100
)

if _phase4_patch_warnings_v92100 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase4",
            "phase4_patch_warning",
            ",".join(_phase4_patch_warnings_v92100)[:1800],
        )
    except Exception:
        pass

exec(
    compile(_source_v92100, str(_core_path_v92100), "exec"),
    globals(),
    globals(),
)
