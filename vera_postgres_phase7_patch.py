"""Source hooks for PostgreSQL-first TichLuy writes in Phase 7.

The immutable V92.6.99 core is not edited. This patch:
- enriches TichLuy source snapshots with raw row/header metadata;
- keeps the legacy Sheet functions under private names;
- exposes wrappers that build the intended PostgreSQL snapshot first, then call
  the legacy function as synchronous Google Sheets mirror.
"""


def apply(source: str):
    warnings = []

    def patch_all(old, new, label):
        nonlocal source
        count = source.count(old)
        if count < 1:
            warnings.append(f"{label}:0")
            return 0
        source = source.replace(old, new)
        return count

    patch_all(
        """            if emp_role in TICHLUY_EXCLUDED_ROLES:\n                continue\n            item['__sheet_row'] = sheet_row\n            rows.append(item)""",
        """            if emp_role in TICHLUY_EXCLUDED_ROLES:\n                continue\n            item['__sheet_row'] = sheet_row\n            item['__raw_values'] = list(row)\n            item['__sheet_header'] = list(header)\n            rows.append(item)""",
        "tichluy_raw_metadata",
    )

    # The V92.6.99 core contains duplicated historical TichLuy blocks. Rename ALL
    # legacy definitions; the last renamed definition remains the effective mirror.
    patch_all(
        "def sync_tichluy_roles_and_stt(credentials_df=None):",
        "def _phase7_legacy_sync_tichluy_roles_and_stt(credentials_df=None):",
        "rename_tichluy_sync",
    )
    patch_all(
        "def ensure_employee_in_tichluy(employee_name, start_work_date=None):",
        "def _phase7_legacy_ensure_employee_in_tichluy(employee_name, start_work_date=None):",
        "rename_tichluy_ensure",
    )
    patch_all(
        "def record_tichluy_contributions(payroll_df, start_date, end_date):",
        "def _phase7_legacy_record_tichluy_contributions(payroll_df, start_date, end_date):",
        "rename_tichluy_contributions",
    )

    helper_anchor = "def _phase7_legacy_sync_tichluy_roles_and_stt(credentials_df=None):"
    helper_block = r'''

def _phase7_tichluy_active():
    if vpg is None:
        return False
    fn = getattr(vpg, "phase7_tichluy_is_enabled", None)
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:
        return False


def _phase7_tichluy_base_snapshot():
    """Current TichLuy snapshot including raw Sheet metadata when possible."""
    try:
        d = load_tichluy_tracking().copy()
    except Exception:
        d = pd.DataFrame()
    needs_raw = (
        not isinstance(d, pd.DataFrame)
        or "__raw_values" not in d.columns
        or "__sheet_header" not in d.columns
    )
    if needs_raw:
        try:
            fresh = _load_tichluy_tracking_from_sheets()
            if isinstance(fresh, pd.DataFrame):
                d = fresh.copy()
        except Exception:
            pass
    if not isinstance(d, pd.DataFrame):
        d = pd.DataFrame(columns=TICHLUY_HEADERS + ["__sheet_row", "__raw_values", "__sheet_header"])
    for c in TICHLUY_HEADERS:
        if c not in d.columns:
            d[c] = ""
    return d.reset_index(drop=True)


def _phase7_tichluy_header(snapshot=None):
    if isinstance(snapshot, pd.DataFrame) and "__sheet_header" in snapshot.columns:
        for value in snapshot["__sheet_header"].tolist():
            if isinstance(value, (list, tuple)) and value:
                return list(value)
    return list(TICHLUY_HEADERS)


def _phase7_tichluy_sync_raw(record, header=None, sheet_row=None):
    rec = dict(record or {})
    use_header = rec.get("__sheet_header")
    if not isinstance(use_header, (list, tuple)) or not use_header:
        use_header = list(header or TICHLUY_HEADERS)
    else:
        use_header = list(use_header)
    raw = rec.get("__raw_values")
    if not isinstance(raw, (list, tuple)):
        raw = []
    raw = list(raw)
    if len(raw) < len(use_header):
        raw += [""] * (len(use_header) - len(raw))
    pos = _tichluy_header_positions(use_header)
    for canonical in TICHLUY_HEADERS:
        idx = pos.get(canonical)
        if idx is not None:
            if idx >= len(raw):
                raw += [""] * (idx + 1 - len(raw))
            raw[idx] = rec.get(canonical, "")
    rec["__raw_values"] = raw
    rec["__sheet_header"] = use_header
    if sheet_row is not None:
        rec["__sheet_row"] = int(sheet_row)
    return rec


def _phase7_tichluy_frame(records):
    cols = list(TICHLUY_HEADERS) + ["__sheet_row", "__raw_values", "__sheet_header"]
    if not records:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(records)
    for c in cols:
        if c not in d.columns:
            d[c] = "" if c not in {"__raw_values", "__sheet_header"} else [[] for _ in range(len(d))]
    extra = [c for c in d.columns if c not in cols]
    return d[cols + extra].reset_index(drop=True)


def _phase7_build_tichluy_sync_snapshot(credentials_df=None):
    credentials_df = load_credentials_recent() if credentials_df is None else credentials_df
    base = _phase7_tichluy_base_snapshot()
    header = _phase7_tichluy_header(base)
    role_map = _credential_role_map(credentials_df)

    eligible = []
    eligible_keys = set()
    if isinstance(credentials_df, pd.DataFrame) and not credentials_df.empty:
        for _, cr in credentials_df.iterrows():
            name = str(cr.get("Tên nhân viên", "")).strip()
            role = str(cr.get("Phân quyền", "nhanvien")).strip().lower()
            key = normalize_login_name(name)
            if not key or role in TICHLUY_EXCLUDED_ROLES:
                continue
            if key in {"ten nhan vien", "ten he thong", "username", "user name"} or key in eligible_keys:
                continue
            eligible.append((name, key))
            eligible_keys.add(key)

    records = []
    existing_keys = set()
    for _, r in base.iterrows():
        rec = r.to_dict()
        name = str(rec.get("Tên nhân viên", "")).strip()
        if not name:
            continue
        key = normalize_login_name(name)
        role = role_map.get(key)
        if key not in eligible_keys or role in TICHLUY_EXCLUDED_ROLES:
            continue
        records.append(rec)
        existing_keys.add(key)

    for name, key in eligible:
        if key in existing_keys:
            continue
        rec = {
            "STT": "",
            "Tên nhân viên": name,
            "Ngày bắt đầu làm": "",
            "Mục tiêu tích lũy": TICHLUY_TARGET_DEFAULT,
            "Đã tích lũy": 0,
            "Còn lại": TICHLUY_TARGET_DEFAULT,
            "Kỳ gần nhất": "",
            "Số tiền kỳ gần nhất": 0,
            "Chi tiết các kỳ": "{}",
        }
        records.append(_phase7_tichluy_sync_raw(rec, header=header))
        existing_keys.add(key)

    out = []
    for idx, rec in enumerate(records, start=2):
        rec = dict(rec)
        rec["STT"] = idx - 1
        out.append(_phase7_tichluy_sync_raw(rec, header=header, sheet_row=idx))
    return _phase7_tichluy_frame(out)


def _phase7_build_tichluy_ensure_snapshot(employee_name, start_work_date=None):
    name = str(employee_name or "").strip()
    base = _phase7_tichluy_base_snapshot()
    if not name:
        return base
    key = normalize_login_name(name)
    if not base.empty and "Tên nhân viên" in base.columns:
        for value in base["Tên nhân viên"].astype(str).tolist():
            if normalize_login_name(value) == key:
                return base

    start_work_date = start_work_date or get_vn_today()
    header = _phase7_tichluy_header(base)
    rec = {
        "STT": "",
        "Tên nhân viên": name,
        "Ngày bắt đầu làm": start_work_date.strftime("%d/%m/%Y"),
        "Mục tiêu tích lũy": TICHLUY_TARGET_DEFAULT,
        "Đã tích lũy": 0,
        "Còn lại": TICHLUY_TARGET_DEFAULT,
        "Kỳ gần nhất": "",
        "Số tiền kỳ gần nhất": 0,
        "Chi tiết các kỳ": "{}",
    }
    max_row = 1
    if "__sheet_row" in base.columns:
        for value in base["__sheet_row"].tolist():
            try:
                max_row = max(max_row, int(float(value)))
            except Exception:
                pass
    rec = _phase7_tichluy_sync_raw(rec, header=header, sheet_row=max_row + 1)
    records = [r.to_dict() for _, r in base.iterrows()] + [rec]
    return _phase7_tichluy_frame(records)


def _phase7_build_tichluy_contribution_snapshot(payroll_df, start_date, end_date):
    base = _phase7_tichluy_base_snapshot()
    if payroll_df is None or not isinstance(payroll_df, pd.DataFrame) or payroll_df.empty:
        return base

    header = _phase7_tichluy_header(base)
    records = [r.to_dict() for _, r in base.iterrows()]
    rows_by_key = {}
    role_map = _credential_role_map(load_credentials_recent())

    for rec_idx, rec in enumerate(records):
        name = str(rec.get("Tên nhân viên", "")).strip()
        key = normalize_login_name(name)
        if not key:
            continue
        completed_score = 1 if _is_tichluy_completed(
            rec.get("Mục tiêu tích lũy", ""), rec.get("Đã tích lũy", ""), rec.get("Còn lại", "")
        ) else 0
        try:
            sheet_row = int(float(rec.get("__sheet_row", rec_idx + 2) or rec_idx + 2))
        except Exception:
            sheet_row = rec_idx + 2
        score = (completed_score, sheet_row)
        if key not in rows_by_key or score > rows_by_key[key][0]:
            rows_by_key[key] = (score, rec_idx)

    period_key = _tichluy_period_key(start_date, end_date)
    next_sheet_row = 2
    for rec in records:
        try:
            next_sheet_row = max(next_sheet_row, int(float(rec.get("__sheet_row", 1) or 1)) + 1)
        except Exception:
            pass

    for _, pr in payroll_df.iterrows():
        name = str(pr.get("Tên Hệ thống", "")).strip()
        key = normalize_login_name(name)
        if not key:
            continue
        role = role_map.get(key)
        if role is None or role in TICHLUY_EXCLUDED_ROLES:
            continue
        amount = max(0.0, float(_money_to_float(pr.get("Tích lũy", 0))))

        if key not in rows_by_key:
            rec = {
                "STT": "",
                "Tên nhân viên": name,
                "Ngày bắt đầu làm": "",
                "Mục tiêu tích lũy": TICHLUY_TARGET_DEFAULT,
                "Đã tích lũy": 0,
                "Còn lại": TICHLUY_TARGET_DEFAULT,
                "Kỳ gần nhất": "",
                "Số tiền kỳ gần nhất": 0,
                "Chi tiết các kỳ": "{}",
            }
            rec = _phase7_tichluy_sync_raw(rec, header=header, sheet_row=next_sheet_row)
            next_sheet_row += 1
            records.append(rec)
            rows_by_key[key] = ((0, int(rec.get("__sheet_row", next_sheet_row - 1))), len(records) - 1)
            # Preserve legacy behavior: a missing profile is created first; the
            # contribution itself is recorded on a later save after profile exists.
            continue

        _score, rec_idx = rows_by_key[key]
        rec = dict(records[rec_idx])
        target = float(_money_to_float(rec.get("Mục tiêu tích lũy", "")) or TICHLUY_TARGET_DEFAULT)
        current_total = float(_money_to_float(rec.get("Đã tích lũy", 0)))
        if _is_tichluy_completed(
            rec.get("Mục tiêu tích lũy", target), rec.get("Đã tích lũy", current_total), rec.get("Còn lại", "")
        ):
            continue

        hist = _parse_tichluy_history(rec.get("Chi tiết các kỳ", ""))
        old_history_total, matched_history_keys = _matching_tichluy_history(hist, start_date, end_date)
        old_display_amount = min(float(TICHLUY_PERIOD_DEFAULT), max(0.0, old_history_total))
        amount_to_record = old_display_amount if old_display_amount > 0 and amount <= 0 else amount
        amount_to_subtract = old_history_total
        new_total = max(0.0, min(target, current_total - amount_to_subtract + amount_to_record))
        for legacy_key in matched_history_keys:
            hist.pop(legacy_key, None)
        hist[period_key] = float(amount_to_record)
        remaining = max(0.0, target - new_total)
        rec.update({
            "Mục tiêu tích lũy": target,
            "Đã tích lũy": new_total,
            "Còn lại": remaining,
            "Kỳ gần nhất": f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
            "Số tiền kỳ gần nhất": amount_to_record,
            "Chi tiết các kỳ": json.dumps(hist, ensure_ascii=False, separators=(",", ":")),
        })
        records[rec_idx] = _phase7_tichluy_sync_raw(rec, header=header)

    return _phase7_tichluy_frame(records)


def sync_tichluy_roles_and_stt(credentials_df=None):
    if not _phase7_tichluy_active():
        return _phase7_legacy_sync_tichluy_roles_and_stt(credentials_df)
    new_df = _phase7_build_tichluy_sync_snapshot(credentials_df)
    return vpg.phase7_tichluy_commit(
        new_df,
        lambda: _phase7_legacy_sync_tichluy_roles_and_stt(credentials_df),
        operation="sync_roles_stt",
        confirm_fn=_load_tichluy_tracking_from_sheets,
    )


def ensure_employee_in_tichluy(employee_name, start_work_date=None):
    if not _phase7_tichluy_active():
        return _phase7_legacy_ensure_employee_in_tichluy(employee_name, start_work_date)
    name = str(employee_name or "").strip()
    if not name:
        return False, "Thiếu tên nhân viên."
    base = _phase7_tichluy_base_snapshot()
    key = normalize_login_name(name)
    if not base.empty and "Tên nhân viên" in base.columns:
        if any(normalize_login_name(v) == key for v in base["Tên nhân viên"].astype(str).tolist()):
            return True, "Nhân viên đã có trong TichLuy."
    new_df = _phase7_build_tichluy_ensure_snapshot(name, start_work_date)
    return vpg.phase7_tichluy_commit(
        new_df,
        lambda: _phase7_legacy_ensure_employee_in_tichluy(name, start_work_date),
        operation="ensure_employee",
        confirm_fn=_load_tichluy_tracking_from_sheets,
    )


def record_tichluy_contributions(payroll_df, start_date, end_date):
    if not _phase7_tichluy_active():
        return _phase7_legacy_record_tichluy_contributions(payroll_df, start_date, end_date)
    if payroll_df is None or not isinstance(payroll_df, pd.DataFrame) or payroll_df.empty:
        return True, "Không có dữ liệu Tích lũy cần cập nhật."
    new_df = _phase7_build_tichluy_contribution_snapshot(payroll_df, start_date, end_date)
    return vpg.phase7_tichluy_commit(
        new_df,
        lambda: _phase7_legacy_record_tichluy_contributions(payroll_df, start_date, end_date),
        operation="record_contributions",
        confirm_fn=_load_tichluy_tracking_from_sheets,
    )

'''

    anchor_pos = source.rfind(helper_anchor)
    if anchor_pos < 0:
        warnings.append("tichluy_wrapper_anchor:0")
    else:
        source = source[:anchor_pos] + helper_block + "\n" + source[anchor_pos:]

    return source, warnings
