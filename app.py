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
        html_table = html_table.replace('<th>', '<th style="background-color: #f2f2f2; border: 1px solid #ddd; padding: 8px; text-align: center;">')
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
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide", initial_sidebar_state="auto")

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
        gsheet_data = sheet_dp.get_all_values()
        
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
        
        st.cache_data.clear()
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
def ensure_credential_control_columns():
    """Tạo các cột điều khiển nếu Sheet mật khẩu cũ chưa có."""
    try:
        client = get_gspread_client()
        if not client: return
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        header = sheet.row_values(1)
        wanted = ["Khóa đăng nhập", "Remember Token Hash", "Remember Token Expiry"]
        # Sau khi chèn J/K: R=Khóa, S=Token Hash, T=Token Expiry
        if len(header) < 20 or header[17:20] != wanted:
            gspread_update_range(sheet, 'R1:T1', [wanted])
    except Exception:
        pass

@st.cache_data(ttl=30)
def load_credentials():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            rows = sheet.get_all_values()
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
        values = sheet.get_all_values()
        targets = {normalize_login_name(x) for x in usernames}
        changed = 0
        for r_idx, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) in targets:
                sheet.update_cell(r_idx, 18, 'KHÓA' if locked else '')
                if locked:
                    sheet.update_cell(r_idx, 19, '')
                    sheet.update_cell(r_idx, 20, '')
                changed += 1
        st.cache_data.clear()
        return True, f"Đã {'khóa' if locked else 'mở khóa'} {changed} tài khoản."
    except Exception as e:
        return False, f"Lỗi cập nhật khóa đăng nhập: {e}"

def create_remember_token(username, days=None):
    """Lưu HASH token ở Google Sheet và duy trì cho tới khi người dùng chủ động Đăng xuất."""
    try:
        client = get_gspread_client()
        if not client: return None
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        values = sheet.get_all_values()
        target = normalize_login_name(username)
        for r_idx, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) == target:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
                sheet.update_cell(r_idx, 19, token_hash)
                # Không đặt ngày hết hạn: token chỉ bị xóa khi Đăng xuất hoặc tài khoản bị khóa.
                sheet.update_cell(r_idx, 20, '')
                st.cache_data.clear()
                return token
    except Exception:
        pass
    return None

def revoke_remember_token(username):
    try:
        client = get_gspread_client()
        if not client: return
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        values = sheet.get_all_values()
        target = normalize_login_name(username)
        for r_idx, row in enumerate(values[1:], start=2):
            if len(row) > 1 and normalize_login_name(row[1]) == target:
                sheet.update_cell(r_idx, 19, '')
                sheet.update_cell(r_idx, 20, '')
                break
        st.cache_data.clear()
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
        values = sheet.get_all_values()
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
            st.cache_data.clear()
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
        all_vals = sheet.get_all_values()
        
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
            
        st.cache_data.clear()
        return True, "Đã lưu đồng loạt cấu hình Ca làm việc thành công!"
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- TẢI DỮ LIỆU TỪ GOOGLE SHEET DỰ PHÒNG ---
@st.cache_data(ttl=10)
def load_backup_sheet_data():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
            rows = sheet.get_all_values()
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

@st.cache_data(ttl=10)
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
        values = sheet.get('A:J')
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

@st.cache_data(ttl=60)
def load_loai_nghi_from_gsheet():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
            rows = sheet.get_all_values()
            if len(rows) > 1:
                return pd.DataFrame(rows[1:], columns=rows[0])
    except Exception:
        pass
    return pd.DataFrame()

# --- GHI VÀ XÓA LỊCH ---
def _next_data_row_a_to_j(sheet):
    """Tìm dòng kế tiếp sau last row thực tế trong vùng A:J."""
    values = sheet.get('A:J')
    last_non_empty = 0
    for idx, row in enumerate(values, start=1):
        if any(str(v).strip() != "" for v in row[:10]):
            last_non_empty = idx
    return max(2, last_non_empty + 1)

def _live_sheet_to_leave_df(sheet):
    """Đọc trực tiếp A:J để kiểm tra trùng/thứ tự ngay trước khi ghi."""
    try:
        values = sheet.get('A:J')
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

        st.cache_data.clear()
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

        st.cache_data.clear()
        if rebalanced:
            return True, f"Đã xóa lịch nghỉ và tự xếp lại thứ tự/phạt cho {rebalanced} bản ghi còn lại."
        return True, "Đã xóa lịch nghỉ thành công!"
    except Exception as e:
        return False, f"Lỗi xóa dòng: {e}"


def _find_schedule_row_index(sheet, original_row):
    """Tìm dòng Google Sheet theo Ngày + Nhân viên + Lý do (bộ ba đang được hệ thống chặn trùng)."""
    values = sheet.get_all_values()
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
        values = sheet.get('A:J')
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
        st.cache_data.clear()
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

        st.cache_data.clear()
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

        st.cache_data.clear()
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
                ("white-space", "nowrap"),
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
# THỐNG KÊ LƯƠNG - ADMIN / LỄ TÂN ĐƯỢC ADMIN MỞ QUYỀN
# ==========================================================
PAYROLL_COLUMNS = [
    "TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
    "Hỗ trợ dạy nghề", "Học phí", "Tích lũy", "Chi Phí Sinh Hoạt",
    "Tiền phạt trong tháng", "Tiền ứng lương", "Tiền hỗ trợ Locker",
    "Số tiền thực nhận", "Email", "Số tài khoản ngân hàng", "Tên ngân hàng", "Số dòng Tip"
]
PAYROLL_ADJUSTMENT_COLUMNS = [
    "Tiền Hỗ Trợ Hoàn Lại", "Hỗ trợ dạy nghề", "Học phí", "Tích lũy",
    "Chi Phí Sinh Hoạt", "Tiền ứng lương", "Tiền hỗ trợ Locker"
]
PAYROLL_HISTORY_HEADERS = [
    "Mã bản lưu", "Từ ngày", "Đến ngày", "Ngày lưu", "Giờ lưu", "Người lưu", "Nguồn dữ liệu",
    "TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại",
    "Hỗ trợ dạy nghề", "Học phí", "Tích lũy", "Chi Phí Sinh Hoạt",
    "Tiền phạt trong tháng", "Tiền ứng lương", "Tiền hỗ trợ Locker", "Số tiền thực nhận",
    "Email", "Số tài khoản ngân hàng", "Tên ngân hàng", "Số dòng Tip"
]


