"""Source hooks for Phase 16 residual PostgreSQL-primary paths."""
from __future__ import annotations

import ast
import re

MARKER = "_PHASE16_RESIDUAL_PATCH_V1 = True"
TARGETS = {
    "set_accounts_login_lock": "_phase16_legacy_set_accounts_login_lock",
    "create_remember_token": "_phase16_legacy_create_remember_token",
    "revoke_remember_token": "_phase16_legacy_revoke_remember_token",
    "ensure_start_work_date_on_login_v92689": "_phase16_legacy_ensure_start_work_date_on_login_v92689",
    "load_admin_leave_audit_notices": "_phase16_legacy_load_admin_leave_audit_notices",
    "notify_admin_leave_schedule_change": "_phase16_legacy_notify_admin_leave_schedule_change",
    "write_leave_activity_log": "_phase16_legacy_write_leave_activity_log",
    "write_leave_activity_logs_batch": "_phase16_legacy_write_leave_activity_logs_batch",
    "load_leave_activity_log": "_phase16_legacy_load_leave_activity_log",
}

HELPER_BLOCK = r'''
_PHASE16_RESIDUAL_PATCH_V1 = True
_PHASE16_LOG_DATASET = "leave_activity_log"
_PHASE16_AUDIT_NOTICE_DATASET = "leave_audit_notice"


def _phase16_active():
    try:
        fn = getattr(vpg, "phase16_is_enabled", None) if vpg is not None else None
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


def _phase16_read_records(dataset, source_loader):
    if vpg is not None:
        fn = getattr(vpg, "phase16_read_records", None)
        if callable(fn):
            return fn(dataset, source_loader)
    try:
        return source_loader()
    except Exception:
        return []


def _phase16_mutate_records(dataset, source_loader, mutator, mirror_fn, updated_by=""):
    if vpg is not None:
        fn = getattr(vpg, "phase16_mutate_records", None)
        if callable(fn):
            return fn(dataset, source_loader, mutator, mirror_fn, updated_by=updated_by)
    return mirror_fn()


def _phase16_unique_id(prefix, raw):
    seed = str(raw or "") + "|" + secrets.token_hex(5)
    return f"{prefix}-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def _phase16_log_source_records():
    ws = _leave_activity_log_ws()
    if ws is None:
        raise RuntimeError("Không kết nối được worksheet Log Book.")
    values = _gs_call_with_backoff(ws.get_all_values)
    records = []
    for ridx, raw in enumerate(values[1:], start=2):
        vals = list(raw[:len(LEAVE_ACTIVITY_LOG_HEADERS)])
        vals += [""] * max(0, len(LEAVE_ACTIVITY_LOG_HEADERS) - len(vals))
        if not any(str(v).strip() for v in vals):
            continue
        rec = dict(zip(LEAVE_ACTIVITY_LOG_HEADERS, vals))
        logical_id = str(rec.get("ID", "") or "").strip()
        if not logical_id:
            logical_id = _phase16_unique_id("LOGLEGACY", f"{ridx}|{rec.get('Thời điểm VN','')}|{rec.get('Tên nhân viên','')}")
        rec["__row"] = ridx
        rec["__phase16_id"] = logical_id
        records.append(rec)
    return records


def _phase16_audit_source_records():
    ws = _get_leave_audit_notice_ws()
    if ws is None:
        raise RuntimeError("Không kết nối được sheet thông báo.")
    values = _gs_call_with_backoff(ws.get_all_values)
    records = []
    for ridx, raw in enumerate(values[1:], start=2):
        vals = list(raw[:len(LEAVE_AUDIT_NOTICE_HEADERS)])
        vals += [""] * max(0, len(LEAVE_AUDIT_NOTICE_HEADERS) - len(vals))
        if not any(str(v).strip() for v in vals):
            continue
        rec = dict(zip(LEAVE_AUDIT_NOTICE_HEADERS, vals))
        logical_id = str(rec.get("ID", "") or "").strip()
        if not logical_id:
            logical_id = _phase16_unique_id("AUDLEGACY", f"{ridx}|{rec.get('Ngày cập nhật','')}|{rec.get('Giờ cập nhật','')}")
        rec["__row"] = ridx
        rec["__phase16_id"] = logical_id
        records.append(rec)
    return records


def _phase16_next_row(records):
    max_row = 1
    for rec in records or []:
        try:
            max_row = max(max_row, int(rec.get("__row", 0) or 0))
        except Exception:
            pass
    return max_row + 1


def _phase16_log_record(action, actor, before_row=None, after_row=None, actor_role=None, source=None, status="SUCCESS", note="", now=None, offset=0):
    before = _leave_audit_snapshot(before_row)
    after = _leave_audit_snapshot(after_row)
    now = now or datetime.now(VN_TZ)
    actor_text = str(actor or "Hệ thống").strip() or "Hệ thống"
    role = _leave_activity_actor_role(actor_text, actor_role)
    source_text = _leave_activity_source(source, actor_text)
    employee = str(after.get("Tên nhân viên", "") or before.get("Tên nhân viên", "")).strip()
    leave_date = str(after.get("Ngày", "") or before.get("Ngày", "")).strip()
    raw_id = "|".join([now.isoformat(), str(offset), str(action), actor_text, employee, leave_date, json.dumps(before, ensure_ascii=False, sort_keys=True, default=str), json.dumps(after, ensure_ascii=False, sort_keys=True, default=str), secrets.token_hex(4)])
    event_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:24]
    row = [
        event_id, now.strftime("%d/%m/%Y %H:%M:%S"), now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"),
        str(action or "").strip(), str(status or "SUCCESS").strip().upper(), actor_text, role, source_text, employee, leave_date,
        str(before.get("Lý do nghỉ", "") or ""), str(after.get("Lý do nghỉ", "") or ""),
        str(before.get("Loại nghỉ", "") or ""), str(after.get("Loại nghỉ", "") or ""),
        before.get("Số ngày tính", ""), after.get("Số ngày tính", ""), before.get("Phạt vi phạm", ""), after.get("Phạt vi phạm", ""),
        _leave_activity_ordinal(before), _leave_activity_ordinal(after), _leave_activity_changed_fields(before, after), str(note or "").strip(),
        json.dumps(before, ensure_ascii=False, default=str), json.dumps(after, ensure_ascii=False, default=str),
    ]
    rec = dict(zip(LEAVE_ACTIVITY_LOG_HEADERS, row))
    rec["__phase16_id"] = event_id
    return rec, row


def _phase16_full_credential_record(row):
    rec = row.to_dict() if hasattr(row, "to_dict") else dict(row or {})
    for key, value in list(rec.items()):
        try:
            if pd.isna(value):
                rec[key] = ""
        except Exception:
            pass
    return rec


def _phase16_find_credential(username):
    creds = load_credentials_recent()
    if not isinstance(creds, pd.DataFrame) or creds.empty:
        return None
    target = normalize_login_name(username)
    if not target or "Tên nhân viên" not in creds.columns:
        return None
    hit = creds[creds["Tên nhân viên"].astype(str).apply(normalize_login_name).eq(target)]
    if hit.empty:
        return None
    return _phase16_full_credential_record(hit.iloc[-1])
'''.strip("\n")

