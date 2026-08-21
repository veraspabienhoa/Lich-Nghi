"""V93.3 - Auto Update A:M + Auto Nghỉ không phép khi thiếu check-in.

Thiết kế:
- Cloud Scheduler gọi job này 1 lần/ngày lúc 20:00 Asia/Saigon.
- TimeSoft: chỉ tạo "Đi trễ không phép" cho NGÀY HÔM NAY, từ 5 phút.
- Hỗ trợ check-in theo ngưỡng: Ca1 2h=120 phút; Ca1 sau 0:0H 3h=180 phút; Ca2 sau 0:0H 1h=60 phút.
- Đối soát hôm nay + hôm qua: nếu dữ liệu Hỗ trợ được nhập sau Auto Update, xóa CHỈ
  dòng Đi trễ không phép do Auto TimeSoft tạo; không xóa dữ liệu nhập tay.
- Bảng tour: xử lý cột Vào trễ >= 5 phút, nhận cột nhân viên NV/Tên nhân viên.
- Sau khi có dòng Auto Update MỚI, gửi 1 email/nhân viên; CC:
  veraspabienhoa@gmail.com + tất cả quanly + tất cả letan.
- Không gửi email nếu lượt chạy không tạo dòng Auto Update mới.
"""
from __future__ import annotations

import html
import os
import re
import smtplib
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd

import timesoft_sync_job as ts

# V93.3: nguồn TourVera hiện hành + Auto vắng mặt sau 20:00.
ts.BANG_TOUR_FILE_ID = "151d1ueCwH2KXX-HPQF1uj340uWSCS2dW"

SMTP_SENDER_EMAIL = "veraspabienhoa@gmail.com"
SMTP_APP_PASSWORD = (os.getenv("SMTP_APP_PASSWORD", "") or "").strip()
AUTO_CC_EMAIL = "veraspabienhoa@gmail.com"
EMAIL_LOG_WORKSHEET = "AutoUpdateEmailLog"
DAILY_ACTOR_TS = "AUTO UPDATE 15:00/20:00 - TIMESOFT"
DAILY_ACTOR_TOUR = "AUTO UPDATE 15:00/20:00 - BẢNG TOUR"
DAILY_ACTOR_ABSENCE = "AUTO UPDATE 20:00 - NGHỈ KHÔNG PHÉP"
ABSENCE_AUTO_UPDATE_CUTOFF_HOUR = 20
ABSENCE_AUTO_ROLES = {"nhanvien", "leader"}
EMAIL_LOG_HEADERS = [
    "Ngày", "Tên nhân viên", "Email", "CC", "Số dòng mới", "Lý do",
    "Trạng thái", "Thời gian gửi", "Chi tiết"
]


LEAVE_DATA_COLUMNS = [
    "Ngày", "Thứ ngày", "Tên nhân viên", "Lý do nghỉ", "Loại nghỉ",
    "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
    "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật",
]


def _load_leave_catalog_new(client):
    """Giữ API catalog cũ nhưng bổ sung type = Loại nghỉ từ cột C LoaiNghi."""
    catalog = {}
    ws = client.open_by_key(ts.SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
    rows = ws.get_all_values()
    for row in rows[1:]:
        vals = list(row)
        name = ts._clean_reason(vals[1] if len(vals) > 1 else "")
        if not name or ts._norm(name) in {"ly do nghi", "loai nghi", "nan", "none"}:
            continue
        catalog[ts._reason_key(name)] = {
            "name": name,
            "type": str(vals[2] if len(vals) > 2 else "").strip(),
            "days": ts._number(vals[4] if len(vals) > 4 else 0, 0.0),
            "penalty": ts._number(vals[5] if len(vals) > 5 else 0, 0.0, money=True),
        }
    return catalog


def _sheet_rows_new(ws, source_id=None):
    """Đọc Sheet1 A:M duy nhất. source_id chỉ giữ để tương thích lời gọi cũ."""
    return ts._sheet_rows_a_to_m(ws)


def _load_all_leave_rows_new(client):
    """Chỉ đọc Sheet1 của SHEET_DU_PHONG_ID."""
    return ts.load_all_leave_rows(client)


def _next_primary_row(ws):
    return ts._next_data_row(ws)


def _save_auto_violation_new(client, d, employee, reason_item, detail, actor):
    """Dùng writer A:M chuẩn của timesoft_sync_job."""
    return ts.save_auto_violation(client, d, employee, reason_item, detail, actor)



def _log(msg: str) -> None:
    ts._log(f"V93.3 DAILY: {msg}")


def _is_support_reason(value) -> bool:
    return "ho tro" in ts._norm(value)


SUPPORT_LATE_ALLOWANCES = {
    ts._norm("Hỗ trợ Ca 1 đi trễ 2 tiếng"): 120,
    ts._norm("Hỗ trợ Ca 1 sau 0:0H đi trễ 3 tiếng"): 180,
    ts._norm("Hỗ trợ Ca 2 sau 0:0H đi trễ 1 tiếng"): 60,
}


def _support_allowance_minutes(reasons: list[str]) -> tuple[int | None, str]:
    """
    Trả về số phút được phép đi trễ theo Lý do Hỗ trợ.
    - Nếu gặp 1 trong 3 lý do chuẩn: dùng mức lớn nhất nếu có nhiều dòng Hỗ trợ.
    - Nếu có Hỗ trợ khác chưa cấu hình: trả None để giữ hành vi cũ = bỏ qua Auto phạt.
    """
    if not reasons:
        return 0, ""
    matched = []
    unknown = []
    for reason in reasons:
        key = ts._norm(reason)
        allowance = SUPPORT_LATE_ALLOWANCES.get(key)
        if allowance is None:
            unknown.append(str(reason or "").strip())
        else:
            matched.append((allowance, str(reason or "").strip()))
    if unknown:
        return None, unknown[0]
    if not matched:
        return None, str(reasons[0] or "")
    allowance, reason = max(matched, key=lambda x: x[0])
    return int(allowance), reason


def _timesoft_shift_late_minutes(row) -> float | None:
    """Ưu tiên tính trực tiếp Check-in - giờ bắt đầu ca; fallback về parser TimeSoft cũ."""
    start = ts._timesoft_row_value(row, ["StartWorkTime", "WorkTimeStart", "ShiftStartTime"])
    checkin = ts._timesoft_row_value(row, ["MachineTimeCheckInStr", "CheckInTimeStr", "CheckInTime"])

    def _to_minutes(value):
        m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(value or "").strip())
        if not m:
            return None
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3) or 0) / 60.0

    sm = _to_minutes(start)
    cm = _to_minutes(checkin)
    if sm is not None and cm is not None:
        diff = cm - sm
        if diff < -12 * 60:
            diff += 24 * 60
        return max(0.0, float(diff))

    return ts._parse_minutes_late(row)


