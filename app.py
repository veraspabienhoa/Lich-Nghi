import streamlit as st
import pandas as pd
from datetime import date, timedelta, datetime, timezone
import calendar
import requests
import os
import io
import gspread
from google.oauth2.service_account import Credentials
import streamlit.components.v1 as components
import time
import smtplib
import unicodedata
import hashlib
import secrets
import hmac
import json
import zipfile
import re
import numbers
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# --- CẤU HÌNH MÚI GIỜ VIỆT NAM ---
VN_TZ = timezone(timedelta(hours=7))

def get_vn_today():
    return datetime.now(VN_TZ).date()

# --- CHUẨN HÓA TÊN / TÀI KHOẢN ---
def normalize_name(name):
    """Đồng nhất cách gõ Thúy/Thuý để tránh lỗi so sánh dữ liệu nghiệp vụ."""
    return str(name).replace("Thuý", "Thúy").replace("thuý", "thúy").strip()

def remove_vietnamese_accents(value):
    """Bỏ dấu tiếng Việt nhưng vẫn giữ nguyên số 0 đầu chuỗi và ký tự khác."""
    text = unicodedata.normalize("NFD", str(value))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")

def normalize_login_name(value):
    """Tên đăng nhập: không phân biệt dấu, HOA/thường; không ép kiểu số."""
    return " ".join(remove_vietnamese_accents(str(value)).strip().split()).casefold()

def password_matches(input_password, stored_password):
    """Mật khẩu được so sánh đúng ký tự, có phân biệt HOA/thường và chấp nhận ký tự đặc biệt/0 đầu."""
    return hmac.compare_digest(str(input_password), str(stored_password))

def is_locked_value(value):
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "khóa", "khoa", "locked", "x"}

def normalize_leave_reason(value):
    """Chuẩn hóa loại nghỉ để so sánh trùng dữ liệu ổn định."""
    text = str(value).replace("🔴", "").strip()
    return " ".join(text.split()).casefold()

PROGRESSIVE_PENALTY_REASONS = {
    normalize_login_name("Nghỉ không phép"): "Nghỉ không phép",
    normalize_login_name("Đi trễ không phép"): "Đi trễ không phép",
    normalize_login_name("Về sớm không phép"): "Về sớm không phép",
    # Chấp nhận tên cũ/biến thể nếu danh mục đang dùng "Ra sớm không phép".
    normalize_login_name("Ra sớm không phép"): "Về sớm không phép",
}

def get_progressive_penalty_reason(value):
    """Trả về tên chuẩn nếu loại nghỉ thuộc 3 nhóm phạt lũy tiến, ngược lại trả về None."""
    key = normalize_login_name(str(value).replace("🔴", "").strip())
    return PROGRESSIVE_PENALTY_REASONS.get(key)

def is_progressive_penalty_reason(value):
    return get_progressive_penalty_reason(value) is not None

def is_nghi_khong_phep_reason(value):
    """Giữ tương thích với code cũ: chỉ kiểm tra riêng Nghỉ không phép."""
    return get_progressive_penalty_reason(value) == "Nghỉ không phép"

def _fallback_admin_remember_token():
    """Token bền vững cho tài khoản admin dự phòng; không lưu mật khẩu trong trình duyệt."""
    try:
        secret = str(st.secrets.get("vera_persistent_login_secret", "VERA-SPA-PERSISTENT-LOGIN-2026"))
    except Exception:
        secret = "VERA-SPA-PERSISTENT-LOGIN-2026"
    digest = hmac.new(secret.encode("utf-8"), b"fallback-admin", hashlib.sha256).hexdigest()
    return "vera_admin_" + digest

def _is_valid_fallback_admin_token(token):
    if not token:
        return False
    return hmac.compare_digest(str(token), _fallback_admin_remember_token())

# --- DANH SÁCH NGÂN HÀNG VIỆT NAM (VietQR, tự làm mới mỗi 24 giờ) ---
VIETQR_BANKS_API = "https://api.vietqr.io/v2/banks"

# Danh sách dự phòng khi API bên ngoài tạm thời không truy cập được.
FALLBACK_VN_BANKS = [
    ("Vietcombank", "Ngân hàng TMCP Ngoại thương Việt Nam"),
    ("VietinBank", "Ngân hàng TMCP Công thương Việt Nam"),
    ("BIDV", "Ngân hàng TMCP Đầu tư và Phát triển Việt Nam"),
    ("Agribank", "Ngân hàng Nông nghiệp và Phát triển Nông thôn Việt Nam"),
    ("MBBank", "Ngân hàng TMCP Quân đội"),
    ("Techcombank", "Ngân hàng TMCP Kỹ thương Việt Nam"),
    ("ACB", "Ngân hàng TMCP Á Châu"),
    ("VPBank", "Ngân hàng TMCP Việt Nam Thịnh Vượng"),
    ("TPBank", "Ngân hàng TMCP Tiên Phong"),
    ("Sacombank", "Ngân hàng TMCP Sài Gòn Thương Tín"),
    ("HDBank", "Ngân hàng TMCP Phát triển Thành phố Hồ Chí Minh"),
    ("VIB", "Ngân hàng TMCP Quốc tế Việt Nam"),
    ("SHB", "Ngân hàng TMCP Sài Gòn - Hà Nội"),
    ("Eximbank", "Ngân hàng TMCP Xuất Nhập khẩu Việt Nam"),
    ("MSB", "Ngân hàng TMCP Hàng Hải Việt Nam"),
    ("OCB", "Ngân hàng TMCP Phương Đông"),
    ("PVcomBank", "Ngân hàng TMCP Đại Chúng Việt Nam"),
    ("LPBank", "Ngân hàng TMCP Lộc Phát Việt Nam"),
    ("SeABank", "Ngân hàng TMCP Đông Nam Á"),
    ("ABBANK", "Ngân hàng TMCP An Bình"),
    ("BacABank", "Ngân hàng TMCP Bắc Á"),
    ("NamABank", "Ngân hàng TMCP Nam Á"),
    ("NCB", "Ngân hàng TMCP Quốc Dân"),
    ("VietABank", "Ngân hàng TMCP Việt Á"),
    ("VietBank", "Ngân hàng TMCP Việt Nam Thương Tín"),
    ("BaoVietBank", "Ngân hàng TMCP Bảo Việt"),
    ("KienLongBank", "Ngân hàng TMCP Kiên Long"),
    ("PGBank", "Ngân hàng TMCP Thịnh vượng và Phát triển"),
    ("SaigonBank", "Ngân hàng TMCP Sài Gòn Công Thương"),
    ("SCB", "Ngân hàng TMCP Sài Gòn"),
    ("COOPBANK", "Ngân hàng Hợp tác xã Việt Nam"),
    ("ShinhanBank", "Ngân hàng TNHH MTV Shinhan Việt Nam"),
    ("Woori", "Ngân hàng TNHH MTV Woori Việt Nam"),
    ("HSBC", "Ngân hàng TNHH MTV HSBC (Việt Nam)"),
    ("StandardChartered", "Ngân hàng TNHH MTV Standard Chartered Bank Việt Nam"),
    ("PublicBank", "Ngân hàng TNHH MTV Public Việt Nam"),
    ("CIMB", "Ngân hàng TNHH MTV CIMB Việt Nam"),
    ("HongLeong", "Ngân hàng TNHH MTV Hong Leong Việt Nam"),
    ("MBV", "Ngân hàng TNHH MTV Việt Nam Hiện Đại"),
    ("Vikki", "Ngân hàng TNHH MTV Số Vikki"),
    ("GPBank", "Ngân hàng Thương mại TNHH MTV Dầu Khí Toàn Cầu"),
    ("CBBank", "Ngân hàng Thương mại TNHH MTV Xây dựng Việt Nam"),
    ("VRB", "Ngân hàng Liên doanh Việt - Nga"),
    ("IndovinaBank", "Ngân hàng TNHH Indovina"),
    ("KBank", "Ngân hàng Đại chúng TNHH Kasikornbank"),
    ("VBSP", "Ngân hàng Chính sách Xã hội"),
]

@st.cache_data(ttl=86400, show_spinner=False)
def load_vietnam_banks():
    """Lấy danh sách ngân hàng từ VietQR; trả về list dict gồm label/value."""
    try:
        r = requests.get(VIETQR_BANKS_API, timeout=12)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        banks = []
        seen = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("name", "")).replace("\n", " ").strip()
            short_name = str(item.get("shortName", item.get("short_name", ""))).strip()
            code = str(item.get("code", "")).strip()
            if not full_name:
                continue
            # Chỉ giữ ngân hàng / chi nhánh ngân hàng / ngân hàng số, loại ví & công ty tài chính.
            name_fold = remove_vietnamese_accents(full_name).casefold()
            if not ("ngan hang" in name_fold or "bank" in name_fold):
                continue
            unique_key = (remove_vietnamese_accents(full_name).casefold(), short_name.casefold())
            if unique_key in seen:
                continue
            seen.add(unique_key)
            short_display = short_name or code
            label = f"{short_display} — {full_name}" if short_display else full_name
            banks.append({"label": label, "value": full_name, "short": short_display})
        if banks:
            return sorted(banks, key=lambda x: remove_vietnamese_accents(x["label"]).casefold())
    except Exception:
        pass

    return [
        {"label": f"{short} — {name}", "value": name, "short": short}
        for short, name in FALLBACK_VN_BANKS
    ]

def bank_selectbox(label, key, current_value=""):
    """Dropdown ngân hàng có ô gõ tìm kiếm tích hợp của Streamlit."""
    banks = load_vietnam_banks()
    current = str(current_value or "").strip()

    # Nếu dữ liệu cũ chưa khớp tên mới từ API, vẫn giữ làm lựa chọn đầu tiên.
    if current and not any(normalize_login_name(x["value"]) == normalize_login_name(current) for x in banks):
        banks = [{"label": f"{current} (đang lưu)", "value": current, "short": ""}] + banks

    placeholder = "-- Chọn ngân hàng --"
    labels = [placeholder] + [x["label"] for x in banks]
    label_to_value = {x["label"]: x["value"] for x in banks}
    index = 0
    if current:
        for i, item in enumerate(banks, start=1):
            if normalize_login_name(item["value"]) == normalize_login_name(current) or normalize_login_name(item.get("short", "")) == normalize_login_name(current):
                index = i
                break

    selected = st.selectbox(
        label,
        labels,
        index=index,
        key=key,
        filter_mode="contains",
        placeholder="Gõ tên hoặc tên viết tắt ngân hàng để tìm...",
        help="Mở danh sách rồi gõ tên ngân hàng hoặc tên viết tắt, ví dụ: VCB, Vietcombank, ACB, MB..."
    )
    return "" if selected == placeholder else label_to_value.get(selected, selected)

# --- ĐỊA CHỈ HÀNH CHÍNH VIỆT NAM (SAU SÁP NHẬP 07/2025: 34 TỈNH/THÀNH, 2 CẤP) ---
VN_ADMIN_API_V2 = "https://provinces.open-api.vn/api/v2/"
FALLBACK_VN_PROVINCES_2025 = [
    "An Giang", "Bắc Ninh", "Cà Mau", "Cần Thơ", "Cao Bằng", "Đà Nẵng",
    "Đắk Lắk", "Điện Biên", "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Nội",
    "Hà Tĩnh", "Hải Phòng", "Hồ Chí Minh", "Huế", "Hưng Yên", "Khánh Hòa",
    "Lai Châu", "Lâm Đồng", "Lạng Sơn", "Lào Cai", "Nghệ An", "Ninh Bình",
    "Phú Thọ", "Quảng Ngãi", "Quảng Ninh", "Quảng Trị", "Sơn La", "Tây Ninh",
    "Thái Nguyên", "Thanh Hóa", "Tuyên Quang", "Vĩnh Long"
]

@st.cache_data(ttl=604800, show_spinner=False)
def load_vietnam_admin_divisions():
    """
    Lấy dữ liệu hành chính Việt Nam sau 01/07/2025 từ Province Open API v2.
    Trả về list: [{code, name, wards:[{code,name}]}]. Cache 7 ngày để không gọi API liên tục.
    """
    try:
        r = requests.get(VN_ADMIN_API_V2, params={"depth": 2}, timeout=15)
        r.raise_for_status()
        payload = r.json()
        result = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                p_name = str(item.get("name", "")).strip()
                p_name = re.sub(r"^(Tỉnh|Thành phố)\s+", "", p_name, flags=re.IGNORECASE).strip()
                p_code = item.get("code", "")
                wards_raw = item.get("wards") or item.get("communes") or item.get("children") or []
                wards = []
                if isinstance(wards_raw, list):
                    for w in wards_raw:
                        if isinstance(w, dict) and str(w.get("name", "")).strip():
                            wards.append({"code": w.get("code", ""), "name": str(w.get("name", "")).strip()})
                if p_name:
                    result.append({"code": p_code, "name": p_name, "wards": wards})
        if result:
            return sorted(result, key=lambda x: remove_vietnamese_accents(x["name"]).casefold()), ""
    except Exception as e:
        return ([{"code": "", "name": x, "wards": []} for x in FALLBACK_VN_PROVINCES_2025],
                f"Không tải được danh mục Phường/Xã trực tuyến: {e}")
    return ([{"code": "", "name": x, "wards": []} for x in FALLBACK_VN_PROVINCES_2025],
            "Không nhận được dữ liệu hành chính trực tuyến.")

def _address_component_match(text, candidate):
    t = normalize_login_name(text)
    c = normalize_login_name(candidate)
    return bool(c and c in t)

def parse_combined_vietnam_address(address, divisions=None):
    """Tách gần đúng địa chỉ cũ thành địa chỉ chi tiết + Phường/Xã + Tỉnh/Thành."""
    raw = str(address or "").strip()
    if not raw:
        return "", "", ""
    divisions = divisions or load_vietnam_admin_divisions()[0]
    province = ""
    ward = ""
    province_obj = None
    # Ưu tiên tên dài để tránh trùng một phần.
    for p in sorted(divisions, key=lambda x: len(str(x.get("name", ""))), reverse=True):
        if _address_component_match(raw, p.get("name", "")):
            province = str(p.get("name", "")).strip()
            province_obj = p
            break
    if province_obj:
        for w in sorted(province_obj.get("wards", []), key=lambda x: len(str(x.get("name", ""))), reverse=True):
            if _address_component_match(raw, w.get("name", "")):
                ward = str(w.get("name", "")).strip()
                break
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    detail_parts = []
    for part in parts:
        n = normalize_login_name(part)
        if province and n == normalize_login_name(province):
            continue
        if ward and n == normalize_login_name(ward):
            continue
        detail_parts.append(part)
    detail = ", ".join(detail_parts).strip()
    if not detail and raw and not province and not ward:
        detail = raw
    return detail, ward, province

def combine_vietnam_address(detail, ward, province):
    parts = [str(x).strip().strip(",") for x in (detail, ward, province) if str(x).strip().strip(",")]
    # Loại trùng nếu người dùng gõ lại tên Phường/Xã/Tỉnh trong ô chi tiết.
    out = []
    seen = set()
    for x in parts:
        k = normalize_login_name(x)
        if k not in seen:
            out.append(x)
            seen.add(k)
    return ", ".join(out)

def vietnam_address_inputs(prefix, current_address="", show_preview=True):
    """
    Render 3 box: Tỉnh/Thành phố -> Phường/Xã -> Địa chỉ chi tiết.
    Không đặt trong st.form vì Phường/Xã phải đổi ngay khi Tỉnh/Thành thay đổi.
    Kết quả trả về là CHUỖI ĐÃ GHÉP để lưu vào đúng 1 cột Địa chỉ.
    """
    divisions, api_err = load_vietnam_admin_divisions()
    parsed_detail, parsed_ward, parsed_province = parse_combined_vietnam_address(current_address, divisions)
    province_names = [str(p.get("name", "")).strip() for p in divisions if str(p.get("name", "")).strip()]
    province_options = [""] + province_names
    p_key = f"{prefix}_province"
    w_key = f"{prefix}_ward"
    d_key = f"{prefix}_detail"
    manual_w_key = f"{prefix}_ward_manual"

    if p_key not in st.session_state:
        st.session_state[p_key] = parsed_province if parsed_province in province_names else ""
    if d_key not in st.session_state:
        st.session_state[d_key] = parsed_detail

    province = st.selectbox(
        "Tỉnh/Thành phố", province_options, key=p_key, filter_mode="contains",
        placeholder="Gõ để tìm Tỉnh/Thành phố..."
    )
    province_obj = next((p for p in divisions if str(p.get("name", "")).strip() == province), None)
    ward_names = [str(w.get("name", "")).strip() for w in (province_obj or {}).get("wards", []) if str(w.get("name", "")).strip()]

    if ward_names:
        ward_options = [""] + sorted(ward_names, key=lambda x: remove_vietnamese_accents(x).casefold())
        existing_ward = st.session_state.get(w_key, "")
        if existing_ward not in ward_options:
            st.session_state[w_key] = parsed_ward if parsed_ward in ward_options else ""
        ward = st.selectbox(
            "Phường/Xã", ward_options, key=w_key, filter_mode="contains",
            placeholder="Gõ để tìm Phường/Xã..."
        )
    else:
        # API tạm lỗi hoặc tỉnh chưa có danh mục: vẫn cho nhập tay để công việc không bị chặn.
        if manual_w_key not in st.session_state:
            st.session_state[manual_w_key] = parsed_ward
        ward = st.text_input("Phường/Xã", key=manual_w_key, placeholder="Nhập Phường/Xã")
        if api_err:
            st.caption("⚠️ Danh mục Phường/Xã trực tuyến đang tạm không khả dụng; có thể nhập tay.")

    detail = st.text_input(
        "Địa chỉ chi tiết", key=d_key,
        placeholder="Số nhà, tên đường, khu phố/thôn/ấp..."
    )
    combined = combine_vietnam_address(detail, ward, province)
    if show_preview:
        st.caption(f"📍 Địa chỉ sẽ lưu: {combined or '(chưa nhập)'}")
    return combined

def employee_registration_window(today=None):
    """Nhân viên được thao tác từ hôm nay đến hết tháng kế tiếp."""
    today = today or get_vn_today()
    if today.month == 12:
        next_month_first = date(today.year + 1, 1, 1)
    else:
        next_month_first = date(today.year, today.month + 1, 1)
    max_date = next_month_first.replace(day=calendar.monthrange(next_month_first.year, next_month_first.month)[1])
    return today, max_date

def normalize_schedule_date(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    try:
        parsed = pd.to_datetime(str(value).strip(), dayfirst=True, errors='raise')
        return parsed.strftime('%d/%m/%Y')
    except Exception:
        return str(value).strip()

def schedule_key(row):
    reason_col = 'Lý do nghỉ' if 'Lý do nghỉ' in row else 'Loại nghỉ'
    return (
        normalize_schedule_date(row.get('Ngày', '')),
        normalize_login_name(row.get('Tên nhân viên', '')),
        remove_vietnamese_accents(str(row.get(reason_col, '')).strip()).casefold(),
    )

# --- HÀM ĐỊNH DẠNG BẢNG HIỂN THỊ TRỰC QUAN ---
def format_display_df(df):
    d = df.copy()
    def fmt_num(val):
        if pd.isna(val) or val == "": return ""
        try:
            v = float(val)
            if v == 0: return ""
            return str(int(v)) if v.is_integer() else str(v)
        except: return str(val)
    
    for col in ['Số ngày tính', 'Số ngày phép cộng dồn']:
        if col in d.columns:
            d[col] = d[col].apply(fmt_num)
            
    if 'Ngày' in d.columns:
        d['Ngày'] = pd.to_datetime(d['Ngày'], errors='coerce').dt.strftime('%d/%m/%Y').fillna(d['Ngày'])
        
    return d

# --- HÀM GỬI EMAIL BÁO CÁO ---
def send_email_report(sender_email, sender_password, to_email, emp_name, df_emp, total_phat, start_str, end_str):
    try:
        subject = f"Báo cáo chi tiết lịch nghỉ và vi phạm - {emp_name} ({start_str} đến {end_str})"
        
        # Định dạng lại bảng để hiển thị đẹp trong email
        df_display = format_display_df(df_emp[['Ngày', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Phạt vi phạm']])
        df_display['Phạt vi phạm'] = df_display['Phạt vi phạm'].apply(lambda x: f"{float(x):,.0f}" if float(x) > 0 else "")
        
        # Thêm style CSS cho bảng HTML
        html_table = df_display.to_html(index=False, justify='center')
        html_table = html_table.replace('<table border="1" class="dataframe">', '<table style="width:100%; border-collapse: collapse; border: 1px solid #ddd; font-family: Arial, sans-serif;">')
        html_table = html_table.replace('<th>', '<th style="background-color: #f2f2f2; border: 1px solid #ddd; padding: 8px; text-align: center; white-space: normal; overflow-wrap: anywhere; word-break: break-word;">')
        html_table = html_table.replace('<td>', '<td style="border: 1px solid #ddd; padding: 8px; text-align: center;">')
        
        html_content = f"""
        <html>
        <body>
            <p>Chào <b>{emp_name}</b>,</p>
            <p>Hệ thống quản lý Vera Spa gửi bạn chi tiết lịch nghỉ và vi phạm trong giai đoạn từ <b>{start_str}</b> đến <b>{end_str}</b>:</p>
            <br>
            {html_table}
            <br>
            <h3 style="color: red;">Tổng tiền phạt vi phạm: {total_phat:,.0f} VNĐ</h3>
            <p><i>Vui lòng kiểm tra lại thông tin. Nếu có bất kỳ sai sót nào, xin vui lòng phản hồi lại trong thời gian sớm nhất.</i></p>
            <br>
            <p>Trân trọng,</p>
            <p><b>VERA SPA</b></p>
        </body>
        </html>
        """
        
        msg = MIMEMultipart()
        msg['From'] = f"Vera Spa <{sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_content, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        
        return True, "Thành công"
    except Exception as e:
        return False, str(e)


# --- THEO DÕI SỐ NGƯỜI ĐANG TRUY CẬP & TRẠNG THÁI HỆ THỐNG ---
@st.cache_resource
def get_active_users():
    return {}

@st.cache_resource
def get_system_status():
    return {"lock_nv": False}

active_users = get_active_users()
system_status = get_system_status()

# Cập nhật thời gian hoạt động của user hiện tại
if st.session_state.get("logged_in") and st.session_state.get("current_user"):
    active_users[st.session_state.current_user] = time.time()

# Dọn dẹp user đã ngưng hoạt động > 5 phút (300 giây)
current_t = time.time()
for u in list(active_users.keys()):
    if current_t - active_users[u] > 300: 
        del active_users[u]

online_users_count = len(active_users)
online_users_list = list(active_users.keys())

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Vera Spa", page_icon="📅", layout="wide", initial_sidebar_state="auto")

# --- JAVASCRIPT: NHỚ ĐĂNG NHẬP + ĐÓNG DROPDOWN KHI BẤM RA NGOÀI ---
components.html("""
<script>
(function () {
    try {
        const parentWin = window.parent;
        const parentDoc = parentWin.document;
        const url = new URL(parentWin.location.href);
        const STORAGE_KEY = 'vera_remember_token';

        // Khi server yêu cầu quên đăng nhập: xóa token khỏi trình duyệt.
        if (url.searchParams.get('forget_login') === '1') {
            parentWin.localStorage.removeItem(STORAGE_KEY);
            url.searchParams.delete('forget_login');
            url.searchParams.delete('remember_token');
            parentWin.history.replaceState({}, '', url.toString());
        } else {
            const tokenInUrl = url.searchParams.get('remember_token');
            const savedToken = parentWin.localStorage.getItem(STORAGE_KEY);

            // Token mới sau khi đăng nhập -> lưu trong localStorage (không lưu mật khẩu).
            if (tokenInUrl) {
                parentWin.localStorage.setItem(STORAGE_KEY, tokenInUrl);
                // Không để bearer token nằm lâu trên thanh địa chỉ sau khi đã lưu cục bộ.
                url.searchParams.delete('remember_token');
                parentWin.history.replaceState({}, '', url.toString());
            } else if (savedToken) {
                // Lần mở app sau: đưa token trở lại URL để server xác thực.
                url.searchParams.set('remember_token', savedToken);
                parentWin.location.replace(url.toString());
                return;
            }
        }

        parentDoc.addEventListener('keydown', function(event) {
            if ((event.key === 'c' || event.key === 'C')) {
                const tag = (event.target.tagName || '').toLowerCase();
                if (tag !== 'input' && tag !== 'textarea') event.stopPropagation();
            }
        }, true);

        // Bấm ra khoảng trống: blur ô select đang hoạt động để popover đóng lại.
        parentDoc.addEventListener('pointerdown', function(event) {
            const insideSelect = event.target.closest && event.target.closest('[data-baseweb="select"], [data-baseweb="popover"]');
            if (!insideSelect) {
                const active = parentDoc.activeElement;
                if (active && active.closest && active.closest('[data-baseweb="select"]')) active.blur();
            }
        }, true);
    } catch (e) {
        console.debug('Vera helper:', e);
    }
})();
</script>
""", height=0, width=0)


# --- ÉP CSS GIAO DIỆN CỐ ĐỊNH (TỐI ƯU HIỆU NĂNG) ---
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Arial:wght@400;700&display=swap');
        
        /* Cấu hình Giao diện toàn trang */
        html, body, [class*="st-"], .stMarkdown, .stText, div, span, p {
            font-family: 'Roboto', sans-serif !important;
            color: #333333 !important;
        }
        
        /* --- FIX LỖI MŨI TÊN (KHÔI PHỤC FONT ICON CỦA STREAMLIT) --- */
        span.material-symbols-rounded, 
        [data-testid="stIconMaterial"], 
        .stIcon, 
        span[class*="stIcon"] {
            font-family: "Material Symbols Rounded" !important;
        }
        
        p, .stText, [data-testid="stMarkdownContainer"] {
            font-size: 16px !important;
        }
        
        .block-container { padding-top: 0.85rem; padding-bottom: 0.75rem; max-width: 1500px; }
        div[data-testid="stVerticalBlock"] > div { gap: 0.12rem !important; }
        div.stButton, div[data-testid="stDownloadButton"], div[data-testid="stFormSubmitButton"] { margin: 0 !important; padding: 0 !important; }
        button { margin-top: 1px !important; min-height: 40px; padding-top: 0.32rem !important; padding-bottom: 0.32rem !important; transition: background-color .16s ease, color .16s ease, border-color .16s ease, transform .12s ease !important; }
        div.stButton > button:hover,
        div[data-testid="stDownloadButton"] > button:hover,
        div[data-testid="stFormSubmitButton"] > button:hover {
            background-color: #c27ba0 !important;
            color: #ffffff !important;
            border-color: #a85f86 !important;
            transform: translateY(-1px);
        }
        input, textarea { font-size: 16px !important; }
        [data-testid="stDataFrame"], [data-testid="stDataEditor"] { width: 100% !important; }

        /* --- WRAPTEXT TIÊU ĐỀ BẢNG ---
           Khi Admin thu hẹp cột, tiêu đề được phép xuống dòng thay vì bị cắt chữ. */
        table th,
        [data-testid="stTable"] th,
        [data-testid="stDataFrame"] [role="columnheader"],
        [data-testid="stDataEditor"] [role="columnheader"],
        [data-testid="stDataFrame"] [role="columnheader"] > div,
        [data-testid="stDataEditor"] [role="columnheader"] > div {
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
            text-overflow: clip !important;
            line-height: 1.15 !important;
            height: auto !important;
            min-height: 38px !important;
        }

        /* Các nhãn header DOM của Glide/Streamlit ở những phiên bản có expose header. */
        [data-testid="stDataFrame"] [class*="header"],
        [data-testid="stDataEditor"] [class*="header"] {
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
        }

        /* Hiệu ứng hover cho TOÀN BỘ dropdown/select/multiselect */
        div[data-baseweb="select"],
        [data-testid="stSelectbox"],
        [data-testid="stMultiSelect"] {
            transition: transform .14s ease !important;
        }
        div[data-baseweb="select"] > div,
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
            transition: background-color .16s ease, border-color .16s ease, box-shadow .16s ease !important;
        }
        div[data-baseweb="select"]:hover > div,
        [data-testid="stSelectbox"]:hover div[data-baseweb="select"] > div,
        [data-testid="stMultiSelect"]:hover div[data-baseweb="select"] > div {
            background-color: #f7e8ef !important;
            border-color: #c27ba0 !important;
            box-shadow: 0 0 0 1px #c27ba0 inset, 0 2px 8px rgba(194, 123, 160, .18) !important;
        }
        div[data-baseweb="select"]:hover svg,
        [data-testid="stSelectbox"]:hover svg,
        [data-testid="stMultiSelect"]:hover svg {
            fill: #a85f86 !important;
            color: #a85f86 !important;
        }
        div[data-baseweb="popover"] [role="option"],
        div[data-baseweb="menu"] [role="option"],
        ul[role="listbox"] li,
        ul[role="listbox"] [role="option"] {
            transition: background-color .14s ease, color .14s ease, padding-left .14s ease !important;
        }
        div[data-baseweb="popover"] [role="option"]:hover,
        div[data-baseweb="menu"] [role="option"]:hover,
        ul[role="listbox"] li:hover,
        ul[role="listbox"] [role="option"]:hover {
            background-color: #f3dce8 !important;
            color: #7d3159 !important;
            padding-left: 14px !important;
        }

        /* Tô nền các vị trí tiêu đề */
        h1, h2, h3 {
            background: #f7e8ef !important;
            border-left: 5px solid #c27ba0 !important;
            border-radius: 7px !important;
            padding: 0.38rem 0.65rem !important;
            margin-top: 0.28rem !important;
            margin-bottom: 0.4rem !important;
        }
        .custom-main-title {
            background: #f7e8ef !important;
            border-left: 5px solid #c27ba0 !important;
            border-radius: 7px !important;
            padding: 0.45rem 0.7rem !important;
        }

        @media (max-width: 768px) {
            .block-container {
                padding-top: 0.6rem !important;
                padding-left: 0.45rem !important;
                padding-right: 0.45rem !important;
            }
            .custom-main-title { font-size: 24px !important; line-height: 1.25 !important; margin-bottom: 8px !important; }
            .custom-main-title > div { float: none !important; text-align: left !important; margin-top: 6px !important; }
            p, .stText, [data-testid="stMarkdownContainer"] { font-size: 15px !important; }
            div[data-testid="stVerticalBlock"] > div { gap: 0.08rem !important; }
            button { min-height: 42px !important; font-size: 15px !important; margin-top: 0 !important; padding-top: 0.25rem !important; padding-bottom: 0.25rem !important; }
            div[data-baseweb="popover"] { max-width: calc(100vw - 12px) !important; }
            [data-testid="stDataFrame"], [data-testid="stDataEditor"] { font-size: 13px !important; }
            [data-testid="stTabs"] button { white-space: nowrap !important; }
        }
        
        /* Loại bỏ thanh cuộn dropdown */
        div[data-baseweb="popover"] > div,
        div[data-baseweb="select"] ul[role="listbox"],
        div[data-testid="stSelectboxVirtualDropdown"] {
            max-height: 85vh !important; 
        }
        
        .custom-main-title {
            font-family: 'Roboto', sans-serif !important;
            font-size: 35px; font-weight: bold; margin-bottom: 20px; color: #333 !important;
        }
        
        /* GIẢM SIZE CHỮ: ĐĂNG KÝ - THAY ĐỔI LỊCH NGHỈ */
        [data-testid="stExpander"] details summary p {
            font-size: 1.3rem !important;
            font-weight: 700 !important;
            color: #d32f2f !important;
            text-transform: uppercase;
        }
    </style>
""", unsafe_allow_html=True)

# --- KẾT NỐI GSPREAD ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
SHEET_LICH_NGHI_2_ID = "1bLxn-L5gXui8pCL1b9TxshCNcykM7jg0J49Dkr5b4DI"
SHEET_CHINH_ID = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
BANG_TOUR_FILE_ID = "1yA1Oog_6R-HmDFatcku-x8s-59p2dP9R"
PAYROLL_SOURCE_SHEET_ID = "1WtYsbEAlifL1PZ-nSGBojgL4Bnur-1vF"
PAYROLL_SOURCE_WORKSHEET = "Báo cáo doanh thu hóa đơn"
PAYROLL_STORAGE_WORKSHEET = "BangLuong"
PAYROLL_CONFIG_WORKSHEET = "CauHinhLuong"
UI_LAYOUT_WORKSHEET = "CauHinhCot"
TICHLUY_WORKSHEET = "TichLuy"
VIOLATION_DEBT_WORKSHEET = "NoViPham"
VIOLATION_DEBT_HEADERS = [
    "STT", "Tên nhân viên", "Số tiền", "Nội dung", "Loại",
    "Kỳ phát sinh từ", "Kỳ phát sinh đến", "Bắt đầu trừ từ",
    "Trạng thái", "Mã nguồn", "Ngày cập nhật", "Giờ cập nhật",
    "Người cập nhật", "Kỳ đã khấu trừ"
]
VIOLATION_DEBT_OPEN_STATUS = "Chưa hoàn thành"
VIOLATION_DEBT_DONE_STATUS = "Đã hoàn thành"
VIOLATION_DEBT_CONTENT = "Chưa hoàn thành nghĩa vụ Vi phạm"
# V41: kỳ lương cố định 01-15 / 16-cuối tháng + loại quanly khỏi TichLuy
TICHLUY_TARGET_DEFAULT = 5000000
TICHLUY_PERIOD_DEFAULT = 500000
TICHLUY_HEADERS = [
    "STT", "Tên nhân viên", "Ngày bắt đầu làm", "Mục tiêu tích lũy", "Đã tích lũy",
    "Còn lại", "Kỳ gần nhất", "Số tiền kỳ gần nhất", "Chi tiết các kỳ"
]

# Các bộ phận không tham gia đăng ký lịch nghỉ / Tích lũy.
LEAVE_EXCLUDED_ROLES = {"letan", "locker", "tapvu"}
TICHLUY_EXCLUDED_ROLES = {"admin", "letan", "quanly", "locker", "tapvu"}
FRONTDESK_ROLES = {"letan", "quanly"}

# V48: Thông báo sinh nhật đầu tháng.
BIRTHDAY_NOTICE_WORKSHEET = "ThongBaoSinhNhat"
BIRTHDAY_VIEWER_ROLES = {"admin", "quanly", "letan"}
BIRTHDAY_EMPLOYEE_ROLES = {"nhanvien", "letan", "locker"}
BIRTHDAY_NOTICE_DAYS = {1, 2, 3, 4, 5}
BIRTHDAY_NOTICE_MAX_LOGINS_PER_DAY = 3
# V51: Trạng thái làm việc của nhân viên được lưu riêng, không thay đổi cấu trúc Sheet1.
EMPLOYMENT_STATUS_WORKSHEET = "TrangThaiNhanSu"
EMPLOYMENT_STATUS_HEADERS = ["STT", "Tên nhân viên", "Trạng thái", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"]
EMPLOYMENT_STATUS_ACTIVE = "Đang làm việc"
EMPLOYMENT_STATUS_TEMP = "Nghỉ việc tạm thời"
EMPLOYMENT_STATUS_LEFT = "Đã nghỉ việc hẳn"
EMPLOYMENT_STATUS_OPTIONS = [EMPLOYMENT_STATUS_ACTIVE, EMPLOYMENT_STATUS_TEMP, EMPLOYMENT_STATUS_LEFT]
DEFAULT_LEAVE_PAGE = "📅 Đăng ký & Thống kê nghỉ phép"
DEFAULT_LEAVE_PAGE_SLUG = "dang-ky-thong-ke-nghi-phep"

def _set_default_page_after_login(role):
    """Lễ tân/Quản lý/Nhân viên luôn mở trang Đăng ký & Thống kê nghỉ phép sau đăng nhập."""
    role_norm = str(role or '').strip().lower()
    if role_norm in {"letan", "quanly", "nhanvien"}:
        st.session_state.app_page = DEFAULT_LEAVE_PAGE
        try:
            st.query_params["page"] = DEFAULT_LEAVE_PAGE_SLUG
        except Exception:
            pass

def _parse_birthday_date(value):
    """Đọc ngày sinh từ dd/mm/yyyy, dd-mm-yyyy, serial Excel; chấp nhận cả dd/mm."""
    parsed = _parse_vn_date(value)
    if parsed is not None:
        return parsed
    text = str(value or '').strip()
    if not text:
        return None
    for fmt in ('%d/%m', '%d-%m'):
        try:
            tmp = datetime.strptime(text, fmt)
            return date(2000, tmp.month, tmp.day)
        except Exception:
            pass
    return None

def get_month_birthdays(credentials_df, month=None):
    """Danh sách sinh nhật trong tháng của nhanvien/letan/locker, sắp theo ngày sinh."""
    if credentials_df is None or credentials_df.empty:
        return []
    month = int(month or get_vn_today().month)
    rows = []
    for _, r in credentials_df.iterrows():
        role = str(r.get('Phân quyền', '')).strip().lower()
        if role not in BIRTHDAY_EMPLOYEE_ROLES:
            continue
        dob = _parse_birthday_date(r.get('Ngày sinh', ''))
        if dob is None or dob.month != month:
            continue
        system_name = str(r.get('Tên nhân viên', '')).strip()
        full_name = str(r.get('Họ và tên đầy đủ', '')).strip()
        display_name = full_name or system_name
        if not display_name:
            continue
        rows.append({
            'Tên nhân viên': system_name,
            'Họ và tên': display_name,
            'Ngày sinh': f"{dob.day:02d}/{dob.month:02d}",
            'Ngày': dob.day,
            'Vai trò': role,
        })
    rows.sort(key=lambda x: (x['Ngày'], normalize_login_name(x['Họ và tên'])))
    return rows

def _get_birthday_notice_worksheet():
    client = get_gspread_client()
    if not client:
        return None
    ss = client.open_by_key(SHEET_MAT_KHAU_ID)
    ws = _get_or_create_worksheet(ss, BIRTHDAY_NOTICE_WORKSHEET, rows=1000, cols=6)
    header = _gs_call_with_backoff(ws.row_values, 1)
    wanted = ["Tài khoản", "Ngày", "Số lần đăng nhập", "Tạm tắt hôm nay"]
    if header[:4] != wanted:
        gspread_update_range(ws, 'A1:D1', [wanted])
    return ws

def register_birthday_notice_login(username):
    """Tăng bộ đếm đúng 1 lần cho một lần đăng nhập; trả về (count, muted)."""
    today_key = get_vn_today().isoformat()
    user_key = normalize_login_name(username)
    try:
        ws = _get_birthday_notice_worksheet()
        if ws is None:
            return 1, False
        vals = _gs_call_with_backoff(ws.get_all_values)
        found_row = None
        count = 0
        muted = False
        for idx, row in enumerate(vals[1:], start=2):
            if len(row) < 2:
                continue
            if normalize_login_name(row[0]) == user_key and str(row[1]).strip() == today_key:
                found_row = idx
                try:
                    count = int(float(str(row[2]).strip() or '0')) if len(row) > 2 else 0
                except Exception:
                    count = 0
                muted = str(row[3]).strip().casefold() in {'1','true','yes','x','tat','tắt'} if len(row) > 3 else False
                break
        count += 1
        if found_row:
            gspread_update_range(ws, f'C{found_row}', [[count]])
        else:
            _gs_call_with_backoff(ws.append_row, [str(username), today_key, count, ''], value_input_option='USER_ENTERED')
        return count, muted
    except Exception:
        # Nếu Google Sheets tạm lỗi, vẫn cho hiện thông báo trong phiên hiện tại.
        return 1, False

def mute_birthday_notice_today(username):
    """Tạm tắt thông báo sinh nhật cho tài khoản hiện tại đến hết ngày hôm nay."""
    today_key = get_vn_today().isoformat()
    user_key = normalize_login_name(username)
    try:
        ws = _get_birthday_notice_worksheet()
        if ws is None:
            return False, "Không kết nối được Google Sheets."
        vals = _gs_call_with_backoff(ws.get_all_values)
        found_row = None
        count = 0
        for idx, row in enumerate(vals[1:], start=2):
            if len(row) >= 2 and normalize_login_name(row[0]) == user_key and str(row[1]).strip() == today_key:
                found_row = idx
                try:
                    count = int(float(str(row[2]).strip() or '0')) if len(row) > 2 else 0
                except Exception:
                    count = 0
                break
        if found_row:
            gspread_update_range(ws, f'D{found_row}', [["1"]])
        else:
            _gs_call_with_backoff(ws.append_row, [str(username), today_key, max(1, count), '1'], value_input_option='USER_ENTERED')
        return True, "Đã tạm tắt thông báo sinh nhật đến hết hôm nay."
    except Exception as e:
        return False, f"Không thể tạm tắt thông báo: {e}"

def render_birthday_login_notice(credentials_df):
    """Hiện thông báo trong ngày 1-5, tối đa ở 3 lần đăng nhập đầu mỗi ngày."""
    role = str(st.session_state.get('current_role', '')).strip().lower()
    if role not in BIRTHDAY_VIEWER_ROLES:
        st.session_state.birthday_login_event = False
        return
    today = get_vn_today()
    if today.day not in BIRTHDAY_NOTICE_DAYS:
        st.session_state.birthday_login_event = False
        return
    birthdays = get_month_birthdays(credentials_df, today.month)
    if not birthdays:
        st.session_state.birthday_login_event = False
        return
    # Chỉ tăng số lần khi đây thực sự là một phiên vừa đăng nhập.
    if st.session_state.get('birthday_login_event', False):
        count, muted = register_birthday_notice_login(st.session_state.get('current_user', ''))
        st.session_state.birthday_notice_count_today = count
        st.session_state.birthday_notice_muted_today = muted
        st.session_state.birthday_login_event = False
    count = int(st.session_state.get('birthday_notice_count_today', 999) or 999)
    muted = bool(st.session_state.get('birthday_notice_muted_today', False))
    if muted or count > BIRTHDAY_NOTICE_MAX_LOGINS_PER_DAY:
        return

    role_labels = {'nhanvien': 'Nhân viên', 'letan': 'Lễ tân', 'locker': 'Locker'}
    lines = []
    for b in birthdays:
        role_label = role_labels.get(b['Vai trò'], b['Vai trò'])
        lines.append(f"🎂 **{b['Họ và tên']}** — {b['Ngày sinh']} — {role_label}")
    st.info("🎉 **SINH NHẬT TRONG THÁNG %02d**\n\n%s" % (today.month, "\n\n".join(lines)))
    c_notice, c_mute = st.columns([4, 1])
    with c_notice:
        st.caption(f"Thông báo này xuất hiện trong ngày 01–05 và tối đa ở 3 lần đăng nhập đầu mỗi ngày. Hôm nay: lần {count}/3.")
    with c_mute:
        if st.button("🔕 Tạm tắt hôm nay", use_container_width=True, key=f"mute_birthday_{today.isoformat()}"):
            ok, msg = mute_birthday_notice_today(st.session_state.get('current_user', ''))
            if ok:
                st.session_state.birthday_notice_muted_today = True
                st.toast(msg)
                st.rerun()
            else:
                st.error(msg)

def render_manual_birthday_check(credentials_df, key_prefix="birthday_manual"):
    """Nút chủ động xem sinh nhật tháng hiện tại, dùng cho mọi vai trò."""
    today = get_vn_today()
    state_key = f"{key_prefix}_show_{today.year}_{today.month}"
    if st.button("🎂 Kiểm tra sinh nhật tháng này", use_container_width=True, key=f"{key_prefix}_button_{today.year}_{today.month}"):
        st.session_state[state_key] = not bool(st.session_state.get(state_key, False))
    if not st.session_state.get(state_key, False):
        return
    birthdays = get_month_birthdays(credentials_df, today.month)
    if not birthdays:
        st.info(f"Tháng {today.month:02d} hiện chưa có sinh nhật của Nhân viên/Lễ tân/Locker trong hồ sơ.")
        return
    role_labels = {'nhanvien': 'Nhân viên', 'letan': 'Lễ tân', 'locker': 'Locker'}
    st.markdown(f"#### 🎉 Sinh nhật tháng {today.month:02d}")
    for b in birthdays:
        st.markdown(f"- 🎂 **{b['Họ và tên']}** — {b['Ngày sinh']} — {role_labels.get(b['Vai trò'], b['Vai trò'])}")


@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None


def gspread_update_range(sheet, range_name, values, **kwargs):
    """Tương thích cả gspread 5.x (range trước) và 6.x (values trước)."""
    try:
        major = int(str(getattr(gspread, '__version__', '5')).split('.')[0])
    except Exception:
        major = 5
    if major >= 6:
        return sheet.update(values, range_name, **kwargs)
    return sheet.update(range_name, values, **kwargs)


def _is_google_sheets_quota_error(exc):
    """Nhận diện lỗi quota/rate-limit của Google Sheets API."""
    msg = str(exc).lower()
    return (
        ('429' in msg or 'too many requests' in msg)
        and ('quota' in msg or 'rate' in msg or 'read requests' in msg or 'write requests' in msg)
    )


def _gs_call_with_backoff(func, *args, retries=5, **kwargs):
    """
    Gọi Google Sheets API với exponential backoff khi gặp 429.
    Mục tiêu chính vẫn là GIẢM số request; retry chỉ là lớp bảo vệ khi quota đang tạm đầy.
    """
    last_exc = None
    for attempt in range(max(1, int(retries))):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_google_sheets_quota_error(exc) or attempt >= retries - 1:
                raise
            # 2s -> 4s -> 8s -> 16s; chỉ dùng khi thật sự gặp 429.
            time.sleep(min(2 ** (attempt + 1), 16))
    if last_exc is not None:
        raise last_exc


def _get_employment_status_worksheet():
    client = get_gspread_client()
    if not client:
        return None
    ss = client.open_by_key(SHEET_MAT_KHAU_ID)
    ws = _get_or_create_worksheet(ss, EMPLOYMENT_STATUS_WORKSHEET, rows=1000, cols=len(EMPLOYMENT_STATUS_HEADERS))
    header = _gs_call_with_backoff(ws.row_values, 1)
    if header[:len(EMPLOYMENT_STATUS_HEADERS)] != EMPLOYMENT_STATUS_HEADERS:
        gspread_update_range(ws, "A1:F1", [EMPLOYMENT_STATUS_HEADERS])
    return ws


@st.cache_data(ttl=120, show_spinner=False)
def load_employment_status_map():
    result = {}
    try:
        ws = _get_employment_status_worksheet()
        if ws is None:
            return result
        vals = _gs_call_with_backoff(ws.get_all_values)
        for row in vals[1:]:
            if len(row) < 2:
                continue
            key = normalize_login_name(row[1])
            if not key:
                continue
            status = str(row[2]).strip() if len(row) > 2 else EMPLOYMENT_STATUS_ACTIVE
            if status not in EMPLOYMENT_STATUS_OPTIONS:
                status = EMPLOYMENT_STATUS_ACTIVE
            result[key] = status
    except Exception:
        pass
    return result


def set_employee_employment_status(employee_name, status, updated_by):
    status = str(status or '').strip()
    if status not in EMPLOYMENT_STATUS_OPTIONS:
        return False, "Trạng thái không hợp lệ."
    try:
        creds = load_credentials()
        target_key = normalize_login_name(employee_name)
        role = ''
        if isinstance(creds, pd.DataFrame) and not creds.empty:
            hit = creds[creds['Tên nhân viên'].apply(normalize_login_name) == target_key]
            if not hit.empty:
                role = str(hit.iloc[0].get('Phân quyền', '')).strip().lower()
        if role != 'nhanvien':
            return False, "Chỉ có thể cập nhật trạng thái nghỉ việc cho tài khoản vai trò nhanvien."
        ws = _get_employment_status_worksheet()
        if ws is None:
            return False, "Không kết nối được Google Sheets."
        vals = _gs_call_with_backoff(ws.get_all_values)
        found_row = None
        for r_idx, row in enumerate(vals[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) == target_key:
                found_row = r_idx
                break
        now = datetime.now(VN_TZ)
        payload = [str(employee_name).strip(), status, now.strftime('%d/%m/%Y'), now.strftime('%H:%M:%S'), str(updated_by).strip()]
        if found_row:
            gspread_update_range(ws, f"B{found_row}:F{found_row}", [payload])
        else:
            stt = max(1, len(vals))
            _gs_call_with_backoff(ws.append_row, [stt] + payload, value_input_option='USER_ENTERED')
        try:
            load_employment_status_map.clear()
        except Exception:
            pass
        return True, f"Đã cập nhật {employee_name}: {status}."
    except Exception as e:
        return False, f"Lỗi cập nhật trạng thái nhân sự: {e}"


def _clear_payroll_config_cache():
    """Chỉ xóa cache cấu hình lương, không xóa toàn bộ cache của ứng dụng."""
    try:
        _load_payroll_config_rows_cached.clear()
    except Exception:
        pass


def _clear_dynamic_data_caches():
    """
    Xóa đúng các cache dữ liệu nghiệp vụ có thể vừa thay đổi.
    Tuyệt đối không dùng st.cache_data.clear() vì nó làm mất mọi cache và gây bão request Google Sheets.
    """
    for fn_name in (
        'load_credentials',
        'load_backup_sheet_data',
        'load_secondary_leave_sheet_data',
        'load_loai_nghi_from_gsheet',
        'load_tichluy_tracking',
    ):
        try:
            fn = globals().get(fn_name)
            if fn is not None and hasattr(fn, 'clear'):
                fn.clear()
        except Exception:
            pass

# --- ĐỒNG BỘ EXCEL SANG GOOGLE SHEETS (CHỈ THÊM MỚI) ---
def admin_sync_excel_to_gsheet():
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        file_id = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
        temp_file = "temp_sync.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file): os.remove(temp_file)
        
        df_excel = xls.iloc[:, :10].copy()
        
        def clean_val(val, is_date=False):
            try:
                if pd.isna(val) or str(val).strip() in ["nan", "NaT", "None", ""]: return ""
                if is_date or hasattr(val, 'strftime'):
                    if hasattr(val, 'strftime'): return val.strftime('%d/%m/%Y')
                    if isinstance(val, (int, float)): return pd.to_datetime(val, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
                    return pd.to_datetime(str(val).strip().split(' ')[0], dayfirst=True).strftime('%d/%m/%Y')
                return str(val).strip()
            except: return str(val).strip()

        cols = df_excel.columns.tolist()
        if len(cols) > 0: df_excel[cols[0]] = df_excel[cols[0]].apply(lambda x: clean_val(x, is_date=True))
        if len(cols) > 7: df_excel[cols[7]] = df_excel[cols[7]].apply(lambda x: clean_val(x, is_date=True))
        for c in cols:
            if c not in [cols[0], cols[7]] if len(cols)>7 else [cols[0]]:
                df_excel[c] = df_excel[c].apply(lambda x: clean_val(x))

        df_excel = df_excel.fillna("")
        df_excel.columns = ["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"]
        
        # Tải dữ liệu hiện tại trên GSheet
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        gsheet_data = _gs_call_with_backoff(sheet_dp.get_all_values)
        
        if len(gsheet_data) > 1:
            df_gsheet = pd.DataFrame(gsheet_data[1:], columns=gsheet_data[0])
        else:
            df_gsheet = pd.DataFrame(columns=df_excel.columns)

        # Lọc ra những dòng chưa có trên GSheet
        df_gsheet['Merge_Key'] = df_gsheet['Ngày'].astype(str) + "_" + df_gsheet['Tên nhân viên'].apply(normalize_name) + "_" + df_gsheet.get('Lý do nghỉ', df_gsheet.get('Loại nghỉ', '')).astype(str)
        df_excel['Merge_Key'] = df_excel['Ngày'].astype(str) + "_" + df_excel['Tên nhân viên'].apply(normalize_name) + "_" + df_excel['Lý do nghỉ'].astype(str)
        
        new_rows_df = df_excel[~df_excel['Merge_Key'].isin(df_gsheet['Merge_Key'])].drop(columns=['Merge_Key'])
        
        if new_rows_df.empty:
            return True, "Không có dữ liệu mới nào từ Excel để đồng bộ."

        values_to_append = new_rows_df.values.tolist()
        sheet_dp.append_rows(values_to_append, value_input_option='USER_ENTERED')
        
        _clear_dynamic_data_caches()
        return True, f"Đã thêm mới {len(values_to_append)} dòng dữ liệu từ Excel lên Sheet (không ghi đè)!"
    except Exception as e:
        return False, f"Lỗi đồng bộ: {e}"

# --- ĐỒNG BỘ GOOGLE SHEETS SANG EXCEL (TẠO FILE DOWNLOAD CHỈ THÊM MỚI) ---
def admin_sync_gsheet_to_excel(df_gsheet, df_excel_goc):
    df_gsheet['Merge_Key'] = df_gsheet['Ngày'].astype(str) + "_" + df_gsheet['Tên nhân viên'].apply(normalize_name) + "_" + df_gsheet.get('Lý do nghỉ', df_gsheet.get('Loại nghỉ', '')).astype(str)
    df_excel_goc['Merge_Key'] = df_excel_goc.iloc[:, 0].astype(str) + "_" + df_excel_goc.iloc[:, 1].apply(normalize_name) + "_" + df_excel_goc.iloc[:, 2].astype(str)
    
    new_rows = df_gsheet[~df_gsheet['Merge_Key'].isin(df_excel_goc['Merge_Key'])].copy()
    
    if new_rows.empty:
        return df_excel_goc, False
        
    new_rows = new_rows.drop(columns=['Merge_Key'], errors='ignore')
    df_excel_merged = pd.concat([df_excel_goc.drop(columns=['Merge_Key'], errors='ignore'), new_rows], ignore_index=True)
    return df_excel_merged, True

# --- HÀM TẢI MẬT KHẨU, PHÂN QUYỀN VÀ TRẠNG THÁI ĐĂNG NHẬP ---
@st.cache_resource(show_spinner=False)
def ensure_credential_control_columns():
    """Tạo các cột điều khiển nếu Sheet mật khẩu cũ chưa có."""
    try:
        client = get_gspread_client()
        if not client: return
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        header = _gs_call_with_backoff(sheet.row_values, 1)
        wanted = ["Khóa đăng nhập", "Remember Token Hash", "Remember Token Expiry"]
        # Sau khi chèn J/K: R=Khóa, S=Token Hash, T=Token Expiry
        if len(header) < 20 or header[17:20] != wanted:
            gspread_update_range(sheet, 'R1:T1', [wanted])
    except Exception:
        pass

@st.cache_data(ttl=120, show_spinner=False)
def load_credentials():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            rows = _gs_call_with_backoff(sheet.get_all_values)
            if len(rows) > 1:
                data_list = []
                for idx, row in enumerate(rows[1:], start=2):
                    stt = row[0] if len(row) > 0 else idx - 1
                    ten = row[1] if len(row) > 1 else ""
                    pwd = str(row[2]) if len(row) > 2 and str(row[2]) != "" else "123456"
                    role = row[3] if len(row) > 3 else "nhanvien"
                    fullname = str(row[4]).strip() if len(row) > 4 else ""
                    dob = str(row[5]).strip() if len(row) > 5 else ""
                    phone = str(row[6]).strip() if len(row) > 6 else ""
                    email = str(row[7]).strip() if len(row) > 7 else ""
                    address = str(row[8]).strip() if len(row) > 8 else ""
                    bank_account = str(row[9]).strip() if len(row) > 9 else ""
                    bank_name = str(row[10]).strip() if len(row) > 10 else ""
                    ps_thang = str(row[11]).strip() if len(row) > 11 else "0"
                    cp_thang = str(row[12]).strip() if len(row) > 12 else "0"
                    pn_nam = str(row[13]).strip() if len(row) > 13 else "0"
                    ca_lam_viec = str(row[14]).strip() if len(row) > 14 else ""
                    ngay_bd = str(row[15]).strip() if len(row) > 15 else ""
                    chu_ky = str(row[16]).strip() if len(row) > 16 else ""
                    login_locked = str(row[17]).strip() if len(row) > 17 else ""
                    remember_hash = str(row[18]).strip() if len(row) > 18 else ""
                    remember_expiry = str(row[19]).strip() if len(row) > 19 else ""

                    # Bỏ các dòng tiêu đề phụ bị đặt lẫn trong dữ liệu tài khoản.
                    ten_norm = normalize_login_name(ten)
                    if ten_norm in {"ten nhan vien", "ten he thong", "username", "user name"}:
                        continue
                    if str(ten).strip() != "":
                        data_list.append({
                            'STT': stt, 'Tên nhân viên': str(ten).strip(), 'Mật khẩu': pwd,
                            'Phân quyền': str(role).strip().lower() if str(role).strip() else 'nhanvien',
                            'Họ và tên đầy đủ': fullname, 'Ngày sinh': dob, 'Điện thoại': phone,
                            'Email': email, 'Địa chỉ': address, 'Số tài khoản ngân hàng': bank_account,
                            'Tên ngân hàng': bank_name, 'Phát sinh tháng': ps_thang,
                            'Có phép tháng': cp_thang, 'Phép năm': pn_nam, 'Ca làm việc': ca_lam_viec,
                            'Ngày bắt đầu ca': ngay_bd, 'Chu kỳ': chu_ky,
                            'Khóa đăng nhập': login_locked, 'Remember Token Hash': remember_hash,
                            'Remember Token Expiry': remember_expiry
                        })
                return pd.DataFrame(data_list)
    except Exception:
        pass
    return pd.DataFrame(columns=[
        'STT', 'Tên nhân viên', 'Mật khẩu', 'Phân quyền', 'Họ và tên đầy đủ', 'Ngày sinh',
        'Điện thoại', 'Email', 'Địa chỉ', 'Số tài khoản ngân hàng', 'Tên ngân hàng',
        'Phát sinh tháng', 'Có phép tháng', 'Phép năm', 'Ca làm việc', 'Ngày bắt đầu ca',
        'Chu kỳ', 'Khóa đăng nhập',
        'Remember Token Hash', 'Remember Token Expiry'
    ])

def set_accounts_login_lock(usernames, locked=True):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        values = _gs_call_with_backoff(sheet.get_all_values)
        targets = {normalize_login_name(x) for x in usernames}
        changed = 0
        for r_idx, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) in targets:
                sheet.update_cell(r_idx, 18, 'KHÓA' if locked else '')
                if locked:
                    sheet.update_cell(r_idx, 19, '')
                    sheet.update_cell(r_idx, 20, '')
                changed += 1
        _clear_dynamic_data_caches()
        return True, f"Đã {'khóa' if locked else 'mở khóa'} {changed} tài khoản."
    except Exception as e:
        return False, f"Lỗi cập nhật khóa đăng nhập: {e}"

def create_remember_token(username, days=None):
    """Lưu HASH token ở Google Sheet và duy trì cho tới khi người dùng chủ động Đăng xuất."""
    try:
        client = get_gspread_client()
        if not client: return None
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        values = _gs_call_with_backoff(sheet.get_all_values)
        target = normalize_login_name(username)
        for r_idx, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) == target:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
                sheet.update_cell(r_idx, 19, token_hash)
                # Không đặt ngày hết hạn: token chỉ bị xóa khi Đăng xuất hoặc tài khoản bị khóa.
                sheet.update_cell(r_idx, 20, '')
                _clear_dynamic_data_caches()
                return token
    except Exception:
        pass
    return None

def revoke_remember_token(username):
    try:
        client = get_gspread_client()
        if not client: return
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        values = _gs_call_with_backoff(sheet.get_all_values)
        target = normalize_login_name(username)
        for r_idx, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) == target:
                sheet.update_cell(r_idx, 19, '')
                sheet.update_cell(r_idx, 20, '')
                break
        _clear_dynamic_data_caches()
    except Exception:
        pass

def validate_remember_token(token, credentials_df):
    """Token hợp lệ cho tới khi bị thu hồi/khóa; không tự hết hạn theo thời gian."""
    if not token or credentials_df.empty:
        return None
    token_hash = hashlib.sha256(str(token).encode('utf-8')).hexdigest()
    for _, row in credentials_df.iterrows():
        saved_hash = str(row.get('Remember Token Hash', '')).strip()
        if not saved_hash or not hmac.compare_digest(token_hash, saved_hash):
            continue
        if is_locked_value(row.get('Khóa đăng nhập', '')):
            return None
        return row
    return None

# --- CẬP NHẬT THÔNG TIN CÁ NHÂN ---
def update_user_profile(username, new_pass, fullname, dob, phone, email, address, bank_account="", bank_name=""):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        # Tìm không phân biệt dấu / HOA thường để đồng nhất với đăng nhập.
        values = _gs_call_with_backoff(sheet.get_all_values)
        target = normalize_login_name(username)
        row_idx = None
        for i, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) == target:
                row_idx = i
                break
        if row_idx:
            if new_pass: sheet.update_cell(row_idx, 3, str(new_pass))
            sheet.update_cell(row_idx, 5, str(fullname))
            sheet.update_cell(row_idx, 6, str(dob))
            sheet.update_cell(row_idx, 7, f"'{phone}")
            sheet.update_cell(row_idx, 8, str(email))
            sheet.update_cell(row_idx, 9, str(address))
            # Hai cột mới được chèn giữa I và J.
            sheet.update_cell(row_idx, 10, f"'{bank_account}" if str(bank_account).strip() else "")
            sheet.update_cell(row_idx, 11, str(bank_name))
            _clear_dynamic_data_caches()
            return True, "Cập nhật hồ sơ thành công!"
        return False, "Không tìm thấy tài khoản."
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- GHI NHẬN HÀNG LOẠT CA LÀM VIỆC TỪ DATAFRAME ---
def batch_update_shift_schedule(edited_df):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        all_vals = _gs_call_with_backoff(sheet.get_all_values)
        
        shift_map = {}
        for _, r in edited_df.iterrows():
            nv_name = normalize_login_name(r['Tên nhân viên'])
            shift_map[nv_name] = {
                'ca': str(r.get('Ca làm việc', '')).replace("nan", "").strip(),
                'ngay': str(r.get('Ngày bắt đầu ca', '')).replace("nan", "").strip(),
                'chuky': str(r.get('Chu kỳ', '')).replace("nan", "").strip()
            }
        
        for i, row in enumerate(all_vals):
            if i == 0: continue 
            if len(row) > 1:
                nv_name = normalize_login_name(row[1])
                if nv_name in shift_map:
                    while len(row) < 20: row.append("") 
                    row[14] = shift_map[nv_name]['ca']
                    row[15] = shift_map[nv_name]['ngay']
                    row[16] = shift_map[nv_name]['chuky']
                    all_vals[i] = row
        
        try: sheet.update('A1', all_vals)
        except: sheet.update(all_vals) 
            
        _clear_dynamic_data_caches()
        return True, "Đã lưu đồng loạt cấu hình Ca làm việc thành công!"
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- TẢI DỮ LIỆU TỪ GOOGLE SHEET DỰ PHÒNG ---
@st.cache_data(ttl=30, show_spinner=False)
def load_backup_sheet_data():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
            rows = _gs_call_with_backoff(sheet.get_all_values)
            if len(rows) > 1:
                df_bk = pd.DataFrame(rows[1:], columns=rows[0])
                if 'Loại nghỉ' in df_bk.columns:
                    df_bk.rename(columns={'Loại nghỉ': 'Lý do nghỉ'}, inplace=True)
                    
                # Bỏ các cột rỗng tên và trùng tên (Fix lỗi PyArrow)
                df_bk = df_bk.loc[:, df_bk.columns.astype(str).str.strip() != '']
                df_bk = df_bk.loc[:, ~df_bk.columns.duplicated(keep='first')]
                
                return df_bk
    except Exception:
        pass
    return pd.DataFrame(columns=["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"])

@st.cache_data(ttl=30, show_spinner=False)
def load_secondary_leave_sheet_data():
    """Đọc Sheet1 của Google Sheet thứ hai, chuẩn hóa về đúng A:J của lịch nghỉ."""
    expected = [
        "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
        "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
    ]
    try:
        client = get_gspread_client()
        if not client:
            return pd.DataFrame(columns=expected)
        sheet = client.open_by_key(SHEET_LICH_NGHI_2_ID).get_worksheet(0)
        values = _gs_call_with_backoff(sheet.get, 'A:J')
        if not values or len(values) < 2:
            return pd.DataFrame(columns=expected)

        rows = []
        for sheet_row, row in enumerate(values[1:], start=2):
            r = list(row[:10]) + [""] * max(0, 10 - len(row))
            if not any(str(v).strip() for v in r):
                continue
            item = dict(zip(expected, r[:10]))
            item['__source_sheet_id'] = SHEET_LICH_NGHI_2_ID
            item['__source_row'] = sheet_row
            rows.append(item)
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=expected + ['__source_sheet_id', '__source_row'])
    except Exception:
        return pd.DataFrame(columns=expected + ['__source_sheet_id', '__source_row'])

@st.cache_data(ttl=120, show_spinner=False)
def load_loai_nghi_from_gsheet():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
            rows = _gs_call_with_backoff(sheet.get_all_values)
            if len(rows) > 1:
                return pd.DataFrame(rows[1:], columns=rows[0])
    except Exception:
        pass
    return pd.DataFrame()

# --- GHI VÀ XÓA LỊCH ---
def _next_data_row_a_to_j(sheet):
    """Tìm dòng kế tiếp sau last row thực tế trong vùng A:J."""
    values = _gs_call_with_backoff(sheet.get, 'A:J')
    last_non_empty = 0
    for idx, row in enumerate(values, start=1):
        if any(str(v).strip() != "" for v in row[:10]):
            last_non_empty = idx
    return max(2, last_non_empty + 1)

def _live_sheet_to_leave_df(sheet):
    """Đọc trực tiếp A:J để kiểm tra trùng/thứ tự ngay trước khi ghi."""
    try:
        values = _gs_call_with_backoff(sheet.get, 'A:J')
        if not values or len(values) < 2:
            return pd.DataFrame(columns=[
                "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
                "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
            ])
        header = [str(x).strip() for x in values[0][:10]]
        expected = [
            "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
            "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
        ]
        if len(header) < 10 or not header[0]:
            header = expected
        rows = []
        for row in values[1:]:
            r = list(row[:10]) + [""] * max(0, 10 - len(row))
            if any(str(v).strip() for v in r):
                rows.append(r[:10])
        df = pd.DataFrame(rows, columns=header[:10]) if rows else pd.DataFrame(columns=header[:10])
        if 'Loại nghỉ' in df.columns and 'Lý do nghỉ' not in df.columns:
            df = df.rename(columns={'Loại nghỉ': 'Lý do nghỉ'})
        for c in expected:
            if c not in df.columns:
                df[c] = ""
        return df[expected].copy()
    except Exception:
        return pd.DataFrame()


def _leave_exists_in_sources(df_sources, ngay, nv, loai_nghi):
    if df_sources is None or df_sources.empty:
        return False
    target_date = pd.to_datetime(ngay, errors='coerce', dayfirst=True)
    if pd.isna(target_date):
        return False
    target_date = target_date.date()
    d = df_sources.copy()
    d['Ngày_cmp'] = pd.to_datetime(d['Ngày'], errors='coerce', dayfirst=True).dt.date
    name_cmp = d['Tên nhân viên'].astype(str).apply(normalize_login_name)
    reason_cmp = d['Lý do nghỉ'].astype(str).apply(normalize_leave_reason)
    return bool(((d['Ngày_cmp'] == target_date) &
                 (name_cmp == normalize_login_name(nv)) &
                 (reason_cmp == normalize_leave_reason(loai_nghi))).any())


def _progressive_ordinal_and_bonus(df_sources, ngay, loai_nghi):
    """
    Tính thứ tự RIÊNG cho từng loại vi phạm trong cùng ngày:
    - Nghỉ không phép
    - Đi trễ không phép
    - Về sớm không phép (kể cả biến thể Ra sớm không phép)

    Người 1/2: +0; Người 3: +100.000; Người 4: +200.000; ...
    """
    canonical = get_progressive_penalty_reason(loai_nghi)
    if canonical is None:
        return 1, 0

    target_date = pd.to_datetime(ngay, errors='coerce', dayfirst=True)
    if pd.isna(target_date) or df_sources is None or df_sources.empty:
        ordinal = 1
    else:
        target_date = target_date.date()
        d = df_sources.copy()
        d['Ngày_cmp'] = pd.to_datetime(d['Ngày'], errors='coerce', dayfirst=True).dt.date
        canonical_series = d['Lý do nghỉ'].astype(str).apply(get_progressive_penalty_reason)
        mask = (d['Ngày_cmp'] == target_date) & canonical_series.eq(canonical)
        ordinal = int(mask.sum()) + 1

    bonus = max(0, ordinal - 2) * 100000
    return ordinal, bonus


def _unexcused_ordinal_and_bonus(df_sources, ngay):
    """Alias tương thích code cũ cho riêng Nghỉ không phép."""
    return _progressive_ordinal_and_bonus(df_sources, ngay, "Nghỉ không phép")


def save_lich_nghi_to_backup_sheet(ngay, nv, loai_nghi, chi_tiet, so_ngay, so_ngay_cong_don, phat_vi_pham, updated_by, df_main_source=None):
    """
    Chỉ ghi lịch vào Google Sheet dự phòng (SHEET_DU_PHONG_ID), Sheet1, đúng A:J ở last row.
    KHÔNG ghi lịch đăng ký mới sang file chính (SHEET_CHINH_ID).
    Trước khi ghi sẽ đọc LIVE Sheet1 để:
    - chặn trùng cùng nhân viên + ngày + loại nghỉ;
    - tính thứ tự riêng cho Nghỉ không phép / Đi trễ không phép / Về sớm không phép và tiền phạt lũy tiến.
    """
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets."

        ngay_cn = get_vn_today().strftime('%d/%m/%Y')
        gio_cn = datetime.now(VN_TZ).strftime('%H:%M:%S')

        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        header = [
            "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
            "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
            "Giờ cập nhật", "Người cập nhật"
        ]
        current_header = sheet_dp.get('A1:J1')
        current_header = current_header[0] if current_header else []
        if not any(str(v).strip() for v in current_header):
            gspread_update_range(sheet_dp, 'A1:J1', [header], value_input_option='USER_ENTERED')

        live_backup = _live_sheet_to_leave_df(sheet_dp)
        combined_live = combine_leave_sources_for_daily_stats(df_main_source, live_backup)

        # Bảo vệ dữ liệu ở lớp cuối cùng, kể cả khi giao diện đang dùng cache cũ.
        if _leave_exists_in_sources(combined_live, ngay, nv, loai_nghi):
            return False, f"Nhân viên '{nv}' đã có loại nghỉ '{str(loai_nghi).replace('🔴 ', '')}' trong ngày {ngay}. Không được đăng ký trùng."

        save_detail = str(chi_tiet).strip()
        save_penalty = float(phat_vi_pham) if phat_vi_pham is not None else 0.0
        ordinal_note = ""
        progressive_reason = get_progressive_penalty_reason(loai_nghi)
        if progressive_reason:
            ordinal, extra_penalty = _progressive_ordinal_and_bonus(combined_live, ngay, loai_nghi)
            ordinal_note = f"Người Thứ {ordinal} {progressive_reason.lower()}"
            save_detail = f"{ordinal_note} | {save_detail}" if save_detail else ordinal_note
            save_penalty += extra_penalty

        row_values = [
            str(ngay),
            str(nv),
            str(loai_nghi).replace("🔴 ", ""),
            save_detail,
            float(so_ngay) if so_ngay is not None else 0.0,
            float(so_ngay_cong_don),
            save_penalty,
            str(ngay_cn),
            str(gio_cn),
            str(updated_by),
        ]

        target_row = _next_data_row_a_to_j(sheet_dp)
        gspread_update_range(sheet_dp, f"A{target_row}:J{target_row}", [row_values], value_input_option='USER_ENTERED')

        _clear_dynamic_data_caches()
        if ordinal_note:
            extra = max(0, save_penalty - float(phat_vi_pham or 0))
            return True, f"{ordinal_note}. Phạt lũy tiến cộng thêm {extra:,.0f} VNĐ; tổng phạt {save_penalty:,.0f} VNĐ."
        return True, "Đã ghi nhận lịch nghỉ thành công vào Google Sheet dự phòng!"
    except Exception as e:
        return False, f"Lỗi ghi dữ liệu: {e}"

def delete_backup_row(row_index_1_based, updated_by=None):
    """Xóa 1 dòng ở Sheet dự phòng và tự xếp lại Người Thứ X/phạt lũy tiến nếu cần."""
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        actor = str(updated_by or st.session_state.get("current_user", "Hệ thống"))

        # Đọc bản ghi trước khi xóa để biết nhóm nào cần xếp lại.
        row_values = sheet.get(f'A{row_index_1_based}:J{row_index_1_based}')
        deleted_row = None
        affected_groups = set()
        if row_values and row_values[0]:
            expected = [
                "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
                "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
                "Giờ cập nhật", "Người cập nhật"
            ]
            vals = list(row_values[0][:10]) + [""] * max(0, 10 - len(row_values[0]))
            deleted_row = dict(zip(expected, vals[:10]))
            deleted_row['__source_sheet_id'] = SHEET_DU_PHONG_ID
            deleted_row['__source_row'] = int(row_index_1_based)
            group_key = _progressive_group_key(deleted_row)
            if group_key:
                affected_groups.add(group_key)

        sheet.delete_rows(row_index_1_based)

        rebalanced = 0
        if affected_groups:
            rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, actor)

        _clear_dynamic_data_caches()
        if rebalanced:
            return True, f"Đã xóa lịch nghỉ và tự xếp lại thứ tự/phạt cho {rebalanced} bản ghi còn lại."
        return True, "Đã xóa lịch nghỉ thành công!"
    except Exception as e:
        return False, f"Lỗi xóa dòng: {e}"


def _find_schedule_row_index(sheet, original_row):
    """Tìm dòng Google Sheet theo Ngày + Nhân viên + Lý do (bộ ba đang được hệ thống chặn trùng)."""
    values = _gs_call_with_backoff(sheet.get_all_values)
    if len(values) < 2:
        return None
    headers = values[0]
    target_key = schedule_key(original_row)
    for idx, vals in enumerate(values[1:], start=2):
        row_dict = {headers[i]: vals[i] if i < len(vals) else '' for i in range(len(headers))}
        if schedule_key(row_dict) == target_key:
            return idx
    return None


def _parse_leave_number(value, default=0.0, money=False):
    """Chuẩn hóa số lấy từ sheet LoaiNghi, hỗ trợ dấu chấm/phẩy và ký hiệu tiền."""
    try:
        if value is None or pd.isna(value):
            return float(default)
        s = str(value).strip()
        if s.lower() in ["", "-", "nan", "none", "nat"]:
            return float(default)
        if money:
            s = (s.replace('.', '').replace(',', '').replace(' ', '')
                   .replace('đ', '').replace('Đ', '').replace('VNĐ', '').replace('VND', ''))
        else:
            s = s.replace(',', '.')
        return float(s)
    except Exception:
        return float(default)


def build_leave_reason_catalog(source_df=None):
    """
    Tạo danh mục Lý do nghỉ -> Số ngày tính / Phạt vi phạm từ sheet LoaiNghi.
    Giữ tên hiển thị sạch, không có tiền tố biểu tượng đỏ.
    """
    source = source_df if source_df is not None else globals().get('df_loai_nghi', pd.DataFrame())
    catalog = {}
    if source is None or source.empty:
        return catalog

    for _, row in source.iterrows():
        vals = row.tolist()
        name = str(vals[1]).strip() if len(vals) > 1 else ""
        if not name or name.lower() in ["nan", "none"]:
            name = str(row.get('Lý do nghỉ', row.get('Loại nghỉ', ''))).strip()
        name = name.replace('🔴 ', '').strip()
        if not name or name.lower() in ["nan", "none", "loại nghỉ", "lý do nghỉ"]:
            continue

        days = _parse_leave_number(vals[4] if len(vals) > 4 else 0, 0.0, money=False)
        penalty = _parse_leave_number(vals[5] if len(vals) > 5 else 0, 0.0, money=True)
        catalog[normalize_leave_reason(name)] = {
            'name': name,
            'days': float(days),
            'penalty': float(penalty),
        }
    return catalog


def get_leave_reason_options(source_df=None, extra_values=None):
    """Danh sách dropdown Lý do nghỉ, tự lấy từ LoaiNghi và bổ sung giá trị lịch sử đang có."""
    catalog = build_leave_reason_catalog(source_df)
    options = [v['name'] for v in catalog.values()]
    if extra_values is not None:
        for val in extra_values:
            clean = str(val).replace('🔴 ', '').strip()
            if clean and clean.lower() not in ['nan', 'none', 'nat']:
                if not any(normalize_leave_reason(clean) == normalize_leave_reason(x) for x in options):
                    options.append(clean)
    return options


def _exclude_original_from_leave_df(df_sources, original_row):
    """Loại đúng bản ghi đang sửa ra khỏi tập dữ liệu dùng để tính lại."""
    if df_sources is None or df_sources.empty:
        return pd.DataFrame(columns=df_sources.columns if hasattr(df_sources, 'columns') else [])
    d = df_sources.copy()

    source_id = str(original_row.get('__source_sheet_id', '')).strip()
    source_row = original_row.get('__source_row', '')
    if source_id and source_row not in ['', None] and '__source_sheet_id' in d.columns and '__source_row' in d.columns:
        try:
            target_row = int(float(source_row))
            row_num = pd.to_numeric(d['__source_row'], errors='coerce')
            exact_mask = (d['__source_sheet_id'].astype(str).str.strip() == source_id) & (row_num == target_row)
            if exact_mask.any():
                return d.loc[~exact_mask].copy()
        except Exception:
            pass

    original_key = schedule_key(original_row)
    keep_mask = d.apply(lambda r: schedule_key(r) != original_key, axis=1)
    return d.loc[keep_mask].copy()


def _strip_generated_progressive_prefix(detail):
    """Bỏ tiền tố 'Người Thứ ...' do hệ thống từng tự thêm để tránh lặp khi sửa."""
    import re
    s = str(detail or '').strip()
    pattern = (
        r'^Người\s+Thứ\s+\d+\s+'
        r'(?:nghỉ\s+không\s+phép|đi\s+trễ\s+không\s+phép|về\s+sớm\s+không\s+phép|ra\s+sớm\s+không\s+phép)'
        r'\s*(?:\|\s*)?'
    )
    return re.sub(pattern, '', s, flags=re.IGNORECASE).strip()


def _get_existing_progressive_ordinal(original_row, all_leave_data=None):
    """
    Lấy đúng thứ tự Người Thứ X của bản ghi hiện hữu để GIỮ NGUYÊN khi sửa
    mà vẫn cùng ngày + cùng nhóm vi phạm lũy tiến.

    Ưu tiên:
    1) Đọc trực tiếp "Người Thứ X" đã lưu trong cột Chi tiết.
    2) Nếu dữ liệu cũ chưa có tiền tố này, suy ra vị trí từ chính bản ghi hiện hữu
       trong dữ liệu 2 nguồn (không coi thao tác sửa là một lượt vi phạm mới).
    """
    import re

    # 1. Bản ghi mới của hệ thống luôn có tiền tố này -> đây là nguồn chính xác nhất.
    detail = str(original_row.get('Chi tiết', '') or '')
    m = re.search(r'Người\s+Thứ\s+(\d+)', detail, flags=re.IGNORECASE)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            pass

    # 2. Tương thích dữ liệu cũ chưa ghi "Người Thứ X".
    canonical = get_progressive_penalty_reason(original_row.get('Lý do nghỉ', ''))
    ngay = normalize_schedule_date(original_row.get('Ngày', ''))
    if not canonical or not ngay or all_leave_data is None or getattr(all_leave_data, 'empty', True):
        return None

    d = all_leave_data.copy()
    if 'Ngày' not in d.columns or 'Lý do nghỉ' not in d.columns:
        return None

    d['_date_keep_ord'] = d['Ngày'].apply(normalize_schedule_date)
    d['_reason_keep_ord'] = d['Lý do nghỉ'].astype(str).apply(get_progressive_penalty_reason)
    same = d[(d['_date_keep_ord'] == ngay) & (d['_reason_keep_ord'] == canonical)].copy()
    if same.empty:
        return None

    # Cố gắng tìm đúng bản ghi theo source sheet + source row.
    src_id = str(original_row.get('__source_sheet_id', '') or '').strip()
    src_row = original_row.get('__source_row', None)
    if src_id and src_row not in [None, ''] and '__source_sheet_id' in same.columns and '__source_row' in same.columns:
        try:
            target_row = int(float(src_row))
            same['_src_row_num'] = pd.to_numeric(same['__source_row'], errors='coerce')
            same['_src_sheet_text'] = same['__source_sheet_id'].astype(str).str.strip()
            # Thứ tự lịch sử theo số dòng trong nguồn; nếu có nhiều nguồn thì giữ thứ tự ổn định
            # theo thứ tự hiện hữu trong DataFrame hợp nhất.
            same = same.reset_index(drop=False).rename(columns={'index': '_original_index'})
            match = same[(same['_src_sheet_text'] == src_id) & (same['_src_row_num'] == target_row)]
            if not match.empty:
                matched_original_index = match.iloc[0]['_original_index']
                # Dùng thứ tự xuất hiện trong tập cùng ngày/cùng loại.
                positions = list(same['_original_index'])
                return positions.index(matched_original_index) + 1
        except Exception:
            pass

    # Fallback theo khóa lịch nếu source metadata không còn.
    target_key = schedule_key(original_row)
    same = same.reset_index(drop=False).rename(columns={'index': '_original_index'})
    for idx, r in same.iterrows():
        if schedule_key(r) == target_key:
            return int(idx) + 1
    return None


def recalculate_schedule_fields(original_row, edited_row, updated_by, all_leave_data=None, source_df=None):
    """
    Tự động tính lại các cột phụ thuộc khi sửa lịch:
    - Số ngày tính: theo LoaiNghi
    - Số ngày phép cộng dồn: tổng tháng của nhân viên, loại bản ghi cũ rồi cộng giá trị mới
    - Phạt vi phạm: theo LoaiNghi + phạt lũy tiến nếu thuộc 3 nhóm vi phạm
    - Ngày/Giờ/Người cập nhật: theo thời điểm và tài khoản đang thao tác
    """
    catalog = build_leave_reason_catalog(source_df)
    result = edited_row.copy()

    ngay = normalize_schedule_date(result.get('Ngày', original_row.get('Ngày', '')))
    nv = str(result.get('Tên nhân viên', original_row.get('Tên nhân viên', ''))).strip()
    reason = str(result.get('Lý do nghỉ', original_row.get('Lý do nghỉ', ''))).replace('🔴 ', '').strip()
    key = normalize_leave_reason(reason)
    defaults = catalog.get(key)

    if defaults:
        reason = defaults['name']
        so_ngay = float(defaults['days'])
        base_penalty = float(defaults['penalty'])
    else:
        # Với dữ liệu lịch sử không còn trong LoaiNghi, giữ giá trị cũ để tránh làm mất dữ liệu.
        so_ngay = _parse_leave_number(original_row.get('Số ngày tính', result.get('Số ngày tính', 0)), 0.0)
        base_penalty = _parse_leave_number(original_row.get('Phạt vi phạm', result.get('Phạt vi phạm', 0)), 0.0, money=True)

    others = _exclude_original_from_leave_df(all_leave_data, original_row)

    # Tính số ngày phép cộng dồn trong cùng tháng/năm của đúng nhân viên.
    dt = pd.to_datetime(ngay, errors='coerce', dayfirst=True)
    accumulated = float(so_ngay)
    if pd.notna(dt) and others is not None and not others.empty:
        d = others.copy()
        d['_dt_calc'] = pd.to_datetime(d['Ngày'], errors='coerce', dayfirst=True)
        d['_days_calc'] = pd.to_numeric(d['Số ngày tính'], errors='coerce').fillna(0)
        same_emp = d['Tên nhân viên'].astype(str).apply(normalize_login_name).eq(normalize_login_name(nv))
        same_month = d['_dt_calc'].dt.month.eq(dt.month) & d['_dt_calc'].dt.year.eq(dt.year)
        accumulated = float(d.loc[same_emp & same_month, '_days_calc'].sum()) + float(so_ngay)

    # Phạt lũy tiến cho Nghỉ không phép / Đi trễ không phép / Về sớm không phép.
    final_penalty = float(base_penalty)
    detail = _strip_generated_progressive_prefix(result.get('Chi tiết', original_row.get('Chi tiết', '')))
    progressive_reason = get_progressive_penalty_reason(reason)
    if progressive_reason:
        # Nếu chỉ sửa nội dung nhưng vẫn cùng ngày + cùng nhóm vi phạm, giữ đúng
        # thứ tự Người Thứ đã ghi trước đó. Nếu đổi ngày/đổi loại thì tính lại thứ tự.
        ordinal = None
        original_reason = str(original_row.get('Lý do nghỉ', '')).replace('🔴 ', '').strip()
        original_canonical = get_progressive_penalty_reason(original_reason)
        original_date = normalize_schedule_date(original_row.get('Ngày', ''))

        # QUY TẮC QUAN TRỌNG KHI SỬA:
        # Nếu vẫn cùng NGÀY + cùng NHÓM VI PHẠM thì đây vẫn là cùng một người/lượt cũ.
        # Tuyệt đối không đẩy Người Thứ 1 thành Người Thứ 2/3 chỉ vì bấm Sửa/Lưu lại.
        if original_canonical == progressive_reason and original_date == ngay:
            ordinal = _get_existing_progressive_ordinal(original_row, all_leave_data)

        # Chỉ cấp thứ tự mới khi thực sự đổi ngày hoặc đổi sang nhóm vi phạm khác,
        # hoặc dữ liệu lịch sử quá cũ không thể xác định thứ tự cũ.
        if ordinal is None:
            ordinal, _ = _progressive_ordinal_and_bonus(others, ngay, reason)
        extra_penalty = max(0, int(ordinal) - 2) * 100000
        final_penalty += float(extra_penalty)
        ordinal_note = f"Người Thứ {ordinal} {progressive_reason.lower()}"
        detail = f"{ordinal_note} | {detail}" if detail else ordinal_note

    now_vn = datetime.now(VN_TZ)
    result['Ngày'] = ngay
    result['Tên nhân viên'] = nv
    result['Lý do nghỉ'] = reason
    result['Chi tiết'] = detail
    result['Số ngày tính'] = float(so_ngay)
    result['Số ngày phép cộng dồn'] = float(accumulated)
    result['Phạt vi phạm'] = float(final_penalty)
    result['Ngày cập nhật'] = now_vn.strftime('%d/%m/%Y')
    result['Giờ cập nhật'] = now_vn.strftime('%H:%M:%S')
    result['Người cập nhật'] = str(updated_by)
    return result


def _load_live_two_leave_sheets(client):
    """Đọc trực tiếp hai Google Sheet lịch nghỉ để tính/sửa bằng dữ liệu mới nhất."""
    primary = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
    secondary = client.open_by_key(SHEET_LICH_NGHI_2_ID).get_worksheet(0)

    df_primary = _live_sheet_to_leave_df(primary)
    if not df_primary.empty:
        df_primary['__source_sheet_id'] = SHEET_DU_PHONG_ID
        # Gắn row sheet theo thứ tự A:J đã đọc; dùng key vẫn là lớp dự phòng chính nếu có dòng trống.
        df_primary['__source_row'] = range(2, len(df_primary) + 2)

    df_secondary = _live_sheet_to_leave_df(secondary)
    if not df_secondary.empty:
        df_secondary['__source_sheet_id'] = SHEET_LICH_NGHI_2_ID
        df_secondary['__source_row'] = range(2, len(df_secondary) + 2)

    return combine_leave_sources_for_daily_stats(df_secondary, df_primary)


def _read_leave_sheet_with_source(sheet, source_id):
    """Đọc A:J và giữ chính xác số dòng vật lý của Google Sheet để có thể cập nhật lại đúng dòng."""
    expected = [
        "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
        "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
        "Giờ cập nhật", "Người cập nhật"
    ]
    try:
        values = _gs_call_with_backoff(sheet.get, 'A:J')
        rows = []
        if not values or len(values) < 2:
            return pd.DataFrame(columns=expected + ['__source_sheet_id', '__source_row'])
        for sheet_row, values_row in enumerate(values[1:], start=2):
            vals = list(values_row[:10]) + [""] * max(0, 10 - len(values_row))
            if not any(str(v).strip() for v in vals[:10]):
                continue
            item = dict(zip(expected, vals[:10]))
            item['__source_sheet_id'] = str(source_id)
            item['__source_row'] = int(sheet_row)
            rows.append(item)
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=expected + ['__source_sheet_id', '__source_row'])
    except Exception:
        return pd.DataFrame(columns=expected + ['__source_sheet_id', '__source_row'])


def _extract_progressive_ordinal(detail):
    """Lấy số X từ tiền tố 'Người Thứ X ...'."""
    try:
        m = re.search(r'Người\s+Thứ\s+(\d+)', str(detail or ''), flags=re.IGNORECASE)
        return max(1, int(m.group(1))) if m else None
    except Exception:
        return None


def _progressive_group_key(row):
    """Khóa nhóm phạt lũy tiến = (ngày, loại chuẩn)."""
    canonical = get_progressive_penalty_reason(row.get('Lý do nghỉ', ''))
    ngay = normalize_schedule_date(row.get('Ngày', ''))
    if not canonical or not ngay:
        return None
    return (str(ngay), str(canonical))


def _existing_base_penalty(row, catalog):
    """Lấy mức phạt gốc, tách phần lũy tiến khỏi tổng tiền hiện có khi cần."""
    reason = str(row.get('Lý do nghỉ', '')).replace('🔴 ', '').strip()
    key = normalize_leave_reason(reason)
    if key in catalog:
        return float(catalog[key].get('penalty', 0) or 0)

    canonical = get_progressive_penalty_reason(reason)
    if canonical:
        for item in catalog.values():
            if get_progressive_penalty_reason(item.get('name', '')) == canonical:
                return float(item.get('penalty', 0) or 0)

    current_total = _parse_leave_number(row.get('Phạt vi phạm', 0), 0.0, money=True)
    old_ordinal = _extract_progressive_ordinal(row.get('Chi tiết', ''))
    old_extra = max(0, int(old_ordinal or 1) - 2) * 100000
    return max(0.0, float(current_total) - float(old_extra))


def rebalance_progressive_penalty_groups(client, affected_groups, updated_by):
    """
    Xếp lại toàn bộ Người Thứ X và mức phạt lũy tiến của các nhóm bị ảnh hưởng.

    Ví dụ sau khi Người Thứ 1 bị xóa/đổi sang Có phép:
      cũ 2 -> mới 1
      cũ 3 -> mới 2 (bỏ +100.000)
      cũ 4 -> mới 3 (chỉ còn +100.000)
    và ghi ngược vào đúng Google Sheet/dòng vật lý.

    Nếu cùng một lịch xuất hiện ở cả hai nguồn, lịch đó chỉ chiếm 1 vị trí thứ tự,
    nhưng mọi bản sao vật lý của nó đều được cập nhật để hai nguồn nhất quán.
    """
    clean_groups = set()
    for item in affected_groups or []:
        try:
            ngay, canonical = item
            if ngay and canonical:
                clean_groups.add((str(ngay), str(canonical)))
        except Exception:
            continue
    if not clean_groups:
        return 0

    # Đọc dữ liệu LIVE, giữ row vật lý của cả hai nguồn.
    sheet_map = {
        SHEET_DU_PHONG_ID: client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0),
        SHEET_LICH_NGHI_2_ID: client.open_by_key(SHEET_LICH_NGHI_2_ID).get_worksheet(0),
    }
    frames = [
        _read_leave_sheet_with_source(sheet_map[SHEET_DU_PHONG_ID], SHEET_DU_PHONG_ID),
        _read_leave_sheet_with_source(sheet_map[SHEET_LICH_NGHI_2_ID], SHEET_LICH_NGHI_2_ID),
    ]
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return 0
    raw_all = pd.concat(frames, ignore_index=True)
    raw_all['_reb_date'] = raw_all['Ngày'].apply(normalize_schedule_date)
    raw_all['_reb_reason'] = raw_all['Lý do nghỉ'].astype(str).apply(get_progressive_penalty_reason)

    catalog = build_leave_reason_catalog(globals().get('df_loai_nghi', pd.DataFrame()))
    now_vn = datetime.now(VN_TZ)
    actor = str(updated_by or 'Hệ thống')
    update_date = now_vn.strftime('%d/%m/%Y')
    update_time = now_vn.strftime('%H:%M:%S')
    updated_physical_rows = 0

    for ngay, canonical in sorted(clean_groups):
        group = raw_all[(raw_all['_reb_date'] == ngay) & (raw_all['_reb_reason'] == canonical)].copy()
        if group.empty:
            continue

        # Một lịch logic = Ngày + Nhân viên + Lý do. Gom mọi bản sao vật lý của lịch đó.
        logical = {}
        for _, r in group.iterrows():
            key = schedule_key(r)
            logical.setdefault(key, []).append(r.copy())

        ordered = []
        for logical_key, physical_rows in logical.items():
            # Ưu tiên thứ tự Người Thứ X đã lưu; đây là thứ tự lịch sử đáng tin cậy nhất.
            ordinals = [
                _extract_progressive_ordinal(r.get('Chi tiết', ''))
                for r in physical_rows
            ]
            ordinals = [x for x in ordinals if x is not None]
            old_ordinal = min(ordinals) if ordinals else None

            # Representative ưu tiên Sheet dự phòng, rồi theo row vật lý.
            representative = sorted(
                physical_rows,
                key=lambda r: (
                    0 if str(r.get('__source_sheet_id', '')) == SHEET_DU_PHONG_ID else 1,
                    int(float(r.get('__source_row', 10**9) or 10**9)),
                )
            )[0]
            fallback_row = int(float(representative.get('__source_row', 10**9) or 10**9))
            ordered.append((old_ordinal, fallback_row, logical_key, representative, physical_rows))

        # Những bản ghi có Người Thứ cũ được giữ đúng trật tự cũ; dữ liệu rất cũ không có tiền tố xếp sau theo row.
        ordered.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 10**9, x[1], normalize_login_name(x[3].get('Tên nhân viên', ''))))

        for new_ordinal, (_, _, logical_key, representative, physical_rows) in enumerate(ordered, start=1):
            base_penalty = _existing_base_penalty(representative, catalog)
            extra_penalty = max(0, new_ordinal - 2) * 100000
            new_penalty = float(base_penalty) + float(extra_penalty)
            prefix = f"Người Thứ {new_ordinal} {canonical.lower()}"

            for physical in physical_rows:
                source_id = str(physical.get('__source_sheet_id', '')).strip()
                try:
                    row_idx = int(float(physical.get('__source_row')))
                except Exception:
                    continue
                target = sheet_map.get(source_id)
                if target is None:
                    continue

                user_note = _strip_generated_progressive_prefix(physical.get('Chi tiết', ''))
                new_detail = f"{prefix} | {user_note}" if user_note else prefix

                # Chỉ thay phần cần thiết; giữ nguyên Số ngày tính và Số ngày phép cộng dồn hiện có.
                e_val = physical.get('Số ngày tính', '')
                f_val = physical.get('Số ngày phép cộng dồn', '')
                values_d_to_j = [[
                    new_detail,
                    e_val,
                    f_val,
                    new_penalty,
                    update_date,
                    update_time,
                    actor,
                ]]
                gspread_update_range(target, f'D{row_idx}:J{row_idx}', values_d_to_j, value_input_option='USER_ENTERED')
                updated_physical_rows += 1

    if updated_physical_rows:
        _clear_dynamic_data_caches()
    return updated_physical_rows


def update_schedule_record(original_row, edited_row, updated_by):
    """
    Sửa đúng dòng ở Google Sheet nguồn của bản ghi đang hiển thị.
    Sau khi sửa, tự động xếp lại Người Thứ X/phạt lũy tiến của nhóm cũ và nhóm mới.
    """
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets."

        # Nhớ nhóm cũ trước khi thay đổi để sau đó có thể co lại thứ tự 2->1, 3->2...
        affected_groups = set()
        old_group = _progressive_group_key(original_row)
        if old_group:
            affected_groups.add(old_group)

        # Đọc LIVE cả hai nguồn để tránh dùng cache khi tính lại hoặc kiểm tra trùng.
        live_all = _load_live_two_leave_sheets(client)
        recalculated = recalculate_schedule_fields(
            original_row,
            edited_row,
            updated_by,
            all_leave_data=live_all,
            source_df=globals().get('df_loai_nghi', pd.DataFrame()),
        )

        ngay = normalize_schedule_date(recalculated.get('Ngày', ''))
        nv = str(recalculated.get('Tên nhân viên', '')).strip()
        lydo = str(recalculated.get('Lý do nghỉ', '')).replace('🔴 ', '').strip()
        if not nv or not lydo:
            return False, "Tên nhân viên và Lý do nghỉ không được để trống."

        # Không cho sửa thành một Ngày + Nhân viên + Loại nghỉ đã tồn tại ở bản ghi khác.
        others = _exclude_original_from_leave_df(live_all, original_row)
        if _leave_exists_in_sources(others, ngay, nv, lydo):
            return False, f"'{nv}' đã có loại nghỉ '{lydo}' trong ngày {ngay}. Không thể tạo lịch trùng khi sửa."

        new_values = [
            ngay,
            nv,
            lydo,
            str(recalculated.get('Chi tiết', '')).strip(),
            float(recalculated.get('Số ngày tính', 0) or 0),
            float(recalculated.get('Số ngày phép cộng dồn', 0) or 0),
            float(recalculated.get('Phạt vi phạm', 0) or 0),
            str(recalculated.get('Ngày cập nhật', '')),
            str(recalculated.get('Giờ cập nhật', '')),
            str(recalculated.get('Người cập nhật', updated_by)),
        ]

        source_id = str(original_row.get('__source_sheet_id', '')).strip() or SHEET_DU_PHONG_ID
        target = client.open_by_key(source_id).get_worksheet(0)
        row_idx = _find_schedule_row_index(target, original_row)
        if not row_idx:
            return False, "Không tìm thấy dòng tương ứng trong Google Sheet nguồn."
        gspread_update_range(target, f'A{row_idx}:J{row_idx}', [new_values], raw=False)

        # Nhóm mới cũng phải được chuẩn hóa. Nếu đổi Không phép -> Có phép thì new_group=None,
        # nhưng old_group vẫn được xếp lại để Người 2 trở thành Người 1, v.v.
        new_group = _progressive_group_key(recalculated)
        if new_group:
            affected_groups.add(new_group)

        rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, updated_by)

        _clear_dynamic_data_caches()
        if rebalanced:
            return True, f"Đã cập nhật lịch nghỉ và tự xếp lại thứ tự/phạt lũy tiến cho {rebalanced} bản ghi trong nhóm bị ảnh hưởng."
        return True, "Đã cập nhật lịch nghỉ thành công."
    except Exception as e:
        return False, f"Lỗi cập nhật lịch nghỉ: {e}"

def delete_schedule_records(original_rows, updated_by=None):
    """Xóa nhiều lịch đúng nguồn rồi tự xếp lại thứ tự/phạt của mọi nhóm vi phạm bị ảnh hưởng."""
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets."
        actor = str(updated_by or st.session_state.get("current_user", "Hệ thống"))

        affected_groups = set()
        grouped = {}
        for row in original_rows:
            group_key = _progressive_group_key(row)
            if group_key:
                affected_groups.add(group_key)
            source_id = str(row.get('__source_sheet_id', '')).strip() or SHEET_DU_PHONG_ID
            grouped.setdefault(source_id, []).append(row)

        deleted = 0
        for source_id, rows in grouped.items():
            target = client.open_by_key(source_id).get_worksheet(0)
            indices = []
            for row in rows:
                idx = _find_schedule_row_index(target, row)
                if idx:
                    indices.append(idx)
            for idx in sorted(set(indices), reverse=True):
                target.delete_rows(idx)
                deleted += 1

        rebalanced = rebalance_progressive_penalty_groups(client, affected_groups, actor) if affected_groups else 0

        _clear_dynamic_data_caches()
        if rebalanced:
            return True, f"Đã xóa {deleted} dòng và tự xếp lại thứ tự/phạt cho {rebalanced} bản ghi còn lại."
        return True, f"Đã xóa {deleted} dòng lịch nghỉ từ đúng Google Sheet nguồn."
    except Exception as e:
        return False, f"Lỗi xóa lịch nghỉ: {e}"


# --- HÀM TẢI FILE TỪ DRIVE ---
def download_file_from_google_drive(id, destination):
    """Tải file nhị phân từ Google Drive, hỗ trợ trang confirm của file lớn."""
    session = requests.Session()
    errors = []

    # Endpoint usercontent thường ổn định hơn với file Excel nhị phân công khai.
    urls = [
        f"https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={id}&confirm=t",
    ]

    for url in urls:
        try:
            response = session.get(url, stream=True, timeout=60, allow_redirects=True)
            response.raise_for_status()

            # Một số file lớn vẫn trả trang confirm; thử lấy token từ HTML/cookie.
            ctype = str(response.headers.get('Content-Type', '')).lower()
            if 'text/html' in ctype:
                html = response.text
                token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
                if not token:
                    m = re.search(r'confirm=([0-9A-Za-z_-]+)', html)
                    token = m.group(1) if m else None
                if token:
                    response = session.get(
                        "https://drive.google.com/uc",
                        params={'export': 'download', 'id': id, 'confirm': token},
                        stream=True, timeout=60, allow_redirects=True
                    )
                    response.raise_for_status()

            with open(destination, "wb") as f:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)

            if os.path.exists(destination) and os.path.getsize(destination) > 0:
                return destination
        except Exception as e:
            errors.append(str(e))
            try:
                if os.path.exists(destination):
                    os.remove(destination)
            except Exception:
                pass

    raise RuntimeError("Không tải được file Google Drive: " + " | ".join(errors[-2:]))


def _excel_col_letter(idx):
    n = idx + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


@st.cache_data(ttl=15, show_spinner=False)
def load_bang_tour_input():
    """Đọc sheet Input từ file Bảng Tour dạng .XLSM trên Google Drive."""
    temp_file = f"temp_bangtour_{os.getpid()}_{int(time.time())}.xlsm"
    try:
        download_file_from_google_drive(BANG_TOUR_FILE_ID, temp_file)

        # XLSM là gói ZIP. Nếu không phải ZIP thì gần như chắc chắn Google Drive
        # đã trả HTML (đăng nhập/xác nhận quyền truy cập), không phải file Excel.
        if not zipfile.is_zipfile(temp_file):
            preview = ""
            try:
                preview = open(temp_file, 'r', encoding='utf-8', errors='ignore').read(180)
            except Exception:
                pass
            hint = " Google Drive đang trả trang HTML thay vì file XLSM." if '<html' in preview.lower() else ""
            return pd.DataFrame(), (
                "File tải về không phải XLSM hợp lệ." + hint +
                " Hãy đặt quyền file thành 'Bất kỳ ai có đường liên kết' hoặc cấp quyền cho service account."
            )

        # File đã xác nhận là .xlsm nên dùng openpyxl trực tiếp; không dùng pyxlsb.
        raw = pd.read_excel(temp_file, sheet_name="Input", header=None, engine="openpyxl")
        if raw.empty:
            return pd.DataFrame(), "Sheet Input đang trống."

        # Hệ thống Tour Vera dùng dòng 20 làm header và dữ liệu từ dòng 21.
        header_idx = 19 if len(raw) > 19 else 0
        # VBA người dùng gửi có rule đến cột X -> giữ tối đa A:X.
        max_cols = min(24, raw.shape[1])
        raw = raw.iloc[:, :max_cols]
        header_vals = raw.iloc[header_idx].tolist()

        headers = []
        seen = {}
        for i, v in enumerate(header_vals):
            txt = "" if pd.isna(v) else str(v).strip()
            if not txt or txt.lower() == "nan":
                txt = _excel_col_letter(i)
            if txt in seen:
                seen[txt] += 1
                txt = f"{txt} ({_excel_col_letter(i)})"
            else:
                seen[txt] = 1
            headers.append(txt)

        df = raw.iloc[header_idx + 1:].copy()
        df.columns = headers
        df = df.dropna(how="all").reset_index(drop=True)
        df.attrs["excel_header_row"] = header_idx + 1
        # Lưu vị trí cột gốc Excel để việc đổi thứ tự hiển thị không làm sai rule màu.
        df.attrs["excel_col_index"] = {headers[i]: i for i in range(len(headers))}
        return df, ""
    except ValueError as e:
        return pd.DataFrame(), f"Không đọc được sheet Input trong file XLSM: {e}"
    except Exception as e:
        return pd.DataFrame(), f"Lỗi tải/đọc Bảng Tour XLSM: {e}"
    finally:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass


def _tour_text(v):
    if pd.isna(v):
        return ""
    return str(v).strip()


def _tour_num(v):
    try:
        if pd.isna(v) or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _find_tour_col(df, wanted):
    """Tìm tên cột theo kiểu không dấu/không phân biệt hoa thường."""
    wanted_norm = remove_vietnamese_accents(str(wanted)).casefold().strip()
    exact = []
    contains = []
    for c in df.columns:
        norm = remove_vietnamese_accents(str(c)).casefold().strip()
        if norm == wanted_norm:
            exact.append(c)
        elif wanted_norm in norm:
            contains.append(c)
    return exact[0] if exact else (contains[0] if contains else None)


def reorder_bang_tour_columns(df):
    """
    Chỉ đổi thứ tự HIỂN THỊ:
    Tên Nhân Viên -> Trạng Thái -> Thời gian còn lại -> các cột còn lại.
    Vị trí cột Excel gốc vẫn được giữ trong df.attrs để tô màu đúng.
    """
    if df.empty:
        return df

    name_col = _find_tour_col(df, "Tên nhân viên")
    status_col = _find_tour_col(df, "Trạng thái")
    remain_col = _find_tour_col(df, "Thời gian còn lại")

    cols = list(df.columns)
    moved = [c for c in [status_col, remain_col] if c is not None]
    base = [c for c in cols if c not in moved]

    if name_col and name_col in base:
        pos = base.index(name_col) + 1
        for c in reversed(moved):
            base.insert(pos, c)
    else:
        # Nếu workbook đổi tên cột thì vẫn ưu tiên đưa hai cột này ra đầu.
        base = moved + base

    out = df.loc[:, base].copy()
    out.attrs.update(df.attrs)
    return out


def _tour_norm_token(v):
    """Chuẩn hóa text Tour: không dấu, không phân biệt hoa/thường, bỏ _ và - thừa."""
    txt = remove_vietnamese_accents(_tour_text(v)).casefold()
    txt = txt.replace("_", " ").replace("-", " ")
    return " ".join(txt.split())


def prepare_bang_tour_display(df):
    """
    Chuẩn bị dữ liệu HIỂN THỊ cho Bảng Tour sau khi đọc từ file:
    - Tên Nhân Viên -> Trạng Thái -> Thời gian còn lại -> các cột khác.
    - DANG CHO -> Đang chờ; DANG THUC HIEN -> Đang thực hiện.
    - Mọi giá trị số hiển thị dạng SỐ NGUYÊN, không có phần thập phân.
    - None / NaN / NaT / <NA> được đổi thành ô trống thật sự.
    - Thời gian còn lại <= -15 được làm trống trên giao diện (không ghi ngược file nguồn).
    """
    out = reorder_bang_tour_columns(df).copy()

    # Ghi nhớ các dòng có thời gian <= -15 trước khi làm trống để không nhầm thành "Đang rảnh".
    expired_indices = set()
    remain_col = _find_tour_col(out, "Thời gian còn lại")
    if remain_col is not None:
        raw_remain = out[remain_col].apply(_tour_num)
        expired_indices = set(raw_remain[raw_remain.apply(lambda x: x is not None and x <= -15)].index.tolist())

        def fmt_remaining(v):
            n = _tour_num(v)
            if n is None or n <= -15:
                return ""
            return str(int(round(n)))

        out[remain_col] = out[remain_col].apply(fmt_remaining)

    status_col = _find_tour_col(out, "Trạng thái")
    if status_col is not None:
        def fmt_status(v):
            token = _tour_norm_token(v)
            if token == "dang cho":
                return "Đang chờ"
            if token == "dang thuc hien":
                return "Đang thực hiện"
            if pd.isna(v):
                return ""
            s = str(v).strip()
            return "" if s.casefold() in {"none", "nan", "nat", "<na>"} else s
        out[status_col] = out[status_col].apply(fmt_status)

    def clean_and_integer_tour_value(v):
        # Ẩn hoàn toàn giá trị thiếu thật sự.
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass

        if isinstance(v, str):
            s = v.strip()
            if s.casefold() in {"none", "nan", "nat", "<na>"}:
                return ""
            # Nếu chuỗi chỉ là một số thì hiển thị thành số nguyên.
            if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s):
                try:
                    return str(int(round(float(s))))
                except Exception:
                    return s
            return s

        # Giá trị số từ openpyxl/pandas -> số nguyên hiển thị.
        if isinstance(v, bool):
            return v
        try:
            if isinstance(v, numbers.Number):
                return str(int(round(float(v))))
        except Exception:
            pass
        return v

    for c in out.columns:
        out[c] = out[c].apply(clean_and_integer_tour_value)

    out.attrs.update(df.attrs)
    out.attrs["_tour_expired_indices"] = expired_indices
    return out

def calculate_bang_tour_stats(df):
    """Tính bảng thống kê số lượng cho Bảng Tour từ dữ liệu gốc vừa tải."""
    if df.empty:
        return pd.DataFrame(columns=["Chỉ số", "Số lượng"])

    status_col = _find_tour_col(df, "Trạng thái")
    remain_col = _find_tour_col(df, "Thời gian còn lại")
    work_col = _find_tour_col(df, "Đi làm")
    shift_col = _find_tour_col(df, "Vào ca")
    break_col = _find_tour_col(df, "Break")

    # Nếu tên cột workbook hơi khác, thử các biến thể thường gặp.
    if break_col is None:
        break_col = _find_tour_col(df, "Breaktime")
    if shift_col is None:
        shift_col = _find_tour_col(df, "Ca")

    blank = pd.Series([""] * len(df), index=df.index, dtype=object)
    status_s = df[status_col].apply(_tour_norm_token) if status_col else blank.copy()
    work_s = df[work_col].apply(_tour_norm_token) if work_col else blank.copy()
    shift_s = df[shift_col].apply(_tour_norm_token) if shift_col else blank.copy()
    break_s = df[break_col].apply(_tour_norm_token) if break_col else blank.copy()
    remain_num = df[remain_col].apply(_tour_num) if remain_col else pd.Series([None] * len(df), index=df.index)
    remain_num = pd.to_numeric(remain_num, errors='coerce')
    status_num = pd.to_numeric(df[status_col], errors='coerce') if status_col else pd.Series([float('nan')] * len(df), index=df.index)

    dang_thuc_hien_mask = status_s.eq("dang thuc hien")
    dang_cho_mask = status_s.eq("dang cho")

    # "Sắp xong": hỗ trợ đúng cả hai trường hợp dữ liệu:
    # - Nếu cột Trạng thái có giá trị số: đếm giá trị <= 30 theo yêu cầu.
    # - Với cấu trúc hiện tại Trạng thái là chữ DANG THUC HIEN: dùng Thời gian còn lại <= 30.
    #   Loại <= -15 vì các giá trị này được làm trống khỏi bảng hiển thị.
    sap_xong_mask = (
        (status_num.notna() & (status_num <= 30))
        | (dang_thuc_hien_mask & remain_num.notna() & (remain_num <= 30) & (remain_num > -15))
    )

    active_shift_mask = shift_s.isin(["ca 1", "ca 2"])
    di_lam_mask = work_s.eq("di lam")
    nghi_phep_mask = work_s.eq("nghi phep")
    idle_time_mask = remain_num.isna() | remain_num.eq(0)
    dang_ranh_mask = idle_time_mask & active_shift_mask & di_lam_mask

    ca1_mask = shift_s.eq("ca 1")
    ca2_mask = shift_s.eq("ca 2")
    break_mask = break_s.eq("break")

    dang_thuc_hien = int(dang_thuc_hien_mask.sum())
    dang_cho = int(dang_cho_mask.sum())
    sap_xong = int(sap_xong_mask.sum())
    dang_ranh = int(dang_ranh_mask.sum())
    co_the_len_tour = sap_xong + dang_ranh

    rows = [
        ("Có thể lên tour", co_the_len_tour),
        ("Đang thực hiện", dang_thuc_hien),
        ("Đang chờ", dang_cho),
        ("Sắp xong (≤ 30 phút)", sap_xong),
        ("Đang rảnh", dang_ranh),
        ("Đi làm", int(di_lam_mask.sum())),
        ("Nghỉ phép", int(nghi_phep_mask.sum())),
        ("Ca 1", int(ca1_mask.sum())),
        ("Ca 2", int(ca2_mask.sum())),
        ("Break", int(break_mask.sum())),
    ]
    return pd.DataFrame(rows, columns=["Chỉ số", "Số lượng"])


def style_bang_tour(df):
    """
    Tô nguyên dòng Bảng Tour theo quy tắc vận hành Vera.
    - Nghỉ phép: nền trắng, chữ mờ.
    - Đi làm: nền trắng, chữ đen.
    - Thời gian >= 15: xanh lá.
    - 0 <= thời gian < 15: vàng.
    - -15 < thời gian < 0: đỏ.
    - Đang rảnh: xanh nhạt + chữ đậm.
    - Break: cam (ưu tiên cao nhất).
    - Header: nền rgb(161,148,140) / #A1948C, chữ đen đậm.
    """
    remain_col = _find_tour_col(df, "Thời gian còn lại")
    work_col = _find_tour_col(df, "Đi làm")
    shift_col = _find_tour_col(df, "Vào ca")
    break_col = _find_tour_col(df, "Break")
    if break_col is None:
        break_col = _find_tour_col(df, "Breaktime")
    if shift_col is None:
        shift_col = _find_tour_col(df, "Ca")

    expired_indices = set(df.attrs.get("_tour_expired_indices", set()))

    def row_style(row):
        work_norm = _tour_norm_token(row.get(work_col, "")) if work_col else ""
        shift_norm = _tour_norm_token(row.get(shift_col, "")) if shift_col else ""
        break_norm = _tour_norm_token(row.get(break_col, "")) if break_col else ""
        remain_num = _tour_num(row.get(remain_col, "")) if remain_col else None

        bg = "#FFFFFF"
        fg = "#000000"
        weight = "400"

        if work_norm == "nghi phep":
            bg, fg, weight = "#FFFFFF", "#A6A6A6", "400"
        elif work_norm == "di lam":
            bg, fg, weight = "#FFFFFF", "#000000", "400"

        # Màu theo thời gian, không ghi đè dòng Nghỉ phép.
        if remain_num is not None and work_norm != "nghi phep":
            if remain_num >= 15:
                bg, fg, weight = "#92D050", "#000000", "600"
            elif 0 <= remain_num < 15:
                bg, fg, weight = "#FFD966", "#000000", "600"
            elif -15 < remain_num < 0:
                bg, fg, weight = "#FF6666", "#000000", "600"

        # Đang rảnh: thời gian trống/0 + Ca 1/Ca 2 + Đi làm; loại trừ dòng <= -15 đã bị làm trống.
        is_idle = (
            row.name not in expired_indices
            and work_norm == "di lam"
            and shift_norm in {"ca 1", "ca 2"}
            and (remain_num is None or remain_num == 0)
        )
        if is_idle:
            bg, fg, weight = "#D9EAD3", "#000000", "700"

        # Break ưu tiên cuối cùng.
        if break_norm == "break":
            bg, fg, weight = "#F4B183", "#000000", "700"

        css = (
            f"background-color:{bg};"
            f"color:{fg};"
            f"font-weight:{weight};"
            "white-space:nowrap;"
        )
        return [css] * len(row)

    styler = df.style.apply(row_style, axis=1).format(na_rep="")
    styler = styler.set_table_styles([
        {
            "selector": "th",
            "props": [
                ("background-color", "#A1948C"),
                ("color", "#000000"),
                ("font-weight", "700"),
                ("text-align", "center"),
                ("white-space", "normal"),
                ("overflow-wrap", "anywhere"),
                ("word-break", "break-word"),
                ("line-height", "1.15"),
            ],
        },
        {"selector": "td", "props": [("white-space", "nowrap")]},
    ])

    status_col = _find_tour_col(df, "Trạng thái")
    if status_col is not None:
        styler = styler.set_properties(
            subset=[status_col],
            **{"white-space": "nowrap", "min-width": "135px", "width": "135px"}
        )
    return styler

def combine_leave_sources_for_daily_stats(*sources):
    """
    Hợp nhất một hoặc nhiều nguồn lịch nghỉ. Loại trùng theo:
    Ngày + Tên nhân viên + Lý do nghỉ. Nguồn truyền vào SAU sẽ được ưu tiên
    khi cùng một bản ghi xuất hiện ở nhiều nguồn.
    """
    expected = [
        'Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính',
        'Số ngày phép cộng dồn', 'Phạt vi phạm', 'Ngày cập nhật',
        'Giờ cập nhật', 'Người cập nhật'
    ]
    meta_cols = ['__source_sheet_id', '__source_row']
    prepared = []
    for source in sources:
        if source is None or source.empty:
            continue
        d = source.copy()
        if 'Loại nghỉ' in d.columns and 'Lý do nghỉ' not in d.columns:
            d = d.rename(columns={'Loại nghỉ': 'Lý do nghỉ'})
        for col in expected:
            if col not in d.columns:
                d[col] = ""
        for col in meta_cols:
            if col not in d.columns:
                d[col] = ""
        d = d[expected + meta_cols].copy()
        d['Ngày'] = pd.to_datetime(d['Ngày'], errors='coerce', dayfirst=True).dt.date
        d = d.dropna(subset=['Ngày'])
        d['Số ngày tính'] = pd.to_numeric(d['Số ngày tính'], errors='coerce').fillna(0)
        d['Số ngày phép cộng dồn'] = pd.to_numeric(d['Số ngày phép cộng dồn'], errors='coerce').fillna(0)
        d['Phạt vi phạm'] = pd.to_numeric(d['Phạt vi phạm'], errors='coerce').fillna(0)
        prepared.append(d)

    if not prepared:
        return pd.DataFrame(columns=expected + meta_cols)

    combined = pd.concat(prepared, ignore_index=True)
    combined['_key'] = (
        combined['Ngày'].astype(str) + '|' +
        combined['Tên nhân viên'].apply(normalize_name).str.casefold() + '|' +
        combined['Lý do nghỉ'].astype(str).str.strip().str.casefold()
    )
    combined = combined.drop_duplicates(subset=['_key'], keep='last').drop(columns=['_key'])
    return combined.reset_index(drop=True)


@st.cache_data(ttl=60)
def load_lich_nghi(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV', 'LoaiNghi'], engine='pyxlsb')
        df_lich = xls['LichNghi'].iloc[:, :10]
        df_lich.columns = ['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Số ngày phép cộng dồn', 'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật']
        
        if os.path.exists(temp_file): os.remove(temp_file)
            
        def safe_date_parse(val):
            try:
                if pd.isna(val): return pd.NaT
                if hasattr(val, 'date'): return val.date() 
                if isinstance(val, (int, float)): return pd.to_datetime(val, unit='D', origin='1899-12-30').date()
                s = str(val).strip().split(' ')[0]
                return pd.to_datetime(s, dayfirst=True).date()
            except: return pd.NaT
                
        df_lich['Ngày'] = df_lich['Ngày'].apply(safe_date_parse)
        df_lich = df_lich.dropna(subset=['Ngày'])
        df_lich['Số ngày tính'] = pd.to_numeric(df_lich['Số ngày tính'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        df_lich['Phạt vi phạm'] = pd.to_numeric(df_lich['Phạt vi phạm'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        
        # FIX FORMAT NGÀY GIỜ CẬP NHẬT TỪ EXCEL SERIAL
        def format_excel_date(val):
            if pd.isna(val) or str(val).strip() == "": return ""
            try:
                if isinstance(val, (int, float)):
                    return pd.to_datetime(val, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
                if hasattr(val, 'strftime'): return val.strftime('%d/%m/%Y')
                return str(val).split(' ')[0]
            except: return str(val)

        def format_excel_time(val):
            if pd.isna(val) or str(val).strip() == "": return ""
            try:
                if isinstance(val, (int, float)):
                    s = int(round(val * 86400))
                    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
                if hasattr(val, 'strftime'): return val.strftime('%H:%M:%S')
                return str(val)
            except: return str(val)

        if 'Ngày cập nhật' in df_lich.columns:
            df_lich['Ngày cập nhật'] = df_lich['Ngày cập nhật'].apply(format_excel_date)
        if 'Giờ cập nhật' in df_lich.columns:
            df_lich['Giờ cập nhật'] = df_lich['Giờ cập nhật'].apply(format_excel_time)
            
        df_nv_excel = xls['DanhSachNV'].dropna(subset=['Tên nhân viên'])
        return df_lich, df_nv_excel, xls['LoaiNghi']
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

@st.cache_data(show_spinner=False)
def to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DuLieuLichNghi')
    return output.getvalue()


# ==========================================================
# THỐNG KÊ LƯƠNG - ADMIN; LỄ TÂN CHỈ ĐƯỢC XEM KHI ADMIN MỞ QUYỀN, KHÔNG CÓ DÒNG LƯƠNG
# ==========================================================
PAYROLL_COLUMNS = [
    "TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
    "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương",
    "Tiền hỗ trợ Locker", "Số tiền thực nhận", "Email",
    "Số tài khoản ngân hàng", "Tên ngân hàng", "Số dòng Tip"
]
PAYROLL_ADJUSTMENT_COLUMNS = [
    "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy", "Chi Phí Sinh Hoạt",
    "Tiền ứng lương", "Tiền hỗ trợ Locker"
]

# Tiêu đề hiển thị chuẩn cho toàn bộ bảng lương (web + Excel).
PAYROLL_DISPLAY_LABELS = {
    "TT": "TT",
    "Tên Hệ thống": "Tên Hệ thống",
    "Tiền Lương": "Tiền Lương",
    "Tiền Hỗ Trợ Hoàn Lại": "Hỗ Trợ Hoàn Lại",
    "Tích lũy": "Tích lũy",
    "Chi Phí Sinh Hoạt": "Phí Sinh Hoạt",
    "Tiền phạt trong tháng": "Vi phạm",
    "Vi phạm kỳ trước": "Vi phạm kỳ trước",
    "Tiền ứng lương": "Tiền ứng",
    "Tiền hỗ trợ Locker": "Tiền hỗ trợ Locker",
    "Số tiền thực nhận": "Thực nhận",
    "Số tài khoản ngân hàng": "Tài khoản ngân hàng",
    "Tên ngân hàng": "Tên ngân hàng",
    "Email": "Email",
}
PAYROLL_HISTORY_HEADERS = [
    "Mã bản lưu", "Từ ngày", "Đến ngày", "Ngày lưu", "Giờ lưu", "Người lưu", "Nguồn dữ liệu",
    "TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
    "Hỗ trợ dạy nghề", "Học phí", "Tích lũy", "Chi Phí Sinh Hoạt",
    "Tiền phạt trong tháng", "Tiền ứng lương", "Tiền hỗ trợ Locker", "Số tiền thực nhận",
    "Email", "Số tài khoản ngân hàng", "Tên ngân hàng", "Số dòng Tip", "Vi phạm kỳ trước"
]


def _get_or_create_worksheet(spreadsheet, title, rows=1000, cols=30):
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

# ==========================================================
# CẤU HÌNH HIỂN THỊ CỘT TOÀN HỆ THỐNG
# V37: thứ tự + độ rộng + độ cao dòng + font + kiểu chữ + căn lề + wrap text
# ==========================================================
TABLE_LAYOUT_LABELS = {
    "tour_main": "Bảng Tour",
    "staff_list": "Danh sách nhân sự",
    "payroll_current": "Bảng lương",
    "payroll_history": "Bảng lương đã lưu / chỉnh sửa",
    "leave_detail": "Chi tiết danh sách nghỉ",
    "leave_manage": "Quản lý lịch nghỉ",
}
TABLE_LAYOUT_STATIC_COLUMNS = {
    "staff_list": [
        "Tên nhân viên", "Họ và tên đầy đủ", "Phân quyền", "Trạng thái làm việc", "Điện thoại", "Email",
        "Địa chỉ", "Số tài khoản ngân hàng", "Tên ngân hàng", "Khóa đăng nhập"
    ],
    "payroll_current": [
        "TT", "Tên Hệ thống", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy",
        "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương",
        "Tiền hỗ trợ Locker", "Số tiền thực nhận", "Số tài khoản ngân hàng", "Tên ngân hàng", "Email"
    ],
    "payroll_history": [
        "TT", "Tên Hệ thống", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy",
        "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương",
        "Tiền hỗ trợ Locker", "Số tiền thực nhận", "Số tài khoản ngân hàng", "Tên ngân hàng", "Email"
    ],
    "leave_detail": [
        "Chọn", "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
        "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
    ],
    "leave_manage": [
        "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
        "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
    ],
}

TABLE_LAYOUT_FONT_OPTIONS = [
    "Roboto", "Arial", "Tahoma", "Verdana", "Times New Roman", "Georgia", "Courier New"
]
TABLE_LAYOUT_FONT_STYLE_OPTIONS = ["Thường", "Đậm", "Nghiêng", "Đậm + Nghiêng"]
TABLE_LAYOUT_ALIGN_OPTIONS = ["left", "center", "right"]
TABLE_LAYOUT_DEFAULT_ROW_HEIGHT = 36
TABLE_LAYOUT_DEFAULT_FONT_SIZE = 13


def _default_column_width(column_name):
    name = str(column_name)
    if name in {"TT", "Chọn"}: return 65
    if name in {"Ngày", "Giờ cập nhật", "Ngày cập nhật"}: return 115
    if name in {"Tên nhân viên", "Tên Hệ thống", "Phân quyền"}: return 150
    if "Email" in name: return 210
    if "Địa chỉ" in name or "Chi tiết" in name: return 240
    if "ngân hàng" in name.casefold(): return 190
    if any(x in name for x in ["Tiền", "Phạt", "Tích lũy", "Phí", "Số ngày"]): return 125
    return 140


def _default_column_alignment(column_name):
    name = str(column_name)
    if name in {"TT", "Chọn"}: return "center"
    if any(x in name for x in ["Tiền", "Phạt", "Tích lũy", "Phí", "Số ngày", "Thời gian còn lại"]):
        return "right"
    return "left"


def _default_column_visual_style(column_name):
    return {
        "font_family": "Roboto",
        "font_size": TABLE_LAYOUT_DEFAULT_FONT_SIZE,
        "font_style": "Thường",
        "align": _default_column_alignment(column_name),
        "wrap": True,
    }


@st.cache_resource(show_spinner=False)
def _ensure_ui_layout_storage():
    try:
        client = get_gspread_client()
        if not client:
            return None, "Chưa cấu hình quyền kết nối Google Sheets."
        ss = client.open_by_key(SHEET_MAT_KHAU_ID)
        ws = _get_or_create_worksheet(ss, UI_LAYOUT_WORKSHEET, rows=100, cols=8)
        if int(getattr(ws, "col_count", 0) or 0) < 7:
            _gs_call_with_backoff(ws.resize, cols=8)
        header = _gs_call_with_backoff(ws.row_values, 1)
        # Giữ 6 cột cũ để tương thích, thêm JSON kiểu hiển thị ở cột G.
        wanted = [
            "TableKey", "Tên bảng", "Thứ tự cột JSON", "Độ rộng cột JSON",
            "Cập nhật lúc", "Người cập nhật", "Kiểu hiển thị JSON"
        ]
        if header[:len(wanted)] != wanted:
            gspread_update_range(ws, "A1:G1", [wanted])
        return ws, ""
    except Exception as e:
        return None, f"Lỗi khởi tạo cấu hình cột: {e}"


@st.cache_data(ttl=300, show_spinner=False)
def load_table_layouts():
    ws, err = _ensure_ui_layout_storage()
    if err or ws is None:
        return {}, err
    try:
        values = _gs_call_with_backoff(ws.get_all_values)
        result = {}
        for row_idx, row in enumerate(values[1:], start=2):
            if not row or not str(row[0]).strip():
                continue
            key = str(row[0]).strip()
            try:
                order = json.loads(row[2]) if len(row) > 2 and str(row[2]).strip() else []
            except Exception:
                order = []
            try:
                widths = json.loads(row[3]) if len(row) > 3 and str(row[3]).strip() else {}
            except Exception:
                widths = {}
            try:
                visual = json.loads(row[6]) if len(row) > 6 and str(row[6]).strip() else {}
            except Exception:
                visual = {}
            result[key] = {
                "row": row_idx,
                "order": order if isinstance(order, list) else [],
                "widths": widths if isinstance(widths, dict) else {},
                "visual": visual if isinstance(visual, dict) else {},
                "updated_at": str(row[4]).strip() if len(row) > 4 else "",
                "updated_by": str(row[5]).strip() if len(row) > 5 else "",
            }
        return result, ""
    except Exception as e:
        return {}, f"Lỗi đọc cấu hình cột: {e}"


def _clear_table_layout_cache():
    try:
        load_table_layouts.clear()
    except Exception:
        pass


def get_table_layout(table_key, available_columns):
    available = [str(c) for c in available_columns]
    layouts, _ = load_table_layouts()
    cfg = layouts.get(str(table_key), {})
    saved_order = [str(c) for c in cfg.get("order", []) if str(c) in available]
    order = saved_order + [c for c in available if c not in saved_order]
    saved_widths = cfg.get("widths", {}) if isinstance(cfg.get("widths", {}), dict) else {}
    widths = {}
    for c in available:
        try:
            widths[c] = max(50, min(800, int(float(saved_widths.get(c, _default_column_width(c))))))
        except Exception:
            widths[c] = _default_column_width(c)
    return order, widths


def get_table_visual_settings(table_key, available_columns):
    """Đọc cấu hình kiểu hiển thị của bảng, tự bù mặc định cho cột mới."""
    available = [str(c) for c in available_columns]
    layouts, _ = load_table_layouts()
    cfg = layouts.get(str(table_key), {})
    raw = cfg.get("visual", {}) if isinstance(cfg.get("visual", {}), dict) else {}
    try:
        row_height = int(float(raw.get("row_height", TABLE_LAYOUT_DEFAULT_ROW_HEIGHT)))
    except Exception:
        row_height = TABLE_LAYOUT_DEFAULT_ROW_HEIGHT
    row_height = max(24, min(120, row_height))
    raw_cols = raw.get("columns", {}) if isinstance(raw.get("columns", {}), dict) else {}
    col_styles = {}
    for c in available:
        d = _default_column_visual_style(c)
        saved = raw_cols.get(c, {}) if isinstance(raw_cols.get(c, {}), dict) else {}
        font_family = str(saved.get("font_family", d["font_family"]))
        if font_family not in TABLE_LAYOUT_FONT_OPTIONS:
            font_family = d["font_family"]
        try:
            font_size = max(8, min(30, int(float(saved.get("font_size", d["font_size"])))))
        except Exception:
            font_size = d["font_size"]
        font_style = str(saved.get("font_style", d["font_style"]))
        if font_style not in TABLE_LAYOUT_FONT_STYLE_OPTIONS:
            font_style = d["font_style"]
        align = str(saved.get("align", d["align"])).lower()
        if align not in TABLE_LAYOUT_ALIGN_OPTIONS:
            align = d["align"]
        wrap = saved.get("wrap", d["wrap"])
        if isinstance(wrap, str):
            wrap = wrap.strip().lower() in {"1", "true", "yes", "y", "có", "co"}
        else:
            wrap = bool(wrap)
        col_styles[c] = {
            "font_family": font_family,
            "font_size": font_size,
            "font_style": font_style,
            "align": align,
            "wrap": wrap,
        }
    return row_height, col_styles


def layout_row_height(table_key, fallback=TABLE_LAYOUT_DEFAULT_ROW_HEIGHT):
    try:
        row_height, _ = get_table_visual_settings(table_key, [])
        return int(row_height)
    except Exception:
        return int(fallback)


def _font_style_css(style_name):
    style_name = str(style_name)
    if style_name == "Đậm":
        return "700", "normal"
    if style_name == "Nghiêng":
        return "400", "italic"
    if style_name == "Đậm + Nghiêng":
        return "700", "italic"
    return "400", "normal"


def apply_table_visual_styler(data_or_styler, table_key, columns=None):
    """
    Áp dụng font/cỡ chữ/kiểu chữ/căn lề/wrap cho bảng đọc (st.dataframe).
    Giữ nguyên Styler sẵn có để không làm mất màu điều kiện của Tour/Lịch nghỉ.
    """
    if isinstance(data_or_styler, pd.DataFrame):
        columns = list(data_or_styler.columns) if columns is None else list(columns)
        styler = data_or_styler.style
    else:
        styler = data_or_styler
        if columns is None:
            try:
                columns = list(styler.data.columns)
            except Exception:
                columns = []
        else:
            columns = list(columns)
    _, col_styles = get_table_visual_settings(table_key, columns)
    header_rules = []
    for idx, c in enumerate(columns):
        if c not in col_styles:
            continue
        cfg = col_styles[c]
        fw, fs = _font_style_css(cfg.get("font_style"))
        wrap = bool(cfg.get("wrap", True))
        props = {
            "font-family": f"'{cfg.get('font_family', 'Roboto')}', sans-serif",
            "font-size": f"{int(cfg.get('font_size', TABLE_LAYOUT_DEFAULT_FONT_SIZE))}px",
            "font-weight": fw,
            "font-style": fs,
            "text-align": cfg.get("align", "left"),
            "white-space": "normal" if wrap else "nowrap",
            "overflow-wrap": "anywhere" if wrap else "normal",
            "word-break": "break-word" if wrap else "normal",
        }
        try:
            styler = styler.set_properties(subset=[c], **props)
        except Exception:
            pass
        # Dòng tiêu đề cũng dùng font/căn lề theo cột và luôn cho phép wrap nếu cột hẹp.
        header_rules.append({
            "selector": f"th.col_heading.level0.col{idx}",
            "props": [
                ("font-family", f"'{cfg.get('font_family', 'Roboto')}', sans-serif"),
                ("font-size", f"{int(cfg.get('font_size', TABLE_LAYOUT_DEFAULT_FONT_SIZE))}px"),
                ("font-weight", fw), ("font-style", fs),
                ("text-align", cfg.get("align", "left")),
                ("white-space", "normal"), ("overflow-wrap", "anywhere"),
                ("word-break", "break-word"), ("line-height", "1.15"),
            ]
        })
    try:
        if header_rules:
            styler = styler.set_table_styles(header_rules, overwrite=False)
    except Exception:
        pass
    return styler


def table_layout_html_css(table_key, columns, selector):
    """Sinh CSS theo từng cột cho các bảng HTML (hiện dùng ở Bảng lương tổng hợp)."""
    row_height, col_styles = get_table_visual_settings(table_key, columns)
    css = [f"{selector} tbody tr{{height:{int(row_height)}px;}}"]
    for idx, c in enumerate(columns, start=1):
        cfg = col_styles.get(c, _default_column_visual_style(c))
        fw, fs = _font_style_css(cfg.get("font_style"))
        wrap = bool(cfg.get("wrap", True))
        white = "normal" if wrap else "nowrap"
        overflow = "anywhere" if wrap else "normal"
        word_break = "break-word" if wrap else "normal"
        font = str(cfg.get("font_family", "Roboto")).replace("'", "")
        size = int(cfg.get("font_size", TABLE_LAYOUT_DEFAULT_FONT_SIZE))
        align = cfg.get("align", "left")
        css.append(
            f"{selector} th:nth-child({idx}),{selector} td:nth-child({idx}){{"
            f"font-family:'{font}',sans-serif!important;font-size:{size}px!important;"
            f"font-weight:{fw}!important;font-style:{fs}!important;text-align:{align}!important;"
            f"white-space:{white}!important;overflow-wrap:{overflow}!important;word-break:{word_break}!important;}}"
        )
        # Tiêu đề luôn wrap để không bị mất chữ khi cột nhỏ.
        css.append(
            f"{selector} th:nth-child({idx}){{white-space:normal!important;overflow-wrap:anywhere!important;word-break:break-word!important;}}"
        )
    return "\n".join(css)


def apply_table_layout_df(df, table_key):
    if not isinstance(df, pd.DataFrame):
        return df, {}
    order, widths = get_table_layout(table_key, list(df.columns))
    return df[order].copy(), widths


def table_layout_column_config(table_key, columns, label_map=None):
    _, widths = get_table_layout(table_key, columns)
    label_map = label_map or {}
    cfg = {}
    for c in columns:
        try:
            cfg[c] = st.column_config.Column(label_map.get(c, c), width=int(widths.get(c, _default_column_width(c))))
        except Exception:
            cfg[c] = st.column_config.TextColumn(label_map.get(c, c), width="medium")
    return cfg


def layout_width(table_key, column_name, fallback=None):
    _, widths = get_table_layout(table_key, [column_name])
    value = int(widths.get(column_name, _default_column_width(column_name)))
    return value if value else (fallback or "medium")


def save_table_layout_config(table_key, order, widths, username, visual=None):
    ws, err = _ensure_ui_layout_storage()
    if err or ws is None:
        return False, err or "Không mở được sheet cấu hình cột."
    try:
        layouts, _ = load_table_layouts()
        cfg = layouts.get(table_key, {})
        row_idx = cfg.get("row")
        now = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")
        if visual is None:
            visual = cfg.get("visual", {}) if isinstance(cfg.get("visual", {}), dict) else {}
        values = [[
            table_key, TABLE_LAYOUT_LABELS.get(table_key, table_key),
            json.dumps(list(order), ensure_ascii=False),
            json.dumps({str(k): int(v) for k, v in widths.items()}, ensure_ascii=False),
            now, str(username), json.dumps(visual, ensure_ascii=False)
        ]]
        if row_idx:
            gspread_update_range(ws, f"A{row_idx}:G{row_idx}", values)
        else:
            _gs_call_with_backoff(ws.append_row, values[0], value_input_option="USER_ENTERED")
        _clear_table_layout_cache()
        return True, "Đã lưu cấu hình hiển thị và áp dụng cho toàn hệ thống."
    except Exception as e:
        return False, f"Lỗi lưu cấu hình cột: {e}"


def get_table_columns_for_settings(table_key):
    if table_key == "tour_main":
        try:
            dft, _ = load_bang_tour_input()
            if isinstance(dft, pd.DataFrame) and not dft.empty:
                return list(prepare_bang_tour_display(dft).columns)
        except Exception:
            pass
        return ["Tên Nhân Viên", "Trạng Thái", "Thời gian còn lại", "Đi làm", "Vào ca", "Break"]
    return list(TABLE_LAYOUT_STATIC_COLUMNS.get(table_key, []))


def _layout_editor_rows_from_saved(table_key, available_cols):
    current_order, current_widths = get_table_layout(table_key, available_cols)
    _, current_styles = get_table_visual_settings(table_key, current_order)
    rows = []
    for pos, col in enumerate(current_order, start=1):
        vis = current_styles.get(col, _default_column_visual_style(col))
        rows.append({
            "Tên cột": col,
            "Vị trí": pos,
            "Độ rộng (px)": int(current_widths.get(col, _default_column_width(col))),
            "Font chữ": vis.get("font_family", "Roboto"),
            "Cỡ chữ": int(vis.get("font_size", TABLE_LAYOUT_DEFAULT_FONT_SIZE)),
            "Kiểu chữ": vis.get("font_style", "Thường"),
            "Căn lề": vis.get("align", _default_column_alignment(col)),
            "Wrap text": bool(vis.get("wrap", True)),
        })
    return rows


def _layout_editor_on_change(table_key, editor_key, rows_state_key, version_state_key):
    """
    V37 - Đổi vị trí theo logic chèn:
    ví dụ cột ở vị trí 6 chuyển thành 3 => vị trí 3,4,5 cũ tự thành 4,5,6.
    Không bao giờ để trùng Vị trí.
    """
    rows = [dict(r) for r in st.session_state.get(rows_state_key, [])]
    if not rows:
        return
    widget_state = st.session_state.get(editor_key, {})
    edits = widget_state.get("edited_rows", {}) if isinstance(widget_state, dict) else {}
    if not isinstance(edits, dict) or not edits:
        return

    # Map row index -> tên cột từ trạng thái trước khi di chuyển.
    original_names = {i: str(r.get("Tên cột", "")) for i, r in enumerate(rows)}

    # Áp dụng các chỉnh sửa không phải vị trí trước.
    for idx_raw, changes in edits.items():
        try:
            idx = int(idx_raw)
        except Exception:
            continue
        if idx < 0 or idx >= len(rows) or not isinstance(changes, dict):
            continue
        target_name = original_names.get(idx, "")
        target_row = next((r for r in rows if str(r.get("Tên cột", "")) == target_name), None)
        if target_row is None:
            continue
        for field, value in changes.items():
            if field == "Vị trí":
                continue
            target_row[field] = value

    # Xử lý vị trí theo cơ chế remove + insert, rồi đánh lại 1..N.
    for idx_raw, changes in edits.items():
        if not isinstance(changes, dict) or "Vị trí" not in changes:
            continue
        try:
            idx = int(idx_raw)
            target_pos = int(float(changes.get("Vị trí")))
        except Exception:
            continue
        target_name = original_names.get(idx, "")
        if not target_name:
            continue
        ordered = sorted(rows, key=lambda r: int(float(r.get("Vị trí", 9999))))
        current_idx = next((i for i, r in enumerate(ordered) if str(r.get("Tên cột", "")) == target_name), None)
        if current_idx is None:
            continue
        target_pos = max(1, min(len(ordered), target_pos))
        item = ordered.pop(current_idx)
        ordered.insert(target_pos - 1, item)
        for pos, r in enumerate(ordered, start=1):
            r["Vị trí"] = pos
        rows = ordered

    # Chuẩn hóa kiểu dữ liệu.
    for pos, r in enumerate(sorted(rows, key=lambda x: int(float(x.get("Vị trí", 9999)))), start=1):
        r["Vị trí"] = pos
        try: r["Độ rộng (px)"] = max(50, min(800, int(float(r.get("Độ rộng (px)", 140)))))
        except Exception: r["Độ rộng (px)"] = 140
        try: r["Cỡ chữ"] = max(8, min(30, int(float(r.get("Cỡ chữ", TABLE_LAYOUT_DEFAULT_FONT_SIZE)))))
        except Exception: r["Cỡ chữ"] = TABLE_LAYOUT_DEFAULT_FONT_SIZE
        if str(r.get("Font chữ", "Roboto")) not in TABLE_LAYOUT_FONT_OPTIONS: r["Font chữ"] = "Roboto"
        if str(r.get("Kiểu chữ", "Thường")) not in TABLE_LAYOUT_FONT_STYLE_OPTIONS: r["Kiểu chữ"] = "Thường"
        if str(r.get("Căn lề", "left")) not in TABLE_LAYOUT_ALIGN_OPTIONS: r["Căn lề"] = "left"
        r["Wrap text"] = bool(r.get("Wrap text", True))

    st.session_state[rows_state_key] = sorted(rows, key=lambda x: int(x["Vị trí"]))
    st.session_state[version_state_key] = int(st.session_state.get(version_state_key, 0) or 0) + 1


@st.cache_resource(show_spinner=False)
def _ensure_payroll_storage():
    """
    Tạo/lấy sheet lưu lương + cấu hình CHỈ MỘT LẦN cho mỗi tiến trình Streamlit.
    V27 gọi hàm này lặp lại trong cùng một rerun, làm phát sinh nhiều request metadata/read.
    """
    client = get_gspread_client()
    if not client:
        return None, None, "Chưa cấu hình quyền kết nối Google Sheets."
    try:
        ss = client.open_by_key(SHEET_MAT_KHAU_ID)
        ws_pay = _get_or_create_worksheet(ss, PAYROLL_STORAGE_WORKSHEET, rows=3000, cols=30)
        ws_cfg = _get_or_create_worksheet(ss, PAYROLL_CONFIG_WORKSHEET, rows=30, cols=5)

        pay_header = _gs_call_with_backoff(ws_pay.row_values, 1)
        if not pay_header or pay_header[:len(PAYROLL_HISTORY_HEADERS)] != PAYROLL_HISTORY_HEADERS:
            gspread_update_range(ws_pay, "A1:Y1", [PAYROLL_HISTORY_HEADERS])

        cfg_vals = _gs_call_with_backoff(ws_cfg.get, 'A:B')
        if not cfg_vals:
            gspread_update_range(ws_cfg, "A1:B5", [
                ["Key", "Value"],
                ["letan_payroll_access", "0"],
                ["default_living_expense", "150000"],
                ["default_locker_support", "80000"],
                ["employee_payroll_overrides_json", "{}"],
            ])
        else:
            # Bổ sung key thiếu chỉ ở lần khởi tạo tài nguyên, không kiểm tra lại ở mỗi rerun.
            existing_keys = {str(r[0]).strip() for r in cfg_vals[1:] if r}
            additions = []
            if "default_living_expense" not in existing_keys:
                additions.append(["default_living_expense", "150000"])
            if "default_locker_support" not in existing_keys:
                additions.append(["default_locker_support", "80000"])
            if "employee_payroll_overrides_json" not in existing_keys:
                additions.append(["employee_payroll_overrides_json", "{}"])
            if "letan_payroll_access" not in existing_keys:
                additions.append(["letan_payroll_access", "0"])
            if additions:
                next_row = max(2, len(cfg_vals) + 1)
                gspread_update_range(ws_cfg, f"A{next_row}:B{next_row + len(additions) - 1}", additions)
        return ws_pay, ws_cfg, ""
    except Exception as e:
        return None, None, f"Lỗi khởi tạo vùng lưu bảng lương: {e}"


@st.cache_data(ttl=300, show_spinner=False)
def _load_payroll_config_rows_cached():
    """Một lần đọc A:B dùng chung cho quyền Lễ tân, mức mặc định và mức riêng NV."""
    _, ws_cfg, err = _ensure_payroll_storage()
    if err or ws_cfg is None:
        return [], err or "Không mở được sheet cấu hình."
    try:
        vals = _gs_call_with_backoff(ws_cfg.get, 'A:B')
        return vals or [], ""
    except Exception as e:
        return [], f"Lỗi đọc cấu hình lương: {e}"


def _payroll_config_dict():
    vals, err = _load_payroll_config_rows_cached()
    cfg = {}
    if vals:
        for row in vals[1:]:
            if row:
                cfg[str(row[0]).strip()] = row[1] if len(row) > 1 else ''
    return cfg, vals, err


def _payroll_config_key_rows(vals):
    rows = {}
    for idx, row in enumerate((vals or [])[1:], start=2):
        if row:
            rows[str(row[0]).strip()] = idx
    return rows


def get_payroll_letan_enabled():
    try:
        cfg, _, _ = _payroll_config_dict()
        value = str(cfg.get('letan_payroll_access', '0')).strip().lower()
        return value in {"1", "true", "yes", "on", "mở", "mo"}
    except Exception:
        return False


def set_payroll_letan_enabled(enabled):
    try:
        _, ws_cfg, err = _ensure_payroll_storage()
        if err or ws_cfg is None:
            return False, err or "Không mở được sheet cấu hình."
        _, vals, read_err = _payroll_config_dict()
        if read_err:
            return False, read_err
        key_rows = _payroll_config_key_rows(vals)
        target_row = key_rows.get('letan_payroll_access', max(2, len(vals) + 1))
        gspread_update_range(ws_cfg, f"A{target_row}:B{target_row}", [["letan_payroll_access", "1" if enabled else "0"]])
        _clear_payroll_config_cache()
        return True, "Đã mở quyền xem Bảng lương cho Lễ tân." if enabled else "Đã đóng quyền xem Bảng lương của Lễ tân."
    except Exception as e:
        return False, f"Lỗi cập nhật quyền Lễ tân: {e}"


def get_payroll_default_amounts():
    """Đọc hai mức mặc định từ snapshot cấu hình đã cache."""
    living, locker = 150000.0, 80000.0
    try:
        cfg, _, _ = _payroll_config_dict()
        living = _money_to_float(cfg.get('default_living_expense', living)) or living
        locker = _money_to_float(cfg.get('default_locker_support', locker)) or locker
    except Exception:
        pass
    return float(living), float(locker)


def set_payroll_default_amounts(living_expense, locker_support):
    try:
        _, ws_cfg, err = _ensure_payroll_storage()
        if err or ws_cfg is None:
            return False, err or "Không mở được sheet cấu hình."
        _, vals, read_err = _payroll_config_dict()
        if read_err:
            return False, read_err
        key_rows = _payroll_config_key_rows(vals)
        next_row = max(2, len(vals) + 1)
        updates = []
        for key, value in [
            ('default_living_expense', int(round(_money_to_float(living_expense)))),
            ('default_locker_support', int(round(_money_to_float(locker_support)))),
        ]:
            row_idx = key_rows.get(key)
            if row_idx is None:
                row_idx = next_row
                next_row += 1
            updates.append((row_idx, key, str(value)))
        # Hai write nhỏ, nhưng KHÔNG phát sinh thêm read nào.
        for row_idx, key, value in updates:
            gspread_update_range(ws_cfg, f"A{row_idx}:B{row_idx}", [[key, value]])
        _clear_payroll_config_cache()
        return True, "Đã lưu mức Chi phí sinh hoạt và Hỗ trợ Locker mặc định."
    except Exception as e:
        return False, f"Lỗi lưu mức mặc định: {e}"


def get_payroll_employee_overrides():
    """Đọc mức riêng từ cùng snapshot cấu hình cache, không gọi Sheets API thêm lần nữa."""
    try:
        cfg, _, _ = _payroll_config_dict()
        raw = cfg.get('employee_payroll_overrides_json', '{}') or '{}'
        data = json.loads(str(raw))
        if not isinstance(data, dict):
            return {}
        cleaned = {}
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            norm_key = normalize_login_name(key)
            if not norm_key:
                continue
            cleaned[norm_key] = {
                "name": str(value.get("name", key)).strip(),
                "living": float(_money_to_float(value.get("living", 0))),
                "locker": float(_money_to_float(value.get("locker", 0))),
            }
        return cleaned
    except Exception:
        return {}


def _write_payroll_employee_overrides(overrides):
    try:
        _, ws_cfg, err = _ensure_payroll_storage()
        if err or ws_cfg is None:
            return False, err or "Không mở được sheet cấu hình."
        _, vals, read_err = _payroll_config_dict()
        if read_err:
            return False, read_err
        key_rows = _payroll_config_key_rows(vals)
        target_row = key_rows.get('employee_payroll_overrides_json', max(2, len(vals) + 1))
        payload = json.dumps(overrides or {}, ensure_ascii=False, separators=(",", ":"))
        gspread_update_range(ws_cfg, f"A{target_row}:B{target_row}", [["employee_payroll_overrides_json", payload]])
        _clear_payroll_config_cache()
        return True, "Đã lưu mức riêng theo nhân viên."
    except Exception as e:
        return False, f"Lỗi lưu mức riêng theo nhân viên: {e}"


def set_payroll_employee_overrides(employee_names, living_expense, locker_support):
    names = [str(x).strip() for x in (employee_names or []) if str(x).strip()]
    if not names:
        return False, "Vui lòng chọn ít nhất 1 nhân viên."
    overrides = get_payroll_employee_overrides()
    living = int(round(_money_to_float(living_expense)))
    locker = int(round(_money_to_float(locker_support)))
    for name in names:
        key = normalize_login_name(name)
        overrides[key] = {"name": name, "living": living, "locker": locker}
    ok, msg = _write_payroll_employee_overrides(overrides)
    if ok:
        return True, f"Đã áp dụng mức riêng cho {len(names)} nhân viên."
    return ok, msg


def clear_payroll_employee_overrides(employee_names):
    names = [str(x).strip() for x in (employee_names or []) if str(x).strip()]
    if not names:
        return False, "Vui lòng chọn ít nhất 1 nhân viên."
    overrides = get_payroll_employee_overrides()
    removed = 0
    for name in names:
        key = normalize_login_name(name)
        if key in overrides:
            overrides.pop(key, None)
            removed += 1
    ok, msg = _write_payroll_employee_overrides(overrides)
    if ok:
        return True, f"Đã xóa mức riêng của {removed} nhân viên; các nhân viên này sẽ dùng mức mặc định chung."
    return ok, msg


def _apply_payroll_override_to_current_session(employee_names, living_expense, locker_support):
    """Cập nhật ngay bảng lương đang mở nếu đã tính trước đó."""
    cur = st.session_state.get('payroll_current_df')
    if not isinstance(cur, pd.DataFrame) or cur.empty or 'Tên Hệ thống' not in cur.columns:
        return
    selected = {normalize_login_name(x) for x in (employee_names or [])}
    d = cur.copy()
    mask = d['Tên Hệ thống'].apply(normalize_login_name).isin(selected)
    if mask.any():
        d.loc[mask, 'Chi Phí Sinh Hoạt'] = float(_money_to_float(living_expense))
        d.loc[mask, 'Tiền hỗ trợ Locker'] = float(_money_to_float(locker_support))
        st.session_state.payroll_current_df = recalculate_payroll_net(d)


def _money_to_float(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, numbers.Number):
        try: return float(value)
        except Exception: return 0.0
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat", "-"}:
        return 0.0
    neg = text.startswith('-')
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return 0.0
    val = float(digits)
    return -val if neg else val


def _filter_real_payroll_rows(df):
    """Loại các dòng tiêu đề/placeholder bị đọc nhầm thành nhân viên thật."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    d = df.copy()
    if 'Tên Hệ thống' in d.columns:
        bad_names = {"ten nhan vien", "ten he thong", "username", "user name"}
        mask_bad = d['Tên Hệ thống'].astype(str).apply(normalize_login_name).isin(bad_names)
        d = d[~mask_bad].copy()
    if 'TT' in d.columns:
        d = d.reset_index(drop=True)
        d['TT'] = range(1, len(d) + 1)
    return d



# ==========================================================
# TÍCH LŨY NHÂN VIÊN
# ==========================================================
TICHLUY_HEADER_ALIASES = {
    "STT": ["STT", "TT", "Thứ tự", "Số thứ tự"],
    "Tên nhân viên": ["Tên nhân viên", "Tên Hệ thống", "Nhân viên", "Username"],
    "Ngày bắt đầu làm": ["Ngày bắt đầu làm", "Ngày bắt đầu đi làm", "Ngày vào làm", "Ngày bắt đầu"],
    "Mục tiêu tích lũy": ["Mục tiêu tích lũy", "Mục tiêu", "Tổng cần tích lũy"],
    "Đã tích lũy": ["Đã tích lũy", "Tích lũy", "Số tiền tích lũy", "Đã đóng"],
    "Còn lại": ["Còn lại", "Còn phải tích lũy", "Số tiền còn lại"],
    "Kỳ gần nhất": ["Kỳ gần nhất", "Kỳ đóng gần nhất"],
    "Số tiền kỳ gần nhất": ["Số tiền kỳ gần nhất", "Tiền kỳ gần nhất"],
    "Chi tiết các kỳ": ["Chi tiết các kỳ", "Lịch sử kỳ", "Lịch sử tích lũy"],
}


def _tichluy_header_positions(header):
    """Map tên cột chuẩn -> index 0-based, chấp nhận các tên cột người dùng đã tạo trước đó."""
    positions = {}
    normalized = [normalize_login_name(x) for x in (header or [])]
    for canonical in TICHLUY_HEADERS:
        aliases = TICHLUY_HEADER_ALIASES.get(canonical, [canonical])
        alias_keys = {normalize_login_name(x) for x in aliases}
        for i, key in enumerate(normalized):
            if key in alias_keys:
                positions[canonical] = i
                break
    return positions


@st.cache_resource(show_spinner=False)
def _ensure_tichluy_sheet():
    """
    Lấy/tạo sheet TichLuy. Nếu người dùng đã tạo cột trước đó thì GIỮ NGUYÊN,
    chỉ bổ sung các cột hệ thống còn thiếu ở bên phải, tránh ghi đè dữ liệu hiện hữu.
    """
    client = get_gspread_client()
    if not client:
        return None, "Chưa cấu hình quyền Google Sheets."
    try:
        ss = client.open_by_key(SHEET_MAT_KHAU_ID)
        ws = _get_or_create_worksheet(ss, TICHLUY_WORKSHEET, rows=1000, cols=20)
        header = _gs_call_with_backoff(ws.row_values, 1)
        if not header or not any(str(x).strip() for x in header):
            gspread_update_range(ws, "A1:I1", [TICHLUY_HEADERS])
        else:
            positions = _tichluy_header_positions(header)
            # STT luôn ở cột A. Nếu sheet cũ chưa có STT, chèn một cột mới ở đầu
            # để KHÔNG ghi đè / làm mất cột Tên nhân viên hiện có.
            if "STT" not in positions or positions.get("STT") != 0:
                try:
                    _gs_call_with_backoff(ss.batch_update, {
                        "requests": [{
                            "insertDimension": {
                                "range": {
                                    "sheetId": ws.id, "dimension": "COLUMNS",
                                    "startIndex": 0, "endIndex": 1
                                },
                                "inheritFromBefore": False
                            }
                        }]
                    })
                    gspread_update_range(ws, "A1:A1", [["STT"]])
                    header = ["STT"] + list(header)
                except Exception:
                    # Fallback tương thích gspread cũ.
                    _gs_call_with_backoff(ws.insert_cols, [["STT"]], col=1)
                    header = ["STT"] + list(header)
                positions = _tichluy_header_positions(header)

            missing = [h for h in TICHLUY_HEADERS if h not in positions]
            if missing:
                start_col = len(header) + 1
                start_a1 = gspread.utils.rowcol_to_a1(1, start_col)
                end_a1 = gspread.utils.rowcol_to_a1(1, start_col + len(missing) - 1)
                gspread_update_range(ws, f"{start_a1}:{end_a1}", [missing])
        return ws, ""
    except Exception as e:
        return None, f"Không mở được sheet TichLuy: {e}"


@st.cache_data(ttl=120, show_spinner=False)
def load_tichluy_tracking():
    """Đọc TichLuy một lần/cache; tự nhận diện vị trí cột để tương thích sheet đã có."""
    try:
        ws, err = _ensure_tichluy_sheet()
        if err or ws is None:
            return pd.DataFrame(columns=TICHLUY_HEADERS)
        values = _gs_call_with_backoff(ws.get_all_values)
        if not values:
            return pd.DataFrame(columns=TICHLUY_HEADERS)
        header = values[0]
        positions = _tichluy_header_positions(header)
        rows = []
        # Dùng cache tài khoản hiện có để ẩn hoàn toàn quanly/letan/locker/tapvu/admin khỏi
        # nghiệp vụ TichLuy, kể cả khi sheet cũ vẫn còn dòng từ phiên bản trước.
        role_map = _credential_role_map(load_credentials())
        for sheet_row, row in enumerate(values[1:], start=2):
            if not any(str(v).strip() for v in row):
                continue
            item = {}
            for canonical in TICHLUY_HEADERS:
                pos = positions.get(canonical)
                item[canonical] = row[pos] if pos is not None and pos < len(row) else ''
            emp_key = normalize_login_name(item.get('Tên nhân viên', ''))
            emp_role = role_map.get(emp_key, 'nhanvien')
            if emp_role in TICHLUY_EXCLUDED_ROLES:
                continue
            item['__sheet_row'] = sheet_row
            rows.append(item)
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=TICHLUY_HEADERS + ['__sheet_row'])
    except Exception:
        return pd.DataFrame(columns=TICHLUY_HEADERS + ['__sheet_row'])


def _parse_vn_date(value):
    """Đọc ngày Việt Nam và cả Excel serial date (ví dụ 46088) từ Google Sheets."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    # Google Sheets có thể trả ngày dưới dạng số serial của Excel/Sheets.
    # Epoch tương thích Excel là 30/12/1899 (serial 1 = 31/12/1899).
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            serial = float(value)
            if 20000 <= serial <= 100000:
                return (date(1899, 12, 30) + timedelta(days=int(serial)))
    except Exception:
        pass

    text = str(value or '').strip()
    if not text or text.casefold() in {'nan','none','nat'}:
        return None

    # Trường hợp serial date được trả về dưới dạng chuỗi, ví dụ "46088" hoặc "46088.0".
    try:
        serial = float(text.replace(',', '.'))
        if 20000 <= serial <= 100000:
            return (date(1899, 12, 30) + timedelta(days=int(serial)))
    except Exception:
        pass

    for fmt in ('%d/%m/%Y','%d-%m-%Y','%Y-%m-%d','%d/%m/%y'):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    try:
        dt = pd.to_datetime(text, dayfirst=True, errors='coerce')
        return None if pd.isna(dt) else dt.date()
    except Exception:
        return None


def _tichluy_period_key(start_date, end_date):
    return f"{start_date.isoformat()}|{end_date.isoformat()}"


def _parse_tichluy_history(value):
    try:
        data = json.loads(str(value or '{}'))
        if not isinstance(data, dict):
            return {}
        return {str(k): float(_money_to_float(v)) for k, v in data.items()}
    except Exception:
        return {}


def _is_tichluy_completed(target_value, accumulated_value, remaining_value=''):
    """
    Xác định hồ sơ đã hoàn thành đóng Tích lũy.
    Dữ liệu Admin nhập tay ở D/E/F được xem là hoàn thành khi E >= D (>0).
    Khi đã hoàn thành, hệ thống tuyệt đối không tự sửa D/E/F/G/H/I của dòng đó.
    """
    try:
        target = float(_money_to_float(target_value))
        accumulated = float(_money_to_float(accumulated_value))
        return target > 0 and accumulated >= target
    except Exception:
        return False


def _first_pay_period_for_start(start_work_date):
    """Kỳ đầu theo ngày vào làm, dùng đúng chuẩn 01-15 / 16-cuối tháng."""
    return _official_payroll_period(
        start_work_date.year, start_work_date.month, 1 if start_work_date.day <= 15 else 2
    )


def _parse_tichluy_period_text(value):
    """Đọc cột Kỳ gần nhất dạng dd/mm/yyyy - dd/mm/yyyy hoặc yyyy-mm-dd|yyyy-mm-dd."""
    text = str(value or '').strip()
    if not text:
        return None, None
    if '|' in text:
        parts = [x.strip() for x in text.split('|', 1)]
    elif ' - ' in text:
        parts = [x.strip() for x in text.split(' - ', 1)]
    else:
        return None, None
    if len(parts) != 2:
        return None, None
    return _parse_vn_date(parts[0]), _parse_vn_date(parts[1])


def _select_preferred_tichluy_rows(tracking):
    """
    Sheet TichLuy của dữ liệu cũ có thể trùng Tên nhân viên.
    Chỉ chọn 1 dòng tốt nhất cho mỗi người:
    1) ưu tiên dòng có Ngày bắt đầu làm hợp lệ;
    2) ưu tiên dòng có Chi tiết các kỳ / Kỳ gần nhất;
    3) ưu tiên dòng có dữ liệu số Mục tiêu/Đã tích lũy/Còn lại;
    4) nếu vẫn bằng nhau, lấy dòng nằm dưới cùng (STT/sheet row mới hơn).
    """
    if tracking is None or tracking.empty:
        return tracking
    best = {}
    for _, r in tracking.iterrows():
        key = normalize_login_name(r.get('Tên nhân viên', ''))
        if not key:
            continue
        completed_ok = 1 if _is_tichluy_completed(
            r.get('Mục tiêu tích lũy', ''), r.get('Đã tích lũy', ''), r.get('Còn lại', '')
        ) else 0
        start_ok = 1 if _parse_vn_date(r.get('Ngày bắt đầu làm', '')) else 0
        hist = _parse_tichluy_history(r.get('Chi tiết các kỳ', ''))
        period_start, period_end = _parse_tichluy_period_text(r.get('Kỳ gần nhất', ''))
        history_ok = 1 if hist or (period_start and period_end) else 0
        numeric_ok = 0
        for c in ('Mục tiêu tích lũy', 'Đã tích lũy', 'Còn lại'):
            raw = str(r.get(c, '') or '').strip()
            if raw not in {'', '-', 'None', 'nan'}:
                numeric_ok += 1
        try:
            sheet_row = int(r.get('__sheet_row', 0) or 0)
        except Exception:
            sheet_row = 0
        # Dòng đã hoàn thành do Admin nhập tay luôn được ưu tiên nếu tên bị trùng.
        score = (completed_ok, start_ok, history_ok, numeric_ok, sheet_row)
        if key not in best or score > best[key][0]:
            best[key] = (score, r)
    if not best:
        return tracking.iloc[0:0].copy()
    return pd.DataFrame([item[1] for item in best.values()]).reset_index(drop=True)


def get_tichluy_charge_map(start_date, end_date, employee_names=None, for_existing_snapshot=False):
    """
    Số Tích lũy tự động của kỳ:
    - mục tiêu mặc định 5.000.000;
    - mỗi kỳ 500.000, kỳ cuối chỉ thu phần còn thiếu;
    - kỳ đầu tiên kể từ ngày bắt đầu làm: nếu số ngày từ ngày vào làm đến cuối kỳ < 10 thì không thu;
    - khi một kỳ đã được ghi nhận trong TichLuy, bảng lương MỚI không thu lại;
      còn bản lương lịch sử đang sửa giữ đúng số của kỳ đã ghi nhận.
    """
    tracking = _select_preferred_tichluy_rows(load_tichluy_tracking())
    wanted = {normalize_login_name(x) for x in (employee_names or []) if str(x).strip()} if employee_names else None
    result, info = {}, {}
    if tracking is None or tracking.empty:
        return result, info
    period_key = _tichluy_period_key(start_date, end_date)
    for _, r in tracking.iterrows():
        name = str(r.get('Tên nhân viên','')).strip()
        key = normalize_login_name(name)
        if not key or (wanted is not None and key not in wanted):
            continue
        start_work = _parse_vn_date(r.get('Ngày bắt đầu làm',''))
        target = float(_money_to_float(r.get('Mục tiêu tích lũy', TICHLUY_TARGET_DEFAULT)) or TICHLUY_TARGET_DEFAULT)
        accumulated = float(_money_to_float(r.get('Đã tích lũy',0)))
        completed_manual = _is_tichluy_completed(
            r.get('Mục tiêu tích lũy', target), r.get('Đã tích lũy', accumulated), r.get('Còn lại', '')
        )
        # Dòng đã hoàn thành do Admin nhập tay được khóa nghiệp vụ: luôn còn lại = 0,
        # không phụ thuộc F đang để trống, dấu '-' hay số cũ.
        if completed_manual:
            remaining = 0.0
        else:
            # Cột F = Còn lại là nguồn ưu tiên nếu có số hợp lệ; nếu trống / '-' thì suy ra D - E.
            remaining_raw = str(r.get('Còn lại', '') or '').strip()
            if remaining_raw and remaining_raw not in {'-', '–', '—'}:
                remaining = max(0.0, float(_money_to_float(remaining_raw)))
                # Nếu E đang trống nhưng F có dữ liệu thì suy ngược Đã tích lũy để giữ D/E/F nhất quán.
                if not str(r.get('Đã tích lũy', '') or '').strip():
                    accumulated = max(0.0, target - remaining)
            else:
                remaining = max(0.0, target - accumulated)
        hist = _parse_tichluy_history(r.get('Chi tiết các kỳ',''))
        history_total, history_keys = _matching_tichluy_history(hist, start_date, end_date)
        # Một kỳ chính thức chỉ thu tối đa mức kỳ. Nếu bản cũ từng tạo nhiều key 16→ngày hiện tại
        # thì chỉ coi là đã đóng tối đa một kỳ, không thu thêm lần nữa.
        existing_amount = min(float(TICHLUY_PERIOD_DEFAULT), max(0.0, history_total))

        # Tương thích dữ liệu TichLuy cũ: có thể chưa có JSON "Chi tiết các kỳ"
        # nhưng đã có "Kỳ gần nhất" + "Số tiền kỳ gần nhất".
        if existing_amount <= 0:
            last_start, last_end = _parse_tichluy_period_text(r.get('Kỳ gần nhất', ''))
            if last_start and last_end:
                last_match = (last_start == start_date and last_end == end_date)
                if not last_match and _is_official_payroll_period(start_date, end_date):
                    cls, cle = _canonicalize_payroll_period(last_start, last_end)
                    last_match = (cls == start_date and cle == end_date)
                if last_match:
                    existing_amount = min(
                        float(TICHLUY_PERIOD_DEFAULT),
                        max(0.0, float(_money_to_float(r.get('Số tiền kỳ gần nhất', 0))))
                    )

        charge = 0.0
        reason = ''
        if existing_amount > 0:
            charge = existing_amount if for_existing_snapshot else 0.0
            reason = 'Kỳ này đã được ghi nhận trước đó.'
        elif remaining <= 0:
            reason = 'Đã đủ mục tiêu tích lũy.'
        elif start_work is None:
            # Dữ liệu TichLuy cũ có nhiều nhân viên đã tồn tại trước khi hệ thống bắt đầu
            # lưu Ngày bắt đầu làm. Nếu D/E/F cho thấy vẫn còn phải tích lũy thì vẫn thu
            # theo kỳ; chỉ các nhân viên mới (được thêm từ hệ thống) mới cần áp dụng chính
            # xác quy tắc kỳ đầu <10 ngày vì các dòng mới luôn có ngày bắt đầu làm.
            charge = min(float(TICHLUY_PERIOD_DEFAULT), remaining)
            reason = 'Hồ sơ cũ chưa có Ngày bắt đầu làm; thu theo số Còn lại trong TichLuy.'
        elif end_date < start_work:
            reason = 'Kỳ lương trước ngày bắt đầu làm.'
        else:
            first_start, first_end = _first_pay_period_for_start(start_work)
            is_first_period = not (end_date < first_start or start_date > first_end)
            first_days = (first_end - start_work).days + 1
            if is_first_period and first_days < 10:
                reason = f'Kỳ đầu chỉ có {first_days} ngày kể từ ngày bắt đầu làm (<10 ngày), tạm không thu.'
            else:
                charge = min(float(TICHLUY_PERIOD_DEFAULT), remaining)
                reason = 'Thu tích lũy theo kỳ.'
        result[key] = float(charge)
        info[key] = {
            'name': name, 'start_date': start_work, 'target': target, 'accumulated': accumulated,
            'remaining': remaining, 'charge': float(charge), 'reason': reason,
            'sheet_row': int(r.get('__sheet_row', 0) or 0),
        }
    return result, info



def _credential_role_map(credentials_df=None):
    """Tên đăng nhập chuẩn hóa -> vai trò, dùng chung để lọc danh sách nghiệp vụ."""
    credentials_df = load_credentials() if credentials_df is None else credentials_df
    result = {}
    if credentials_df is None or credentials_df.empty:
        return result
    for _, r in credentials_df.iterrows():
        key = normalize_login_name(r.get('Tên nhân viên', ''))
        if key:
            result[key] = str(r.get('Phân quyền', 'nhanvien')).strip().lower()
    return result


def get_leave_eligible_employee_names(credentials_df, excel_df=None):
    """Danh sách đăng ký nghỉ: ẩn letan, locker, tapvu kể cả khi tên còn nằm trong file Excel cũ."""
    role_map = _credential_role_map(credentials_df)
    excluded_names = {k for k, role in role_map.items() if role in LEAVE_EXCLUDED_ROLES}
    names = []
    if credentials_df is not None and not credentials_df.empty and 'Tên nhân viên' in credentials_df.columns:
        for _, r in credentials_df.iterrows():
            name = str(r.get('Tên nhân viên', '')).strip()
            role = str(r.get('Phân quyền', 'nhanvien')).strip().lower()
            if name and role not in LEAVE_EXCLUDED_ROLES:
                names.append(name)
    if excel_df is not None and not excel_df.empty and 'Tên nhân viên' in excel_df.columns:
        for name in excel_df['Tên nhân viên'].dropna().astype(str).str.strip().tolist():
            if name and normalize_login_name(name) not in excluded_names:
                names.append(name)
    # Giữ tên hiển thị gốc nhưng loại trùng theo chuẩn hóa.
    by_key = {}
    for name in names:
        by_key.setdefault(normalize_login_name(name), name)
    return sorted(by_key.values(), key=lambda x: normalize_login_name(x))


def renumber_credential_sheet_stt(sheet=None):
    """Đánh lại STT cột A của Sheet1 theo các tài khoản có tên ở cột B, chỉ 1 read + 1 write."""
    try:
        if sheet is None:
            client = get_gspread_client()
            if not client:
                return False, 'Chưa cấu hình Google Sheets.'
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        names = _gs_call_with_backoff(sheet.col_values, 2)
        if len(names) <= 1:
            return True, 'Sheet1 chưa có nhân viên để đánh STT.'
        seq = 0
        values = []
        for name in names[1:]:
            if str(name).strip():
                seq += 1
                values.append([seq])
            else:
                values.append([''])
        if values:
            gspread_update_range(sheet, f"A2:A{len(values)+1}", values, value_input_option='USER_ENTERED')
        return True, f'Đã sắp xếp lại STT Sheet1: {seq} tài khoản.'
    except Exception as e:
        return False, f'Lỗi đánh STT Sheet1: {e}'


def sync_tichluy_roles_and_stt(credentials_df=None):
    """
    Đồng bộ TichLuy với danh sách nhân viên hiện tại nhưng KHÔNG sửa dữ liệu tích lũy đã nhập tay:
    - chỉ giữ các vai trò được phép tham gia TichLuy;
    - tự thêm các nhân viên hiện tại còn thiếu vào cuối sheet;
    - KHÔNG ghi đè D/E/F của bất kỳ dòng đã có;
    - đặc biệt dòng đã hoàn thành (E >= D > 0) tuyệt đối không bị hệ thống sửa D:I;
    - cột A được đánh lại từ dòng 2: 1,2,3... theo đúng thứ tự dòng hiện tại.
    """
    try:
        credentials_df = load_credentials() if credentials_df is None else credentials_df
        role_map = _credential_role_map(credentials_df)
        ws, err = _ensure_tichluy_sheet()
        if err or ws is None:
            return False, err or 'Không mở được TichLuy.'

        values = _gs_call_with_backoff(ws.get_all_values)
        if not values:
            values = [list(TICHLUY_HEADERS)]
        header = values[0]
        pos = _tichluy_header_positions(header)
        name_pos = pos.get('Tên nhân viên')
        if name_pos is None:
            return False, 'TichLuy chưa có cột Tên nhân viên.'

        # Danh sách nhân viên hiện tại cần có trong TichLuy, giữ đúng thứ tự Sheet1.
        eligible = []
        eligible_keys = set()
        if isinstance(credentials_df, pd.DataFrame) and not credentials_df.empty:
            for _, cr in credentials_df.iterrows():
                name = str(cr.get('Tên nhân viên', '')).strip()
                role = str(cr.get('Phân quyền', 'nhanvien')).strip().lower()
                key = normalize_login_name(name)
                if not key or role in TICHLUY_EXCLUDED_ROLES:
                    continue
                if key in {'ten nhan vien', 'ten he thong', 'username', 'user name'} or key in eligible_keys:
                    continue
                eligible.append((name, key))
                eligible_keys.add(key)

        # Chỉ xóa dòng thuộc tài khoản hiện tại có role bị loại hoặc tên không còn tồn tại.
        # Không chỉnh D:I ở những dòng được giữ lại.
        delete_sheet_rows = []
        existing_keys = set()
        kept_names = []
        for sheet_row, row in enumerate(values[1:], start=2):
            name = row[name_pos] if name_pos < len(row) else ''
            if not str(name).strip():
                continue
            key = normalize_login_name(name)
            role = role_map.get(key)
            if key not in eligible_keys or role in TICHLUY_EXCLUDED_ROLES:
                delete_sheet_rows.append(sheet_row)
            else:
                existing_keys.add(key)
                kept_names.append(str(name).strip())

        if delete_sheet_rows:
            client = get_gspread_client()
            ss = client.open_by_key(SHEET_MAT_KHAU_ID)
            requests = []
            for r in sorted(delete_sheet_rows, reverse=True):
                requests.append({
                    'deleteDimension': {
                        'range': {
                            'sheetId': ws.id, 'dimension': 'ROWS',
                            'startIndex': r - 1, 'endIndex': r
                        }
                    }
                })
            _gs_call_with_backoff(ss.batch_update, {'requests': requests})

        # Tự thêm những nhân viên hiện tại còn thiếu. Với nhân sự cũ chưa có ngày vào làm,
        # để C trống để Admin bổ sung; D/E/F có mặc định và có thể nhập tay nếu đã hoàn thành.
        missing = [(name, key) for name, key in eligible if key not in existing_keys]
        if missing:
            rows_to_append = []
            for name, key in missing:
                row = [''] * len(header)
                defaults = {
                    'STT': '',
                    'Tên nhân viên': name,
                    'Ngày bắt đầu làm': '',
                    'Mục tiêu tích lũy': TICHLUY_TARGET_DEFAULT,
                    'Đã tích lũy': 0,
                    'Còn lại': TICHLUY_TARGET_DEFAULT,
                    'Kỳ gần nhất': '',
                    'Số tiền kỳ gần nhất': 0,
                    'Chi tiết các kỳ': '{}',
                }
                for canonical, value in defaults.items():
                    idx = pos.get(canonical)
                    if idx is not None and idx < len(row):
                        row[idx] = value
                rows_to_append.append(row)
            # Một request thay vì append_row từng nhân viên để giảm quota.
            start_row = max(2, len(values) - len(delete_sheet_rows) + 1)
            end_row = start_row + len(rows_to_append) - 1
            end_col = gspread.utils.rowcol_to_a1(1, len(header)).rstrip('1')
            gspread_update_range(ws, f'A{start_row}:{end_col}{end_row}', rows_to_append, value_input_option='USER_ENTERED')

        # Sau xóa/thêm, đọc đúng cột Tên nhân viên và đánh STT A2 = 1,2,3... theo từng dòng thực tế.
        # Nếu có dòng trống xen giữa thì A của dòng đó để trống; các dòng có tên vẫn chạy số liên tục.
        # Đây là cột duy nhất hệ thống luôn được phép thay đổi trên các dòng đã hoàn thành.
        names_after = _gs_call_with_backoff(ws.col_values, name_pos + 1)
        stt_values = []
        seq = 0
        for name in names_after[1:] if len(names_after) > 1 else []:
            if str(name).strip():
                seq += 1
                stt_values.append([seq])
            else:
                stt_values.append([''])
        if stt_values:
            gspread_update_range(ws, f'A2:A{len(stt_values) + 1}', stt_values, value_input_option='USER_ENTERED')

        try:
            load_tichluy_tracking.clear()
        except Exception:
            pass
        return True, (
            f'Đã đồng bộ TichLuy: {seq} dòng nhân viên, thêm {len(missing)} người còn thiếu; '
            'D/E/F của các dòng hiện có được giữ nguyên.'
        )
    except Exception as e:
        return False, f'Lỗi đồng bộ STT/TichLuy: {e}'

def ensure_employee_in_tichluy(employee_name, start_work_date=None):
    """Thêm nhân viên vào TichLuy nếu chưa có; ngày bắt đầu = ngày tạo tài khoản."""
    try:
        name = str(employee_name or '').strip()
        if not name:
            return False, 'Thiếu tên nhân viên.'
        start_work_date = start_work_date or get_vn_today()
        ws, err = _ensure_tichluy_sheet()
        if err or ws is None:
            return False, err or 'Không mở được TichLuy.'
        values = _gs_call_with_backoff(ws.get_all_values)
        header = values[0] if values else list(TICHLUY_HEADERS)
        pos = _tichluy_header_positions(header)
        key = normalize_login_name(name)
        name_pos = pos.get('Tên nhân viên', 0)
        for row in values[1:] if values else []:
            if name_pos < len(row) and normalize_login_name(row[name_pos]) == key:
                return True, 'Nhân viên đã có trong TichLuy.'
        row = [''] * len(header)
        defaults = {
            'STT': '',
            'Tên nhân viên': name,
            'Ngày bắt đầu làm': start_work_date.strftime('%d/%m/%Y'),
            'Mục tiêu tích lũy': TICHLUY_TARGET_DEFAULT,
            'Đã tích lũy': 0,
            'Còn lại': TICHLUY_TARGET_DEFAULT,
            'Kỳ gần nhất': '', 'Số tiền kỳ gần nhất': 0, 'Chi tiết các kỳ': '{}'
        }
        for canonical, value in defaults.items():
            if canonical in pos:
                row[pos[canonical]] = value
        ws.append_row(row, value_input_option='USER_ENTERED')
        try: load_tichluy_tracking.clear()
        except Exception: pass
        return True, 'Đã thêm vào TichLuy.'
    except Exception as e:
        return False, f'Lỗi thêm TichLuy: {e}'


def ensure_employee_in_leave_employee_list(employee_name, start_work_date=None):
    """
    Đồng bộ nhân viên mới sang file lịch nghỉ 1Kz0... vào sheet DanhSachNV.
    Không chèn dòng giả vào Sheet1 A:J vì Sheet1 là dữ liệu lịch nghỉ nghiệp vụ.
    """
    try:
        name = str(employee_name or '').strip()
        if not name:
            return False, 'Thiếu tên nhân viên.'
        start_work_date = start_work_date or get_vn_today()
        client = get_gspread_client()
        if not client:
            return False, 'Chưa cấu hình Google Sheets.'
        ss = client.open_by_key(SHEET_DU_PHONG_ID)
        ws = None
        for title in ('DanhSachNV', 'Danh sách NV', 'NhanVien', 'Nhân viên'):
            try:
                ws = ss.worksheet(title)
                break
            except Exception:
                pass
        if ws is None:
            ws = ss.add_worksheet(title='DanhSachNV', rows=1000, cols=5)
        header = _gs_call_with_backoff(ws.row_values, 1)
        if not header or normalize_login_name(header[0] if header else '') not in {'ten nhan vien','ten he thong'}:
            gspread_update_range(ws, 'A1:B1', [['Tên nhân viên','Ngày bắt đầu làm']])
        values = _gs_call_with_backoff(ws.get, 'A:B')
        key = normalize_login_name(name)
        for row in values[1:] if values else []:
            if row and normalize_login_name(row[0]) == key:
                return True, 'Nhân viên đã có trong DanhSachNV của file lịch nghỉ.'
        ws.append_row([name, start_work_date.strftime('%d/%m/%Y')], value_input_option='USER_ENTERED')
        return True, 'Đã thêm vào DanhSachNV của file lịch nghỉ.'
    except Exception as e:
        return False, f'Lỗi đồng bộ danh sách nhân viên lịch nghỉ: {e}'


def record_tichluy_contributions(payroll_df, start_date, end_date):
    """Ghi/ghi đè số Tích lũy của kỳ vào TichLuy theo khóa kỳ, tránh cộng trùng khi lưu lại."""
    try:
        if payroll_df is None or payroll_df.empty:
            return True, 'Không có dữ liệu Tích lũy cần cập nhật.'
        ws, err = _ensure_tichluy_sheet()
        if err or ws is None:
            return False, err or 'Không mở được TichLuy.'
        values = _gs_call_with_backoff(ws.get_all_values)
        if not values:
            return False, 'Sheet TichLuy chưa có tiêu đề.'
        header = values[0]
        pos = _tichluy_header_positions(header)
        rows_by_key = {}
        role_map = _credential_role_map(load_credentials())
        name_pos = pos.get('Tên nhân viên', 0)
        for r_idx, row in enumerate(values[1:], start=2):
            if name_pos < len(row) and str(row[name_pos]).strip():
                full_row = list(row) + [''] * max(0, len(header)-len(row))
                key = normalize_login_name(full_row[name_pos])
                def _rv(canonical, default=''):
                    idx = pos.get(canonical)
                    return full_row[idx] if idx is not None and idx < len(full_row) else default
                completed_score = 1 if _is_tichluy_completed(
                    _rv('Mục tiêu tích lũy'), _rv('Đã tích lũy'), _rv('Còn lại')
                ) else 0
                score = (completed_score, r_idx)
                if key not in rows_by_key or score > rows_by_key[key][0]:
                    rows_by_key[key] = (score, r_idx, full_row[:len(header)])
        period_key = _tichluy_period_key(start_date, end_date)
        updates = []
        created = 0
        for _, pr in payroll_df.iterrows():
            name = str(pr.get('Tên Hệ thống','')).strip()
            key = normalize_login_name(name)
            if not key:
                continue
            role = role_map.get(key)
            if role is None or role in TICHLUY_EXCLUDED_ROLES:
                continue
            amount = max(0.0, float(_money_to_float(pr.get('Tích lũy',0))))
            if key not in rows_by_key:
                # Dữ liệu cũ thiếu hồ sơ TichLuy: tạo dòng để Admin bổ sung Ngày bắt đầu làm.
                new_row = [''] * len(header)
                defaults = {
                    'Tên nhân viên': name, 'Ngày bắt đầu làm': '', 'Mục tiêu tích lũy': TICHLUY_TARGET_DEFAULT,
                    'Đã tích lũy': 0, 'Còn lại': TICHLUY_TARGET_DEFAULT, 'Kỳ gần nhất': '',
                    'Số tiền kỳ gần nhất': 0, 'Chi tiết các kỳ': '{}'
                }
                for canonical, value in defaults.items():
                    if canonical in pos: new_row[pos[canonical]] = value
                ws.append_row(new_row, value_input_option='USER_ENTERED')
                created += 1
                continue
            _score, r_idx, row = rows_by_key[key]
            def g(canonical, default=''):
                i = pos.get(canonical)
                return row[i] if i is not None and i < len(row) else default
            target = float(_money_to_float(g('Mục tiêu tích lũy')) or TICHLUY_TARGET_DEFAULT)
            current_total = float(_money_to_float(g('Đã tích lũy')))
            # Admin có thể nhập tay D/E/F cho người đã hoàn thành. Nếu E >= D > 0,
            # tuyệt đối bỏ qua dòng này: không sửa D/E/F và cũng không sửa G/H/I.
            if _is_tichluy_completed(g('Mục tiêu tích lũy'), g('Đã tích lũy'), g('Còn lại')):
                continue
            hist = _parse_tichluy_history(g('Chi tiết các kỳ'))
            old_history_total, matched_history_keys = _matching_tichluy_history(hist, start_date, end_date)
            old_display_amount = min(float(TICHLUY_PERIOD_DEFAULT), max(0.0, old_history_total))
            amount_to_record = old_display_amount if old_display_amount > 0 and amount <= 0 else amount

            # Nếu dữ liệu cũ đã ghi cùng kỳ dưới nhiều key rút gọn (vd. 16→17, 16→20),
            # gom về một key chính thức và loại phần cộng trùng khỏi tổng tích lũy.
            amount_to_subtract = old_history_total
            new_total = max(0.0, min(target, current_total - amount_to_subtract + amount_to_record))
            for legacy_key in matched_history_keys:
                hist.pop(legacy_key, None)
            hist[period_key] = float(amount_to_record)
            remaining = max(0.0, target - new_total)
            values_to_set = {
                'Mục tiêu tích lũy': target,
                'Đã tích lũy': new_total,
                'Còn lại': remaining,
                'Kỳ gần nhất': f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
                'Số tiền kỳ gần nhất': amount_to_record,
                'Chi tiết các kỳ': json.dumps(hist, ensure_ascii=False, separators=(',',':')),
            }
            for canonical, value in values_to_set.items():
                if canonical in pos:
                    row[pos[canonical]] = value
            updates.append((r_idx, row[:len(header)]))
        # Ghi full row để giữ nguyên mọi cột do người dùng tự thêm trong TichLuy.
        end_col_a1 = gspread.utils.rowcol_to_a1(1, len(header)).rstrip('1')
        for r_idx, row in updates:
            gspread_update_range(ws, f'A{r_idx}:{end_col_a1}{r_idx}', [row])
        try: load_tichluy_tracking.clear()
        except Exception: pass
        return True, f'Đã cập nhật Tích lũy cho {len(updates)} nhân viên' + (f'; tạo bổ sung {created} hồ sơ thiếu.' if created else '.')
    except Exception as e:
        return False, f'Lỗi cập nhật TichLuy: {e}'


def get_employee_violation_details(employee_name, start_date, end_date, leave_df=None):
    """Chi tiết các dòng có Phạt vi phạm > 0 của một nhân viên trong đúng kỳ lương."""
    cols = ['Ngày', 'Lý do nghỉ', 'Chi tiết', 'Phạt vi phạm']
    try:
        d = leave_df.copy() if isinstance(leave_df, pd.DataFrame) else load_backup_sheet_data()
        if d is None or d.empty or 'Tên nhân viên' not in d.columns:
            return pd.DataFrame(columns=cols)
        d = d.copy()
        if 'Lý do nghỉ' not in d.columns and 'Loại nghỉ' in d.columns:
            d = d.rename(columns={'Loại nghỉ':'Lý do nghỉ'})
        for c in cols:
            if c not in d.columns: d[c] = ''
        # Dùng cùng bộ parse ngày với logic tổng Vi phạm để email/Excel chi tiết
        # luôn khớp 100% với số tiền trên bảng lương.
        d['__date'] = d['Ngày'].apply(_parse_vn_date)
        d['__key'] = d['Tên nhân viên'].apply(normalize_login_name)
        d['__penalty'] = d['Phạt vi phạm'].apply(_money_to_float)
        key = normalize_login_name(employee_name)
        d = d[(d['__key'] == key) & (d['__date'] >= start_date) & (d['__date'] <= end_date) & (d['__penalty'] > 0)].copy()
        if d.empty:
            return pd.DataFrame(columns=cols)
        d['Ngày'] = d['__date'].apply(lambda x: x.strftime('%d/%m/%Y') if x else '')
        d['Phạt vi phạm'] = d['__penalty']
        return d[cols].reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=cols)


def _standardize_payroll_source(raw_df):
    """Chuẩn hóa đúng theo quy tắc người dùng: B=Thời gian, F=Loại, G=Tiền, I=Nhân viên."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=["Thời gian", "Sản phẩm/ Dịch vụ/ PT", "Tổng tiền", "NV tư vấn"])
    raw = raw_df.copy()
    # Tìm dòng tiêu đề; nếu không tìm được vẫn dùng vị trí cột B/F/G/I.
    header_idx = None
    for i in range(min(20, len(raw))):
        vals = [str(x).strip().casefold() for x in raw.iloc[i].tolist()]
        joined = " | ".join(vals)
        if "thời gian" in joined and ("sản phẩm" in joined or "dịch vụ" in joined) and "tổng tiền" in joined:
            header_idx = i
            break
    if header_idx is not None:
        data = raw.iloc[header_idx + 1:].copy()
    else:
        data = raw.copy()
    while data.shape[1] < 9:
        data[data.shape[1]] = ""
    out = pd.DataFrame({
        "Thời gian": data.iloc[:, 1],
        "Sản phẩm/ Dịch vụ/ PT": data.iloc[:, 5],
        "Tổng tiền": data.iloc[:, 6],
        "NV tư vấn": data.iloc[:, 8],
    })
    out = out.replace({None: ""})
    out["Thời gian_DT"] = pd.to_datetime(out["Thời gian"], dayfirst=True, errors="coerce")
    out["Tổng tiền"] = out["Tổng tiền"].apply(_money_to_float)
    out["NV tư vấn"] = out["NV tư vấn"].astype(str).str.strip()
    out["Sản phẩm/ Dịch vụ/ PT"] = out["Sản phẩm/ Dịch vụ/ PT"].astype(str).str.strip()
    return out.dropna(subset=["Thời gian_DT"])


@st.cache_data(ttl=60, show_spinner=False)
def load_payroll_source_from_google_sheet():
    try:
        client = get_gspread_client()
        if not client:
            return pd.DataFrame(), "Chưa cấu hình quyền Google Sheets."
        ws = client.open_by_key(PAYROLL_SOURCE_SHEET_ID).worksheet(PAYROLL_SOURCE_WORKSHEET)
        values = _gs_call_with_backoff(ws.get_all_values)
        if not values:
            return pd.DataFrame(), "Sheet dữ liệu lương đang trống."
        raw = pd.DataFrame(values)
        return _standardize_payroll_source(raw), ""
    except Exception as e:
        return pd.DataFrame(), f"Không đọc được nguồn dữ liệu lương mặc định: {e}"


def load_payroll_source_from_uploaded_excel(uploaded_file):
    try:
        if uploaded_file is None:
            return pd.DataFrame(), "Chưa chọn file dữ liệu lương."
        uploaded_file.seek(0)
        raw = pd.read_excel(uploaded_file, sheet_name=PAYROLL_SOURCE_WORKSHEET, header=None, engine="openpyxl")
        return _standardize_payroll_source(raw), ""
    except Exception as e:
        return pd.DataFrame(), f"Không đọc được sheet '{PAYROLL_SOURCE_WORKSHEET}': {e}"


def _official_payroll_period(year, month, period_no):
    """
    Trả về kỳ lương chính thức của VERA SPA.
    Kỳ 1 luôn từ ngày 01 đến hết ngày 15.
    Kỳ 2 luôn từ ngày 16 đến hết ngày cuối cùng của tháng.
    Không phụ thuộc ngày hiện tại và không cắt kỳ tại ngày hôm nay.
    """
    year, month, period_no = int(year), int(month), int(period_no)
    if period_no == 1:
        return date(year, month, 1), date(year, month, 15)
    if period_no == 2:
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 16), date(year, month, last_day)
    raise ValueError('period_no chỉ nhận 1 hoặc 2')


def _canonicalize_payroll_period(start_date, end_date):
    """
    Chuẩn hóa một khoảng ngày thuộc cùng tháng về đúng kỳ lương chính thức.
    Hàm này dùng cho nghiệp vụ Tích lũy/lưu kỳ để tránh trường hợp kỳ 2 bị ghi 16→ngày hiện tại.
    Nếu khoảng ngày không cùng tháng hoặc cắt qua cả hai kỳ thì giữ nguyên để bảo toàn dữ liệu lịch sử cũ.
    """
    if not start_date or not end_date:
        return start_date, end_date
    if start_date.year != end_date.year or start_date.month != end_date.month:
        return start_date, end_date
    if end_date.day <= 15:
        return _official_payroll_period(start_date.year, start_date.month, 1)
    if start_date.day >= 16:
        return _official_payroll_period(start_date.year, start_date.month, 2)
    return start_date, end_date


def _is_official_payroll_period(start_date, end_date):
    if not start_date or not end_date:
        return False
    if start_date.year != end_date.year or start_date.month != end_date.month:
        return False
    period_no = 1 if start_date.day == 1 else (2 if start_date.day == 16 else 0)
    if not period_no:
        return False
    return (start_date, end_date) == _official_payroll_period(start_date.year, start_date.month, period_no)


def _matching_tichluy_history(hist, start_date, end_date):
    """
    Trả về (tổng tiền đã ghi, các key khớp) cho cùng một kỳ lương chính thức.
    Hỗ trợ dữ liệu cũ từng ghi Kỳ 2 dạng 16→ngày hiện tại hoặc Kỳ 1 dạng 01→ngày hiện tại.
    Chỉ gom legacy key khi khoảng đang tính là kỳ chính thức 01-15 / 16-cuối tháng.
    """
    if not isinstance(hist, dict):
        return 0.0, []
    exact_key = _tichluy_period_key(start_date, end_date)
    if not _is_official_payroll_period(start_date, end_date):
        amount = max(0.0, float(_money_to_float(hist.get(exact_key, 0))))
        return amount, ([exact_key] if exact_key in hist else [])

    matched = []
    total = 0.0
    for key, value in hist.items():
        ks, ke = _parse_tichluy_period_text(key)
        if not ks or not ke:
            continue
        cs, ce = _canonicalize_payroll_period(ks, ke)
        if cs == start_date and ce == end_date:
            matched.append(str(key))
            total += max(0.0, float(_money_to_float(value)))
    return total, matched


def resolve_payroll_period(preset, today=None, custom_range=None):
    """
    Kỳ lương cố định toàn hệ thống:
      • Kỳ 1: 01 → 15
      • Kỳ 2: 16 → ngày cuối tháng
    Không dùng ngày hiện tại làm ngày kết thúc kỳ.
    """
    today = today or get_vn_today()
    first_this = date(today.year, today.month, 1)
    prev_last = first_this - timedelta(days=1)
    if preset == "Kỳ 1 - Tháng này":
        return *_official_payroll_period(today.year, today.month, 1), ""
    if preset == "Kỳ 2 - Tháng này":
        return *_official_payroll_period(today.year, today.month, 2), ""
    if preset == "Kỳ 1 - Tháng trước":
        return *_official_payroll_period(prev_last.year, prev_last.month, 1), ""
    if preset == "Kỳ 2 - Tháng trước":
        return *_official_payroll_period(prev_last.year, prev_last.month, 2), ""
    return None, None, "Không xác định được kỳ lương."


def _period_penalty_by_employee(start_date, end_date, leave_primary=None, leave_secondary=None):
    """
    Tiền phạt CHỈ lấy từ Google Sheet 1Kz0... (SHEET_DU_PHONG_ID), theo kỳ đang chọn.

    Quan trọng: parse ngày từng ô bằng _parse_vn_date để hỗ trợ đồng thời
    dd-mm-yyyy, dd/mm/yyyy, yyyy-mm-dd và Excel/Google Sheets serial date.
    Cách cũ dùng pd.to_datetime cho cả Series có thể bỏ sót một số dòng khi
    dữ liệu ngày trong cùng Sheet không đồng nhất định dạng.
    """
    try:
        d = leave_primary.copy() if isinstance(leave_primary, pd.DataFrame) else load_backup_sheet_data()
        if d is None or d.empty:
            return {}
        if 'Ngày' not in d.columns or 'Tên nhân viên' not in d.columns or 'Phạt vi phạm' not in d.columns:
            return {}

        d = d.copy()
        d['Ngày_DT'] = d['Ngày'].apply(_parse_vn_date)
        d['__penalty'] = d['Phạt vi phạm'].apply(_money_to_float)
        d['__key'] = d['Tên nhân viên'].apply(normalize_login_name)

        # Chỉ giữ các dòng ngày hợp lệ, nằm trọn trong kỳ lương (inclusive 2 đầu).
        d = d[
            d['Ngày_DT'].notna()
            & (d['Ngày_DT'] >= start_date)
            & (d['Ngày_DT'] <= end_date)
        ].copy()
        if d.empty:
            return {}

        # Cộng trực tiếp TẤT CẢ dòng Phạt vi phạm của cùng nhân viên trong kỳ.
        # Không dedupe theo ngày/lý do vì một nhân viên có thể có nhiều vi phạm cùng ngày.
        return d.groupby('__key', dropna=False)['__penalty'].sum().to_dict()
    except Exception:
        return {}


def get_period_penalty_audit(employee_name, start_date, end_date, leave_primary=None):
    """Trả chi tiết các dòng phạt đã được cộng cho một nhân viên trong kỳ, không phát sinh thêm API read nếu đã truyền DataFrame."""
    try:
        d = leave_primary.copy() if isinstance(leave_primary, pd.DataFrame) else load_backup_sheet_data()
        if d is None or d.empty:
            return pd.DataFrame(columns=['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Phạt vi phạm'])
        for c in ['Ngày','Tên nhân viên','Lý do nghỉ','Chi tiết','Phạt vi phạm']:
            if c not in d.columns:
                d[c] = ''
        d['__date'] = d['Ngày'].apply(_parse_vn_date)
        d['__key'] = d['Tên nhân viên'].apply(normalize_login_name)
        d['__penalty'] = d['Phạt vi phạm'].apply(_money_to_float)
        key = normalize_login_name(employee_name)
        d = d[(d['__key'] == key) & d['__date'].notna() & (d['__date'] >= start_date) & (d['__date'] <= end_date) & (d['__penalty'] != 0)].copy()
        d['Ngày'] = d['__date'].apply(lambda x: x.strftime('%d/%m/%Y') if x else '')
        d['Phạt vi phạm'] = d['__penalty']
        return d[['Ngày','Tên nhân viên','Lý do nghỉ','Chi tiết','Phạt vi phạm']].reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Phạt vi phạm'])



def _next_official_payroll_period(start_date, end_date):
    """Trả về kỳ lương chính thức kế tiếp (01-15 hoặc 16-cuối tháng)."""
    cs, ce = _canonicalize_payroll_period(start_date, end_date)
    if cs.day == 1:
        return _official_payroll_period(cs.year, cs.month, 2)
    if cs.month == 12:
        return _official_payroll_period(cs.year + 1, 1, 1)
    return _official_payroll_period(cs.year, cs.month + 1, 1)


def _violation_debt_source_key(kind, start_date, end_date, employee_name):
    return f"{kind}|{start_date.isoformat()}|{end_date.isoformat()}|{normalize_login_name(employee_name)}"


@st.cache_resource(show_spinner=False)
def _ensure_violation_debt_storage():
    """Tạo/lấy sheet NoViPham dùng làm sổ nghĩa vụ Vi phạm chuyển kỳ."""
    client = get_gspread_client()
    if not client:
        return None, "Chưa cấu hình quyền kết nối Google Sheets."
    try:
        ss = client.open_by_key(SHEET_MAT_KHAU_ID)
        ws = _get_or_create_worksheet(ss, VIOLATION_DEBT_WORKSHEET, rows=3000, cols=20)
        header = _gs_call_with_backoff(ws.row_values, 1)
        if not header or header[:len(VIOLATION_DEBT_HEADERS)] != VIOLATION_DEBT_HEADERS:
            gspread_update_range(ws, "A1:N1", [VIOLATION_DEBT_HEADERS])
        return ws, ""
    except Exception as e:
        return None, f"Lỗi khởi tạo sheet {VIOLATION_DEBT_WORKSHEET}: {e}"


@st.cache_data(ttl=120, show_spinner=False)
def load_violation_debt_ledger():
    """Đọc sổ nợ Vi phạm một lần, có cache để tránh quota Sheets."""
    ws, err = _ensure_violation_debt_storage()
    if err or ws is None:
        return pd.DataFrame(columns=VIOLATION_DEBT_HEADERS + ['__sheet_row'])
    try:
        vals = _gs_call_with_backoff(ws.get_all_values)
        if not vals:
            return pd.DataFrame(columns=VIOLATION_DEBT_HEADERS + ['__sheet_row'])
        header = [str(x).strip() for x in vals[0]]
        pos = {name: (header.index(name) if name in header else None) for name in VIOLATION_DEBT_HEADERS}
        rows = []
        for sheet_row, row in enumerate(vals[1:], start=2):
            if not any(str(v).strip() for v in row):
                continue
            item = {name: (row[p] if p is not None and p < len(row) else '') for name, p in pos.items()}
            item['__sheet_row'] = sheet_row
            rows.append(item)
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=VIOLATION_DEBT_HEADERS + ['__sheet_row'])
    except Exception:
        return pd.DataFrame(columns=VIOLATION_DEBT_HEADERS + ['__sheet_row'])


def _clear_violation_debt_cache():
    try:
        load_violation_debt_ledger.clear()
    except Exception:
        pass


def _is_open_violation_debt_status(value):
    txt = normalize_login_name(value)
    return txt in {'', normalize_login_name(VIOLATION_DEBT_OPEN_STATUS), 'chua hoan thanh'}


def get_violation_debt_state(start_date, end_date, employee_names=None):
    """
    Trả về:
      due_map: nghĩa vụ từ kỳ trước đã tới hạn khấu trừ ở kỳ đang tính.
      deferred_current_map: tiền Vi phạm của chính kỳ đang tính mà Admin đã chủ động hoãn.
      active_df: các nghĩa vụ còn mở liên quan.

    Lưu ý: khoản "Tạm hoãn vi phạm" của một kỳ vẫn phải được trừ khỏi Vi phạm gốc
    của kỳ đó kể cả sau này nghĩa vụ đã được hoàn thành ở kỳ kế tiếp. Nhờ vậy khi
    mở lại/tính lại lịch sử, số của kỳ cũ không bị thay đổi ngược trở lại.
    """
    d = load_violation_debt_ledger().copy()
    if d is None or d.empty:
        return {}, {}, pd.DataFrame(columns=VIOLATION_DEBT_HEADERS)

    allowed = None
    if employee_names is not None:
        allowed = {normalize_login_name(x) for x in employee_names if normalize_login_name(x)}

    due_map = {}
    deferred_current_map = {}
    active_rows = []
    for _, r in d.iterrows():
        emp = str(r.get('Tên nhân viên', '')).strip()
        key = normalize_login_name(emp)
        if not key or (allowed is not None and key not in allowed):
            continue
        amount = max(0.0, float(_money_to_float(r.get('Số tiền', 0))))
        if amount <= 0:
            continue
        src_start = _parse_vn_date(r.get('Kỳ phát sinh từ', ''))
        src_end = _parse_vn_date(r.get('Kỳ phát sinh đến', ''))
        due_from = _parse_vn_date(r.get('Bắt đầu trừ từ', ''))
        debt_type = str(r.get('Loại', '')).strip()

        # Lịch sử tạm hoãn là thuộc tính của kỳ phát sinh, không phụ thuộc trạng thái
        # nghĩa vụ ở thời điểm hiện tại.
        if normalize_login_name(debt_type) == normalize_login_name('Tạm hoãn vi phạm') and src_start and src_end:
            cs, ce = _canonicalize_payroll_period(src_start, src_end)
            if cs == start_date and ce == end_date:
                deferred_current_map[key] = deferred_current_map.get(key, 0.0) + amount

        # Chỉ nghĩa vụ còn mở mới được cộng vào kỳ hiện tại.
        if not _is_open_violation_debt_status(r.get('Trạng thái', '')):
            continue
        if due_from and due_from <= start_date:
            due_map[key] = due_map.get(key, 0.0) + amount
        active_rows.append(r.to_dict())

    active_df = pd.DataFrame(active_rows) if active_rows else pd.DataFrame(columns=VIOLATION_DEBT_HEADERS)
    return due_map, deferred_current_map, active_df


def get_open_negative_payroll_debts():
    """
    Danh sách các khoản nợ do Thực nhận âm còn Chưa hoàn thành.

    Chỉ lấy Loại = "Âm thực nhận" trong sheet NoViPham; không trộn với
    khoản Admin chủ động "Tạm hoãn vi phạm".

    Trả về:
      summary_df: tổng nợ theo nhân viên.
      detail_df:  chi tiết từng kỳ phát sinh còn mở.
    """
    d = load_violation_debt_ledger().copy()
    if d is None or d.empty:
        empty_summary = pd.DataFrame(columns=["Tên nhân viên", "Tổng còn nợ", "Số kỳ còn nợ", "Kỳ nợ gần nhất", "Bắt đầu trừ từ"])
        empty_detail = pd.DataFrame(columns=["Tên nhân viên", "Số tiền", "Kỳ phát sinh từ", "Kỳ phát sinh đến", "Bắt đầu trừ từ", "Nội dung", "Trạng thái"])
        return empty_summary, empty_detail

    rows = []
    for _, r in d.iterrows():
        debt_type = normalize_login_name(r.get('Loại', ''))
        if debt_type != normalize_login_name('Âm thực nhận'):
            continue
        if not _is_open_violation_debt_status(r.get('Trạng thái', '')):
            continue
        emp = str(r.get('Tên nhân viên', '')).strip()
        amount = max(0.0, float(_money_to_float(r.get('Số tiền', 0))))
        if not emp or amount <= 0:
            continue
        src_start = _parse_vn_date(r.get('Kỳ phát sinh từ', ''))
        src_end = _parse_vn_date(r.get('Kỳ phát sinh đến', ''))
        due_from = _parse_vn_date(r.get('Bắt đầu trừ từ', ''))
        rows.append({
            "Tên nhân viên": emp,
            "Số tiền": int(round(amount)),
            "Kỳ phát sinh từ": src_start.strftime('%d/%m/%Y') if src_start else str(r.get('Kỳ phát sinh từ', '')).strip(),
            "Kỳ phát sinh đến": src_end.strftime('%d/%m/%Y') if src_end else str(r.get('Kỳ phát sinh đến', '')).strip(),
            "Bắt đầu trừ từ": due_from.strftime('%d/%m/%Y') if due_from else str(r.get('Bắt đầu trừ từ', '')).strip(),
            "Nội dung": str(r.get('Nội dung', '')).strip() or VIOLATION_DEBT_CONTENT,
            "Trạng thái": str(r.get('Trạng thái', '')).strip() or VIOLATION_DEBT_OPEN_STATUS,
            "__src_start": src_start,
            "__src_end": src_end,
            "__due_from": due_from,
        })

    if not rows:
        empty_summary = pd.DataFrame(columns=["Tên nhân viên", "Tổng còn nợ", "Số kỳ còn nợ", "Kỳ nợ gần nhất", "Bắt đầu trừ từ"])
        empty_detail = pd.DataFrame(columns=["Tên nhân viên", "Số tiền", "Kỳ phát sinh từ", "Kỳ phát sinh đến", "Bắt đầu trừ từ", "Nội dung", "Trạng thái"])
        return empty_summary, empty_detail

    detail = pd.DataFrame(rows)
    # Kỳ cũ trước, kỳ mới sau để Admin dễ theo dõi diễn biến.
    detail = detail.sort_values(
        by=["__src_start", "Tên nhân viên"],
        key=lambda s: s.map(lambda x: x.toordinal() if hasattr(x, 'toordinal') else 99999999) if s.name == "__src_start" else s,
        na_position='last'
    ).reset_index(drop=True)

    summary_rows = []
    for emp, grp in detail.groupby("Tên nhân viên", sort=True):
        total = int(round(grp["Số tiền"].apply(_money_to_float).sum()))
        latest = grp.copy()
        latest["__sort"] = latest["__src_start"].apply(lambda x: x.toordinal() if hasattr(x, 'toordinal') else -1)
        latest_row = latest.sort_values("__sort", ascending=False).iloc[0]
        due_dates = [x for x in grp["__due_from"].tolist() if hasattr(x, 'strftime')]
        earliest_due = min(due_dates) if due_dates else None
        latest_period = f"{latest_row['Kỳ phát sinh từ']} - {latest_row['Kỳ phát sinh đến']}"
        summary_rows.append({
            "Tên nhân viên": emp,
            "Tổng còn nợ": total,
            "Số kỳ còn nợ": int(len(grp)),
            "Kỳ nợ gần nhất": latest_period,
            "Bắt đầu trừ từ": earliest_due.strftime('%d/%m/%Y') if earliest_due else str(latest_row.get('Bắt đầu trừ từ', '')).strip(),
        })

    summary = pd.DataFrame(summary_rows).sort_values(["Tổng còn nợ", "Tên nhân viên"], ascending=[False, True]).reset_index(drop=True)
    detail = detail.drop(columns=["__src_start", "__src_end", "__due_from"], errors='ignore')
    return summary, detail


def defer_violation_to_next_period(employee_name, amount, start_date, end_date, updated_by):
    """Admin chuyển một phần/toàn bộ Vi phạm của kỳ hiện tại sang các kỳ kế tiếp."""
    employee_name = str(employee_name).strip()
    amount = max(0.0, float(_money_to_float(amount)))
    if not employee_name or amount <= 0:
        return False, "Vui lòng chọn nhân viên và nhập số tiền lớn hơn 0."

    ws, err = _ensure_violation_debt_storage()
    if err or ws is None:
        return False, err or "Không mở được sổ nghĩa vụ Vi phạm."
    try:
        vals = _gs_call_with_backoff(ws.get_all_values)
        header = [str(x).strip() for x in (vals[0] if vals else VIOLATION_DEBT_HEADERS)]
        if header[:len(VIOLATION_DEBT_HEADERS)] != VIOLATION_DEBT_HEADERS:
            gspread_update_range(ws, "A1:N1", [VIOLATION_DEBT_HEADERS])
            vals = [VIOLATION_DEBT_HEADERS] + (vals[1:] if vals else [])
            header = VIOLATION_DEBT_HEADERS[:]
        source_key = _violation_debt_source_key('DEFER', start_date, end_date, employee_name)
        next_start, next_end = _next_official_payroll_period(start_date, end_date)
        now = datetime.now(VN_TZ)
        source_col = VIOLATION_DEBT_HEADERS.index('Mã nguồn')
        amount_col = VIOLATION_DEBT_HEADERS.index('Số tiền')
        existing_row = None
        existing_amount = 0.0
        existing_status = ''
        status_col = VIOLATION_DEBT_HEADERS.index('Trạng thái')
        for row_idx, row in enumerate(vals[1:], start=2):
            key_value = row[source_col] if source_col < len(row) else ''
            if str(key_value).strip() == source_key:
                existing_row = row_idx
                existing_amount = float(_money_to_float(row[amount_col] if amount_col < len(row) else 0))
                existing_status = row[status_col] if status_col < len(row) else ''
                break
        if existing_row and not _is_open_violation_debt_status(existing_status):
            return False, "Khoản Vi phạm đã tạm hoãn của kỳ này đã được khấu trừ ở kỳ sau, không thể mở lại từ kỳ cũ."
        total_amount = existing_amount + amount
        _defer_note_text = f"Trừ kỳ lương kế tiếp: {total_amount:,.0f} đ".replace(',', '.')
        row_values = [
            (existing_row - 1 if existing_row else max(1, len(vals))),
            employee_name,
            int(round(total_amount)),
            _defer_note_text,
            "Tạm hoãn vi phạm",
            start_date.strftime('%d/%m/%Y'),
            end_date.strftime('%d/%m/%Y'),
            next_start.strftime('%d/%m/%Y'),
            VIOLATION_DEBT_OPEN_STATUS,
            source_key,
            now.strftime('%d/%m/%Y'),
            now.strftime('%H:%M:%S'),
            str(updated_by),
            "",
        ]
        if existing_row:
            gspread_update_range(ws, f"A{existing_row}:N{existing_row}", [row_values])
        else:
            ws.append_row(row_values, value_input_option='USER_ENTERED')
        _clear_violation_debt_cache()
        return True, (
            f"Đã chuyển thêm {amount:,.0f}đ Vi phạm của {employee_name} sang kỳ kế tiếp. "
            f"Tổng nghĩa vụ đang hoãn từ kỳ này: {total_amount:,.0f}đ."
        ).replace(',', '.')
    except Exception as e:
        return False, f"Lỗi lưu nghĩa vụ Vi phạm: {e}"


def _renumber_violation_debt_stt(ws):
    """Đánh lại STT cột A từ dòng 2 sau khi xóa nghĩa vụ."""
    try:
        vals = _gs_call_with_backoff(ws.get_all_values)
        if not vals or len(vals) <= 1:
            return
        # Chỉ đánh STT cho các dòng có dữ liệu thật ở B:N.
        numbers = []
        n = 0
        for row in vals[1:]:
            has_data = any(str(v).strip() for v in row[1:14]) if len(row) > 1 else False
            if has_data:
                n += 1
                numbers.append([n])
            else:
                numbers.append([''])
        if numbers:
            gspread_update_range(ws, f"A2:A{len(numbers)+1}", numbers)
    except Exception:
        pass


def update_violation_debt_obligation(sheet_row, amount, content, due_from, updated_by):
    """
    Admin sửa một nghĩa vụ Vi phạm đang mở.
    Giữ nguyên nhân viên / loại / kỳ phát sinh / mã nguồn để không phá lịch sử,
    chỉ cho chỉnh số tiền, nội dung và ngày bắt đầu trừ.
    """
    try:
        sheet_row = int(sheet_row)
    except Exception:
        return False, "Không xác định được dòng nghĩa vụ cần sửa."
    if sheet_row < 2:
        return False, "Dòng nghĩa vụ không hợp lệ."
    amount = max(0.0, float(_money_to_float(amount)))
    if amount <= 0:
        return False, "Số tiền nghĩa vụ phải lớn hơn 0. Nếu không còn nghĩa vụ, hãy dùng nút Xóa."
    if not hasattr(due_from, 'strftime'):
        due_from = _parse_vn_date(due_from)
    if due_from is None:
        return False, "Ngày bắt đầu trừ không hợp lệ."

    ws, err = _ensure_violation_debt_storage()
    if err or ws is None:
        return False, err or "Không mở được sổ nghĩa vụ Vi phạm."
    try:
        row = _gs_call_with_backoff(ws.row_values, sheet_row)
        if not row or not any(str(v).strip() for v in row):
            return False, "Nghĩa vụ này không còn tồn tại. Hãy tải lại danh sách."
        while len(row) < len(VIOLATION_DEBT_HEADERS):
            row.append('')
        pos = {name: VIOLATION_DEBT_HEADERS.index(name) for name in VIOLATION_DEBT_HEADERS}
        if not _is_open_violation_debt_status(row[pos['Trạng thái']]):
            return False, "Chỉ có thể sửa nghĩa vụ đang ở trạng thái Chưa hoàn thành."

        now = datetime.now(VN_TZ)
        row[pos['Số tiền']] = int(round(amount))
        row[pos['Nội dung']] = str(content).strip() or VIOLATION_DEBT_CONTENT
        row[pos['Bắt đầu trừ từ']] = due_from.strftime('%d/%m/%Y')
        row[pos['Ngày cập nhật']] = now.strftime('%d/%m/%Y')
        row[pos['Giờ cập nhật']] = now.strftime('%H:%M:%S')
        row[pos['Người cập nhật']] = str(updated_by)
        gspread_update_range(ws, f"A{sheet_row}:N{sheet_row}", [row[:len(VIOLATION_DEBT_HEADERS)]])
        _clear_violation_debt_cache()
        return True, "Đã cập nhật Nghĩa vụ Vi phạm."
    except Exception as e:
        return False, f"Lỗi cập nhật Nghĩa vụ Vi phạm: {e}"


def delete_violation_debt_obligation(sheet_row, updated_by=''):
    """Admin xóa hẳn một nghĩa vụ Vi phạm đang mở và đánh lại STT."""
    try:
        sheet_row = int(sheet_row)
    except Exception:
        return False, "Không xác định được dòng nghĩa vụ cần xóa."
    if sheet_row < 2:
        return False, "Dòng nghĩa vụ không hợp lệ."
    ws, err = _ensure_violation_debt_storage()
    if err or ws is None:
        return False, err or "Không mở được sổ nghĩa vụ Vi phạm."
    try:
        row = _gs_call_with_backoff(ws.row_values, sheet_row)
        if not row or not any(str(v).strip() for v in row):
            return False, "Nghĩa vụ này không còn tồn tại."
        while len(row) < len(VIOLATION_DEBT_HEADERS):
            row.append('')
        status = row[VIOLATION_DEBT_HEADERS.index('Trạng thái')]
        if not _is_open_violation_debt_status(status):
            return False, "Chỉ xóa trực tiếp nghĩa vụ đang Chưa hoàn thành."
        _gs_call_with_backoff(ws.delete_rows, sheet_row)
        _renumber_violation_debt_stt(ws)
        _clear_violation_debt_cache()
        return True, "Đã xóa Nghĩa vụ Vi phạm."
    except Exception as e:
        return False, f"Lỗi xóa Nghĩa vụ Vi phạm: {e}"


def refresh_current_payroll_violation_debt(payroll_df, start_date, end_date):
    """Tính lại riêng cột Vi phạm kỳ trước theo ledger mới nhất rồi tính lại Thực nhận."""
    if payroll_df is None or payroll_df.empty:
        return payroll_df
    d = payroll_df.copy()
    names = d.get('Tên Hệ thống', pd.Series(dtype=str)).astype(str).tolist()
    due_map, _, _ = get_violation_debt_state(start_date, end_date, names)
    if 'Vi phạm kỳ trước' not in d.columns:
        d['Vi phạm kỳ trước'] = 0.0
    for idx, r in d.iterrows():
        key = normalize_login_name(r.get('Tên Hệ thống', ''))
        d.at[idx, 'Vi phạm kỳ trước'] = float(_money_to_float(due_map.get(key, 0)))
    return recalculate_payroll_net(d)


def get_all_open_violation_debts_for_admin():
    """Trả toàn bộ nghĩa vụ Vi phạm đang mở, kể cả nhân viên không nằm trong kỳ lương hiện tại."""
    d = load_violation_debt_ledger().copy()
    if d is None or d.empty:
        return pd.DataFrame(columns=VIOLATION_DEBT_HEADERS + ['__sheet_row'])
    keep = []
    for _, r in d.iterrows():
        amount = max(0.0, float(_money_to_float(r.get('Số tiền', 0))))
        if amount <= 0 or not _is_open_violation_debt_status(r.get('Trạng thái', '')):
            continue
        keep.append(r.to_dict())
    return pd.DataFrame(keep) if keep else pd.DataFrame(columns=VIOLATION_DEBT_HEADERS + ['__sheet_row'])


def _upsert_negative_violation_debt(ws, vals, employee_name, amount, start_date, end_date, updated_by):
    """Tạo/cập nhật khoản âm Thực nhận của một kỳ, không tạo trùng khi lưu lại cùng kỳ."""
    source_key = _violation_debt_source_key('NEG', start_date, end_date, employee_name)
    source_col = VIOLATION_DEBT_HEADERS.index('Mã nguồn')
    next_start, _ = _next_official_payroll_period(start_date, end_date)
    now = datetime.now(VN_TZ)
    existing_row = None
    existing_status = ''
    for row_idx, row in enumerate(vals[1:], start=2):
        key_value = row[source_col] if source_col < len(row) else ''
        if str(key_value).strip() == source_key:
            existing_row = row_idx
            status_col = VIOLATION_DEBT_HEADERS.index('Trạng thái')
            existing_status = row[status_col] if status_col < len(row) else ''
            break

    # Nếu khoản của kỳ này đã được khấu trừ xong ở kỳ sau thì không tự mở lại do chỉnh lịch sử.
    if existing_row and not _is_open_violation_debt_status(existing_status):
        return None

    amount = max(0.0, float(_money_to_float(amount)))
    # Không tạo dòng rỗng cho nhân viên có Thực nhận >= 0. Chỉ cập nhật về hoàn thành
    # nếu trước đó đã tồn tại một khoản âm của chính kỳ này.
    if amount <= 0 and not existing_row:
        return None
    status = VIOLATION_DEBT_OPEN_STATUS if amount > 0 else VIOLATION_DEBT_DONE_STATUS
    row_values = [
        (existing_row - 1 if existing_row else max(1, len(vals))),
        str(employee_name).strip(),
        int(round(amount)),
        VIOLATION_DEBT_CONTENT,
        "Âm thực nhận",
        start_date.strftime('%d/%m/%Y'),
        end_date.strftime('%d/%m/%Y'),
        next_start.strftime('%d/%m/%Y'),
        status,
        source_key,
        now.strftime('%d/%m/%Y'),
        now.strftime('%H:%M:%S'),
        str(updated_by),
        "" if amount > 0 else f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
    ]
    if existing_row:
        gspread_update_range(ws, f"A{existing_row}:N{existing_row}", [row_values])
        return ('updated', existing_row, row_values)
    ws.append_row(row_values, value_input_option='USER_ENTERED')
    vals.append([str(x) for x in row_values])
    return ('appended', None, row_values)


def commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by):
    """
    Khi lưu bảng lương:
      1) Các nghĩa vụ cũ đã tới hạn và đã được đưa vào cột "Vi phạm kỳ trước" được đánh dấu hoàn thành.
      2) Nếu Thực nhận âm và Admin KHÔNG chủ động tạm hoãn người đó, phần âm được tự lưu thành nghĩa vụ mới.
      3) Nếu Admin đã chủ động tạm hoãn Vi phạm của người đó trong kỳ, không tự tạo thêm nợ âm để tránh trùng.
    """
    if payroll_df is None or payroll_df.empty:
        return True, ""
    ws, err = _ensure_violation_debt_storage()
    if err or ws is None:
        return False, err or "Không mở được sổ nghĩa vụ Vi phạm."
    try:
        vals = _gs_call_with_backoff(ws.get_all_values)
        if not vals:
            vals = [VIOLATION_DEBT_HEADERS]
        header = [str(x).strip() for x in vals[0]]
        if header[:len(VIOLATION_DEBT_HEADERS)] != VIOLATION_DEBT_HEADERS:
            gspread_update_range(ws, "A1:N1", [VIOLATION_DEBT_HEADERS])
            header = VIOLATION_DEBT_HEADERS[:]
            vals[0] = header
        pos = {name: header.index(name) for name in VIOLATION_DEBT_HEADERS}
        payroll_names = {
            normalize_login_name(x): str(x).strip()
            for x in payroll_df.get('Tên Hệ thống', pd.Series(dtype=str)).tolist()
            if normalize_login_name(x)
        }
        now = datetime.now(VN_TZ)
        settled_count = 0
        # Nghĩa vụ cũ tới hạn đã được cộng vào Vi phạm của kỳ này -> đóng khoản cũ.
        for row_idx, row in enumerate(vals[1:], start=2):
            emp = row[pos['Tên nhân viên']] if pos['Tên nhân viên'] < len(row) else ''
            emp_key = normalize_login_name(emp)
            if emp_key not in payroll_names:
                continue
            status = row[pos['Trạng thái']] if pos['Trạng thái'] < len(row) else ''
            if not _is_open_violation_debt_status(status):
                continue
            due_from = _parse_vn_date(row[pos['Bắt đầu trừ từ']] if pos['Bắt đầu trừ từ'] < len(row) else '')
            if not due_from or due_from > start_date:
                continue
            # Ghi trạng thái hoàn thành + kỳ đã khấu trừ; các cột khác giữ nguyên.
            gspread_update_range(ws, f"I{row_idx}:N{row_idx}", [[
                VIOLATION_DEBT_DONE_STATUS,
                row[pos['Mã nguồn']] if pos['Mã nguồn'] < len(row) else '',
                now.strftime('%d/%m/%Y'),
                now.strftime('%H:%M:%S'),
                str(saved_by),
                f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
            ]])
            # Đồng bộ snapshot trong bộ nhớ để bước upsert phía sau không đọc sai trạng thái.
            while len(row) < len(VIOLATION_DEBT_HEADERS):
                row.append('')
            row[pos['Trạng thái']] = VIOLATION_DEBT_DONE_STATUS
            settled_count += 1

        # V50: xác định nhân viên Admin đã chủ động tạm hoãn Vi phạm của CHÍNH kỳ này.
        # Những người này không được tự tạo thêm một khoản "Âm thực nhận" khác, tránh ghi nợ kép.
        manual_adjusted_keys = set()
        for row in vals[1:]:
            emp = row[pos['Tên nhân viên']] if pos['Tên nhân viên'] < len(row) else ''
            emp_key = normalize_login_name(emp)
            debt_type = row[pos['Loại']] if pos['Loại'] < len(row) else ''
            if normalize_login_name(debt_type) != normalize_login_name('Tạm hoãn vi phạm'):
                continue
            src_start = _parse_vn_date(row[pos['Kỳ phát sinh từ']] if pos['Kỳ phát sinh từ'] < len(row) else '')
            src_end = _parse_vn_date(row[pos['Kỳ phát sinh đến']] if pos['Kỳ phát sinh đến'] < len(row) else '')
            if src_start and src_end:
                cs, ce = _canonicalize_payroll_period(src_start, src_end)
                if cs == start_date and ce == end_date and emp_key:
                    manual_adjusted_keys.add(emp_key)

        negative_count = 0
        negative_total = 0.0
        auto_negative_names = []
        for _, r in payroll_df.iterrows():
            emp = str(r.get('Tên Hệ thống', '')).strip()
            emp_key = normalize_login_name(emp)
            if not emp_key:
                continue
            net = float(_money_to_float(r.get('Số tiền thực nhận', 0)))

            if emp_key in manual_adjusted_keys:
                # Nếu trước đó đã từng lưu tự động phần âm của cùng kỳ rồi sau đó Admin mới
                # chủ động tạm hoãn, đóng khoản tự động cũ để không tồn tại song song hai nghĩa vụ.
                _upsert_negative_violation_debt(ws, vals, emp, 0.0, start_date, end_date, saved_by)
                continue

            debt_amount = abs(net) if net < 0 else 0.0
            result = _upsert_negative_violation_debt(
                ws, vals, emp, debt_amount, start_date, end_date, saved_by
            )
            if debt_amount > 0 and result is not None:
                negative_count += 1
                negative_total += debt_amount
                auto_negative_names.append(emp)

        _clear_violation_debt_cache()
        msg_parts = []
        if settled_count:
            msg_parts.append(f"đã khấu trừ {settled_count} nghĩa vụ Vi phạm cũ")
        if negative_count:
            msg_parts.append(
                (f"đã tự lưu {negative_total:,.0f}đ phần Thực nhận âm của {negative_count} nhân viên "
                 f"không được Admin điều chỉnh sang kỳ kế tiếp: {', '.join(auto_negative_names)}").replace(',', '.')
            )
        return True, '; '.join(msg_parts)
    except Exception as e:
        return False, f"Lỗi cập nhật nghĩa vụ Vi phạm chuyển kỳ: {e}"


def build_payroll_table(source_df, credentials_df, start_date, end_date, leave_primary=None, leave_secondary=None, default_living_expense=150000, default_locker_support=80000):
    """Tổng hợp lương: chỉ cộng G khi F bắt đầu bằng 'Tip', nhóm theo tên nhân viên ở cột I."""
    if source_df is None or source_df.empty:
        return pd.DataFrame(columns=PAYROLL_COLUMNS), []
    src = source_df.copy()
    src['Ngày'] = src['Thời gian_DT'].dt.date
    src = src[(src['Ngày'] >= start_date) & (src['Ngày'] <= end_date)]
    tip_mask = src['Sản phẩm/ Dịch vụ/ PT'].astype(str).str.strip().str.casefold().str.startswith('tip')
    tip = src[tip_mask].copy()
    tip['__key'] = tip['NV tư vấn'].apply(normalize_login_name)
    salary_map = tip.groupby('__key')['Tổng tiền'].sum().to_dict() if not tip.empty else {}
    tip_count_map = tip.groupby('__key').size().to_dict() if not tip.empty else {}

    creds = credentials_df.copy() if credentials_df is not None else pd.DataFrame()
    if creds.empty:
        return pd.DataFrame(columns=PAYROLL_COLUMNS), sorted(set(tip['NV tư vấn'].tolist())) if not tip.empty else []
    creds = creds[creds['Tên nhân viên'].astype(str).str.strip() != ''].copy()
    # Loại dòng tiêu đề phụ nếu sheet tài khoản có header lặp trong vùng dữ liệu.
    creds = creds[~creds['Tên nhân viên'].astype(str).apply(normalize_login_name).isin({
        'ten nhan vien', 'ten he thong', 'username', 'user name'
    })].copy()

    # V40: Tính lương chỉ áp dụng cho tài khoản có vai trò chính xác là ``nhanvien``.
    # Letan / quanly / locker / tapvu / admin không tạo dòng trong bảng lương mới.
    # Giữ tập khóa của toàn bộ tài khoản để Tip thuộc bộ phận bị loại không bị báo nhầm là
    # "không khớp tài khoản hệ thống".
    all_credential_keys = set(creds['Tên nhân viên'].apply(normalize_login_name).tolist())
    if 'Phân quyền' in creds.columns:
        roles = creds['Phân quyền'].astype(str).str.strip().str.lower()
        creds = creds[roles.eq('nhanvien')].copy()
    else:
        # Sheet cũ chưa có cột phân quyền được xem là nhân viên để bảo toàn tương thích.
        creds = creds.copy()
    creds['__key'] = creds['Tên nhân viên'].apply(normalize_login_name)
    penalty_map = _period_penalty_by_employee(start_date, end_date, leave_primary, leave_secondary)
    due_violation_debt_map, deferred_current_violation_map, _ = get_violation_debt_state(
        start_date, end_date, creds['Tên nhân viên'].astype(str).tolist()
    )
    employee_overrides = get_payroll_employee_overrides()
    # Tích lũy tự động lấy từ sheet TichLuy. Nếu kỳ này đã được ghi nhận trước đó,
    # bảng lương vẫn phải HIỂN THỊ đúng số tiền của kỳ đó. Việc chống cộng trùng được
    # xử lý ở bước commit/save TichLuy, không được biến số hiển thị thành 0.
    tichluy_map, _tichluy_info = get_tichluy_charge_map(
        start_date, end_date, creds['Tên nhân viên'].astype(str).tolist(), for_existing_snapshot=True
    )

    rows = []
    for idx, (_, c) in enumerate(creds.iterrows(), start=1):
        k = c['__key']
        emp_override = employee_overrides.get(k, {})
        emp_living = emp_override.get("living", default_living_expense)
        emp_locker = emp_override.get("locker", default_locker_support)
        rows.append({
            "TT": idx,
            "Tên Hệ thống": str(c.get('Tên nhân viên', '')).strip(),
            "Họ và tên": str(c.get('Họ và tên đầy đủ', '')).strip(),
            "Tiền Lương": float(salary_map.get(k, 0)),
            "Tiền Hỗ Trợ Hoàn Lại": 0.0,
            "Tích lũy": float(tichluy_map.get(k, 0)),
            "Chi Phí Sinh Hoạt": float(_money_to_float(emp_living)),
            # V50: tách riêng Vi phạm phát sinh trong kỳ và nghĩa vụ Vi phạm từ kỳ trước.
            "Tiền phạt trong tháng": float(
                max(0.0, _money_to_float(penalty_map.get(k, 0)) - _money_to_float(deferred_current_violation_map.get(k, 0)))
            ),
            "Vi phạm kỳ trước": float(_money_to_float(due_violation_debt_map.get(k, 0))),
            "Tiền ứng lương": 0.0,
            "Tiền hỗ trợ Locker": float(_money_to_float(emp_locker)),
            "Số tiền thực nhận": 0.0,
            "Email": str(c.get('Email', '')).strip(),
            "Số tài khoản ngân hàng": str(c.get('Số tài khoản ngân hàng', '')).strip().replace("'", ""),
            "Tên ngân hàng": str(c.get('Tên ngân hàng', '')).strip(),
            "Số dòng Tip": int(tip_count_map.get(k, 0)),
        })
    result = pd.DataFrame(rows, columns=PAYROLL_COLUMNS)
    result = recalculate_payroll_net(result)
    unmatched = sorted({
        str(v).strip() for v in tip.loc[~tip['__key'].isin(all_credential_keys), 'NV tư vấn'].tolist()
        if str(v).strip()
    })
    return result, unmatched


def refresh_saved_payroll_from_system(payroll_df, start_date, end_date, credentials_df=None, leave_primary=None):
    """
    Cập nhật một bản lương đã lưu bằng dữ liệu hệ thống mới nhất, nhưng giữ nguyên
    các khoản nhập tay và Tiền Lương đã lưu.

    Tự cập nhật:
    - Vi phạm trong kỳ từ Google Sheet lịch nghỉ chính 1Kz0...
    - Vi phạm kỳ trước từ sổ NoViPham đã tới hạn khấu trừ
    - Tích lũy theo sheet TichLuy và quy tắc kỳ lương
    - Phí Sinh Hoạt / Hỗ trợ Locker theo mức mặc định hoặc mức riêng hiện hành
    - Tài khoản ngân hàng / Tên ngân hàng / Email từ hồ sơ nhân viên
    - Thực nhận sau khi các khoản trên thay đổi

    Không tự đổi Tiền Lương vì dữ liệu doanh thu nguồn có thể là file Excel upload
    và không được lưu như một nguồn dữ liệu vĩnh viễn trong hệ thống.
    """
    if payroll_df is None or not isinstance(payroll_df, pd.DataFrame) or payroll_df.empty:
        return pd.DataFrame(columns=PAYROLL_COLUMNS), {"updated": 0, "missing": []}

    d = payroll_df.copy()
    creds = credentials_df.copy() if isinstance(credentials_df, pd.DataFrame) else load_credentials()
    leave_df = leave_primary.copy() if isinstance(leave_primary, pd.DataFrame) else load_backup_sheet_data()

    # Khi mở lại/tính lại bảng lương, bổ sung các nhân viên còn thiếu vào TichLuy trước.
    # D/E/F của các dòng đã có (đặc biệt người đã hoàn thành) không bị thay đổi.
    sync_tichluy_roles_and_stt(creds)

    # Dùng cùng snapshot cấu hình để tránh phát sinh nhiều request Google Sheets.
    default_living, default_locker = get_payroll_default_amounts()
    overrides = get_payroll_employee_overrides()
    penalty_map = _period_penalty_by_employee(start_date, end_date, leave_df, None)
    due_violation_debt_map, deferred_current_violation_map, _ = get_violation_debt_state(
        start_date, end_date, d.get('Tên Hệ thống', pd.Series(dtype=str)).astype(str).tolist()
    )
    tichluy_map, tichluy_info = get_tichluy_charge_map(
        start_date, end_date, d.get('Tên Hệ thống', pd.Series(dtype=str)).astype(str).tolist(),
        for_existing_snapshot=True
    )

    cred_map = {}
    if isinstance(creds, pd.DataFrame) and not creds.empty and 'Tên nhân viên' in creds.columns:
        for _, cr in creds.iterrows():
            key = normalize_login_name(cr.get('Tên nhân viên', ''))
            if key:
                cred_map[key] = cr

    missing = []
    updated = 0
    for idx, row in d.iterrows():
        emp_name = str(row.get('Tên Hệ thống', '')).strip()
        key = normalize_login_name(emp_name)
        if not key:
            continue

        # Tiền phạt luôn lấy lại theo đúng kỳ của bản lương đang mở.
        new_penalty = float(
            max(0.0, _money_to_float(penalty_map.get(key, 0)) - _money_to_float(deferred_current_violation_map.get(key, 0)))
        )
        if 'Tiền phạt trong tháng' in d.columns:
            d.at[idx, 'Tiền phạt trong tháng'] = new_penalty
        if 'Vi phạm kỳ trước' not in d.columns:
            d['Vi phạm kỳ trước'] = 0.0
        d.at[idx, 'Vi phạm kỳ trước'] = float(_money_to_float(due_violation_debt_map.get(key, 0)))
        # Tích lũy của bản lịch sử lấy đúng số kỳ đã ghi nhận; nếu chưa ghi thì tính theo quy tắc hiện tại.
        if 'Tích lũy' in d.columns:
            d.at[idx, 'Tích lũy'] = float(_money_to_float(tichluy_map.get(key, d.at[idx, 'Tích lũy'])))

        # Mức khấu trừ/hỗ trợ dùng mức riêng nếu có, nếu không dùng mức chung.
        ov = overrides.get(key, {}) if isinstance(overrides, dict) else {}
        living = ov.get('living', default_living)
        locker = ov.get('locker', default_locker)
        if 'Chi Phí Sinh Hoạt' in d.columns:
            d.at[idx, 'Chi Phí Sinh Hoạt'] = float(_money_to_float(living))
        if 'Tiền hỗ trợ Locker' in d.columns:
            d.at[idx, 'Tiền hỗ trợ Locker'] = float(_money_to_float(locker))

        # Đồng bộ thông tin hồ sơ mới nhất.
        cr = cred_map.get(key)
        if cr is None:
            missing.append(emp_name)
        else:
            if 'Số tài khoản ngân hàng' in d.columns:
                d.at[idx, 'Số tài khoản ngân hàng'] = str(cr.get('Số tài khoản ngân hàng', '')).strip().replace("'", "")
            if 'Tên ngân hàng' in d.columns:
                d.at[idx, 'Tên ngân hàng'] = str(cr.get('Tên ngân hàng', '')).strip()
            if 'Email' in d.columns:
                d.at[idx, 'Email'] = str(cr.get('Email', '')).strip()
            if 'Họ và tên' in d.columns:
                d.at[idx, 'Họ và tên'] = str(cr.get('Họ và tên đầy đủ', '')).strip()
        updated += 1

    d = recalculate_payroll_net(d)
    d = _filter_real_payroll_rows(d)
    return d, {
        "updated": updated,
        "missing": sorted(set(missing)),
        "tichluy_updated": sum(1 for v in tichluy_map.values() if float(_money_to_float(v)) > 0),
        "tichluy_info": tichluy_info,
    }


def recalculate_payroll_net(df):
    d = df.copy()
    money_cols = [
        "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy",
        "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương", "Tiền hỗ trợ Locker"
    ]
    for col in money_cols:
        if col not in d.columns:
            d[col] = 0
        d[col] = pd.to_numeric(d[col], errors='coerce').fillna(0)
    # V51: Tiền Lương = 0 thì không ghi Phí Sinh Hoạt và Tiền hỗ trợ Locker.
    # Đặt ở hàm tính thực nhận để áp dụng đồng nhất cho bảng mới, bảng lịch sử và bảng chỉnh sửa.
    zero_salary_mask = d["Tiền Lương"].abs().le(1e-9)
    d.loc[zero_salary_mask, "Chi Phí Sinh Hoạt"] = 0.0
    d.loc[zero_salary_mask, "Tiền hỗ trợ Locker"] = 0.0
    net = (
        d["Tiền Lương"] + d["Tiền Hỗ Trợ Hoàn Lại"]
        - d["Tích lũy"] - d["Chi Phí Sinh Hoạt"]
        - d["Tiền phạt trong tháng"] - d["Vi phạm kỳ trước"]
        - d["Tiền ứng lương"] - d["Tiền hỗ trợ Locker"]
    )
    # V47: KHÔNG chặn ở 0. Giá trị âm phải được hiển thị đúng để biết phần nghĩa vụ
    # chưa thanh toán và chuyển sang kỳ lương kế tiếp.
    d["Số tiền thực nhận"] = net
    return d


def save_payroll_snapshot(payroll_df, start_date, end_date, source_label, saved_by):
    try:
        ws_pay, _, err = _ensure_payroll_storage()
        if err or ws_pay is None:
            return False, err or "Không mở được vùng lưu Bảng lương.", ""
        now = datetime.now(VN_TZ)
        batch_id = f"BL-{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}-{now.strftime('%Y%m%d%H%M%S')}"
        rows = []
        for _, r in payroll_df.iterrows():
            row = [
                batch_id, start_date.strftime('%d/%m/%Y'), end_date.strftime('%d/%m/%Y'),
                now.strftime('%d/%m/%Y'), now.strftime('%H:%M:%S'), str(saved_by), str(source_label),
                int(_money_to_float(r.get('TT', 0))), str(r.get('Tên Hệ thống', '')), str(r.get('Họ và tên', '')),
                float(_money_to_float(r.get('Tiền Lương', 0))), float(_money_to_float(r.get('Tiền Hỗ Trợ Hoàn Lại', 0))),
                float(_money_to_float(r.get('Hỗ trợ dạy nghề', 0))), float(_money_to_float(r.get('Học phí', 0))),
                float(_money_to_float(r.get('Tích lũy', 0))), float(_money_to_float(r.get('Chi Phí Sinh Hoạt', 0))),
                float(_money_to_float(r.get('Tiền phạt trong tháng', 0))), float(_money_to_float(r.get('Tiền ứng lương', 0))),
                float(_money_to_float(r.get('Tiền hỗ trợ Locker', 0))), float(_money_to_float(r.get('Số tiền thực nhận', 0))),
                str(r.get('Email', '')), "'" + str(r.get('Số tài khoản ngân hàng', '')).replace("'", ""),
                str(r.get('Tên ngân hàng', '')), int(_money_to_float(r.get('Số dòng Tip', 0))),
                float(_money_to_float(r.get('Vi phạm kỳ trước', 0)))
            ]
            rows.append(row)
        if rows:
            ws_pay.append_rows(rows, value_input_option='USER_ENTERED')
        tl_ok, tl_msg = record_tichluy_contributions(payroll_df, start_date, end_date)
        debt_ok, debt_msg = commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by)
        try:
            load_payroll_history.clear()
        except Exception:
            pass
        msg = f"Đã lưu bảng lương {len(rows)} nhân viên vào hệ thống."
        if not tl_ok:
            msg += f" ⚠️ {tl_msg}"
        if debt_msg:
            msg += f" · {debt_msg}"
        if not debt_ok:
            msg += f" ⚠️ {debt_msg}"
        return True, msg, batch_id
    except Exception as e:
        return False, f"Lỗi lưu bảng lương: {e}", ""


def overwrite_payroll_snapshot(batch_id, payroll_df, start_date, end_date, source_label, saved_by):
    """Ghi đè một bản lương đã lưu, giữ nguyên Mã bản lưu và cập nhật dấu thời gian/người sửa."""
    try:
        ws_pay, _, err = _ensure_payroll_storage()
        if err or ws_pay is None:
            return False, err or "Không mở được vùng lưu Bảng lương."

        batch_id = str(batch_id).strip()
        if not batch_id:
            return False, "Thiếu Mã bản lưu cần cập nhật."

        values = _gs_call_with_backoff(ws_pay.get_all_values)
        matched_rows = []
        for row_idx, row in enumerate(values[1:], start=2):
            if row and str(row[0]).strip() == batch_id:
                matched_rows.append(row_idx)

        if not matched_rows:
            return False, f"Không tìm thấy bản lương {batch_id} để ghi đè."

        # Xóa bản cũ từ dưới lên để không làm lệch chỉ số dòng.
        for row_idx in sorted(matched_rows, reverse=True):
            ws_pay.delete_rows(row_idx)

        now = datetime.now(VN_TZ)
        rows = []
        payroll_df = _filter_real_payroll_rows(recalculate_payroll_net(payroll_df))
        for _, r in payroll_df.iterrows():
            rows.append([
                batch_id, start_date.strftime('%d/%m/%Y'), end_date.strftime('%d/%m/%Y'),
                now.strftime('%d/%m/%Y'), now.strftime('%H:%M:%S'), str(saved_by), str(source_label),
                int(_money_to_float(r.get('TT', 0))), str(r.get('Tên Hệ thống', '')), str(r.get('Họ và tên', '')),
                float(_money_to_float(r.get('Tiền Lương', 0))), float(_money_to_float(r.get('Tiền Hỗ Trợ Hoàn Lại', 0))),
                0.0, 0.0,
                float(_money_to_float(r.get('Tích lũy', 0))), float(_money_to_float(r.get('Chi Phí Sinh Hoạt', 0))),
                float(_money_to_float(r.get('Tiền phạt trong tháng', 0))), float(_money_to_float(r.get('Tiền ứng lương', 0))),
                float(_money_to_float(r.get('Tiền hỗ trợ Locker', 0))), float(_money_to_float(r.get('Số tiền thực nhận', 0))),
                str(r.get('Email', '')), "'" + str(r.get('Số tài khoản ngân hàng', '')).replace("'", ""),
                str(r.get('Tên ngân hàng', '')), int(_money_to_float(r.get('Số dòng Tip', 0))),
                float(_money_to_float(r.get('Vi phạm kỳ trước', 0)))
            ])

        if rows:
            ws_pay.append_rows(rows, value_input_option='USER_ENTERED')
        tl_ok, tl_msg = record_tichluy_contributions(payroll_df, start_date, end_date)
        debt_ok, debt_msg = commit_violation_debts_after_payroll(payroll_df, start_date, end_date, saved_by)
        try:
            load_payroll_history.clear()
        except Exception:
            pass
        msg = f"Đã ghi đè cập nhật bản lương {batch_id} cho {len(rows)} nhân viên."
        if not tl_ok:
            msg += f" ⚠️ {tl_msg}"
        if debt_msg:
            msg += f" · {debt_msg}"
        if not debt_ok:
            msg += f" ⚠️ {debt_msg}"
        return True, msg
    except Exception as e:
        return False, f"Lỗi ghi đè bảng lương: {e}"



def delete_payroll_snapshots(batch_ids):
    """
    Xóa một hoặc nhiều BẢN LƯƠNG khỏi vùng Lịch sử bảng lương đã lưu.
    Chỉ xóa các dòng trong sheet lưu lịch sử bảng lương; không đụng tới TichLuy,
    dữ liệu lịch nghỉ/vi phạm hay hồ sơ nhân viên.
    """
    try:
        wanted = {
            str(x).strip() for x in (batch_ids or [])
            if str(x).strip()
        }
        if not wanted:
            return False, "Chưa chọn bản lương cần xóa.", []

        ws_pay, _, err = _ensure_payroll_storage()
        if err or ws_pay is None:
            return False, err or "Không mở được vùng lưu Bảng lương.", []

        values = _gs_call_with_backoff(ws_pay.get_all_values)
        if len(values) < 2:
            return False, "Lịch sử bảng lương hiện đang trống.", []

        matched_rows = []
        found_batches = set()
        # Dòng 1 là header; dữ liệu bắt đầu từ dòng 2.
        for row_idx, row in enumerate(values[1:], start=2):
            batch_id = str(row[0]).strip() if row else ""
            if batch_id in wanted:
                matched_rows.append(row_idx)
                found_batches.add(batch_id)

        if not matched_rows:
            return False, "Không tìm thấy các bản lương đã chọn trong hệ thống.", []

        # Gom các dòng liên tiếp thành block để giảm số request Google Sheets.
        blocks = []
        start = prev = matched_rows[0]
        for row_idx in matched_rows[1:]:
            if row_idx == prev + 1:
                prev = row_idx
            else:
                blocks.append((start, prev))
                start = prev = row_idx
        blocks.append((start, prev))

        # Xóa từ dưới lên để chỉ số dòng phía trên không bị thay đổi.
        for start_row, end_row in reversed(blocks):
            if start_row == end_row:
                _gs_call_with_backoff(ws_pay.delete_rows, start_row)
            else:
                _gs_call_with_backoff(ws_pay.delete_rows, start_row, end_row)

        try:
            load_payroll_history.clear()
        except Exception:
            pass

        deleted = [x for x in batch_ids if str(x).strip() in found_batches]
        missing = sorted(wanted - found_batches)
        msg = (
            f"Đã xóa {len(found_batches)} bản lương khỏi Lịch sử bảng lương "
            f"({len(matched_rows)} dòng dữ liệu)."
        )
        if missing:
            msg += " Không tìm thấy: " + ", ".join(missing)
        return True, msg, deleted
    except Exception as e:
        return False, f"Lỗi xóa lịch sử bảng lương: {e}", []


@st.cache_data(ttl=60, show_spinner=False)
def load_payroll_history():
    try:
        ws_pay, _, err = _ensure_payroll_storage()
        if err or ws_pay is None:
            return pd.DataFrame(columns=PAYROLL_HISTORY_HEADERS)
        values = _gs_call_with_backoff(ws_pay.get_all_values)
        if len(values) < 2:
            return pd.DataFrame(columns=PAYROLL_HISTORY_HEADERS)
        header = values[0][:len(PAYROLL_HISTORY_HEADERS)]
        rows = []
        for r in values[1:]:
            rr = list(r[:len(PAYROLL_HISTORY_HEADERS)]) + [''] * max(0, len(PAYROLL_HISTORY_HEADERS) - len(r))
            if any(str(v).strip() for v in rr): rows.append(rr[:len(PAYROLL_HISTORY_HEADERS)])
        return pd.DataFrame(rows, columns=header if len(header)==len(PAYROLL_HISTORY_HEADERS) else PAYROLL_HISTORY_HEADERS)
    except Exception:
        return pd.DataFrame(columns=PAYROLL_HISTORY_HEADERS)


def payroll_history_to_table(history_df):
    cols = [c for c in PAYROLL_COLUMNS if c in history_df.columns]
    d = history_df[cols].copy()
    for col in [c for c in PAYROLL_COLUMNS if c.startswith('Tiền') or c in {'Tích lũy','Chi Phí Sinh Hoạt','Vi phạm kỳ trước','Số tiền thực nhận'}]:
        if col in d.columns: d[col] = pd.to_numeric(d[col], errors='coerce').fillna(0)
    if 'TT' in d.columns: d['TT'] = pd.to_numeric(d['TT'], errors='coerce').fillna(0).astype(int)
    if 'Số dòng Tip' in d.columns: d['Số dòng Tip'] = pd.to_numeric(d['Số dòng Tip'], errors='coerce').fillna(0).astype(int)
    return d


def build_payroll_excel_bytes(payroll_df, start_date, end_date):
    """Xuất toàn bộ bảng lương: A4 ngang, fit 1 trang chiều rộng, có đầy đủ tài khoản/ngân hàng."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.page import PageMargins

    d = recalculate_payroll_net(payroll_df).copy()

    # V46: luôn làm mới Họ và Tên từ hồ sơ Sheet1 cột E trước khi xuất Excel.
    # Nhờ vậy cả Export trực tiếp và file tổng hợp gửi cho Lễ tân đều dùng cùng
    # thông tin Họ và Tên mới nhất, kể cả các bản lịch sử cũ chưa lưu trường này.
    try:
        _export_creds = load_credentials()
        if isinstance(_export_creds, pd.DataFrame) and not _export_creds.empty and 'Tên nhân viên' in _export_creds.columns:
            _fullname_map = {}
            for _, _cr in _export_creds.iterrows():
                _k = normalize_login_name(_cr.get('Tên nhân viên', ''))
                if _k:
                    _fullname_map[_k] = str(_cr.get('Họ và tên đầy đủ', '')).strip()
            if 'Họ và tên' not in d.columns:
                d['Họ và tên'] = ''
            if 'Tên Hệ thống' in d.columns:
                for _idx, _rr in d.iterrows():
                    _k = normalize_login_name(_rr.get('Tên Hệ thống', ''))
                    _full = _fullname_map.get(_k, '')
                    if _full:
                        d.at[_idx, 'Họ và tên'] = _full
    except Exception:
        # Nếu Google Sheets tạm thời lỗi, vẫn cho phép export bằng dữ liệu đã có trong bảng lương.
        pass

    # V45/V50: File Excel export KHÔNG xuất Email. Giữ Họ và Tên nhân viên ở cột M
    # như cấu trúc đã chốt trước đó; cột Vi phạm kỳ trước được bổ sung sau cùng (cột N).
    export_cols = [
        "TT", "Tên Hệ thống", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
        "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Tiền ứng lương",
        "Tiền hỗ trợ Locker", "Số tiền thực nhận", "Số tài khoản ngân hàng", "Tên ngân hàng", "Họ và tên",
        "Vi phạm kỳ trước"
    ]
    for c in export_cols:
        if c not in d.columns:
            d[c] = "" if c in {"Tên Hệ thống","Số tài khoản ngân hàng","Tên ngân hàng","Họ và tên"} else 0

    wb = Workbook()
    ws = wb.active
    ws.title = "Bảng lương"
    last_col = len(export_cols)
    last_letter = get_column_letter(last_col)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws['A1'] = "BẢNG LƯƠNG NHÂN VIÊN"
    ws['A1'].font = Font(name='Arial', size=18, bold=True)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].fill = PatternFill('solid', fgColor='F3E4EC')
    ws.row_dimensions[1].height = 30
    ws['A2'] = "KỲ LƯƠNG"
    ws['A2'].font = Font(name='Arial', size=11, bold=True)
    ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=last_col)
    ws['B2'] = f"Từ ngày {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')}"
    ws['B2'].font = Font(name='Arial', size=11, bold=True)

    # Dùng bộ tiêu đề chuẩn, riêng cột Họ và tên đổi tên hiển thị theo yêu cầu export.
    header_labels = dict(PAYROLL_DISPLAY_LABELS)
    header_labels["Họ và tên"] = "Họ và Tên nhân viên"
    for c, h in enumerate(export_cols, start=1):
        display_header = header_labels.get(h, h)
        cell = ws.cell(row=3, column=c, value=display_header)
        cell.font = Font(name='Arial', size=9, bold=True, color='000000')
        cell.fill = PatternFill('solid', fgColor='A1948C')
        # Riêng Tên ngân hàng không wrap text theo yêu cầu.
        cell.alignment = Alignment(
            horizontal='center', vertical='center',
            wrap_text=False if h == 'Tên ngân hàng' else True
        )
    ws.row_dimensions[3].height = 52
    thin = Side(style='thin', color='A6A6A6')

    start_row = 4
    money_cols = {"Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương", "Tiền hỗ trợ Locker", "Số tiền thực nhận"}
    non_positive_fill = PatternFill('solid', fgColor='FFF2CC')
    for i, (_, r) in enumerate(d.iterrows(), start=start_row):
        for j, col in enumerate(export_cols, start=1):
            val = r.get(col, '')
            if col in money_cols:
                val = float(_money_to_float(val))
            elif col == 'TT':
                val = int(_money_to_float(val))
            elif col == 'Số tài khoản ngân hàng':
                val = str(val).replace("'", "")
            ws.cell(row=i, column=j, value=val)
        # Tài khoản ngân hàng buộc kiểu Text để giữ số 0 đầu.
        bank_col = export_cols.index('Số tài khoản ngân hàng') + 1
        ws.cell(row=i, column=bank_col).number_format = '@'

        # V45: tô vàng TOÀN BỘ dòng khi Thực nhận <= 0 để Admin dễ kiểm tra.
        if _money_to_float(r.get('Số tiền thực nhận', 0)) <= 0:
            for j in range(1, last_col + 1):
                ws.cell(row=i, column=j).fill = non_positive_fill

    total_row = start_row + len(d)
    ws.cell(total_row, 2, "TỔNG")
    for j, col in enumerate(export_cols, start=1):
        if col in money_cols:
            values = d[col].apply(_money_to_float)
            # V44: riêng cột J / Thực nhận, dòng TỔNG chỉ cộng những nhân viên có
            # Thực nhận > 0. Giá trị âm hoặc 0 không được cộng vào tổng chi trả.
            total_value = values[values > 0].sum() if col == "Số tiền thực nhận" else values.sum()
            ws.cell(total_row, j, float(total_value))
    for c in range(1, last_col + 1):
        ws.cell(total_row, c).font = Font(name='Arial', size=10, bold=True)
        ws.cell(total_row, c).fill = PatternFill('solid', fgColor='E2E3E5')

    for row in ws.iter_rows(min_row=3, max_row=total_row, min_col=1, max_col=last_col):
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if cell.row >= 4:
                cell.font = Font(name='Arial', size=9, bold=(cell.row == total_row))
                # Không wrap cột Tên ngân hàng; các cột còn lại giữ wrap để gọn trên A4 ngang.
                col_name = export_cols[cell.column - 1]
                cell.alignment = Alignment(
                    vertical='center',
                    wrap_text=False if col_name == 'Tên ngân hàng' else True
                )
    for j, col in enumerate(export_cols, start=1):
        if col in money_cols:
            for row in range(4, total_row + 1):
                ws.cell(row, j).number_format = '#,##0'
                ws.cell(row, j).alignment = Alignment(horizontal='right', vertical='center')

    # Auto-fit có giới hạn để vẫn vừa A4 ngang.
    for j, col in enumerate(export_cols, start=1):
        max_len = len(col)
        for row in range(4, min(total_row, 60) + 1):
            max_len = max(max_len, len(str(ws.cell(row, j).value or '')))
        if col == 'TT':
            # Cột TT cũ tối thiểu rộng 6; giảm 80% còn khoảng 20% chiều rộng.
            width = 1.2
        elif col in money_cols:
            width = min(max(max_len + 2, 12), 17)
        elif col == 'Tên ngân hàng':
            # Không wrap nên cho phép cột rộng hơn để tên ngân hàng nằm trên một dòng.
            width = min(max(max_len + 2, 22), 32)
        elif col == 'Họ và tên':
            width = min(max(max_len + 2, 18), 28)
        else:
            width = min(max(max_len + 2, 6), 19)
        ws.column_dimensions[get_column_letter(j)].width = width
    for r in range(4, total_row + 1):
        ws.row_dimensions[r].height = 20

    ws.freeze_panes = 'A4'
    ws.auto_filter.ref = f"A3:{last_letter}{max(3,total_row-1)}"
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins = PageMargins(left=0.18, right=0.18, top=0.3, bottom=0.3, header=0.12, footer=0.12)
    ws.print_options.horizontalCentered = True
    ws.print_title_rows = '1:3'
    ws.print_area = f"A1:{last_letter}{total_row}"
    ws.sheet_view.zoomScale = 65
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()



def build_employee_payroll_excel_bytes(employee_row, start_date, end_date, violation_details=None):
    """Tạo phiếu lương cá nhân theo đúng bố cục nội dung email gửi nhân viên."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.worksheet.page import PageMargins

    emp = str(employee_row.get('Tên Hệ thống', '')).strip()
    # Bản V21 đã bỏ cột Họ và Tên khỏi bảng lương; nếu dữ liệu cũ còn cột này thì vẫn ưu tiên dùng.
    full = str(employee_row.get('Họ và tên', '')).strip()
    display_name = full if full and full.lower() not in {'nan', 'none'} else emp

    items = [
        ('Tiền Lương', 'Tiền Lương'),
        ('Tiền Hỗ Trợ Hoàn Lại', 'Tiền Hỗ Trợ Hoàn Lại'),
        ('Tích lũy', 'Tích lũy'),
        ('Chi Phí Sinh Hoạt', 'Chi Phí Sinh Hoạt'),
        ('Tiền phạt trong tháng', 'Tiền phạt trong tháng'),
        ('Vi phạm kỳ trước', 'Vi phạm kỳ trước'),
        ('Tiền ứng lương', 'Tiền ứng lương'),
        ('Tiền hỗ trợ Locker', 'Tiền hỗ trợ Locker'),
        ('Số tiền thực nhận', 'Số tiền thực nhận'),
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = 'Bảng lương'
    ws.sheet_view.showGridLines = False

    thin = Side(style='thin', color='D9D9D9')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill('solid', fgColor='A1948C')

    # Lời chào / thông tin kỳ lương giống nội dung email.
    ws.merge_cells('A1:B1')
    ws['A1'] = f'Chào {display_name},'
    ws['A1'].font = Font(name='Arial', size=14, bold=False, color='000000')
    ws['A1'].alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[1].height = 24

    ws.merge_cells('A3:B3')
    ws['A3'] = f"VERA SPA gửi bảng lương kỳ từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')}."
    ws['A3'].font = Font(name='Arial', size=12, color='000000')
    ws['A3'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 30

    # Bảng Khoản mục / Số tiền.
    ws['A5'] = 'Khoản mục'
    ws['B5'] = 'Số tiền'
    for c in ('A5', 'B5'):
        ws[c].font = Font(name='Arial', size=11, bold=True, color='000000')
        ws[c].fill = header_fill
        ws[c].alignment = Alignment(horizontal='center', vertical='center')
        ws[c].border = border
    ws.row_dimensions[5].height = 25

    row = 6
    for label, source_col in items:
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=float(_money_to_float(employee_row.get(source_col, 0))))
        ws.cell(row=row, column=1).font = Font(name='Arial', size=11, bold=False)
        ws.cell(row=row, column=2).font = Font(name='Arial', size=11, bold=(source_col == 'Số tiền thực nhận'))
        ws.cell(row=row, column=1).alignment = Alignment(horizontal='left', vertical='center')
        ws.cell(row=row, column=2).alignment = Alignment(horizontal='right', vertical='center')
        ws.cell(row=row, column=2).number_format = '#,##0 "VNĐ"'
        ws.cell(row=row, column=1).border = border
        ws.cell(row=row, column=2).border = border
        ws.row_dimensions[row].height = 23
        row += 1

    net = float(_money_to_float(employee_row.get('Số tiền thực nhận', 0)))
    net_row = row + 1
    ws.merge_cells(start_row=net_row, start_column=1, end_row=net_row, end_column=2)
    ws.cell(net_row, 1, f'Số tiền thực nhận: {net:,.0f} VNĐ')
    ws.cell(net_row, 1).font = Font(name='Arial', size=13, bold=True, color='000000')
    ws.cell(net_row, 1).alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[net_row].height = 28

    note_row = net_row + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=2)
    ws.cell(note_row, 1, 'Vui lòng kiểm tra và phản hồi nếu có sai sót.')
    ws.cell(note_row, 1).font = Font(name='Arial', size=11)
    ws.cell(note_row, 1).alignment = Alignment(horizontal='left', vertical='center')

    sign_row = note_row + 2
    ws.merge_cells(start_row=sign_row, start_column=1, end_row=sign_row, end_column=2)
    ws.cell(sign_row, 1, 'Trân trọng,')
    ws.cell(sign_row, 1).font = Font(name='Arial', size=11)

    ws.merge_cells(start_row=sign_row + 1, start_column=1, end_row=sign_row + 1, end_column=2)
    ws.cell(sign_row + 1, 1, 'VERA SPA')
    ws.cell(sign_row + 1, 1).font = Font(name='Arial', size=12, bold=True)

    # Căn vừa trang và dễ đọc trên điện thoại/PC khi mở file.
    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 22
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.page_margins = PageMargins(left=0.45, right=0.45, top=0.5, bottom=0.5, header=0.2, footer=0.2)
    ws.print_options.horizontalCentered = True
    ws.print_area = f'A1:B{sign_row + 1}'
    ws.sheet_view.zoomScale = 90

    # Sheet chi tiết vi phạm để nhân viên đối chiếu đúng kỳ lương.
    ws_vp = wb.create_sheet('Chi tiết vi phạm')
    ws_vp.sheet_view.showGridLines = False
    vp_headers = ['Ngày', 'Lý do nghỉ', 'Chi tiết', 'Phạt vi phạm']
    for j, h in enumerate(vp_headers, start=1):
        c = ws_vp.cell(1, j, h)
        c.font = Font(name='Arial', size=10, bold=True, color='000000')
        c.fill = header_fill
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = border
    vp_df = violation_details.copy() if isinstance(violation_details, pd.DataFrame) else pd.DataFrame(columns=vp_headers)
    for c in vp_headers:
        if c not in vp_df.columns: vp_df[c] = ''
    if vp_df.empty:
        ws_vp.merge_cells('A2:D2')
        ws_vp['A2'] = 'Không có vi phạm bị phạt trong kỳ lương này.'
        ws_vp['A2'].font = Font(name='Arial', size=10, italic=True)
    else:
        for i, (_, vr) in enumerate(vp_df[vp_headers].iterrows(), start=2):
            for j, h in enumerate(vp_headers, start=1):
                val = float(_money_to_float(vr.get(h,0))) if h == 'Phạt vi phạm' else str(vr.get(h,'') or '')
                cell = ws_vp.cell(i, j, val)
                cell.border = border
                cell.font = Font(name='Arial', size=10)
                cell.alignment = Alignment(vertical='top', wrap_text=(h in {'Lý do nghỉ','Chi tiết'}))
                if h == 'Phạt vi phạm':
                    cell.number_format = '#,##0 "VNĐ"'
                    cell.alignment = Alignment(horizontal='right', vertical='top')
        total_r = len(vp_df) + 2
        ws_vp.cell(total_r, 3, 'TỔNG VI PHẠM').font = Font(name='Arial', size=10, bold=True)
        ws_vp.cell(total_r, 4, float(vp_df['Phạt vi phạm'].apply(_money_to_float).sum()))
        ws_vp.cell(total_r, 4).font = Font(name='Arial', size=10, bold=True)
        ws_vp.cell(total_r, 4).number_format = '#,##0 "VNĐ"'
    ws_vp.column_dimensions['A'].width = 14
    ws_vp.column_dimensions['B'].width = 24
    ws_vp.column_dimensions['C'].width = 42
    ws_vp.column_dimensions['D'].width = 18
    ws_vp.freeze_panes = 'A2'
    ws_vp.page_setup.orientation = ws_vp.ORIENTATION_LANDSCAPE
    ws_vp.page_setup.paperSize = ws_vp.PAPERSIZE_A4
    ws_vp.sheet_properties.pageSetUpPr.fitToPage = True
    ws_vp.page_setup.fitToWidth = 1
    ws_vp.page_setup.fitToHeight = 0

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()

def send_payroll_email(sender_email, sender_password, to_email, employee_row, start_date, end_date, violation_details=None):
    try:
        emp = str(employee_row.get('Tên Hệ thống',''))
        full = str(employee_row.get('Họ và tên',''))
        subject = f"Bảng lương {emp} - {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')}"
        money_fields = [
            "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy", "Chi Phí Sinh Hoạt",
            "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương", "Tiền hỗ trợ Locker", "Số tiền thực nhận"
        ]
        html_rows = "".join(
            f"<tr><td style='padding:6px;border:1px solid #ddd'>{field}</td><td style='padding:6px;border:1px solid #ddd;text-align:right'>{_money_to_float(employee_row.get(field,0)):,.0f} VNĐ</td></tr>"
            for field in money_fields
        )
        vp_df = violation_details.copy() if isinstance(violation_details, pd.DataFrame) else pd.DataFrame()
        if not vp_df.empty:
            vp_rows = "".join(
                f"<tr><td style='padding:5px;border:1px solid #ddd'>{str(vr.get('Ngày',''))}</td>"
                f"<td style='padding:5px;border:1px solid #ddd'>{str(vr.get('Lý do nghỉ',''))}</td>"
                f"<td style='padding:5px;border:1px solid #ddd'>{str(vr.get('Chi tiết',''))}</td>"
                f"<td style='padding:5px;border:1px solid #ddd;text-align:right'>{_money_to_float(vr.get('Phạt vi phạm',0)):,.0f} VNĐ</td></tr>"
                for _, vr in vp_df.iterrows()
            )
            vp_total = vp_df['Phạt vi phạm'].apply(_money_to_float).sum() if 'Phạt vi phạm' in vp_df.columns else 0
            violation_html = f"""
            <p><b>Chi tiết vi phạm trong kỳ:</b></p>
            <table style='border-collapse:collapse;min-width:620px'>
            <tr><th style='padding:6px;border:1px solid #ddd;background:#A1948C;white-space:normal;overflow-wrap:anywhere;word-break:break-word'>Ngày</th><th style='padding:6px;border:1px solid #ddd;background:#A1948C;white-space:normal;overflow-wrap:anywhere;word-break:break-word'>Lý do</th><th style='padding:6px;border:1px solid #ddd;background:#A1948C;white-space:normal;overflow-wrap:anywhere;word-break:break-word'>Chi tiết</th><th style='padding:6px;border:1px solid #ddd;background:#A1948C;white-space:normal;overflow-wrap:anywhere;word-break:break-word'>Phạt</th></tr>
            {vp_rows}
            </table><p><b>Tổng vi phạm: {vp_total:,.0f} VNĐ</b></p>
            """
        else:
            violation_html = "<p><b>Chi tiết vi phạm trong kỳ:</b> Không có vi phạm bị phạt.</p>"
        html = f"""
        <html><body style='font-family:Arial,sans-serif'>
        <p>Chào <b>{full or emp}</b>,</p>
        <p>VERA SPA gửi bảng lương kỳ từ <b>{start_date.strftime('%d/%m/%Y')}</b> đến <b>{end_date.strftime('%d/%m/%Y')}</b>.</p>
        <table style='border-collapse:collapse;min-width:520px'>
        <tr><th style='padding:7px;border:1px solid #ddd;background:#A1948C;color:#000;white-space:normal;overflow-wrap:anywhere;word-break:break-word'>Khoản mục</th><th style='padding:7px;border:1px solid #ddd;background:#A1948C;color:#000;white-space:normal;overflow-wrap:anywhere;word-break:break-word'>Số tiền</th></tr>
        {html_rows}
        </table>
        <p><b>Số tiền thực nhận: {_money_to_float(employee_row.get('Số tiền thực nhận',0)):,.0f} VNĐ</b></p>
        {violation_html}
        <p>Vui lòng kiểm tra và phản hồi nếu có sai sót.</p><p>Trân trọng,<br><b>VERA SPA</b></p>
        </body></html>
        """
        msg = MIMEMultipart()
        msg['From'] = f"Vera Spa <{sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html, 'html'))
        attachment = build_employee_payroll_excel_bytes(employee_row, start_date, end_date, violation_details)
        part = MIMEApplication(attachment, _subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        part.add_header('Content-Disposition', 'attachment', filename=f"BangLuong_{normalize_login_name(emp).replace(' ','_')}_{start_date.strftime('%d%m%Y')}_{end_date.strftime('%d%m%Y')}.xlsx")
        msg.attach(part)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True, "Thành công"
    except Exception as e:
        return False, str(e)

def send_payroll_summary_email(sender_email, sender_password, to_email, recipient_name, payroll_df, start_date, end_date):
    """Gửi file bảng lương TỔNG HỢP cho đúng một Lễ tân được Admin chỉ định."""
    try:
        subject = f"Bảng lương tổng hợp - {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')}"
        total_salary = recalculate_payroll_net(payroll_df)['Tiền Lương'].apply(_money_to_float).sum()
        total_net = recalculate_payroll_net(payroll_df)['Số tiền thực nhận'].apply(_money_to_float).sum()
        html = f"""
        <html><body style='font-family:Arial,sans-serif'>
        <p>Chào <b>{recipient_name}</b>,</p>
        <p>VERA SPA gửi file <b>bảng lương tổng hợp</b> kỳ <b>{start_date.strftime('%d/%m/%Y')}</b> đến <b>{end_date.strftime('%d/%m/%Y')}</b>.</p>
        <p>Số nhân viên: <b>{len(payroll_df)}</b><br>
        Tổng tiền lương: <b>{total_salary:,.0f} VNĐ</b><br>
        Tổng thực nhận: <b>{total_net:,.0f} VNĐ</b></p>
        <p>File Excel đầy đủ được đính kèm email này.</p>
        <p>Trân trọng,<br><b>VERA SPA</b></p>
        </body></html>
        """
        msg = MIMEMultipart()
        msg['From'] = f"Vera Spa <{sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html, 'html'))
        # V46: dùng đúng bộ export chuẩn nên file gửi Lễ tân cũng:
        # - bỏ Email, thay bằng Họ và Tên từ Sheet1 cột E
        # - tô vàng toàn bộ dòng có Thực nhận <= 0
        attachment = build_payroll_excel_bytes(payroll_df, start_date, end_date)
        part = MIMEApplication(attachment, _subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        part.add_header('Content-Disposition', 'attachment', filename=f"BangLuong_TongHop_{start_date.strftime('%d%m%Y')}_{end_date.strftime('%d%m%Y')}.xlsx")
        msg.attach(part)
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True, "Đã gửi bảng lương tổng hợp thành công."
    except Exception as e:
        return False, str(e)


# Tải dữ liệu
ensure_credential_control_columns()
df_credentials = load_credentials() 
df_backup = load_backup_sheet_data()
df_leave_secondary = load_secondary_leave_sheet_data()
df_loai_nghi_gsheet = load_loai_nghi_from_gsheet()
GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

with st.spinner("Đang tải dữ liệu hệ thống..."):
    df_lich, df_nv_excel, df_loai_nghi_excel = load_lich_nghi(GDRIVE_LINK) 

df_loai_nghi = df_loai_nghi_gsheet if not df_loai_nghi_gsheet.empty else df_loai_nghi_excel

if df_lich.empty or df_nv_excel.empty:
    st.warning("Hệ thống chưa tìm thấy dữ liệu.")
    st.stop()

# --- ĐĂNG NHẬP ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = ""
    st.session_state.current_role = ""
if "birthday_login_event" not in st.session_state:
    st.session_state.birthday_login_event = False

# Tự đăng nhập bằng token đã nhớ (không lưu mật khẩu ở localStorage).
if not st.session_state.logged_in:
    try:
        remembered_token = st.query_params.get('remember_token', '')
        if _is_valid_fallback_admin_token(remembered_token):
            st.session_state.logged_in = True
            st.session_state.current_user = "Quản Trị Viên"
            st.session_state.current_role = "admin"
            st.session_state.birthday_login_event = True
        else:
            remembered_row = validate_remember_token(remembered_token, df_credentials) if remembered_token else None
            if remembered_row is not None:
                st.session_state.logged_in = True
                st.session_state.current_user = str(remembered_row['Tên nhân viên']).strip()
                st.session_state.current_role = str(remembered_row.get('Phân quyền', 'nhanvien')).strip().lower()
                st.session_state.birthday_login_event = True
                _set_default_page_after_login(st.session_state.current_role)
            elif remembered_token:
                # Token bị khóa/sai/đã thu hồi -> xóa khỏi trình duyệt.
                st.query_params['forget_login'] = '1'
                try: del st.query_params['remember_token']
                except Exception: pass
    except Exception:
        pass

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    # Trên điện thoại đưa nút Đăng nhập sang phải. Khối CSS này chỉ tồn tại ở màn hình login.
    st.markdown("""
    <style>
    @media (max-width: 768px) {
        div[data-testid="stFormSubmitButton"] { display:flex !important; justify-content:flex-end !important; }
        div[data-testid="stFormSubmitButton"] > button { width:auto !important; min-width:145px !important; }
    }
    </style>
    """, unsafe_allow_html=True)
    with st.form("login_form"):
        username_input = st.text_input("Tên đăng nhập", autocomplete="username").strip()
        password_input = st.text_input("Mật khẩu", type="password", autocomplete="current-password")
        st.caption("🔐 Thiết bị này sẽ duy trì đăng nhập cho tới khi bạn bấm Đăng xuất.")

        # Nút lưu đăng nhập dùng token bảo mật hiện có; không lưu mật khẩu dạng chữ thường.
        c_save_login, c_login = st.columns([2, 1])
        with c_save_login:
            save_credentials_submit = st.form_submit_button("💾 Lưu tên đăng nhập và mật khẩu")
        with c_login:
            login_submit = st.form_submit_button("Đăng Nhập")

        if login_submit or save_credentials_submit:
            input_name_norm = normalize_login_name(username_input)

            # Tài khoản quản trị dự phòng cũ: vẫn chấp nhận HOA/thường ở tên đăng nhập.
            if input_name_norm == normalize_login_name('admin') and password_matches(password_input, '32531235'):
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.session_state.current_role = "admin"
                st.session_state.birthday_login_event = True
                # Admin dự phòng cũng được duy trì đăng nhập cho tới khi bấm Đăng xuất.
                st.query_params['remember_token'] = _fallback_admin_remember_token()
                st.rerun()
            else:
                user_found = False
                locked_account = False
                matched_row = None

                for _, row in df_credentials.iterrows():
                    db_name = str(row['Tên nhân viên']).strip()
                    if input_name_norm == normalize_login_name(db_name):
                        matched_row = row
                        if is_locked_value(row.get('Khóa đăng nhập', '')):
                            locked_account = True
                            break
                        if password_matches(password_input, row.get('Mật khẩu', '')):
                            st.session_state.logged_in = True
                            st.session_state.current_user = db_name
                            st.session_state.current_role = str(row.get('Phân quyền', 'nhanvien')).strip().lower()
                            st.session_state.birthday_login_event = True
                            _set_default_page_after_login(st.session_state.current_role)
                            user_found = True
                            break

                if locked_account:
                    st.error("🔒 Tài khoản này đang bị khóa đăng nhập tạm thời. Vui lòng liên hệ Admin.")
                elif user_found:
                    token = create_remember_token(st.session_state.current_user)
                    if token:
                        st.query_params['remember_token'] = token
                    st.rerun()
                else:
                    st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop()


# Vai trò tạp vụ không được hiển thị bất kỳ dữ liệu/chức năng nghiệp vụ nào.
# Chỉ giữ nút Đăng xuất để tài khoản không bị kẹt phiên đăng nhập.
if st.session_state.current_role == "tapvu":
    st.markdown("""
        <style>
            [data-testid="collapsedControl"] { display: none !important; }
            [data-testid="stSidebar"] { display: none !important; }
            .custom-main-title { display: none !important; }
        </style>
    """, unsafe_allow_html=True)
    st.markdown("### 🎂 Sinh nhật")
    render_manual_birthday_check(df_credentials, key_prefix="tapvu_birthday_manual")
    _blank1, _blank2, _logout_col = st.columns([6, 2, 2])
    with _logout_col:
        if st.button("🚪 Đăng xuất", use_container_width=True, key="tapvu_logout"):
            if st.session_state.current_user and st.session_state.current_user != "Quản Trị Viên":
                revoke_remember_token(st.session_state.current_user)
            st.session_state.logged_in = False
            st.session_state.current_user = ""
            st.session_state.current_role = ""
            st.session_state.birthday_login_event = False
            st.session_state.pop("birthday_notice_count_today", None)
            st.session_state.pop("birthday_notice_muted_today", None)
            st.session_state.pop("app_page", None)
            st.query_params['forget_login'] = '1'
            try: del st.query_params['remember_token']
            except Exception: pass
            try: del st.query_params['page']
            except Exception: pass
            st.rerun()
    st.stop()


# ==========================================
# ĐIỀU HƯỚNG THEO TỪNG TRANG CHỨC NĂNG
# ==========================================
is_admin_letan = st.session_state.current_role in ["admin", "letan", "quanly"]

PAGE_SLUGS = {
    "🧭 Bảng Tour": "bang-tour",
    "💰 Thống kê lương": "thong-ke-luong",
    "📅 Đăng ký & Thống kê nghỉ phép": "dang-ky-thong-ke-nghi-phep",
    "✏️ Quản lý lịch nghỉ": "quan-ly-lich-nghi",
    "⏰ Thiết lập ca làm việc": "thiet-lap-ca",
    "👥 Danh sách nhân sự": "danh-sach-nhan-su",
    "➕ Thêm nhân viên": "them-nhan-vien",
    "✏️ Sửa / Xóa nhân viên": "sua-xoa-nhan-vien",
    "🔒 Khóa đăng nhập": "khoa-dang-nhap",
    "🔐 Khóa quyền đăng ký": "khoa-quyen-dang-ky",
    "🔄 Đồng bộ dữ liệu": "dong-bo-du-lieu",
    "⚙️ Cấu hình cột": "cau-hinh-cot",
    "👤 Hồ sơ cá nhân": "ho-so-ca-nhan",
}
SLUG_TO_PAGE = {v: k for k, v in PAGE_SLUGS.items()}

payroll_letan_enabled = get_payroll_letan_enabled()

if st.session_state.current_role == "admin":
    allowed_pages = [
        "🧭 Bảng Tour", "💰 Thống kê lương", "📅 Đăng ký & Thống kê nghỉ phép", "✏️ Quản lý lịch nghỉ",
        "⏰ Thiết lập ca làm việc", "👥 Danh sách nhân sự", "➕ Thêm nhân viên",
        "✏️ Sửa / Xóa nhân viên", "🔒 Khóa đăng nhập", "🔐 Khóa quyền đăng ký",
        "🔄 Đồng bộ dữ liệu", "⚙️ Cấu hình cột"
    ]
elif st.session_state.current_role in ["letan", "quanly"]:
    allowed_pages = [
        "🧭 Bảng Tour", "📅 Đăng ký & Thống kê nghỉ phép", "✏️ Quản lý lịch nghỉ",
        "⏰ Thiết lập ca làm việc", "👥 Danh sách nhân sự", "➕ Thêm nhân viên",
        "✏️ Sửa / Xóa nhân viên", "👤 Hồ sơ cá nhân"
    ]
    if payroll_letan_enabled:
        allowed_pages.insert(1, "💰 Thống kê lương")
elif st.session_state.current_role == "locker":
    # Locker chỉ được xem Bảng Tour và tự cập nhật hồ sơ; không hiện các bảng/chức năng đính kèm khác.
    allowed_pages = ["🧭 Bảng Tour", "👤 Hồ sơ cá nhân"]
else:
    allowed_pages = [
        "🧭 Bảng Tour", "📅 Đăng ký & Thống kê nghỉ phép", "✏️ Quản lý lịch nghỉ",
        "👤 Hồ sơ cá nhân"
    ]

# Đọc trang từ URL để nút Back/Forward và swipe trên điện thoại hoạt động.
requested_slug = str(st.query_params.get("page", "")).strip()
requested_page = SLUG_TO_PAGE.get(requested_slug)
if requested_page in allowed_pages:
    st.session_state.app_page = requested_page
elif st.session_state.get("app_page") not in allowed_pages:
    preferred_default = DEFAULT_LEAVE_PAGE if st.session_state.current_role in {"letan", "quanly", "nhanvien"} and DEFAULT_LEAVE_PAGE in allowed_pages else allowed_pages[0]
    st.session_state.app_page = preferred_default
selected_page = st.session_state.app_page


def open_app_page(page_name):
    if page_name not in allowed_pages:
        return
    st.session_state.app_page = page_name
    st.query_params["page"] = PAGE_SLUGS[page_name]
    st.rerun()

# Nhân viên vẫn ẩn sidebar; Admin/Lễ tân/Quản lý dùng mỗi chức năng = một nút riêng.
if st.session_state.current_role in ["nhanvien", "locker"]:
    st.markdown("""
        <style>
            [data-testid="collapsedControl"] { display: none !important; }
            [data-testid="stSidebar"] { display: none !important; }
        </style>
    """, unsafe_allow_html=True)
else:
    st.sidebar.title("📌 MENU CHỨC NĂNG")
    for page_name in allowed_pages:
        if st.sidebar.button(page_name, key=f"nav_{PAGE_SLUGS[page_name]}", use_container_width=True,
                             type="primary" if selected_page == page_name else "secondary"):
            open_app_page(page_name)
    if st.session_state.current_role == "admin":
        st.sidebar.markdown("---")
        st.sidebar.subheader("💰 QUYỀN LỄ TÂN - BẢNG LƯƠNG")
        if payroll_letan_enabled:
            st.sidebar.success("🟢 Lễ tân đang được phép mở Bảng lương")
            if st.sidebar.button("🔒 Đóng quyền Lễ tân xem lương", use_container_width=True):
                ok, msg = set_payroll_letan_enabled(False)
                (st.sidebar.success if ok else st.sidebar.error)(msg)
                if ok: st.rerun()
        else:
            st.sidebar.warning("🔴 Lễ tân đang bị khóa Bảng lương")
            if st.sidebar.button("🔓 Mở quyền Lễ tân xem lương", use_container_width=True):
                ok, msg = set_payroll_letan_enabled(True)
                (st.sidebar.success if ok else st.sidebar.error)(msg)
                if ok: st.rerun()

# --- GIAO DIỆN HEADER ---
st.write("")
col_title, col_logout = st.columns([8, 2])
with col_title:
    st.markdown("""
        <div class='custom-main-title'>WELCOME TO VERA SPA</div>
    """, unsafe_allow_html=True)
with col_logout:
    if st.button("🚪 Đăng xuất", use_container_width=True):
        if st.session_state.current_user and st.session_state.current_user != "Quản Trị Viên":
            revoke_remember_token(st.session_state.current_user)
        st.session_state.logged_in = False
        st.session_state.current_user = ""
        st.session_state.current_role = ""
        st.session_state.birthday_login_event = False
        st.session_state.pop("birthday_notice_count_today", None)
        st.session_state.pop("birthday_notice_muted_today", None)
        st.session_state.pop("app_page", None)
        st.query_params['forget_login'] = '1'
        try: del st.query_params['remember_token']
        except Exception: pass
        try: del st.query_params['page']
        except Exception: pass
        st.rerun()

# V48: Thông báo sinh nhật đầu tháng cho Admin/Quản lý/Lễ tân.
render_birthday_login_notice(df_credentials)
# V51: nút xem sinh nhật chủ động áp dụng cho mọi tài khoản.
with st.expander("🎂 Sinh nhật nhân sự trong tháng", expanded=False):
    render_manual_birthday_check(
        df_credentials,
        key_prefix=f"birthday_manual_{normalize_login_name(st.session_state.current_user) or 'user'}"
    )

# Nhân viên: thanh nút điều hướng ngay trên nội dung, phù hợp điện thoại.
if st.session_state.current_role in ["nhanvien", "locker"]:
    nav_cols = st.columns(2)
    for idx, page_name in enumerate(allowed_pages):
        with nav_cols[idx % 2]:
            if st.button(page_name, key=f"mobile_nav_{PAGE_SLUGS[page_name]}", use_container_width=True,
                         type="primary" if selected_page == page_name else "secondary"):
                open_app_page(page_name)

# Swipe trái/phải giữa các trang chức năng trên điện thoại. Nếu lịch sử có sẵn,
# ưu tiên back/forward; URL page giúp trạng thái được phục hồi chính xác.
components.html(f"""
<script>
(function() {{
    try {{
        const parentWin = window.parent, doc = parentWin.document;
        if (!parentWin.matchMedia('(max-width: 768px)').matches) return;
        const pages = {json.dumps([PAGE_SLUGS[p] for p in allowed_pages], ensure_ascii=False)};
        const current = {json.dumps(PAGE_SLUGS[selected_page])};
        let x0=null, y0=null, target0=null;
        doc.addEventListener('touchstart', function(e) {{
            if (!e.touches || e.touches.length !== 1) return;
            x0=e.touches[0].clientX; y0=e.touches[0].clientY; target0=e.target;
        }}, {{passive:true}});
        doc.addEventListener('touchend', function(e) {{
            if (x0===null || !e.changedTouches || e.changedTouches.length!==1) return;
            const t=target0; target0=null;
            if (t && t.closest && t.closest('input,textarea,button,a,[data-baseweb="select"],[data-testid="stDataFrame"],[data-testid="stDataEditor"]')) {{x0=y0=null;return;}}
            const dx=e.changedTouches[0].clientX-x0, dy=e.changedTouches[0].clientY-y0; x0=y0=null;
            if (Math.abs(dx)<90 || Math.abs(dx)<Math.abs(dy)*1.4) return;
            const i=pages.indexOf(current); if(i<0) return;
            const ni=dx<0 ? i+1 : i-1;
            if(ni<0 || ni>=pages.length) return;
            const url=new URL(parentWin.location.href); url.searchParams.set('page', pages[ni]);
            parentWin.location.href=url.toString();
        }}, {{passive:true}});
    }} catch(e) {{ console.debug('Vera swipe:',e); }}
}})();
</script>
""", height=0, width=0)

# Hồ sơ cá nhân là một trang riêng và KHÔNG hiển thị cho Admin.
if selected_page == "👤 Hồ sơ cá nhân" and st.session_state.current_role != "admin":
    st.subheader(f"👤 Cập nhật hồ sơ cá nhân: {st.session_state.current_user}")
    cred_row = df_credentials[df_credentials['Tên nhân viên'].apply(normalize_login_name) == normalize_login_name(st.session_state.current_user)]
    curr_fullname = str(cred_row.iloc[0].get('Họ và tên đầy đủ', '')).strip() if not cred_row.empty else ""
    curr_dob = str(cred_row.iloc[0].get('Ngày sinh', '')).strip() if not cred_row.empty else ""
    curr_phone = str(cred_row.iloc[0].get('Điện thoại', '')).strip().replace("'", "") if not cred_row.empty else ""
    curr_email = str(cred_row.iloc[0].get('Email', '')).strip() if not cred_row.empty else ""
    curr_address = str(cred_row.iloc[0].get('Địa chỉ', '')).strip() if not cred_row.empty else ""
    curr_bank_account = str(cred_row.iloc[0].get('Số tài khoản ngân hàng', '')).strip().replace("'", "") if not cred_row.empty else ""
    curr_bank_name = str(cred_row.iloc[0].get('Tên ngân hàng', '')).strip() if not cred_row.empty else ""

    # Không dùng st.form ở khu vực địa chỉ để Tỉnh/Thành -> Phường/Xã cập nhật ngay khi chọn.
    old_pass = st.text_input("Mật khẩu hiện tại (🔴 Bắt buộc để lưu)", type="password", key="profile_old_pass")
    new_pass = st.text_input("Mật khẩu mới (Bỏ trống nếu không đổi)", type="password", key="profile_new_pass")
    c1, c2 = st.columns(2)
    with c1:
        in_fullname = st.text_input("Họ và tên đầy đủ", value=curr_fullname, key="profile_fullname")
        in_dob = st.text_input("Ngày sinh (VD: 15/08/1990)", value=curr_dob, key="profile_dob")
        in_phone = st.text_input("Số điện thoại", value=curr_phone, key="profile_phone")
        in_email = st.text_input("Email", value=curr_email, key="profile_email")
    with c2:
        st.markdown("**📍 Địa chỉ**")
        in_address = vietnam_address_inputs("profile_address", curr_address)
        in_bank_account = st.text_input("Số tài khoản ngân hàng", value=curr_bank_account, key="profile_bank_account")
        in_bank_name = bank_selectbox("Tên ngân hàng", key="profile_bank_name", current_value=curr_bank_name)
    if st.button("💾 Lưu thay đổi", use_container_width=True, key="profile_save_button"):
        db_old_pass = str(cred_row.iloc[0]['Mật khẩu']) if not cred_row.empty else "123456"
        if not password_matches(old_pass, db_old_pass):
            st.error("❌ Mật khẩu hiện tại không chính xác!")
        elif new_pass and len(str(new_pass)) < 4:
            st.error("❌ Mật khẩu mới quá ngắn.")
        else:
            ok, msg = update_user_profile(
                st.session_state.current_user, new_pass, in_fullname.strip(), in_dob.strip(),
                in_phone.strip(), in_email.strip(), in_address.strip(),
                in_bank_account.strip(), in_bank_name.strip()
            )
            (st.success if ok else st.error)(msg)
            if ok: st.rerun()

# ==========================================
# CÁC TRANG CHỨC NĂNG ĐỘC LẬP
# ==========================================
if selected_page == "👤 Hồ sơ cá nhân":
    pass  # Nội dung hồ sơ đã hiển thị ở phía trên.
elif selected_page == "⏰ Thiết lập ca làm việc" and is_admin_letan:
    st.subheader("⏰ Thiết lập ca làm việc")
    st.info("Chỉnh sửa trực tiếp trên bảng dưới đây để phân ca đồng loạt cho toàn bộ nhân viên.")

    df_shifts = df_credentials[['Tên nhân viên', 'Ca làm việc', 'Ngày bắt đầu ca', 'Chu kỳ']].copy()
    calc_height = (len(df_shifts) * 36) + 42 

    edited_df = st.data_editor(
        df_shifts,
        height=calc_height,
        column_config={
            "Tên nhân viên": st.column_config.TextColumn("Tên nhân viên", disabled=True),
            "Ca làm việc": st.column_config.SelectboxColumn(
                "Ca làm việc",
                options=["Ca 1 (10:00 - 23:00)", "Ca 2 (13:00 - 00:00)", "Cố định Ca 1 (Không đổi)", "Cố định Ca 2 (Không đổi)"],
                width="large"
            ),
            "Ngày bắt đầu ca": st.column_config.TextColumn("Ngày bắt đầu (DD/MM/YYYY)"),
            "Chu kỳ": st.column_config.SelectboxColumn(
                "Chu kỳ luân phiên",
                options=["Luân phiên (14 ngày)", "Theo chu kỳ Tháng", "Cố định (Không đổi)"],
                width="medium"
            )
        },
        hide_index=True,
        use_container_width=True
    )

    st.write("")
    if st.button("💾 Lưu Toàn Bộ Cấu Hình Ca", use_container_width=True):
        with st.spinner("Đang lưu đồng loạt vào hệ thống..."):
            res, msg = batch_update_shift_schedule(edited_df)
            if res: st.success(msg)
            else: st.error(msg)

elif selected_page == "👥 Danh sách nhân sự" and is_admin_letan:
    st.subheader("👥 Danh sách nhân sự")
    staff_source_df = df_credentials.copy()
    employment_map = load_employment_status_map()
    staff_source_df['Trạng thái làm việc'] = staff_source_df.apply(
        lambda r: employment_map.get(normalize_login_name(r.get('Tên nhân viên', '')), EMPLOYMENT_STATUS_ACTIVE)
        if str(r.get('Phân quyền', '')).strip().lower() == 'nhanvien' else '', axis=1
    )
    cols_staff = ['Tên nhân viên', 'Họ và tên đầy đủ', 'Phân quyền', 'Trạng thái làm việc', 'Điện thoại', 'Email', 'Địa chỉ', 'Số tài khoản ngân hàng', 'Tên ngân hàng', 'Khóa đăng nhập']
    cols_staff = [c for c in cols_staff if c in staff_source_df.columns]
    staff_df, staff_widths = apply_table_layout_df(staff_source_df[cols_staff], "staff_list")
    st.dataframe(
        apply_table_visual_styler(staff_df, "staff_list", list(staff_df.columns)),
        width='stretch', height='content', hide_index=True,
        row_height=layout_row_height("staff_list"),
        column_config=table_layout_column_config("staff_list", list(staff_df.columns))
    )

elif selected_page == "⚙️ Cấu hình cột" and st.session_state.current_role == "admin":
    st.subheader("⚙️ Cấu hình hiển thị cột toàn hệ thống")
    st.info(
        "Admin có thể tùy chỉnh thứ tự, độ rộng cột, độ cao dòng, font, cỡ chữ, kiểu chữ, "
        "căn lề và Wrap Text. Sau khi lưu, cấu hình được dùng chung cho toàn bộ tài khoản."
    )

    table_key = st.selectbox(
        "Chọn bảng cần tùy chỉnh",
        options=list(TABLE_LAYOUT_LABELS.keys()),
        format_func=lambda x: TABLE_LAYOUT_LABELS.get(x, x),
        key="ui_layout_table_selector"
    )
    available_cols = get_table_columns_for_settings(table_key)
    if not available_cols:
        st.warning("Chưa xác định được danh sách cột của bảng này.")
    else:
        rows_state_key = f"layout_rows_state_{table_key}"
        version_state_key = f"layout_editor_version_{table_key}"
        row_height_state_key = f"layout_row_height_state_{table_key}"
        init_flag_key = f"layout_init_signature_{table_key}"
        signature = "|".join(map(str, available_cols))

        # Chỉ khởi tạo từ dữ liệu đã lưu khi mới mở bảng hoặc sau khi Save/Reset.
        if st.session_state.get(init_flag_key) != signature or rows_state_key not in st.session_state:
            st.session_state[rows_state_key] = _layout_editor_rows_from_saved(table_key, available_cols)
            saved_row_height, _ = get_table_visual_settings(table_key, available_cols)
            st.session_state[row_height_state_key] = int(saved_row_height)
            st.session_state[version_state_key] = int(st.session_state.get(version_state_key, 0) or 0) + 1
            st.session_state[init_flag_key] = signature

        st.markdown("#### 🧱 Kích thước dòng")
        st.number_input(
            "Độ cao dòng (px)", min_value=24, max_value=120, step=2,
            key=row_height_state_key,
            help="Áp dụng cho các dòng dữ liệu của bảng. Giá trị gợi ý: 32–48 px."
        )

        st.markdown("#### 🎨 Thiết lập từng cột")
        st.caption(
            "Cột **Vị trí** dùng cơ chế chèn tự động. Ví dụ: đổi một cột từ vị trí 6 → 3 thì "
            "các cột đang ở 3, 4, 5 sẽ tự chuyển thành 4, 5, 6."
        )

        editor_version = int(st.session_state.get(version_state_key, 0) or 0)
        editor_key = f"layout_editor_{table_key}_{editor_version}"
        cfg_df = pd.DataFrame(st.session_state.get(rows_state_key, []))

        st.data_editor(
            cfg_df,
            key=editor_key,
            width="stretch", height="content", hide_index=True, num_rows="fixed",
            disabled=["Tên cột"],
            row_height=42,
            on_change=_layout_editor_on_change,
            args=(table_key, editor_key, rows_state_key, version_state_key),
            column_config={
                "Tên cột": st.column_config.TextColumn("Tên cột", disabled=True, width=210),
                "Vị trí": st.column_config.NumberColumn(
                    "Vị trí", min_value=1, max_value=max(1, len(cfg_df)), step=1, format="%d", width=75
                ),
                "Độ rộng (px)": st.column_config.NumberColumn(
                    "Độ rộng (px)", min_value=50, max_value=800, step=10, format="%d", width=110
                ),
                "Font chữ": st.column_config.SelectboxColumn(
                    "Font chữ", options=TABLE_LAYOUT_FONT_OPTIONS, width=135
                ),
                "Cỡ chữ": st.column_config.NumberColumn(
                    "Cỡ chữ", min_value=8, max_value=30, step=1, format="%d", width=85
                ),
                "Kiểu chữ": st.column_config.SelectboxColumn(
                    "Kiểu chữ", options=TABLE_LAYOUT_FONT_STYLE_OPTIONS, width=135
                ),
                "Căn lề": st.column_config.SelectboxColumn(
                    "Căn lề", options=TABLE_LAYOUT_ALIGN_OPTIONS, width=95,
                    help="left = trái, center = giữa, right = phải"
                ),
                "Wrap text": st.column_config.CheckboxColumn(
                    "Wrap text", width=95,
                    help="Bật để nội dung được phép xuống dòng khi chiều rộng cột nhỏ."
                ),
            }
        )

        st.caption(
            "Các tiêu đề cột vẫn luôn được phép xuống dòng để tránh mất chữ. Font/căn lề/Wrap Text "
            "được áp dụng cho bảng hiển thị; độ rộng và độ cao dòng áp dụng cả bảng hiển thị và bảng chỉnh sửa."
        )

        c_save_layout, c_reset_layout = st.columns(2)
        with c_save_layout:
            if st.button("💾 Lưu & áp dụng toàn hệ thống", use_container_width=True, key=f"save_layout_{table_key}"):
                rows = [dict(r) for r in st.session_state.get(rows_state_key, [])]
                rows = sorted(rows, key=lambda r: int(float(r.get("Vị trí", 9999))))
                for pos, r in enumerate(rows, start=1):
                    r["Vị trí"] = pos
                new_order = [str(r.get("Tên cột", "")) for r in rows if str(r.get("Tên cột", ""))]
                new_widths = {
                    str(r["Tên cột"]): max(50, min(800, int(float(r.get("Độ rộng (px)", 140)))))
                    for r in rows if str(r.get("Tên cột", ""))
                }
                visual_cols = {}
                for r in rows:
                    col = str(r.get("Tên cột", ""))
                    if not col:
                        continue
                    visual_cols[col] = {
                        "font_family": str(r.get("Font chữ", "Roboto")),
                        "font_size": max(8, min(30, int(float(r.get("Cỡ chữ", TABLE_LAYOUT_DEFAULT_FONT_SIZE))))),
                        "font_style": str(r.get("Kiểu chữ", "Thường")),
                        "align": str(r.get("Căn lề", _default_column_alignment(col))).lower(),
                        "wrap": bool(r.get("Wrap text", True)),
                    }
                visual = {
                    "row_height": max(24, min(120, int(st.session_state.get(row_height_state_key, TABLE_LAYOUT_DEFAULT_ROW_HEIGHT)))),
                    "columns": visual_cols,
                }
                ok, msg = save_table_layout_config(
                    table_key, new_order, new_widths, st.session_state.current_user, visual=visual
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    # Lần render sau đọc lại đúng dữ liệu đã lưu.
                    st.session_state.pop(rows_state_key, None)
                    st.session_state.pop(init_flag_key, None)
                    st.rerun()

        with c_reset_layout:
            if st.button("♻️ Khôi phục mặc định", use_container_width=True, key=f"reset_layout_{table_key}"):
                default_order = list(available_cols)
                default_widths = {c: _default_column_width(c) for c in default_order}
                default_visual = {
                    "row_height": TABLE_LAYOUT_DEFAULT_ROW_HEIGHT,
                    "columns": {c: _default_column_visual_style(c) for c in default_order},
                }
                ok, msg = save_table_layout_config(
                    table_key, default_order, default_widths, st.session_state.current_user, visual=default_visual
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    st.session_state.pop(rows_state_key, None)
                    st.session_state.pop(init_flag_key, None)
                    st.session_state.pop(row_height_state_key, None)
                    st.rerun()

elif selected_page == "➕ Thêm nhân viên" and is_admin_letan:
    st.subheader("➕ Thêm nhân viên")
    st.write("Nhập thông tin nhân viên mới:")
    st.caption("📍 Địa chỉ dùng danh mục hành chính Việt Nam sau 01/07/2025; khi lưu sẽ tự ghép vào duy nhất cột Địa chỉ.")
    col1, col2 = st.columns(2)
    with col1:
        new_usr = st.text_input("Tên đăng nhập (Bắt buộc)", key="new_emp_username")
        new_pwd = st.text_input("Mật khẩu", value="123456", key="new_emp_password")
        new_role = st.selectbox("Phân quyền", ["nhanvien", "quanly", "locker", "tapvu", "letan", "admin"], filter_mode="contains", key="new_emp_role")
        new_fn = st.text_input("Họ và tên đầy đủ", key="new_emp_fullname")
        new_phone = st.text_input("Số điện thoại", key="new_emp_phone")
        new_email = st.text_input("Email", key="new_emp_email")
    with col2:
        st.markdown("**📍 Địa chỉ**")
        new_address = vietnam_address_inputs("new_emp_address", "")
        new_bank_account = st.text_input("Số tài khoản ngân hàng", key="new_emp_bank_account")
        new_bank_name = bank_selectbox("Tên ngân hàng", key="new_employee_bank_name", current_value="")

    if st.button("💾 Lưu Nhân Viên Mới", use_container_width=True, key="save_new_employee"):
        if new_usr:
            try:
                client = get_gspread_client()
                sheet_mk = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                all_emps = _gs_call_with_backoff(sheet_mk.col_values, 2)

                if normalize_login_name(new_usr) in {normalize_login_name(x) for x in all_emps}:
                    st.error("Tên đăng nhập đã tồn tại (hệ thống không phân biệt dấu và HOA/thường)!")
                else:
                    stt_new = len(all_emps)
                    row_data = [
                        stt_new, new_usr, str(new_pwd), new_role, new_fn, "", new_phone, new_email, new_address,
                        new_bank_account, new_bank_name, "0", "0", "0", "", "", "", "", "", ""
                    ]
                    _gs_call_with_backoff(sheet_mk.append_row, row_data, value_input_option='USER_ENTERED')
                    start_work_date = get_vn_today()
                    role_new = str(new_role).strip().lower()
                    # Chỉ vai trò nhanvien tham gia TichLuy. Quanly/letan/locker/tapvu/admin không xuất hiện ở TichLuy.
                    if role_new not in TICHLUY_EXCLUDED_ROLES:
                        tl_ok, tl_msg = ensure_employee_in_tichluy(new_usr, start_work_date)
                    else:
                        tl_ok, tl_msg = True, 'Vai trò này không tham gia TichLuy.'

                    # KHÔNG đồng bộ nhân viên mới sang file 1Kz0... theo yêu cầu mới.
                    # Sau khi thêm phải đánh lại STT cột A của Sheet1 và TichLuy.
                    stt_ok, stt_msg = renumber_credential_sheet_stt(sheet_mk)
                    try:
                        load_credentials.clear()
                    except Exception:
                        pass
                    tl_sync_ok, tl_sync_msg = sync_tichluy_roles_and_stt()
                    _clear_dynamic_data_caches()

                    if tl_ok and stt_ok and tl_sync_ok:
                        extra = f" · Ngày bắt đầu làm {start_work_date.strftime('%d/%m/%Y')}" if role_new not in TICHLUY_EXCLUDED_ROLES else ""
                        st.success(f"Đã thêm thành công: {new_usr}{extra} · đã sắp xếp lại STT Sheet1/TichLuy.")
                    else:
                        st.warning(
                            f"Đã tạo tài khoản {new_usr}, nhưng có bước phụ chưa hoàn tất: "
                            f"{tl_msg} | {stt_msg} | {tl_sync_msg}"
                        )
            except Exception as e:
                st.error(f"Lỗi: {e}")
        else:
            st.error("Vui lòng nhập Tên đăng nhập.")


elif selected_page == "✏️ Sửa / Xóa nhân viên" and is_admin_letan:
    st.subheader("✏️ Sửa / Xóa nhân viên")
    st.write("Chọn nhân viên cần thao tác.")

    st.markdown("#### 🏷️ Trạng thái làm việc của nhân viên")
    if 'Phân quyền' in df_credentials.columns:
        nhanvien_df_status = df_credentials[
            df_credentials['Phân quyền'].astype(str).str.strip().str.lower().eq('nhanvien')
        ].copy()
    else:
        nhanvien_df_status = pd.DataFrame(columns=df_credentials.columns)
    status_emp_options = nhanvien_df_status['Tên nhân viên'].dropna().astype(str).tolist() if not nhanvien_df_status.empty else []
    if not status_emp_options:
        st.info("Hiện không có tài khoản vai trò nhanvien để cập nhật trạng thái.")
    else:
        c_status_emp, c_status_value, c_status_save = st.columns([2.2, 1.6, 1.2])
        with c_status_emp:
            status_emp = st.selectbox(
                "Chọn nhân viên", options=status_emp_options, filter_mode="contains",
                key="employment_status_employee"
            )
        current_status_map = load_employment_status_map()
        current_status = current_status_map.get(normalize_login_name(status_emp), EMPLOYMENT_STATUS_ACTIVE)
        with c_status_value:
            status_value = st.selectbox(
                "Trạng thái", options=EMPLOYMENT_STATUS_OPTIONS,
                index=EMPLOYMENT_STATUS_OPTIONS.index(current_status) if current_status in EMPLOYMENT_STATUS_OPTIONS else 0,
                key=f"employment_status_value_{normalize_login_name(status_emp)}"
            )
        with c_status_save:
            st.write("")
            st.write("")
            if st.button("💾 Lưu trạng thái", use_container_width=True, key="save_employment_status"):
                ok, msg = set_employee_employment_status(status_emp, status_value, st.session_state.current_user)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()
    st.caption("Trạng thái nghỉ việc không xóa tài khoản hoặc dữ liệu lịch sử; có thể đổi lại 'Đang làm việc' bất cứ lúc nào.")
    st.markdown("---")

    col_action1, col_action2 = st.columns(2)
    with col_action1:
        st.markdown("#### 🗑️ Xóa nhân viên")
        del_usr = st.selectbox("Chọn nhân viên cần xóa:", [""] + df_credentials['Tên nhân viên'].tolist(), filter_mode="contains", key="delete_employee_select")
        if st.button("Xác nhận xóa", use_container_width=True):
            if del_usr:
                try:
                    client = get_gspread_client()
                    sheet_mk = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                    cells = sheet_mk.findall(del_usr, in_column=2)
                    if cells:
                        sheet_mk.delete_rows(cells[0].row)
                        renumber_credential_sheet_stt(sheet_mk)
                        try:
                            load_credentials.clear()
                        except Exception:
                            pass
                        sync_tichluy_roles_and_stt()
                        _clear_dynamic_data_caches()
                        st.success(f"Đã xóa nhân viên: {del_usr} · đã sắp xếp lại STT Sheet1/TichLuy.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Lỗi xóa: {e}")
    with col_action2:
        st.markdown("#### ✏️ Chỉnh sửa hồ sơ")
        if st.session_state.current_role == "admin":
            edit_usr = st.selectbox("Chọn nhân viên cần sửa:", [""] + df_credentials['Tên nhân viên'].tolist(), key='sb_edit_employee', filter_mode="contains")
            if edit_usr:
                usr_data = df_credentials[df_credentials['Tên nhân viên'] == edit_usr].iloc[0]
                edit_key = re.sub(r"[^a-zA-Z0-9_]+", "_", normalize_login_name(edit_usr)) or "employee"
                e_pass = st.text_input("Mật khẩu", value=str(usr_data.get('Mật khẩu', '')), key=f"edit_password_{edit_key}")
                e_fn = st.text_input("Họ tên", value=str(usr_data.get('Họ và tên đầy đủ', '')), key=f"edit_fullname_{edit_key}")
                e_dob = st.text_input("Ngày sinh", value=str(usr_data.get('Ngày sinh', '')), key=f"edit_dob_{edit_key}")
                e_phone = st.text_input("SĐT", value=str(usr_data.get('Điện thoại', '')).replace("'", ""), key=f"edit_phone_{edit_key}")
                e_email = st.text_input("Email", value=str(usr_data.get('Email', '')), key=f"edit_email_{edit_key}")
                st.markdown("**📍 Địa chỉ**")
                e_address = vietnam_address_inputs(f"edit_address_{edit_key}", str(usr_data.get('Địa chỉ', '')))
                e_bank_account = st.text_input("Số tài khoản ngân hàng", value=str(usr_data.get('Số tài khoản ngân hàng', '')).replace("'", ""), key=f"edit_bank_account_{edit_key}")
                e_bank_name = bank_selectbox("Tên ngân hàng", key=f"edit_bank_name_{edit_key}", current_value=str(usr_data.get('Tên ngân hàng', '')))
                if st.button("💾 Cập nhật dữ liệu", use_container_width=True, key=f"edit_save_{edit_key}"):
                    ok, msg = update_user_profile(edit_usr, e_pass, e_fn, e_dob, e_phone, e_email, e_address, e_bank_account, e_bank_name)
                    (st.success if ok else st.error)(msg)
                    if ok: st.rerun()
        else:
            st.info("Lễ tân/Quản lý được phép xóa theo quyền hiện tại; chỉnh sửa chi tiết hồ sơ chỉ dành cho Admin.")

elif selected_page == "🔒 Khóa đăng nhập" and st.session_state.current_role == "admin":
    st.markdown("### 🔒 Khóa / mở khóa đăng nhập")
    if st.session_state.current_role != "admin":
        st.info("Chỉ tài khoản Admin được phép khóa hoặc mở khóa đăng nhập.")
    else:
        lockable_df = df_credentials[df_credentials['Tên nhân viên'].apply(normalize_login_name) != normalize_login_name(st.session_state.current_user)].copy()
        lockable_users = lockable_df['Tên nhân viên'].dropna().astype(str).tolist()
        selected_lock_users = st.multiselect(
            "Chọn một hoặc nhiều tài khoản:",
            options=lockable_users,
            default=[],
            filter_mode="contains",
            placeholder="Gõ để tìm tài khoản..."
        )
        c_lock1, c_lock2, c_lock3, c_lock4 = st.columns(4)
        with c_lock1:
            if st.button("🔒 Khóa tài khoản đã chọn", use_container_width=True):
                if not selected_lock_users:
                    st.warning("Vui lòng chọn ít nhất 1 tài khoản.")
                else:
                    ok, msg = set_accounts_login_lock(selected_lock_users, True)
                    (st.success if ok else st.error)(msg)
                    if ok: st.rerun()
        with c_lock2:
            if st.button("🔓 Mở khóa tài khoản đã chọn", use_container_width=True):
                if not selected_lock_users:
                    st.warning("Vui lòng chọn ít nhất 1 tài khoản.")
                else:
                    ok, msg = set_accounts_login_lock(selected_lock_users, False)
                    (st.success if ok else st.error)(msg)
                    if ok: st.rerun()
        with c_lock3:
            if st.button("⛔ Khóa TOÀN BỘ", use_container_width=True):
                ok, msg = set_accounts_login_lock(lockable_users, True)
                (st.success if ok else st.error)(msg)
                if ok: st.rerun()
        with c_lock4:
            if st.button("✅ Mở TOÀN BỘ", use_container_width=True):
                ok, msg = set_accounts_login_lock(lockable_users, False)
                (st.success if ok else st.error)(msg)
                if ok: st.rerun()

        locked_now = lockable_df[lockable_df['Khóa đăng nhập'].apply(is_locked_value)]
        st.caption(f"Đang khóa: {len(locked_now)} / {len(lockable_df)} tài khoản. Tài khoản Admin đang sử dụng được loại khỏi danh sách để tránh tự khóa chính mình.")
        if not locked_now.empty:
            st.dataframe(locked_now[['Tên nhân viên', 'Phân quyền', 'Khóa đăng nhập']], width='stretch', height='content', hide_index=True)

elif selected_page == "🔐 Khóa quyền đăng ký" and st.session_state.current_role == "admin":
    st.subheader("🔐 Khóa quyền đăng ký lịch của nhân viên")
    if system_status["lock_nv"]:
        st.warning("🔴 Quyền đăng ký/xóa lịch của tài khoản Nhân viên đang bị KHÓA tạm thời.")
        if st.button("🔓 Mở lại quyền nhân viên", use_container_width=True):
            system_status["lock_nv"] = False
            st.rerun()
    else:
        st.success("🟢 Quyền đăng ký/xóa lịch của tài khoản Nhân viên đang MỞ.")
        if st.button("🔒 Khóa quyền nhân viên tạm thời", use_container_width=True):
            system_status["lock_nv"] = True
            st.rerun()

elif selected_page == "🔄 Đồng bộ dữ liệu" and st.session_state.current_role == "admin":
    st.subheader("🔄 Đồng bộ dữ liệu")
    st.info("Các công cụ đồng bộ chỉ dành cho tài khoản Admin.")
    if st.button("🔄 Đồng bộ Excel ➡️ Google Sheets", help="Chỉ thêm những dòng mới từ Excel vào Sheet", use_container_width=True):
        with st.spinner("Đang kiểm tra và đồng bộ..."):
            res, msg = admin_sync_excel_to_gsheet()
            (st.success if res else st.error)(msg)
    if st.button("⬇️ Tạo Excel mới từ Google Sheets", help="Gộp dữ liệu mới từ Sheet vào file Excel gốc", use_container_width=True):
        with st.spinner("Đang tạo file..."):
            df_merged, has_new = admin_sync_gsheet_to_excel(df_backup, df_lich)
            if has_new:
                st.download_button("📥 Tải file Excel cập nhật", data=to_excel(df_merged), file_name="LichNghi_CapNhat.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
            else:
                st.info("Excel gốc đã có đủ dữ liệu, không có dòng mới.")

elif selected_page == "💰 Thống kê lương" and (st.session_state.current_role == "admin" or (st.session_state.current_role in ["letan", "quanly"] and payroll_letan_enabled)):
    st.subheader("💰 Thống kê lương nhân viên")
    st.caption("Tiền Lương được tính theo đúng quy tắc: cột F bắt đầu bằng 'Tip' → cộng cột G theo tên nhân viên ở cột I.")

    tab_calc, tab_history = st.tabs(["🧮 Tính lương nhân viên", "🗂 Lịch sử bảng lương đã lưu"])
    with tab_calc:
        # V52: Admin chủ động kiểm tra các khoản nợ Thực nhận âm còn mở của các kỳ trước.
        if st.session_state.current_role == "admin":
            _debt_btn_col, _debt_hide_col = st.columns([3, 2])
            with _debt_btn_col:
                if st.button(
                    "💳 Kiểm tra nhân viên còn nợ Thực nhận âm kỳ trước",
                    use_container_width=True,
                    key="admin_check_open_negative_payroll_debts"
                ):
                    _clear_violation_debt_cache()
                    st.session_state.show_open_negative_payroll_debts = True
            with _debt_hide_col:
                if st.button(
                    "✖️ Ẩn danh sách nợ",
                    use_container_width=True,
                    key="admin_hide_open_negative_payroll_debts",
                    disabled=not bool(st.session_state.get('show_open_negative_payroll_debts', False))
                ):
                    st.session_state.show_open_negative_payroll_debts = False
                    st.rerun()

            if st.session_state.get('show_open_negative_payroll_debts', False):
                _negative_debt_summary, _negative_debt_detail = get_open_negative_payroll_debts()
                if _negative_debt_summary.empty:
                    st.success("✅ Hiện không có nhân viên nào còn nợ do Thực nhận âm của các kỳ trước.")
                else:
                    _negative_debt_total = int(round(_negative_debt_summary['Tổng còn nợ'].apply(_money_to_float).sum()))
                    _d1, _d2 = st.columns(2)
                    _d1.metric("Nhân viên còn nợ", len(_negative_debt_summary))
                    _d2.metric("Tổng nợ Thực nhận âm", f"{_negative_debt_total:,.0f} đ".replace(',', '.'))
                    st.caption("Chỉ hiển thị các khoản Loại = Âm thực nhận và Trạng thái = Chưa hoàn thành. Khoản Admin chủ động tạm hoãn Vi phạm không nằm trong danh sách này.")

                    _summary_show = _negative_debt_summary.copy()
                    _summary_show['Tổng còn nợ'] = _summary_show['Tổng còn nợ'].apply(lambda x: f"{_money_to_float(x):,.0f}".replace(',', '.'))
                    st.dataframe(
                        _summary_show,
                        hide_index=True,
                        width="stretch",
                        height="content"
                    )
                    with st.expander("🔎 Xem chi tiết từng kỳ còn nợ", expanded=False):
                        _detail_show = _negative_debt_detail.copy()
                        if 'Số tiền' in _detail_show.columns:
                            _detail_show['Số tiền'] = _detail_show['Số tiền'].apply(lambda x: f"{_money_to_float(x):,.0f}".replace(',', '.'))
                        st.dataframe(
                            _detail_show,
                            hide_index=True,
                            width="stretch",
                            height="content"
                        )

        default_living_db, default_locker_db = get_payroll_default_amounts()
        with st.expander("⚙️ Mức khấu trừ mặc định", expanded=False):
            cfg1, cfg2, cfg3 = st.columns([3, 3, 2])
            with cfg1:
                payroll_default_living = st.number_input(
                    "Chi phí sinh hoạt / nhân viên", min_value=0.0, step=10000.0, format="%.0f",
                    value=float(default_living_db), key="payroll_default_living"
                )
            with cfg2:
                payroll_default_locker = st.number_input(
                    "Hỗ trợ Locker / nhân viên", min_value=0.0, step=10000.0, format="%.0f",
                    value=float(default_locker_db), key="payroll_default_locker"
                )
            with cfg3:
                st.write("")
                if st.button("💾 Lưu mức mặc định", use_container_width=True, key="save_payroll_defaults"):
                    ok, msg = set_payroll_default_amounts(payroll_default_living, payroll_default_locker)
                    (st.success if ok else st.error)(msg)
            st.caption("Hai mức này được áp dụng cho toàn bộ nhân viên khi tạo bảng lương mới. Mức riêng theo nhân viên bên dưới sẽ được ưu tiên hơn mức mặc định chung.")

            # --- MỨC RIÊNG CHO 1 / NHIỀU NHÂN VIÊN ---
            st.markdown("#### 👥 Mức riêng theo nhân viên")
            payroll_emp_choices_df = df_credentials.copy() if isinstance(df_credentials, pd.DataFrame) else pd.DataFrame()
            if not payroll_emp_choices_df.empty and 'Tên nhân viên' in payroll_emp_choices_df.columns:
                payroll_emp_choices_df = payroll_emp_choices_df[payroll_emp_choices_df['Tên nhân viên'].astype(str).str.strip() != ''].copy()
                payroll_emp_choices_df = payroll_emp_choices_df[~payroll_emp_choices_df['Tên nhân viên'].astype(str).apply(normalize_login_name).isin({
                    'ten nhan vien', 'ten he thong', 'username', 'user name'
                })]
                if 'Phân quyền' in payroll_emp_choices_df.columns:
                    _pay_roles = payroll_emp_choices_df['Phân quyền'].astype(str).str.strip().str.lower()
                    payroll_emp_choices_df = payroll_emp_choices_df[_pay_roles.eq('nhanvien')].copy()
                payroll_employee_options = payroll_emp_choices_df['Tên nhân viên'].astype(str).str.strip().drop_duplicates().tolist()
            else:
                payroll_employee_options = []

            selected_payroll_override_emps = st.multiselect(
                "Chọn 1 hoặc nhiều nhân viên cần đặt mức riêng:",
                options=payroll_employee_options,
                key="payroll_override_employees",
                filter_mode="contains",
                help="Các nhân viên được chọn sẽ dùng mức riêng thay cho mức mặc định chung khi tạo bảng lương mới."
            )

            existing_payroll_overrides = get_payroll_employee_overrides()
            _selected_keys = [normalize_login_name(x) for x in selected_payroll_override_emps]
            _living_values = [existing_payroll_overrides[k]['living'] for k in _selected_keys if k in existing_payroll_overrides]
            _locker_values = [existing_payroll_overrides[k]['locker'] for k in _selected_keys if k in existing_payroll_overrides]
            _living_initial = _living_values[0] if _living_values and len(set(_living_values)) == 1 else float(payroll_default_living)
            _locker_initial = _locker_values[0] if _locker_values and len(set(_locker_values)) == 1 else float(payroll_default_locker)
            _override_sig = hashlib.md5("|".join(sorted(_selected_keys)).encode('utf-8')).hexdigest()[:10] if _selected_keys else "none"

            ov1, ov2, ov3, ov4 = st.columns([3, 3, 2, 2])
            with ov1:
                payroll_override_living = st.number_input(
                    "Chi phí sinh hoạt riêng / nhân viên", min_value=0.0, step=10000.0, format="%.0f",
                    value=float(_living_initial), key=f"payroll_override_living_{_override_sig}",
                    disabled=not bool(selected_payroll_override_emps)
                )
            with ov2:
                payroll_override_locker = st.number_input(
                    "Hỗ trợ Locker riêng / nhân viên", min_value=0.0, step=10000.0, format="%.0f",
                    value=float(_locker_initial), key=f"payroll_override_locker_{_override_sig}",
                    disabled=not bool(selected_payroll_override_emps)
                )
            with ov3:
                st.write("")
                if st.button(
                    "💾 Áp dụng mức riêng", use_container_width=True, key="save_payroll_employee_overrides",
                    disabled=not bool(selected_payroll_override_emps)
                ):
                    ok, msg = set_payroll_employee_overrides(
                        selected_payroll_override_emps, payroll_override_living, payroll_override_locker
                    )
                    if ok:
                        _apply_payroll_override_to_current_session(
                            selected_payroll_override_emps, payroll_override_living, payroll_override_locker
                        )
                        st.success(msg)
                    else:
                        st.error(msg)
            with ov4:
                st.write("")
                if st.button(
                    "♻️ Dùng lại mặc định", use_container_width=True, key="clear_payroll_employee_overrides",
                    disabled=not bool(selected_payroll_override_emps)
                ):
                    ok, msg = clear_payroll_employee_overrides(selected_payroll_override_emps)
                    if ok:
                        _apply_payroll_override_to_current_session(
                            selected_payroll_override_emps, payroll_default_living, payroll_default_locker
                        )
                        st.success(msg)
                    else:
                        st.error(msg)

            # Hiển thị danh sách nhân viên đang có mức riêng để Admin dễ kiểm tra.
            if existing_payroll_overrides:
                _override_rows = []
                _display_order = {normalize_login_name(n): i for i, n in enumerate(payroll_employee_options)}
                for _k, _v in existing_payroll_overrides.items():
                    _override_rows.append({
                        "Tên Hệ thống": _v.get("name", _k),
                        "Phí Sinh Hoạt riêng": int(round(_money_to_float(_v.get("living", 0)))),
                        "Hỗ trợ Locker riêng": int(round(_money_to_float(_v.get("locker", 0)))),
                        "__order": _display_order.get(_k, 9999),
                    })
                _override_df = pd.DataFrame(_override_rows).sort_values(["__order", "Tên Hệ thống"]).drop(columns=["__order"])
                with st.expander(f"📋 Danh sách mức riêng đang lưu ({len(_override_df)} nhân viên)", expanded=False):
                    st.dataframe(
                        _override_df, width="stretch", height="content", hide_index=True,
                        column_config={
                            "Tên Hệ thống": st.column_config.TextColumn("Tên Hệ thống"),
                            "Phí Sinh Hoạt riêng": st.column_config.NumberColumn("Phí Sinh Hoạt riêng", format="%,d"),
                            "Hỗ trợ Locker riêng": st.column_config.NumberColumn("Hỗ trợ Locker riêng", format="%,d"),
                        }
                    )

        c_period, c_source = st.columns(2)
        with c_period:
            preset = st.selectbox(
                "Chọn kỳ tính lương:",
                ["Kỳ 1 - Tháng này", "Kỳ 2 - Tháng này", "Kỳ 1 - Tháng trước", "Kỳ 2 - Tháng trước"],
                key="payroll_period_preset", filter_mode="contains"
            )
            p_start, p_end, period_err = resolve_payroll_period(preset, get_vn_today())
            if period_err:
                st.error(period_err)
            elif p_start and p_end:
                st.info(f"Kỳ đang chọn: **{p_start.strftime('%d/%m/%Y')} → {p_end.strftime('%d/%m/%Y')}**")
        with c_source:
            source_mode = st.selectbox(
                "Nguồn dữ liệu lương:",
                ["Upload file Excel", "Google Sheet mặc định"],
                index=0,
                key="payroll_source_mode", filter_mode="contains"
            )
            payroll_upload = None
            if source_mode == "Upload file Excel":
                payroll_upload = st.file_uploader(
                    "Upload file dulieuluong (.xlsx/.xlsm)", type=["xlsx", "xlsm"], key="payroll_upload_file",
                    help=f"File phải có sheet '{PAYROLL_SOURCE_WORKSHEET}'."
                )
            else:
                st.caption("Nguồn mặc định: Google Sheet 1WtYsbEAlifL1PZ-nSGBojgL4Bnur-1vF")

        # Trạng thái trực quan cho quy trình tải dữ liệu & tính lương.
        if "payroll_process_message" not in st.session_state:
            st.session_state.payroll_process_message = "⏸️ Sẵn sàng tính lương nhân viên."
        if "payroll_process_state" not in st.session_state:
            st.session_state.payroll_process_state = "idle"

        state_icon = {"idle": "⚪", "running": "🔵", "complete": "🟢", "error": "🔴"}.get(
            st.session_state.payroll_process_state, "⚪"
        )
        st.markdown(
            f"<div style='padding:8px 12px;border:1px solid #d9d9d9;border-radius:8px;"
            f"background:#fafafa;margin:4px 0 8px 0;font-weight:600;'>"
            f"{state_icon} {st.session_state.payroll_process_message}</div>",
            unsafe_allow_html=True
        )

        calc_col, recalc_col = st.columns(2)
        with calc_col:
            payroll_calc_clicked = st.button(
                "🧮 Tính lương nhân viên", use_container_width=True, disabled=bool(period_err),
                key="payroll_calc_button"
            )
        with recalc_col:
            payroll_clear_recalc_clicked = st.button(
                "🧹 Xóa dữ liệu bảng lương & tính lại", use_container_width=True, disabled=bool(period_err),
                key="payroll_clear_recalc_button",
                help="Xóa bảng lương đang nằm trong phiên làm việc, tải lại hồ sơ/role mới nhất rồi tính lại từ đầu."
            )

        if payroll_clear_recalc_clicked:
            # Chỉ xóa dữ liệu bảng lương đang tính trong phiên, KHÔNG xóa lịch sử đã lưu.
            for _k in [
                "payroll_current_df", "payroll_current_start", "payroll_current_end",
                "payroll_current_source", "payroll_unmatched", "payroll_adjustment_editor"
            ]:
                st.session_state.pop(_k, None)
            _clear_violation_debt_cache()
            st.session_state.payroll_process_state = "idle"
            st.session_state.payroll_process_message = "Đã xóa dữ liệu cũ · Đang tính lại từ đầu..."

        if payroll_calc_clicked or payroll_clear_recalc_clicked:
            progress = st.progress(0, text="0% - Bắt đầu xử lý...")
            status = st.status("🧮 Đang tính lương nhân viên...", expanded=True, state="running")
            try:
                st.session_state.payroll_process_state = "running"
                st.session_state.payroll_process_message = "Đang kiểm tra nguồn dữ liệu..."
                status.write("1/5 · Kiểm tra nguồn dữ liệu và kỳ lương")
                progress.progress(10, text="10% - Kiểm tra nguồn dữ liệu")

                if source_mode == "Upload file Excel":
                    if payroll_upload is None:
                        raise ValueError("Vui lòng upload file Excel dữ liệu lương trước khi tính.")
                    status.write(f"2/5 · Đang đọc file: {getattr(payroll_upload, 'name', 'Upload Excel')}")
                    progress.progress(25, text="25% - Đang đọc file Excel")
                    src_df, src_err = load_payroll_source_from_uploaded_excel(payroll_upload)
                    src_label = getattr(payroll_upload, 'name', 'Upload Excel')
                else:
                    status.write("2/5 · Đang tải dữ liệu từ Google Sheet mặc định")
                    progress.progress(25, text="25% - Đang tải Google Sheet")
                    src_df, src_err = load_payroll_source_from_google_sheet()
                    src_label = f"Google Sheet {PAYROLL_SOURCE_SHEET_ID}"

                if src_err:
                    raise ValueError(src_err)

                row_count = len(src_df) if isinstance(src_df, pd.DataFrame) else 0
                status.write(f"✅ Đã đọc {row_count:,} dòng dữ liệu nguồn".replace(",", "."))
                progress.progress(45, text="45% - Đã đọc dữ liệu nguồn")

                status.write("3/5 · Đang tải dữ liệu tiền phạt từ hệ thống")
                st.session_state.payroll_process_message = "Đang tải dữ liệu tiền phạt..."
                progress.progress(60, text="60% - Đang tải tiền phạt")
                leave_primary = load_backup_sheet_data()
                penalty_rows = len(leave_primary) if isinstance(leave_primary, pd.DataFrame) else 0
                status.write(f"✅ Đã tải {penalty_rows:,} dòng lịch nghỉ/vi phạm".replace(",", "."))

                status.write("4/5 · Đang tải lại vai trò nhân viên, đồng bộ TichLuy và tính lương")
                st.session_state.payroll_process_message = "Đang tải lại vai trò nhân viên và tính lương..."
                progress.progress(75, text="75% - Đang tính lương")

                # V44: luôn lấy hồ sơ/Phân quyền MỚI NHẤT trước mỗi lần tính lương.
                # Điều này bảo đảm người vừa đổi từ nhanvien -> letan/quanly/locker/tapvu
                # bị loại ngay khỏi bảng lương, không chờ cache load_credentials hết hạn.
                try:
                    load_credentials.clear()
                except Exception:
                    pass
                credentials_live = load_credentials()
                df_credentials = credentials_live
                nhanvien_live_count = 0
                if isinstance(credentials_live, pd.DataFrame) and not credentials_live.empty and 'Phân quyền' in credentials_live.columns:
                    nhanvien_live_count = int(
                        credentials_live['Phân quyền'].astype(str).str.strip().str.lower().eq('nhanvien').sum()
                    )
                status.write(f"✅ Hồ sơ mới nhất: {nhanvien_live_count} tài khoản vai trò nhanvien")

                # Bảo đảm mọi nhân viên đủ điều kiện đều có một dòng trong TichLuy trước khi tính.
                # Đồng bộ chỉ thêm người thiếu + đánh STT; không sửa D/E/F của người đã có.
                tl_sync_ok, tl_sync_msg = sync_tichluy_roles_and_stt(credentials_live)
                if tl_sync_ok:
                    status.write(f"✅ {tl_sync_msg}")
                else:
                    status.write(f"⚠️ {tl_sync_msg}")
                # Tiền phạt chỉ dùng dữ liệu ở Google Sheet 1Kz0...; không lấy nguồn lịch nghỉ thứ hai.
                payroll_df, unmatched_names = build_payroll_table(
                    src_df, credentials_live, p_start, p_end,
                    leave_primary=leave_primary, leave_secondary=None,
                    default_living_expense=payroll_default_living,
                    default_locker_support=payroll_default_locker
                )

                status.write("5/5 · Đang hoàn tất và lưu kết quả vào phiên làm việc")
                st.session_state.payroll_process_message = "Đang hoàn tất bảng lương..."
                progress.progress(92, text="92% - Đang hoàn tất")
                st.session_state.payroll_current_df = payroll_df
                st.session_state.payroll_current_start = p_start.isoformat()
                st.session_state.payroll_current_end = p_end.isoformat()
                st.session_state.payroll_current_source = src_label
                st.session_state.payroll_unmatched = unmatched_names

                progress.progress(100, text="100% - Hoàn tất")
                st.session_state.payroll_process_state = "complete"
                st.session_state.payroll_process_message = f"Hoàn tất · Đã tính lương cho {len(payroll_df)} nhân viên."
                status.update(
                    label=f"✅ Hoàn tất - Đã tính lương cho {len(payroll_df)} nhân viên",
                    state="complete", expanded=False
                )
                st.success(f"✅ Đã tính lương cho {len(payroll_df)} tài khoản nhân viên.")
                if unmatched_names:
                    status.write(f"⚠️ Có {len(unmatched_names)} tên trong dữ liệu Tip chưa khớp tài khoản hệ thống.")
            except Exception as e:
                progress.empty()
                st.session_state.payroll_process_state = "error"
                st.session_state.payroll_process_message = f"Lỗi: {e}"
                status.update(label=f"❌ Không thể tính lương: {e}", state="error", expanded=True)
                status.write(f"❌ {e}")
                st.error(f"❌ {e}")

        current = st.session_state.get('payroll_current_df')
        if isinstance(current, pd.DataFrame) and not current.empty:
            # Dọn cả dữ liệu đang nằm trong session từ bản cũ để không còn dòng header giả.
            current = _filter_real_payroll_rows(current)
            st.session_state.payroll_current_df = current
            current_start = date.fromisoformat(st.session_state.get('payroll_current_start'))
            current_end = date.fromisoformat(st.session_state.get('payroll_current_end'))
            unmatched = st.session_state.get('payroll_unmatched', [])
            if unmatched:
                st.warning("Có tên ở dữ liệu Tip nhưng không khớp tài khoản hệ thống: " + ", ".join(map(str, unmatched)))

            # V49: bảng điều chỉnh được chuyển xuống CUỐI trang và luôn đóng mặc định.
            # Ở phần nội dung phía trên, dùng dữ liệu bảng lương hiện có trong session.
            # Khi Admin mở bảng điều chỉnh ở cuối trang và thay đổi số liệu, ứng dụng lưu lại
            # vào session rồi rerun để toàn bộ thống kê/export/email phía trên cập nhật đồng bộ.
            final_df = recalculate_payroll_net(current.copy())
            final_df = _filter_real_payroll_rows(final_df)
            st.session_state.payroll_current_df = final_df

            # V47: Admin có thể chủ động tạm hoãn tiền Vi phạm của kỳ hiện tại.
            # Khoản đã hoãn được lưu vào sheet NoViPham và chỉ bắt đầu trừ từ kỳ kế tiếp.
            if st.session_state.current_role == "admin":
                # V55: giữ section Tạm hoãn Vi phạm luôn mở trong lúc thao tác.
                # Selectbox có filter_mode="contains" sẽ rerun khi gõ/chọn; nếu expanded=False
                # thì expander tự đóng sau mỗi rerun. Để expanded=True giúp phần này không bị
                # ẩn khi Admin đang tìm/chọn nhân viên hoặc nhập số tiền tạm hoãn.
                with st.expander("⏭️ Tạm hoãn Vi phạm sang kỳ kế tiếp", expanded=True):
                    _leave_for_defer = load_backup_sheet_data()
                    _raw_penalty_map = _period_penalty_by_employee(current_start, current_end, _leave_for_defer, None)
                    _due_debt_map, _deferred_map, _active_debts = get_violation_debt_state(
                        current_start, current_end, final_df['Tên Hệ thống'].astype(str).tolist()
                    )
                    _defer_options = []
                    _available_by_emp = {}
                    for _emp in final_df['Tên Hệ thống'].astype(str).tolist():
                        _ek = normalize_login_name(_emp)
                        _raw_current = max(0.0, float(_money_to_float(_raw_penalty_map.get(_ek, 0))))
                        _already_deferred = max(0.0, float(_money_to_float(_deferred_map.get(_ek, 0))))
                        _available = max(0.0, _raw_current - _already_deferred)
                        if _available > 0:
                            _defer_options.append(_emp)
                            _available_by_emp[_emp] = (_raw_current, _already_deferred, _available)

                    if _defer_options:
                        _defer_emp = st.selectbox(
                            "Nhân viên", _defer_options, filter_mode="contains", key="payroll_defer_violation_emp"
                        )
                        _raw_current, _already_deferred, _available = _available_by_emp[_defer_emp]
                        c_a, c_b, c_c = st.columns(3)
                        c_a.metric("Vi phạm gốc kỳ này", f"{_raw_current:,.0f} đ".replace(',', '.'))
                        c_b.metric("Đã tạm hoãn", f"{_already_deferred:,.0f} đ".replace(',', '.'))
                        c_c.metric("Còn có thể hoãn", f"{_available:,.0f} đ".replace(',', '.'))
                        _step = 50000.0 if _available >= 50000 else max(1.0, _available)
                        _defer_amount = st.number_input(
                            "Số tiền Vi phạm muốn chuyển sang kỳ kế tiếp",
                            min_value=0.0, max_value=float(_available), value=float(_available), step=float(_step),
                            format="%.0f", key=f"payroll_defer_violation_amount_{normalize_login_name(_defer_emp)}"
                        )
                        if st.button("💾 Lưu nghĩa vụ Vi phạm sang kỳ kế tiếp", use_container_width=True, key="save_deferred_violation"):
                            ok, msg = defer_violation_to_next_period(
                                _defer_emp, _defer_amount, current_start, current_end, st.session_state.current_user
                            )
                            if ok:
                                # Cập nhật ngay bảng đang mở; lần Tính lại sau sẽ đọc lại ledger và cho cùng kết quả.
                                _tmp = st.session_state.payroll_current_df.copy()
                                _mask = _tmp['Tên Hệ thống'].apply(normalize_login_name).eq(normalize_login_name(_defer_emp))
                                if _mask.any():
                                    _idx = _tmp.index[_mask][0]
                                    _tmp.at[_idx, 'Tiền phạt trong tháng'] = max(
                                        0.0,
                                        float(_money_to_float(_tmp.at[_idx, 'Tiền phạt trong tháng'])) - float(_money_to_float(_defer_amount))
                                    )
                                    _tmp = recalculate_payroll_net(_tmp)
                                    st.session_state.payroll_current_df = _tmp
                                st.session_state.pop('payroll_adjustment_editor', None)
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
                    else:
                        st.info("Không còn khoản Vi phạm của kỳ hiện tại có thể tạm hoãn.")

                    # V54: Admin xem + sửa/xóa toàn bộ Nghĩa vụ Vi phạm đang mở.
                    # Việc sửa/xóa có hiệu lực ngay với kỳ đang mở và mọi kỳ tính sau.
                    _all_active = get_all_open_violation_debts_for_admin()
                    if isinstance(_all_active, pd.DataFrame) and not _all_active.empty:
                        _show_cols = [c for c in [
                            'Tên nhân viên','Số tiền','Nội dung','Loại','Kỳ phát sinh từ','Kỳ phát sinh đến','Bắt đầu trừ từ','Trạng thái'
                        ] if c in _all_active.columns]
                        _debt_show = _all_active[_show_cols].copy()
                        if 'Số tiền' in _debt_show.columns:
                            _debt_show['Số tiền'] = _debt_show['Số tiền'].apply(lambda x: f"{_money_to_float(x):,.0f}".replace(',', '.'))
                        st.caption("Nghĩa vụ Vi phạm đang mở")
                        st.dataframe(_debt_show, hide_index=True, width="stretch", height="content")

                        with st.expander("✏️ Sửa / Xóa Nghĩa vụ Vi phạm", expanded=False):
                            _manage_rows = _all_active.reset_index(drop=True).copy()
                            _manage_options = []
                            _manage_lookup = {}
                            for _i, _r in _manage_rows.iterrows():
                                _sheet_row = int(_r.get('__sheet_row', 0) or 0)
                                _emp = str(_r.get('Tên nhân viên', '')).strip()
                                _amt = float(_money_to_float(_r.get('Số tiền', 0)))
                                _typ = str(_r.get('Loại', '')).strip()
                                _p1 = str(_r.get('Kỳ phát sinh từ', '')).strip()
                                _p2 = str(_r.get('Kỳ phát sinh đến', '')).strip()
                                _label = f"{_emp} · {_amt:,.0f} đ · {_typ} · {_p1} → {_p2}".replace(',', '.')
                                # Bảo đảm label duy nhất nếu có nhiều dòng giống nhau.
                                if _label in _manage_lookup:
                                    _label = f"{_label} · dòng {_sheet_row}"
                                _manage_options.append(_label)
                                _manage_lookup[_label] = _r.to_dict()

                            _selected_label = st.selectbox(
                                "Chọn nghĩa vụ cần chỉnh sửa",
                                _manage_options,
                                filter_mode="contains",
                                key="admin_manage_violation_debt_select",
                            )
                            _selected = _manage_lookup.get(_selected_label, {})
                            _selected_row = int(_selected.get('__sheet_row', 0) or 0)
                            _selected_amount = max(0.0, float(_money_to_float(_selected.get('Số tiền', 0))))
                            _selected_due = _parse_vn_date(_selected.get('Bắt đầu trừ từ', '')) or current_start
                            _edit_amount = st.number_input(
                                "Số tiền nghĩa vụ",
                                min_value=1.0,
                                value=float(max(1.0, _selected_amount)),
                                step=50000.0,
                                format="%.0f",
                                key=f"edit_violation_debt_amount_{_selected_row}",
                            )
                            _edit_content = st.text_input(
                                "Nội dung",
                                value=str(_selected.get('Nội dung', '')).strip() or VIOLATION_DEBT_CONTENT,
                                key=f"edit_violation_debt_content_{_selected_row}",
                            )
                            _edit_due = st.date_input(
                                "Bắt đầu trừ từ kỳ/ngày",
                                value=_selected_due,
                                format="DD/MM/YYYY",
                                key=f"edit_violation_debt_due_{_selected_row}",
                            )
                            _btn_edit, _btn_delete = st.columns(2)
                            if _btn_edit.button(
                                "💾 Lưu chỉnh sửa nghĩa vụ",
                                use_container_width=True,
                                key=f"save_violation_debt_edit_{_selected_row}",
                            ):
                                _ok, _msg = update_violation_debt_obligation(
                                    _selected_row,
                                    _edit_amount,
                                    _edit_content,
                                    _edit_due,
                                    st.session_state.current_user,
                                )
                                if _ok:
                                    _clear_violation_debt_cache()
                                    try:
                                        st.session_state.payroll_current_df = refresh_current_payroll_violation_debt(
                                            st.session_state.payroll_current_df,
                                            current_start,
                                            current_end,
                                        )
                                    except Exception:
                                        pass
                                    st.session_state.pop('payroll_adjustment_editor', None)
                                    st.success(_msg + " Kỳ đang mở đã được tính lại; các kỳ tiếp theo sẽ dùng số mới.")
                                    st.rerun()
                                else:
                                    st.error(_msg)

                            _confirm_delete = st.checkbox(
                                "Tôi xác nhận xóa vĩnh viễn nghĩa vụ này",
                                key=f"confirm_delete_violation_debt_{_selected_row}",
                            )
                            if _btn_delete.button(
                                "🗑️ Xóa nghĩa vụ",
                                use_container_width=True,
                                disabled=not _confirm_delete,
                                key=f"delete_violation_debt_{_selected_row}",
                            ):
                                _ok, _msg = delete_violation_debt_obligation(
                                    _selected_row,
                                    st.session_state.current_user,
                                )
                                if _ok:
                                    _clear_violation_debt_cache()
                                    try:
                                        st.session_state.payroll_current_df = refresh_current_payroll_violation_debt(
                                            st.session_state.payroll_current_df,
                                            current_start,
                                            current_end,
                                        )
                                    except Exception:
                                        pass
                                    st.session_state.pop('payroll_adjustment_editor', None)
                                    st.success(_msg + " Kỳ đang mở đã được tính lại; các kỳ tiếp theo sẽ không còn khoản đã xóa.")
                                    st.rerun()
                                else:
                                    st.error(_msg)

                            st.caption(
                                "Thay đổi áp dụng ngay cho kỳ lương đang mở và mọi kỳ tính sau. "
                                "Các bản lương lịch sử đã lưu không bị ghi đè tự động; khi cần cập nhật bản cũ, mở bản đó và bấm Cập nhật bảng lương từ hệ thống."
                            )
                    else:
                        st.caption("Hiện không có Nghĩa vụ Vi phạm nào đang mở.")

            # Thông báo rõ các dòng âm: khi Admin lưu bảng lương, phần âm sẽ chuyển thành nghĩa vụ Vi phạm kỳ sau.
            _negative_rows = final_df[final_df['Số tiền thực nhận'].apply(_money_to_float) < 0]
            if not _negative_rows.empty:
                _negative_total = abs(_negative_rows['Số tiền thực nhận'].apply(_money_to_float).sum())
                _negative_details = [
                    f"{str(_r.get('Tên Hệ thống','')).strip()}: {_money_to_float(_r.get('Số tiền thực nhận',0)):,.0f} đ".replace(',', '.')
                    for _, _r in _negative_rows.iterrows()
                ]
                st.warning(
                    f"Có {len(_negative_rows)} nhân viên Thực nhận âm, tổng phần chưa hoàn thành "
                    f"{_negative_total:,.0f} đ. Khi lưu bảng lương, những nhân viên Admin KHÔNG chủ động tạm hoãn "
                    f"sẽ được tự lưu thành '{VIOLATION_DEBT_CONTENT}' và trừ từ kỳ lương kế tiếp.".replace(',', '.')
                )
                st.markdown("**Nhân viên Thực nhận âm:** " + " · ".join(_negative_details))

            total_salary = final_df['Tiền Lương'].sum()
            total_penalty = final_df['Tiền phạt trong tháng'].apply(_money_to_float).sum() + final_df.get('Vi phạm kỳ trước', pd.Series(0, index=final_df.index)).apply(_money_to_float).sum()
            total_net = final_df['Số tiền thực nhận'].sum()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Nhân viên", len(final_df))
            c2.metric("Tổng Tiền Lương", f"{total_salary:,.0f} đ".replace(',', '.'))
            c3.metric("Tổng tiền phạt", f"{total_penalty:,.0f} đ".replace(',', '.'))
            c4.metric("Tổng thực nhận", f"{total_net:,.0f} đ".replace(',', '.'))

            display_cols = [
                "TT", "Tên Hệ thống", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
                "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương",
                "Tiền hỗ trợ Locker", "Số tiền thực nhận"
            ]
            st.markdown("### 📋 Bảng lương tổng hợp")
            # HTML table dùng width:100% + table-layout:fixed để không tạo thanh cuộn ngang/dọc.
            web_df = final_df[display_cols].copy()
            web_df, payroll_web_widths = apply_table_layout_df(web_df, "payroll_current")
            payroll_internal_order = list(web_df.columns)
            money_web_cols = [c for c in payroll_internal_order if c.startswith('Tiền') or c in {'Tích lũy','Chi Phí Sinh Hoạt','Vi phạm kỳ trước','Số tiền thực nhận'}]
            for c in money_web_cols:
                web_df[c] = web_df[c].apply(lambda v: f"{_money_to_float(v):,.0f}".replace(',', '.'))

            # V53: nếu Admin đã tạm hoãn Vi phạm của kỳ này, ghi chú ngay TRONG ô Vi phạm.
            # Dùng token rồi thay bằng HTML sau khi pandas escape bảng, để không phải tắt escaping
            # cho các dữ liệu tên nhân viên lấy từ Google Sheet.
            _violation_note_tokens = {}
            try:
                _, _web_deferred_map, _ = get_violation_debt_state(
                    current_start, current_end, final_df['Tên Hệ thống'].astype(str).tolist()
                )
                if 'Tiền phạt trong tháng' in web_df.columns:
                    for _row_pos, _row_idx in enumerate(web_df.index):
                        _emp_name = str(final_df.loc[_row_idx, 'Tên Hệ thống']).strip() if _row_idx in final_df.index else ''
                        _defer_amt = max(0.0, float(_money_to_float(_web_deferred_map.get(normalize_login_name(_emp_name), 0))))
                        if _defer_amt <= 0:
                            continue
                        _base_violation = str(web_df.at[_row_idx, 'Tiền phạt trong tháng'])
                        _token = f"__VERA_DEFER_NOTE_{_row_pos}__"
                        _note_amount = f"{_defer_amt:,.0f}".replace(',', '.')
                        _violation_note_tokens[_token] = (
                            f"<div class='payroll-violation-value'>{_base_violation}</div>"
                            f"<div class='payroll-violation-note'>Trừ kỳ lương kế tiếp: {_note_amount} đ</div>"
                        )
                        web_df.at[_row_idx, 'Tiền phạt trong tháng'] = _token
            except Exception:
                _violation_note_tokens = {}

            # V50: bảng tổng hợp trên website không hiển thị thông tin ngân hàng hoặc Email.
            # V46: ghi nhớ dòng Thực nhận <= 0 trước khi đổi định dạng/đổi tên cột để
            # có thể tô vàng toàn bộ dòng trên bảng Thống kê lương nhân viên.
            _web_non_positive_mask = final_df['Số tiền thực nhận'].apply(_money_to_float).le(0).tolist()

            # Chỉ đổi tên cột lúc hiển thị; dữ liệu nội bộ vẫn giữ tên chuẩn để tính toán/lưu lịch sử.
            web_df = web_df.rename(columns={c: PAYROLL_DISPLAY_LABELS.get(c, c) for c in web_df.columns})
            payroll_html = web_df.to_html(index=False, escape=True, classes='vera-payroll-table')
            # Chỉ mở HTML đối với token do hệ thống tự tạo; mọi dữ liệu Sheet khác vẫn được escape an toàn.
            for _token, _fragment in _violation_note_tokens.items():
                payroll_html = payroll_html.replace(_token, _fragment)

            # Gắn class cho từng <tr> trong <tbody> theo đúng thứ tự dòng.
            # Không phụ thuộc alternating row color nên dòng <= 0 luôn nổi bật màu vàng.
            try:
                _head_html, _body_tail = payroll_html.split('<tbody>', 1)
                _body_html, _tail_html = _body_tail.split('</tbody>', 1)
                _row_counter = {'i': 0}
                def _mark_payroll_non_positive_row(_match):
                    _i = _row_counter['i']
                    _row_counter['i'] += 1
                    if _i < len(_web_non_positive_mask) and _web_non_positive_mask[_i]:
                        return '<tr class="payroll-nonpositive">'
                    return '<tr>'
                _body_html = re.sub(r'<tr>', _mark_payroll_non_positive_row, _body_html)
                payroll_html = _head_html + '<tbody>' + _body_html + '</tbody>' + _tail_html
            except Exception:
                pass

            width_total = max(1, sum(int(payroll_web_widths.get(c, 140)) for c in payroll_internal_order))
            colgroup = '<colgroup>' + ''.join(
                f'<col style="width:{(int(payroll_web_widths.get(c, 140)) / width_total) * 100:.3f}%">'
                for c in payroll_internal_order
            ) + '</colgroup>'
            payroll_html = payroll_html.replace('>', '>' + colgroup, 1)
            payroll_layout_css = table_layout_html_css(
                "payroll_current", payroll_internal_order, "table.vera-payroll-table"
            )
            st.markdown(
                f"""<style>
                {payroll_layout_css}
                .vera-payroll-wrap{{width:100%;overflow:visible;}}
                table.vera-payroll-table{{width:100%;table-layout:fixed;border-collapse:collapse;font-size:clamp(8px,.68vw,12px);}}
                table.vera-payroll-table th{{background:#A1948C!important;color:#000!important;font-weight:700!important;padding:5px 3px;border:1px solid #c9c9c9;white-space:normal!important;overflow-wrap:anywhere!important;word-break:break-word!important;line-height:1.15!important;vertical-align:middle!important;}}
                table.vera-payroll-table td{{padding:4px 3px;border:1px solid #dedede;white-space:normal;word-break:break-word;vertical-align:middle;}}
                table.vera-payroll-table .payroll-violation-value{{line-height:1.05;}}
                table.vera-payroll-table .payroll-violation-note{{font-size:6px!important;line-height:1.05!important;margin-top:2px;white-space:normal!important;font-weight:400;}}
                table.vera-payroll-table tbody tr:nth-child(even){{background:#fafafa;}}
                table.vera-payroll-table tbody tr.payroll-nonpositive td{{background:#FFF2CC!important;}}
                @media(max-width:800px){{table.vera-payroll-table{{font-size:7px;}}table.vera-payroll-table th,table.vera-payroll-table td{{padding:3px 1px;}}}}
                </style>""" + f"<div class='vera-payroll-wrap'>{payroll_html}</div>",
                unsafe_allow_html=True
            )

            c_save, c_export = st.columns(2)
            with c_save:
                if st.button("💾 Lưu bảng lương kỳ này vào hệ thống", use_container_width=True):
                    ok, msg, batch_id = save_payroll_snapshot(
                        final_df, current_start, current_end,
                        st.session_state.get('payroll_current_source', ''), st.session_state.current_user
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        load_payroll_history.clear()
                        st.caption(f"Mã bản lưu: {batch_id}")
            with c_export:
                excel_bytes = build_payroll_excel_bytes(final_df, current_start, current_end)
                st.download_button(
                    "📥 Export toàn bộ Bảng lương Excel",
                    data=excel_bytes,
                    file_name=f"BangLuong_{current_start.strftime('%d%m%Y')}_{current_end.strftime('%d%m%Y')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

            with st.expander("📧 GỬI BẢNG LƯƠNG QUA EMAIL"):
                # V44: không gửi email cá nhân cho nhân viên có Thực nhận = 0.
                _email_net = final_df['Số tiền thực nhận'].apply(_money_to_float) if 'Số tiền thực nhận' in final_df.columns else pd.Series(0, index=final_df.index)
                emailable = final_df[
                    final_df['Email'].astype(str).str.contains('@', na=False) & (_email_net > 0)
                ].copy()
                employees_email = emailable['Tên Hệ thống'].tolist()
                selected_email_emps = st.multiselect(
                    "Chọn 1, nhiều hoặc tất cả nhân viên:", employees_email, default=employees_email,
                    filter_mode="contains", key="payroll_email_recipients"
                )
                st.caption(
                    f"Có {len(employees_email)} nhân viên có Email hợp lệ và Thực nhận > 0. "
                    "Nhân viên có Thực nhận ≤ 0 sẽ tự động không được gửi mail."
                )
                if st.button("🚀 Gửi bảng lương cho nhân viên đã chọn", use_container_width=True):
                    if not selected_email_emps:
                        st.warning("Vui lòng chọn ít nhất 1 nhân viên.")
                    else:
                        sender_email = "veraspabienhoa@gmail.com"
                        sender_pass = "zvtgbysfmdaqxaau"
                        progress = st.progress(0)
                        ok_count, errors = 0, []
                        # Chỉ đọc Sheet1 lịch nghỉ một lần rồi lọc theo từng nhân viên, tránh quota 429.
                        email_leave_df = load_backup_sheet_data()
                        for idx, emp in enumerate(selected_email_emps):
                            row = emailable[emailable['Tên Hệ thống'] == emp].iloc[0]
                            emp_violations = get_employee_violation_details(emp, current_start, current_end, email_leave_df)
                            ok, msg = send_payroll_email(
                                sender_email, sender_pass, str(row['Email']).strip(), row,
                                current_start, current_end, emp_violations
                            )
                            if ok: ok_count += 1
                            else: errors.append(f"{emp}: {msg}")
                            progress.progress((idx + 1) / len(selected_email_emps))
                            time.sleep(0.35)
                        if ok_count: st.success(f"Đã gửi thành công {ok_count}/{len(selected_email_emps)} email bảng lương.")
                        for e in errors: st.error(e)

            if st.session_state.current_role == "admin":
                with st.expander("📨 GỬI BẢNG LƯƠNG TỔNG HỢP CHO LỄ TÂN"):
                    # Hiển thị TẤT CẢ tài khoản Lễ tân trước; chỉ sau khi Admin check tên
                    # mới lấy Email tương ứng từ hồ sơ hệ thống. Không lọc Email từ đầu.
                    letan_df = df_credentials.copy()
                    if not letan_df.empty and 'Phân quyền' in letan_df.columns:
                        letan_df = letan_df[
                            letan_df['Phân quyền'].astype(str).str.strip().str.lower().isin(['letan', 'quanly'])
                        ].copy()
                        if 'Tên nhân viên' in letan_df.columns:
                            letan_df = letan_df[
                                ~letan_df['Tên nhân viên'].astype(str).apply(normalize_login_name).isin({
                                    'ten nhan vien', 'ten he thong', 'username', 'user name'
                                })
                            ].copy()
                    if letan_df.empty:
                        st.info("Không có tài khoản Lễ tân trong hồ sơ hệ thống.")
                    else:
                        st.write("**Check đúng 1 Lễ tân để hệ thống lấy Email từ hồ sơ:**")
                        checked_letan = []
                        for i, (_, lr) in enumerate(letan_df.iterrows()):
                            lname = str(lr.get('Tên nhân viên', '')).strip()
                            if not lname:
                                continue
                            if st.checkbox(lname, key=f"payroll_letan_check_{i}_{normalize_login_name(lname)}"):
                                checked_letan.append(lname)

                        if len(checked_letan) > 1:
                            st.warning("⚠️ Chỉ được check 1 Lễ tân cho mỗi lần gửi.")
                        elif len(checked_letan) == 1:
                            selected_letan = checked_letan[0]
                            matched = letan_df[
                                letan_df['Tên nhân viên'].astype(str).apply(normalize_login_name)
                                == normalize_login_name(selected_letan)
                            ]
                            if matched.empty:
                                st.error("Không tìm thấy hồ sơ Lễ tân đã chọn.")
                            else:
                                rletan = matched.iloc[0]
                                letan_email = str(rletan.get('Email', '')).strip()
                                if letan_email and '@' in letan_email:
                                    st.success(f"📧 Email nhận: {letan_email}")
                                else:
                                    st.warning(f"⚠️ Tài khoản {selected_letan} chưa có Email hợp lệ trong hồ sơ.")

                                if st.button(
                                    "📤 Gửi bảng lương tổng hợp cho Lễ tân đã check",
                                    use_container_width=True,
                                    key="send_payroll_summary_letan",
                                    disabled=not (letan_email and '@' in letan_email)
                                ):
                                    sender_email = "veraspabienhoa@gmail.com"
                                    sender_pass = "zvtgbysfmdaqxaau"
                                    ok, msg = send_payroll_summary_email(
                                        sender_email, sender_pass, letan_email,
                                        selected_letan, final_df, current_start, current_end
                                    )
                                    (st.success if ok else st.error)(msg)
                        else:
                            st.caption("Chưa chọn Lễ tân nhận bảng lương tổng hợp.")

            # V49: BẢNG ĐIỀU CHỈNH BẢNG LƯƠNG LUÔN ẨN VÀ NẰM CUỐI CÙNG CỦA TRANG TÍNH LƯƠNG.
            # Đây chính là bảng TT / Tên Hệ thống / Tiền Lương / Hỗ Trợ Hoàn Lại / Tích lũy / ...
            # Người dùng chỉ mở khi thật sự cần điều chỉnh.
            with st.expander("✏️ Mở bảng điều chỉnh bảng lương", expanded=False):
                st.caption("Bảng này được ẩn mặc định. Chỉ mở khi cần chỉnh các khoản cho bảng lương hiện tại.")
                editor_cols = [
                    "TT", "Tên Hệ thống", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
                    "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương", "Tiền hỗ trợ Locker"
                ]
                editor_source = st.session_state.get('payroll_current_df')
                if isinstance(editor_source, pd.DataFrame) and not editor_source.empty:
                    editor_source = _filter_real_payroll_rows(editor_source.copy())
                    editor_df = editor_source[editor_cols].copy()
                    editor_df, _ = apply_table_layout_df(editor_df, "payroll_current")
                    col_cfg = {
                        "TT": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS["TT"], format="%d", disabled=True, width=layout_width("payroll_current", "TT", "small")),
                        "Tên Hệ thống": st.column_config.TextColumn(PAYROLL_DISPLAY_LABELS["Tên Hệ thống"], disabled=True, width=layout_width("payroll_current", "Tên Hệ thống", "small")),
                        "Tiền Lương": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS["Tiền Lương"], format="%,d", disabled=True, width=layout_width("payroll_current", "Tiền Lương", "small")),
                        "Tiền phạt trong tháng": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS["Tiền phạt trong tháng"], format="%,d", disabled=True, width=layout_width("payroll_current", "Tiền phạt trong tháng", "small")),
                        "Vi phạm kỳ trước": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS["Vi phạm kỳ trước"], format="%,d", disabled=True, width=layout_width("payroll_current", "Vi phạm kỳ trước", "small")),
                        "Tích lũy": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS["Tích lũy"], format="%,d", disabled=True, width=layout_width("payroll_current", "Tích lũy", "small")),
                    }
                    for c in [x for x in PAYROLL_ADJUSTMENT_COLUMNS if x != "Tích lũy"]:
                        col_cfg[c] = st.column_config.NumberColumn(
                            PAYROLL_DISPLAY_LABELS.get(c, c), min_value=0.0, step=50000.0,
                            format="%,d", width=layout_width("payroll_current", c, "small")
                        )
                    edited = st.data_editor(
                        editor_df, key="payroll_adjustment_editor", width="stretch", height="content", hide_index=True,
                        row_height=layout_row_height("payroll_current"),
                        column_config=col_cfg,
                        disabled=["TT", "Tên Hệ thống", "Tiền Lương", "Tích lũy", "Tiền phạt trong tháng", "Vi phạm kỳ trước"]
                    )

                    adjusted_df = editor_source.copy()
                    for c in editor_cols:
                        if c in edited.columns:
                            adjusted_df[c] = edited[c].values
                    adjusted_df = recalculate_payroll_net(adjusted_df)
                    adjusted_df = _filter_real_payroll_rows(adjusted_df)

                    compare_cols = [c for c in editor_cols + ["Số tiền thực nhận"] if c in adjusted_df.columns and c in editor_source.columns]
                    _changed = False
                    try:
                        _left = adjusted_df[compare_cols].reset_index(drop=True).fillna(0)
                        _right = editor_source[compare_cols].reset_index(drop=True).fillna(0)
                        _changed = not _left.equals(_right)
                    except Exception:
                        _changed = True
                    if _changed:
                        st.session_state.payroll_current_df = adjusted_df
                        st.rerun()
                else:
                    st.info("Chưa có dữ liệu bảng lương để điều chỉnh.")

    with tab_history:
        delete_flash = st.session_state.pop("payroll_history_delete_flash", None)
        if delete_flash:
            flash_type, flash_text = delete_flash
            if flash_type == "success":
                st.success(flash_text)
            else:
                st.warning(flash_text)

        history = load_payroll_history()
        if history.empty or 'Mã bản lưu' not in history.columns:
            st.info("Chưa có bảng lương nào được lưu trong hệ thống.")
        else:
            batches = [x for x in history['Mã bản lưu'].dropna().astype(str).unique().tolist() if x.strip()]
            # Bản mới nhất nằm cuối Sheet nên đảo lên đầu.
            batches = list(reversed(batches))

            # Admin có thể xóa bớt một hoặc nhiều bản lịch sử. Chức năng này chỉ xóa
            # snapshot bảng lương, tuyệt đối không hoàn tác Tích lũy / Vi phạm / hồ sơ.
            if st.session_state.current_role == "admin":
                batch_labels = {}
                for _batch_id in batches:
                    _g = history[history['Mã bản lưu'].astype(str) == str(_batch_id)]
                    if _g.empty:
                        batch_labels[_batch_id] = str(_batch_id)
                    else:
                        _r = _g.iloc[0]
                        _period = f"{_r.get('Từ ngày','')} → {_r.get('Đến ngày','')}"
                        _saved_at = f"{_r.get('Ngày lưu','')} {_r.get('Giờ lưu','')}".strip()
                        batch_labels[_batch_id] = f"{_batch_id} | {_period} | lưu {_saved_at}"

                with st.expander("🗑 Xóa bớt lịch sử bảng lương (Admin)", expanded=False):
                    st.caption(
                        "Có thể chọn 1 hoặc nhiều bản lương để xóa. Việc này chỉ xóa Lịch sử bảng lương đã lưu; "
                        "không thay đổi sheet TichLuy, dữ liệu vi phạm, lịch nghỉ hoặc hồ sơ nhân viên."
                    )
                    delete_batches = st.multiselect(
                        "Chọn bản lương cần xóa:",
                        options=batches,
                        default=[],
                        format_func=lambda x: batch_labels.get(x, str(x)),
                        filter_mode="contains",
                        key="payroll_history_delete_batches",
                    )
                    if delete_batches:
                        st.warning(
                            f"Bạn đang chọn xóa {len(delete_batches)} bản lương. Thao tác này không có nút hoàn tác trong ứng dụng."
                        )
                    confirm_delete_history = st.checkbox(
                        "Tôi xác nhận xóa vĩnh viễn các bản lương đã chọn khỏi lịch sử",
                        key="confirm_delete_payroll_history",
                    )
                    if st.button(
                        "🗑 Xóa các bản lương đã chọn",
                        use_container_width=True,
                        type="primary",
                        key="delete_selected_payroll_history",
                        disabled=not (delete_batches and confirm_delete_history),
                    ):
                        ok_delete, msg_delete, deleted_batches = delete_payroll_snapshots(delete_batches)
                        if ok_delete:
                            # Dọn state liên quan đến các batch vừa xóa để lần rerun sau không giữ editor cũ.
                            for _deleted_batch in deleted_batches:
                                for _state_key in (
                                    f"payroll_history_system_refresh_{_deleted_batch}",
                                    f"payroll_history_editor_version_{_deleted_batch}",
                                ):
                                    st.session_state.pop(_state_key, None)
                            st.session_state.pop("payroll_history_batch", None)
                            st.session_state.pop("payroll_history_delete_batches", None)
                            st.session_state.pop("confirm_delete_payroll_history", None)
                            st.session_state["payroll_history_delete_flash"] = ("success", msg_delete)
                            st.rerun()
                        else:
                            st.error(msg_delete)

            batch = st.selectbox("Chọn bản lương đã lưu:", batches, filter_mode="contains", key="payroll_history_batch")
            saved = history[history['Mã bản lưu'].astype(str) == str(batch)].copy()
            if not saved.empty:
                st.info(
                    f"Kỳ {saved.iloc[0].get('Từ ngày','')} → {saved.iloc[0].get('Đến ngày','')} | "
                    f"Lưu bởi {saved.iloc[0].get('Người lưu','')} lúc {saved.iloc[0].get('Giờ lưu','')} ngày {saved.iloc[0].get('Ngày lưu','')}"
                )
                saved_table = payroll_history_to_table(saved)
                saved_table = _filter_real_payroll_rows(saved_table)

                # Nếu Admin vừa bấm "Cập nhật bảng lương từ hệ thống", dùng bản đã làm mới
                # làm dữ liệu nền cho editor ở lần rerun kế tiếp. Mỗi batch có state riêng.
                hist_refresh_key = f"payroll_history_system_refresh_{batch}"
                hist_editor_version_key = f"payroll_history_editor_version_{batch}"
                if hist_refresh_key in st.session_state:
                    try:
                        refreshed_state_df = st.session_state.get(hist_refresh_key)
                        if isinstance(refreshed_state_df, pd.DataFrame) and not refreshed_state_df.empty:
                            saved_table = _filter_real_payroll_rows(refreshed_state_df.copy())
                    except Exception:
                        pass

                # Không hiển thị/tính dòng Lễ tân kể cả với bản lịch sử cũ.
                try:
                    letan_keys = set(
                        df_credentials.loc[
                            df_credentials['Phân quyền'].astype(str).str.strip().str.lower().isin(['letan', 'quanly']), 'Tên nhân viên'
                        ].apply(normalize_login_name).tolist()
                    )
                    if 'Tên Hệ thống' in saved_table.columns and letan_keys:
                        saved_table = saved_table[~saved_table['Tên Hệ thống'].apply(normalize_login_name).isin(letan_keys)].copy()
                except Exception:
                    pass

                try:
                    hs = pd.to_datetime(saved.iloc[0]['Từ ngày'], dayfirst=True).date()
                    he = pd.to_datetime(saved.iloc[0]['Đến ngày'], dayfirst=True).date()
                except Exception:
                    hs, he = get_vn_today(), get_vn_today()

                # Cập nhật các dữ liệu hệ thống có thể thay đổi sau khi bản lương đã được lưu.
                # Nút được đặt ngay phía trên tiêu đề Mở lại và chỉnh sửa bản lương theo yêu cầu.
                st.caption(
                    "Nút cập nhật hệ thống sẽ làm mới: Tích lũy, Vi phạm, Phí Sinh Hoạt, Tiền hỗ trợ Locker, "
                    "Tài khoản ngân hàng, Tên ngân hàng và Email. Tiền Lương không bị thay đổi."
                )
                if st.button(
                    "🔄 Cập nhật bảng lương từ hệ thống",
                    use_container_width=True,
                    key=f"refresh_payroll_from_system_{batch}"
                ):
                    progress_refresh = st.progress(0)
                    status_refresh = st.empty()
                    try:
                        status_refresh.info("⏳ Đang tải hồ sơ nhân viên mới nhất...")
                        progress_refresh.progress(20)
                        try:
                            load_credentials.clear()
                        except Exception:
                            pass
                        credentials_live = load_credentials()

                        status_refresh.info("⏳ Đang tải tiền phạt trong kỳ từ hệ thống lịch nghỉ...")
                        progress_refresh.progress(45)
                        try:
                            load_backup_sheet_data.clear()
                        except Exception:
                            pass
                        leave_live = load_backup_sheet_data()

                        status_refresh.info("⏳ Đang tải Tích lũy, Phí sinh hoạt / Locker và cập nhật bảng lương...")
                        progress_refresh.progress(70)
                        _clear_payroll_config_cache()
                        # Bắt buộc làm mới TichLuy để nút cập nhật không dùng snapshot cache 120 giây cũ.
                        try:
                            load_tichluy_tracking.clear()
                        except Exception:
                            pass
                        refreshed_df, refresh_meta = refresh_saved_payroll_from_system(
                            saved_table, hs, he,
                            credentials_df=credentials_live,
                            leave_primary=leave_live
                        )

                        current_hist_version = int(st.session_state.get(hist_editor_version_key, 0) or 0)
                        st.session_state[hist_refresh_key] = refreshed_df
                        st.session_state[hist_editor_version_key] = current_hist_version + 1
                        saved_table = _filter_real_payroll_rows(refreshed_df.copy())

                        progress_refresh.progress(100)
                        status_refresh.success(
                            f"✅ Đã cập nhật dữ liệu hệ thống cho {refresh_meta.get('updated', len(refreshed_df))} nhân viên; "
                            f"Tích lũy kỳ này có số tiền ở {refresh_meta.get('tichluy_updated', 0)} nhân viên. "
                            "Tiền Lương và các khoản nhập tay được giữ nguyên; Thực nhận đã tính lại."
                        )
                        missing_profiles = refresh_meta.get('missing', [])
                        if missing_profiles:
                            st.warning(
                                "⚠️ Không tìm thấy hồ sơ hệ thống của: " + ", ".join(missing_profiles)
                                + ". Các thông tin ngân hàng/email cũ của những người này được giữ nguyên."
                            )
                    except Exception as e:
                        progress_refresh.empty()
                        status_refresh.error(f"❌ Không cập nhật được bảng lương từ hệ thống: {e}")

                st.markdown("#### ✏️ Mở lại và chỉnh sửa bản lương")
                st.caption(
                    "Bạn có thể sửa trực tiếp các khoản tiền bên dưới. Cột Thực nhận được hệ thống tự tính lại. "
                    "Khi bấm Ghi đè, Mã bản lưu được giữ nguyên và bản cũ sẽ được thay bằng dữ liệu mới."
                )

                history_edit_cols = [c for c in [
                    "TT", "Tên Hệ thống", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy",
                    "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương", "Tiền hỗ trợ Locker",
                    "Số tiền thực nhận", "Số tài khoản ngân hàng", "Tên ngân hàng", "Email"
                ] if c in saved_table.columns]
                hist_editor_df = saved_table[history_edit_cols].copy()
                hist_editor_df, _ = apply_table_layout_df(hist_editor_df, "payroll_history")

                hist_col_cfg = {
                    "TT": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS.get("TT", "TT"), format="%d", disabled=True, width=layout_width("payroll_history", "TT", "small")),
                    "Tên Hệ thống": st.column_config.TextColumn(PAYROLL_DISPLAY_LABELS.get("Tên Hệ thống", "Tên Hệ thống"), disabled=True, width=layout_width("payroll_history", "Tên Hệ thống", "small")),
                    "Số tiền thực nhận": st.column_config.NumberColumn(PAYROLL_DISPLAY_LABELS.get("Số tiền thực nhận", "Thực nhận"), format="%,d", disabled=True, width=layout_width("payroll_history", "Số tiền thực nhận", "small")),
                    "Số tài khoản ngân hàng": st.column_config.TextColumn(PAYROLL_DISPLAY_LABELS.get("Số tài khoản ngân hàng", "Tài khoản ngân hàng"), disabled=True, width=layout_width("payroll_history", "Số tài khoản ngân hàng", "small")),
                    "Tên ngân hàng": st.column_config.TextColumn(PAYROLL_DISPLAY_LABELS.get("Tên ngân hàng", "Tên ngân hàng"), disabled=True, width=layout_width("payroll_history", "Tên ngân hàng", "small")),
                    "Email": st.column_config.TextColumn(PAYROLL_DISPLAY_LABELS.get("Email", "Email"), disabled=True, width=layout_width("payroll_history", "Email", "small")),
                }
                for c in [
                    "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Tích lũy", "Chi Phí Sinh Hoạt",
                    "Tiền phạt trong tháng", "Vi phạm kỳ trước", "Tiền ứng lương", "Tiền hỗ trợ Locker"
                ]:
                    if c in hist_editor_df.columns:
                        hist_col_cfg[c] = st.column_config.NumberColumn(
                            PAYROLL_DISPLAY_LABELS.get(c, c), min_value=0.0, step=50000.0, format="%,d", width=layout_width("payroll_history", c, "small"),
                            disabled=(c in {"Tích lũy", "Vi phạm kỳ trước"})
                        )

                hist_editor_version = int(st.session_state.get(hist_editor_version_key, 0) or 0)
                edited_hist = st.data_editor(
                    hist_editor_df,
                    key=f"payroll_history_editor_{batch}_{hist_editor_version}",
                    width="stretch", height="content", hide_index=True,
                    row_height=layout_row_height("payroll_history"),
                    column_config=hist_col_cfg,
                    disabled=[c for c in ["TT", "Tên Hệ thống", "Tích lũy", "Vi phạm kỳ trước", "Số tiền thực nhận", "Số tài khoản ngân hàng", "Tên ngân hàng", "Email"] if c in hist_editor_df.columns]
                )

                edited_saved_table = saved_table.copy()
                for c in edited_hist.columns:
                    if c in edited_saved_table.columns:
                        edited_saved_table[c] = edited_hist[c].values
                edited_saved_table = recalculate_payroll_net(edited_saved_table)
                edited_saved_table = _filter_real_payroll_rows(edited_saved_table)

                # Hiển thị nhanh tổng sau khi sửa để Admin kiểm tra trước khi ghi đè.
                h1, h2, h3 = st.columns(3)
                h1.metric("Nhân viên", len(edited_saved_table))
                h2.metric("Tổng Tiền Lương", f"{edited_saved_table['Tiền Lương'].apply(_money_to_float).sum():,.0f} đ".replace(',', '.'))
                h3.metric("Tổng Thực nhận", f"{edited_saved_table['Số tiền thực nhận'].apply(_money_to_float).sum():,.0f} đ".replace(',', '.'))

                confirm_overwrite = st.checkbox(
                    f"Tôi xác nhận ghi đè bản lương {batch}",
                    key=f"confirm_payroll_overwrite_{batch}"
                )
                c_overwrite, c_export_hist = st.columns(2)
                with c_overwrite:
                    if st.button(
                        "💾 Ghi đè cập nhật bản lương này",
                        use_container_width=True,
                        key=f"overwrite_payroll_{batch}",
                        disabled=not confirm_overwrite
                    ):
                        source_label = str(saved.iloc[0].get('Nguồn dữ liệu', '')).strip()
                        ok, msg = overwrite_payroll_snapshot(
                            batch, edited_saved_table, hs, he, source_label, st.session_state.current_user
                        )
                        if ok:
                            load_payroll_history.clear()
                            try:
                                st.session_state.pop(hist_refresh_key, None)
                                st.session_state[hist_editor_version_key] = hist_editor_version + 1
                            except Exception:
                                pass
                            st.success(msg)
                            st.info(
                                f"Bản {batch} đã được cập nhật lúc {datetime.now(VN_TZ).strftime('%H:%M:%S %d/%m/%Y')} "
                                f"bởi {st.session_state.current_user}."
                            )
                        else:
                            st.error(msg)

                with c_export_hist:
                    try:
                        hist_excel = build_payroll_excel_bytes(edited_saved_table, hs, he)
                        st.download_button(
                            "📥 Export bản đang chỉnh sửa",
                            data=hist_excel,
                            file_name=f"BangLuong_DaLuu_{hs.strftime('%d%m%Y')}_{he.strftime('%d%m%Y')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key=f"export_payroll_history_{batch}"
                        )
                    except Exception as e:
                        st.warning(f"Không tạo được file export lịch sử: {e}")

                # --- GỬI EMAIL TỪ BẢN LƯƠNG ĐANG MỞ / ĐANG CHỈNH SỬA ---
                # Dùng trực tiếp edited_saved_table để email phản ánh đúng số liệu Admin/Lễ tân/Quản lý
                # đang nhìn thấy trên màn hình, kể cả trước khi bấm Ghi đè.
                with st.expander("📧 GỬI BẢNG LƯƠNG QUA EMAIL (BẢN ĐANG CHỈNH SỬA)"):
                    st.caption(
                        "Email và file đính kèm sẽ dùng số liệu của bản đang chỉnh sửa hiện tại. "
                        "Nếu cần lưu các thay đổi này vào hệ thống, hãy bấm Ghi đè cập nhật bản lương."
                    )
                    hist_emailable = edited_saved_table.copy()
                    if 'Email' in hist_emailable.columns:
                        _hist_email_net = (
                            hist_emailable['Số tiền thực nhận'].apply(_money_to_float)
                            if 'Số tiền thực nhận' in hist_emailable.columns
                            else pd.Series(0, index=hist_emailable.index)
                        )
                        hist_emailable = hist_emailable[
                            hist_emailable['Email'].astype(str).str.contains('@', na=False) & (_hist_email_net > 0)
                        ].copy()
                    else:
                        hist_emailable = pd.DataFrame()

                    hist_employee_names = (
                        hist_emailable['Tên Hệ thống'].astype(str).tolist()
                        if not hist_emailable.empty and 'Tên Hệ thống' in hist_emailable.columns else []
                    )
                    hist_selected_email_emps = st.multiselect(
                        "Chọn 1, nhiều hoặc tất cả nhân viên:",
                        hist_employee_names,
                        default=hist_employee_names,
                        filter_mode="contains",
                        key=f"payroll_history_email_recipients_{batch}"
                    )
                    st.caption(
                        f"Có {len(hist_employee_names)} nhân viên có Email hợp lệ và Thực nhận > 0 trong bản lương này. "
                        "Nhân viên có Thực nhận = 0 sẽ không được gửi mail."
                    )

                    if st.button(
                        "🚀 Gửi bảng lương cho nhân viên đã chọn",
                        use_container_width=True,
                        key=f"send_payroll_history_employees_{batch}"
                    ):
                        if not hist_selected_email_emps:
                            st.warning("Vui lòng chọn ít nhất 1 nhân viên.")
                        else:
                            sender_email = "veraspabienhoa@gmail.com"
                            sender_pass = "zvtgbysfmdaqxaau"
                            progress_hist_email = st.progress(0)
                            hist_ok_count, hist_errors = 0, []
                            # Một snapshot lịch vi phạm dùng chung cho toàn bộ email trong lần gửi.
                            hist_email_leave_df = load_backup_sheet_data()
                            for idx, emp in enumerate(hist_selected_email_emps):
                                matched_emp = hist_emailable[
                                    hist_emailable['Tên Hệ thống'].astype(str) == str(emp)
                                ]
                                if matched_emp.empty:
                                    hist_errors.append(f"{emp}: Không tìm thấy dữ liệu bảng lương.")
                                else:
                                    row = matched_emp.iloc[0]
                                    to_email = str(row.get('Email', '')).strip()
                                    emp_violations = get_employee_violation_details(emp, hs, he, hist_email_leave_df)
                                    ok, msg = send_payroll_email(
                                        sender_email, sender_pass, to_email, row, hs, he, emp_violations
                                    )
                                    if ok:
                                        hist_ok_count += 1
                                    else:
                                        hist_errors.append(f"{emp}: {msg}")
                                progress_hist_email.progress((idx + 1) / len(hist_selected_email_emps))
                                time.sleep(0.35)

                            if hist_ok_count:
                                st.success(
                                    f"Đã gửi thành công {hist_ok_count}/{len(hist_selected_email_emps)} "
                                    "email bảng lương từ bản đang chỉnh sửa."
                                )
                            for err in hist_errors:
                                st.error(err)

                if st.session_state.current_role == "admin":
                    with st.expander("📨 GỬI BẢNG LƯƠNG TỔNG HỢP CHO LỄ TÂN (BẢN ĐANG CHỈNH SỬA)"):
                        hist_letan_df = df_credentials.copy()
                        if not hist_letan_df.empty and 'Phân quyền' in hist_letan_df.columns:
                            hist_letan_df = hist_letan_df[
                                hist_letan_df['Phân quyền'].astype(str).str.strip().str.lower().isin(['letan', 'quanly'])
                            ].copy()
                            if 'Tên nhân viên' in hist_letan_df.columns:
                                hist_letan_df = hist_letan_df[
                                    ~hist_letan_df['Tên nhân viên'].astype(str).apply(normalize_login_name).isin({
                                        'ten nhan vien', 'ten he thong', 'username', 'user name'
                                    })
                                ].copy()

                        if hist_letan_df.empty:
                            st.info("Không có tài khoản Lễ tân trong hồ sơ hệ thống.")
                        else:
                            st.write("**Check đúng 1 Lễ tân để hệ thống lấy Email từ hồ sơ:**")
                            hist_checked_letan = []
                            for i, (_, lr) in enumerate(hist_letan_df.iterrows()):
                                lname = str(lr.get('Tên nhân viên', '')).strip()
                                if not lname:
                                    continue
                                if st.checkbox(
                                    lname,
                                    key=f"payroll_history_letan_check_{batch}_{i}_{normalize_login_name(lname)}"
                                ):
                                    hist_checked_letan.append(lname)

                            if len(hist_checked_letan) > 1:
                                st.warning("⚠️ Chỉ được check 1 Lễ tân cho mỗi lần gửi.")
                            elif len(hist_checked_letan) == 1:
                                hist_selected_letan = hist_checked_letan[0]
                                hist_matched_letan = hist_letan_df[
                                    hist_letan_df['Tên nhân viên'].astype(str).apply(normalize_login_name)
                                    == normalize_login_name(hist_selected_letan)
                                ]
                                if hist_matched_letan.empty:
                                    st.error("Không tìm thấy hồ sơ Lễ tân đã chọn.")
                                else:
                                    hist_rletan = hist_matched_letan.iloc[0]
                                    hist_letan_email = str(hist_rletan.get('Email', '')).strip()
                                    if hist_letan_email and '@' in hist_letan_email:
                                        st.success(f"📧 Email nhận: {hist_letan_email}")
                                    else:
                                        st.warning(
                                            f"⚠️ Tài khoản {hist_selected_letan} chưa có Email hợp lệ trong hồ sơ."
                                        )

                                    if st.button(
                                        "📤 Gửi bảng lương tổng hợp cho Lễ tân đã check",
                                        use_container_width=True,
                                        key=f"send_payroll_history_summary_letan_{batch}",
                                        disabled=not (hist_letan_email and '@' in hist_letan_email)
                                    ):
                                        sender_email = "veraspabienhoa@gmail.com"
                                        sender_pass = "zvtgbysfmdaqxaau"
                                        ok, msg = send_payroll_summary_email(
                                            sender_email, sender_pass, hist_letan_email,
                                            hist_selected_letan, edited_saved_table, hs, he
                                        )
                                        if ok:
                                            st.success(
                                                f"✅ Đã gửi bảng lương tổng hợp của bản {batch} "
                                                f"cho {hist_selected_letan}."
                                            )
                                        else:
                                            st.error(msg)
                            else:
                                st.caption("Chưa chọn Lễ tân nhận bảng lương tổng hợp.")

elif selected_page == "🧭 Bảng Tour":
    st.subheader("🧭 Bảng Tour")

    c_refresh, _ = st.columns([2, 8])
    with c_refresh:
        if st.button("🔄 Làm mới Bảng Tour", use_container_width=True):
            load_bang_tour_input.clear()
            st.rerun()

    df_tour, tour_err = load_bang_tour_input()
    if tour_err:
        st.error(tour_err)
    elif df_tour.empty:
        st.info("Không có dữ liệu trong sheet Input.")
    else:
        # Bảng thống kê dùng dữ liệu GỐC vừa đọc, trước khi làm trống thời gian <= -15.
        tour_stats_df = calculate_bang_tour_stats(df_tour)
        st.markdown("### 📊 Thống kê Bảng Tour")
        def style_tour_stats_row(row):
            if str(row.get("Chỉ số", "")).strip() == "Có thể lên tour":
                return ["background-color:#92D050;color:#000000;font-weight:700;"] * len(row)
            return [""] * len(row)

        tour_stats_styled = (
            tour_stats_df.style
            .apply(style_tour_stats_row, axis=1)
            .set_table_styles([
                {
                    "selector": "th",
                    "props": [
                        ("background-color", "#A1948C"),
                        ("color", "#000000"),
                        ("font-weight", "700"),
                        ("text-align", "center"),
                        ("white-space", "normal"),
                        ("overflow-wrap", "anywhere"),
                        ("word-break", "break-word"),
                        ("line-height", "1.15"),
                    ],
                }
            ])
        )
        st.dataframe(
            tour_stats_styled,
            use_container_width=True,
            hide_index=True,
            height="content"
        )

        # Sau khi lấy dữ liệu: sắp cột + định dạng Thời gian còn lại dạng số nguyên.
        # Giá trị <= -15 được làm trống trên bảng hiển thị.
        df_tour_display = prepare_bang_tour_display(df_tour)
        df_tour_display, _tour_widths = apply_table_layout_df(df_tour_display, "tour_main")

        # Auto-fit toàn bộ chiều cao: hiển thị đủ dòng, bỏ thanh cuộn dọc.
        status_col_display = _find_tour_col(df_tour_display, "Trạng thái")
        remain_col_display = _find_tour_col(df_tour_display, "Thời gian còn lại")
        tour_column_config = table_layout_column_config("tour_main", list(df_tour_display.columns))
        if status_col_display is not None:
            tour_column_config[status_col_display] = st.column_config.TextColumn(
                status_col_display, width=layout_width("tour_main", status_col_display, "medium")
            )
        if remain_col_display is not None:
            tour_column_config[remain_col_display] = st.column_config.TextColumn(
                remain_col_display, width=layout_width("tour_main", remain_col_display, "small")
            )

        st.dataframe(
            apply_table_visual_styler(style_bang_tour(df_tour_display), "tour_main", list(df_tour_display.columns)),
            use_container_width=True,
            hide_index=True,
            height="content",
            row_height=layout_row_height("tour_main"),
            column_config=tour_column_config
        )
        st.caption(
            "Màu dòng: Nghỉ phép = chữ mờ/nền trắng; Đi làm = chữ đen/nền trắng; "
            "≥15 phút = xanh; 0–<15 = vàng; -15–<0 = đỏ; ≤-15 làm trống thời gian; Break = cam."
        )

elif selected_page == "📅 Đăng ký & Thống kê nghỉ phép":
    st.subheader("➕ Đăng ký lịch nghỉ")
    all_users = get_leave_eligible_employee_names(df_credentials, df_nv_excel)
    if st.session_state.current_role == "nhanvien" and system_status["lock_nv"]:
        st.error("🔒 Tính năng đăng ký lịch nghỉ hiện đang bị Admin tạm khóa. Vui lòng liên hệ Admin hoặc Lễ Tân để được hỗ trợ!")
    else:
        if is_admin_letan:
            list_nv_input = ["-- Chọn nhân viên --"] + all_users
            chosen_dates = st.date_input("Chọn ngày nghỉ (Khoảng thời gian nếu là Phép năm):", value=(get_vn_today(), get_vn_today()), key="sb_chosen_date")
        else:
            list_nv_input = [st.session_state.current_user]
            emp_min_date, emp_max_date = employee_registration_window()
            chosen_dates = st.date_input(
                "Chọn ngày nghỉ (Nhân viên chọn 1 ngày):",
                get_vn_today(),
                min_value=emp_min_date,
                max_value=emp_max_date,
                key="sb_chosen_date"
            )
            st.caption(f"Nhân viên được đăng ký từ {emp_min_date.strftime('%d/%m/%Y')} đến hết {emp_max_date.strftime('%d/%m/%Y')}.")

        if isinstance(chosen_dates, tuple):
            if len(chosen_dates) == 2: start_date, end_date = chosen_dates
            elif len(chosen_dates) == 1: start_date = end_date = chosen_dates[0]
            else: start_date = end_date = get_vn_today()
        else:
            start_date = end_date = chosen_dates

        chosen_nv = st.selectbox("Chọn nhân viên:", list_nv_input, key="sb_chosen_nv", filter_mode="contains")

        # --- BỘ LỌC ĐỘNG CHO LÝ DO NGHỈ ---
        list_loai_nghi = []
        loai_nghi_dict = {}
        current_role = st.session_state.current_role.lower()
        role_for_leave_rules = "letan" if current_role == "quanly" else current_role

        if not df_loai_nghi.empty:
            for idx, row in df_loai_nghi.iterrows():
                row_vals = row.tolist()
                l_name = str(row_vals[1]).strip() if len(row_vals) > 1 else ""
                if not l_name or l_name.lower() in ["nan", "none"]:
                    l_name = str(row.get('Lý do nghỉ', row.get('Loại nghỉ', ''))).strip()

                if l_name and l_name.lower() not in ["nan", "loại nghỉ", "lý do nghỉ", "none", ""]:
                    dk_ngay = str(row_vals[6]).strip().lower() if len(row_vals) > 6 else ""
                    dk_role = str(row_vals[7]).strip().lower() if len(row_vals) > 7 else ""

                    role_allowed = True
                    if dk_role and dk_role not in ["nan", "none", "tất cả", "all", ""]:
                        if role_for_leave_rules not in dk_role: role_allowed = False

                    day_allowed = True
                    if dk_ngay and dk_ngay not in ["nan", "none", "tất cả", "all", ""]:
                        wd = start_date.weekday()
                        wd_map = {
                            0: ["hai", "t2"], 1: ["ba", "t3"], 2: ["tư", "tu", "t4"],
                            3: ["năm", "nam", "t5"], 4: ["sáu", "sau", "t6"],
                            5: ["bảy", "bẩy", "t7", "cuối tuần"], 6: ["chủ nhật", "chu nhat", "cn", "cuối tuần"]
                        }
                        day_allowed = any(k in dk_ngay for k in wd_map[wd])

                    if day_allowed and role_allowed:
                        if "không phép" in l_name.lower(): l_name = f"🔴 {l_name}"
                        list_loai_nghi.append(l_name)
                        try:
                            s_ngay_str = str(row_vals[4]).replace(',', '').strip() if len(row_vals) > 4 else ""
                            s_ngay = float(s_ngay_str) if s_ngay_str != "" else 0.0
                        except: s_ngay = 0.0

                        try:
                            p_str = str(row_vals[5] if len(row_vals)>5 else "0").replace('.', '').replace(',', '').replace(' ', '').replace('đ', '').replace('VNĐ', '').replace('VND', '')
                            p_val = 0.0 if p_str.lower() in ["", "-", "nan", "none"] else float(p_str)
                        except: p_val = 0.0

                        loai_nghi_dict[l_name.lower()] = [s_ngay, p_val]

        if not list_loai_nghi:
            list_loai_nghi = ["Nghỉ phép", "🔴 Nghỉ không phép", "Nghỉ phát sinh", "🔴 Đi trễ không phép", "🔴 Về sớm không phép"]
            loai_nghi_dict = {l.lower(): [0.0, 0.0] for l in list_loai_nghi}

        chosen_loai = st.selectbox("Lý do nghỉ:", ["-- Chọn lý do nghỉ --"] + list_loai_nghi, key="sb_loai_nghi_live", filter_mode="contains")

        default_songay = 0.0
        default_phat = 0.0
        if chosen_loai and chosen_loai != "-- Chọn lý do nghỉ --" and chosen_loai.lower() in loai_nghi_dict:
            default_songay = loai_nghi_dict[chosen_loai.lower()][0]
            default_phat = loai_nghi_dict[chosen_loai.lower()][1]

        is_loi_vi_pham = "lỗi vi phạm khác" in chosen_loai.lower() if chosen_loai else False
        is_nghi_ly_do_khac = "nghỉ lý do khác" in chosen_loai.lower() if chosen_loai else False
        if is_loi_vi_pham: default_songay = 0.0

        # --- CẢNH BÁO SỚM SỐ NGƯỜI NGHỈ ---
        early_warning = ""
        norm_loai_temp = chosen_loai.strip().lower() if chosen_loai else ""
        if chosen_loai and chosen_loai != "-- Chọn lý do nghỉ --":
            num_days_temp = (end_date - start_date).days + 1
            if num_days_temp > 1 and "phép năm" not in norm_loai_temp:
                early_warning = "❌ Chọn Khoảng thời gian nhiều ngày chỉ áp dụng cho 'Nghỉ Phép năm'."
            elif not is_nghi_ly_do_khac and default_phat <= 0 and "phép năm" not in norm_loai_temp and not is_loi_vi_pham:
                for i in range(num_days_temp):
                    chk_d = start_date + timedelta(days=i)
                    chk_is_we = chk_d.weekday() >= 5
                    if norm_loai_temp == "nghỉ phát sinh":
                        # Cảnh báo NGAY khi vừa chọn Nghỉ phát sinh, chưa cần bấm Lưu.
                        current_hour = datetime.now(VN_TZ).hour
                        if current_hour < 9 or current_hour >= 17:
                            early_warning = "❌ Khung giờ đăng ký 'Nghỉ phát sinh' chỉ cho phép từ 09:00 đến 17:00!"
                            break
                        if chk_is_we:
                            early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} là cuối tuần, không được phép 'Nghỉ phát sinh'!"
                            break
                        c_ps = len(df_lich[(df_lich['Ngày'] == chk_d) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == "nghỉ phát sinh")]) if not df_lich.empty else 0
                        if c_ps >= 2:
                            early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} đã đạt giới hạn 2 người 'Nghỉ phát sinh'!"
                            break
                        # Đồng thời kiểm tra hạn mức tổng số người nghỉ trong ngày.
                        m_ppl = 5 if not chk_is_we else 3
                        c_nghi = len(df_lich[(df_lich['Ngày'] == chk_d) & (df_lich['Số ngày tính'] > 0)]) if not df_lich.empty else 0
                        if c_nghi >= m_ppl:
                            early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} đã đủ hạn mức {m_ppl} người nghỉ trong ngày!"
                            break
                    else:
                        m_ppl = 5 if not chk_is_we else 3
                        c_nghi = len(df_lich[(df_lich['Ngày'] == chk_d) & (df_lich['Số ngày tính'] > 0)]) if not df_lich.empty else 0
                        if c_nghi >= m_ppl:
                            early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} đã đạt giới hạn {m_ppl} người nghỉ chung/ngày."
                            break

        if early_warning: st.error(early_warning)

        # Kiểm tra lịch đã có từ CẢ HAI nguồn để chặn đăng ký trùng ngay trên giao diện.
        registration_all_df = combine_leave_sources_for_daily_stats(df_lich, df_leave_secondary, df_backup)
        existing_today = []
        if not registration_all_df.empty and chosen_nv != "-- Chọn nhân viên --":
            ex_df = registration_all_df[
                (registration_all_df['Tên nhân viên'].astype(str).apply(normalize_login_name) == normalize_login_name(chosen_nv)) &
                (registration_all_df['Ngày'] == start_date)
            ]
            existing_today = ex_df['Lý do nghỉ'].astype(str).str.strip().tolist()

        dyn_key_suffix = f"{chosen_loai}_{start_date}_{chosen_nv}"

        # Hiển thị trước thứ tự và mức cộng phạt cho 3 nhóm vi phạm lũy tiến.
        progressive_preview_reason = get_progressive_penalty_reason(chosen_loai)
        if progressive_preview_reason:
            preview_ordinal, preview_extra = _progressive_ordinal_and_bonus(
                registration_all_df, start_date, chosen_loai
            )
            preview_total = float(default_phat) + float(preview_extra)
            st.warning(
                f"⚠️ {progressive_preview_reason} ngày {start_date.strftime('%d/%m/%Y')}: Người Thứ {preview_ordinal}. "
                f"Phạt theo quy định {float(default_phat):,.0f} VNĐ"
                + (f" + lũy tiến {preview_extra:,.0f} VNĐ" if preview_extra > 0 else "")
                + f" = {preview_total:,.0f} VNĐ."
            )

        with st.form("form_nhap_lich_inner"):
            txt_chitiet_label = "Chi tiết vi phạm / Ghi chú (🔴 **Bắt buộc**):" if (is_loi_vi_pham or is_nghi_ly_do_khac) else "Chi tiết vi phạm / Ghi chú (nếu có):"
            input_chitiet = st.text_input(txt_chitiet_label).strip()

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                val_songay = st.number_input("Số ngày tính:", value=float(default_songay), step=0.5, key=f"num_songay_{dyn_key_suffix}", disabled=is_loi_vi_pham)

            # HIỂN THỊ Ô MỨC PHẠT CHO TẤT CẢ TÀI KHOẢN (ĐÃ MỞ LẠI)
            with col_p2:
                txt_phat_label = "Mức phạt vi phạm VNĐ (🔴 **Bắt buộc**):" if is_loi_vi_pham else "Mức phạt vi phạm (VNĐ):"
                val_phat = st.number_input(
                    txt_phat_label,
                    value=float(default_phat),
                    step=50000.0,
                    key=f"num_phat_{dyn_key_suffix}",
                    disabled=is_progressive_penalty_reason(chosen_loai)
                )

            confirm_multiple = True
            if existing_today:
                normalized_existing = {normalize_leave_reason(x) for x in existing_today}
                if normalize_leave_reason(chosen_loai) in normalized_existing:
                    st.error(f"❌ Nhân viên này đã có Lý do nghỉ: '{chosen_loai.replace('🔴 ', '')}' vào ngày này rồi. KHÔNG THỂ trùng cùng 1 loại nghỉ!")
                    confirm_multiple = False
                else:
                    st.warning(f"⚠️ CẢNH BÁO: Nhân viên '{chosen_nv}' đã có các lịch sau trong ngày {start_date.strftime('%d/%m/%Y')}: {', '.join(existing_today)}")
                    confirm_multiple = st.checkbox("Tôi xác nhận đăng ký này là ĐÚNG và MỚI.")

            submit_lich = st.form_submit_button("💾 Xác Nhận Ghi Lịch Nghỉ")

            if submit_lich:
                today = get_vn_today()
                can_proceed = True

                if current_role == "nhanvien":
                    emp_min_date, emp_max_date = employee_registration_window(today)
                    if start_date < emp_min_date or end_date > emp_max_date:
                        st.error(f"❌ Tài khoản NHÂN VIÊN chỉ được đăng ký từ hôm nay đến hết ngày {emp_max_date.strftime('%d/%m/%Y')} (tháng hiện tại và 1 tháng kế tiếp).")
                        can_proceed = False
                elif current_role in ["letan", "quanly"] and start_date < today:
                    st.error("❌ Lỗi: Tài khoản LỄ TÂN/QUẢN LÝ không được đăng ký lịch trong **QUÁ KHỨ**. Muốn sửa lịch cũ, vui lòng liên hệ Admin.")
                    can_proceed = False

                if can_proceed:
                    if not confirm_multiple:
                        st.error("❌ Vui lòng tick Xác nhận cảnh báo bên trên trước khi lưu.")
                    elif chosen_nv == "-- Chọn nhân viên --" or not chosen_nv:
                        st.error("❌ Vui lòng chọn nhân viên cần nhập lịch nghỉ!")
                    elif chosen_loai == "-- Chọn lý do nghỉ --" or not chosen_loai:
                        st.error("❌ Vui lòng chọn lý do nghỉ!")
                    elif early_warning:
                        st.error(f"❌ Không thể lưu: {early_warning}")
                    else:
                        norm_loai = chosen_loai.strip().lower().replace("🔴 ", "")
                        num_days_selected = (end_date - start_date).days + 1

                        if is_loi_vi_pham:
                            val_songay = 0.0 
                            if not input_chitiet:
                                st.error("❌ Bắt buộc nhập Chi tiết vi phạm / Ghi chú đối với 'Lỗi vi phạm khác'.")
                                can_proceed = False
                            if val_phat <= 0 and st.session_state.current_role == "admin":
                                st.error("❌ Bắt buộc nhập số tiền Phạt vi phạm > 0 đối với 'Lỗi vi phạm khác'.")
                                can_proceed = False

                        if is_nghi_ly_do_khac and not input_chitiet:
                            st.error("❌ Bắt buộc nhập Chi tiết vi phạm / Ghi chú đối với 'Nghỉ lý do khác'.")
                            can_proceed = False

                        # KIỂM TRA GIỚI HẠN NHÂN SỰ CÁ NHÂN
                        if can_proceed:
                            nv_info = df_credentials[df_credentials['Tên nhân viên'].str.lower() == chosen_nv.lower()]
                            limit_ps = pd.to_numeric(nv_info.iloc[0].get('Phát sinh tháng', 0), errors='coerce') if not nv_info.empty else 0
                            limit_cp = pd.to_numeric(nv_info.iloc[0].get('Có phép tháng', 0), errors='coerce') if not nv_info.empty else 0
                            limit_pn = pd.to_numeric(nv_info.iloc[0].get('Phép năm', 0), errors='coerce') if not nv_info.empty else 0

                            if pd.isna(limit_ps): limit_ps = 0
                            if pd.isna(limit_cp): limit_cp = 0
                            if pd.isna(limit_pn): limit_pn = 0

                            user_hist = df_lich[df_lich['Tên nhân viên'] == chosen_nv] if not df_lich.empty else pd.DataFrame(columns=['Ngày', 'Lý do nghỉ', 'Số ngày tính'])
                            user_hist['Ngày_DT'] = pd.to_datetime(user_hist['Ngày'], errors='coerce')
                            user_hist['M'] = user_hist['Ngày_DT'].dt.month
                            user_hist['Y'] = user_hist['Ngày_DT'].dt.year

                            curr_m = start_date.month
                            curr_y = start_date.year

                            total_phep_required = val_songay * num_days_selected
                            accumulated_month = user_hist[(user_hist['M'] == curr_m) & (user_hist['Y'] == curr_y)]['Số ngày tính'].sum()

                            if "phép năm" in norm_loai:
                                used_pn = user_hist[(user_hist['Y'] == curr_y) & (user_hist['Lý do nghỉ'].str.lower().str.contains("phép năm", na=False))]['Số ngày tính'].sum()
                                if limit_pn > 0 and (used_pn + total_phep_required > limit_pn):
                                    st.error(f"❌ Vượt quá số ngày Phép năm! Bạn cần {total_phep_required} ngày nhưng quỹ phép chỉ còn {limit_pn - used_pn} ngày trong năm {curr_y}.")
                                    can_proceed = False

                            elif "phát sinh" in norm_loai:
                                used_ps = len(user_hist[(user_hist['M'] == curr_m) & (user_hist['Y'] == curr_y) & (user_hist['Lý do nghỉ'].str.lower().str.contains("phát sinh", na=False))])
                                if limit_ps > 0 and (used_ps >= limit_ps):
                                    st.error(f"❌ Vượt giới hạn Phát sinh! Nhân viên này chỉ được đăng ký {limit_ps} lần phát sinh/tháng.")
                                    can_proceed = False

                            elif not is_nghi_ly_do_khac and "không phép" not in norm_loai and val_songay > 0:
                                used_cp = user_hist[(user_hist['M'] == curr_m) & (user_hist['Y'] == curr_y) & (~user_hist['Lý do nghỉ'].str.lower().str.contains("không phép|phát sinh|lý do khác", na=False, regex=True))]['Số ngày tính'].sum()
                                if limit_cp > 0 and (used_cp + total_phep_required > limit_cp):
                                    st.error(f"❌ Vượt số ngày Có phép trong tháng! Nhân viên này chỉ được nghỉ tối đa {limit_cp} ngày/tháng.")
                                    can_proceed = False

                            if can_proceed:
                                all_saved = True
                                save_success_notes = []
                                for i in range(num_days_selected):
                                    curr_date_iter = start_date + timedelta(days=i)
                                    is_weekend_iter = curr_date_iter.weekday() >= 5

                                    if val_songay is not None: accumulated_month += val_songay
                                    else: val_songay = 0.0

                                    # Không cho cùng một nhân viên đăng ký cùng một Loại nghỉ hai lần trong cùng ngày.
                                    latest_registration_df = combine_leave_sources_for_daily_stats(df_lich, df_leave_secondary, df_backup)
                                    if _leave_exists_in_sources(latest_registration_df, curr_date_iter, chosen_nv, chosen_loai):
                                        st.error(
                                            f"❌ {chosen_nv} đã có '{chosen_loai.replace('🔴 ', '')}' ngày "
                                            f"{curr_date_iter.strftime('%d/%m/%Y')}. Không thể đăng ký trùng."
                                        )
                                        all_saved = False
                                        break

                                    if not is_nghi_ly_do_khac and val_phat <= 0 and "phép năm" not in norm_loai and not is_loi_vi_pham:
                                        if norm_loai == "nghỉ phát sinh":
                                            current_hour = datetime.now(VN_TZ).hour
                                            if current_hour < 9 or current_hour >= 17:
                                                st.error("❌ Khung giờ đăng ký 'Nghỉ phát sinh' chỉ cho phép từ 09:00 đến 17:00!")
                                                all_saved = False
                                                break
                                            elif is_weekend_iter:
                                                st.error(f"❌ Ngày {curr_date_iter.strftime('%d/%m/%Y')} là cuối tuần, không được phép 'Nghỉ phát sinh'!")
                                                all_saved = False
                                                break
                                            else:
                                                count_ps = len(df_lich[(df_lich['Ngày'] == curr_date_iter) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == "nghỉ phát sinh")]) if not df_lich.empty else 0
                                                if count_ps >= 2:
                                                    st.error(f"❌ Ngày {curr_date_iter.strftime('%d/%m/%Y')} đã đạt giới hạn 2 người 'Nghỉ phát sinh'!")
                                                    all_saved = False
                                                    break
                                        else:
                                            max_people = 5 if not is_weekend_iter else 3
                                            today_total_nghi = len(df_lich[(df_lich['Ngày'] == curr_date_iter) & (df_lich['Số ngày tính'] > 0)]) if not df_lich.empty else 0
                                            if today_total_nghi >= max_people:
                                                st.error(f"❌ Ngày {curr_date_iter.strftime('%d/%m/%Y')} đã đạt giới hạn {max_people} người nghỉ chung/ngày.")
                                                all_saved = False
                                                break

                                    # GỌI HÀM LƯU LÊN GOOGLE SHEETS
                                    penalty_to_save = float(default_phat) if is_progressive_penalty_reason(chosen_loai) else float(val_phat)
                                    success_bk, msg_bk = save_lich_nghi_to_backup_sheet(
                                        curr_date_iter.strftime('%d/%m/%Y'), chosen_nv, chosen_loai.replace("🔴 ", ""),
                                        input_chitiet, val_songay, accumulated_month, penalty_to_save, st.session_state.current_user,
                                        df_main_source=df_lich
                                    )

                                    if not success_bk:
                                        st.error(f"❌ LỖI GOOGLE SHEETS: {msg_bk}")
                                        all_saved = False
                                        break
                                    if msg_bk:
                                        save_success_notes.append(msg_bk)

                                # CHỈ IN THÀNH CÔNG NẾU API THỰC SỰ TRẢ VỀ SUCCESS
                                if all_saved:
                                    st.success(f"✅ Đã ghi nhận lịch nghỉ thành công cho {num_days_selected} ngày!")
                                    for note in save_success_notes:
                                        if "Người Thứ" in note:
                                            st.info(note)
                                    _clear_dynamic_data_caches()



    st.markdown("---")
    st.markdown("## 📊 Thống kê nghỉ phép")

    # Bộ lọc thời gian & nhân viên
    col_date, col_name, col_refresh = st.columns([5, 4, 2])

    with col_date:
        today = get_vn_today() 
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            filter_type = st.selectbox(
                "Lọc thời gian:", 
                ["Hôm nay", "Hôm qua", "Ngày mai", "Chọn ngày", "Khoảng thời gian", "Tuần này", "Tuần trước", "Tuần sau", "Tháng này", "Tháng sau"],
                filter_mode="contains"
            )
        with col_d2:
            if filter_type == "Hôm nay": start_date = end_date = today
            elif filter_type == "Hôm qua": start_date = end_date = today - timedelta(days=1)
            elif filter_type == "Ngày mai": start_date = end_date = today + timedelta(days=1)
            elif filter_type == "Tuần này":
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif filter_type == "Tuần trước":
                start_date = today - timedelta(days=today.weekday() + 7)
                end_date = start_date + timedelta(days=6)
            elif filter_type == "Tuần sau":
                start_date = today - timedelta(days=today.weekday()) + timedelta(days=7)
                end_date = start_date + timedelta(days=6)
            elif filter_type == "Tháng này":
                start_date = today.replace(day=1)
                end_date = today.replace(day=calendar.monthrange(today.year, today.month)[1])
            elif filter_type == "Tháng trước":
                end_date = today.replace(day=1) - timedelta(days=1)
                start_date = end_date.replace(day=1)
            elif filter_type == "Tháng sau":
                start_date = today.replace(year=today.year + 1, month=1, day=1) if today.month == 12 else today.replace(month=today.month + 1, day=1)
                end_date = start_date.replace(day=calendar.monthrange(start_date.year, start_date.month)[1])
            elif filter_type == "Chọn ngày":
                start_date = end_date = st.date_input("Chọn ngày:", today)
            elif filter_type == "Khoảng thời gian":
                date_range = st.date_input("Chọn khoảng thời gian:", [today, today])
                start_date, end_date = (date_range[0], date_range[1]) if len(date_range) == 2 else (date_range[0], date_range[0])
            else: start_date = end_date = today

    with col_name:
        list_nv = ["- Tất cả nhân viên -"] + get_leave_eligible_employee_names(df_credentials, df_nv_excel)
        selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", list_nv, filter_mode="contains")

    with col_refresh:
        st.write("") 
        if st.button("🔄 Cập Nhật Dữ Liệu", use_container_width=True):
            _clear_dynamic_data_caches()
            st.rerun()

    # Lọc dữ liệu: phần thống kê/Chi tiết danh sách dùng ĐÚNG 2 Google Sheet:
    # 1) SHEET_DU_PHONG_ID (nơi nhập liệu hiện tại)
    # 2) SHEET_LICH_NGHI_2_ID
    # Nếu trùng Ngày + Tên nhân viên + Lý do nghỉ thì ưu tiên Sheet dự phòng.
    detail_all_df = combine_leave_sources_for_daily_stats(df_leave_secondary, df_backup)
    if not detail_all_df.empty:
        mask_date = (detail_all_df['Ngày'] >= start_date) & (detail_all_df['Ngày'] <= end_date)
        filtered_df = detail_all_df[mask_date].copy()
        if selected_nv != "- Tất cả nhân viên -":
            filtered_df = filtered_df[
                filtered_df['Tên nhân viên'].astype(str).str.strip().str.casefold() == selected_nv.strip().casefold()
            ]
    else:
        filtered_df = detail_all_df.copy()

    # --- THỐNG KÊ ---
    excluded_keywords = ["đi trễ", "di tre", "không dọn vệ sinh", "khong don ve sinh", "lỗi vi phạm", "loi vi pham", "qua tour", "xuống phòng", "xuong phong", "ra sớm", "ra som", "vào muộn", "vao muon", "đi tua", "di tua", "ngưng nhận", "ngung nhan", "hỗ trợ ca", "ho tro ca"]
    def is_excluded(r): return any(kw in str(r).lower() for kw in excluded_keywords)

    # Nguồn riêng cho "Thống kê chi tiết theo từng ngày": hợp nhất CẢ 2 FILE.
    daily_all_df = combine_leave_sources_for_daily_stats(df_leave_secondary, df_backup)
    if not daily_all_df.empty:
        daily_mask = (daily_all_df['Ngày'] >= start_date) & (daily_all_df['Ngày'] <= end_date)
        daily_filtered_df = daily_all_df[daily_mask].copy()
        if selected_nv != "- Tất cả nhân viên -":
            daily_filtered_df = daily_filtered_df[
                daily_filtered_df['Tên nhân viên'].astype(str).str.strip().str.casefold() == selected_nv.strip().casefold()
            ]
    else:
        daily_filtered_df = daily_all_df.copy()

    daily_thuc_nghi = (
        daily_filtered_df[~daily_filtered_df['Lý do nghỉ'].apply(is_excluded)].copy()
        if not daily_filtered_df.empty else pd.DataFrame(columns=daily_all_df.columns)
    )

    if filtered_df.empty:
        df_thuc_nghi = phat_sinh_df = khong_phep_df = co_phep_df = pd.DataFrame(columns=filtered_df.columns if hasattr(filtered_df, 'columns') else [])
        tong_phat = 0.0
    else:
        df_thuc_nghi = filtered_df[~filtered_df['Lý do nghỉ'].apply(is_excluded)].copy()
        if df_thuc_nghi.empty: phat_sinh_df = khong_phep_df = co_phep_df = pd.DataFrame(columns=filtered_df.columns)
        else:
            ly_do_lower = df_thuc_nghi['Lý do nghỉ'].astype(str).str.strip().str.lower()
            phat_sinh_df = df_thuc_nghi[ly_do_lower == 'nghỉ phát sinh']
            khong_phep_df = df_thuc_nghi[ly_do_lower.str.contains('không phép', na=False)]
            co_phep_df = df_thuc_nghi[(ly_do_lower != 'nghỉ phát sinh') & (~ly_do_lower.str.contains('không phép', na=False))]
        tong_phat = filtered_df['Phạt vi phạm'].sum()

    st.write("") 
    if st.session_state.current_role == "admin":
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tổng số người nghỉ", len(df_thuc_nghi))
        c2.metric("✅ CÓ phép", len(co_phep_df))
        c3.metric("⚠️ PHÁT SINH", len(phat_sinh_df))
        c4.metric("❌ KHÔNG phép", len(khong_phep_df))
        c5.metric("💰 Tổng tiền phạt", f"{tong_phat:,.0f} đ".replace(",", "."))
        cols_to_hide = []
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tổng số người nghỉ", len(df_thuc_nghi))
        c2.metric("✅ CÓ phép", len(co_phep_df))
        c3.metric("⚠️ PHÁT SINH", len(phat_sinh_df))
        c4.metric("❌ KHÔNG phép", len(khong_phep_df))
        cols_to_hide = ['Phạt vi phạm']

    st.markdown("### 📅 Thống kê chi tiết theo từng ngày")
    st.caption("Phần này hợp nhất dữ liệu từ 2 Google Sheet lịch nghỉ và loại trùng trước khi thống kê.")
    if not daily_thuc_nghi.empty:
        daily_stats = []
        daily_limit_flags = []
        for d in sorted(daily_filtered_df['Ngày'].dropna().unique()):
            day_df = daily_filtered_df[daily_filtered_df['Ngày'] == d]
            day_thuc_nghi = day_df[~day_df['Lý do nghỉ'].apply(is_excluded)]
            d_loai = day_thuc_nghi['Lý do nghỉ'].astype(str).str.strip().str.lower()

            count_co_phep = len(day_thuc_nghi[(d_loai != 'nghỉ phát sinh') & (~d_loai.str.contains('không phép', na=False))])
            count_phat_sinh = len(day_thuc_nghi[d_loai == 'nghỉ phát sinh'])
            count_khong_phep = len(day_thuc_nghi[d_loai.str.contains('không phép', na=False)])
            is_weekend = d.weekday() >= 5
            max_people = 3 if is_weekend else 5
            total_count_for_limit = len(day_df[pd.to_numeric(day_df['Số ngày tính'], errors='coerce').fillna(0) > 0])

            stat_row = {
                "Ngày": d.strftime('%d/%m/%Y'),
                "Tổng số người nghỉ": len(day_thuc_nghi),
                "✅ CÓ phép": count_co_phep,
                "⚠️ PHÁT SINH": count_phat_sinh,
                "❌ KHÔNG phép": count_khong_phep
            }
            if st.session_state.current_role == "admin":
                stat_row["💰 Tổng tiền phạt"] = f"{pd.to_numeric(day_df['Phạt vi phạm'], errors='coerce').fillna(0).sum():,.0f} đ".replace(",", ".")

            daily_stats.append(stat_row)
            daily_limit_flags.append({
                'co_phep_full': total_count_for_limit >= max_people,
                'phat_sinh_full': (count_phat_sinh >= 2) or (is_weekend and count_phat_sinh > 0)
            })

        daily_stats_df = pd.DataFrame(daily_stats)

        def highlight_daily_limits(row):
            styles = [''] * len(row)
            flags = daily_limit_flags[row.name]
            for idx, col in enumerate(row.index):
                if col == '✅ CÓ phép' and flags['co_phep_full']:
                    styles[idx] = 'background-color: #ffcdd2; color: #b71c1c; font-weight: 700;'
                elif col == '⚠️ PHÁT SINH' and flags['phat_sinh_full']:
                    styles[idx] = 'background-color: #ffcdd2; color: #b71c1c; font-weight: 700;'
            return styles

        st.dataframe(
            daily_stats_df.style.apply(highlight_daily_limits, axis=1),
            width="stretch",
            height="content",
            hide_index=True
        )
    else:
        st.info("Không có dữ liệu báo nghỉ trong khoảng thời gian đã chọn ở cả hai nguồn.")

    st.markdown("---")

    export_df = format_display_df(filtered_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
    df_for_excel = export_df.copy()
    if st.session_state.current_role == "admin" and not df_for_excel.empty:
        tong_cong_row = pd.Series(index=df_for_excel.columns, dtype=object)
        tong_cong_row['Tên nhân viên'] = "TỔNG TIỀN PHẠT:"
        tong_cong_row['Phạt vi phạm'] = tong_phat
        df_for_excel = pd.concat([df_for_excel, tong_cong_row.to_frame().T], ignore_index=True)

    if st.session_state.current_role == "nhanvien":
        st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")
    else:
        col_header, col_download = st.columns([7, 3])
        with col_header:
            st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")
        with col_download:
            st.write("")
            if not export_df.empty:
                st.download_button(
                    "📥 Tải Dữ Liệu Lọc Xuống (Excel)",
                    data=to_excel(df_for_excel),
                    file_name=f"LichNghi_{start_date.strftime('%d%m%Y')}_to_{end_date.strftime('%d%m%Y')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            else:
                st.button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", disabled=True, use_container_width=True)

    # --- KHU VỰC CHỈ DÀNH CHO ADMIN: GỬI EMAIL BÁO CÁO ---
    if st.session_state.current_role == "admin" and not filtered_df.empty:
        with st.expander("📧 GỬI BÁO CÁO QUA EMAIL CHO NHÂN VIÊN"):
            st.info("Hệ thống sẽ tự động tách dữ liệu của từng nhân viên và gửi đến đúng Email của họ. Bạn có thể chọn gửi cho 1 người, nhiều người hoặc tất cả.")

            unique_employees_in_filter = filtered_df['Tên nhân viên'].dropna().unique().tolist()

            with st.form("form_send_email"):
                # Thêm multiselect cho phép chọn người nhận
                selected_to_send = st.multiselect(
                    "Chọn nhân viên nhận báo cáo:", 
                    options=unique_employees_in_filter, 
                    default=unique_employees_in_filter,
                    filter_mode="contains",
                    help="Có thể xóa bớt hoặc chọn lại. Mặc định là gửi cho tất cả những người có trong danh sách lọc bên trên."
                )

                # Đã lưu cứng thông tin Email và Mật khẩu ứng dụng vào code
                sender_email = "veraspabienhoa@gmail.com"
                sender_pass = "zvtgbysfmdaqxaau" # Đã bỏ khoảng trắng

                st.write(f"📧 **Email gửi đi mặc định:** `{sender_email}`")

                if st.form_submit_button("🚀 Xác Nhận Gửi Email"):
                    if not sender_email or not sender_pass:
                        st.error("❌ Vui lòng nhập đầy đủ Email và Mật khẩu ứng dụng!")
                    elif not selected_to_send:
                        st.warning("⚠️ Vui lòng chọn ít nhất 1 nhân viên để gửi!")
                    else:
                        success_count = 0
                        error_messages = []

                        progress_bar = st.progress(0)

                        for i, emp in enumerate(selected_to_send):
                            df_emp = filtered_df[filtered_df['Tên nhân viên'] == emp]
                            total_phat = df_emp['Phạt vi phạm'].sum()

                            emp_row = df_credentials[df_credentials['Tên nhân viên'].str.lower() == str(emp).strip().lower()]
                            emp_email = str(emp_row.iloc[0].get('Email', '')).strip() if not emp_row.empty else ""

                            if not emp_email or "@" not in emp_email:
                                error_messages.append(f"⚠️ Bỏ qua {emp}: Không có Email hợp lệ.")
                            else:
                                res, msg = send_email_report(
                                    sender_email, sender_pass, emp_email, emp, df_emp, 
                                    total_phat, start_date.strftime('%d/%m/%Y'), end_date.strftime('%d/%m/%Y')
                                )
                                if res:
                                    success_count += 1
                                else:
                                    error_messages.append(f"❌ Lỗi gửi {emp}: {msg}")

                            progress_bar.progress((i + 1) / len(selected_to_send))
                            time.sleep(0.5) # Chờ nửa giây để tránh bị Google chặn Spam

                        if success_count > 0:
                            st.success(f"✅ Đã gửi thành công {success_count} email báo cáo!")
                        if error_messages:
                            for err in error_messages:
                                st.error(err)

    # ĐỊNH DẠNG MÀU ĐỎ KHÔNG PHÉP CHO DATAFRAME
    def highlight_khong_phep(val):
        if isinstance(val, str) and "không phép" in val.lower():
            return 'color: red; font-weight: bold;'
        return ''

    tab1, tab2, tab3, tab4 = st.tabs(["Tất cả danh sách", "Danh sách Nghỉ CÓ phép", "Danh sách Nghỉ PHÁT SINH", "Danh sách Nghỉ KHÔNG phép"])

    with tab1:
        if export_df.empty:
            st.info("Trống.")
        elif st.session_state.current_role in ["admin", "letan", "quanly"]:
            # Admin/Lễ tân/Quản lý: checkbox chọn 1 hoặc nhiều dòng và sửa trực tiếp tại bảng.
            raw_detail_full = filtered_df.copy().reset_index(drop=True)
            raw_detail = raw_detail_full.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore').copy()
            if 'Lý do nghỉ' in raw_detail.columns:
                raw_detail['Lý do nghỉ'] = raw_detail['Lý do nghỉ'].astype(str).str.replace('🔴 ', '', regex=False).str.strip()

            # Danh mục Lý do nghỉ dùng trực tiếp trong bảng sửa.
            reason_options = get_leave_reason_options(
                globals().get('df_loai_nghi', pd.DataFrame()),
                raw_detail['Lý do nghỉ'].tolist() if 'Lý do nghỉ' in raw_detail.columns else []
            )

            # Seed riêng cho data_editor để khi đổi Lý do nghỉ, các cột phụ thuộc được
            # tự tính và hiển thị lại ngay ở lần rerun kế tiếp.
            fingerprint_parts = []
            for _, _r in raw_detail_full.iterrows():
                fingerprint_parts.append(
                    f"{_r.get('__source_sheet_id','')}|{_r.get('__source_row','')}|{schedule_key(_r)}|"
                    f"{_r.get('Ngày cập nhật','')}|{_r.get('Giờ cập nhật','')}"
                )
            detail_fp = "||".join(fingerprint_parts)
            if st.session_state.get('_detail_editor_fingerprint') != detail_fp:
                seed_df = raw_detail.copy()
                seed_df.insert(0, "Chọn", False)
                st.session_state['_detail_editor_seed'] = seed_df
                st.session_state['_detail_editor_fingerprint'] = detail_fp
                st.session_state['_detail_editor_version'] = int(st.session_state.get('_detail_editor_version', 0)) + 1

            editor_df = st.session_state.get('_detail_editor_seed', raw_detail.copy()).copy()
            if 'Chọn' not in editor_df.columns:
                editor_df.insert(0, "Chọn", False)
            editor_df, _ = apply_table_layout_df(editor_df, "leave_detail")

            # Các cột này chỉ do hệ thống tính, không cho nhập tay để tránh sai dữ liệu.
            derived_cols = [
                "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
                "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
            ]
            disabled_cols = [c for c in derived_cols if c in editor_df.columns]

            editor_version = int(st.session_state.get('_detail_editor_version', 1))
            editor_key = f"detail_schedule_editor_v{editor_version}"
            detail_col_config = table_layout_column_config("leave_detail", list(editor_df.columns))
            if "Chọn" in editor_df.columns:
                detail_col_config["Chọn"] = st.column_config.CheckboxColumn(
                    "Chọn", help="Tick 1 hoặc nhiều dòng để sửa/xóa",
                    default=False, width=layout_width("leave_detail", "Chọn", "small")
                )
            if "Ngày" in editor_df.columns:
                detail_col_config["Ngày"] = st.column_config.DateColumn(
                    "Ngày", format="DD/MM/YYYY", width=layout_width("leave_detail", "Ngày", "small")
                )
            if "Lý do nghỉ" in editor_df.columns:
                detail_col_config["Lý do nghỉ"] = st.column_config.SelectboxColumn(
                    "Lý do nghỉ", options=reason_options, required=True,
                    width=layout_width("leave_detail", "Lý do nghỉ", "medium"),
                    help="Bấm vào ô để chọn Lý do nghỉ. Danh sách được tải tự động từ sheet LoaiNghi."
                )
            if "Số ngày tính" in editor_df.columns:
                detail_col_config["Số ngày tính"] = st.column_config.NumberColumn(
                    "Số ngày tính", step=0.5, format="%.1f", disabled=True,
                    width=layout_width("leave_detail", "Số ngày tính", "small")
                )
            if "Số ngày phép cộng dồn" in editor_df.columns:
                detail_col_config["Số ngày phép cộng dồn"] = st.column_config.NumberColumn(
                    "Số ngày phép cộng dồn", step=0.5, format="%.1f", disabled=True,
                    width=layout_width("leave_detail", "Số ngày phép cộng dồn", "small")
                )
            if "Phạt vi phạm" in editor_df.columns:
                detail_col_config["Phạt vi phạm"] = st.column_config.NumberColumn(
                    "Phạt vi phạm", step=50000, format="%.0f", disabled=True,
                    width=layout_width("leave_detail", "Phạt vi phạm", "small")
                )
            for _c in ["Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"]:
                if _c in editor_df.columns:
                    detail_col_config[_c] = st.column_config.TextColumn(
                        _c, disabled=True, width=layout_width("leave_detail", _c, "small")
                    )
            detail_editor = st.data_editor(
                editor_df,
                width="stretch",
                height="content",
                hide_index=True,
                row_height=layout_row_height("leave_detail"),
                num_rows="fixed",
                disabled=disabled_cols,
                column_config=detail_col_config,
                key=editor_key
            )

            # Khi Lý do nghỉ / Ngày / Nhân viên thay đổi, tự động tính lại ngay các cột phụ thuộc.
            editor_event = st.session_state.get(editor_key, {})
            edited_rows_event = editor_event.get('edited_rows', {}) if isinstance(editor_event, dict) else {}
            recalc_positions = []
            for row_pos, changes in edited_rows_event.items():
                try:
                    pos_int = int(row_pos)
                except Exception:
                    continue
                if isinstance(changes, dict) and any(
                    c in changes for c in ["Lý do nghỉ", "Ngày", "Tên nhân viên", "Chi tiết"]
                ):
                    recalc_positions.append(pos_int)

            if recalc_positions:
                recalculated_editor = detail_editor.copy()
                # Phần Chi tiết danh sách đang dùng đúng hai Google Sheet này.
                calculation_df = detail_all_df.copy() if 'detail_all_df' in locals() else pd.DataFrame()
                for pos in sorted(set(recalc_positions)):
                    if pos < 0 or pos >= len(recalculated_editor) or pos >= len(raw_detail_full):
                        continue
                    original_for_calc = raw_detail_full.iloc[pos].copy()
                    edited_for_calc = recalculated_editor.drop(columns=['Chọn'], errors='ignore').iloc[pos].copy()
                    calculated = recalculate_schedule_fields(
                        original_for_calc,
                        edited_for_calc,
                        st.session_state.current_user,
                        all_leave_data=calculation_df,
                        source_df=globals().get('df_loai_nghi', pd.DataFrame()),
                    )
                    for c in [
                        "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
                        "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
                        "Giờ cập nhật", "Người cập nhật"
                    ]:
                        if c in recalculated_editor.columns and c in calculated.index:
                            recalculated_editor.at[pos, c] = calculated[c]

                # Dùng key mới để data_editor nhận DataFrame đã tính lại, tránh phải bấm lần hai.
                st.session_state['_detail_editor_seed'] = recalculated_editor
                st.session_state['_detail_editor_version'] = editor_version + 1
                st.rerun()

            selected_positions = detail_editor.index[detail_editor['Chọn'] == True].tolist()
            st.caption(f"Đã chọn {len(selected_positions)} dòng. Chỉ các dòng được tick mới được lưu thay đổi hoặc xóa.")
            c_edit, c_delete = st.columns(2)

            with c_edit:
                if st.button("💾 Lưu thay đổi các dòng đã chọn", use_container_width=True):
                    if not selected_positions:
                        st.warning("Vui lòng tick ít nhất 1 dòng cần sửa.")
                    else:
                        can_edit_all = True
                        messages = []
                        today_edit = get_vn_today()
                        for pos in selected_positions:
                            original = raw_detail_full.iloc[pos].copy()
                            edited = detail_editor.drop(columns=['Chọn']).iloc[pos].copy()

                            # Lễ tân không được sửa lịch quá khứ; Admin được phép.
                            original_date = pd.to_datetime(original.get('Ngày'), errors='coerce').date() if pd.notna(pd.to_datetime(original.get('Ngày'), errors='coerce')) else today_edit
                            edited_date_obj = pd.to_datetime(edited.get('Ngày'), errors='coerce')
                            if st.session_state.current_role in ['letan', 'quanly'] and (original_date < today_edit or (pd.notna(edited_date_obj) and edited_date_obj.date() < today_edit)):
                                st.error("❌ Lễ tân không được sửa lịch trong quá khứ.")
                                can_edit_all = False
                                break
                            if not str(edited.get('Tên nhân viên', '')).strip() or not str(edited.get('Lý do nghỉ', '')).strip():
                                st.error("❌ Tên nhân viên và Lý do nghỉ không được để trống.")
                                can_edit_all = False
                                break

                            ok, msg = update_schedule_record(original, edited, st.session_state.current_user)
                            messages.append((ok, msg))
                            if not ok:
                                can_edit_all = False
                                break

                        for ok, msg in messages:
                            (st.success if ok else st.error)(msg)
                        if can_edit_all:
                            _clear_dynamic_data_caches()
                            st.rerun()

            with c_delete:
                if st.button("🗑️ Xóa các dòng đã chọn", use_container_width=True):
                    if not selected_positions:
                        st.warning("Vui lòng tick ít nhất 1 dòng cần xóa.")
                    else:
                        originals = [raw_detail_full.iloc[pos].copy() for pos in selected_positions]
                        today_del = get_vn_today()
                        if st.session_state.current_role in ['letan', 'quanly']:
                            has_past = False
                            for r in originals:
                                dt = pd.to_datetime(r.get('Ngày'), errors='coerce')
                                if pd.notna(dt) and dt.date() < today_del:
                                    has_past = True
                                    break
                            if has_past:
                                st.error("❌ Lễ tân không được xóa lịch trong quá khứ.")
                            else:
                                ok, msg = delete_schedule_records(originals, st.session_state.current_user)
                                (st.success if ok else st.error)(msg)
                                if ok: st.rerun()
                        else:
                            ok, msg = delete_schedule_records(originals, st.session_state.current_user)
                            (st.success if ok else st.error)(msg)
                            if ok: st.rerun()
        else:
            # Nhân viên: chỉ xem, không có checkbox sửa/xóa và không có Export Excel.
            export_view_df, _ = apply_table_layout_df(export_df.copy(), "leave_detail")
            st.dataframe(
                apply_table_visual_styler(export_view_df.style.map(highlight_khong_phep), "leave_detail", list(export_view_df.columns)),
                width="stretch",
                height="content",
                hide_index=True,
                row_height=layout_row_height("leave_detail"),
                column_config=table_layout_column_config("leave_detail", list(export_view_df.columns))
            )

    with tab2:
        if co_phep_df.empty:
            st.info("Trống.")
        else:
            co_display = format_display_df(co_phep_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
            co_display, _ = apply_table_layout_df(co_display, "leave_detail")
            st.dataframe(
                apply_table_visual_styler(co_display.style.map(highlight_khong_phep), "leave_detail", list(co_display.columns)),
                width="stretch", height="content", hide_index=True, row_height=layout_row_height("leave_detail"),
                column_config=table_layout_column_config("leave_detail", list(co_display.columns))
            )

    with tab3:
        if phat_sinh_df.empty:
            st.info("Trống.")
        else:
            ps_display = format_display_df(phat_sinh_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
            ps_display, _ = apply_table_layout_df(ps_display, "leave_detail")
            st.dataframe(
                apply_table_visual_styler(ps_display.style.map(highlight_khong_phep), "leave_detail", list(ps_display.columns)),
                width="stretch", height="content", hide_index=True, row_height=layout_row_height("leave_detail"),
                column_config=table_layout_column_config("leave_detail", list(ps_display.columns))
            )

    with tab4:
        if khong_phep_df.empty:
            st.success("Không có ai!")
        else:
            kp_display = format_display_df(khong_phep_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
            kp_display, _ = apply_table_layout_df(kp_display, "leave_detail")
            st.dataframe(
                apply_table_visual_styler(kp_display.style.map(highlight_khong_phep), "leave_detail", list(kp_display.columns)),
                width="stretch", height="content", hide_index=True, row_height=layout_row_height("leave_detail"),
                column_config=table_layout_column_config("leave_detail", list(kp_display.columns))
            )




elif selected_page == "✏️ Quản lý lịch nghỉ":
    st.subheader("✏️ Quản lý lịch nghỉ")
    st.markdown("### 🗑️ Xóa / Quản lý lịch nghỉ đã đăng ký")

    df_backup_view = df_backup.copy()
    if st.session_state.current_role == "nhanvien":
        df_backup_view = df_backup_view[df_backup_view['Tên nhân viên'] == st.session_state.current_user]

    if df_backup_view.empty: 
        st.info("Chưa có lịch nghỉ nào được đăng ký.")
    else:
        # ẨN CỘT PHẠT VI PHẠM TRONG BẢNG QUẢN LÝ CHO NON-ADMIN NHƯNG ĐÃ QUA ĐỊNH DẠNG SẠCH
        df_view_display = df_backup_view.copy()
        if st.session_state.current_role != "admin" and "Phạt vi phạm" in df_view_display.columns:
            df_view_display = df_view_display.drop(columns=["Phạt vi phạm"])

        df_view_display = format_display_df(df_view_display)
        df_view_display, _ = apply_table_layout_df(df_view_display, "leave_manage")
        st.dataframe(
            apply_table_visual_styler(df_view_display, "leave_manage", list(df_view_display.columns)),
            width="stretch", height="content", hide_index=True, row_height=layout_row_height("leave_manage"),
            column_config=table_layout_column_config("leave_manage", list(df_view_display.columns))
        )

        if st.session_state.current_role == "nhanvien" and system_status["lock_nv"]:
            st.error("🔒 Tính năng xóa lịch nghỉ hiện đang bị Admin tạm khóa. Vui lòng liên hệ Admin để được hỗ trợ!")
        else:
            with st.form("form_delete_backup_row"):
                col_ly_do_disp = 'Lý do nghỉ' if 'Lý do nghỉ' in df_backup_view.columns else 'Loại nghỉ'

                row_options = []
                valid_indices = []
                for i, row in df_backup.iterrows():
                    if st.session_state.current_role == "nhanvien" and row.get('Tên nhân viên') != st.session_state.current_user:
                        continue
                    row_options.append(f"Dòng {i+1}: {row.get('Ngày')} - {row.get('Tên nhân viên')} - {row.get(col_ly_do_disp, '')}")
                    valid_indices.append((i, str(row.get('Ngày'))))

                selected_row_str = st.selectbox("Chọn dòng lịch nghỉ cần xóa:", row_options, filter_mode="contains")

                if st.form_submit_button("🗑️ Xóa Lịch Nghỉ Đã Chọn") and selected_row_str:
                    sel_idx = row_options.index(selected_row_str)
                    real_i, sel_date_str = valid_indices[sel_idx]

                    try:
                        sel_date = pd.to_datetime(sel_date_str, format='%d/%m/%Y').date()
                    except:
                        sel_date = get_vn_today()

                    can_delete = True
                    today = get_vn_today()
                    if st.session_state.current_role == "nhanvien":
                        emp_min_date, emp_max_date = employee_registration_window(today)
                        if sel_date < emp_min_date or sel_date > emp_max_date:
                            st.error(f"❌ Nhân viên chỉ được xóa lịch từ hôm nay đến hết {emp_max_date.strftime('%d/%m/%Y')}.")
                            can_delete = False
                    elif st.session_state.current_role in ["letan", "quanly"] and sel_date < today:
                        st.error("❌ Lỗi: Tài khoản LỄ TÂN không được xóa lịch trong **QUÁ KHỨ**. Vui lòng liên hệ Admin.")
                        can_delete = False

                    if can_delete:
                        success_del, msg_del = delete_backup_row(real_i + 2, st.session_state.current_user)
                        if success_del:
                            st.success(f"✅ {msg_del}")
                            _clear_dynamic_data_caches()
                            st.rerun()
                        else: st.error(f"❌ {msg_del}")