def _get_or_create_worksheet(spreadsheet, title, rows=1000, cols=30):
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def _ensure_payroll_storage():
    """Tạo sheet lưu lịch sử lương và sheet cấu hình quyền nếu chưa có."""
    client = get_gspread_client()
    if not client:
        return None, None, "Chưa cấu hình quyền kết nối Google Sheets."
    try:
        ss = client.open_by_key(SHEET_MAT_KHAU_ID)
        ws_pay = _get_or_create_worksheet(ss, PAYROLL_STORAGE_WORKSHEET, rows=3000, cols=30)
        ws_cfg = _get_or_create_worksheet(ss, PAYROLL_CONFIG_WORKSHEET, rows=30, cols=5)
        if not ws_pay.row_values(1):
            gspread_update_range(ws_pay, f"A1:X1", [PAYROLL_HISTORY_HEADERS])
        else:
            header = ws_pay.row_values(1)
            if header[:len(PAYROLL_HISTORY_HEADERS)] != PAYROLL_HISTORY_HEADERS:
                gspread_update_range(ws_pay, f"A1:X1", [PAYROLL_HISTORY_HEADERS])
        if not ws_cfg.row_values(1):
            gspread_update_range(ws_cfg, "A1:B2", [["Key", "Value"], ["letan_payroll_access", "0"]])
        return ws_pay, ws_cfg, ""
    except Exception as e:
        return None, None, f"Lỗi khởi tạo vùng lưu bảng lương: {e}"


def get_payroll_letan_enabled():
    try:
        _, ws_cfg, err = _ensure_payroll_storage()
        if err or ws_cfg is None:
            return False
        vals = ws_cfg.get('A:B')
        for row in vals[1:]:
            if len(row) >= 2 and str(row[0]).strip() == "letan_payroll_access":
                return str(row[1]).strip().lower() in {"1", "true", "yes", "on", "mở", "mo"}
        return False
    except Exception:
        return False


def set_payroll_letan_enabled(enabled):
    try:
        _, ws_cfg, err = _ensure_payroll_storage()
        if err or ws_cfg is None:
            return False, err or "Không mở được sheet cấu hình."
        vals = ws_cfg.get('A:B')
        target_row = None
        for idx, row in enumerate(vals[1:], start=2):
            if len(row) >= 1 and str(row[0]).strip() == "letan_payroll_access":
                target_row = idx
                break
        if target_row is None:
            target_row = max(2, len(vals) + 1)
            gspread_update_range(ws_cfg, f"A{target_row}:B{target_row}", [["letan_payroll_access", "1" if enabled else "0"]])
        else:
            ws_cfg.update_cell(target_row, 2, "1" if enabled else "0")
        return True, "Đã mở quyền xem Bảng lương cho Lễ tân." if enabled else "Đã đóng quyền xem Bảng lương của Lễ tân."
    except Exception as e:
        return False, f"Lỗi cập nhật quyền Lễ tân: {e}"


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
        values = ws.get_all_values()
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


def resolve_payroll_period(preset, today=None, custom_range=None):
    today = today or get_vn_today()
    first_this = date(today.year, today.month, 1)
    prev_last = first_this - timedelta(days=1)
    prev_first = date(prev_last.year, prev_last.month, 1)
    if preset == "Kỳ 1 - Tháng này":
        return first_this, min(today, date(today.year, today.month, 15)), ""
    if preset == "Kỳ 2 - Tháng này":
        if today.day < 16:
            return None, None, "Tháng này chưa tới ngày 16 nên Kỳ 2 chưa bắt đầu."
        return date(today.year, today.month, 16), today, ""
    if preset == "Kỳ 1 - Tháng trước":
        return prev_first, date(prev_last.year, prev_last.month, 15), ""
    if preset == "Kỳ 2 - Tháng trước":
        return date(prev_last.year, prev_last.month, 16), prev_last, ""
    if preset == "Tùy chọn ngày":
        if isinstance(custom_range, tuple) and len(custom_range) == 2:
            return custom_range[0], custom_range[1], ""
        return None, None, "Vui lòng chọn đủ Từ ngày và Đến ngày."
    return None, None, "Không xác định được kỳ lương."


def _period_penalty_by_employee(start_date, end_date, leave_primary, leave_secondary):
    try:
        combined = combine_leave_sources_for_daily_stats(leave_secondary, leave_primary)
        if combined.empty:
            return {}
        d = combined.copy()
        d['Ngày_DT'] = pd.to_datetime(d['Ngày'], dayfirst=True, errors='coerce').dt.date
        d['Phạt vi phạm'] = pd.to_numeric(d['Phạt vi phạm'], errors='coerce').fillna(0)
        d = d[(d['Ngày_DT'] >= start_date) & (d['Ngày_DT'] <= end_date)]
        d['__key'] = d['Tên nhân viên'].apply(normalize_login_name)
        return d.groupby('__key')['Phạt vi phạm'].sum().to_dict()
    except Exception:
        return {}