def _support_index(rows: list[dict]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows or []:
        dkey = ts._date_key(row.get("Ngày"))
        ekey = ts._employee_key(row.get("Tên nhân viên"))
        reason = str(row.get("Lý do nghỉ", "") or "").strip()
        if dkey and ekey and _is_support_reason(reason):
            out[(dkey, ekey)].append(reason)
    return out


def _support_for_employee(supports: dict, d: date, employee: str) -> tuple[list[str], int | None, str]:
    reasons = supports.get((d.strftime("%d/%m/%Y"), ts._employee_key(employee)), [])
    allowance, matched_reason = _support_allowance_minutes(reasons)
    return reasons, allowance, matched_reason


def _has_support(supports: dict, d: date, employee: str) -> tuple[bool, str]:
    reasons, _, matched_reason = _support_for_employee(supports, d, employee)
    return bool(reasons), (matched_reason or (reasons[0] if reasons else ""))


def _is_auto_timesoft_row(row: dict) -> bool:
    actor = ts._norm(row.get("Người cập nhật", ""))
    detail = ts._norm(row.get("Chi tiết", ""))
    reason = ts._reason_key(row.get("Lý do nghỉ", ""))
    late_key = ts._reason_key("Đi trễ không phép")
    if reason != late_key:
        return False
    actor_ok = "auto update" in actor and "timesoft" in actor
    detail_ok = "auto update timesoft" in detail
    return actor_ok or detail_ok


def _strip_progressive_prefix(detail: str) -> str:
    text0 = str(detail or "").strip()
    return re.sub(
        r"^Người\s+Thứ\s+\d+\s+đi\s+trễ\s+không\s+phép\s*\|\s*",
        "",
        text0,
        flags=re.I,
    ).strip()


def _rebalance_primary_late_rows(client, affected_dates: set[date], catalog: dict) -> int:
    """Xếp lại Người Thứ và tiền phạt cho các dòng Auto TimeSoft còn lại.

    Mọi dòng Đi trễ không phép trong Sheet1 đều được tính vào thứ tự, nhưng chỉ
    các dòng do Auto TimeSoft tạo mới được sửa Chi tiết/Phạt. Dòng nhập tay giữ nguyên.
    """
    if not affected_dates:
        return 0
    primary_ws = client.open_by_key(ts.SHEET_DU_PHONG_ID).get_worksheet(0)
    rows = _sheet_rows_new(primary_ws, ts.SHEET_DU_PHONG_ID)
    reason_item = ts._catalog_item(catalog, "Đi trễ không phép") or {"penalty": 0}
    base_penalty = float(reason_item.get("penalty", 0) or 0)
    changed = 0
    for d in sorted(affected_dates):
        ordinal = 0
        for row in rows:
            if ts._parse_date(row.get("Ngày")) != d:
                continue
            if ts._reason_key(row.get("Lý do nghỉ", "")) != ts._reason_key("Đi trễ không phép"):
                continue
            ordinal += 1
            if not _is_auto_timesoft_row(row):
                continue
            new_penalty = base_penalty + max(0, ordinal - 2) * 100000
            base_detail = _strip_progressive_prefix(row.get("Chi tiết", ""))
            new_detail = f"Người Thứ {ordinal} đi trễ không phép"
            if base_detail:
                new_detail += f" | {base_detail}"
            sheet_row = int(row.get("__row", 0) or 0)
            if sheet_row < 2:
                continue
            old_penalty = ts._number(row.get("Phạt vi phạm"), 0.0, money=True)
            old_detail = str(row.get("Chi tiết", "") or "").strip()
            if old_detail != new_detail or abs(old_penalty - new_penalty) > 0.1:
                current = primary_ws.get(f"A{sheet_row}:M{sheet_row}")
                existing = list(current[0]) if current else [""] * 13
                headers = ts._get_leave_headers(primary_ws, strict=True)
                updated = dict(row)
                updated["Chi tiết"] = new_detail
                updated["Phạt vi phạm"] = new_penalty
                safe_values = ts._record_to_sheet_row(
                    updated, headers, existing_values=existing
                )
                primary_ws.update(
                    range_name=f"A{sheet_row}:M{sheet_row}",
                    values=[safe_values],
                    value_input_option="USER_ENTERED",
                )
                changed += 1
    return changed


def _late_minutes_from_auto_detail(detail: str) -> float | None:
    text = ts._norm(detail)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*phut", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def reverse_supported_timesoft_penalties(client, catalog: dict) -> dict:
    """Xóa đúng phạt Auto TimeSoft nếu Hỗ trợ được nhập muộn.

    Đối soát hôm nay + hôm qua để dữ liệu Hỗ trợ nhập sau 20:00 hôm trước vẫn
    có thể sửa phạt ở lần chạy ngày hôm sau.
    """
    result = {"reversed": 0, "rebalanced": 0, "errors": 0}
    all_rows = _load_all_leave_rows_new(client)
    supports = _support_index(all_rows)
    today = datetime.now(ts.VN_TZ).date()
    allowed_dates = {today, today - timedelta(days=1)}
    primary_ws = client.open_by_key(ts.SHEET_DU_PHONG_ID).get_worksheet(0)
    primary_rows = _sheet_rows_new(primary_ws, ts.SHEET_DU_PHONG_ID)
    to_delete: list[tuple[int, date, str, str]] = []
    affected_dates: set[date] = set()

    for row in primary_rows:
        d = ts._parse_date(row.get("Ngày"))
        if d not in allowed_dates or not _is_auto_timesoft_row(row):
            continue
        employee = str(row.get("Tên nhân viên", "") or "").strip()
        reasons, allowance, support_reason = _support_for_employee(supports, d, employee)
        if not reasons:
            continue

        # Hỗ trợ không nhận diện: giữ hành vi cũ, xóa Auto phạt.
        # Hỗ trợ có ngưỡng: chỉ xóa nếu số phút trễ <= mức được phép.
        late_minutes = _late_minutes_from_auto_detail(row.get("Chi tiết", ""))
        should_reverse = allowance is None
        if allowance is not None and late_minutes is not None:
            should_reverse = float(late_minutes) <= float(allowance)
        elif allowance is not None and late_minutes is None:
            # Không đủ dữ liệu để chứng minh vượt ngưỡng -> ưu tiên không phạt sai.
            should_reverse = True

        if not should_reverse:
            _log(
                f"AUTO TIMESOFT KEEP: {employee} · {d.strftime('%d/%m/%Y')} · "
                f"trễ {late_minutes:.0f} phút > Hỗ trợ {allowance} phút · '{support_reason}'"
            )
            continue

        sheet_row = int(row.get("__row", 0) or 0)
        if sheet_row >= 2:
            to_delete.append((sheet_row, d, employee, support_reason))

    for sheet_row, d, employee, support_reason in sorted(to_delete, reverse=True):
        try:
            primary_ws.delete_rows(sheet_row)
            result["reversed"] += 1
            affected_dates.add(d)
            _log(
                f"AUTO TIMESOFT REVERSE: {employee} · {d.strftime('%d/%m/%Y')} · "
                f"'{support_reason}' -> đã hủy phạt Auto TimeSoft trước đó"
            )
        except Exception as exc:
            result["errors"] += 1
            _log(f"AUTO TIMESOFT REVERSE ERROR row={sheet_row}: {type(exc).__name__}: {exc}")

    if affected_dates:
        try:
            result["rebalanced"] = _rebalance_primary_late_rows(client, affected_dates, catalog)
        except Exception as exc:
            result["errors"] += 1
            _log(f"REBALANCE ERROR: {type(exc).__name__}: {exc}")
    _log(
        f"AUTO TIMESOFT REVERSE SUMMARY: reversed={result['reversed']}; "
        f"rebalanced={result['rebalanced']}; errors={result['errors']}"
    )
    return result


def _read_added_row(client, add_msg: str) -> dict:
    m = re.search(r"ADDED_ROW_(\d+)", str(add_msg or ""))
    if not m:
        return {}
    row_num = int(m.group(1))
    ws = client.open_by_key(ts.SHEET_DU_PHONG_ID).get_worksheet(0)
    headers = ts._get_leave_headers(ws, strict=True)
    vals = ws.get(f"A{row_num}:M{row_num}")
    row = list(vals[0]) if vals else []
    out = ts._sheet_row_to_record(row, headers)
    out["__row"] = row_num
    out["__raw_values"] = list(row[:13]) + [""] * max(0, 13 - len(row))
    return out


def _employment_status_map(client) -> dict[str, str]:
    """Đọc TrangThaiNhanSu; mặc định nhân sự không có dòng trạng thái là Đang làm việc."""
    out = {}
    try:
        ss = client.open_by_key(ts.SHEET_MAT_KHAU_ID)
        ws = ss.worksheet("TrangThaiNhanSu")
        vals = ws.get_all_values()
        for row in vals[1:]:
            name = str(row[1] if len(row) > 1 else "").strip()
            status = str(row[2] if len(row) > 2 else "").strip()
            if name:
                out[ts._employee_key(name)] = ts._norm(status)
    except Exception as exc:
        _log(f"ABSENCE status WARN: {type(exc).__name__}")
    return out


def _active_shifted_staff(client) -> list[dict]:
    """Chỉ nhanvien/leader đang làm việc và có Ca làm việc."""
    ws = client.open_by_key(ts.SHEET_MAT_KHAU_ID).get_worksheet(0)
    vals = ws.get_all_values()
    if len(vals) <= 1:
        return []

    statuses = _employment_status_map(client)
    active_key = ts._norm("Đang làm việc")
    temp_key = ts._norm("Tạm thời nghỉ việc")
    left_key = ts._norm("Đã nghỉ việc")

    out = []
    for row in vals[1:]:
        name = str(row[1] if len(row) > 1 else "").strip()
        role = ts._norm(row[3] if len(row) > 3 else "")
        shift = str(row[14] if len(row) > 14 else "").strip()
        if not name or role not in ABSENCE_AUTO_ROLES or not shift:
            continue
        status = statuses.get(ts._employee_key(name), active_key)
        if status in {temp_key, left_key} or status != active_key:
            continue
        out.append({"name": name, "key": ts._employee_key(name), "role": role, "shift": shift})
    return out


def _checkin_keys_today(checkin_df: pd.DataFrame, target_date: date) -> set[str]:
    keys = set()
    if not isinstance(checkin_df, pd.DataFrame) or checkin_df.empty:
        return keys
    for _, row in checkin_df.iterrows():
        raw_date = ts._timesoft_row_value(
            row, ["WorkDateStr", "WorkDate", "CreateDateStr", "CreateDate"]
        )
        parsed = ts._parse_date(raw_date)
        if parsed is not None and parsed != target_date:
            continue
        raw_name = ts._timesoft_row_value(
            row, ["employeeInfo.Name", "EmployeeName", "employeeName", "Name", "FullName"]
        )
        key = ts._employee_key(raw_name)
        if key:
            keys.add(key)
    return keys


def _covered_leave_keys(rows: list[dict], target_date: date) -> set[str]:
    """Có lịch nghỉ hợp lệ khi cùng ngày Số ngày tính = 0.5 hoặc 1."""
    out = set()
    for row in rows or []:
        if ts._parse_date(row.get("Ngày")) != target_date:
            continue
        days = ts._number(row.get("Số ngày tính"), 0.0)
        if not (abs(float(days) - 0.5) < 1e-9 or abs(float(days) - 1.0) < 1e-9):
            continue
        key = ts._employee_key(row.get("Tên nhân viên"))
        if key:
            out.add(key)
    return out


def process_absence_without_checkin_today(
    client,
    catalog: dict,
    checkin_df: pd.DataFrame,
) -> tuple[dict, list[dict]]:
    """
    Sau 20:00: nhanvien/leader active + có ca, không check-in và không có lịch
    Số ngày tính 0.5/1 -> Auto Nghỉ không phép.
    """
    result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    added_rows = []
    now = datetime.now(ts.VN_TZ)
    today = now.date()

    if now.hour < ABSENCE_AUTO_UPDATE_CUTOFF_HOUR:
        _log("ABSENCE: chưa tới 20:00 -> bỏ qua để tránh phạt nhầm.")
        return result, added_rows

    # Không biến lỗi TimeSoft/API thành phạt hàng loạt.
    if not isinstance(checkin_df, pd.DataFrame) or checkin_df.empty:
        _log("ABSENCE SAFE-SKIP: snapshot check-in rỗng.")
        return result, added_rows

    checkin_keys = _checkin_keys_today(checkin_df, today)
    if not checkin_keys:
        _log("ABSENCE SAFE-SKIP: không nhận diện được tên nhân viên trong snapshot.")
        return result, added_rows

    rows = _load_all_leave_rows_new(client)
    covered_keys = _covered_leave_keys(rows, today)
    staff = _active_shifted_staff(client)

    reason_item = catalog.get(ts._reason_key("Nghỉ không phép"))
    if not reason_item:
        result["errors"] += 1
        _log("ABSENCE ERROR: LoaiNghi chưa có 'Nghỉ không phép'.")
        return result, added_rows

    weekday = ts._weekday_vi(today)

    for profile in staff:
        if profile["key"] in checkin_keys:
            continue
        if profile["key"] in covered_keys:
            continue

        result["eligible"] += 1
        detail = (
            f"Auto Update TimeSoft · không có dữ liệu check-in · {weekday}"
            " · Sheet1 không có lịch nghỉ Số ngày tính 0.5 hoặc 1"
            f" · Ca {profile['shift']}"
        )
        ok, msg = _save_auto_violation_new(
            client, today, profile["name"], reason_item, detail, DAILY_ACTOR_ABSENCE
        )
        if ok and msg == "SKIP_DUPLICATE":
            result["skipped"] += 1
        elif ok:
            result["added"] += 1
            added = _read_added_row(client, msg)
            if added:
                added_rows.append(added)
            _log(f"ABSENCE ADDED: {profile['name']} · {today}")
        else:
            result["errors"] += 1
            _log(f"ABSENCE ERROR: {profile['name']}: {msg}")

    return result, added_rows


def process_timesoft_today(client, cfg: dict, employee_map: dict, catalog: dict, supports: dict, checkin_df: pd.DataFrame) -> tuple[dict, list[dict]]:
    result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0, "support_skipped": 0}
    added_rows: list[dict] = []
    reason_item = ts._catalog_item(catalog, "Đi trễ không phép")
    if not reason_item:
        _log("AUTO TIMESOFT ERROR: LoaiNghi chưa có 'Đi trễ không phép'.")
        result["errors"] += 1
        return result, added_rows
    if not isinstance(checkin_df, pd.DataFrame) or checkin_df.empty:
        return result, added_rows

    threshold = max(ts.AUTO_PENALTY_MINUTES, int(cfg.get("threshold_minutes", ts.AUTO_PENALTY_MINUTES)))
    today = datetime.now(ts.VN_TZ).date()
    for _, row in checkin_df.iterrows():
        minutes = _timesoft_shift_late_minutes(row)
        if minutes is None or minutes < threshold:
            continue
        result["eligible"] += 1
        raw_name = ts._timesoft_row_value(row, [
            "employeeInfo.Name", "EmployeeName", "employeeName", "Name", "FullName"
        ])
        employee = ts.canonical_employee(raw_name, employee_map)
        if not employee:
            result["skipped"] += 1
            _log(f"AUTO TIMESOFT SKIP: không khớp nhân viên '{raw_name}'")
            continue

        raw_date = ts._timesoft_row_value(row, ["WorkDateStr", "WorkDate", "CreateDateStr", "CreateDate"])
        work_date = ts._parse_date(raw_date) or today
        # V84.4: tuyệt đối không tạo phạt mới cho ngày cũ.
        if work_date != today:
            result["skipped"] += 1
            continue

        support_reasons, allowance, support_reason = _support_for_employee(supports, work_date, employee)
        if support_reasons:
            # Hỗ trợ khác chưa cấu hình: giữ hành vi cũ = bỏ qua Auto phạt.
            if allowance is None:
                result["skipped"] += 1
                result["support_skipped"] += 1
                _log(
                    f"AUTO TIMESOFT SKIP SUPPORT: {employee} · {work_date.strftime('%d/%m/%Y')} · "
                    f"{minutes:.0f} phút · Hỗ trợ khác '{support_reason}'"
                )
                continue

            # 3 lý do Hỗ trợ có ngưỡng: chỉ bỏ qua nếu check-in trễ <= mức cho phép.
            if float(minutes) <= float(allowance):
                result["skipped"] += 1
                result["support_skipped"] += 1
                _log(
                    f"AUTO TIMESOFT SKIP SUPPORT LIMIT: {employee} · {work_date.strftime('%d/%m/%Y')} · "
                    f"trễ {minutes:.0f}/{allowance} phút · '{support_reason}'"
                )
                continue

            _log(
                f"AUTO TIMESOFT SUPPORT EXCEEDED: {employee} · {work_date.strftime('%d/%m/%Y')} · "
                f"trễ {minutes:.0f} phút > cho phép {allowance} phút · '{support_reason}'"
            )

        shift_start = ts._timesoft_row_value(row, ["StartWorkTime", "WorkTimeStart", "ShiftStartTime"])
        checkin_time = ts._timesoft_row_value(row, ["MachineTimeCheckInStr", "CheckInTimeStr", "CheckInTime"])
        detail = f"Auto Update TimeSoft · check-in muộn {int(round(minutes))} phút"
        if support_reasons and allowance is not None and float(minutes) > float(allowance):
            detail += f" · Hỗ trợ cho phép {allowance} phút nhưng đã vượt {int(round(float(minutes) - float(allowance)))} phút"
        if shift_start:
            detail += f" · Ca bắt đầu {shift_start}"
        if checkin_time:
            detail += f" · Check-in {checkin_time}"
        ok, msg = _save_auto_violation_new(client, work_date, employee, reason_item, detail, DAILY_ACTOR_TS)
        if ok and msg == "SKIP_DUPLICATE":
            result["skipped"] += 1
        elif ok:
            result["added"] += 1
            added = _read_added_row(client, msg)
            if added:
                added["__minutes"] = int(round(minutes))
                added_rows.append(added)
            _log(f"AUTO TIMESOFT ADDED: {employee} · {minutes:.0f} phút · {work_date}")
        else:
            result["errors"] += 1
            _log(f"AUTO TIMESOFT ERROR: {employee}: {msg}")
    return result, added_rows


