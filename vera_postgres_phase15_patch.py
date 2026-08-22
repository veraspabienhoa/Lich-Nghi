"""Source hooks for Phase 15 PostgreSQL-primary shift data."""
from __future__ import annotations

import ast
import re


MARKER = "_PHASE15_SHIFT_PATCH_V1 = True"
TARGETS = {
    "_apply_employee_shift_plan": "_phase15_legacy_apply_employee_shift_plan",
    "_replace_shift_assignment": "_phase15_legacy_replace_shift_assignment",
    "load_shift_definitions": "_phase15_legacy_load_shift_definitions",
    "save_shift_definition": "_phase15_legacy_save_shift_definition",
    "delete_shift_definition": "_phase15_legacy_delete_shift_definition",
    "load_shift_break_config": "_phase15_legacy_load_shift_break_config",
    "save_shift_break_config": "_phase15_legacy_save_shift_break_config",
    "batch_update_shift_schedule": "_phase15_legacy_batch_update_shift_schedule",
}


HELPER_BLOCK = r'''
_PHASE15_SHIFT_PATCH_V1 = True
_PHASE15_SHIFT_DEFINITIONS_KEY = "shift_definitions"
_PHASE15_SHIFT_BREAK_CONFIG_KEY = "shift_break_config"


def _phase15_read_config(key, source_loader, default):
    if vpg is not None:
        fn = getattr(vpg, "phase15_read_config", None)
        if callable(fn):
            return fn(key, source_loader, default=default)
    try:
        result = source_loader()
    except Exception as exc:
        return default, f"{type(exc).__name__}: {exc}"
    if isinstance(result, tuple) and len(result) >= 2:
        return result[0], str(result[1] or "")
    return result, ""


def _phase15_commit_config(key, value, mirror_fn, updated_by="", confirm_fn=None):
    if vpg is not None:
        fn = getattr(vpg, "phase15_commit_config", None)
        if callable(fn):
            return fn(
                key, value, mirror_fn,
                updated_by=updated_by, confirm_fn=confirm_fn,
            )
    return mirror_fn()


def _phase15_shift_records_from_df(df):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in [
            "ID", "Tên ca", "Giờ bắt đầu", "Giờ kết thúc", "Ghi chú",
            "Thứ tự", "Trạng thái", "Bộ phận",
            "Áp dụng nghỉ giữa ca", "Duration nghỉ giữa ca (phút)",
            "Khoảng gom FaceID (phút)", "__row",
        ]:
            value = row.get(col, "")
            try:
                if pd.isna(value):
                    value = ""
            except Exception:
                pass
            if hasattr(value, "item"):
                try:
                    value = value.item()
                except Exception:
                    pass
            rec[col] = value
        records.append(rec)
    return records


def _phase15_shift_records_to_df(records):
    columns = [
        "ID", "Tên ca", "Giờ bắt đầu", "Giờ kết thúc", "Ghi chú",
        "Thứ tự", "Trạng thái", "Bộ phận",
        "Áp dụng nghỉ giữa ca", "Duration nghỉ giữa ca (phút)",
        "Khoảng gom FaceID (phút)", "__row",
    ]
    rows = []
    for raw in records or []:
        rec = dict(raw or {})
        try:
            order = int(float(rec.get("Thứ tự", 999) or 999))
        except Exception:
            order = 999
        dep = str(rec.get("Bộ phận", "") or "").strip() or "Nhân viên + Leader"
        if dep not in SHIFT_DEPARTMENT_ORDER:
            dep = "Nhân viên + Leader"

        be = rec.get("Áp dụng nghỉ giữa ca", False)
        if isinstance(be, str):
            be = be.strip().lower() in {
                "1", "true", "yes", "on", "có", "co", "bật", "bat"
            }
        else:
            be = bool(be)
        try:
            duration = max(1, min(600, int(float(rec.get("Duration nghỉ giữa ca (phút)", 60) or 60))))
        except Exception:
            duration = int(SHIFT_BREAK_DEFAULTS.get(dep, {}).get("duration_minutes", 60))
        try:
            cluster = max(1, min(60, int(float(rec.get("Khoảng gom FaceID (phút)", 10) or 10))))
        except Exception:
            cluster = 10
        try:
            source_row = int(float(rec.get("__row", 0) or 0))
        except Exception:
            source_row = 0

        rows.append({
            "ID": str(rec.get("ID", "") or "").strip(),
            "Tên ca": str(rec.get("Tên ca", "") or "").strip(),
            "Giờ bắt đầu": str(rec.get("Giờ bắt đầu", "") or "").strip(),
            "Giờ kết thúc": str(rec.get("Giờ kết thúc", "") or "").strip(),
            "Ghi chú": str(rec.get("Ghi chú", "") or "").strip(),
            "Thứ tự": order,
            "Trạng thái": str(rec.get("Trạng thái", "") or "").strip() or "Đang dùng",
            "Bộ phận": dep,
            "Áp dụng nghỉ giữa ca": bool(be),
            "Duration nghỉ giữa ca (phút)": int(duration),
            "Khoảng gom FaceID (phút)": int(cluster),
            "__row": source_row,
        })
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["Bộ phận", "Thứ tự", "Tên ca"], kind="stable"
    ).reset_index(drop=True)


def _phase15_shift_source():
    df = _phase15_legacy_load_shift_definitions()
    records = _phase15_shift_records_from_df(df)
    # Legacy loader returns the built-in SHIFT001..SHIFT004 fallback with __row=0
    # when Sheets is unavailable. Serve it, but never persist a transient fallback.
    if records:
        real_rows = []
        for rec in records:
            try:
                real_rows.append(int(float(rec.get("__row", 0) or 0)))
            except Exception:
                real_rows.append(0)
        ids = {str(x.get("ID", "") or "").strip() for x in records}
        if all(x <= 0 for x in real_rows) and ids.issubset({"SHIFT001", "SHIFT002", "SHIFT003", "SHIFT004"}):
            return records, "Google Sheets shift source unavailable; using transient fallback."
    return records, ""


def _phase15_break_default():
    return {
        dep: dict(SHIFT_BREAK_DEFAULTS[dep])
        for dep in SHIFT_DEPARTMENT_ORDER
    }


def _phase15_break_source():
    ws = _get_shift_break_config_worksheet()
    if ws is None:
        return _phase15_break_default(), "Không mở được sheet cấu hình nghỉ giữa ca."
    return _phase15_legacy_load_shift_break_config(), ""


def _phase15_call_with_legacy_shift_loader(fn):
    current = globals().get("load_shift_definitions")
    globals()["load_shift_definitions"] = _phase15_legacy_load_shift_definitions
    try:
        return fn()
    finally:
        if current is not None:
            globals()["load_shift_definitions"] = current


def _phase15_next_shift_snapshot(
    current_records, shift_id, name, start_time, end_time, note, order,
    department, break_enabled, break_duration_minutes, new_shift_id,
    faceid_cluster_minutes,
):
    name = str(name or "").strip()
    department = str(department or "").strip()
    if department not in SHIFT_DEPARTMENT_ORDER:
        return False, "Bộ phận không hợp lệ.", current_records, ""
    if not name:
        return False, "Tên ca không được để trống.", current_records, ""

    shift_id = str(shift_id or "").strip()
    requested = str(new_shift_id if new_shift_id is not None else shift_id).strip()
    if not shift_id and not requested:
        requested = "SHIFT-" + datetime.now(VN_TZ).strftime("%Y%m%d%H%M%S%f")
    if not requested:
        return False, "ID ca không được để trống.", current_records, ""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", requested):
        return (
            False,
            "ID ca chỉ được dùng chữ, số và các ký tự . _ - (không dùng khoảng trắng/ký tự đặc biệt).",
            current_records,
            "",
        )

    out = [dict(x) for x in (current_records or [])]
    for rec in out:
        rid = str(rec.get("ID", "") or "").strip()
        state = str(rec.get("Trạng thái", "") or "").strip().lower()
        if state == "đã xóa":
            continue
        if rid == requested and (not shift_id or rid != shift_id):
            return False, f"ID ca '{requested}' đang được một ca khác sử dụng.", current_records, ""

    try:
        duration = max(1, min(600, int(float(break_duration_minutes))))
    except Exception:
        duration = 60
    try:
        cluster = max(1, min(60, int(float(faceid_cluster_minutes))))
    except Exception:
        cluster = 10
    try:
        order_value = int(order or 999)
    except Exception:
        order_value = 999

    target_idx = None
    source_row = 0
    if shift_id:
        for i, rec in enumerate(out):
            if str(rec.get("ID", "") or "").strip() == shift_id:
                target_idx = i
                try:
                    source_row = int(float(rec.get("__row", 0) or 0))
                except Exception:
                    source_row = 0
                break

    candidate = {
        "ID": requested,
        "Tên ca": name,
        "Giờ bắt đầu": str(start_time or "").strip(),
        "Giờ kết thúc": str(end_time or "").strip(),
        "Ghi chú": str(note or "").strip(),
        "Thứ tự": order_value,
        "Trạng thái": "Đang dùng",
        "Bộ phận": department,
        "Áp dụng nghỉ giữa ca": bool(break_enabled),
        "Duration nghỉ giữa ca (phút)": int(duration),
        "Khoảng gom FaceID (phút)": int(cluster),
        "__row": source_row,
    }
    if target_idx is None:
        out.append(candidate)
    else:
        out[target_idx] = candidate
    return True, "", out, requested


def _phase15_full_employee_record(row):
    if hasattr(row, "to_dict"):
        rec = row.to_dict()
    else:
        rec = dict(row or {})
    return rec


def _phase15_employee_upsert(record, mirror_fn, operation):
    if vpg is not None:
        fn = getattr(vpg, "phase15_employee_upsert", None)
        if callable(fn):
            return fn(record, mirror_fn=mirror_fn, operation=operation)
    return mirror_fn()


def _phase15_employee_batch_upsert(records, mirror_fn, operation):
    if vpg is not None:
        fn = getattr(vpg, "phase15_employee_batch_upsert", None)
        if callable(fn):
            return fn(records, mirror_fn=mirror_fn, operation=operation)
    return mirror_fn()


def _phase15_return_mirror_failure(captured, exc, fallback_prefix):
    result = captured.get("result")
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], bool) and result[0] is False:
        return result
    return False, f"{fallback_prefix}: {exc}"
'''.strip("\n")


