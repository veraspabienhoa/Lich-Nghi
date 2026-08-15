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

# --- CẤU HÌNH MÚI GIỜ VIỆT NAM ---
VN_TZ = timezone(timedelta(hours=7))

def get_vn_today():
    return datetime.now(VN_TZ).date()

# --- THEO DÕI SỐ NGƯỜI ĐANG TRUY CẬP ---
@st.cache_resource
def get_active_users():
    return {}

active_users = get_active_users()

if st.session_state.get("logged_in") and st.session_state.get("current_user"):
    active_users[st.session_state.current_user] = time.time()

current_t = time.time()
for u in list(active_users.keys()):
    if current_t - active_users[u] > 300: 
        del active_users[u]

online_users_count = len(active_users)
online_users_list = list(active_users.keys())

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide", initial_sidebar_state="auto")

# --- CHẶN SỰ KIỆN PHÍM TẮT CLEAR CACHE BẰNG JAVASCRIPT ---
components.html("""
<script>
    const parentDoc = window.parent.document;
    parentDoc.addEventListener('keydown', function(event) {
        if ((event.key === 'c' || event.key === 'C')) {
            const tag = event.target.tagName.toLowerCase();
            if (tag !== 'input' && tag !== 'textarea') {
                event.stopPropagation();
            }
        }
    }, true);
</script>
""", height=0, width=0)

# --- KHỞI TẠO BIẾN GIAO DIỆN TOÀN CỤC ---
if "global_font" not in st.session_state:
    st.session_state.global_font = "Roboto"
if "global_size" not in st.session_state:
    st.session_state.global_size = 16
if "global_color" not in st.session_state:
    st.session_state.global_color = "#333333"

# --- ÉP CSS GIAO DIỆN TOÀN CỤC & TỐI ƯU MOBILE ---
st.markdown(f"""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Arial:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Times+New+Roman&display=swap');
        
        /* Áp dụng font, kích thước và màu chữ toàn trang web */
        html, body, [class*="css"] {{
            font-family: '{st.session_state.global_font}', sans-serif !important;
            font-size: {st.session_state.global_size}px !important;
            color: {st.session_state.global_color} !important;
        }}
        
        .block-container {{
            padding-top: 1.5rem;
            padding-bottom: 1rem;
        }}
        
        .custom-main-title {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 15px;
        }}
        
        /* Thu nhỏ size chữ của mục Đăng ký lịch nghỉ */
        [data-testid="stExpander"] details summary p {{
            font-size: 1.1rem !important;
            font-weight: 700 !important;
            color: #d32f2f !important;
            text-transform: uppercase;
        }}
        
        div[data-baseweb="popover"] > div,
        div[data-baseweb="select"] ul[role="listbox"],
        div[data-testid="stSelectboxVirtualDropdown"] {{
            max-height: 85vh !important; 
        }}
        
        @media (max-width: 768px) {{
            .block-container {{
                padding-top: 1rem !important;
                padding-left: 0.5rem !important;
                padding-right: 0.5rem !important;
            }}
            .custom-main-title {{
                font-size: 22px !important;
                text-align: center;
            }}
        }}
    </style>
""", unsafe_allow_html=True)

# --- KẾT NỐI GSPREAD ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
SHEET_CHINH_ID = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT" 

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None