def process_tour_today(client, cfg: dict, employee_map: dict, catalog: dict) -> tuple[dict, list[dict]]:
    result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    added_rows: list[dict] = []
    try:
        df = ts.load_bang_tour_input()
    except Exception as exc:
        _log(f"AUTO TOUR ERROR: {type(exc).__name__}: {exc}")
        result["errors"] += 1
        return result, added_rows
    if df.empty:
        return result, added_rows

    name_col = (
        ts._find_col(df, "Tên nhân viên")
        or ts._find_col(df, "Tên Nhân Viên")
        or ts._find_col(df, "Nhân viên")
        or ts._find_col(df, "NV")
    )
    late_col = ts._find_col(df, "Vào trễ")
    out_col = ts._find_col(df, "Giờ ra")
    in_col = ts._find_col(df, "Giờ vào")
    if name_col is None or late_col is None:
        _log(f"AUTO TOUR ERROR: thiếu cột NV/Tên nhân viên hoặc Vào trễ. columns={list(df.columns)}")
        result["errors"] += 1
        return result, added_rows

    threshold = max(ts.AUTO_PENALTY_MINUTES, int(cfg.get("threshold_minutes", ts.AUTO_PENALTY_MINUTES)))
    today = datetime.now(ts.VN_TZ).date()
    for _, row in df.iterrows():
        minutes = ts._tour_late_minutes(row.get(late_col, ""))
        if minutes is None or minutes < threshold:
            continue
        result["eligible"] += 1
        raw_name = row.get(name_col, "")
        employee = ts.canonical_employee(raw_name, employee_map)
        if not employee:
            result["skipped"] += 1
            _log(f"AUTO TOUR SKIP: không khớp nhân viên '{raw_name}'")
            continue
        reason_item = ts._outside_reason(minutes, catalog)
        if not reason_item:
            result["errors"] += 1
            _log(f"AUTO TOUR ERROR: chưa có loại phù hợp trong LoaiNghi cho {minutes:.0f} phút")
            continue
        detail_parts = [f"Auto Update Bảng tour · vào muộn {int(round(minutes))} phút"]
        if out_col is not None and str(row.get(out_col, "")).strip():
            detail_parts.append(f"Giờ ra {str(row.get(out_col)).strip()}")
        if in_col is not None and str(row.get(in_col, "")).strip():
            detail_parts.append(f"Giờ vào {str(row.get(in_col)).strip()}")
        ok, msg = _save_auto_violation_new(
            client, today, employee, reason_item, " · ".join(detail_parts), DAILY_ACTOR_TOUR
        )
        if ok and msg == "SKIP_DUPLICATE":
            result["skipped"] += 1
        elif ok:
            result["added"] += 1
            added = _read_added_row(client, msg)
            if added:
                added["__minutes"] = int(round(minutes))
                added_rows.append(added)
            _log(f"AUTO TOUR ADDED: {employee} · {reason_item['name']} · {minutes:.0f} phút")
        else:
            result["errors"] += 1
            _log(f"AUTO TOUR ERROR: {employee}: {msg}")
    return result, added_rows


