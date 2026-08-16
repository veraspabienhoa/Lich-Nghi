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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
SHEET_CHINH_ID = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
BANG_TOUR_FILE_ID = "1yA1Oog_6R-HmDFatcku-x8s-59p2dP9R"

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

def create_remember_token(username, days=30):
    """Lưu HASH token ở Google Sheet; trình duyệt chỉ giữ token, không giữ mật khẩu."""
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
                expiry = datetime.now(VN_TZ) + timedelta(days=days)
                sheet.update_cell(r_idx, 19, token_hash)
                sheet.update_cell(r_idx, 20, expiry.isoformat())
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
    if not token or credentials_df.empty:
        return None
    token_hash = hashlib.sha256(str(token).encode('utf-8')).hexdigest()
    now = datetime.now(VN_TZ)
    for _, row in credentials_df.iterrows():
        saved_hash = str(row.get('Remember Token Hash', '')).strip()
        if not saved_hash or not hmac.compare_digest(token_hash, saved_hash):
            continue
        if is_locked_value(row.get('Khóa đăng nhập', '')):
            return None
        try:
            exp = datetime.fromisoformat(str(row.get('Remember Token Expiry', '')).strip())
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=VN_TZ)
            if exp < now:
                return None
        except Exception:
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

def save_lich_nghi_to_backup_sheet(ngay, nv, loai_nghi, chi_tiet, so_ngay, so_ngay_cong_don, phat_vi_pham, role):
    """
    Ghi lịch vào Google Sheet dự phòng, worksheet đầu tiên (Sheet1), đúng vùng A:J.
    Dữ liệu luôn được ghi vào dòng ngay sau last row thực tế, không dùng append_row
    để tránh Google Sheets tự suy luận sai table range/cột bắt đầu.

    Sau đó hệ thống thử ghi thêm vào worksheet LichNghi của SHEET_CHINH_ID nếu ID đó
    là Google Sheet. Việc nơi thứ hai không hỗ trợ ghi không làm mất dòng đã lưu ở Sheet1.
    """
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets."

        ngay_cn = get_vn_today().strftime('%d/%m/%Y')
        gio_cn = datetime.now(VN_TZ).strftime('%H:%M:%S')
        row_values = [
            str(ngay),
            str(nv),
            str(loai_nghi).replace("🔴 ", ""),
            str(chi_tiet),
            float(so_ngay) if so_ngay is not None else 0.0,
            float(so_ngay_cong_don),
            float(phat_vi_pham),
            str(ngay_cn),
            str(gio_cn),
            str(role),
        ]

        # Nơi bắt buộc: Google Sheet dự phòng / Sheet1.
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        header = [
            "Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính",
            "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật",
            "Giờ cập nhật", "Người cập nhật"
        ]

        # Nếu A1:J1 chưa có header chuẩn thì chỉ bổ sung các ô header đang trống;
        # không chèn cột và không làm lệch dữ liệu cũ.
        current_header = sheet_dp.get('A1:J1')
        current_header = current_header[0] if current_header else []
        if not any(str(v).strip() for v in current_header):
            gspread_update_range(sheet_dp, 'A1:J1', [header], value_input_option='USER_ENTERED')

        target_row = _next_data_row_a_to_j(sheet_dp)
        target_range = f"A{target_row}:J{target_row}"
        gspread_update_range(
            sheet_dp,
            target_range,
            [row_values],
            value_input_option='USER_ENTERED'
        )

        # Nơi thứ hai: chỉ ghi nếu SHEET_CHINH_ID thực sự là Google Sheet.
        main_note = ""
        try:
            sheet_chinh_lich = client.open_by_key(SHEET_CHINH_ID).worksheet("LichNghi")
            main_row = _next_data_row_a_to_j(sheet_chinh_lich)
            gspread_update_range(
                sheet_chinh_lich,
                f"A{main_row}:J{main_row}",
                [row_values],
                value_input_option='USER_ENTERED'
            )
            main_note = " Đồng thời đã ghi vào sheet LichNghi của file chính."
        except Exception:
            # File chính hiện có thể là XLSB/XLSM trên Drive nên gspread không ghi trực tiếp được.
            main_note = ""

        st.cache_data.clear()
        return True, f"Đã lưu lịch nghỉ tại Sheet1, dòng {target_row}, vùng A{target_row}:J{target_row}." + main_note
    except Exception as e:
        return False, f"Lỗi ghi dữ liệu: {e}"


