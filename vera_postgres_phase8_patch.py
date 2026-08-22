"""Source hooks for Phase 8 NoViPham + PayrollHistory PostgreSQL-first writes."""


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

    # ---------------- NoViPham simple mutations ----------------
    patch_all(
        '''        if existing_row:\n            gspread_update_range(ws, f"A{existing_row}:N{existing_row}", [row_values])\n        else:\n            ws.append_row(row_values, value_input_option='USER_ENTERED')\n        _clear_violation_debt_cache()''',
        '''        _p8new = _phase8_violation_upsert_snapshot(row_values, existing_row)\n        def _p8mirror_defer():\n            if existing_row:\n                gspread_update_range(ws, f"A{existing_row}:N{existing_row}", [row_values])\n            else:\n                ws.append_row(row_values, value_input_option='USER_ENTERED')\n            _clear_violation_debt_cache()\n            return True\n        _phase8_commit_dataset(\n            "violation_debt", _p8new, _p8mirror_defer,\n            operation="defer_violation", confirm_fn=_load_violation_debt_ledger_from_sheets,\n        )''',
        "violation_defer",
    )

    patch_all(
        '''        gspread_update_range(ws, f"A{sheet_row}:N{sheet_row}", [row[:len(VIOLATION_DEBT_HEADERS)]])\n        _clear_violation_debt_cache()\n        return True, "Đã cập nhật Nghĩa vụ Vi phạm."''',
        '''        _p8row_values = row[:len(VIOLATION_DEBT_HEADERS)]\n        _p8new = _phase8_violation_upsert_snapshot(_p8row_values, sheet_row)\n        def _p8mirror_update():\n            gspread_update_range(ws, f"A{sheet_row}:N{sheet_row}", [_p8row_values])\n            _clear_violation_debt_cache()\n            return True\n        _phase8_commit_dataset(\n            "violation_debt", _p8new, _p8mirror_update,\n            operation="update_obligation", confirm_fn=_load_violation_debt_ledger_from_sheets,\n        )\n        return True, "Đã cập nhật Nghĩa vụ Vi phạm."''',
        "violation_update",
    )

    patch_all(
        '''        _gs_call_with_backoff(ws.delete_rows, sheet_row)\n        _renumber_violation_debt_stt(ws)\n        _clear_violation_debt_cache()\n        return True, "Đã xóa Nghĩa vụ Vi phạm."''',
        '''        _p8new = _phase8_violation_delete_snapshot(sheet_row)\n        def _p8mirror_delete():\n            _gs_call_with_backoff(ws.delete_rows, sheet_row)\n            _renumber_violation_debt_stt(ws)\n            _clear_violation_debt_cache()\n            return True\n        _phase8_commit_dataset(\n            "violation_debt", _p8new, _p8mirror_delete,\n            operation="delete_obligation", confirm_fn=_load_violation_debt_ledger_from_sheets,\n        )\n        return True, "Đã xóa Nghĩa vụ Vi phạm."''',
        "violation_delete",
    )

    # Whole after-payroll debt update is a multi-row mutation: keep it as one mirror.
    patch_all(
        "def commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by):",
        "def _phase8_legacy_commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by):",
        "rename_violation_commit_after_payroll",
    )

    # ---------------- PayrollHistory mutations ----------------
    patch_all(
        '''            rows.append(row)\n        if rows:\n            ws_pay.append_rows(rows, value_input_option='USER_ENTERED')\n        tl_ok, tl_msg = record_tichluy_contributions(payroll_df, start_date, end_date)''',
        '''            rows.append(row)\n        if rows:\n            _p8new_payroll = _phase8_payroll_append_snapshot(rows)\n            _phase8_commit_dataset(\n                "payroll_history", _p8new_payroll,\n                lambda: ws_pay.append_rows(rows, value_input_option='USER_ENTERED'),\n                operation="save_snapshot", confirm_fn=_load_payroll_history_from_sheets,\n            )\n        tl_ok, tl_msg = record_tichluy_contributions(payroll_df, start_date, end_date)''',
        "payroll_save",
    )

    patch_all(
        '''        # Xóa bản cũ từ dưới lên để không làm lệch chỉ số dòng.\n        for row_idx in sorted(matched_rows, reverse=True):\n            ws_pay.delete_rows(row_idx)\n\n        now = datetime.now(VN_TZ)''',
        '''        # Phase 8 hoãn mirror xóa đến khi snapshot PostgreSQL mới đã commit.\n        _p8_overwrite_delete_rows = sorted(matched_rows, reverse=True)\n\n        now = datetime.now(VN_TZ)''',
        "payroll_overwrite_defer_delete",
    )

    patch_all(
        '''            ])\n\n        if rows:\n            ws_pay.append_rows(rows, value_input_option='USER_ENTERED')\n        tl_ok, tl_msg = record_tichluy_contributions(payroll_df, start_date, end_date)''',
        '''            ])\n\n        _p8new_payroll = _phase8_payroll_replace_snapshot(batch_id, rows)\n        def _p8mirror_overwrite():\n            for _p8row_idx in _p8_overwrite_delete_rows:\n                ws_pay.delete_rows(_p8row_idx)\n            if rows:\n                ws_pay.append_rows(rows, value_input_option='USER_ENTERED')\n            return True\n        _phase8_commit_dataset(\n            "payroll_history", _p8new_payroll, _p8mirror_overwrite,\n            operation="overwrite_snapshot", confirm_fn=_load_payroll_history_from_sheets,\n        )\n        tl_ok, tl_msg = record_tichluy_contributions(payroll_df, start_date, end_date)''',
        "payroll_overwrite",
    )

    patch_all(
        '''        # Xóa từ dưới lên để chỉ số dòng phía trên không bị thay đổi.\n        for start_row, end_row in reversed(blocks):\n            if start_row == end_row:\n                _gs_call_with_backoff(ws_pay.delete_rows, start_row)\n            else:\n                _gs_call_with_backoff(ws_pay.delete_rows, start_row, end_row)''',
        '''        # PostgreSQL-first; Google Sheets block delete chỉ chạy trong mirror.\n        _p8new_payroll = _phase8_payroll_delete_snapshot(wanted)\n        def _p8mirror_delete_payroll():\n            for start_row, end_row in reversed(blocks):\n                if start_row == end_row:\n                    _gs_call_with_backoff(ws_pay.delete_rows, start_row)\n                else:\n                    _gs_call_with_backoff(ws_pay.delete_rows, start_row, end_row)\n            return True\n        _phase8_commit_dataset(\n            "payroll_history", _p8new_payroll, _p8mirror_delete_payroll,\n            operation="delete_snapshots", confirm_fn=_load_payroll_history_from_sheets,\n        )''',
        "payroll_delete",
    )

    # Once Phase 8 is active, its confirmed PostgreSQL snapshot must not be marked
    # stale again by the legacy post-write cleanup block.
    patch_all(
        'vpg.invalidate_dataset("payroll_history")',
        '_phase8_maybe_invalidate_dataset("payroll_history")',
        "payroll_invalidation_guard",
    )

    helper_anchor = "def _phase8_legacy_commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by):"
    helper_block = r'''


def _phase8_active():
    if vpg is None:
        return False
    fn = getattr(vpg, "phase8_is_enabled", None)
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:
        return False


def _phase8_commit_dataset(dataset_key, new_df, mirror_fn, operation="update", confirm_fn=None):
    if vpg is not None:
        fn = getattr(vpg, "phase8_dataset_commit", None)
        if callable(fn):
            return fn(dataset_key, new_df, mirror_fn, operation=operation, confirm_fn=confirm_fn)
    return mirror_fn()


def _phase8_maybe_invalidate_dataset(dataset_key):
    if vpg is None or not _vpg_is_enabled():
        return
    if _phase8_active() and str(dataset_key) in {"violation_debt", "payroll_history"}:
        return
    try:
        vpg.invalidate_dataset(dataset_key)
    except Exception:
        pass


def _phase8_violation_base_snapshot():
    try:
        d = load_violation_debt_ledger().copy()
    except Exception:
        d = pd.DataFrame()
    if not isinstance(d, pd.DataFrame):
        d = pd.DataFrame()
    for c in VIOLATION_DEBT_HEADERS:
        if c not in d.columns:
            d[c] = ""
    if "__sheet_row" not in d.columns:
        d["__sheet_row"] = list(range(2, len(d) + 2))
    return d[VIOLATION_DEBT_HEADERS + ["__sheet_row"]].reset_index(drop=True)


def _phase8_violation_frame(records):
    cols = VIOLATION_DEBT_HEADERS + ["__sheet_row"]
    if not records:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(records)
    for c in cols:
        if c not in d.columns:
            d[c] = ""
    return d[cols].reset_index(drop=True)


def _phase8_violation_upsert_snapshot(row_values, existing_row=None):
    base = _phase8_violation_base_snapshot()
    records = [r.to_dict() for _, r in base.iterrows()]
    vals = list(row_values or [])[:len(VIOLATION_DEBT_HEADERS)]
    vals += [""] * max(0, len(VIOLATION_DEBT_HEADERS) - len(vals))
    item = dict(zip(VIOLATION_DEBT_HEADERS, vals))
    target_idx = None
    if existing_row is not None:
        try:
            erow = int(existing_row)
        except Exception:
            erow = None
        if erow is not None:
            for i, rec in enumerate(records):
                try:
                    if int(float(rec.get("__sheet_row", 0) or 0)) == erow:
                        target_idx = i
                        break
                except Exception:
                    pass
    if target_idx is None:
        source_key = str(item.get("Mã nguồn", "")).strip()
        if source_key:
            for i, rec in enumerate(records):
                if str(rec.get("Mã nguồn", "")).strip() == source_key:
                    target_idx = i
                    break
    if target_idx is not None:
        item["__sheet_row"] = records[target_idx].get("__sheet_row", existing_row or target_idx + 2)
        records[target_idx] = item
    else:
        max_row = 1
        for rec in records:
            try:
                max_row = max(max_row, int(float(rec.get("__sheet_row", 0) or 0)))
            except Exception:
                pass
        item["__sheet_row"] = max_row + 1
        records.append(item)
    return _phase8_violation_frame(records)


def _phase8_violation_delete_snapshot(sheet_row):
    base = _phase8_violation_base_snapshot()
    try:
        target = int(sheet_row)
    except Exception:
        return base
    records = []
    for _, r in base.iterrows():
        try:
            rnum = int(float(r.get("__sheet_row", 0) or 0))
        except Exception:
            rnum = 0
        if rnum == target:
            continue
        records.append(r.to_dict())
    records.sort(key=lambda x: int(float(x.get("__sheet_row", 0) or 0)) if str(x.get("__sheet_row", "")).strip() else 10**9)
    for idx, rec in enumerate(records, start=2):
        rec["STT"] = idx - 1
        rec["__sheet_row"] = idx
    return _phase8_violation_frame(records)


def _phase8_build_violation_after_payroll(payroll_df, start_date, end_date, saved_by):
    base = _phase8_violation_base_snapshot()
    records = [r.to_dict() for _, r in base.iterrows()]
    payroll_names = {
        normalize_login_name(x): str(x).strip()
        for x in payroll_df.get("Tên Hệ thống", pd.Series(dtype=str)).tolist()
        if normalize_login_name(x)
    }
    now = datetime.now(VN_TZ)

    for rec in records:
        emp_key = normalize_login_name(rec.get("Tên nhân viên", ""))
        if emp_key not in payroll_names:
            continue
        if not _is_open_violation_debt_status(rec.get("Trạng thái", "")):
            continue
        due_from = _parse_vn_date(rec.get("Bắt đầu trừ từ", ""))
        if not due_from or due_from > start_date:
            continue
        rec["Trạng thái"] = VIOLATION_DEBT_DONE_STATUS
        rec["Ngày cập nhật"] = now.strftime("%d/%m/%Y")
        rec["Giờ cập nhật"] = now.strftime("%H:%M:%S")
        rec["Người cập nhật"] = str(saved_by)
        rec["Kỳ đã khấu trừ"] = f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"

    manual_adjusted_keys = set()
    for rec in records:
        if normalize_login_name(rec.get("Loại", "")) != normalize_login_name("Tạm hoãn vi phạm"):
            continue
        src_start = _parse_vn_date(rec.get("Kỳ phát sinh từ", ""))
        src_end = _parse_vn_date(rec.get("Kỳ phát sinh đến", ""))
        emp_key = normalize_login_name(rec.get("Tên nhân viên", ""))
        if src_start and src_end and emp_key:
            cs, ce = _canonicalize_payroll_period(src_start, src_end)
            if cs == start_date and ce == end_date:
                manual_adjusted_keys.add(emp_key)

    def upsert_negative(employee_name, amount):
        source_key = _violation_debt_source_key("NEG", start_date, end_date, employee_name)
        existing_idx = None
        for i, rec in enumerate(records):
            if str(rec.get("Mã nguồn", "")).strip() == source_key:
                existing_idx = i
                break
        if existing_idx is not None and not _is_open_violation_debt_status(records[existing_idx].get("Trạng thái", "")):
            return
        amount2 = max(0.0, float(_money_to_float(amount)))
        if amount2 <= 0 and existing_idx is None:
            return
        next_start, _ = _next_official_payroll_period(start_date, end_date)
        status = VIOLATION_DEBT_OPEN_STATUS if amount2 > 0 else VIOLATION_DEBT_DONE_STATUS
        values = [
            (existing_idx + 1 if existing_idx is not None else len(records) + 1),
            str(employee_name).strip(),
            int(round(amount2)),
            VIOLATION_DEBT_CONTENT,
            "Âm thực nhận",
            start_date.strftime("%d/%m/%Y"),
            end_date.strftime("%d/%m/%Y"),
            next_start.strftime("%d/%m/%Y"),
            status,
            source_key,
            now.strftime("%d/%m/%Y"),
            now.strftime("%H:%M:%S"),
            str(saved_by),
            "" if amount2 > 0 else f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
        ]
        item = dict(zip(VIOLATION_DEBT_HEADERS, values))
        if existing_idx is not None:
            item["__sheet_row"] = records[existing_idx].get("__sheet_row", existing_idx + 2)
            records[existing_idx] = item
        else:
            max_row = 1
            for rec in records:
                try:
                    max_row = max(max_row, int(float(rec.get("__sheet_row", 0) or 0)))
                except Exception:
                    pass
            item["__sheet_row"] = max_row + 1
            records.append(item)

    for _, r in payroll_df.iterrows():
        emp = str(r.get("Tên Hệ thống", "")).strip()
        emp_key = normalize_login_name(emp)
        if not emp_key:
            continue
        net = float(_money_to_float(r.get("Số tiền thực nhận", 0)))
        if emp_key in manual_adjusted_keys:
            upsert_negative(emp, 0.0)
        else:
            upsert_negative(emp, abs(net) if net < 0 else 0.0)

    return _phase8_violation_frame(records)


def commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by):
    if payroll_df is None or not isinstance(payroll_df, pd.DataFrame) or payroll_df.empty:
        return True, ""
    new_df = _phase8_build_violation_after_payroll(payroll_df, start_date, end_date, saved_by)
    return _phase8_commit_dataset(
        "violation_debt",
        new_df,
        lambda: _phase8_legacy_commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by),
        operation="commit_after_payroll",
        confirm_fn=_load_violation_debt_ledger_from_sheets,
    )


def _phase8_payroll_base_snapshot():
    try:
        d = load_payroll_history().copy()
    except Exception:
        d = pd.DataFrame()
    if not isinstance(d, pd.DataFrame):
        d = pd.DataFrame()
    for c in PAYROLL_HISTORY_HEADERS:
        if c not in d.columns:
            d[c] = ""
    return d[PAYROLL_HISTORY_HEADERS].reset_index(drop=True)


def _phase8_payroll_rows_frame(rows):
    return pd.DataFrame(list(rows or []), columns=PAYROLL_HISTORY_HEADERS)


def _phase8_payroll_append_snapshot(rows):
    base = _phase8_payroll_base_snapshot()
    extra = _phase8_payroll_rows_frame(rows)
    return pd.concat([base, extra], ignore_index=True) if not extra.empty else base


def _phase8_payroll_replace_snapshot(batch_id, rows):
    base = _phase8_payroll_base_snapshot()
    if "Mã bản lưu" in base.columns:
        base = base[base["Mã bản lưu"].astype(str).str.strip() != str(batch_id).strip()].copy()
    extra = _phase8_payroll_rows_frame(rows)
    return pd.concat([base, extra], ignore_index=True) if not extra.empty else base.reset_index(drop=True)


def _phase8_payroll_delete_snapshot(batch_ids):
    base = _phase8_payroll_base_snapshot()
    wanted = {str(x).strip() for x in (batch_ids or []) if str(x).strip()}
    if wanted and "Mã bản lưu" in base.columns:
        base = base[~base["Mã bản lưu"].astype(str).str.strip().isin(wanted)].copy()
    return base.reset_index(drop=True)

'''

    anchor_pos = source.rfind(helper_anchor)
    if anchor_pos < 0:
        warnings.append("phase8_helper_anchor:0")
    else:
        source = source[:anchor_pos] + helper_block + "\n" + source[anchor_pos:]

    return source, warnings