def build_payroll_table(source_df, credentials_df, start_date, end_date, leave_primary=None, leave_secondary=None):
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
    if 'Phân quyền' in creds.columns:
        creds = creds[creds['Phân quyền'].astype(str).str.lower().ne('admin')]
    creds['__key'] = creds['Tên nhân viên'].apply(normalize_login_name)
    penalty_map = _period_penalty_by_employee(start_date, end_date, leave_primary, leave_secondary)

    rows = []
    for idx, (_, c) in enumerate(creds.iterrows(), start=1):
        k = c['__key']
        rows.append({
            "TT": idx,
            "Tên Hệ thống": str(c.get('Tên nhân viên', '')).strip(),
            "Họ và tên": str(c.get('Họ và tên đầy đủ', '')).strip(),
            "Tiền Lương": float(salary_map.get(k, 0)),
            "Tiền Hỗ Trợ Hoàn Lại": 0.0,
            "Hỗ trợ dạy nghề": 0.0,
            "Học phí": 0.0,
            "Tích lũy": 0.0,
            "Chi Phí Sinh Hoạt": 0.0,
            "Tiền phạt trong tháng": float(penalty_map.get(k, 0)),
            "Tiền ứng lương": 0.0,
            "Tiền hỗ trợ Locker": 0.0,
            "Số tiền thực nhận": 0.0,
            "Email": str(c.get('Email', '')).strip(),
            "Số tài khoản ngân hàng": str(c.get('Số tài khoản ngân hàng', '')).strip().replace("'", ""),
            "Tên ngân hàng": str(c.get('Tên ngân hàng', '')).strip(),
            "Số dòng Tip": int(tip_count_map.get(k, 0)),
        })
    result = pd.DataFrame(rows, columns=PAYROLL_COLUMNS)
    result = recalculate_payroll_net(result)
    credential_keys = set(creds['__key'].tolist())
    unmatched = sorted({str(v).strip() for v in tip.loc[~tip['__key'].isin(credential_keys), 'NV tư vấn'].tolist() if str(v).strip()})
    return result, unmatched


def recalculate_payroll_net(df):
    d = df.copy()
    money_cols = [
        "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Hỗ trợ dạy nghề", "Học phí", "Tích lũy",
        "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Tiền ứng lương", "Tiền hỗ trợ Locker"
    ]
    for col in money_cols:
        if col not in d.columns: d[col] = 0
        d[col] = pd.to_numeric(d[col], errors='coerce').fillna(0)
    net = (
        d["Tiền Lương"] + d["Tiền Hỗ Trợ Hoàn Lại"] + d["Hỗ trợ dạy nghề"]
        - d["Học phí"] - d["Tích lũy"] - d["Chi Phí Sinh Hoạt"]
        - d["Tiền phạt trong tháng"] - d["Tiền ứng lương"] - d["Tiền hỗ trợ Locker"]
    )
    d["Số tiền thực nhận"] = net.clip(lower=0)
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
                str(r.get('Tên ngân hàng', '')), int(_money_to_float(r.get('Số dòng Tip', 0)))
            ]
            rows.append(row)
        if rows:
            ws_pay.append_rows(rows, value_input_option='USER_ENTERED')
        return True, f"Đã lưu bảng lương {len(rows)} nhân viên vào hệ thống.", batch_id
    except Exception as e:
        return False, f"Lỗi lưu bảng lương: {e}", ""


@st.cache_data(ttl=15, show_spinner=False)
def load_payroll_history():
    try:
        ws_pay, _, err = _ensure_payroll_storage()
        if err or ws_pay is None:
            return pd.DataFrame(columns=PAYROLL_HISTORY_HEADERS)
        values = ws_pay.get_all_values()
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
    for col in [c for c in PAYROLL_COLUMNS if c.startswith('Tiền') or c in {'Học phí','Tích lũy','Chi Phí Sinh Hoạt','Số tiền thực nhận'}]:
        if col in d.columns: d[col] = pd.to_numeric(d[col], errors='coerce').fillna(0)
    if 'TT' in d.columns: d['TT'] = pd.to_numeric(d['TT'], errors='coerce').fillna(0).astype(int)
    if 'Số dòng Tip' in d.columns: d['Số dòng Tip'] = pd.to_numeric(d['Số dòng Tip'], errors='coerce').fillna(0).astype(int)
    return d