WRAPPERS = {
    "_phase15_legacy_apply_employee_shift_plan": r'''
def _apply_employee_shift_plan(employee_name, shift_name, effective_date, cycle=""):
    employee_name = str(employee_name or "").strip()
    shift_name = str(shift_name or "").strip()
    if not employee_name or not shift_name:
        return False, "Thiếu nhân viên hoặc ca áp dụng."

    creds = load_credentials_recent()
    if not isinstance(creds, pd.DataFrame) or creds.empty or "Tên nhân viên" not in creds.columns:
        return _phase15_legacy_apply_employee_shift_plan(
            employee_name, shift_name, effective_date, cycle
        )
    hit = creds[
        creds["Tên nhân viên"].astype(str).apply(normalize_login_name)
        .eq(normalize_login_name(employee_name))
    ]
    if hit.empty:
        return _phase15_legacy_apply_employee_shift_plan(
            employee_name, shift_name, effective_date, cycle
        )

    rec = _phase15_full_employee_record(hit.iloc[-1])
    rec["Ca làm việc"] = shift_name
    rec["Ngày bắt đầu ca"] = (
        effective_date.strftime("%d/%m/%Y")
        if isinstance(effective_date, date) else str(effective_date or "")
    )
    rec["Chu kỳ"] = str(cycle or "").strip()
    captured = {}

    def _mirror():
        captured["result"] = _phase15_legacy_apply_employee_shift_plan(
            employee_name, shift_name, effective_date, cycle
        )
        return captured["result"]

    try:
        return _phase15_employee_upsert(rec, _mirror, "phase15_staff_plan_shift")
    except Exception as exc:
        return _phase15_return_mirror_failure(
            captured, exc, "Lỗi áp dụng ca"
        )
'''.strip("\n"),

    "_phase15_legacy_replace_shift_assignment": r'''
def _replace_shift_assignment(old_label, new_label):
    old_label = str(old_label or "").strip()
    new_label = str(new_label or "").strip()
    if not old_label or old_label == new_label:
        return True, ""

    try:
        creds = load_credentials_recent()
        records = []
        if isinstance(creds, pd.DataFrame) and not creds.empty:
            for _, row in creds.iterrows():
                if str(row.get("Ca làm việc", "") or "").strip() != old_label:
                    continue
                rec = _phase15_full_employee_record(row)
                rec["Ca làm việc"] = new_label
                records.append(rec)

        captured = {}
        def _mirror():
            captured["result"] = _phase15_legacy_replace_shift_assignment(
                old_label, new_label
            )
            return captured["result"]

        return _phase15_employee_batch_upsert(
            records, _mirror, "phase15_replace_shift_assignment"
        )
    except Exception as exc:
        return False, f"Lỗi cập nhật phân ca hiện tại: {exc}"
'''.strip("\n"),

    "_phase15_legacy_load_shift_definitions": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_shift_definitions():
    try:
        fallback_df = _phase15_legacy_load_shift_definitions()
        fallback = _phase15_shift_records_from_df(fallback_df)
        records, _ = _phase15_read_config(
            _PHASE15_SHIFT_DEFINITIONS_KEY,
            _phase15_shift_source,
            fallback,
        )
        return _phase15_shift_records_to_df(records)
    except Exception:
        return _phase15_legacy_load_shift_definitions()
'''.strip("\n"),

    "_phase15_legacy_save_shift_definition": r'''
def save_shift_definition(
    shift_id, name, start_time, end_time, note, order, username,
    department="Nhân viên + Leader",
    break_enabled=False,
    break_duration_minutes=60,
    new_shift_id=None,
    faceid_cluster_minutes=10,
):
    current_df = load_shift_definitions()
    current_records = _phase15_shift_records_from_df(current_df)
    ok, msg, next_records, requested_id = _phase15_next_shift_snapshot(
        current_records, shift_id, name, start_time, end_time, note, order,
        department, break_enabled, break_duration_minutes, new_shift_id,
        faceid_cluster_minutes,
    )
    if not ok:
        return False, msg

    def _mirror():
        return _phase15_call_with_legacy_shift_loader(
            lambda: _phase15_legacy_save_shift_definition(
                shift_id, name, start_time, end_time, note, order, username,
                department=department,
                break_enabled=break_enabled,
                break_duration_minutes=break_duration_minutes,
                new_shift_id=requested_id,
                faceid_cluster_minutes=faceid_cluster_minutes,
            )
        )

    result = _phase15_commit_config(
        _PHASE15_SHIFT_DEFINITIONS_KEY,
        next_records,
        _mirror,
        updated_by=str(username or ""),
        confirm_fn=_phase15_shift_source,
    )
    try:
        load_shift_definitions.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase15_legacy_delete_shift_definition": r'''
def delete_shift_definition(shift_id, username):
    target_id = str(shift_id or "").strip()
    if not target_id:
        return False, "ID ca không hợp lệ."

    current = _phase15_shift_records_from_df(load_shift_definitions())
    matched = [x for x in current if str(x.get("ID", "") or "").strip() == target_id]
    if not matched:
        return False, f"Không tìm thấy ca ID {target_id} trong sheet cấu hình."
    next_records = [
        dict(x) for x in current
        if str(x.get("ID", "") or "").strip() != target_id
    ]

    result = _phase15_commit_config(
        _PHASE15_SHIFT_DEFINITIONS_KEY,
        next_records,
        lambda: _phase15_legacy_delete_shift_definition(target_id, username),
        updated_by=str(username or ""),
        confirm_fn=_phase15_shift_source,
    )
    try:
        load_shift_definitions.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase15_legacy_load_shift_break_config": r'''
@st.cache_data(ttl=300, show_spinner=False)
def load_shift_break_config():
    default = _phase15_break_default()
    try:
        value, _ = _phase15_read_config(
            _PHASE15_SHIFT_BREAK_CONFIG_KEY,
            _phase15_break_source,
            default,
        )
        result = _phase15_break_default()
        if isinstance(value, dict):
            for dep in SHIFT_DEPARTMENT_ORDER:
                raw = value.get(dep, {}) if isinstance(value.get(dep, {}), dict) else {}
                enabled = raw.get("enabled", result[dep]["enabled"])
                if isinstance(enabled, str):
                    enabled = enabled.strip().lower() in {
                        "1", "true", "yes", "on", "có", "co", "bật", "bat"
                    }
                try:
                    duration = max(
                        1, min(600, int(float(
                            raw.get("duration_minutes", result[dep]["duration_minutes"])
                        )))
                    )
                except Exception:
                    duration = int(result[dep]["duration_minutes"])
                result[dep] = {
                    "enabled": bool(enabled),
                    "duration_minutes": int(duration),
                }
        return result
    except Exception:
        return _phase15_legacy_load_shift_break_config()
'''.strip("\n"),

    "_phase15_legacy_save_shift_break_config": r'''
def save_shift_break_config(department, enabled, duration_minutes, username):
    department = str(department or "").strip()
    if department not in SHIFT_DEPARTMENT_ORDER:
        return False, "Bộ phận không hợp lệ."
    try:
        duration = max(1, min(600, int(float(duration_minutes))))
    except Exception as exc:
        return False, f"Lỗi lưu cấu hình nghỉ giữa ca: {exc}"

    current = load_shift_break_config()
    next_value = {
        dep: dict(current.get(dep, SHIFT_BREAK_DEFAULTS[dep]))
        for dep in SHIFT_DEPARTMENT_ORDER
    }
    next_value[department] = {
        "enabled": bool(enabled),
        "duration_minutes": int(duration),
    }

    result = _phase15_commit_config(
        _PHASE15_SHIFT_BREAK_CONFIG_KEY,
        next_value,
        lambda: _phase15_legacy_save_shift_break_config(
            department, enabled, duration, username
        ),
        updated_by=str(username or ""),
        confirm_fn=_phase15_break_source,
    )
    try:
        load_shift_break_config.clear()
    except Exception:
        pass
    return result
'''.strip("\n"),

    "_phase15_legacy_batch_update_shift_schedule": r'''
def batch_update_shift_schedule(edited_df):
    try:
        creds = load_credentials_recent()
        if not isinstance(creds, pd.DataFrame) or creds.empty:
            return _phase15_legacy_batch_update_shift_schedule(edited_df)

        shift_map = {}
        for _, row in edited_df.iterrows():
            key = normalize_login_name(row.get("Tên nhân viên", ""))
            if not key:
                continue
            def _clean(value):
                text = str(value or "").strip()
                return "" if text.lower() in {"", "none", "nan", "<na>"} else text
            shift_map[key] = {
                "Ca làm việc": _clean(row.get("Ca làm việc", "")),
                "Ngày bắt đầu ca": _clean(row.get("Ngày bắt đầu ca", "")),
                "Chu kỳ": _clean(row.get("Chu kỳ", "")),
            }

        records = []
        for _, row in creds.iterrows():
            key = normalize_login_name(row.get("Tên nhân viên", ""))
            if key not in shift_map:
                continue
            rec = _phase15_full_employee_record(row)
            rec.update(shift_map[key])
            records.append(rec)

        captured = {}
        def _mirror():
            captured["result"] = _phase15_legacy_batch_update_shift_schedule(edited_df)
            return captured["result"]

        try:
            return _phase15_employee_batch_upsert(
                records, _mirror, "phase15_batch_shift_schedule"
            )
        except Exception as exc:
            return _phase15_return_mirror_failure(
                captured, exc, "Lỗi cập nhật"
            )
    except Exception as exc:
        return False, f"Lỗi cập nhật: {exc}"
'''.strip("\n"),
}


def _rename_top_level_function(source, old, new):
    pattern = re.compile(
        rf"(?m)^def\s+{re.escape(old)}\s*\("
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        return source, len(matches)
    return pattern.sub(f"def {new}(", source, count=1), 1


def apply(source):
    warnings = []
    if MARKER in source:
        return source, warnings

    renamed = source
    for old, new in TARGETS.items():
        renamed, count = _rename_top_level_function(renamed, old, new)
        if count != 1:
            warnings.append(f"phase15_rename_{old}:{count}")

    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        return source, warnings + [f"phase15_parse_after_rename:{type(exc).__name__}"]

    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in set(TARGETS.values())
    }
    missing = [name for name in TARGETS.values() if name not in nodes]
    if missing:
        warnings.extend(f"phase15_node_{name}:0" for name in missing)
        return source, warnings

    lines = renamed.splitlines(keepends=True)
    insertions = []
    first_node = min((nodes[n] for n in TARGETS.values()), key=lambda x: x.lineno)
    first_start = int(first_node.lineno)
    if getattr(first_node, "decorator_list", None):
        first_start = min(first_start, *(int(d.lineno) for d in first_node.decorator_list))
    insertions.append((max(0, first_start - 1), HELPER_BLOCK + "\n\n"))

    for legacy, wrapper in WRAPPERS.items():
        node = nodes[legacy]
        insertions.append(
            (int(getattr(node, "end_lineno", node.lineno)), "\n\n" + wrapper + "\n")
        )

    for idx, block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(idx, block)

    patched = "".join(lines)
    try:
        ast.parse(patched)
    except Exception as exc:
        return source, warnings + [f"phase15_parse_patched:{type(exc).__name__}"]
    return patched, warnings