def delete_backup_row(row_index_1_based):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet.delete_rows(row_index_1_based)
        st.cache_data.clear()
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

def update_schedule_record(original_row, edited_row, updated_by):
    """Sửa 1 lịch ở Sheet dự phòng và cố gắng đồng bộ sang Sheet chính."""
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."

        def num(v, fallback=0.0):
            try:
                if pd.isna(v) or str(v).strip() == '': return float(fallback)
                return float(str(v).replace(',', '').strip())
            except Exception:
                return float(fallback)

        ngay = normalize_schedule_date(edited_row.get('Ngày', original_row.get('Ngày', '')))
        nv = str(edited_row.get('Tên nhân viên', original_row.get('Tên nhân viên', ''))).strip()
        lydo = str(edited_row.get('Lý do nghỉ', original_row.get('Lý do nghỉ', ''))).replace('🔴 ', '').strip()
        chitiet = str(edited_row.get('Chi tiết', original_row.get('Chi tiết', ''))).strip()
        songay = num(edited_row.get('Số ngày tính', original_row.get('Số ngày tính', 0)), original_row.get('Số ngày tính', 0))
        congdon = num(edited_row.get('Số ngày phép cộng dồn', original_row.get('Số ngày phép cộng dồn', 0)), original_row.get('Số ngày phép cộng dồn', 0))
        phat = num(edited_row.get('Phạt vi phạm', original_row.get('Phạt vi phạm', 0)), original_row.get('Phạt vi phạm', 0))
        ngay_cn = get_vn_today().strftime('%d/%m/%Y')
        gio_cn = datetime.now(VN_TZ).strftime('%H:%M:%S')
        new_values = [ngay, nv, lydo, chitiet, songay, congdon, phat, ngay_cn, gio_cn, str(updated_by)]

        backup = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        row_idx = _find_schedule_row_index(backup, original_row)
        if not row_idx:
            return False, "Không tìm thấy dòng tương ứng trong Google Sheet dự phòng."
        gspread_update_range(backup, f'A{row_idx}:J{row_idx}', [new_values], raw=False)

        sync_warning = ''
        try:
            main = client.open_by_key(SHEET_CHINH_ID).worksheet('LichNghi')
            main_idx = _find_schedule_row_index(main, original_row)
            if main_idx:
                gspread_update_range(main, f'A{main_idx}:J{main_idx}', [new_values], raw=False)
        except Exception as e:
            sync_warning = f" (Cảnh báo đồng bộ Sheet chính: {e})"

        st.cache_data.clear()
        return True, "Đã cập nhật lịch nghỉ." + sync_warning
    except Exception as e:
        return False, f"Lỗi cập nhật lịch nghỉ: {e}"

def delete_schedule_records(original_rows):
    """Xóa một hoặc nhiều lịch theo checkbox; xóa từ dưới lên để không lệch số dòng."""
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        backup = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        backup_indices = []
        for row in original_rows:
            idx = _find_schedule_row_index(backup, row)
            if idx: backup_indices.append(idx)
        for idx in sorted(set(backup_indices), reverse=True):
            backup.delete_rows(idx)

        sync_warning = ''
        try:
            main = client.open_by_key(SHEET_CHINH_ID).worksheet('LichNghi')
            main_indices = []
            for row in original_rows:
                idx = _find_schedule_row_index(main, row)
                if idx: main_indices.append(idx)
            for idx in sorted(set(main_indices), reverse=True):
                main.delete_rows(idx)
        except Exception as e:
            sync_warning = f" (Cảnh báo đồng bộ Sheet chính: {e})"

        st.cache_data.clear()
        return True, f"Đã xóa {len(set(backup_indices))} dòng lịch nghỉ." + sync_warning
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


