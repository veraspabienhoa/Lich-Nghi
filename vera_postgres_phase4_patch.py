"""Source-level Phase 4 hooks for the immutable V92.6.99 core.

The core file itself is intentionally not modified. ``apply`` only replaces the
narrow persistence calls needed for PostgreSQL-first employee/leave CRUD.
Unmatched anchors are reported and left on the legacy Sheets path so a future
core change cannot take the app down.
"""


def apply(source: str):
    warnings = []

    def patch(old, new, label):
        nonlocal source
        count = source.count(old)
        if count != 1:
            warnings.append(f"{label}:{count}")
            return
        source = source.replace(old, new, 1)

    patch(
        "                    _gs_call_with_backoff(sheet_mk.append_row, row_data, value_input_option='USER_ENTERED')",
        '''                    _p4rec = dict(zip(CREDENTIAL_COLUMNS, row_data))
                    _p4rec["source_row"] = int(len(all_emps) + 1)
                    _vera_phase4_employee_upsert(
                        _p4rec,
                        lambda: _gs_call_with_backoff(sheet_mk.append_row, row_data, value_input_option='USER_ENTERED'),
                        operation="create",
                    )''',
        "employee_create",
    )

    patch(
        '''        if row_idx:
            if new_pass: sheet.update_cell(row_idx, 3, str(new_pass))
            if new_role is not None and str(new_role).strip():
                sheet.update_cell(row_idx, 4, str(new_role).strip().lower())
            sheet.update_cell(row_idx, 5, str(fullname))
            sheet.update_cell(row_idx, 6, str(dob))
            sheet.update_cell(row_idx, 7, f"'{phone}")
            sheet.update_cell(row_idx, 8, str(email))
            sheet.update_cell(row_idx, 9, str(address))
            # Hai cột mới được chèn giữa I và J.
            sheet.update_cell(row_idx, 10, f"'{bank_account}" if str(bank_account).strip() else "")
            sheet.update_cell(row_idx, 11, str(bank_name))
            _clear_dynamic_data_caches()''',
        '''        if row_idx:
            _p4vals = list(values[row_idx - 1])
            while len(_p4vals) < len(CREDENTIAL_COLUMNS):
                _p4vals.append("")
            _p4rec = dict(zip(CREDENTIAL_COLUMNS, _p4vals[:len(CREDENTIAL_COLUMNS)]))
            _p4rec["source_row"] = int(row_idx)
            if new_pass: _p4rec["Mật khẩu"] = str(new_pass)
            if new_role is not None and str(new_role).strip():
                _p4rec["Phân quyền"] = str(new_role).strip().lower()
            _p4rec["Họ và tên đầy đủ"] = str(fullname)
            _p4rec["Ngày sinh"] = str(dob)
            _p4rec["Điện thoại"] = str(phone).replace("'", "")
            _p4rec["Email"] = str(email)
            _p4rec["Địa chỉ"] = str(address)
            _p4rec["Số tài khoản ngân hàng"] = str(bank_account).replace("'", "")
            _p4rec["Tên ngân hàng"] = str(bank_name)
            def _p4mirror():
                if new_pass: sheet.update_cell(row_idx, 3, str(new_pass))
                if new_role is not None and str(new_role).strip(): sheet.update_cell(row_idx, 4, str(new_role).strip().lower())
                sheet.update_cell(row_idx, 5, str(fullname)); sheet.update_cell(row_idx, 6, str(dob))
                sheet.update_cell(row_idx, 7, f"'{phone}"); sheet.update_cell(row_idx, 8, str(email))
                sheet.update_cell(row_idx, 9, str(address))
                sheet.update_cell(row_idx, 10, f"'{bank_account}" if str(bank_account).strip() else "")
                sheet.update_cell(row_idx, 11, str(bank_name)); return True
            _vera_phase4_employee_upsert(_p4rec, _p4mirror, operation="profile_update")
            _clear_dynamic_data_caches()''',
        "employee_profile_update",
    )

    patch(
        '''        _gs_call_with_backoff(ss.batch_update, {"requests": requests})
        renumber_credential_sheet_stt(sheet_mk)''',
        '''        _p4names = [display_by_key.get(normalize_login_name(x), x) for x in names if normalize_login_name(x) in row_by_key]
        _vera_phase4_employee_delete(
            _p4names,
            lambda: _gs_call_with_backoff(ss.batch_update, {"requests": requests}),
            operation="delete",
        )
        renumber_credential_sheet_stt(sheet_mk)''',
        "employee_delete",
    )

    patch(
        '''        last_row = len(out_dq) + 1
        if out_dq:
            # 3 batch ranges thay cho update từng ô/dòng.
            gspread_update_range(sheet, f'D2:Q{last_row}', out_dq, value_input_option='USER_ENTERED')
            gspread_update_range(sheet, f'R2:T{last_row}', out_rt, value_input_option='USER_ENTERED')
            gspread_update_range(sheet, f'U2:U{last_row}', out_u, value_input_option='USER_ENTERED')''',
        '''        last_row = len(out_dq) + 1
        if out_dq:
            _p4records = []
            for _p4i, _p4raw in enumerate(all_vals[1:], start=2):
                _p4row = list(_p4raw) + [""] * max(0, len(CREDENTIAL_COLUMNS) - len(_p4raw))
                _p4pos = _p4i - 2
                _p4row[3:17] = list(out_dq[_p4pos]); _p4row[17:20] = list(out_rt[_p4pos]); _p4row[20] = out_u[_p4pos][0]
                _p4rec = dict(zip(CREDENTIAL_COLUMNS, _p4row[:len(CREDENTIAL_COLUMNS)])); _p4rec["source_row"] = _p4i
                _p4rec["Điện thoại"] = str(_p4rec.get("Điện thoại", "")).replace("'", "")
                _p4rec["Số tài khoản ngân hàng"] = str(_p4rec.get("Số tài khoản ngân hàng", "")).replace("'", "")
                _p4records.append(_p4rec)
            def _p4mirror():
                gspread_update_range(sheet, f'D2:Q{last_row}', out_dq, value_input_option='USER_ENTERED')
                gspread_update_range(sheet, f'R2:T{last_row}', out_rt, value_input_option='USER_ENTERED')
                gspread_update_range(sheet, f'U2:U{last_row}', out_u, value_input_option='USER_ENTERED'); return True
            _vera_phase4_employee_batch_upsert(_p4records, _p4mirror, operation="staff_bulk_import")''',
        "employee_batch_import",
    )

    patch(
        '''        row_values = _leave_record_to_sheet_row(record, sheet_headers=sheet_headers)
        target_row = _next_data_row_a_to_m(sheet_dp)
        gspread_update_range(sheet_dp, f"A{target_row}:M{target_row}", [row_values], value_input_option='USER_ENTERED')

        # V92.6.63: mọi đăng ký (user hoặc Auto Update) đều ghi Audit Log.''',
        '''        row_values = _leave_record_to_sheet_row(record, sheet_headers=sheet_headers)
        target_row = _next_data_row_a_to_m(sheet_dp)
        _p4rec = dict(record); _p4rec["__source_sheet_id"] = SHEET_DU_PHONG_ID; _p4rec["__source_row"] = int(target_row)
        _vera_phase4_leave_upsert(
            _p4rec,
            lambda: gspread_update_range(sheet_dp, f"A{target_row}:M{target_row}", [row_values], value_input_option='USER_ENTERED'),
            operation="create",
        )

        # V92.6.63: mọi đăng ký (user hoặc Auto Update) đều ghi Audit Log.''',
        "leave_single_create",
    )

    patch(
        '''        target_row = _next_data_row_a_to_m(sheet_dp)
        end_row = target_row + len(rows_to_write) - 1
        gspread_update_range(
            sheet_dp, f"A{target_row}:M{end_row}", rows_to_write,
            value_input_option="USER_ENTERED",
        )
        write_leave_activity_logs_batch(''',
        '''        target_row = _next_data_row_a_to_m(sheet_dp)
        end_row = target_row + len(rows_to_write) - 1
        _p4records = []
        for _p4i, _p4src in enumerate(records):
            _p4rec = dict(_p4src); _p4rec["__source_sheet_id"] = SHEET_DU_PHONG_ID; _p4rec["__source_row"] = int(target_row + _p4i); _p4records.append(_p4rec)
        _vera_phase4_leave_batch_upsert(
            _p4records,
            lambda: gspread_update_range(sheet_dp, f"A{target_row}:M{end_row}", rows_to_write, value_input_option="USER_ENTERED"),
            operation="range_create",
        )
        write_leave_activity_logs_batch(''',
        "leave_range_create",
    )

    patch(
        '''        gspread_batch_delete_rows(sheet, [row_index_1_based])
        rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, actor, primary_sheet=sheet) if affected_groups else 0''',
        '''        _vera_phase4_leave_delete(
            [deleted_row] if deleted_row is not None else [],
            lambda: gspread_batch_delete_rows(sheet, [row_index_1_based]),
            operation="delete_single",
        )
        rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, actor, primary_sheet=sheet) if affected_groups else 0''',
        "leave_delete_single",
    )

    patch(
        '''        new_values = _leave_record_to_sheet_row(
            recalculated,
            sheet_headers=sheet_headers,
            existing_values=existing,
        )
        gspread_update_range(target, f'A{row_idx}:M{row_idx}', [new_values], raw=False)

        new_group = _progressive_group_key(recalculated)''',
        '''        new_values = _leave_record_to_sheet_row(
            recalculated,
            sheet_headers=sheet_headers,
            existing_values=existing,
        )
        _p4rec = dict(recalculated); _p4rec["__source_sheet_id"] = SHEET_DU_PHONG_ID; _p4rec["__source_row"] = int(row_idx)
        _vera_phase4_leave_upsert(
            _p4rec,
            lambda: gspread_update_range(target, f'A{row_idx}:M{row_idx}', [new_values], raw=False),
            operation="reason_update",
        )

        new_group = _progressive_group_key(recalculated)''',
        "leave_reason_update",
    )

    patch(
        '''        deleted = gspread_batch_delete_rows(target, indices)

        rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, actor, primary_sheet=target) if affected_groups else 0''',
        '''        deleted = _vera_phase4_leave_delete(
            matched_delete_records,
            lambda: gspread_batch_delete_rows(target, indices),
            operation="delete_batch",
        )

        rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, actor, primary_sheet=target) if affected_groups else 0''',
        "leave_delete_batch",
    )

    return source, warnings
