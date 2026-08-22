"""Source hooks for Phase 14 PostgreSQL-primary operational HR records."""
from __future__ import annotations

import ast
import re


MARKER = "_PHASE14_OPERATIONAL_PATCH_V1 = True"
TARGETS = {
    "load_employment_status_map": "_phase14_legacy_load_employment_status_map",
    "set_employee_employment_status": "_phase14_legacy_set_employee_employment_status",
    "load_long_leave_requests": "_phase14_legacy_load_long_leave_requests",
    "append_long_leave_request": "_phase14_legacy_append_long_leave_request",
    "update_long_leave_row": "_phase14_legacy_update_long_leave_row",
    "delete_long_leave_row": "_phase14_legacy_delete_long_leave_row",
    "load_staff_schedule_plans": "_phase14_legacy_load_staff_schedule_plans",
    "append_staff_schedule_plan": "_phase14_legacy_append_staff_schedule_plan",
    "update_staff_schedule_plan_row": "_phase14_legacy_update_staff_schedule_plan_row",
}


HELPER_BLOCK = r"""
_PHASE14_OPERATIONAL_PATCH_V1 = True
_PHASE14_EMPLOYMENT_DATASET = "employment_status"
_PHASE14_LONG_LEAVE_DATASET = "long_leave"
_PHASE14_STAFF_PLAN_DATASET = "staff_plan"


class _Phase14BusinessError(Exception):
    pass


def _phase14_current_user():
    try:
        return str(st.session_state.get("current_user", "") or "")
    except Exception:
        return ""


def _phase14_id(prefix, value, fallback_payload=None):
    value = str(value or "").strip()
    if value:
        return f"{prefix}:{value}"
    raw = json.dumps(fallback_payload or {}, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}:legacy:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:18]}"


def _phase14_read_records(dataset, source_loader):
    if vpg is not None:
        fn = getattr(vpg, "phase14_read_records", None)
        if callable(fn):
            return fn(dataset, source_loader)
    try:
        return source_loader()
    except Exception:
        return []


def _phase14_mutate_records(dataset, source_loader, mutator, mirror_fn, updated_by="", confirm_fn=None):
    if vpg is not None:
        fn = getattr(vpg, "phase14_mutate_records", None)
        if callable(fn):
            return fn(
                dataset, source_loader, mutator, mirror_fn,
                updated_by=updated_by, confirm_fn=confirm_fn,
            )
    return mirror_fn()


def _phase14_next_row_hint(records):
    max_row = 1
    for rec in records or []:
        try:
            max_row = max(max_row, int(rec.get("__row", 0) or 0))
        except Exception:
            pass
    return max_row + 1


def _phase14_clean_record(rec):
    out = dict(rec or {})
    out.pop("__phase14_id", None)
    return out


def _phase14_df(records, headers):
    rows = []
    for rec0 in records or []:
        rec = _phase14_clean_record(rec0)
        row = {h: rec.get(h, "") for h in headers}
        if "__row" in rec:
            row["__row"] = rec.get("__row")
        rows.append(row)
    cols = list(headers) + ["__row"]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)


def _phase14_employment_source_records():
    ws = _get_employment_status_worksheet()
    if ws is None:
        raise RuntimeError("Không kết nối được Google Sheets.")
    vals = _gs_call_with_backoff(ws.get_all_values)
    records = []
    for ridx, row in enumerate(vals[1:], start=2):
        rr = list(row[:len(EMPLOYMENT_STATUS_HEADERS)]) + [""] * max(
            0, len(EMPLOYMENT_STATUS_HEADERS) - len(row)
        )
        if not any(str(v).strip() for v in rr):
            continue
        rec = dict(zip(EMPLOYMENT_STATUS_HEADERS, rr[:len(EMPLOYMENT_STATUS_HEADERS)]))
        key = normalize_login_name(rec.get("Tên nhân viên", ""))
        if not key:
            continue
        rec["Trạng thái"] = EMPLOYMENT_STATUS_ALIASES.get(
            normalize_login_name(rec.get("Trạng thái", "")),
            EMPLOYMENT_STATUS_ACTIVE,
        )
        rec["__row"] = ridx
        rec["__phase14_id"] = _phase14_id("emp", key)
        records.append(rec)
    return records


def _phase14_long_leave_source_records():
    ws = _get_long_leave_worksheet()
    if ws is None:
        raise RuntimeError("Không kết nối được Google Sheets.")
    vals = _gs_call_with_backoff(ws.get_all_values)
    records = []
    for ridx, row in enumerate(vals[1:], start=2):
        rr = list(row[:len(LONG_LEAVE_HEADERS)]) + [""] * max(
            0, len(LONG_LEAVE_HEADERS) - len(row)
        )
        if not any(str(v).strip() for v in rr):
            continue
        rec = dict(zip(LONG_LEAVE_HEADERS, rr[:len(LONG_LEAVE_HEADERS)]))
        rec["__row"] = ridx
        rec["__phase14_id"] = _phase14_id(
            "long", rec.get("ID", ""),
            {"row": ridx, "record": {k: rec.get(k, "") for k in LONG_LEAVE_HEADERS}},
        )
        records.append(rec)
    return records


def _phase14_staff_plan_source_records():
    ws = _get_staff_plan_worksheet()
    if ws is None:
        raise RuntimeError("Không kết nối được Google Sheets.")
    vals = _gs_call_with_backoff(ws.get_all_values)
    records = []
    for ridx, row in enumerate(vals[1:], start=2):
        rr = list(row[:len(STAFF_PLAN_HEADERS)]) + [""] * max(
            0, len(STAFF_PLAN_HEADERS) - len(row)
        )
        if not any(str(v).strip() for v in rr):
            continue
        rec = dict(zip(STAFF_PLAN_HEADERS, rr[:len(STAFF_PLAN_HEADERS)]))
        rec["__row"] = ridx
        rec["__phase14_id"] = _phase14_id(
            "plan", rec.get("ID", ""),
            {"row": ridx, "record": {k: rec.get(k, "") for k in STAFF_PLAN_HEADERS}},
        )
        records.append(rec)
    return records


def _phase14_find_by_row(records, row_idx):
    try:
        row_idx = int(row_idx)
    except Exception:
        return None
    for rec in records or []:
        try:
            if int(rec.get("__row", 0) or 0) == row_idx:
                return rec
        except Exception:
            continue
    return None


def _phase14_mirror_append_long(row):
    try:
        ws = _get_long_leave_worksheet()
        if ws is None:
            return False, "Không kết nối được Google Sheets.", ""
        _gs_call_with_backoff(
            ws.append_row,
            [row.get(h, "") for h in LONG_LEAVE_HEADERS],
            value_input_option="USER_ENTERED",
        )
        return True, "Đã lưu yêu cầu.", str(row.get("ID", "") or "")
    except Exception as e:
        return False, f"Lỗi lưu yêu cầu: {e}", ""


def _phase14_mirror_append_staff_plan(row):
    try:
        ws = _get_staff_plan_worksheet()
        if ws is None:
            return False, "Không kết nối được Google Sheets để lưu kế hoạch.", ""
        _gs_call_with_backoff(
            ws.append_row,
            [row.get(h, "") for h in STAFF_PLAN_HEADERS],
            value_input_option="USER_ENTERED",
        )
        return True, "Đã lưu kế hoạch/hẹn ngày nhân sự.", str(row.get("ID", "") or "")
    except Exception as e:
        return False, f"Lỗi lưu kế hoạch nhân sự: {e}", ""
""".strip("\n")