def employee_directory(client) -> tuple[dict[str, str], list[str]]:
    ws = client.open_by_key(ts.SHEET_MAT_KHAU_ID).get_worksheet(0)
    vals = ws.get_all_values()
    emails: dict[str, str] = {}
    cc = [AUTO_CC_EMAIL]
    if not vals:
        return emails, cc
    header = [ts._norm(x) for x in vals[0]]

    def find_idx(candidates, fallback):
        for i, h in enumerate(header):
            if h in candidates:
                return i
        return fallback

    name_idx = find_idx({"ten nhan vien", "ten he thong", "username", "user name"}, 1)
    role_idx = find_idx({"phan quyen", "role", "quyen"}, 3)
    email_idx = find_idx({"email", "e mail", "mail"}, 7)

    for row in vals[1:]:
        name = str(row[name_idx] if name_idx < len(row) else "").strip()
        role = ts._norm(row[role_idx] if role_idx < len(row) else "")
        email = str(row[email_idx] if email_idx < len(row) else "").strip()
        if name and "@" in email:
            emails[ts._employee_key(name)] = email
        if role in {"quanly", "quan ly", "letan", "le tan"} and "@" in email:
            cc.append(email)

    dedup_cc = []
    seen = set()
    for email in cc:
        key = str(email).strip().casefold()
        if key and "@" in key and key not in seen:
            dedup_cc.append(str(email).strip())
            seen.add(key)
    return emails, dedup_cc


