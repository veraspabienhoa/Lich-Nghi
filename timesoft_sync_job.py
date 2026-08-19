"""Cloud Run Job V84: TimeSoft snapshot + Auto Update phạt 24/7.

Mỗi lần Cloud Scheduler gọi:
1) Đồng bộ TimeSoft -> PostgreSQL (giữ nguyên chức năng V82/V83).
2) Đọc trạng thái CauHinhAutoPhat trên Google Sheet.
3) Nếu PAUSED: không ghi phạt.
4) Nếu RUNNING:
   - TimeSoft: Đi trễ không phép khi trễ >= 5 phút.
   - Bảng tour: Ra ngoài vào muộn khi cột Vào trễ >= 5 phút.
   - Cẩm Nhung * được đối chiếu như Cẩm Nhung.
   - Chống trùng Ngày + Nhân viên + Lý do.
   - Ghi đúng Sheet1 A:J tại last row.

Google Sheets dùng Application Default Credentials của service account gắn với Cloud Run Job.
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import gspread
import pandas as pd
import requests
from google.auth import default as google_auth_default
from sqlalchemy import text

import vera_postgres as vpg

VN_TZ = timezone(timedelta(hours=7))

# -------------------- TimeSoft --------------------
BASE_URL = str(os.getenv("TIMESOFT_BASE_URL", "https://vera.timesoft.vn") or "https://vera.timesoft.vn").rstrip("/")
USERNAME = str(os.getenv("TIMESOFT_USERNAME", "") or "").strip()
PASSWORD = str(os.getenv("TIMESOFT_PASSWORD", "") or "")
SYNC_DAYS = max(1, min(7, int(os.getenv("TIMESOFT_SYNC_DAYS", "2") or 2)))
CHECKIN_PAGE_SIZE = max(20, min(500, int(os.getenv("TIMESOFT_CHECKIN_PAGE_SIZE", "100") or 100)))
MAX_CHECKIN_PAGES = 500
LOCK_NAME = "vera-timesoft-background-sync-v84"

REPORT_SUMMARY_PAGE = "/Report/ReportSummaryInvoice/Index"
REPORT_CHECKIN_PAGE = "/Report/ReportEmployeeCheckin/Index"
API_SUMMARY = "/Report/ReportSummaryInvoice/SearchFullText"
API_CHECKIN = "/Report/ReportEmployeeCheckin/SearchElastic"

# -------------------- Vera / Google --------------------
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
SHEET_LICH_NGHI_2_ID = "1bLxn-L5gXui8pCL1b9TxshCNcykM7jg0J49Dkr5b4DI"
BANG_TOUR_FILE_ID = "1toTjr9r2YTIou2vySWtdsdY6DB8uGvPn"

AUTO_PENALTY_CONFIG_WORKSHEET = "CauHinhAutoPhat"
AUTO_PENALTY_CONFIG_HEADERS = [
    "Key", "Trạng thái", "Ngưỡng phút", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
]
AUTO_PENALTY_CONFIG_KEY = "AUTO_PENALTY"
AUTO_PENALTY_RUNNING = "RUNNING"
AUTO_PENALTY_PAUSED = "PAUSED"
AUTO_PENALTY_MINUTES = 5

LEAVE_HEADERS = [
    "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
    "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
    "Giờ cập nhật", "Người cập nhật",
]

PROGRESSIVE_REASONS = {
    "nghi khong phep": "Nghỉ không phép",
    "di tre khong phep": "Đi trễ không phép",
    "ve som khong phep": "Về sớm không phép",
    "ra som khong phep": "Về sớm không phép",
}

OUTSIDE_LATE_EXCLUDED = {
    "ra ngoai vao muon duoi 30 phut",
    "ra ngoai vao muon duoi 60 phut",
    "ra ngoai vao muon duoi 120 phut",
    "ra ngoai vao muon tu 120 phut tro len",
    "ra ngoai vao muon tren 120 phut",
}


def _log(msg: str) -> None:
    now = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now} +07] {msg}", flush=True)


def _strip_accents(value) -> str:
    text0 = str(value or "")
    text0 = unicodedata.normalize("NFD", text0)
    text0 = "".join(ch for ch in text0 if unicodedata.category(ch) != "Mn")
    return text0.replace("đ", "d").replace("Đ", "D")


def _norm(value) -> str:
    return " ".join(_strip_accents(value).casefold().strip().split())


def _clean_employee_name(value) -> str:
    text0 = " ".join(str(value or "").strip().split())
    text0 = re.sub(r"\s*\*+\s*$", "", text0).strip()
    return " ".join(text0.split())


def _employee_key(value) -> str:
    return _norm(_clean_employee_name(value))


def _clean_reason(value) -> str:
    return " ".join(str(value or "").replace("🔴", "").strip().split())


def _reason_key(value) -> str:
    return _norm(_clean_reason(value))


def _date_key(value) -> str:
    d = _parse_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text0 = str(value).strip()
    if not text0 or text0.casefold() in {"nan", "nat", "none"}:
        return None
    try:
        parsed = pd.to_datetime(text0, dayfirst=True, errors="coerce")
        if pd.notna(parsed):
            return parsed.date()
    except Exception:
        pass
    return None


def _number(value, default=0.0, money=False) -> float:
    try:
        if value is None or pd.isna(value):
            return float(default)
    except Exception:
        pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except Exception:
            return float(default)
    text0 = str(value).strip()
    if not text0 or text0.casefold() in {"nan", "none", "nat", "-"}:
        return float(default)
    try:
        if money:
            text0 = (text0.replace(".", "").replace(",", "").replace(" ", "")
                     .replace("đ", "").replace("Đ", "").replace("VNĐ", "").replace("VND", ""))
        else:
            text0 = text0.replace(",", ".")
        return float(text0)
    except Exception:
        return float(default)


# ==========================================================
# GOOGLE AUTH / SHEETS
# ==========================================================
def get_gspread_client() -> gspread.Client:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds, _ = google_auth_default(scopes=scopes)
    return gspread.authorize(creds)


def _worksheet_or_create(ss, title: str, rows=20, cols=8):
    try:
        return ss.worksheet(title)
    except Exception:
        return ss.add_worksheet(title=title, rows=rows, cols=cols)


def load_auto_penalty_config(client: gspread.Client) -> dict:
    default_cfg = {
        "paused": False,
        "status": AUTO_PENALTY_RUNNING,
        "threshold_minutes": AUTO_PENALTY_MINUTES,
    }
    ss = client.open_by_key(SHEET_DU_PHONG_ID)
    ws = _worksheet_or_create(ss, AUTO_PENALTY_CONFIG_WORKSHEET, rows=20, cols=8)
    vals = ws.get("A1:F2")
    header = vals[0] if vals else []
    if list(header[:6]) != AUTO_PENALTY_CONFIG_HEADERS:
        ws.update(range_name="A1:F1", values=[AUTO_PENALTY_CONFIG_HEADERS], value_input_option="USER_ENTERED")
    row = vals[1] if len(vals) > 1 else []
    if not row or str(row[0]).strip() != AUTO_PENALTY_CONFIG_KEY:
        now = datetime.now(VN_TZ)
        row = [
            AUTO_PENALTY_CONFIG_KEY, AUTO_PENALTY_RUNNING, AUTO_PENALTY_MINUTES,
            now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"), "Hệ thống",
        ]
        ws.update(range_name="A2:F2", values=[row], value_input_option="USER_ENTERED")
    row = list(row) + [""] * max(0, 6 - len(row))
    status = str(row[1] or AUTO_PENALTY_RUNNING).strip().upper()
    try:
        threshold = int(float(row[2] or AUTO_PENALTY_MINUTES))
    except Exception:
        threshold = AUTO_PENALTY_MINUTES
    threshold = max(AUTO_PENALTY_MINUTES, threshold)
    return {
        "paused": status == AUTO_PENALTY_PAUSED,
        "status": status,
        "threshold_minutes": threshold,
    }


def _sheet_rows_a_to_j(ws) -> list[dict]:
    values = ws.get("A:J")
    if not values or len(values) < 2:
        return []
    header = [str(x).strip() for x in values[0][:10]]
    if len(header) < 10 or not header[0]:
        header = LEAVE_HEADERS[:]
    # Tương thích tiêu đề cũ Loại nghỉ.
    header = ["Lý do nghỉ" if x == "Loại nghỉ" else x for x in header]
    rows = []
    for sheet_row, row in enumerate(values[1:], start=2):
        vals = list(row[:10]) + [""] * max(0, 10 - len(row))
        if not any(str(v).strip() for v in vals):
            continue
        item = {header[i] if i < len(header) and header[i] else LEAVE_HEADERS[i]: vals[i] for i in range(10)}
        for c in LEAVE_HEADERS:
            item.setdefault(c, "")
        item["__row"] = sheet_row
        rows.append(item)
    return rows


def _ensure_leave_header(ws) -> None:
    current = ws.get("A1:J1")
    row = current[0] if current else []
    if not any(str(v).strip() for v in row):
        ws.update(range_name="A1:J1", values=[LEAVE_HEADERS], value_input_option="USER_ENTERED")


def _next_data_row(ws) -> int:
    values = ws.get("A:J")
    last = 0
    for idx, row in enumerate(values, start=1):
        if any(str(v).strip() for v in row[:10]):
            last = idx
    return max(2, last + 1)


def load_all_leave_rows(client: gspread.Client) -> list[dict]:
    rows: list[dict] = []
    for sheet_id in (SHEET_DU_PHONG_ID, SHEET_LICH_NGHI_2_ID):
        try:
            ws = client.open_by_key(sheet_id).get_worksheet(0)
            for item in _sheet_rows_a_to_j(ws):
                item["__source"] = sheet_id
                rows.append(item)
        except Exception as exc:
            _log(f"WARN: không đọc được nguồn lịch {sheet_id[:8]}...: {type(exc).__name__}")
    # Loại trùng logic giữa 2 nguồn.
    logical: dict[tuple[str, str, str], dict] = {}
    for item in rows:
        key = (_date_key(item.get("Ngày")), _employee_key(item.get("Tên nhân viên")), _reason_key(item.get("Lý do nghỉ")))
        if all(key):
            logical[key] = item
    return list(logical.values())


def load_employee_name_map(client: gspread.Client) -> dict[str, str]:
    ws = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
    values = ws.get_all_values()
    if len(values) < 2:
        return {}
    header = values[0]
    name_idx = None
    for i, h in enumerate(header):
        if _norm(h) in {"ten nhan vien", "ten he thong", "username", "user name"}:
            name_idx = i
            break
    if name_idx is None:
        name_idx = 1  # Sheet1 hiện dùng cột B cho Tên nhân viên.
    out: dict[str, str] = {}
    for row in values[1:]:
        if name_idx >= len(row):
            continue
        name = str(row[name_idx]).strip()
        key = _employee_key(name)
        if key and key not in out:
            out[key] = name
    return out


def canonical_employee(raw_name, employee_map: dict[str, str]) -> str:
    key = _employee_key(raw_name)
    if not key:
        return ""
    return str(employee_map.get(key, "")).strip()


def load_leave_catalog(client: gspread.Client) -> dict[str, dict]:
    ws = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
    rows = ws.get_all_values()
    catalog: dict[str, dict] = {}
    if len(rows) < 2:
        return catalog
    for row in rows[1:]:
        vals = list(row)
        name = str(vals[1]).strip() if len(vals) > 1 else ""
        if not name or _norm(name) in {"loai nghi", "ly do nghi", "nan", "none"}:
            continue
        clean = _clean_reason(name)
        catalog[_reason_key(clean)] = {
            "name": clean,
            "days": _number(vals[4] if len(vals) > 4 else 0, 0.0),
            "penalty": _number(vals[5] if len(vals) > 5 else 0, 0.0, money=True),
        }
    return catalog


def _catalog_item(catalog: dict[str, dict], wanted: str) -> dict | None:
    exact = catalog.get(_reason_key(wanted))
    if exact:
        return exact
    target = _norm(wanted)
    for item in catalog.values():
        if _norm(item.get("name", "")) == target:
            return item
    return None


def _outside_reason(minutes: float, catalog: dict[str, dict]) -> dict | None:
    m = float(minutes)
    if m < 30:
        candidates = ["Ra ngoài vào muộn dưới 30 phút"]
    elif m < 60:
        candidates = ["Ra ngoài vào muộn dưới 60 phút"]
    elif m < 120:
        candidates = ["Ra ngoài vào muộn dưới 120 phút"]
    else:
        candidates = [
            "Ra ngoài vào muộn từ 120 phút trở lên",
            "Ra ngoài vào muộn trên 120 phút",
            "Ra ngoài vào muộn dưới 120 phút",
        ]
    for name in candidates:
        item = _catalog_item(catalog, name)
        if item:
            return item
    return None


def _progressive_canonical(reason: str) -> str | None:
    key = _norm(reason)
    if key in OUTSIDE_LATE_EXCLUDED:
        return None
    return PROGRESSIVE_REASONS.get(key)


def _same_leave_exists(rows: list[dict], d: date, employee: str, reason: str) -> bool:
    key = (d.strftime("%d/%m/%Y"), _employee_key(employee), _reason_key(reason))
    for r in rows:
        rkey = (_date_key(r.get("Ngày")), _employee_key(r.get("Tên nhân viên")), _reason_key(r.get("Lý do nghỉ")))
        if rkey == key:
            return True
    return False


def _monthly_accumulated_days(rows: list[dict], d: date, employee: str, new_days: float) -> float:
    total = 0.0
    emp_key = _employee_key(employee)
    for r in rows:
        rd = _parse_date(r.get("Ngày"))
        if not rd or rd.year != d.year or rd.month != d.month:
            continue
        if _employee_key(r.get("Tên nhân viên")) != emp_key:
            continue
        total += _number(r.get("Số ngày tính"), 0.0)
    return total + float(new_days)


def _progressive_ordinal(rows: list[dict], d: date, reason: str) -> int:
    canonical = _progressive_canonical(reason)
    if not canonical:
        return 1
    count = 0
    for r in rows:
        rd = _parse_date(r.get("Ngày"))
        if rd != d:
            continue
        if _progressive_canonical(_clean_reason(r.get("Lý do nghỉ", ""))) == canonical:
            count += 1
    return count + 1


def save_auto_violation(
    client: gspread.Client,
    d: date,
    employee: str,
    reason_item: dict,
    detail: str,
    actor: str,
) -> tuple[bool, str]:
    """Ghi 1 vi phạm vào Sheet1 A:J; chống trùng LIVE ngay trước khi ghi."""
    reason = _clean_reason(reason_item.get("name", ""))
    days = float(reason_item.get("days", 0) or 0)
    base_penalty = float(reason_item.get("penalty", 0) or 0)
    if not employee or not reason:
        return False, "Thiếu nhân viên hoặc lý do."

    primary_ws = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
    _ensure_leave_header(primary_ws)
    live_rows = load_all_leave_rows(client)
    if _same_leave_exists(live_rows, d, employee, reason):
        return True, "SKIP_DUPLICATE"

    accumulated = _monthly_accumulated_days(live_rows, d, employee, days)
    penalty = base_penalty
    canonical = _progressive_canonical(reason)
    if canonical:
        ordinal = _progressive_ordinal(live_rows, d, reason)
        penalty += max(0, ordinal - 2) * 100000
        prefix = f"Người Thứ {ordinal} {canonical.lower()}"
        detail = f"{prefix} | {detail}" if detail else prefix

    now = datetime.now(VN_TZ)
    row = [
        d.strftime("%d/%m/%Y"),
        employee,
        reason,
        str(detail or "").strip(),
        days,
        accumulated,
        penalty,
        now.strftime("%d/%m/%Y"),
        now.strftime("%H:%M:%S"),
        actor,
    ]
    target = _next_data_row(primary_ws)
    primary_ws.update(
        range_name=f"A{target}:J{target}",
        values=[row],
        value_input_option="USER_ENTERED",
    )
    return True, f"ADDED_ROW_{target}"


# ==========================================================
# BẢNG TOUR
# ==========================================================
def _download_bang_tour_bytes() -> bytes:
    # Link usercontent thường trả thẳng binary cho file public/được chia sẻ.
    urls = [
        f"https://drive.usercontent.google.com/download?id={BANG_TOUR_FILE_ID}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={BANG_TOUR_FILE_ID}&confirm=t",
    ]
    errors = []
    for url in urls:
        try:
            r = requests.get(url, timeout=90, allow_redirects=True)
            r.raise_for_status()
            content = r.content
            if content[:2] == b"PK" and len(content) > 1000:
                return content
            # Google đôi khi trả HTML confirmation; thử lấy confirm token/link.
            text0 = content[:200000].decode("utf-8", errors="ignore")
            m = re.search(r'href="([^"]*download[^"]*)"', text0, flags=re.I)
            if m:
                href = m.group(1).replace("&amp;", "&")
                if href.startswith("/"):
                    href = "https://drive.google.com" + href
                rr = requests.get(href, timeout=90, allow_redirects=True)
                rr.raise_for_status()
                if rr.content[:2] == b"PK" and len(rr.content) > 1000:
                    return rr.content
            errors.append("Google Drive trả HTML thay vì XLSM")
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    raise RuntimeError("Không tải được Bảng tour: " + " | ".join(errors[-2:]))


def load_bang_tour_input() -> pd.DataFrame:
    content = _download_bang_tour_bytes()
    bio = io.BytesIO(content)
    if not zipfile.is_zipfile(bio):
        raise RuntimeError("Bảng tour tải về không phải file XLSM hợp lệ.")
    bio.seek(0)
    raw = pd.read_excel(bio, sheet_name="Input", header=None, engine="openpyxl")
    if raw.empty:
        return pd.DataFrame()
    header_idx = 19 if len(raw) > 19 else 0
    max_cols = min(24, raw.shape[1])
    raw = raw.iloc[:, :max_cols]
    header_vals = raw.iloc[header_idx].tolist()
    headers = []
    seen = {}
    for i, v in enumerate(header_vals):
        text0 = "" if pd.isna(v) else str(v).strip()
        if not text0 or text0.casefold() == "nan":
            text0 = f"COL_{i+1}"
        if text0 in seen:
            seen[text0] += 1
            text0 = f"{text0}_{i+1}"
        else:
            seen[text0] = 1
        headers.append(text0)
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = headers
    return df.dropna(how="all").reset_index(drop=True)


def _find_col(df: pd.DataFrame, wanted: str):
    target = _norm(wanted)
    exact = []
    contains = []
    for c in df.columns:
        normc = _norm(c)
        if normc == target:
            exact.append(c)
        elif target in normc:
            contains.append(c)
    return exact[0] if exact else (contains[0] if contains else None)


def _tour_late_minutes(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, timedelta):
        return max(0.0, value.total_seconds() / 60.0)
    try:
        if isinstance(value, pd.Timedelta):
            return max(0.0, value.total_seconds() / 60.0)
    except Exception:
        pass
    if hasattr(value, "hour") and hasattr(value, "minute") and not isinstance(value, (int, float)):
        try:
            return max(0.0, float(value.hour * 60 + value.minute + getattr(value, "second", 0) / 60.0))
        except Exception:
            pass
    # Trong file Tour hiện tại Vào trễ thường là số phút. Nếu là serial thời gian Excel < 1,
    # chuyển sang phút (1 ngày = 1440 phút).
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = float(value)
        if 0 < n < 1:
            return max(0.0, n * 1440.0)
        return max(0.0, n)
    text0 = str(value).strip()
    if not text0 or text0.casefold() in {"nan", "none", "nat"}:
        return None
    m = re.search(r"(-?\d+(?:[\.,]\d+)?)\s*(?:phút|phut|min|mins|minute|minutes)?", text0, flags=re.I)
    if m and ":" not in text0:
        try:
            return max(0.0, float(m.group(1).replace(",", ".")))
        except Exception:
            pass
    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text0)
    if tm:
        h, mi, sec = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
        return max(0.0, h * 60 + mi + sec / 60.0)
    return None


def process_tour_penalties(client: gspread.Client, cfg: dict, employee_map: dict, catalog: dict) -> dict:
    result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    try:
        df = load_bang_tour_input()
    except Exception as exc:
        _log(f"AUTO TOUR ERROR: {type(exc).__name__}: {exc}")
        result["errors"] += 1
        return result
    if df.empty:
        return result
    name_col = (
        _find_col(df, "Tên nhân viên")
        or _find_col(df, "Tên Nhân Viên")
        or _find_col(df, "Nhân viên")
        or _find_col(df, "NV")
    )
    late_col = _find_col(df, "Vào trễ")
    out_col = _find_col(df, "Giờ ra")
    in_col = _find_col(df, "Giờ vào")
    if name_col is None or late_col is None:
        _log(f"AUTO TOUR ERROR: thiếu cột Tên nhân viên hoặc Vào trễ. columns={list(df.columns)}")
        result["errors"] += 1
        return result
    threshold = max(AUTO_PENALTY_MINUTES, int(cfg.get("threshold_minutes", AUTO_PENALTY_MINUTES)))
    today = datetime.now(VN_TZ).date()
    for _, row in df.iterrows():
        minutes = _tour_late_minutes(row.get(late_col, ""))
        if minutes is None or minutes < threshold:
            continue
        result["eligible"] += 1
        raw_name = row.get(name_col, "")
        employee = canonical_employee(raw_name, employee_map)
        if not employee:
            _log(f"AUTO TOUR SKIP: không khớp nhân viên '{raw_name}'")
            result["skipped"] += 1
            continue
        reason_item = _outside_reason(minutes, catalog)
        if not reason_item:
            _log(f"AUTO TOUR ERROR: chưa có loại phù hợp trong LoaiNghi cho {minutes:.0f} phút")
            result["errors"] += 1
            continue
        detail_parts = [f"Auto Update Bảng tour · vào muộn {int(round(minutes))} phút"]
        if out_col is not None and str(row.get(out_col, "")).strip():
            detail_parts.append(f"Giờ ra {str(row.get(out_col)).strip()}")
        if in_col is not None and str(row.get(in_col, "")).strip():
            detail_parts.append(f"Giờ vào {str(row.get(in_col)).strip()}")
        ok, msg = save_auto_violation(
            client, today, employee, reason_item, " · ".join(detail_parts), "AUTO UPDATE 24/7 - BẢNG TOUR"
        )
        if ok and msg == "SKIP_DUPLICATE":
            result["skipped"] += 1
        elif ok:
            result["added"] += 1
            _log(f"AUTO TOUR ADDED: {employee} · {reason_item['name']} · {minutes:.0f} phút")
        else:
            result["errors"] += 1
            _log(f"AUTO TOUR ERROR: {employee}: {msg}")
    return result


# ==========================================================
# TIMESOFT LOGIN + API
# ==========================================================
def _date_range_text(start_date: date, end_date: date) -> str:
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"


def _visible_input(page, selectors):
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 12)):
                node = loc.nth(i)
                if node.is_visible():
                    return node
        except Exception:
            continue
    return None


def _login_error_text(page) -> str:
    selectors = [
        ".validation-summary-errors", ".field-validation-error", ".alert-danger",
        ".alert-warning", ".error", ".error-message", ".text-danger", "[role=\"alert\"]",
        ".toast-message", ".notifyjs-bootstrap-error",
    ]
    messages = []
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 8)):
                node = loc.nth(i)
                if not node.is_visible():
                    continue
                txt = re.sub(r"\s+", " ", str(node.inner_text() or "")).strip()
                if txt and txt not in messages:
                    messages.append(txt[:200])
        except Exception:
            continue
    return " | ".join(messages[:3])


def _login_with_playwright(page, verify_url: str) -> tuple[bool, str]:
    password_box = _visible_input(page, [
        'input[type="password"]', 'input[name*="password" i]', 'input[id*="password" i]',
        'input[name*="pass" i]', 'input[id*="pass" i]',
    ])
    if password_box is None:
        return True, "session-existing"
    username_box = _visible_input(page, [
        'input[name="UserName"]', 'input[name="Username"]', 'input[name*="username" i]',
        'input[id*="username" i]', 'input[name*="user" i]', 'input[id*="user" i]',
        'input[name*="account" i]', 'input[id*="account" i]', 'input[name*="login" i]',
        'input[id*="login" i]', 'input[type="email"]', 'input[type="text"]',
    ])
    if username_box is None:
        return False, "Không nhận diện được ô tài khoản TimeSoft."
    try:
        username_box.click()
        username_box.fill(USERNAME)
        password_box.click()
        password_box.fill(PASSWORD)
        try:
            password_box.press("Tab")
        except Exception:
            pass
        page.wait_for_timeout(250)
    except Exception as exc:
        return False, f"Không nhập được form TimeSoft: {type(exc).__name__}"
    submit = None
    for selector in [
        'button[type="submit"]', 'input[type="submit"]',
        'input[type="button"][value*="đăng" i]', 'input[type="button"][value*="login" i]',
        'button:has-text("Đăng nhập")', 'button:has-text("Đăng Nhập")', 'button:has-text("Login")',
        'a:has-text("Đăng nhập")', 'a:has-text("Đăng Nhập")', '[onclick*="login" i]', '[id*="login" i]',
    ]:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                node = loc.nth(i)
                if node.is_visible() and node.is_enabled():
                    submit = node
                    break
            if submit is not None:
                break
        except Exception:
            continue
    try:
        if submit is not None:
            submit.click(timeout=8000)
        else:
            password_box.press("Enter")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        page.goto(verify_url, wait_until="domcontentloaded", timeout=35000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(700)
    except Exception as exc:
        return False, f"Không xác minh được session TimeSoft: {type(exc).__name__}"
    final_url = str(page.url or "")
    still_password = _visible_input(page, ['input[type="password"]', 'input[name*="password" i]'])
    if "/user/login" in final_url.lower() and still_password is not None:
        err = _login_error_text(page)
        return False, "Đăng nhập TimeSoft thất bại" + (f": {err}" if err else ".")
    return True, "login-ok"


def _requests_session_from_browser(cookies, user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        "User-Agent": str(user_agent or "Mozilla/5.0"),
    })
    for cookie in cookies or []:
        name = str(cookie.get("name", "") or "")
        value = str(cookie.get("value", "") or "")
        if not name:
            continue
        kwargs = {}
        domain = str(cookie.get("domain", "") or "").strip()
        path = str(cookie.get("path", "") or "/").strip() or "/"
        if domain:
            kwargs["domain"] = domain
        if path:
            kwargs["path"] = path
        try:
            session.cookies.set(name, value, **kwargs)
        except Exception:
            session.cookies.set(name, value)
    return session


def create_authenticated_session() -> requests.Session:
    if not USERNAME or not PASSWORD:
        raise RuntimeError("Thiếu TIMESOFT_USERNAME/TIMESOFT_PASSWORD trong Secret Manager.")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Image chưa cài Playwright/Chromium.") from exc
    verify_url = urljoin(BASE_URL + "/", REPORT_SUMMARY_PAGE.lstrip("/"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        try:
            context = browser.new_context(ignore_https_errors=False, viewport={"width": 1440, "height": 1000}, locale="vi-VN")
            page = context.new_page()
            page.goto(verify_url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(500)
            ok, msg = _login_with_playwright(page, verify_url)
            if not ok:
                raise RuntimeError(msg)
            cookies = context.cookies()
            try:
                user_agent = str(page.evaluate("navigator.userAgent") or "Mozilla/5.0")
            except Exception:
                user_agent = "Mozilla/5.0"
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()
    return _requests_session_from_browser(cookies, user_agent)


def post_json(session: requests.Session, api_path: str, referer_path: str, payload: dict, timeout: int = 60) -> dict:
    url = urljoin(BASE_URL + "/", api_path.lstrip("/"))
    referer = urljoin(BASE_URL + "/", referer_path.lstrip("/"))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": referer,
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = session.post(url, json=payload, headers=headers, timeout=timeout, allow_redirects=True)
    if resp.status_code in {401, 403} or "/user/login" in str(resp.url or "").lower():
        raise RuntimeError("Phiên TimeSoft hết hạn trong lúc gọi API.")
    resp.raise_for_status()
    ctype = str(resp.headers.get("content-type", "") or "").lower()
    if "json" not in ctype:
        raise RuntimeError("TimeSoft API không trả JSON.")
    data = resp.json()
    code = str((data or {}).get("ReturnCode", "") or "")
    if code and code != "000000":
        raise RuntimeError(f"TimeSoft ReturnCode={code}: {(data or {}).get('Message', '')}")
    return data or {}


def fetch_summary(session: requests.Session, target_date: date) -> tuple[pd.DataFrame, dict]:
    payload = {
        "objectSearch": {
            "TypeData": 0,
            "TypeInvoice": 1,
            "TachInvoiceSpa": 0,
            "CreateTimeRange": _date_range_text(target_date, target_date),
        },
        "pageSize": 0,
    }
    data = post_json(session, API_SUMMARY, REPORT_SUMMARY_PAGE, payload)
    rows = data.get("Data") or []
    df = pd.json_normalize(rows, sep=".") if isinstance(rows, list) and rows else pd.DataFrame()
    meta_keys = [
        "Total", "TotalMoney", "TotalDiscount", "TotalQuantity", "TotalInvoice",
        "TotalActualRevenu", "TotalPromotion", "TotalPayment", "TotalActualTerm",
        "TotalDebt", "ReportByYear", "Message",
    ]
    meta = {k: data.get(k) for k in meta_keys if k in data}
    return df, meta


def fetch_checkin(session: requests.Session, target_date: date) -> tuple[pd.DataFrame, dict]:
    all_rows = []
    total_expected = None
    for page_index in range(1, MAX_CHECKIN_PAGES + 1):
        payload = {
            "objectSearch": {"CreateDateRange": _date_range_text(target_date, target_date)},
            "pageIndex": page_index,
            "pageSize": CHECKIN_PAGE_SIZE,
        }
        data = post_json(session, API_CHECKIN, REPORT_CHECKIN_PAGE, payload)
        if total_expected is None:
            try:
                total_expected = int(data.get("Total") or 0)
            except Exception:
                total_expected = 0
        page_rows = data.get("Data") or []
        if not isinstance(page_rows, list) or not page_rows:
            break
        all_rows.extend(page_rows)
        if total_expected and len(all_rows) >= total_expected:
            break
    df = pd.json_normalize(all_rows, sep=".") if all_rows else pd.DataFrame()
    return df, {"Total": total_expected if total_expected is not None else len(all_rows)}


def _timesoft_row_value(row, candidates):
    for c in candidates:
        if c in row.index:
            value = row.get(c)
            if value is not None and str(value).strip().casefold() not in {"", "nan", "none", "nat"}:
                return value
    return ""


def _time_minutes(value) -> float | None:
    text0 = str(value or "").strip()
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text0)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return h * 60 + mi + sec / 60.0


def _parse_minutes_late(row) -> float | None:
    direct = _timesoft_row_value(row, [
        "TotalMinuteInGoLate", "TotalMinuteGoLate", "MinuteInGoLate", "GoLateMinute", "LateMinute"
    ])
    try:
        if str(direct).strip() != "":
            return max(0.0, float(str(direct).replace(",", ".").strip()))
    except Exception:
        pass
    start = _timesoft_row_value(row, ["StartWorkTime", "WorkTimeStart", "ShiftStartTime"])
    checkin = _timesoft_row_value(row, ["MachineTimeCheckInStr", "CheckInTimeStr", "CheckInTime"])
    sm = _time_minutes(start)
    cm = _time_minutes(checkin)
    if sm is None or cm is None:
        return None
    diff = cm - sm
    if diff < -12 * 60:
        diff += 24 * 60
    return max(0.0, float(diff))


def process_timesoft_penalties(
    client: gspread.Client,
    cfg: dict,
    employee_map: dict,
    catalog: dict,
    checkin_by_date: list[tuple[date, pd.DataFrame]],
) -> dict:
    result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    reason_item = _catalog_item(catalog, "Đi trễ không phép")
    if not reason_item:
        _log("AUTO TIMESOFT ERROR: LoaiNghi chưa có 'Đi trễ không phép'.")
        result["errors"] += 1
        return result
    threshold = max(AUTO_PENALTY_MINUTES, int(cfg.get("threshold_minutes", AUTO_PENALTY_MINUTES)))
    today = datetime.now(VN_TZ).date()

    for target_date, df in checkin_by_date:
        # V84.1: Auto phạt chỉ xử lý dữ liệu của NGÀY HÔM NAY.
        # PostgreSQL vẫn được phép đồng bộ hôm nay + hôm qua theo SYNC_DAYS.
        if target_date != today:
            continue
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for _, row in df.iterrows():
            minutes = _parse_minutes_late(row)
            if minutes is None or minutes < threshold:
                continue
            result["eligible"] += 1
            raw_name = _timesoft_row_value(row, [
                "employeeInfo.Name", "EmployeeName", "employeeName", "Name", "FullName"
            ])
            employee = canonical_employee(raw_name, employee_map)
            if not employee:
                _log(f"AUTO TIMESOFT SKIP: không khớp nhân viên '{raw_name}'")
                result["skipped"] += 1
                continue
            raw_date = _timesoft_row_value(row, ["WorkDateStr", "WorkDate", "CreateDateStr", "CreateDate"])
            work_date = _parse_date(raw_date) or target_date
            shift_start = _timesoft_row_value(row, ["StartWorkTime", "WorkTimeStart", "ShiftStartTime"])
            checkin_time = _timesoft_row_value(row, ["MachineTimeCheckInStr", "CheckInTimeStr", "CheckInTime"])
            detail = f"Auto Update TimeSoft · check-in muộn {int(round(minutes))} phút"
            if shift_start:
                detail += f" · Ca bắt đầu {shift_start}"
            if checkin_time:
                detail += f" · Check-in {checkin_time}"
            ok, msg = save_auto_violation(
                client, work_date, employee, reason_item, detail, "AUTO UPDATE 24/7 - TIMESOFT"
            )
            if ok and msg == "SKIP_DUPLICATE":
                result["skipped"] += 1
            elif ok:
                result["added"] += 1
                _log(f"AUTO TIMESOFT ADDED: {employee} · {minutes:.0f} phút · {work_date}")
            else:
                result["errors"] += 1
                _log(f"AUTO TIMESOFT ERROR: {employee}: {msg}")
    return result


# ==========================================================
# POSTGRES SNAPSHOT
# ==========================================================
def _key(prefix: str, target_date: date) -> str:
    return f"{prefix}_{target_date.strftime('%Y%m%d')}"


def write_snapshot(target_date: date, invoice_df: pd.DataFrame, invoice_meta: dict,
                   checkin_df: pd.DataFrame, checkin_meta: dict) -> None:
    source_version = target_date.isoformat()
    vpg.write_dataset(_key("timesoft_summary_invoice", target_date), invoice_df, ttl_seconds=86400, source_version=source_version)
    vpg.write_dataset(_key("timesoft_summary_totals", target_date), pd.DataFrame([invoice_meta]), ttl_seconds=86400, source_version=source_version)
    vpg.write_dataset(_key("timesoft_employee_checkin", target_date), checkin_df, ttl_seconds=86400, source_version=source_version)
    if target_date == datetime.now(VN_TZ).date():
        vpg.write_dataset("timesoft_summary_invoice_today", invoice_df, ttl_seconds=1800, source_version=source_version)
        vpg.write_dataset("timesoft_summary_totals_today", pd.DataFrame([invoice_meta]), ttl_seconds=1800, source_version=source_version)
        vpg.write_dataset("timesoft_employee_checkin_today", checkin_df, ttl_seconds=1800, source_version=source_version)


def write_status(status: str, started_at: datetime, details: list[dict], error: str = "",
                 auto_status: str = "", tour_result: dict | None = None, timesoft_result: dict | None = None) -> None:
    now = datetime.now(VN_TZ)
    today_detail = next((x for x in details if x.get("date") == now.date().isoformat()), {})
    tour_result = tour_result or {}
    timesoft_result = timesoft_result or {}
    row = {
        "status": status,
        "synced_at": now.isoformat(),
        "synced_at_vn": now.strftime("%d/%m/%Y %H:%M:%S"),
        "started_at": started_at.isoformat(),
        "duration_seconds": round((now - started_at).total_seconds(), 3),
        "days_synced": len(details),
        "invoice_rows": int(today_detail.get("invoice_rows", 0) or 0),
        "checkin_rows": int(today_detail.get("checkin_rows", 0) or 0),
        "total_money": float(today_detail.get("total_money", 0) or 0),
        "total_discount": float(today_detail.get("total_discount", 0) or 0),
        "total_actual_revenue": float(today_detail.get("total_actual_revenue", 0) or 0),
        "auto_penalty_status": str(auto_status or ""),
        "auto_tour_added": int(tour_result.get("added", 0) or 0),
        "auto_timesoft_added": int(timesoft_result.get("added", 0) or 0),
        "auto_tour_errors": int(tour_result.get("errors", 0) or 0),
        "auto_timesoft_errors": int(timesoft_result.get("errors", 0) or 0),
        "error": str(error or "")[:500],
    }
    vpg.write_dataset("timesoft_background_status", pd.DataFrame([row]), ttl_seconds=1800, source_version=now.isoformat())


# ==========================================================
# MAIN JOB
# ==========================================================
def run_sync() -> int:
    started_at = datetime.now(VN_TZ)
    _log(f"Bắt đầu TimeSoft background sync V84; days={SYNC_DAYS}")
    if not vpg.is_enabled():
        _log("ERROR: PostgreSQL chưa được bật.")
        return 2

    engine = vpg.get_engine()
    lock_conn = engine.connect()
    got_lock = False
    details: list[dict] = []
    checkin_by_date: list[tuple[date, pd.DataFrame]] = []
    tour_result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    timesoft_result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    auto_status = "UNKNOWN"

    try:
        got_lock = bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": LOCK_NAME}).scalar())
        if not got_lock:
            _log("Một lần đồng bộ TimeSoft khác đang chạy; bỏ qua lần này.")
            return 0

        session = create_authenticated_session()
        today = datetime.now(VN_TZ).date()
        dates = [today - timedelta(days=i) for i in range(SYNC_DAYS)]
        for target_date in dates:
            invoice_df, invoice_meta = fetch_summary(session, target_date)
            checkin_df, checkin_meta = fetch_checkin(session, target_date)
            write_snapshot(target_date, invoice_df, invoice_meta, checkin_df, checkin_meta)
            checkin_by_date.append((target_date, checkin_df))
            detail = {
                "date": target_date.isoformat(),
                "invoice_rows": len(invoice_df),
                "checkin_rows": len(checkin_df),
                "checkin_total": int(checkin_meta.get("Total") or len(checkin_df)),
                "total_money": float(invoice_meta.get("TotalMoney") or 0),
                "total_discount": float(invoice_meta.get("TotalDiscount") or 0),
                "total_actual_revenue": float(invoice_meta.get("TotalActualRevenu") or 0),
            }
            details.append(detail)
            _log(f"Đã đồng bộ {target_date.isoformat()}: invoice_rows={len(invoice_df)}; checkin_rows={len(checkin_df)}")

        # Auto penalty chỉ chạy sau khi snapshot thành công.
        try:
            client = get_gspread_client()
            cfg = load_auto_penalty_config(client)
            auto_status = str(cfg.get("status", "UNKNOWN"))
            _log(f"Auto penalty status={auto_status}; threshold={cfg.get('threshold_minutes', AUTO_PENALTY_MINUTES)} phút")
            if cfg.get("paused"):
                _log("Auto penalty PAUSED bởi Admin -> chỉ đồng bộ snapshot, KHÔNG ghi phạt.")
            else:
                employee_map = load_employee_name_map(client)
                catalog = load_leave_catalog(client)
                _log(f"Đã tải danh mục: employees={len(employee_map)}; leave_types={len(catalog)}")
                timesoft_result = process_timesoft_penalties(client, cfg, employee_map, catalog, checkin_by_date)
                tour_result = process_tour_penalties(client, cfg, employee_map, catalog)
                _log(
                    "Auto penalty hoàn tất: "
                    f"TimeSoft eligible={timesoft_result['eligible']} added={timesoft_result['added']} skipped={timesoft_result['skipped']} errors={timesoft_result['errors']}; "
                    f"Tour eligible={tour_result['eligible']} added={tour_result['added']} skipped={tour_result['skipped']} errors={tour_result['errors']}"
                )
        except Exception as auto_exc:
            # Không làm mất snapshot TimeSoft nếu riêng phần Google/Auto penalty lỗi.
            auto_status = "ERROR"
            _log(f"AUTO PENALTY ERROR: {type(auto_exc).__name__}: {auto_exc}")
            tour_result["errors"] = int(tour_result.get("errors", 0)) + 1

        write_status(
            "success", started_at, details,
            auto_status=auto_status,
            tour_result=tour_result,
            timesoft_result=timesoft_result,
        )
        _log(f"Hoàn tất Job V84 trong {(datetime.now(VN_TZ)-started_at).total_seconds():.1f}s")
        return 0
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: {exc}"
        _log(f"ERROR: {safe_error}")
        try:
            write_status(
                "error", started_at, details, safe_error,
                auto_status=auto_status,
                tour_result=tour_result,
                timesoft_result=timesoft_result,
            )
        except Exception as status_exc:
            _log(f"Không ghi được sync status: {type(status_exc).__name__}")
        return 1
    finally:
        if got_lock:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": LOCK_NAME})
            except Exception:
                pass
        try:
            lock_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(run_sync())