WRAPPERS = {
    "_phase16_legacy_set_accounts_login_lock": r'''
def set_accounts_login_lock(usernames, locked=True):
    if not _phase16_active():
        return _phase16_legacy_set_accounts_login_lock(usernames, locked=locked)
    try:
        targets = {normalize_login_name(x) for x in usernames}
        creds = load_credentials_recent()
        records = []
        if isinstance(creds, pd.DataFrame) and not creds.empty:
            for _, row in creds.iterrows():
                if normalize_login_name(row.get("Tên nhân viên", "")) not in targets:
                    continue
                rec = _phase16_full_credential_record(row)
                rec["Khóa đăng nhập"] = "KHÓA" if locked else ""
                if locked:
                    rec["Remember Token Hash"] = ""
                    rec["Remember Token Expiry"] = ""
                records.append(rec)
        captured = {}
        def _mirror():
            captured["result"] = _phase16_legacy_set_accounts_login_lock(usernames, locked=locked)
            return captured["result"]
        result = _vera_phase4_employee_batch_upsert(records, _mirror, operation="phase16_login_lock")
        _clear_dynamic_data_caches()
        return result
    except Exception as e:
        result = locals().get("captured", {}).get("result")
        if isinstance(result, (tuple, list)) and result and result[0] is False:
            return result
        return False, f"Lỗi cập nhật khóa đăng nhập: {e}"
'''.strip("\n"),

    "_phase16_legacy_create_remember_token": r'''
def create_remember_token(username, days=None):
    if not _phase16_active():
        return _phase16_legacy_create_remember_token(username, days=days)
    try:
        rec = _phase16_find_credential(username)
        if rec is None:
            return None
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        rec["Remember Token Hash"] = token_hash
        rec["Remember Token Expiry"] = ""
        def _mirror():
            client = get_gspread_client()
            if not client:
                raise RuntimeError("Chưa cấu hình quyền kết nối.")
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            values = _gs_call_with_backoff(sheet.get_all_values)
            target = normalize_login_name(username)
            for r_idx, row in enumerate(values[1:], start=2):
                if len(row) > 1 and normalize_login_name(row[1]) == target:
                    _gs_call_with_backoff(sheet.update_cell, r_idx, 19, token_hash)
                    _gs_call_with_backoff(sheet.update_cell, r_idx, 20, "")
                    return True
            raise RuntimeError("Không tìm thấy tài khoản.")
        _vera_phase4_employee_upsert(rec, _mirror, operation="phase16_remember_token_create")
        _clear_dynamic_data_caches()
        return token
    except Exception:
        return None
'''.strip("\n"),

    "_phase16_legacy_revoke_remember_token": r'''
def revoke_remember_token(username):
    if not _phase16_active():
        return _phase16_legacy_revoke_remember_token(username)
    try:
        rec = _phase16_find_credential(username)
        if rec is None:
            return
        rec["Remember Token Hash"] = ""
        rec["Remember Token Expiry"] = ""
        def _mirror():
            client = get_gspread_client()
            if not client:
                raise RuntimeError("Chưa cấu hình quyền kết nối.")
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            values = _gs_call_with_backoff(sheet.get_all_values)
            target = normalize_login_name(username)
            for r_idx, row in enumerate(values[1:], start=2):
                if len(row) > 1 and normalize_login_name(row[1]) == target:
                    _gs_call_with_backoff(sheet.update_cell, r_idx, 19, "")
                    _gs_call_with_backoff(sheet.update_cell, r_idx, 20, "")
                    return True
            return True
        _vera_phase4_employee_upsert(rec, _mirror, operation="phase16_remember_token_revoke")
        _clear_dynamic_data_caches()
    except Exception:
        pass
    return None
'''.strip("\n"),

    "_phase16_legacy_ensure_start_work_date_on_login_v92689": r'''
def ensure_start_work_date_on_login_v92689(username):
    if not _phase16_active():
        return _phase16_legacy_ensure_start_work_date_on_login_v92689(username)
    username = str(username or "").strip()
    if not username or normalize_login_name(username) == normalize_login_name("Quản Trị Viên"):
        return False
    try:
        rec = _phase16_find_credential(username)
        if rec is None:
            return False
        if str(rec.get("Ngày bắt đầu làm", "") or "").strip():
            return False
        work_date = get_vn_today().strftime("%d/%m/%Y")
        rec["Ngày bắt đầu làm"] = work_date
        def _mirror():
            ensure_credential_control_columns()
            client = get_gspread_client()
            if not client:
                raise RuntimeError("Chưa cấu hình Google Sheets.")
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            values = _gs_call_with_backoff(sheet.get_all_values)
            target = normalize_login_name(username)
            for row_idx, row in enumerate(values[1:], start=2):
                if len(row) > 1 and normalize_login_name(row[1]) == target:
                    existing = str(row[20]).strip() if len(row) > 20 else ""
                    if existing:
                        return True
                    gspread_update_range(sheet, f"U{row_idx}:U{row_idx}", [[work_date]], value_input_option="USER_ENTERED")
                    return True
            raise RuntimeError("Không tìm thấy tài khoản.")
        _vera_phase4_employee_upsert(rec, _mirror, operation="phase16_start_work_date")
        _clear_dynamic_data_caches()
        return True
    except Exception:
        return False
'''.strip("\n"),

    "_phase16_legacy_load_leave_activity_log": r'''
@st.cache_data(ttl=20, show_spinner=False)
def load_leave_activity_log():
    if not _phase16_active():
        return _phase16_legacy_load_leave_activity_log()
    try:
        records = _phase16_read_records(_PHASE16_LOG_DATASET, _phase16_log_source_records)
        rows = []
        for rec0 in records or []:
            rec = dict(rec0 or {})
            rec.pop("__phase16_id", None); rec.pop("__row", None)
            rows.append({h: rec.get(h, "") for h in LEAVE_ACTIVITY_LOG_HEADERS})
        return pd.DataFrame(rows, columns=LEAVE_ACTIVITY_LOG_HEADERS)
    except Exception:
        return pd.DataFrame(columns=LEAVE_ACTIVITY_LOG_HEADERS)
'''.strip("\n"),

    "_phase16_legacy_write_leave_activity_log": r'''
def write_leave_activity_log(action, actor, before_row=None, after_row=None, actor_role=None, source=None, status="SUCCESS", note=""):
    if not _phase16_active():
        return _phase16_legacy_write_leave_activity_log(action, actor, before_row=before_row, after_row=after_row, actor_role=actor_role, source=source, status=status, note=note)
    try:
        rec, row = _phase16_log_record(action, actor, before_row=before_row, after_row=after_row, actor_role=actor_role, source=source, status=status, note=note)
        def _mutate(records):
            out = [dict(x) for x in records]; item = dict(rec); item["__row"] = _phase16_next_row(out); out.append(item); return out
        def _mirror():
            ws = _leave_activity_log_ws()
            if ws is None:
                return False, "Không kết nối được worksheet Log Book."
            _gs_call_with_backoff(ws.append_row, row, value_input_option="USER_ENTERED")
            return True, "Đã ghi Log Book."
        result = _phase16_mutate_records(_PHASE16_LOG_DATASET, _phase16_log_source_records, _mutate, _mirror, updated_by=str(actor or ""))
        try: load_leave_activity_log.clear()
        except Exception: pass
        return result
    except Exception as e:
        return False, f"Không ghi được Log Book: {e}"
'''.strip("\n"),

    "_phase16_legacy_write_leave_activity_logs_batch": r'''
def write_leave_activity_logs_batch(action, actor, after_rows, actor_role=None, source=None, status="SUCCESS", note=""):
    if not _phase16_active():
        return _phase16_legacy_write_leave_activity_logs_batch(action, actor, after_rows, actor_role=actor_role, source=source, status=status, note=note)
    try:
        rows_in = list(after_rows or [])
        if not rows_in:
            return True, "Không có audit cần ghi."
        now = datetime.now(VN_TZ); recs, sheet_rows = [], []
        for offset, after_row in enumerate(rows_in):
            rec, row = _phase16_log_record(action, actor, before_row=None, after_row=after_row, actor_role=actor_role, source=source, status=status, note=note, now=now, offset=offset)
            recs.append(rec); sheet_rows.append(row)
        def _mutate(records):
            out = [dict(x) for x in records]; next_row = _phase16_next_row(out)
            for offset, rec in enumerate(recs):
                item = dict(rec); item["__row"] = next_row + offset; out.append(item)
            return out
        def _mirror():
            ws = _leave_activity_log_ws()
            if ws is None:
                return False, "Không kết nối được worksheet Log Book."
            vals = _gs_call_with_backoff(ws.get_all_values); start_row = max(2, len(vals)+1); end_row = start_row + len(sheet_rows)-1
            gspread_update_range(ws, f"A{start_row}:Y{end_row}", sheet_rows, value_input_option="USER_ENTERED")
            return True, f"Đã ghi {len(sheet_rows)} dòng Log Book."
        result = _phase16_mutate_records(_PHASE16_LOG_DATASET, _phase16_log_source_records, _mutate, _mirror, updated_by=str(actor or ""))
        try: load_leave_activity_log.clear()
        except Exception: pass
        return result
    except Exception as e:
        return False, f"Không ghi được Log Book theo lô: {e}"
'''.strip("\n"),

    "_phase16_legacy_load_admin_leave_audit_notices": r'''
@st.cache_data(ttl=15, show_spinner=False)
def load_admin_leave_audit_notices(limit=10):
    if not _phase16_active():
        return _phase16_legacy_load_admin_leave_audit_notices(limit=limit)
    try:
        records = _phase16_read_records(_PHASE16_AUDIT_NOTICE_DATASET, _phase16_audit_source_records)
        clean = []
        for rec0 in records or []:
            rec = dict(rec0 or {}); rec.pop("__phase16_id", None); rec.pop("__row", None)
            if str(rec.get("ID", "") or "").strip():
                clean.append({h: rec.get(h, "") for h in LEAVE_AUDIT_NOTICE_HEADERS})
        return list(reversed(clean[-max(1, int(limit)):]))
    except Exception:
        return []
'''.strip("\n"),

    "_phase16_legacy_notify_admin_leave_schedule_change": r'''
def notify_admin_leave_schedule_change(action, actor, actor_role, before_row, after_row=None):
    if not _phase16_active():
        return _phase16_legacy_notify_admin_leave_schedule_change(action, actor, actor_role, before_row, after_row=after_row)
    actor_role = str(actor_role or "").strip().lower()
    if actor_role not in {"letan", "quanly"}:
        return True, ""
    before = _leave_audit_snapshot(before_row); after = _leave_audit_snapshot(after_row)
    action_label = "SỬA" if str(action).lower() == "edit" else "XÓA"; now = datetime.now(VN_TZ)
    raw_id = "|".join([action_label, str(actor), json.dumps(before, ensure_ascii=False, sort_keys=True, default=str), json.dumps(after, ensure_ascii=False, sort_keys=True, default=str), now.isoformat()])
    audit_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:24]
    email_ok, email_msg = send_admin_leave_audit_email(action, actor, actor_role, before, after)
    employee = str(after.get("Tên nhân viên", "") or before.get("Tên nhân viên", "")).strip()
    row = [audit_id, action_label, str(actor or "").strip(), actor_role, employee, json.dumps(before, ensure_ascii=False, default=str), json.dumps(after, ensure_ascii=False, default=str), now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"), LEAVE_AUDIT_ADMIN_EMAIL, ("SUCCESS" if email_ok else f"ERROR: {email_msg}")[:500]]
    rec = dict(zip(LEAVE_AUDIT_NOTICE_HEADERS, row)); rec["__phase16_id"] = audit_id
    try:
        def _mutate(records):
            out=[dict(x) for x in records]; item=dict(rec); item["__row"]=_phase16_next_row(out); out.append(item); return out
        def _mirror():
            ws = _get_leave_audit_notice_ws()
            if ws is None:
                return False, "Không kết nối được sheet thông báo."
            _gs_call_with_backoff(ws.append_row, row, value_input_option="USER_ENTERED")
            return True, "Đã tạo thông báo cho Admin."
        notice_result = _phase16_mutate_records(_PHASE16_AUDIT_NOTICE_DATASET, _phase16_audit_source_records, _mutate, _mirror, updated_by=str(actor or ""))
        notice_ok = not (isinstance(notice_result,(tuple,list)) and notice_result and isinstance(notice_result[0],bool) and notice_result[0] is False)
        notice_msg = notice_result[1] if isinstance(notice_result,(tuple,list)) and len(notice_result)>1 else ("Đã tạo thông báo cho Admin." if notice_ok else "Không tạo được thông báo.")
        try: load_admin_leave_audit_notices.clear()
        except Exception: pass
    except Exception as e:
        notice_ok=False; notice_msg=f"Lỗi ghi notification: {e}"
    ok = bool(email_ok and notice_ok)
    detail = " | ".join(x for x in [email_msg, ("Đã tạo thông báo cho Admin." if notice_ok else notice_msg)] if x)
    return ok, detail
'''.strip("\n"),
}


