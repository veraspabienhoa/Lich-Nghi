# V92.20.0 - PostgreSQL Phase 15 shift data + Phase 14/13/12/11/10/9/8/7/6/5/4 (2026-08-23)
"""VERA SPA V92.20.0.

Giữ nguyên V92.6.99, MENU V92.6.101 và PostgreSQL Phase 2/3/4/5/6/7/8/9.
Phase 4: CRUD Nhân viên + Lịch nghỉ ghi PostgreSQL trước, Google Sheets mirror.
Phase 5: credentials + leave_primary đọc PostgreSQL normalized làm nguồn chính.
Phase 6: TichLuy + NoViPham + PayrollHistory đọc PostgreSQL durable làm nguồn chính.
Phase 7: TichLuy ghi PostgreSQL trước; Google Sheets chỉ là mirror đồng bộ.
Phase 8: NoViPham + PayrollHistory ghi PostgreSQL trước, Google Sheets mirror;
bao gồm tạo/sửa/xóa nghĩa vụ, kết chuyển nợ sau lương, lưu/ghi đè/xóa lịch sử lương.
Phase 9: khi PostgreSQL đã cấu hình và VERA_DATA_BACKEND chưa được đặt rõ,
5 dataset nghiệp vụ đã migrate mặc định chạy ở chế độ postgres thay vì dual.
Các sheet cấu hình/UI/maintenance chưa migrate vẫn giữ nguyên đường Google Sheets
trực tiếp của core và không bị thay đổi bởi cutover dataset này.
Phase 10: khóa đăng ký theo vai trò, Auto Check RUNNING/PAUSED và cấu hình
giờ/ngưỡng nghỉ giữa ca đọc PostgreSQL-primary, ghi PostgreSQL trước rồi mirror Sheets.
Phase 11: Phân quyền chức năng và Chia sẻ bảng nhập liệu theo tài khoản
đọc/ghi PostgreSQL-primary; Google Sheets tiếp tục là mirror đồng bộ để rollback.
Phase 12: cấu hình giao diện Desktop/Mobile đọc/ghi PostgreSQL-primary;
Google Sheets tiếp tục là mirror đồng bộ cho cấu hình giao diện.
Phase 13: CauHinhMenuAdmin, CauHinhCot và CauHinhLuong đọc/ghi PostgreSQL-primary;
Google Sheets tiếp tục là mirror đồng bộ cho cấu hình MENU, cột và bảng lương.
Phase 14: TrangThaiNhanSu, NghiDaiHan và LichHenNhanSu đọc/ghi PostgreSQL-primary
bằng bản ghi durable có logical ID; Google Sheets tiếp tục là mirror đồng bộ.
Phase 15: CauHinhCaLamViec, CauHinhNghiGiuaCa và phân ca O:P:Q
đọc/ghi PostgreSQL-primary; phân ca dùng chung dataset credentials đã migrate.

Rollback tức thời:
- VERA_DATA_BACKEND=dual             -> quay 5 dataset về chế độ chuyển tiếp.
- VERA_DATA_BACKEND=sheets           -> quay lớp dữ liệu dùng chung về Sheets.
- VERA_PHASE4_WRITE_BACKEND=sheets   -> write-path Nhân viên/Lịch nghỉ về Sheets.
- VERA_PHASE5_READ_BACKEND=sheets    -> read-path Nhân viên/Lịch nghỉ về Sheets.
- VERA_PHASE6_READ_BACKEND=sheets    -> read-path TichLuy/Nợ/Lương về Sheets.
- VERA_PHASE7_TICHLUY_WRITE_BACKEND=sheets -> write-path TichLuy về Sheets.
- VERA_PHASE8_WRITE_BACKEND=sheets   -> write-path NoViPham/PayrollHistory về Sheets.
- VERA_PHASE10_SETTINGS_BACKEND=sheets -> 3 cấu hình điều khiển Phase 10 về Sheets.
- VERA_PHASE11_AUTH_BACKEND=sheets -> phân quyền/chia sẻ form Phase 11 về Sheets.
- VERA_PHASE12_UI_BACKEND=sheets -> cấu hình giao diện Phase 12 về Sheets.
- VERA_PHASE13_CONFIG_BACKEND=sheets -> MENU/cột/cấu hình lương Phase 13 về Sheets.
- VERA_PHASE14_OPERATIONS_BACKEND=sheets -> Trạng thái/Nghỉ dài hạn/Lịch hẹn Phase 14 về Sheets.
- VERA_PHASE15_SHIFT_BACKEND=sheets -> danh mục ca/nghỉ giữa ca/phân ca Phase 15 về Sheets.

Route, PAGE_FEATURE_KEYS, PAGE_SLUGS, phân quyền, giao diện và nghiệp vụ không đổi.
Hai Google Sheet cũ và ID của chúng không bị thay đổi.
"""
from pathlib import Path as _Path
import os as _os