def build_payroll_excel_bytes(payroll_df, start_date, end_date):
    """Xuất Excel theo mẫu bảng lương: A4 ngang, fit 1 trang chiều ngang, căn cột/dòng gọn."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.page import PageMargins

    d = recalculate_payroll_net(payroll_df).copy()
    wb = Workbook()
    ws = wb.active
    ws.title = "Bảng lương"
    ws.merge_cells('A1:T1')
    ws['A1'] = "BẢNG LƯƠNG NHÂN VIÊN"
    ws['A1'].font = Font(name='Arial', size=18, bold=True)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws['A1'].fill = PatternFill('solid', fgColor='F3E4EC')
    ws.row_dimensions[1].height = 30
    ws['A2'] = "KỲ LƯƠNG"
    ws['A2'].font = Font(name='Arial', size=11, bold=True)
    ws.merge_cells('B2:T2')
    ws['B2'] = f"Từ ngày {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')}"
    ws['B2'].font = Font(name='Arial', size=11, bold=True)

    left_headers = [
        "TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Hỗ trợ dạy nghề",
        "Học phí", "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Tiền ứng lương",
        "Tiền hỗ trợ Locker", "Số tiền thực nhận"
    ]
    right_headers = ["TT", "Tên Hệ thống", "Tên người nhận", "Số tài khoản nhận", "Số tiền chuyển", "Nội dung giao dịch", "Tên ngân hàng nhận"]
    headers = left_headers + right_headers
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=c, value=h)
        cell.font = Font(name='Arial', size=9, bold=True)
        cell.fill = PatternFill('solid', fgColor='A1948C')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 58
    thin = Side(style='thin', color='A6A6A6')

    start_row = 4
    for i, (_, r) in enumerate(d.iterrows(), start=start_row):
        net = float(_money_to_float(r.get('Số tiền thực nhận', 0)))
        left_vals = [
            int(_money_to_float(r.get('TT', i-start_row+1))), str(r.get('Tên Hệ thống','')), str(r.get('Họ và tên','')),
            _money_to_float(r.get('Tiền Lương',0)), _money_to_float(r.get('Tiền Hỗ Trợ Hoàn Lại',0)),
            _money_to_float(r.get('Hỗ trợ dạy nghề',0)), _money_to_float(r.get('Học phí',0)), _money_to_float(r.get('Tích lũy',0)),
            _money_to_float(r.get('Chi Phí Sinh Hoạt',0)), _money_to_float(r.get('Tiền phạt trong tháng',0)),
            _money_to_float(r.get('Tiền ứng lương',0)), _money_to_float(r.get('Tiền hỗ trợ Locker',0)), net
        ]
        right_vals = [
            int(_money_to_float(r.get('TT', i-start_row+1))), str(r.get('Tên Hệ thống','')), str(r.get('Họ và tên','')),
            str(r.get('Số tài khoản ngân hàng','')).replace("'", ""), net, "TRA NO CA NHAN", str(r.get('Tên ngân hàng',''))
        ]
        for j, val in enumerate(left_vals + right_vals, start=1):
            ws.cell(row=i, column=j, value=val)
        ws.cell(row=i, column=17).number_format = '@'  # Q: Số tài khoản

    total_row = start_row + len(d)
    ws.cell(total_row, 3, "Tổng")
    ws.cell(total_row, 16, "Tổng")
    sum_cols = [4,5,6,7,8,9,10,11,12,13,18]
    for col in sum_cols:
        if col == 18:
            val = d['Số tiền thực nhận'].apply(_money_to_float).sum()
        else:
            mapping = {4:'Tiền Lương',5:'Tiền Hỗ Trợ Hoàn Lại',6:'Hỗ trợ dạy nghề',7:'Học phí',8:'Tích lũy',9:'Chi Phí Sinh Hoạt',10:'Tiền phạt trong tháng',11:'Tiền ứng lương',12:'Tiền hỗ trợ Locker',13:'Số tiền thực nhận'}
            val = d[mapping[col]].apply(_money_to_float).sum() if mapping[col] in d.columns else 0
        ws.cell(total_row, col, float(val))
    for c in range(1, 21):
        ws.cell(total_row, c).font = Font(name='Arial', size=10, bold=True)
        ws.cell(total_row, c).fill = PatternFill('solid', fgColor='E2E3E5')

    for row in ws.iter_rows(min_row=3, max_row=total_row, min_col=1, max_col=20):
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if cell.row >= 4:
                cell.font = Font(name='Arial', size=9, bold=(cell.row == total_row))
                cell.alignment = Alignment(vertical='center', wrap_text=True)
    for col in list(range(4,14)) + [18]:
        for row in range(4, total_row+1):
            ws.cell(row, col).number_format = '#,##0'
            ws.cell(row, col).alignment = Alignment(horizontal='right', vertical='center')

    widths = {
        'A':5, 'B':16, 'C':23, 'D':14, 'E':16, 'F':15, 'G':11, 'H':11, 'I':15, 'J':16, 'K':14, 'L':15, 'M':15,
        'N':5, 'O':16, 'P':23, 'Q':19, 'R':16, 'S':20, 'T':20
    }
    for col, width in widths.items(): ws.column_dimensions[col].width = width
    for r in range(4, total_row+1): ws.row_dimensions[r].height = 20
    ws.freeze_panes = 'A4'
    ws.auto_filter.ref = f"A3:T{max(3,total_row-1)}"
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins = PageMargins(left=0.2, right=0.2, top=0.35, bottom=0.35, header=0.15, footer=0.15)
    ws.print_options.horizontalCentered = True
    ws.print_title_rows = '1:3'
    ws.print_area = f"A1:T{total_row}"
    ws.sheet_view.zoomScale = 70
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def send_payroll_email(sender_email, sender_password, to_email, employee_row, start_date, end_date):
    try:
        emp = str(employee_row.get('Tên Hệ thống',''))
        full = str(employee_row.get('Họ và tên',''))
        subject = f"Bảng lương {emp} - {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')}"
        money_fields = [
            "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Hỗ trợ dạy nghề", "Học phí", "Tích lũy",
            "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Tiền ứng lương", "Tiền hỗ trợ Locker", "Số tiền thực nhận"
        ]
        html_rows = "".join(
            f"<tr><td style='padding:6px;border:1px solid #ddd'>{field}</td><td style='padding:6px;border:1px solid #ddd;text-align:right'>{_money_to_float(employee_row.get(field,0)):,.0f} VNĐ</td></tr>"
            for field in money_fields
        )
        html = f"""
        <html><body style='font-family:Arial,sans-serif'>
        <p>Chào <b>{full or emp}</b>,</p>
        <p>VERA SPA gửi bảng lương kỳ từ <b>{start_date.strftime('%d/%m/%Y')}</b> đến <b>{end_date.strftime('%d/%m/%Y')}</b>.</p>
        <table style='border-collapse:collapse;min-width:520px'>
        <tr><th style='padding:7px;border:1px solid #ddd;background:#A1948C;color:#000'>Khoản mục</th><th style='padding:7px;border:1px solid #ddd;background:#A1948C;color:#000'>Số tiền</th></tr>
        {html_rows}
        </table>
        <p><b>Số tiền thực nhận: {_money_to_float(employee_row.get('Số tiền thực nhận',0)):,.0f} VNĐ</b></p>
        <p>Vui lòng kiểm tra và phản hồi nếu có sai sót.</p><p>Trân trọng,<br><b>VERA SPA</b></p>
        </body></html>
        """
        msg = MIMEMultipart()
        msg['From'] = f"Vera Spa <{sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html, 'html'))
        one_df = pd.DataFrame([employee_row])
        attachment = build_payroll_excel_bytes(one_df, start_date, end_date)
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

# Tự đăng nhập bằng token đã nhớ (không lưu mật khẩu ở localStorage).
if not st.session_state.logged_in:
    try:
        remembered_token = st.query_params.get('remember_token', '')
        if _is_valid_fallback_admin_token(remembered_token):
            st.session_state.logged_in = True
            st.session_state.current_user = "Quản Trị Viên"
            st.session_state.current_role = "admin"
        else:
            remembered_row = validate_remember_token(remembered_token, df_credentials) if remembered_token else None
            if remembered_row is not None:
                st.session_state.logged_in = True
                st.session_state.current_user = str(remembered_row['Tên nhân viên']).strip()
                st.session_state.current_role = str(remembered_row.get('Phân quyền', 'nhanvien')).strip().lower()
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


# ==========================================
# ĐIỀU HƯỚNG THEO TỪNG TRANG CHỨC NĂNG
# ==========================================
is_admin_letan = st.session_state.current_role in ["admin", "letan"]

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
    "👤 Hồ sơ cá nhân": "ho-so-ca-nhan",
}
SLUG_TO_PAGE = {v: k for k, v in PAGE_SLUGS.items()}

payroll_letan_enabled = get_payroll_letan_enabled()

if st.session_state.current_role == "admin":
    allowed_pages = [
        "🧭 Bảng Tour", "💰 Thống kê lương", "📅 Đăng ký & Thống kê nghỉ phép", "✏️ Quản lý lịch nghỉ",
        "⏰ Thiết lập ca làm việc", "👥 Danh sách nhân sự", "➕ Thêm nhân viên",
        "✏️ Sửa / Xóa nhân viên", "🔒 Khóa đăng nhập", "🔐 Khóa quyền đăng ký",
        "🔄 Đồng bộ dữ liệu"
    ]
elif st.session_state.current_role == "letan":
    allowed_pages = [
        "🧭 Bảng Tour", "📅 Đăng ký & Thống kê nghỉ phép", "✏️ Quản lý lịch nghỉ",
        "⏰ Thiết lập ca làm việc", "👥 Danh sách nhân sự", "➕ Thêm nhân viên",
        "✏️ Sửa / Xóa nhân viên", "👤 Hồ sơ cá nhân"
    ]
    if payroll_letan_enabled:
        allowed_pages.insert(1, "💰 Thống kê lương")
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
    st.session_state.app_page = allowed_pages[0]
selected_page = st.session_state.app_page


def open_app_page(page_name):
    if page_name not in allowed_pages:
        return
    st.session_state.app_page = page_name
    st.query_params["page"] = PAGE_SLUGS[page_name]
    st.rerun()

# Nhân viên vẫn ẩn sidebar; Admin/Lễ tân dùng mỗi chức năng = một nút riêng.
if st.session_state.current_role == "nhanvien":
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
        st.session_state.pop("app_page", None)
        st.query_params['forget_login'] = '1'
        try: del st.query_params['remember_token']
        except Exception: pass
        try: del st.query_params['page']
        except Exception: pass
        st.rerun()

# Nhân viên: thanh nút điều hướng ngay trên nội dung, phù hợp điện thoại.
if st.session_state.current_role == "nhanvien":
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

    with st.form("personal_profile_form"):
        old_pass = st.text_input("Mật khẩu hiện tại (🔴 Bắt buộc để lưu)", type="password")
        new_pass = st.text_input("Mật khẩu mới (Bỏ trống nếu không đổi)", type="password")
        c1, c2 = st.columns(2)
        with c1:
            in_fullname = st.text_input("Họ và tên đầy đủ", value=curr_fullname)
            in_dob = st.text_input("Ngày sinh (VD: 15/08/1990)", value=curr_dob)
            in_phone = st.text_input("Số điện thoại", value=curr_phone)
            in_email = st.text_input("Email", value=curr_email)
        with c2:
            in_address = st.text_input("Địa chỉ", value=curr_address)
            in_bank_account = st.text_input("Số tài khoản ngân hàng", value=curr_bank_account)
            in_bank_name = bank_selectbox("Tên ngân hàng", key="profile_bank_name", current_value=curr_bank_name)
        if st.form_submit_button("💾 Lưu thay đổi", use_container_width=True):
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
    cols_staff = ['Tên nhân viên', 'Họ và tên đầy đủ', 'Phân quyền', 'Điện thoại', 'Email', 'Số tài khoản ngân hàng', 'Tên ngân hàng', 'Khóa đăng nhập']
    cols_staff = [c for c in cols_staff if c in df_credentials.columns]
    st.dataframe(df_credentials[cols_staff], width='stretch', height='content', hide_index=True)

elif selected_page == "➕ Thêm nhân viên" and is_admin_letan:
    st.subheader("➕ Thêm nhân viên")
    with st.form("form_add_emp"):
        st.write("Nhập thông tin nhân viên mới:")
        col1, col2 = st.columns(2)
        with col1:
            new_usr = st.text_input("Tên đăng nhập (Bắt buộc)")
            new_pwd = st.text_input("Mật khẩu", value="123456")
            new_role = st.selectbox("Phân quyền", ["nhanvien", "letan", "admin"], filter_mode="contains")
        with col2:
            new_fn = st.text_input("Họ và tên đầy đủ")
            new_phone = st.text_input("Số điện thoại")
            new_bank_account = st.text_input("Số tài khoản ngân hàng")
            new_bank_name = bank_selectbox("Tên ngân hàng", key="new_employee_bank_name", current_value="")

        if st.form_submit_button("Lưu Nhân Viên Mới"):
            if new_usr:
                try:
                    client = get_gspread_client()
                    sheet_mk = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                    all_emps = sheet_mk.col_values(2)

                    if normalize_login_name(new_usr) in {normalize_login_name(x) for x in all_emps}:
                        st.error("Tên đăng nhập đã tồn tại (hệ thống không phân biệt dấu và HOA/thường)!")
                    else:
                        stt_new = len(all_emps)
                        row_data = [
                        stt_new, new_usr, str(new_pwd), new_role, new_fn, "", new_phone, "", "",
                        new_bank_account, new_bank_name, "0", "0", "0", "", "", "", "", "", ""
                    ]
                        sheet_mk.append_row(row_data)
                        st.cache_data.clear()
                        st.success(f"Đã thêm thành công: {new_usr}")
                except Exception as e:
                    st.error(f"Lỗi: {e}")
            else:
                st.error("Vui lòng nhập Tên đăng nhập.")


elif selected_page == "✏️ Sửa / Xóa nhân viên" and is_admin_letan:
    st.subheader("✏️ Sửa / Xóa nhân viên")
    st.write("Chọn nhân viên cần thao tác.")
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
                        st.cache_data.clear()
                        st.success(f"Đã xóa nhân viên: {del_usr}")
                        st.rerun()
                except Exception as e:
                    st.error(f"Lỗi xóa: {e}")
    with col_action2:
        st.markdown("#### ✏️ Chỉnh sửa hồ sơ")
        if st.session_state.current_role == "admin":
            edit_usr = st.selectbox("Chọn nhân viên cần sửa:", [""] + df_credentials['Tên nhân viên'].tolist(), key='sb_edit_employee', filter_mode="contains")
            if edit_usr:
                usr_data = df_credentials[df_credentials['Tên nhân viên'] == edit_usr].iloc[0]
                with st.form("form_edit_emp_admin_v2"):
                    e_pass = st.text_input("Mật khẩu", value=str(usr_data.get('Mật khẩu', '')))
                    e_fn = st.text_input("Họ tên", value=str(usr_data.get('Họ và tên đầy đủ', '')))
                    e_dob = st.text_input("Ngày sinh", value=str(usr_data.get('Ngày sinh', '')))
                    e_phone = st.text_input("SĐT", value=str(usr_data.get('Điện thoại', '')).replace("'", ""))
                    e_email = st.text_input("Email", value=str(usr_data.get('Email', '')))
                    e_address = st.text_input("Địa chỉ", value=str(usr_data.get('Địa chỉ', '')))
                    e_bank_account = st.text_input("Số tài khoản ngân hàng", value=str(usr_data.get('Số tài khoản ngân hàng', '')).replace("'", ""))
                    e_bank_name = bank_selectbox("Tên ngân hàng", key=f"edit_bank_name_{normalize_login_name(edit_usr)}", current_value=str(usr_data.get('Tên ngân hàng', '')))
                    if st.form_submit_button("💾 Cập nhật dữ liệu", use_container_width=True):
                        ok, msg = update_user_profile(edit_usr, e_pass, e_fn, e_dob, e_phone, e_email, e_address, e_bank_account, e_bank_name)
                        (st.success if ok else st.error)(msg)
                        if ok: st.rerun()
        else:
            st.info("Lễ tân được phép xóa theo quyền hiện tại; chỉnh sửa chi tiết hồ sơ chỉ dành cho Admin.")

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

elif selected_page == "💰 Thống kê lương" and (st.session_state.current_role == "admin" or (st.session_state.current_role == "letan" and payroll_letan_enabled)):
    st.subheader("💰 Thống kê lương nhân viên")
    st.caption("Tiền Lương được tính theo đúng quy tắc: cột F bắt đầu bằng 'Tip' → cộng cột G theo tên nhân viên ở cột I.")

    tab_calc, tab_history = st.tabs(["🧮 Tính lương kỳ hiện tại", "🗂 Lịch sử bảng lương đã lưu"])
    with tab_calc:
        c_period, c_source = st.columns(2)
        with c_period:
            preset = st.selectbox(
                "Chọn kỳ tính lương:",
                ["Kỳ 1 - Tháng này", "Kỳ 2 - Tháng này", "Kỳ 1 - Tháng trước", "Kỳ 2 - Tháng trước", "Tùy chọn ngày"],
                key="payroll_period_preset", filter_mode="contains"
            )
            custom_dates = None
            if preset == "Tùy chọn ngày":
                custom_dates = st.date_input("Từ ngày - Đến ngày", value=(get_vn_today(), get_vn_today()), key="payroll_custom_dates")
            p_start, p_end, period_err = resolve_payroll_period(preset, get_vn_today(), custom_dates)
            if period_err:
                st.error(period_err)
            elif p_start and p_end:
                st.info(f"Kỳ đang chọn: **{p_start.strftime('%d/%m/%Y')} → {p_end.strftime('%d/%m/%Y')}**")
        with c_source:
            source_mode = st.selectbox(
                "Nguồn dữ liệu lương:",
                ["Google Sheet mặc định", "Upload file Excel"],
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

        if st.button("🔄 Tải dữ liệu & tính bảng lương", use_container_width=True, disabled=bool(period_err)):
            with st.spinner("Đang đọc dữ liệu doanh thu và tính lương..."):
                if source_mode == "Upload file Excel":
                    src_df, src_err = load_payroll_source_from_uploaded_excel(payroll_upload)
                    src_label = getattr(payroll_upload, 'name', 'Upload Excel') if payroll_upload is not None else 'Upload Excel'
                else:
                    src_df, src_err = load_payroll_source_from_google_sheet()
                    src_label = f"Google Sheet {PAYROLL_SOURCE_SHEET_ID}"
                if src_err:
                    st.error(src_err)
                else:
                    payroll_df, unmatched_names = build_payroll_table(
                        src_df, df_credentials, p_start, p_end,
                        leave_primary=df_backup, leave_secondary=df_leave_secondary
                    )
                    st.session_state.payroll_current_df = payroll_df
                    st.session_state.payroll_current_start = p_start.isoformat()
                    st.session_state.payroll_current_end = p_end.isoformat()
                    st.session_state.payroll_current_source = src_label
                    st.session_state.payroll_unmatched = unmatched_names
                    st.success(f"Đã tính lương cho {len(payroll_df)} tài khoản nhân viên.")

        current = st.session_state.get('payroll_current_df')
        if isinstance(current, pd.DataFrame) and not current.empty:
            current_start = date.fromisoformat(st.session_state.get('payroll_current_start'))
            current_end = date.fromisoformat(st.session_state.get('payroll_current_end'))
            unmatched = st.session_state.get('payroll_unmatched', [])
            if unmatched:
                st.warning("Có tên ở dữ liệu Tip nhưng không khớp tài khoản hệ thống: " + ", ".join(map(str, unmatched)))

            # Bảng nhập các khoản điều chỉnh. Tiền Lương / Phạt được tự động lấy, các khoản khác cho Admin/Lễ tân chỉnh.
            editor_cols = [
                "TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền Hỗ Trợ Hoàn Lại", "Hỗ trợ dạy nghề",
                "Học phí", "Tích lũy", "Chi Phí Sinh Hoạt", "Tiền phạt trong tháng", "Tiền ứng lương", "Tiền hỗ trợ Locker"
            ]
            editor_df = current[editor_cols].copy()
            col_cfg = {
                "TT": st.column_config.NumberColumn("TT", format="%d", disabled=True, width="small"),
                "Tên Hệ thống": st.column_config.TextColumn("Tên Hệ thống", disabled=True, width="medium"),
                "Họ và tên": st.column_config.TextColumn("Họ và tên", disabled=True, width="large"),
                "Tiền Lương": st.column_config.NumberColumn("Tiền Lương", format="%.0f", disabled=True),
                "Tiền phạt trong tháng": st.column_config.NumberColumn("Tiền phạt trong tháng", format="%.0f", disabled=True),
            }
            for c in PAYROLL_ADJUSTMENT_COLUMNS:
                col_cfg[c] = st.column_config.NumberColumn(c, min_value=0.0, step=50000.0, format="%.0f")
            edited = st.data_editor(
                editor_df, key="payroll_adjustment_editor", width="stretch", height="content", hide_index=True,
                column_config=col_cfg, disabled=["TT", "Tên Hệ thống", "Họ và tên", "Tiền Lương", "Tiền phạt trong tháng"]
            )
            final_df = current.copy()
            for c in editor_cols:
                if c in edited.columns:
                    final_df[c] = edited[c].values
            final_df = recalculate_payroll_net(final_df)
            st.session_state.payroll_current_df = final_df

            total_salary = final_df['Tiền Lương'].sum()
            total_penalty = final_df['Tiền phạt trong tháng'].sum()
            total_net = final_df['Số tiền thực nhận'].sum()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Nhân viên", len(final_df))
            c2.metric("Tổng Tiền Lương", f"{total_salary:,.0f} đ".replace(',', '.'))
            c3.metric("Tổng tiền phạt", f"{total_penalty:,.0f} đ".replace(',', '.'))
            c4.metric("Tổng thực nhận", f"{total_net:,.0f} đ".replace(',', '.'))

            display_cols = [c for c in PAYROLL_COLUMNS if c not in {"Email", "Số tài khoản ngân hàng", "Tên ngân hàng"}]
            st.markdown("### 📋 Bảng lương tổng hợp")
            st.dataframe(
                final_df[display_cols].style.format({c: '{:,.0f}' for c in final_df[display_cols].columns if c.startswith('Tiền') or c in {'Học phí','Tích lũy','Chi Phí Sinh Hoạt','Số tiền thực nhận'}}),
                width="stretch", height="content", hide_index=True
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
                emailable = final_df[final_df['Email'].astype(str).str.contains('@', na=False)].copy()
                employees_email = emailable['Tên Hệ thống'].tolist()
                selected_email_emps = st.multiselect(
                    "Chọn 1, nhiều hoặc tất cả nhân viên:", employees_email, default=employees_email,
                    filter_mode="contains", key="payroll_email_recipients"
                )
                st.caption(f"Có {len(employees_email)} nhân viên có email hợp lệ trong hồ sơ hệ thống.")
                if st.button("🚀 Gửi bảng lương cho nhân viên đã chọn", use_container_width=True):
                    if not selected_email_emps:
                        st.warning("Vui lòng chọn ít nhất 1 nhân viên.")
                    else:
                        sender_email = "veraspabienhoa@gmail.com"
                        sender_pass = "zvtgbysfmdaqxaau"
                        progress = st.progress(0)
                        ok_count, errors = 0, []
                        for idx, emp in enumerate(selected_email_emps):
                            row = emailable[emailable['Tên Hệ thống'] == emp].iloc[0]
                            ok, msg = send_payroll_email(sender_email, sender_pass, str(row['Email']).strip(), row, current_start, current_end)
                            if ok: ok_count += 1
                            else: errors.append(f"{emp}: {msg}")
                            progress.progress((idx + 1) / len(selected_email_emps))
                            time.sleep(0.35)
                        if ok_count: st.success(f"Đã gửi thành công {ok_count}/{len(selected_email_emps)} email bảng lương.")
                        for e in errors: st.error(e)

    with tab_history:
        history = load_payroll_history()
        if history.empty or 'Mã bản lưu' not in history.columns:
            st.info("Chưa có bảng lương nào được lưu trong hệ thống.")
        else:
            batches = [x for x in history['Mã bản lưu'].dropna().astype(str).unique().tolist() if x.strip()]
            # Bản mới nhất nằm cuối Sheet nên đảo lên đầu.
            batches = list(reversed(batches))
            batch = st.selectbox("Chọn bản lương đã lưu:", batches, filter_mode="contains", key="payroll_history_batch")
            saved = history[history['Mã bản lưu'].astype(str) == str(batch)].copy()
            if not saved.empty:
                st.info(
                    f"Kỳ {saved.iloc[0].get('Từ ngày','')} → {saved.iloc[0].get('Đến ngày','')} | "
                    f"Lưu bởi {saved.iloc[0].get('Người lưu','')} lúc {saved.iloc[0].get('Giờ lưu','')} ngày {saved.iloc[0].get('Ngày lưu','')}"
                )
                saved_table = payroll_history_to_table(saved)
                st.dataframe(saved_table, width="stretch", height="content", hide_index=True)
                try:
                    hs = pd.to_datetime(saved.iloc[0]['Từ ngày'], dayfirst=True).date()
                    he = pd.to_datetime(saved.iloc[0]['Đến ngày'], dayfirst=True).date()
                    hist_excel = build_payroll_excel_bytes(saved_table, hs, he)
                    st.download_button(
                        "📥 Export lại bản lương đã lưu",
                        data=hist_excel,
                        file_name=f"BangLuong_DaLuu_{hs.strftime('%d%m%Y')}_{he.strftime('%d%m%Y')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                except Exception as e:
                    st.warning(f"Không tạo được file export lịch sử: {e}")

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
                        ("white-space", "nowrap"),
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

        # Auto-fit toàn bộ chiều cao: hiển thị đủ dòng, bỏ thanh cuộn dọc.
        status_col_display = _find_tour_col(df_tour_display, "Trạng thái")
        remain_col_display = _find_tour_col(df_tour_display, "Thời gian còn lại")
        tour_column_config = {}
        if status_col_display is not None:
            tour_column_config[status_col_display] = st.column_config.TextColumn(
                status_col_display, width="medium"
            )
        if remain_col_display is not None:
            tour_column_config[remain_col_display] = st.column_config.TextColumn(
                remain_col_display, width="small"
            )

        st.dataframe(
            style_bang_tour(df_tour_display),
            use_container_width=True,
            hide_index=True,
            height="content",
            column_config=tour_column_config
        )
        st.caption(
            "Màu dòng: Nghỉ phép = chữ mờ/nền trắng; Đi làm = chữ đen/nền trắng; "
            "≥15 phút = xanh; 0–<15 = vàng; -15–<0 = đỏ; ≤-15 làm trống thời gian; Break = cam."
        )

elif selected_page == "📅 Đăng ký & Thống kê nghỉ phép":
    st.subheader("➕ Đăng ký lịch nghỉ")
    users_s = df_credentials['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_credentials.empty else []
    users_e = df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_nv_excel.empty else []
    all_users = sorted(list(set(users_s + users_e)))
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
                        if current_role not in dk_role: role_allowed = False

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
                elif current_role == "letan" and start_date < today:
                    st.error("❌ Lỗi: Tài khoản LỄ TÂN không được đăng ký lịch trong **QUÁ KHỨ**. Muốn sửa lịch cũ, vui lòng liên hệ Admin.")
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
                                    st.cache_data.clear()



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
        list_nv = ["- Tất cả nhân viên -"] + sorted(list(set(df_credentials['Tên nhân viên'].dropna().tolist() + (df_nv_excel['Tên nhân viên'].dropna().tolist() if not df_nv_excel.empty else []))))
        selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", list_nv, filter_mode="contains")

    with col_refresh:
        st.write("") 
        if st.button("🔄 Cập Nhật Dữ Liệu", use_container_width=True):
            st.cache_data.clear()
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
        elif st.session_state.current_role in ["admin", "letan"]:
            # Admin/Lễ tân: checkbox chọn 1 hoặc nhiều dòng và sửa trực tiếp tại bảng.
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

            # Các cột này chỉ do hệ thống tính, không cho nhập tay để tránh sai dữ liệu.
            derived_cols = [
                "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm",
                "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"
            ]
            disabled_cols = [c for c in derived_cols if c in editor_df.columns]

            editor_version = int(st.session_state.get('_detail_editor_version', 1))
            editor_key = f"detail_schedule_editor_v{editor_version}"
            detail_editor = st.data_editor(
                editor_df,
                width="stretch",
                height="content",
                hide_index=True,
                num_rows="fixed",
                disabled=disabled_cols,
                column_config={
                    "Chọn": st.column_config.CheckboxColumn(
                        "Chọn", help="Tick 1 hoặc nhiều dòng để sửa/xóa",
                        default=False, width="small"
                    ),
                    "Ngày": st.column_config.DateColumn("Ngày", format="DD/MM/YYYY"),
                    "Lý do nghỉ": st.column_config.SelectboxColumn(
                        "Lý do nghỉ",
                        options=reason_options,
                        required=True,
                        help="Bấm vào ô để chọn Lý do nghỉ. Danh sách được tải tự động từ sheet LoaiNghi."
                    ),
                    "Số ngày tính": st.column_config.NumberColumn(
                        "Số ngày tính", step=0.5, format="%.1f", disabled=True
                    ) if "Số ngày tính" in editor_df.columns else None,
                    "Số ngày phép cộng dồn": st.column_config.NumberColumn(
                        "Số ngày phép cộng dồn", step=0.5, format="%.1f", disabled=True
                    ) if "Số ngày phép cộng dồn" in editor_df.columns else None,
                    "Phạt vi phạm": st.column_config.NumberColumn(
                        "Phạt vi phạm", step=50000, format="%.0f", disabled=True
                    ) if "Phạt vi phạm" in editor_df.columns else None,
                    "Ngày cập nhật": st.column_config.TextColumn("Ngày cập nhật", disabled=True),
                    "Giờ cập nhật": st.column_config.TextColumn("Giờ cập nhật", disabled=True),
                    "Người cập nhật": st.column_config.TextColumn("Người cập nhật", disabled=True),
                },
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
                            if st.session_state.current_role == 'letan' and (original_date < today_edit or (pd.notna(edited_date_obj) and edited_date_obj.date() < today_edit)):
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
                            st.cache_data.clear()
                            st.rerun()

            with c_delete:
                if st.button("🗑️ Xóa các dòng đã chọn", use_container_width=True):
                    if not selected_positions:
                        st.warning("Vui lòng tick ít nhất 1 dòng cần xóa.")
                    else:
                        originals = [raw_detail_full.iloc[pos].copy() for pos in selected_positions]
                        today_del = get_vn_today()
                        if st.session_state.current_role == 'letan':
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
            st.dataframe(
                export_df.style.map(highlight_khong_phep),
                width="stretch",
                height="content",
                hide_index=True
            )

    with tab2:
        if co_phep_df.empty:
            st.info("Trống.")
        else:
            co_display = format_display_df(co_phep_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
            st.dataframe(co_display.style.map(highlight_khong_phep), width="stretch", height="content", hide_index=True)

    with tab3:
        if phat_sinh_df.empty:
            st.info("Trống.")
        else:
            ps_display = format_display_df(phat_sinh_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
            st.dataframe(ps_display.style.map(highlight_khong_phep), width="stretch", height="content", hide_index=True)

    with tab4:
        if khong_phep_df.empty:
            st.success("Không có ai!")
        else:
            kp_display = format_display_df(khong_phep_df.drop(columns=cols_to_hide + ['__source_sheet_id', '__source_row'], errors='ignore'))
            st.dataframe(kp_display.style.map(highlight_khong_phep), width="stretch", height="content", hide_index=True)




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
        st.dataframe(df_view_display, width="stretch", height="content", hide_index=True)

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
                    elif st.session_state.current_role == "letan" and sel_date < today:
                        st.error("❌ Lỗi: Tài khoản LỄ TÂN không được xóa lịch trong **QUÁ KHỨ**. Vui lòng liên hệ Admin.")
                        can_delete = False

                    if can_delete:
                        success_del, msg_del = delete_backup_row(real_i + 2, st.session_state.current_user)
                        if success_del:
                            st.success(f"✅ {msg_del}")
                            st.cache_data.clear()
                            st.rerun()
                        else: st.error(f"❌ {msg_del}")