def style_bang_tour(df):
    """
    Chuyển các Conditional Formatting chính trong VBA người dùng gửi sang Pandas Styler.
    Mapping cột theo Excel: B, D:G, E:F, G, I, K, L, P, Q, R, T...
    """
    def row_style(row):
        styles = [""] * len(row)
        vals = list(row.values)
        def get(pos):
            return vals[pos] if pos < len(vals) else ""
        def add(pos, css):
            if 0 <= pos < len(styles):
                styles[pos] = (styles[pos] + ";" + css).strip(";")
        def add_range(a, b, css):
            for pos in range(a, min(b + 1, len(styles))):
                add(pos, css)

        b = _tour_text(get(1))       # B Nhân viên / cách dòng
        g = _tour_text(get(6))       # G Trạng thái
        i_val = _tour_num(get(8))    # I
        k_txt = _tour_text(get(10))  # K Còn lại
        k_num = _tour_num(get(10))
        l = _tour_text(get(11))      # L Thanh toán / SL tour tùy phiên bản
        p = _tour_text(get(15))      # P Đi làm
        q = _tour_text(get(16))      # Q Ca
        r = _tour_text(get(17))      # R Break
        t_num = _tour_num(get(19))   # T

        # Rule trọng tâm D:G theo K (VBA: trắng / đỏ / vàng / xanh).
        if k_txt == "":
            add_range(3, 6, "background-color:#FFFFFF")
        elif k_num is not None and k_num <= 0:
            add_range(3, 6, "background-color:#FF0000;color:#FFFFFF;font-weight:700")
        elif k_num is not None and 0 < k_num < 10:
            add_range(3, 6, "background-color:#FFFF00;color:#000000;font-weight:700")
        elif k_num is not None and k_num >= 10:
            add_range(3, 6, "background-color:#92D050;color:#000000")

        # Trạng thái Dang cho: VBA có hai rule, màu xám là rule được thêm sau.
        if remove_vietnamese_accents(g).casefold() == "dang cho":
            add_range(3, 6, "background-color:#D9D9D9")
            add(6, "background-color:#D9D9D9;font-weight:700")

        # Breaktime / cách dòng.
        if r.casefold() == "breaktime":
            add_range(3, 6, "background-color:#FCE4D6;color:#000000")
            add(17, "background-color:#FCE4D6;color:#000000;font-weight:700")
        if b.casefold() == "cách dòng" or remove_vietnamese_accents(b).casefold() == "cach dong":
            for pos in range(len(styles)):
                add(pos, "background-color:#7571C1;color:#7571C1")

        # Đi làm / nghỉ phép / vào ca theo VBA.
        p_norm = remove_vietnamese_accents(p).casefold()
        q_norm = remove_vietnamese_accents(q).casefold()
        if p_norm == "di lam":
            add(15, "background-color:#000000;color:#2F75B5;font-weight:700")
        elif p_norm == "nghi phep":
            add(15, "background-color:#000000;color:#D9D9D9;font-weight:700")
        if q_norm in {"ca 1", "ca 2", "vao ca"}:
            add(16, "background-color:#000000;color:#FFFFFF;font-weight:700")

        # CHO THANH TOAN ở cột L.
        if remove_vietnamese_accents(l).casefold() == "cho thanh toan":
            add(11, "background-color:#FFE699;color:#000000;font-weight:700")

        # T < -30: nền đen, chữ xám sáng.
        if t_num is not None and t_num < -30:
            add(19, "background-color:#000000;color:#F2F2F2;font-weight:700")

        # E:F nếu K = Xac nhan; hoặc I < -5.
        if remove_vietnamese_accents(k_txt).casefold() == "xac nhan":
            add_range(4, 5, "background-color:#000000;color:#FFFF00;font-weight:700")
        if i_val is not None and i_val < -5:
            add_range(4, 5, "background-color:#000000;color:#0070C0;font-weight:700")

        return styles

    styler = df.style.apply(row_style, axis=1)
    styler = styler.set_table_styles([
        {"selector": "th", "props": [("background-color", "#f2d9e6"), ("font-weight", "700"), ("text-align", "center")]},
        {"selector": "td", "props": [("white-space", "nowrap")]},
    ])
    return styler


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