def _email_log_ws(client):
    ss = client.open_by_key(ts.SHEET_DU_PHONG_ID)
    try:
        ws = ss.worksheet(EMAIL_LOG_WORKSHEET)
    except Exception:
        ws = ss.add_worksheet(title=EMAIL_LOG_WORKSHEET, rows=2000, cols=12)
    current = ws.get("A1:I1")
    row = current[0] if current else []
    if list(row[:9]) != EMAIL_LOG_HEADERS:
        ws.update(range_name="A1:I1", values=[EMAIL_LOG_HEADERS], value_input_option="USER_ENTERED")
    return ws


def _append_email_log(client, employee: str, to_email: str, cc: list[str], rows: list[dict], status: str, detail: str):
    try:
        ws = _email_log_ws(client)
        now = datetime.now(ts.VN_TZ)
        reasons = "; ".join(str(r.get("Lý do nghỉ", "") or "").strip() for r in rows)
        ws.append_row([
            now.strftime("%d/%m/%Y"), employee, to_email, ", ".join(cc), len(rows), reasons,
            status, now.strftime("%d/%m/%Y %H:%M:%S"), str(detail or "")[:1000]
        ], value_input_option="USER_ENTERED")
    except Exception as exc:
        _log(f"EMAIL LOG WARN: {type(exc).__name__}: {exc}")