_vpg_runtime = None
try:
    import vera_postgres as _vpg_runtime
    from vera_postgres_phase2 import install as _install_vpg_phase2

    # Phase 9: 5 dataset nghiệp vụ đã hoàn tất read/write cutover qua các Phase 4-8.
    # Chỉ tự chọn postgres khi deployment không đặt VERA_DATA_BACKEND rõ ràng.
    # Các vùng core còn gọi Google Sheets trực tiếp (config/UI/maintenance) không
    # đi qua biến này nên tiếp tục hoạt động bình thường trong các phase tiếp theo.
    if (
        callable(getattr(_vpg_runtime, "is_enabled", None))
        and _vpg_runtime.is_enabled()
        and not str(_os.getenv("VERA_DATA_BACKEND", "") or "").strip()
    ):
        _os.environ["VERA_DATA_BACKEND"] = "postgres"
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

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase5 import install as _install_vpg_phase5
        _install_vpg_phase5(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase6 import install as _install_vpg_phase6
        _install_vpg_phase6(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase7 import install as _install_vpg_phase7
        _install_vpg_phase7(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase8 import install as _install_vpg_phase8
        _install_vpg_phase8(_vpg_runtime)
    except Exception:
        pass


if _vpg_runtime is not None:
    try:
        from vera_postgres_phase10 import install as _install_vpg_phase10
        _install_vpg_phase10(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase11 import install as _install_vpg_phase11
        _install_vpg_phase11(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase12 import install as _install_vpg_phase12
        _install_vpg_phase12(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase13 import install as _install_vpg_phase13
        _install_vpg_phase13(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase14 import install as _install_vpg_phase14
        _install_vpg_phase14(_vpg_runtime)
    except Exception:
        pass

if _vpg_runtime is not None:
    try:
        from vera_postgres_phase15 import install as _install_vpg_phase15
        _install_vpg_phase15(_vpg_runtime)
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


_core_path_v92130 = _Path(__file__).with_name("app_v92699_core.py")
_source_v92130 = _core_path_v92130.read_text(encoding="utf-8")

_phase4_patch_warnings_v92130 = []
try:
    from vera_postgres_phase4_patch import apply as _apply_phase4_patches
    _source_v92130, _phase4_patch_warnings_v92130 = _apply_phase4_patches(_source_v92130)
except Exception as _phase4_patch_error_v92130:
    _phase4_patch_warnings_v92130 = [f"patch_module:{type(_phase4_patch_error_v92130).__name__}"]

_phase7_patch_warnings_v92130 = []
try:
    from vera_postgres_phase7_patch import apply as _apply_phase7_patches
    _source_v92130, _phase7_patch_warnings_v92130 = _apply_phase7_patches(_source_v92130)
except Exception as _phase7_patch_error_v92130:
    _phase7_patch_warnings_v92130 = [f"patch_module:{type(_phase7_patch_error_v92130).__name__}"]

_phase8_patch_warnings_v92130 = []
try:
    from vera_postgres_phase8_patch import apply as _apply_phase8_patches
    _source_v92130, _phase8_patch_warnings_v92130 = _apply_phase8_patches(_source_v92130)
except Exception as _phase8_patch_error_v92130:
    _phase8_patch_warnings_v92130 = [f"patch_module:{type(_phase8_patch_error_v92130).__name__}"]

_phase10_patch_warnings_v92130 = []
try:
    from vera_postgres_phase10_patch import apply as _apply_phase10_patches
    _source_v92130, _phase10_patch_warnings_v92130 = _apply_phase10_patches(_source_v92130)
except Exception as _phase10_patch_error_v92130:
    _phase10_patch_warnings_v92130 = [f"patch_module:{type(_phase10_patch_error_v92130).__name__}"]

_phase11_patch_warnings_v92130 = []
try:
    from vera_postgres_phase11_patch import apply as _apply_phase11_patches
    _source_v92130, _phase11_patch_warnings_v92130 = _apply_phase11_patches(_source_v92130)
except Exception as _phase11_patch_error_v92130:
    _phase11_patch_warnings_v92130 = [f"patch_module:{type(_phase11_patch_error_v92130).__name__}"]

_phase12_patch_warnings_v92130 = []
try:
    from vera_postgres_phase12_patch import apply as _apply_phase12_patches
    _source_v92130, _phase12_patch_warnings_v92130 = _apply_phase12_patches(_source_v92130)
except Exception as _phase12_patch_error_v92130:
    _phase12_patch_warnings_v92130 = [f"patch_module:{type(_phase12_patch_error_v92130).__name__}"]

_phase13_patch_warnings_v92130 = []
try:
    from vera_postgres_phase13_patch import apply as _apply_phase13_patches
    _source_v92130, _phase13_patch_warnings_v92130 = _apply_phase13_patches(_source_v92130)
except Exception as _phase13_patch_error_v92130:
    _phase13_patch_warnings_v92130 = [f"patch_module:{type(_phase13_patch_error_v92130).__name__}"]

_phase14_patch_warnings_v92130 = []
try:
    from vera_postgres_phase14_patch import apply as _apply_phase14_patches
    _source_v92130, _phase14_patch_warnings_v92130 = _apply_phase14_patches(_source_v92130)
except Exception as _phase14_patch_error_v92130:
    _phase14_patch_warnings_v92130 = [f"patch_module:{type(_phase14_patch_error_v92130).__name__}"]

_phase15_patch_warnings_v92130 = []
try:
    from vera_postgres_phase15_patch_fix import apply as _apply_phase15_patches
    _source_v92130, _phase15_patch_warnings_v92130 = _apply_phase15_patches(_source_v92130)
except Exception as _phase15_patch_error_v92130:
    _phase15_patch_warnings_v92130 = [f"patch_module:{type(_phase15_patch_error_v92130).__name__}"]

# Existing V92.6.101 display-only MENU patch.
_old_menu_map_v92130 = '_MENU_DISPLAY_LABELS_V92699 = {"🧾 Log Book": "Log Book"}'
_new_menu_map_v92130 = """_MENU_DISPLAY_LABELS_V92699 = {
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
if _old_menu_map_v92130 in _source_v92130:
    _source_v92130 = _source_v92130.replace(_old_menu_map_v92130, _new_menu_map_v92130, 1)
else:
    _phase4_patch_warnings_v92130.append("menu_display_labels:0")
_source_v92130 = _source_v92130.replace("MENU CHỨC NĂNG", "MENU")
_first_line_v92130, _sep_v92130, _rest_v92130 = _source_v92130.partition("\n")
_source_v92130 = (
    "# V92.20.0 - PostgreSQL Phase 15 shift data + Phase 14/13/12/11/10/9/8/7/6/5/4 (2026-08-23)\n"
    + _rest_v92130
)

if _phase4_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase4",
            "phase4_patch_warning",
            ",".join(_phase4_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase7_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase7",
            "phase7_patch_warning",
            ",".join(_phase7_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase8_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase8",
            "phase8_patch_warning",
            ",".join(_phase8_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase10_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase10",
            "phase10_patch_warning",
            ",".join(_phase10_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase11_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase11",
            "phase11_patch_warning",
            ",".join(_phase11_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase12_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase12",
            "phase12_patch_warning",
            ",".join(_phase12_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase13_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase13",
            "phase13_patch_warning",
            ",".join(_phase13_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase14_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase14",
            "phase14_patch_warning",
            ",".join(_phase14_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

if _phase15_patch_warnings_v92130 and _vpg_runtime is not None:
    try:
        _vpg_runtime.record_event(
            "phase15",
            "phase15_patch_warning",
            ",".join(_phase15_patch_warnings_v92130)[:1800],
        )
    except Exception:
        pass

exec(
    compile(_source_v92130, str(_core_path_v92130), "exec"),
    globals(),
    globals(),
)
