"""One-time migration for Vera Spa leave Sheet1 -> canonical A:L schema.

Target schema:
A Ngày
B Thứ ngày
C Tên nhân viên
D Lý do nghỉ
E Loại nghỉ
F Chi tiết
G Số ngày tính
H Số ngày phép cộng dồn
I Phạt vi phạm
J Ngày cập nhật
K Giờ cập nhật
L Người cập nhật

Supported source schemas:
- V93.1 A:M with blank physical column E.
- V86.x A:K with Loại nghỉ but without Thứ ngày.
- Legacy A:J without Thứ ngày/Loại nghỉ; Loại nghỉ is derived from worksheet LoaiNghi.

Safety:
- Does nothing unless --apply is passed.
- Creates a backup worksheet before changing Sheet1.
- Never reads or writes the retired Google Sheet 1bLxn....
"""
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
VN_TZ = timezone(timedelta(hours=7))

TARGET_HEADERS = [
    "Ngày", "Thứ ngày", "Tên nhân viên", "Lý do nghỉ", "Loại nghỉ",
    "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
    "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật",
]
LEGACY_AM_HEADERS = [
    "Ngày", "Thứ ngày", "Tên nhân viên", "Lý do nghỉ", "", "Loại nghỉ",
    "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
    "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật",
]
LEGACY_AK_HEADERS = [
    "Ngày", "Tên nhân viên", "Lý do nghỉ", "Loại nghỉ", "Chi tiết",
    "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
    "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật",
]
LEGACY_AJ_HEADERS = [
    "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
    "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
    "Giờ cập nhật", "Người cập nhật",
]


def _norm(value) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.replace("đ", "d").replace("Đ", "D").casefold().strip().split())


def _clean_header(row, n):
    vals = list(row[:n]) + [""] * max(0, n - len(row))
    return [str(x).strip() for x in vals]


def _parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
        except Exception:
            return None
    return None


def _weekday_vi(value) -> str:
    d = _parse_date(value)
    if d is None:
        return ""
    return {
        0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm",
        4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật",
    }[d.weekday()]


def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    env_json = str(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if env_json:
        creds = Credentials.from_service_account_info(json.loads(env_json), scopes=scopes)
        return gspread.authorize(creds)
    import google.auth
    creds, _ = google.auth.default(scopes=scopes)
    return gspread.authorize(creds)


def load_reason_type_map(ss):
    try:
        ws = ss.worksheet("LoaiNghi")
        values = ws.get_all_values()
    except Exception:
        return {}
    out = {}
    for row in values[1:]:
        reason = str(row[1] if len(row) > 1 else "").strip()
        leave_type = str(row[2] if len(row) > 2 else "").strip()
        if reason:
            out[_norm(reason)] = leave_type
    return out


def detect_schema(values):
    header = values[0] if values else []
    if _clean_header(header, 12) == TARGET_HEADERS:
        return "AL"
    if _clean_header(header, 13) == LEGACY_AM_HEADERS:
        return "AM"
    if _clean_header(header, 11) == LEGACY_AK_HEADERS:
        return "AK"
    if _clean_header(header, 10) == LEGACY_AJ_HEADERS:
        return "AJ"
    return "UNKNOWN"


def convert_rows(values, schema, reason_type_map):
    converted = []
    missing_type = []
    for sheet_row, raw in enumerate(values[1:], start=2):
        if not any(str(v).strip() for v in raw):
            continue
        if schema == "AM":
            v = list(raw[:13]) + [""] * max(0, 13 - len(raw))
            out = [v[0], v[1] or _weekday_vi(v[0]), v[2], v[3], v[5], v[6], v[7], v[8], v[9], v[10], v[11], v[12]]
        elif schema == "AK":
            v = list(raw[:11]) + [""] * max(0, 11 - len(raw))
            out = [v[0], _weekday_vi(v[0]), v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10]]
        elif schema == "AJ":
            v = list(raw[:10]) + [""] * max(0, 10 - len(raw))
            leave_type = reason_type_map.get(_norm(v[2]), "")
            if not leave_type and str(v[2]).strip():
                missing_type.append((sheet_row, str(v[2]).strip()))
            out = [v[0], _weekday_vi(v[0]), v[1], v[2], leave_type, v[3], v[4], v[5], v[6], v[7], v[8], v[9]]
        else:
            raise ValueError(f"Schema nguồn không hỗ trợ: {schema}")
        converted.append(out)
    return converted, missing_type


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Thực hiện migration. Nếu bỏ qua, chỉ kiểm tra/preview.")
    args = ap.parse_args()

    client = get_client()
    ss = client.open_by_key(SHEET_DU_PHONG_ID)
    ws = ss.get_worksheet(0)
    values = ws.get("A:M")
    if not values:
        raise RuntimeError("Sheet1 đang rỗng; không có dữ liệu để migrate.")

    schema = detect_schema(values)
    print(f"Detected schema: {schema}")
    if schema == "AL":
        print("Sheet1 đã đúng A:L. Không cần migration.")
        return
    if schema == "UNKNOWN":
        print("Header hiện tại:", values[0])
        raise RuntimeError("Không nhận diện được schema. Không thay đổi dữ liệu.")

    reason_type_map = load_reason_type_map(ss)
    converted, missing_type = convert_rows(values, schema, reason_type_map)
    print(f"Rows sẽ migrate: {len(converted)}")
    if missing_type:
        print(f"Cảnh báo: {len(missing_type)} dòng A:J cũ không tìm thấy Loại nghỉ trong LoaiNghi.")
        for row_num, reason in missing_type[:20]:
            print(f"  row {row_num}: {reason}")

    if not args.apply:
        print("PREVIEW ONLY. Chạy lại với --apply để thực hiện. Không có dữ liệu nào bị thay đổi.")
        return

    stamp = datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
    backup_name = f"Sheet1_BACKUP_{stamp}"
    try:
        ss.duplicate_sheet(ws.id, new_sheet_name=backup_name)
    except Exception:
        backup = ss.add_worksheet(title=backup_name, rows=max(1000, len(values) + 50), cols=13)
        backup.update(f"A1:M{len(values)}", values, value_input_option="RAW")
    print(f"Backup created: {backup_name}")

    ws.batch_clear(["A:M"])
    payload = [TARGET_HEADERS] + converted
    ws.update(f"A1:L{len(payload)}", payload, value_input_option="USER_ENTERED")

    verify = ws.get("A:L")
    if not verify or _clean_header(verify[0], 12) != TARGET_HEADERS:
        raise RuntimeError(f"Migration verify header thất bại. Backup còn tại {backup_name}.")
    actual_rows = sum(1 for row in verify[1:] if any(str(v).strip() for v in row))
    if actual_rows != len(converted):
        raise RuntimeError(
            f"Migration verify row count thất bại: expected={len(converted)}, actual={actual_rows}. "
            f"Backup còn tại {backup_name}."
        )

    print(f"DONE: Sheet1 -> A:L, {actual_rows} dòng. Backup: {backup_name}")


if __name__ == "__main__":
    main()