def _money_text(value) -> str:
    n = ts._number(value, 0.0, money=True)
    return f"{n:,.0f}".replace(",", ".") + " VNĐ"


def send_employee_email(to_email: str, cc: list[str], employee: str, rows: list[dict]) -> tuple[bool, str]:
    if not SMTP_SENDER_EMAIL or not SMTP_APP_PASSWORD:
        return False, "Thiếu SMTP_SENDER_EMAIL/SMTP_APP_PASSWORD."
    if not to_email or "@" not in to_email:
        return False, "Nhân viên chưa có email hợp lệ."
    clean_cc = []
    seen = {to_email.casefold()}
    for e in cc:
        e = str(e or "").strip()
        if "@" not in e:
            continue
        k = e.casefold()
        if k not in seen:
            clean_cc.append(e)
            seen.add(k)

    target_date = str(rows[0].get("Ngày", "") if rows else "").strip() or datetime.now(ts.VN_TZ).strftime("%d/%m/%Y")
    table_rows = []
    for r in rows:
        reason = html.escape(str(r.get("Lý do nghỉ", "") or ""))
        detail = html.escape(str(r.get("Chi tiết", "") or ""))
        penalty = html.escape(_money_text(r.get("Phạt vi phạm", 0)))
        table_rows.append(
            "<tr>"
            f"<td style='padding:7px;border:1px solid #ddd'>{reason}</td>"
            f"<td style='padding:7px;border:1px solid #ddd'>{detail}</td>"
            f"<td style='padding:7px;border:1px solid #ddd;text-align:right'>{penalty}</td>"
            "</tr>"
        )
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;line-height:1.5">
      <p>Chào <b>{html.escape(employee)}</b>,</p>
      <p>Hệ thống VERA SPA ghi nhận Auto Update ngày <b>{html.escape(target_date)}</b>:</p>
      <table style="border-collapse:collapse;width:100%">
        <thead><tr>
          <th style="padding:7px;border:1px solid #ddd;text-align:left">Nội dung</th>
          <th style="padding:7px;border:1px solid #ddd;text-align:left">Chi tiết</th>
          <th style="padding:7px;border:1px solid #ddd;text-align:right">Mức phạt</th>
        </tr></thead>
        <tbody>{''.join(table_rows)}</tbody>
      </table>
      <p>Dữ liệu trên đã được cập nhật vào hệ thống.</p>
      <p>Nếu thông tin chưa chính xác, vui lòng liên hệ Lễ tân/Quản lý.</p>
      <p>Trân trọng,<br><b>VERA SPA</b></p>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Vera Spa <{SMTP_SENDER_EMAIL}>"
    msg["To"] = to_email
    if clean_cc:
        msg["Cc"] = ", ".join(clean_cc)
    msg["Subject"] = f"[VERA SPA] Thông báo Auto Update ngày {target_date}"
    msg.attach(MIMEText(body, "html", "utf-8"))
    recipients = [to_email] + clean_cc
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(SMTP_SENDER_EMAIL, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_SENDER_EMAIL, recipients, msg.as_string())
        return True, "SENT"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _sent_email_keys(client) -> set[tuple[str, str, str]]:
    """
    Các Auto Update đã gửi mail thành công.
    Key = Ngày + Nhân viên + Lý do nghỉ.
    Đọc từ AutoUpdateEmailLog để chống gửi trùng giữa 15:00 / 20:00 / 21:00 / chạy tay.
    """
    try:
        ws = _email_log_ws(client)
        vals = ws.get_all_values()
    except Exception as exc:
        _log(f"EMAIL SENT-KEY WARN: {type(exc).__name__}: {exc}")
        return set()

    if len(vals) <= 1:
        return set()

    header = [ts._norm(x) for x in vals[0]]

    def idx_of(names, fallback):
        for i, h in enumerate(header):
            if h in names:
                return i
        return fallback

    date_idx = idx_of({"ngay"}, 0)
    emp_idx = idx_of({"ten nhan vien"}, 1)
    reason_idx = idx_of({"ly do"}, 5)
    status_idx = idx_of({"trang thai"}, 6)

    out = set()
    for row in vals[1:]:
        status = ts._norm(row[status_idx] if status_idx < len(row) else "")
        if status != "sent":
            continue
        d = str(row[date_idx] if date_idx < len(row) else "").strip()
        emp = str(row[emp_idx] if emp_idx < len(row) else "").strip()
        reasons_text = str(row[reason_idx] if reason_idx < len(row) else "").strip()
        if not d or not emp:
            continue
        reasons = [x.strip() for x in reasons_text.split(";") if x.strip()]
        for reason in reasons:
            out.add((d, ts._employee_key(emp), ts._reason_key(reason)))
    return out