def _rename_top_level_function(source, old, new):
    pattern = re.compile(rf"(?m)^def\s+{re.escape(old)}\s*\(")
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
            warnings.append(f"phase16_rename_{old}:{count}")
    if warnings:
        return source, warnings
    try:
        tree = ast.parse(renamed)
    except Exception as exc:
        return source, [f"phase16_parse_after_rename:{type(exc).__name__}"]
    nodes = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [name for name in TARGETS.values() if name not in nodes]
    if missing:
        return source, [f"phase16_node_{name}:0" for name in missing]
    lines = renamed.splitlines(keepends=True); insertions = []
    first_node = min((nodes[name] for name in TARGETS.values()), key=lambda n: n.lineno)
    first_start = int(first_node.lineno)
    if getattr(first_node, "decorator_list", None):
        first_start = min(first_start, *(int(d.lineno) for d in first_node.decorator_list))
    insertions.append((max(0, first_start-1), HELPER_BLOCK + "\n\n"))
    for legacy_name, wrapper in WRAPPERS.items():
        node = nodes[legacy_name]
        insertions.append((int(getattr(node, "end_lineno", node.lineno)), "\n\n" + wrapper + "\n"))
    for idx, block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(idx, block)
    patched = "".join(lines)
    try:
        ast.parse(patched)
    except Exception as exc:
        return source, [f"phase16_parse_patched:{type(exc).__name__}:{exc}"]
    if MARKER not in patched:
        return source, ["phase16_marker_missing"]
    return patched, warnings