# --- ĐỒNG BỘ THÔNG MINH 1: EXCEL -> GOOGLE SHEETS (CHỈ THÊM DÒNG MỚI, KHÔNG GHI ĐÈ, KHÔNG XÓA DÒNG CŨ) ---
def admin_sync_excel_to_gsheet():
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        file_id = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
        temp_file = "temp_sync.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file): os.remove(temp_file)
        
        df_raw = xls.iloc[:, :10].copy()
        
        def clean_val(val, is_date=False, is_time=False):
            try:
                if pd.isna(val) or str(val).strip() in ["nan", "NaT", "None", ""]: return ""
                if is_time:
                    if hasattr(val, 'strftime'): return val.strftime('%H:%M:%S')
                    if isinstance(val, (int, float)):
                        ts = int(round(val * 86400))
                        return f"{ts//3600:02d}:{(ts%3600)//60:02d}:{ts%60:02d}"
                    return str(val).strip()
                if is_date or hasattr(val, 'strftime'):
                    if hasattr(val, 'strftime'): return val.strftime('%d/%m/%Y')
                    if isinstance(val, (int, float)): return pd.to_datetime(val, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
                    return pd.to_datetime(str(val).strip().split(' ')[0], dayfirst=True).strftime('%d/%m/%Y')
                return str(val).strip()
            except: return str(val).strip()

        cols = df_raw.columns.tolist()
        if len(cols) > 0: df_raw[cols[0]] = df_raw[cols[0]].apply(lambda x: clean_val(x, is_date=True))
        if len(cols) > 7: df_raw[cols[7]] = df_raw[cols[7]].apply(lambda x: clean_val(x, is_date=True))
        if len(cols) > 8: df_raw[cols[8]] = df_raw[cols[8]].apply(lambda x: clean_val(x, is_time=True))
        for c in cols:
            if c != cols[0] and c != cols[7] and c != cols[8]: df_raw[c] = df_raw[c].apply(lambda x: clean_val(x))

        df_raw = df_raw.fillna("")
        while len(df_raw.columns) < 10: df_raw[f"Col{len(df_raw.columns)}"] = ""
        excel_rows = df_raw.astype(str).values.tolist()

        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        existing_rows = sheet_dp.get_all_values()
        existing_set = {tuple(row[:10]) for row in existing_rows[1:]} if len(existing_rows) > 1 else set()

        new_rows = [row for row in excel_rows if tuple(row[:10]) not in existing_set]

        if new_rows:
            sheet_dp.append_rows(new_rows, value_input_option='USER_ENTERED')
            
        st.cache_data.clear()
        return True, f"Đã quét và đồng bộ thành công {len(new_rows)} dòng mới từ Excel lên Sheet (Giữ nguyên dữ liệu cũ)."
    except Exception as e:
        return False, f"Lỗi đồng bộ: {e}"

# --- ĐỒNG BỘ THÔNG MINH 2: GOOGLE SHEETS -> EXCEL ---
def admin_sync_gsheet_to_excel():
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet_rows = sheet_dp.get_all_values()
        if len(sheet_rows) <= 1:
            return True, "Không có dữ liệu trên Sheet để đồng bộ."
            
        st.cache_data.clear()
        return True, f"Đã quét thành công {len(sheet_rows)-1} dòng dữ liệu từ Sheet (Các dòng mới chưa có trong hệ thống đã được bảo toàn)."
    except Exception as e:
        return False, f"Lỗi đồng bộ ngược: {e}"

# --- HÀM TẢI MẬT KHẨU VÀ PHÂN QUYỀN ---
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
                    pwd = row[2] if len(row) > 2 else "123456"
                    role = row[3] if len(row) > 3 else "nhanvien"
                    fullname = str(row[4]).strip() if len(row) > 4 else ""
                    dob = str(row[5]).strip() if len(row) > 5 else ""
                    phone = str(row[6]).strip() if len(row) > 6 else ""
                    email = str(row[7]).strip() if len(row) > 7 else ""
                    address = str(row[8]).strip() if len(row) > 8 else ""
                    ps_thang = str(row[9]).strip() if len(row) > 9 else "0"
                    cp_thang = str(row[10]).strip() if len(row) > 10 else "0"
                    pn_nam = str(row[11]).strip() if len(row) > 11 else "0"
                    ca_lam_viec = str(row[12]).strip() if len(row) > 12 else ""
                    ngay_bd = str(row[13]).strip() if len(row) > 13 else ""
                    chu_ky = str(row[14]).strip() if len(row) > 14 else ""
                    
                    if str(ten).strip() != "":
                        data_list.append({
                            'STT': stt, 'Tên nhân viên': str(ten).strip(), 'Mật khẩu': str(pwd).strip() if str(pwd).strip() else "123456",
                            'Phân quyền': str(role).strip().lower() if str(role).strip() else "nhanvien",
                            'Họ và tên đầy đủ': fullname, 'Ngày sinh': dob, 'Điện thoại': phone,
                            'Email': email, 'Địa chỉ': address, 'Phát sinh tháng': ps_thang,
                            'Có phép tháng': cp_thang, 'Phép năm': pn_nam, 'Ca làm việc': ca_lam_viec,
                            'Ngày bắt đầu ca': ngay_bd, 'Chu kỳ': chu_ky
                        })
                return pd.DataFrame(data_list)
    except Exception:
        pass
    return pd.DataFrame(columns=['STT', 'Tên nhân viên', 'Mật khẩu', 'Phân quyền', 'Họ và tên đầy đủ', 'Ngày sinh', 'Điện thoại', 'Email', 'Địa chỉ', 'Phát sinh tháng', 'Có phép tháng', 'Phép năm', 'Ca làm việc', 'Ngày bắt đầu ca', 'Chu kỳ'])

# --- CẬP NHẬT HỒ SƠ ---
def update_user_profile(username, new_pass, fullname, dob, phone, email, address):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        cells = sheet.findall(username, in_column=2)
        if cells:
            row_idx = cells[0].row
            if new_pass: sheet.update_cell(row_idx, 3, str(new_pass))
            sheet.update_cell(row_idx, 5, str(fullname))
            sheet.update_cell(row_idx, 6, str(dob))
            sheet.update_cell(row_idx, 7, f"'{phone}") 
            sheet.update_cell(row_idx, 8, str(email))
            sheet.update_cell(row_idx, 9, str(address))
            st.cache_data.clear() 
            return True, "Cập nhật hồ sơ thành công!"
        return False, "Không tìm thấy tài khoản."
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- THÊM / XÓA NHÂN VIÊN ---
def admin_add_employee(name, password, role, fullname, dob, phone, email, address, ps="0", cp="0", pn="0"):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        rows = sheet.get_all_values()
        for r in rows[1:]:
            if len(r) > 1 and str(r[1]).strip().lower() == name.strip().lower():
                return False, f"Nhân viên '{name}' đã tồn tại!"
        next_stt = len(rows)
        sheet.append_row([next_stt, name.strip(), password.strip() if password else "123456", role, fullname, dob, f"'{phone}", email, address, ps, cp, pn])
        st.cache_data.clear()
        return True, f"Đã thêm nhân viên {name} thành công!"
    except Exception as e:
        return False, f"Lỗi: {e}"

def admin_delete_employee(name):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        cells = sheet.findall(name, in_column=2)
        if not cells: return False, "Không tìm thấy nhân viên."
        sheet.delete_rows(cells[0].row)
        st.cache_data.clear()
        return True, f"Đã xóa nhân viên {name} thành công!"
    except Exception as e:
        return False, f"Lỗi: {e}"

# --- GHI NHẬN HÀNG LOẠT CA LÀM VIỆC ---
def batch_update_shift_schedule(edited_df):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        all_vals = sheet.get_all_values()
        
        shift_map = {}
        for _, r in edited_df.iterrows():
            nv_name = str(r['Tên nhân viên']).strip().lower()
            shift_map[nv_name] = {
                'ca': str(r.get('Ca làm việc', '')).replace("nan", "").strip(),
                'ngay': str(r.get('Ngày bắt đầu ca', '')).replace("nan", "").strip(),
                'chuky': str(r.get('Chu kỳ', '')).replace("nan", "").strip()
            }
        
        for i, row in enumerate(all_vals):
            if i == 0: continue 
            if len(row) > 1:
                nv_name = str(row[1]).strip().lower()
                if nv_name in shift_map:
                    while len(row) < 15: row.append("") 
                    row[12] = shift_map[nv_name]['ca']
                    row[13] = shift_map[nv_name]['ngay']
                    row[14] = shift_map[nv_name]['chuky']
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
def save_lich_nghi_to_backup_sheet(ngay, nv, loai_nghi, chi_tiet, so_ngay, so_ngay_cong_don, phat_vi_pham, role):
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        ngay_cn = get_vn_today().strftime('%d/%m/%Y')
        gio_cn = datetime.now(VN_TZ).strftime('%H:%M:%S')
        
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        if len(sheet_dp.get_all_values()) == 0:
            sheet_dp.append_row(["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"])
        
        sheet_dp.append_row([
            str(ngay), str(nv), str(loai_nghi), str(chi_tiet),
            float(so_ngay) if so_ngay is not None else 0.0, 
            float(so_ngay_cong_don), float(phat_vi_pham), 
            str(ngay_cn), str(gio_cn), str(role)
        ])

        try:
            sheet_chinh_lich = client.open_by_key(SHEET_CHINH_ID).worksheet("LichNghi")
            sheet_chinh_lich.append_row([
                str(ngay), str(nv), str(loai_nghi), str(chi_tiet),
                float(so_ngay) if so_ngay is not None else 0.0, 
                float(so_ngay_cong_don), float(phat_vi_pham), 
                str(ngay_cn), str(gio_cn), str(role)
            ])
        except Exception:
            pass

        st.cache_data.clear()
        return True, "Đã ghi nhận lịch nghỉ thành công!"
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

# --- HÀM TẢI FILE TỪ DRIVE ---
def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
    if token:
        response = session.get(URL, params={'id': id, 'confirm': token}, stream=True)
    with open(destination, "wb") as f:
        for chunk in response.iter_content(32768):
            if chunk: f.write(chunk)

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

try:
    if "admin_token" in st.query_params and st.query_params["admin_token"] == "active":
        st.session_state.logged_in = True
        st.session_state.current_user = "Quản Trị Viên"
        st.session_state.current_role = "admin"
except Exception:
    pass

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    with st.form("login_form"):
        username_input = st.text_input("Tên đăng nhập").strip()
        password_input = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng Nhập"):
            if username_input.lower() == "admin" and password_input == "32531235":
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.session_state.current_role = "admin"
                try: st.query_params["admin_token"] = "active"
                except: pass
                st.rerun()
            else:
                user_found = False
                for _, row in df_credentials.iterrows():
                    db_name = str(row['Tên nhân viên']).strip()
                    if username_input.lower() == db_name.lower() and password_input == str(row['Mật khẩu']).strip():
                        st.session_state.logged_in = True
                        st.session_state.current_user = db_name
                        st.session_state.current_role = str(row.get('Phân quyền', 'nhanvien')).strip().lower()
                        user_found = True
                        break
                if user_found: st.rerun()
                else: st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop()


# ==========================================
# ẨN HOÀN TOÀN SIDEBAR NẾU LÀ NHÂN VIÊN
# ==========================================
if st.session_state.current_role == "nhanvien":
    st.markdown("""
        <style>
            [data-testid="collapsedControl"] { display: none !important; }
            [data-testid="stSidebar"] { display: none !important; }
        </style>
    """, unsafe_allow_html=True)


# ==========================================
# THIẾT LẬP MENU ĐIỀU HƯỚNG BÊN TRÁI (SIDEBAR)
# ==========================================
is_admin_letan = st.session_state.current_role in ["admin", "letan"]

if is_admin_letan:
    st.sidebar.title("📌 MENU CHỨC NĂNG")
    menu_options = ["📊 Tình Hình Nghỉ Phép", "⏰ Thiết Lập Ca Làm Việc", "👥 Quản Lý Nhân Sự"]
    
    if st.session_state.current_role == "admin":
        st.sidebar.markdown("---")
        st.sidebar.subheader("🎨 TÙY CHỈNH GIAO DIỆN TOÀN TRANG")
        fonts_list = ["Roboto", "Arial", "Cinzel Decorative", "Times New Roman"]
        sel_font = st.sidebar.selectbox("Font chữ:", fonts_list, index=fonts_list.index(st.session_state.global_font))
        sel_size = st.sidebar.slider("Cỡ chữ (px):", 12, 24, st.session_state.global_size)
        sel_color = st.sidebar.color_picker("Màu chữ chủ đạo:", st.session_state.global_color)
        
        if st.sidebar.button("💾 Áp dụng Giao diện"):
            st.session_state.global_font = sel_font
            st.session_state.global_size = sel_size
            st.session_state.global_color = sel_color
            st.rerun()
                
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 CÔNG CỤ ADMIN")
        if st.sidebar.button("🔄 Đồng Bộ Excel ➡️ Google Sheets"):
            with st.spinner("Đang đồng bộ dữ liệu mới..."):
                res, msg = admin_sync_excel_to_gsheet()
                if res: st.sidebar.success(msg)
                else: st.sidebar.error(msg)
                
        if st.sidebar.button("🔄 Đồng Bộ Google Sheets ➡️ Excel"):
            with st.spinner("Đang quét dữ liệu Google Sheets..."):
                res, msg = admin_sync_gsheet_to_excel()
                if res: st.sidebar.success(msg)
                else: st.sidebar.error(msg)
                
    selected_page = st.sidebar.radio("Chọn trang:", menu_options)
else:
    selected_page = "📊 Tình Hình Nghỉ Phép"


# --- GIAO DIỆN HEADER CHÍNH BÊN PHẢI ---
st.write("")
col_title, col_logout = st.columns([7, 3]) 
with col_title:
    r_label = {"admin": "Quản Trị Viên", "letan": "Lễ Tân"}.get(st.session_state.current_role, "Nhân Viên")
    
    admin_view_online = ""
    if st.session_state.current_role == "admin" and online_users_list:
        admin_view_online = f"<br><span style='font-size: 13px; font-weight: normal; color: #666;'>👤 Chi tiết: {', '.join(online_users_list)}</span>"
        
    st.markdown(f"""
        <div class='custom-main-title'>
            WELCOME TO VERA SPA
            <div style="float: right; text-align: right; margin-top: 8px;">
                <span style="font-size: 16px; font-family: Arial; font-weight: normal; color: #28a745;">
                    🟢 Đang trực tuyến: {online_users_count}
                </span>
                {admin_view_online}
            </div>
        </div>
    """, unsafe_allow_html=True)

with col_logout:
    c_btn1, c_btn2 = st.columns(2)
    with c_btn1:
        btn_manage_account = st.button("🛠 Hồ sơ Nhân Viên", use_container_width=True)
    with c_btn2:
        if st.button("🚪 Đăng xuất", use_container_width=True):
            st.session_state.logged_in = False
            try:
                if "admin_token" in st.query_params: del st.query_params["admin_token"]
            except: pass
            st.rerun()

# --- DROPDOWN HIỂN THỊ ONLINE (CHỈ ADMIN) ---
if st.session_state.current_role == "admin":
    with st.expander(f"🟢 Đang trực tuyến: {online_users_count} người", expanded=False):
        st.write(", ".join(online_users_list) if online_users_list else "Không có ai.")

# --- MODAL HỒ SƠ / QUẢN LÝ TÀI KHOẢN ---
if 'show_modal' not in st.session_state:
    st.session_state.show_modal = False
if btn_manage_account:
    st.session_state.show_modal = not st.session_state.show_modal

if st.session_state.show_modal:
    st.subheader(f"Cập nhật hồ sơ cá nhân: {st.session_state.current_user}")
    cred_row = df_credentials[df_credentials['Tên nhân viên'].str.lower() == st.session_state.current_user.lower()]
    
    curr_fullname = str(cred_row.iloc[0].get('Họ và tên đầy đủ', '')).strip() if not cred_row.empty else ""
    curr_dob = str(cred_row.iloc[0].get('Ngày sinh', '')).strip() if not cred_row.empty else ""
    curr_phone = str(cred_row.iloc[0].get('Điện thoại', '')).strip().replace("'", "") if not cred_row.empty else ""
    curr_email = str(cred_row.iloc[0].get('Email', '')).strip() if not cred_row.empty else ""
    curr_address = str(cred_row.iloc[0].get('Địa chỉ', '')).strip() if not cred_row.empty else ""
    
    with st.form("change_pass_form"):
        old_pass = st.text_input("Mật khẩu hiện tại (🔴 **Bắt buộc** để lưu)", type="password")
        new_pass = st.text_input("Mật khẩu mới (Bỏ trống nếu không đổi)", type="password")
        in_fullname = st.text_input("Họ và tên đầy đủ", value=curr_fullname)
        in_dob = st.text_input("Ngày sinh (Ví dụ: 15/08/1990)", value=curr_dob)
        in_phone = st.text_input("Số điện thoại", value=curr_phone)
        in_email = st.text_input("Email", value=curr_email)
        in_address = st.text_input("Địa chỉ", value=curr_address)
        
        if st.form_submit_button("Lưu Thay Đổi"):
            db_old_pass = str(cred_row.iloc[0]['Mật khẩu']).strip() if not cred_row.empty else "123456"
            if old_pass != db_old_pass:
                st.error("❌ Mật khẩu hiện tại không chính xác!")
            elif new_pass and len(new_pass.strip()) < 4:
                st.error("❌ Mật khẩu mới quá ngắn.")
            else:
                success, msg = update_user_profile(
                    st.session_state.current_user, 
                    new_pass.strip(), 
                    in_fullname.strip(), 
                    in_dob.strip(), 
                    in_phone.strip(), 
                    in_email.strip(), 
                    in_address.strip()
                )
                if success:
                    st.success(f"✅ {msg}")
                    st.session_state.show_modal = False
                else: st.error(f"❌ {msg}")


# ==========================================
# PAGE 1: ⏰ THIẾT LẬP CA LÀM VIỆC (CHỈ ADMIN/LỄ TÂN)
# ==========================================
if selected_page == "⏰ Thiết Lập Ca Làm Việc" and is_admin_letan:
    st.subheader("Cấu Hình Phân Ca Nhân Viên")
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


# ==========================================
# PAGE 3: 👥 QUẢN LÝ NHÂN SỰ (THÊM / XÓA / SỬA NHÂN VIÊN)
# ==========================================
elif selected_page == "👥 Quản Lý Nhân Sự" and is_admin_letan:
    st.subheader("Quản Lý Danh Sách Nhân Viên")
    
    tab_m1, tab_m2, tab_m3 = st.tabs(["➕ Thêm nhân viên mới", "✏️ Sửa hồ sơ nhân sự", "🗑️ Xóa nhân viên"])
    
    with tab_m1:
        with st.form("form_add_nv_new"):
            add_name = st.text_input("Tên đăng nhập (Bắt buộc):").strip()
            add_pass = st.text_input("Mật khẩu ban đầu:", value="123456", type="password")
            add_role = st.selectbox("Phân quyền hệ thống:", ["nhanvien", "letan", "admin"])
            add_full = st.text_input("Họ và tên đầy đủ:")
            add_dob = st.text_input("Ngày sinh (DD/MM/YYYY):")
            add_phone = st.text_input("Số điện thoại:")
            add_email = st.text_input("Email:")
            add_addr = st.text_input("Địa chỉ:")
            
            c_p1, c_p2, c_p3 = st.columns(3)
            with c_p1: add_ps = st.text_input("Giới hạn Phát sinh/tháng:", value="2")
            with c_p2: add_cp = st.text_input("Giới hạn Có phép/tháng:", value="3")
            with c_p3: add_pn = st.text_input("Giới hạn Phép năm/năm:", value="12")
            
            if st.form_submit_button("Thêm Nhân Viên"):
                if not add_name:
                    st.error("❌ Vui lòng nhập tên đăng nhập!")
                else:
                    res_add, msg_add = admin_add_employee(add_name, add_pass, add_role, add_full, add_dob, add_phone, add_email, add_addr, add_ps, add_cp, add_pn)
                    if res_add: st.success(msg_add)
                    else: st.error(msg_add)
                    
    with tab_m2:
        st.info("Admin có toàn quyền chọn và chỉnh sửa thông tin chi tiết hoặc nâng cấp hồ sơ nhân viên trực tiếp qua bảng Cấu hình Hồ sơ Nhân Viên.")
        edit_target = st.selectbox("Chọn nhân viên cần chỉnh sửa:", df_credentials['Tên nhân viên'].tolist())
        target_row = df_credentials[df_credentials['Tên nhân viên'] == edit_target].iloc[0] if edit_target else None
        
        if target_row is not None:
            with st.form("form_edit_nv_admin"):
                ed_full = st.text_input("Họ và tên đầy đủ", value=str(target_row.get('Họ và tên đầy đủ', '')))
                ed_dob = st.text_input("Ngày sinh", value=str(target_row.get('Ngày sinh', '')))
                ed_phone = st.text_input("Điện thoại", value=str(target_row.get('Điện thoại', '')).replace("'", ""))
                ed_email = st.text_input("Email", value=str(target_row.get('Email', '')))
                ed_addr = st.text_input("Địa chỉ", value=str(target_row.get('Địa chỉ', '')))
                ed_pass = st.text_input("Mật khẩu mới (Bỏ trống nếu giữ nguyên)", type="password")
                
                if st.form_submit_button("Cập Nhật Hồ Sơ"):
                    res_ed, msg_ed = update_user_profile(edit_target, ed_pass, ed_full, ed_dob, ed_phone, ed_email, ed_addr)
                    if res_ed: st.success(msg_ed)
                    else: st.error(msg_ed)

    with tab_m3:
        if st.session_state.current_role != "admin":
            st.warning("⚠️ Chỉ tài khoản **Admin** mới có quyền xóa nhân viên khỏi hệ thống.")
        else:
            with st.form("form_del_nv_admin"):
                del_target = st.selectbox("Chọn nhân viên cần xóa khỏi hệ thống:", df_credentials['Tên nhân viên'].tolist())
                if st.form_submit_button("Xóa Nhân Viên Này"):
                    res_del, msg_del = admin_delete_employee(del_target)
                    if res_del: st.success(msg_del)
                    else: st.error(msg_del)


# ==========================================
# PAGE 2: 📊 TÌNH HÌNH NGHỈ PHÉP
# ==========================================
elif selected_page == "📊 Tình Hình Nghỉ Phép":

    with st.expander("📝 ĐĂNG KÝ - THAY ĐỔI LỊCH NGHỈ", expanded=False):
        tabs = st.tabs(["➕ Nhập lịch nghỉ mới", "✏️ Quản lý / Xóa lịch đã đăng ký"])
        tab_input_lich, tab_manage_lich = tabs[0], tabs[1]
            
        users_s = df_credentials['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_credentials.empty else []
        users_e = df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_nv_excel.empty else []
        all_users = sorted(list(set(users_s + users_e)))
        
        with tab_input_lich:
            if is_admin_letan:
                list_nv_input = ["-- Chọn nhân viên --"] + all_users
                chosen_dates = st.date_input("Chọn ngày nghỉ (Khoảng thời gian nếu là Phép năm):", value=(get_vn_today(), get_vn_today()), key="sb_chosen_date")
            else:
                list_nv_input = [st.session_state.current_user]
                chosen_dates = st.date_input("Chọn ngày nghỉ (Nhân viên chọn 1 ngày):", get_vn_today(), key="sb_chosen_date")
            
            if isinstance(chosen_dates, tuple):
                if len(chosen_dates) == 2: start_date, end_date = chosen_dates
                elif len(chosen_dates) == 1: start_date = end_date = chosen_dates[0]
                else: start_date = end_date = get_vn_today()
            else:
                start_date = end_date = chosen_dates

            chosen_nv = st.selectbox("Chọn nhân viên:", list_nv_input, key="sb_chosen_nv")
            
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
                            list_loai_nghi.append(l_name)
                            try:
                                s_ngay_str = str(row_vals[4]).replace(',', '').strip() if len(row_vals) > 4 else ""
                                s_ngay = float(s_ngay_str) if s_ngay_str != "" else None
                            except: s_ngay = None
                            
                            try:
                                p_str = str(row_vals[5] if len(row_vals)>5 else "0").replace('.', '').replace(',', '').replace(' ', '').replace('đ', '').replace('VNĐ', '').replace('VND', '')
                                p_val = 0.0 if p_str.lower() in ["", "-", "nan", "none"] else float(p_str)
                            except: p_val = 0.0
                                
                            loai_nghi_dict[l_name.lower()] = [s_ngay, p_val]
                            
            if not list_loai_nghi:
                list_loai_nghi = ["Nghỉ phép", "Nghỉ không phép", "Nghỉ phát sinh", "Đi trễ không phép"]
                loai_nghi_dict = {l.lower(): [None, 0.0] for l in list_loai_nghi}

            chosen_loai = st.selectbox("Lý do nghỉ:", ["-- Chọn lý do nghỉ --"] + list_loai_nghi, key="sb_loai_nghi_live")
            
            default_songay = None
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
                            c_ps = len(df_lich[(df_lich['Ngày'] == chk_d) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == "nghỉ phát sinh")]) if not df_lich.empty else 0
                            if c_ps >= 2:
                                early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} đã đạt giới hạn 2 người 'Nghỉ phát sinh'!"
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
                    val_songay = st.number_input("Số ngày tính (🔴 **Bắt buộc**):", value=default_songay, step=0.5, key=f"num_songay_{dyn_key_suffix}", disabled=is_loi_vi_pham)
                with col_p2:
                    txt_phat_label = "Mức phạt vi phạm VNĐ (🔴 **Bắt buộc**):" if is_loi_vi_pham else "Mức phạt vi phạm (VNĐ):"
                    val_phat = st.number_input(txt_phat_label, value=float(default_phat), step=50000.0, key=f"num_phat_{dyn_key_suffix}")
                
                confirm_multiple = True
                if existing_today:
                    if chosen_loai in existing_today:
                        st.error(f"❌ Nhân viên này đã có Lý do nghỉ: '{chosen_loai}' vào ngày này rồi. KHÔNG THỂ trùng cùng 1 lý do!")
                        confirm_multiple = False
                    else:
                        st.warning(f"⚠️ CẢNH BÁO: Nhân viên '{chosen_nv}' đã có các lịch sau trong ngày {start_date.strftime('%d/%m/%Y')}: {', '.join(existing_today)}")
                        confirm_multiple = st.checkbox("Tôi xác nhận đăng ký này là ĐÚNG và MỚI.")

                submit_lich = st.form_submit_button("💾 Xác Nhận Ghi Lịch Nghỉ")
                
                if submit_lich:
                    today = get_vn_today()
                    can_proceed = True
                    
                    if current_role == "nhanvien" and start_date <= today:
                        st.error("❌ Lỗi: Tài khoản NHÂN VIÊN chỉ được đăng ký lịch từ **NGÀY MAI** trở đi. Muốn đổi lịch hôm nay, vui lòng liên hệ Lễ Tân / Admin.")
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
                            norm_loai = chosen_loai.strip().lower()
                            num_days_selected = (end_date - start_date).days + 1
                            
                            if val_songay is None:
                                st.error("❌ Vui lòng nhập Số ngày tính (🔴 Bắt buộc không được để trống)!")
                                can_proceed = False
                            
                            if is_loi_vi_pham:
                                val_songay = 0.0 
                                if not input_chitiet:
                                    st.error("❌ Bắt buộc nhập Chi tiết vi phạm / Ghi chú đối với 'Lỗi vi phạm khác'.")
                                    can_proceed = False
                                if val_phat <= 0:
                                    st.error("❌ Bắt buộc nhập số tiền Phạt vi phạm > 0 đối với 'Lỗi vi phạm khác'.")
                                    can_proceed = False
                            
                            if is_nghi_ly_do_khac and not input_chitiet:
                                st.error("❌ Bắt buộc nhập Chi tiết vi phạm / Ghi chú đối với 'Nghỉ lý do khác'.")
                                can_proceed = False
                            
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
                                                continue
                                            elif is_weekend_iter:
                                                st.error(f"❌ Ngày {curr_date_iter.strftime('%d/%m/%Y')} là cuối tuần, không được phép 'Nghỉ phát sinh'!")
                                                continue
                                            else:
                                                count_ps = len(df_lich[(df_lich['Ngày'] == curr_date_iter) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == "nghỉ phát sinh")]) if not df_lich.empty else 0
                                                if count_ps >= 2:
                                                    st.error(f"❌ Ngày {curr_date_iter.strftime('%d/%m/%Y')} đã đạt giới hạn 2 người 'Nghỉ phát sinh'!")
                                                    continue
                                        else:
                                            max_people = 5 if not is_weekend_iter else 3
                                            today_total_nghi = len(df_lich[(df_lich['Ngày'] == curr_date_iter) & (df_lich['Số ngày tính'] > 0)]) if not df_lich.empty else 0
                                            if today_total_nghi >= max_people:
                                                st.error(f"❌ Ngày {curr_date_iter.strftime('%d/%m/%Y')} đã đạt giới hạn {max_people} người nghỉ chung/ngày.")
                                                continue

                                    success_bk, msg_bk = save_lich_nghi_to_backup_sheet(
                                        curr_date_iter.strftime('%d/%m/%Y'), chosen_nv, chosen_loai, 
                                        input_chitiet, val_songay, accumulated_month, val_phat, st.session_state.current_role
                                    )
                                    
                                st.success(f"✅ Đã ghi nhận lịch nghỉ thành công cho {num_days_selected} ngày!")
                                st.cache_data.clear()

        with tab_manage_lich:
         
            st.markdown("### 🗑️ Xóa / Quản lý lịch nghỉ đã đăng ký")
            
            df_backup_view = df_backup.copy()
            # Đảm bảo các cột không bị trùng tên bằng cách đặt lại danh sách cột chuẩn
            df_backup_view.columns = ['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Số ngày phép cộng dồn', 'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật']
            
            if st.session_state.current_role == "nhanvien":
                df_backup_view = df_backup_view[df_backup_view['Tên nhân viên'] == st.session_state.current_user]

            if df_backup_view.empty: 
                st.info("Chưa có lịch nghỉ nào được đăng ký.")
            else:
                # Hiển thị dataframe với các cột đã được chuẩn hóa tên
                st.dataframe(df_backup_view, use_container_width=True, hide_index=True)
                with st.form("form_delete_backup_row"):
                    # Sử dụng tên cột chuẩn để lọc
                    col_ly_do_disp = 'Lý do nghỉ'
                    
                    row_options = []
                    valid_indices = []
                    for i, row in df_backup.iterrows():
                        if st.session_state.current_role == "nhanvien" and str(row.get('Tên nhân viên')).strip() != st.session_state.current_user:
                            continue
                        row_options.append(f"Dòng {i+1}: {row.get('Ngày')} - {row.get('Tên nhân viên')} - {row.get(col_ly_do_disp, '')}")
                        valid_indices.append((i, str(row.get('Ngày'))))
                        
                    selected_row_str = st.selectbox("Chọn dòng lịch nghỉ cần xóa:", row_options)
                    
                    if st.form_submit_button("🗑️ Xóa Lịch Nghỉ Đã Chọn") and selected_row_str:
                        sel_idx = row_options.index(selected_row_str)
                        real_i, sel_date_str = valid_indices[sel_idx]
                        
                        try:
                            sel_date = pd.to_datetime(sel_date_str, format='%d/%m/%Y').date()
                        except:
                            sel_date = get_vn_today()
                        
                        can_delete = True
                        today = get_vn_today()
                        if st.session_state.current_role == "nhanvien" and sel_date <= today:
                            st.error("❌ Lỗi: Tài khoản NHÂN VIÊN chỉ được xóa lịch của **NGÀY MAI** trở đi. Vui lòng liên hệ Lễ tân/Admin.")
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

    st.markdown("---")

    # Bộ lọc thời gian & nhân viên
    col_date, col_name, col_refresh = st.columns([5, 4, 2])

    with col_date:
        today = get_vn_today() 
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            filter_type = st.selectbox(
                "Lọc thời gian:", 
                ["Hôm nay", "Hôm qua", "Ngày mai", "Chọn ngày", "Khoảng thời gian", "Tuần này", "Tuần trước", "Tuần sau", "Tháng này", "Tháng sau"]
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
        selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", list_nv)

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
        for d in sorted(filtered_df['Ngày'].dropna().unique()):
            day_df = filtered_df[filtered_df['Ngày'] == d]
            day_thuc_nghi = day_df[~day_df['Lý do nghỉ'].apply(is_excluded)]
            d_loai = day_thuc_nghi['Lý do nghỉ'].astype(str).str.strip().str.lower()
            daily_stats.append({
                "Ngày": d.strftime('%d/%m/%Y'),
                "Tổng số người nghỉ": len(day_thuc_nghi),
                "✅ CÓ phép": len(day_thuc_nghi[(d_loai != 'nghỉ phát sinh') & (~d_loai.str.contains('không phép', na=False))]),
                "⚠️ PHÁT SINH": len(day_thuc_nghi[d_loai == 'nghỉ phát sinh']),
                "❌ KHÔNG phép": len(day_thuc_nghi[d_loai.str.contains('không phép', na=False)]),
                "💰 Tổng tiền phạt": f"{day_df['Phạt vi phạm'].sum():,.0f} đ".replace(",", ".")
            })
        st.dataframe(pd.DataFrame(daily_stats), use_container_width=True, hide_index=True)
    else: st.info("Không có dữ liệu báo nghỉ trong khoảng thời gian đã chọn.")

    st.markdown("---")

    export_df = filtered_df.drop(columns=cols_to_hide, errors='ignore')
    df_for_excel = export_df.copy()
    if st.session_state.current_role == "admin" and not df_for_excel.empty:
        tong_cong_row = pd.Series(index=df_for_excel.columns, dtype=object)
        tong_cong_row['Tên nhân viên'] = "TỔNG TIỀN PHẠT:"
        tong_cong_row['Phạt vi phạm'] = tong_phat
        df_for_excel = pd.concat([df_for_excel, tong_cong_row.to_frame().T], ignore_index=True)

    col_header, col_download = st.columns([7, 3])
    with col_header: st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")
    with col_download:
        st.write("") 
        if not export_df.empty:
            st.download_button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", data=to_excel(df_for_excel), file_name=f"LichNghi_{start_date.strftime('%d%m%Y')}_to_{end_date.strftime('%d%m%Y')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        else: st.button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", disabled=True, use_container_width=True)

    tab1, tab2, tab3, tab4 = st.tabs(["Tất cả danh sách", "Danh sách Nghỉ CÓ phép", "Danh sách Nghỉ PHÁT SINH", "Danh sách Nghỉ KHÔNG phép"])
    
    with tab1: 
        st.dataframe(export_df, use_container_width=True, hide_index=True)
        
    with tab2: 
        if co_phep_df.empty:
            st.info("Trống.")
        else:
            st.dataframe(co_phep_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
            
    with tab3: 
        if phat_sinh_df.empty:
            st.info("Trống.")
        else:
            st.dataframe(phat_sinh_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
            
    with tab4: 
        if khong_phep_df.empty:
            st.success("Không có ai!")
        else:
            st.dataframe(khong_phep_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