def _is_any_auto_penalty_row(row: dict) -> bool:
    """
    Nhận tất cả dòng phạt do Auto Update tạo, bất kể nguồn:
    TimeSoft / Bảng tour / CA1 / Job 21:00 / Admin chạy Auto Update.
    """
    actor = ts._norm(row.get("Người cập nhật", ""))
    detail = ts._norm(row.get("Chi tiết", ""))
    penalty = ts._number(row.get("Phạt vi phạm", 0), 0.0, money=True)

    # Quy tắc email chỉ áp dụng cho dòng có Phạt vi phạm > 0.
    if float(penalty or 0) <= 0:
        return False

    return ("auto update" in actor) or ("auto update" in detail)


def pending_auto_penalty_rows(client, target_date: date | None = None) -> list[dict]:
    """
    Quét LIVE Sheet1 và lấy mọi Auto Update phạt của ngày cần gửi mà CHƯA có log SENT.
    Nhờ vậy nếu lần tạo dữ liệu đã ghi phạt nhưng email lỗi/mất kết nối thì lần job kế tiếp sẽ gửi lại.
    """
    target_date = target_date or datetime.now(ts.VN_TZ).date()
    date_key = target_date.strftime("%d/%m/%Y")
    sent = _sent_email_keys(client)

    ws = client.open_by_key(ts.SHEET_DU_PHONG_ID).get_worksheet(0)
    rows = _sheet_rows_new(ws, ts.SHEET_DU_PHONG_ID)

    pending = []
    for row in rows:
        if ts._date_key(row.get("Ngày")) != date_key:
            continue
        if not _is_any_auto_penalty_row(row):
            continue

        employee = str(row.get("Tên nhân viên", "") or "").strip()
        reason = str(row.get("Lý do nghỉ", "") or "").strip()
        key = (date_key, ts._employee_key(employee), ts._reason_key(reason))
        if key in sent:
            continue

        pending.append(dict(row))

    return pending


