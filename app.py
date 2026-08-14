import streamlit as st
import pandas as pd
from datetime import date, datetime, time
import requests
import os
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH HỆ THỐNG ---
SHEET_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

@st.cache_resource
def get_gspread_client():
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None

# --- HÀM TẢI FILE `.xlsb` TỪ GOOGLE DRIVE ---
def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    token = None
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            token = value
            break
    if token:
        params = {'id': id, 'confirm': token}
        response = session.get(URL, params=params, stream=True)
    with open(destination, "wb") as f:
        for chunk in response.iter_content(32768):
            if chunk:
                f.write(chunk)

@st.cache_data(ttl=30)
def load_main_lich_nghi(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
        df_main = xls.iloc[:, :10]
        df_main.columns = [
            'Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 
            'Số ngày tính', 'Số ngày đã nghỉ trong tháng', 
            'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật'
        ]
        
        # Chuẩn hóa ngày tháng
        def safe_date_parse(val):
            try:
                if pd.isna(val): return pd.NaT
                if hasattr(val, 'date'): return val.date() 
                if isinstance(val, (int, float)): 
                    return pd.to_datetime(val, unit='D', origin='1899-12-30').date()
                s = str(val).strip().split(' ')[0]
                return pd.to_datetime(s, dayfirst=True).date()
            except:
                return pd.NaT

        df_main['Ngày'] = df_main['Ngày'].apply(safe_date_parse)
        df_main = df_main.dropna(subset=['Ngày'])
        return df_main
    except Exception as e:
        return pd.DataFrame()

@st.cache_data(ttl=10)
def get_system_data():
    client = get_gspread_client()
    if not client:
        return None, None, None, None, None
    
    try:
        sh = client.open_by_key(SHEET_ID)
        
        def read_ws(name):
            try:
                data = sh.worksheet(name).get_all_records()
                return pd.DataFrame(data) if data else pd.DataFrame()
            except:
                return pd.DataFrame()

        df_taikhoan = read_ws("TaiKhoan")
        df_loai_nghi = read_ws("LoaiNghi")
        df_config = read_ws("Config")
        df_nhanvien = read_ws("Nhanvien")
        df_backup = read_ws("LichNghi") # Sheet dự phòng trên Google Sheet
        
        # Tải dữ liệu chính từ file .xlsb trên Google Drive
        df_main_lich = load_main_lich_nghi(GDRIVE_LINK)
        
        # Hợp nhất dữ liệu chính (.xlsb) và dữ liệu dự phòng (Google Sheet LichNghi)
        if not df_backup.empty:
            # Chuẩn hóa cột dự phòng để gộp chung
            df_backup['Ngày'] = pd.to_datetime(df_backup['Ngày'], dayfirst=True, errors='coerce').dt.date
            df_backup_formatted = pd.DataFrame({
                'Ngày': df_backup['Ngày'],
                'Tên nhân viên': df_backup.get('Tên nhân viên', ''),
                'Lý do nghỉ': df_backup.get('Lý do nghỉ', ''),
                'Chi tiết': df_backup.get('Chi tiết', ''),
                'Số ngày tính': pd.to_numeric(df_backup.get('Số ngày tính', 1), errors='coerce').fillna(1),
                'Số ngày đã nghỉ trong tháng': 0,
                'Phạt vi phạm': pd.to_numeric(df_backup.get('Phạt vi phạm', 0), errors='coerce').fillna(0),
                'Ngày cập nhật': df_backup.get('Ngày tạo', ''),
                'Giờ cập nhật': '',
                'Người cập nhật': df_backup.get('Người tạo', '')
            })
            df_lich = pd.concat([df_main_lich, df_backup_formatted], ignore_index=True)
        else:
            df_lich = df_main_lich

        config_dict = {}
        if not df_config.empty and 'Key' in df_config.columns and 'Value' in df_config.columns:
            for _, row in df_config.iterrows():
                try:
                    config_dict[str(row['Key']).strip()] = int(row['Value'])
                except:
                    pass
                    
        if 'weekday_limit' not in config_dict: config_dict['weekday_limit'] = 5
        if 'weekend_limit' not in config_dict: config_dict['weekend_limit'] = 2
        if 'phat_sinh_limit' not in config_dict: config_dict['phat_sinh_limit'] = 1

        return df_taikhoan, df_loai_nghi, config_dict, df_nhanvien, df_lich
    except Exception as e:
        st.error(f"Lỗi kết nối hệ thống: {e}")
        return None, None, None, None, None

# --- TẢI DỮ LIỆU ---
df_taikhoan, df_loai_nghi, config, df_nhanvien, df_lich = get_system_data()

if df_taikhoan is None:
    st.warning("⚠️ Không thể kết nối dữ liệu. Vui lòng kiểm tra lại cấu hình.")
    st.stop()

# --- ĐĂNG NHẬP ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "current_user" not in st.session_state: st.session_state.current_user = ""
if "current_role" not in st.session_state: st.session_state.current_role = ""

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    with st.form("login_form"):
        user_in = st.text_input("Tên đăng nhập").strip()
        pwd_in = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng Nhập"):
            if user_in == "admin" and pwd_in == "32531235":
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.session_state.current_role = "admin"
                st.rerun()
            elif not df_taikhoan.empty:
                match = df_taikhoan[(df_taikhoan['Tên nhân viên'].astype(str).str.strip().str.lower() == user_in.lower()) & 
                                    (df_taikhoan['Mật khẩu'].astype(str).str.strip() == pwd_in)]
                if not match.empty:
                    st.session_state.logged_in = True
                    st.session_state.current_user = str(match.iloc[0]['Tên nhân viên'])
                    st.session_state.current_role = str(match.iloc[0].get('Phân quyền', 'nhanvien')).strip().lower()
                    st.rerun()
                else:
                    st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
            else:
                st.error("❌ Hệ thống tài khoản trống!")
    st.stop()

# --- GIAO DIỆN CHÍNH ---
col_t, col_l = st.columns([7, 3])
with col_t:
    role_label = "Quản Trị Viên" if st.session_state.current_role == "admin" else ("Lễ Tân" if st.session_state.current_role == "letan" else "Nhân Viên")
    st.title(f"📊 Tình Hình Nghỉ Phép - {st.session_state.current_user} ({role_label})")
with col_l:
    st.write("")
    if st.button("🚪 Đăng xuất", use_container_width=True):
        st.session_state.logged_in = False
        st.rerun()

st.markdown("---")

# --- NHẬP LỊCH NGHỈ (DÀNH CHO ADMIN & LỄ TÂN) ---
if st.session_state.current_role in ["admin", "letan"]:
    with st.expander("📝 Nhập lịch nghỉ mới cho nhân viên", expanded=True):
        with st.form("nhap_form"):
            nv_list = df_nhanvien['Tên nhân viên'].dropna().astype(str).tolist() if not df_nhanvien.empty and 'Tên nhân viên' in df_nhanvien.columns else []
            nv = st.selectbox("Chọn nhân viên:", ["-- Chọn nhân viên --"] + nv_list)
            ngay = st.date_input("Chọn ngày nghỉ:", date.today())
            
            ly_do_list = df_loai_nghi['Lý do nghỉ'].dropna().astype(str).tolist() if not df_loai_nghi.empty and 'Lý do nghỉ' in df_loai_nghi.columns else []
            ly_do = st.selectbox("Lý do nghỉ:", ["-- Chọn lý do --"] + ly_do_list)
            chitiet = st.text_input("Chi tiết vi phạm / Ghi chú (nếu có):").strip()
            
            phat_preview = 0.0
            if ly_do != "-- Chọn lý do --" and not df_loai_nghi.empty:
                r_match = df_loai_nghi[df_loai_nghi['Lý do nghỉ'] == ly_do]
                if not r_match.empty:
                    try: phat_preview = float(r_match.iloc[0]['Phạt vi phạm'])
                    except: phat_preview = 0.0
            st.info(f"💵 Mức phạt tham chiếu: **{phat_preview:,.0f} VNĐ**")
            
            if st.form_submit_button("💾 Xác Nhận Ghi Lịch Nghỉ"):
                if nv == "-- Chọn nhân viên --":
                    st.error("❌ Vui lòng chọn nhân viên!")
                elif ly_do == "-- Chọn lý do --":
                    st.error("❌ Vui lòng chọn lý do nghỉ!")
                else:
                    row_rule = df_loai_nghi[df_loai_nghi['Lý do nghỉ'] == ly_do].iloc[0]
                    s_ngay = float(row_rule['Số ngày tính phép']) if pd.notna(row_rule['Số ngày tính phép']) else 0.0
                    p_val = float(row_rule['Phạt vi phạm']) if pd.notna(row_rule['Phạt vi phạm']) else 0.0
                    
                    is_weekend = ngay.weekday() >= 5
                    if "phát sinh" in ly_do.lower() and datetime.now().time() < time(9, 0):
                        st.error("❌ Chỉ được phép đăng ký 'Nghỉ phát sinh' từ 09:00 sáng trở đi!")
                    elif (not is_weekend and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config.get('weekday_limit', 5)) or \
                         (is_weekend and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config.get('weekend_limit', 2)):
                        limit_val = config.get('weekday_limit', 5) if not is_weekend else config.get('weekend_limit', 2)
                        st.error(f"❌ Ngày {ngay.strftime('%d/%m/%Y')} đã đạt giới hạn tối đa {limit_val} người đăng ký nghỉ!")
                    elif "phát sinh" in ly_do.lower() and len(df_lich[(df_lich['Ngày'] == str(ngay)) & (df_lich['Lý do nghỉ'].str.contains("phát sinh", case=False))]) >= config.get('phat_sinh_limit', 1):
                        st.error(f"❌ Đã hết suất 'Nghỉ phát sinh' cho ngày này!")
                    else:
                        client = get_gspread_client()
                        client.open_by_key(SHEET_ID).worksheet("LichNghi").append_row([
                            str(ngay), nv, ly_do, chitiet, s_ngay, p_val, str(date.today()), st.session_state.current_user
                        ])
                        st.success("✅ Đã ghi nhận lịch nghỉ thành công vào kho dự phòng!")
                        st.cache_data.clear()
                        st.rerun()

# --- QUẢN LÝ QUY TẮC (ADMIN ONLY) ---
if st.session_state.current_role == "admin":
    with st.expander("⚙️ Xem & Quản lý Quy tắc / Giới hạn hệ thống"):
        st.subheader("Cấu hình giới hạn (Sheet Config)")
        st.json(config)
        st.subheader("Danh mục lý do nghỉ & mức phạt (Sheet LoaiNghi)")
        st.dataframe(df_loai_nghi, use_container_width=True, hide_index=True)

# --- HIỂN THỊ DANH SÁCH LỊCH NGHỈ ---
st.subheader("📋 Lịch Sử Nghỉ Đã Đăng Ký (Từ File Chính .xlsb & Dự Phòng)")
if not df_lich.empty:
    st.dataframe(df_lich, use_container_width=True, hide_index=True)
else:
    st.info("Chưa có dữ liệu lịch nghỉ nào được ghi nhận.")