WRAPPERS = {
    "_phase14_legacy_load_employment_status_map": r"""
@st.cache_data(ttl=120, show_spinner=False)
def load_employment_status_map():
    result = {}
    records = _phase14_read_records(
        _PHASE14_EMPLOYMENT_DATASET,
        _phase14_employment_source_records,
    )
    for rec in records or []:
        key = normalize_login_name(rec.get("Tên nhân viên", ""))
        if not key:
            continue
        status = EMPLOYMENT_STATUS_ALIASES.get(
            normalize_login_name(rec.get("Trạng thái", "")),
            EMPLOYMENT_STATUS_ACTIVE,
        )
        result[key] = status
    return result
""".strip("\n"),

    "_phase14_legacy_set_employee_employment_status": r"""
def set_employee_employment_status(employee_name, status, updated_by):
    status = EMPLOYMENT_STATUS_ALIASES.get(normalize_login_name(status), "")
    if status not in EMPLOYMENT_STATUS_OPTIONS:
        return False, "Trạng thái không hợp lệ."

    try:
        creds = load_credentials_recent()
        target_key = normalize_login_name(employee_name)
        role = ""
        if isinstance(creds, pd.DataFrame) and not creds.empty:
            hit = creds[creds["Tên nhân viên"].apply(normalize_login_name) == target_key]
            if not hit.empty:
                role = str(hit.iloc[0].get("Phân quyền", "")).strip().lower()
        if role not in EMPLOYMENT_STATUS_MANAGEABLE_ROLES:
            return False, "Chỉ có thể cập nhật trạng thái làm việc cho tài khoản nhân sự."

        now = datetime.now(VN_TZ)

        def _p14_mutate(records):
            out = [dict(x) for x in records]
            current = next(
                (x for x in out if normalize_login_name(x.get("Tên nhân viên", "")) == target_key),
                None,
            )
            if current is None:
                stt_values = []
                for x in out:
                    try:
                        stt_values.append(int(float(x.get("STT", 0) or 0)))
                    except Exception:
                        pass
                current = {
                    "STT": max(stt_values or [0]) + 1,
                    "Tên nhân viên": str(employee_name).strip(),
                    "__row": _phase14_next_row_hint(out),
                    "__phase14_id": _phase14_id("emp", target_key),
                }
                out.append(current)
            current["Tên nhân viên"] = str(employee_name).strip()
            current["Trạng thái"] = status
            current["Ngày cập nhật"] = now.strftime("%d/%m/%Y")
            current["Giờ cập nhật"] = now.strftime("%H:%M:%S")
            current["Người cập nhật"] = str(updated_by).strip()
            current["__phase14_id"] = _phase14_id("emp", target_key)
            return out

        result = _phase14_mutate_records(
            _PHASE14_EMPLOYMENT_DATASET,
            _phase14_employment_source_records,
            _p14_mutate,
            lambda: _phase14_legacy_set_employee_employment_status(
                employee_name, status, updated_by
            ),
            updated_by=str(updated_by or ""),
            confirm_fn=_phase14_employment_source_records,
        )
        try:
            load_employment_status_map.clear()
        except Exception:
            pass
        return result
    except Exception as e:
        return False, f"Lỗi cập nhật trạng thái nhân sự: {e}"
""".strip("\n"),

    "_phase14_legacy_load_long_leave_requests": r"""
@st.cache_data(ttl=60, show_spinner=False)
def load_long_leave_requests():
    try:
        records = _phase14_read_records(
            _PHASE14_LONG_LEAVE_DATASET,
            _phase14_long_leave_source_records,
        )
        return _phase14_df(records, LONG_LEAVE_HEADERS)
    except Exception:
        return pd.DataFrame(columns=LONG_LEAVE_HEADERS + ["__row"])
""".strip("\n"),

    "_phase14_legacy_append_long_leave_request": r"""
def append_long_leave_request(
    employee_name, role, start_date, end_date, reason, detail,
    status, source, updated_by, email_cc="", request_type=LONG_LEAVE_REQUEST_TYPE_LONG
):
    try:
        now = datetime.now(VN_TZ)
        req_id = _long_leave_request_id(employee_name, request_type=request_type)
        row_values = [
            req_id, str(employee_name).strip(), str(role).strip().lower(),
            start_date.strftime("%d/%m/%Y"), end_date.strftime("%d/%m/%Y"),
            str(reason).strip(), str(detail).strip(), str(status).strip(), "",
            now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"),
            "", "", "", str(source).strip(), str(updated_by).strip(),
            now.strftime("%d/%m/%Y %H:%M:%S"),
            "", "", "", "", str(email_cc or "").strip(),
            str(request_type or LONG_LEAVE_REQUEST_TYPE_LONG).strip(),
        ]
        rec = dict(zip(LONG_LEAVE_HEADERS, row_values))
        rec["__phase14_id"] = _phase14_id("long", req_id)

        def _p14_mutate(records):
            out = [dict(x) for x in records]
            item = dict(rec)
            item["__row"] = _phase14_next_row_hint(out)
            out.append(item)
            return out

        result = _phase14_mutate_records(
            _PHASE14_LONG_LEAVE_DATASET,
            _phase14_long_leave_source_records,
            _p14_mutate,
            lambda: _phase14_mirror_append_long(rec),
            updated_by=str(updated_by or ""),
            confirm_fn=_phase14_long_leave_source_records,
        )
        _clear_long_leave_cache()
        return result
    except Exception as e:
        return False, f"Lỗi lưu yêu cầu: {e}", ""
""".strip("\n"),

    "_phase14_legacy_update_long_leave_row": r"""
def update_long_leave_row(row_idx, updates, updated_by):
    try:
        row_idx = int(row_idx)
        now = datetime.now(VN_TZ)

        def _p14_mutate(records):
            out = [dict(x) for x in records]
            target = _phase14_find_by_row(out, row_idx)
            if target is None:
                raise _Phase14BusinessError("Không tìm thấy bản ghi yêu cầu cần cập nhật.")
            for k, v in (updates or {}).items():
                if k in LONG_LEAVE_HEADERS:
                    target[k] = v
            target["Người cập nhật"] = str(updated_by).strip()
            target["Cập nhật lúc"] = now.strftime("%d/%m/%Y %H:%M:%S")
            return out

        result = _phase14_mutate_records(
            _PHASE14_LONG_LEAVE_DATASET,
            _phase14_long_leave_source_records,
            _p14_mutate,
            lambda: _phase14_legacy_update_long_leave_row(row_idx, updates, updated_by),
            updated_by=str(updated_by or ""),
            confirm_fn=_phase14_long_leave_source_records,
        )
        _clear_long_leave_cache()
        return result
    except _Phase14BusinessError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Lỗi cập nhật yêu cầu: {e}"
""".strip("\n"),

    "_phase14_legacy_delete_long_leave_row": r"""
def delete_long_leave_row(row_idx):
    try:
        row_idx = int(row_idx)

        def _p14_mutate(records):
            out = [dict(x) for x in records]
            target = _phase14_find_by_row(out, row_idx)
            if target is None:
                raise _Phase14BusinessError("Không tìm thấy bản ghi yêu cầu cần xóa.")
            target_id = str(target.get("__phase14_id", "") or "")
            return [
                x for x in out
                if str(x.get("__phase14_id", "") or "") != target_id
            ]

        result = _phase14_mutate_records(
            _PHASE14_LONG_LEAVE_DATASET,
            _phase14_long_leave_source_records,
            _p14_mutate,
            lambda: _phase14_legacy_delete_long_leave_row(row_idx),
            updated_by=_phase14_current_user(),
            confirm_fn=_phase14_long_leave_source_records,
        )
        _clear_long_leave_cache()
        return result
    except _Phase14BusinessError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Lỗi xóa bản ghi: {e}"
""".strip("\n"),

    "_phase14_legacy_load_staff_schedule_plans": r"""
@st.cache_data(ttl=60, show_spinner=False)
def load_staff_schedule_plans():
    try:
        records = _phase14_read_records(
            _PHASE14_STAFF_PLAN_DATASET,
            _phase14_staff_plan_source_records,
        )
        return _phase14_df(records, STAFF_PLAN_HEADERS)
    except Exception:
        return pd.DataFrame(columns=STAFF_PLAN_HEADERS + ["__row"])
""".strip("\n"),

    "_phase14_legacy_append_staff_schedule_plan": r"""
def append_staff_schedule_plan(
    employee_name, plan_type, start_date, end_date=None, shift_name="",
    cycle="", note="", created_by="",
):
    try:
        employee_name = str(employee_name or "").strip()
        plan_type = str(plan_type or "").strip()
        if not employee_name:
            return False, "Vui lòng chọn nhân viên.", ""
        if plan_type not in {STAFF_PLAN_SHIFT, STAFF_PLAN_LONG_LEAVE, STAFF_PLAN_RESIGN}:
            return False, "Loại kế hoạch không hợp lệ.", ""
        if not isinstance(start_date, date):
            return False, "Ngày bắt đầu không hợp lệ.", ""
        if plan_type == STAFF_PLAN_SHIFT and not str(shift_name or "").strip():
            return False, "Vui lòng chọn ca sẽ áp dụng.", ""
        if plan_type == STAFF_PLAN_LONG_LEAVE:
            if not isinstance(end_date, date):
                return False, "Vui lòng chọn ngày bắt đầu quay lại làm việc.", ""
            if end_date <= start_date:
                return False, "Ngày quay lại làm việc phải sau ngày bắt đầu nghỉ dài hạn.", ""

        now = datetime.now(VN_TZ)
        plan_id = _staff_plan_id(employee_name, plan_type)
        row_values = [
            plan_id, employee_name, plan_type, start_date.strftime("%d/%m/%Y"),
            end_date.strftime("%d/%m/%Y") if isinstance(end_date, date) else "",
            str(shift_name or "").strip(), str(cycle or "").strip(),
            str(note or "").strip(), STAFF_PLAN_PENDING,
            now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"),
            str(created_by or "").strip(), "", "", "",
        ]
        rec = dict(zip(STAFF_PLAN_HEADERS, row_values))
        rec["__phase14_id"] = _phase14_id("plan", plan_id)

        def _p14_mutate(records):
            out = [dict(x) for x in records]
            d = _phase14_df(out, STAFF_PLAN_HEADERS)
            if isinstance(d, pd.DataFrame) and not d.empty:
                d = d[
                    d["Tên nhân viên"].astype(str).apply(normalize_login_name)
                    .eq(normalize_login_name(employee_name))
                ]
                d = d[~d["Trạng thái"].astype(str).str.strip().eq(STAFF_PLAN_CANCELLED)]
                d = d[d["Loại kế hoạch"].astype(str).str.strip().eq(plan_type)]

                if plan_type == STAFF_PLAN_SHIFT:
                    for _, r in d.iterrows():
                        if _parse_vn_date(r.get("Từ ngày", "")) == start_date:
                            raise _Phase14BusinessError(
                                f"{employee_name} đã có kế hoạch đổi ca vào "
                                f"{start_date.strftime('%d/%m/%Y')}. Hãy hủy kế hoạch cũ trước."
                            )
                elif plan_type == STAFF_PLAN_LONG_LEAVE:
                    for _, r in d.iterrows():
                        rs = _parse_vn_date(r.get("Từ ngày", ""))
                        retd = _parse_vn_date(r.get("Đến ngày", ""))
                        if rs is None or retd is None:
                            continue
                        if start_date < retd and end_date > rs:
                            raise _Phase14BusinessError(
                                f"{employee_name} đã có kế hoạch nghỉ dài hạn chồng lấn "
                                f"{rs.strftime('%d/%m/%Y')} → {retd.strftime('%d/%m/%Y')}."
                            )
                elif plan_type == STAFF_PLAN_RESIGN:
                    pending_resign = d[
                        ~d["Trạng thái"].astype(str).str.strip()
                        .isin([STAFF_PLAN_DONE, STAFF_PLAN_CANCELLED])
                    ]
                    if not pending_resign.empty:
                        rr = pending_resign.iloc[-1]
                        rd = _parse_vn_date(rr.get("Từ ngày", ""))
                        raise _Phase14BusinessError(
                            f"{employee_name} đã có kế hoạch Nghỉ việc"
                            + (f" từ {rd.strftime('%d/%m/%Y')}" if isinstance(rd, date) else "")
                            + ". Hãy hủy kế hoạch cũ trước."
                        )

            item = dict(rec)
            item["__row"] = _phase14_next_row_hint(out)
            out.append(item)
            return out

        result = _phase14_mutate_records(
            _PHASE14_STAFF_PLAN_DATASET,
            _phase14_staff_plan_source_records,
            _p14_mutate,
            lambda: _phase14_mirror_append_staff_plan(rec),
            updated_by=str(created_by or ""),
            confirm_fn=_phase14_staff_plan_source_records,
        )
        _clear_staff_plan_cache()
        return result
    except _Phase14BusinessError as e:
        return False, str(e), ""
    except Exception as e:
        return False, f"Lỗi lưu kế hoạch nhân sự: {e}", ""
""".strip("\n"),

    "_phase14_legacy_update_staff_schedule_plan_row": r"""
def update_staff_schedule_plan_row(row_idx, updates):
    try:
        row_idx = int(row_idx)

        def _p14_mutate(records):
            out = [dict(x) for x in records]
            target = _phase14_find_by_row(out, row_idx)
            if target is None:
                raise _Phase14BusinessError("Không tìm thấy kế hoạch cần cập nhật.")
            for k, v in dict(updates or {}).items():
                if k in STAFF_PLAN_HEADERS:
                    target[k] = v
            return out

        result = _phase14_mutate_records(
            _PHASE14_STAFF_PLAN_DATASET,
            _phase14_staff_plan_source_records,
            _p14_mutate,
            lambda: _phase14_legacy_update_staff_schedule_plan_row(row_idx, updates),
            updated_by=_phase14_current_user(),
            confirm_fn=_phase14_staff_plan_source_records,
        )
        _clear_staff_plan_cache()
        return result
    except _Phase14BusinessError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Lỗi cập nhật kế hoạch: {e}"
""".strip("\n"),
}


def _rename_top_level_function(source, old, new):
    pattern = re.compile(rf"(?m)^def {re.escape(old)}\(")
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
            warnings.append(f"phase14_rename_{old}:{count}")

    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        warnings.append(f"phase14_ast:{type(exc).__name__}")
        return source, warnings

    nodes = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = [n for n in TARGETS.values() if n not in nodes]
    if missing:
        warnings.extend(f"phase14_node_{n}:0" for n in missing)
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
        warnings.append(f"phase14_patched_ast:{type(exc).__name__}")
        return source, warnings

    return patched, warnings