def send_pending_auto_penalty_notifications(client, target_date: date | None = None) -> dict:
    """
    Hàm chuẩn duy nhất để gửi email cho mọi Auto Update phạt:
    FROM: veraspabienhoa@gmail.com
    TO: email người bị phạt
    CC: veraspabienhoa@gmail.com + tất cả quanly + tất cả letan

    Chỉ log SENT sau khi SMTP gửi thành công. FAILED sẽ được retry ở lần chạy kế tiếp.
    """
    rows = pending_auto_penalty_rows(client, target_date=target_date)
    if not rows:
        _log("EMAIL PENDING: không có Auto Update phạt chưa gửi.")
        return {"employees": 0, "sent": 0, "failed": 0, "missing_email": 0, "pending_rows": 0}

    result = send_notifications(client, rows)
    result["pending_rows"] = len(rows)
    return result


def send_notifications(client, added_rows: list[dict]) -> dict:
    result = {"employees": 0, "sent": 0, "failed": 0, "missing_email": 0}
    if not added_rows:
        _log("Không có dòng Auto Update mới -> không gửi email.")
        return result
    email_map, cc = employee_directory(client)
    grouped: dict[str, list[dict]] = defaultdict(list)
    display_names: dict[str, str] = {}
    for row in added_rows:
        employee = str(row.get("Tên nhân viên", "") or "").strip()
        key = ts._employee_key(employee)
        if not key:
            continue
        grouped[key].append(row)
        display_names[key] = employee
    result["employees"] = len(grouped)

    for key, rows in grouped.items():
        employee = display_names[key]
        to_email = str(email_map.get(key, "") or "").strip()
        if not to_email or "@" not in to_email:
            result["failed"] += 1
            result["missing_email"] += 1
            detail = "Nhân viên chưa có email hợp lệ trong danh sách tài khoản."
            _append_email_log(client, employee, to_email, cc, rows, "FAILED", detail)
            _log(f"EMAIL SKIP: {employee}: {detail}")
            continue
        ok, detail = send_employee_email(to_email, cc, employee, rows)
        if ok:
            result["sent"] += 1
            _append_email_log(client, employee, to_email, cc, rows, "SENT", detail)
            _log(f"EMAIL SENT: {employee} -> {to_email}; rows={len(rows)}")
        else:
            result["failed"] += 1
            _append_email_log(client, employee, to_email, cc, rows, "FAILED", detail)
            _log(f"EMAIL ERROR: {employee}: {detail}")
    return result


def run_daily() -> int:
    started = datetime.now(ts.VN_TZ)
    _log(f"Bắt đầu Auto Update 15:00/20:00; date={started.strftime('%Y-%m-%d')}")
    try:
        client = ts.get_gspread_client()
        # V84.4 chạy theo Scheduler 20:00; không phụ thuộc job snapshot 5 phút.
        # Admin PAUSED vẫn được tôn trọng để có thể dừng khẩn cấp toàn bộ Auto Update.
        cfg = ts.load_auto_penalty_config(client)
        _log(f"Config status={cfg.get('status')}; threshold={cfg.get('threshold_minutes', 5)} phút")
        if cfg.get("paused"):
            _log("Auto Update đang PAUSED bởi Admin -> không ghi phạt, không gửi email.")
            return 0

        employee_map = ts.load_employee_name_map(client)
        catalog = _load_leave_catalog_new(client)
        _log(f"Đã tải danh mục: employees={len(employee_map)}; leave_types={len(catalog)}")

        reverse_result = reverse_supported_timesoft_penalties(client, catalog)
        live_rows = _load_all_leave_rows_new(client)
        supports = _support_index(live_rows)

        today = datetime.now(ts.VN_TZ).date()
        session = ts.create_authenticated_session()
        checkin_df, checkin_meta = ts.fetch_checkin(session, today)
        _log(f"TimeSoft hôm nay: checkin_rows={len(checkin_df)}; total={checkin_meta.get('Total')}")

        ts_result, ts_added = process_timesoft_today(client, cfg, employee_map, catalog, supports, checkin_df)
        absence_result, absence_added = process_absence_without_checkin_today(
            client, catalog, checkin_df
        )
        tour_result, tour_added = process_tour_today(client, cfg, employee_map, catalog)
        # V86.12: KHÔNG chỉ gửi theo added_rows của lượt hiện tại.
        # Quét toàn bộ Auto Update phạt hôm nay chưa có log SENT để:
        # - gửi đủ mọi nguồn Auto Update;
        # - retry nếu email của lượt trước bị lỗi;
        # - xử lý cả trường hợp dòng đã ghi thành công nhưng _read_added_row không lấy được.
        email_result = send_pending_auto_penalty_notifications(client, target_date=today)

        _log(
            "Hoàn tất Auto Update 15:00/20:00: "
            f"TimeSoft eligible={ts_result['eligible']} added={ts_result['added']} skipped={ts_result['skipped']} "
            f"support_skipped={ts_result['support_skipped']} errors={ts_result['errors']}; "
            f"Absence eligible={absence_result['eligible']} added={absence_result['added']} skipped={absence_result['skipped']} errors={absence_result['errors']}; "
            f"Tour eligible={tour_result['eligible']} added={tour_result['added']} skipped={tour_result['skipped']} errors={tour_result['errors']}; "
            f"Reverse={reverse_result['reversed']}; Emails sent={email_result['sent']} failed={email_result['failed']}"
        )
        _log(f"Job xong trong {(datetime.now(ts.VN_TZ) - started).total_seconds():.1f}s")
        return 0
    except Exception as exc:
        _log(f"FATAL ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(run_daily())