# Tải dữ liệu
ensure_credential_control_columns()
df_credentials = load_credentials() 
df_backup = load_backup_sheet_data()
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
        remembered_row = validate_remember_token(remembered_token, df_credentials) if remembered_token else None
        if remembered_row is not None:
            st.session_state.logged_in = True
            st.session_state.current_user = str(remembered_row['Tên nhân viên']).strip()
            st.session_state.current_role = str(remembered_row.get('Phân quyền', 'nhanvien')).strip().lower()
        elif remembered_token:
            # Token hết hạn/bị khóa/sai -> xóa khỏi trình duyệt.
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
        remember_login = st.checkbox("Ghi nhớ đăng nhập trên thiết bị này (30 ngày)", value=True)

        if st.form_submit_button("Đăng Nhập"):
            input_name_norm = normalize_login_name(username_input)

            # Tài khoản quản trị dự phòng cũ: vẫn chấp nhận HOA/thường ở tên đăng nhập.
            if input_name_norm == normalize_login_name('admin') and password_matches(password_input, '32531235'):
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.session_state.current_role = "admin"
                # Admin dự phòng không lưu token vì không nằm trong Sheet tài khoản.
                if not remember_login:
                    st.query_params['forget_login'] = '1'
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
                    if remember_login:
                        token = create_remember_token(st.session_state.current_user)
                        if token:
                            st.query_params['remember_token'] = token
                    else:
                        revoke_remember_token(st.session_state.current_user)
                        st.query_params['forget_login'] = '1'
                    st.rerun()
                else:
                    st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop()


# ==========================================
# ĐIỀU HƯỚNG THEO TỪNG TRANG CHỨC NĂNG
# ==========================================
is_admin_letan = st.session_state.current_role in ["admin", "letan"]

PAGE_SLUGS = {
    "📊 Thống kê nghỉ phép": "thong-ke-nghi-phep",
    "🧭 Bảng Tour": "bang-tour",
    "➕ Đăng ký lịch nghỉ": "dang-ky-lich-nghi",
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

if st.session_state.current_role == "admin":
    allowed_pages = [
        "📊 Thống kê nghỉ phép", "🧭 Bảng Tour", "➕ Đăng ký lịch nghỉ", "✏️ Quản lý lịch nghỉ",
        "⏰ Thiết lập ca làm việc", "👥 Danh sách nhân sự", "➕ Thêm nhân viên",
        "✏️ Sửa / Xóa nhân viên", "🔒 Khóa đăng nhập", "🔐 Khóa quyền đăng ký",
        "🔄 Đồng bộ dữ liệu"
    ]
elif st.session_state.current_role == "letan":
    allowed_pages = [
        "📊 Thống kê nghỉ phép", "🧭 Bảng Tour", "➕ Đăng ký lịch nghỉ", "✏️ Quản lý lịch nghỉ",
        "⏰ Thiết lập ca làm việc", "👥 Danh sách nhân sự", "➕ Thêm nhân viên",
        "✏️ Sửa / Xóa nhân viên", "👤 Hồ sơ cá nhân"
    ]
else:
    allowed_pages = [
        "📊 Thống kê nghỉ phép", "🧭 Bảng Tour", "➕ Đăng ký lịch nghỉ", "✏️ Quản lý lịch nghỉ",
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

elif selected_page == "🧭 Bảng Tour":
    st.subheader("🧭 Bảng Tour")
    st.caption("Dữ liệu lấy từ Google Drive → sheet Input. Màu được mô phỏng theo Conditional Formatting trong file VBA bạn cung cấp.")

    c_refresh, c_info = st.columns([2, 8])
    with c_refresh:
        if st.button("🔄 Làm mới Bảng Tour", use_container_width=True):
            load_bang_tour_input.clear()
            st.rerun()
    with c_info:
        st.caption("Dữ liệu được cache tối đa 15 giây để giảm tải file Drive.")

    df_tour, tour_err = load_bang_tour_input()
    if tour_err:
        st.error(tour_err)
    elif df_tour.empty:
        st.info("Không có dữ liệu trong sheet Input.")
    else:
        # Auto height nhưng giới hạn hợp lý nếu tour quá dài; chiều ngang cho phép cuộn trên mobile.
        tour_height = max(260, min(42 + len(df_tour) * 35, 1200))
        st.dataframe(
            style_bang_tour(df_tour),
            use_container_width=True,
            hide_index=True,
            height=tour_height
        )
        st.caption("Màu chính: K <= 0 đỏ • 0 < K < 10 vàng • K >= 10 xanh • Breaktime màu cam nhạt • Dang cho màu xám • CHO THANH TOAN màu vàng nhạt.")

elif selected_page == "➕ Đăng ký lịch nghỉ":
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
            list_loai_nghi = ["Nghỉ phép", "🔴 Nghỉ không phép", "Nghỉ phát sinh", "🔴 Đi trễ không phép"]
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

        existing_today = []
        if not df_lich.empty and chosen_nv != "-- Chọn nhân viên --":
            ex_df = df_lich[(df_lich['Tên nhân viên'] == chosen_nv) & (df_lich['Ngày'] == start_date)]
            existing_today = ex_df['Lý do nghỉ'].astype(str).str.strip().tolist()

        dyn_key_suffix = f"{chosen_loai}_{start_date}_{chosen_nv}"

        with st.form("form_nhap_lich_inner"):
            txt_chitiet_label = "Chi tiết vi phạm / Ghi chú (🔴 **Bắt buộc**):" if (is_loi_vi_pham or is_nghi_ly_do_khac) else "Chi tiết vi phạm / Ghi chú (nếu có):"
            input_chitiet = st.text_input(txt_chitiet_label).strip()

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                val_songay = st.number_input("Số ngày tính:", value=float(default_songay), step=0.5, key=f"num_songay_{dyn_key_suffix}", disabled=is_loi_vi_pham)

            # HIỂN THỊ Ô MỨC PHẠT CHO TẤT CẢ TÀI KHOẢN (ĐÃ MỞ LẠI)
            with col_p2:
                txt_phat_label = "Mức phạt vi phạm VNĐ (🔴 **Bắt buộc**):" if is_loi_vi_pham else "Mức phạt vi phạm (VNĐ):"
                val_phat = st.number_input(txt_phat_label, value=float(default_phat), step=50000.0, key=f"num_phat_{dyn_key_suffix}")

            confirm_multiple = True
            if existing_today:
                if chosen_loai.replace("🔴 ", "") in existing_today:
                    st.error(f"❌ Nhân viên này đã có Lý do nghỉ: '{chosen_loai}' vào ngày này rồi. KHÔNG THỂ trùng cùng 1 lý do!")
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
                                all_saved = True  # Thêm cờ kiểm tra lưu thành công
                                for i in range(num_days_selected):
                                    curr_date_iter = start_date + timedelta(days=i)
                                    is_weekend_iter = curr_date_iter.weekday() >= 5

                                    if val_songay is not None: accumulated_month += val_songay
                                    else: val_songay = 0.0

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
                                    success_bk, msg_bk = save_lich_nghi_to_backup_sheet(
                                        curr_date_iter.strftime('%d/%m/%Y'), chosen_nv, chosen_loai.replace("🔴 ", ""), 
                                        input_chitiet, val_songay, accumulated_month, val_phat, st.session_state.current_role
                                    )

                                    # KIỂM TRA NẾU LỖI THÌ BÁO VÀ DỪNG LẠI NGAY
                                    if not success_bk:
                                        st.error(f"❌ LỖI GOOGLE SHEETS: {msg_bk}")
                                        all_saved = False
                                        break

                                # CHỈ IN THÀNH CÔNG NẾU API THỰC SỰ TRẢ VỀ SUCCESS
                                if all_saved:
                                    st.success(f"✅ Đã ghi nhận lịch nghỉ thành công cho {num_days_selected} ngày!")
                                    st.cache_data.clear()


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
                        success_del, msg_del = delete_backup_row(real_i + 2)
                        if success_del:
                            st.success(f"✅ {msg_del}")
                            st.cache_data.clear()
                            st.rerun()
                        else: st.error(f"❌ {msg_del}")

elif selected_page == "📊 Thống kê nghỉ phép":
    st.subheader("📊 Thống kê nghỉ phép")
    st.markdown("---")

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

    # Lọc dữ liệu
    mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
    filtered_df = df_lich[mask_date].copy()
    if selected_nv != "- Tất cả nhân viên -": filtered_df = filtered_df[filtered_df['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]

    # --- THỐNG KÊ ---
    excluded_keywords = ["đi trễ", "di tre", "không dọn vệ sinh", "khong don ve sinh", "lỗi vi phạm", "loi vi pham", "qua tour", "xuống phòng", "xuong phong", "ra sớm", "ra som", "vào muộn", "vao muon", "đi tua", "di tua", "ngưng nhận", "ngung nhan", "hỗ trợ ca", "ho tro ca"]
    def is_excluded(r): return any(kw in str(r).lower() for kw in excluded_keywords)

    if filtered_df.empty:
        df_thuc_nghi = phat_sinh_df = khong_phep_df = co_phep_df = pd.DataFrame(columns=df_lich.columns)
        tong_phat = 0.0
    else:
        df_thuc_nghi = filtered_df[~filtered_df['Lý do nghỉ'].apply(is_excluded)].copy()
        if df_thuc_nghi.empty: phat_sinh_df = khong_phep_df = co_phep_df = pd.DataFrame(columns=df_lich.columns)
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
    if not df_thuc_nghi.empty:
        daily_stats = []
        daily_limit_flags = []
        for d in sorted(filtered_df['Ngày'].dropna().unique()):
            day_df = filtered_df[filtered_df['Ngày'] == d]
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
                stat_row["💰 Tổng tiền phạt"] = f"{day_df['Phạt vi phạm'].sum():,.0f} đ".replace(",", ".")

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
        st.caption("Ô nền đỏ = đã chạm hạn mức nghỉ của ngày đó (ngày thường tối đa 5 người, cuối tuần tối đa 3; Nghỉ phát sinh tối đa 2 người và không áp dụng cuối tuần).")
    else:
        st.info("Không có dữ liệu báo nghỉ trong khoảng thời gian đã chọn.")


    st.markdown("---")

    export_df = format_display_df(filtered_df.drop(columns=cols_to_hide, errors='ignore'))
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
            raw_detail = filtered_df.drop(columns=cols_to_hide, errors='ignore').copy().reset_index(drop=True)
            editor_df = raw_detail.copy()
            editor_df.insert(0, "Chọn", False)

            disabled_cols = [c for c in ["Ngày cập nhật", "Giờ cập nhật", "Người cập nhật", "Số ngày phép cộng dồn"] if c in editor_df.columns]
            detail_editor = st.data_editor(
                editor_df,
                width="stretch",
                height="content",
                hide_index=True,
                num_rows="fixed",
                disabled=disabled_cols,
                column_config={
                    "Chọn": st.column_config.CheckboxColumn("Chọn", help="Tick 1 hoặc nhiều dòng để sửa/xóa", default=False, width="small"),
                    "Ngày": st.column_config.DateColumn("Ngày", format="DD/MM/YYYY"),
                    "Số ngày tính": st.column_config.NumberColumn("Số ngày tính", step=0.5),
                    "Phạt vi phạm": st.column_config.NumberColumn("Phạt vi phạm", step=50000, format="%.0f") if "Phạt vi phạm" in editor_df.columns else None,
                },
                key="detail_schedule_editor"
            )

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
                            original = raw_detail.iloc[pos].copy()
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
                        originals = [raw_detail.iloc[pos].copy() for pos in selected_positions]
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
                                ok, msg = delete_schedule_records(originals)
                                (st.success if ok else st.error)(msg)
                                if ok: st.rerun()
                        else:
                            ok, msg = delete_schedule_records(originals)
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
            co_display = format_display_df(co_phep_df.drop(columns=cols_to_hide, errors='ignore'))
            st.dataframe(co_display.style.map(highlight_khong_phep), width="stretch", height="content", hide_index=True)

    with tab3:
        if phat_sinh_df.empty:
            st.info("Trống.")
        else:
            ps_display = format_display_df(phat_sinh_df.drop(columns=cols_to_hide, errors='ignore'))
            st.dataframe(ps_display.style.map(highlight_khong_phep), width="stretch", height="content", hide_index=True)

    with tab4:
        if khong_phep_df.empty:
            st.success("Không có ai!")
        else:
            kp_display = format_display_df(khong_phep_df.drop(columns=cols_to_hide, errors='ignore'))
            st.dataframe(kp_display.style.map(highlight_khong_phep), width="stretch", height="content", hide_index=True)


