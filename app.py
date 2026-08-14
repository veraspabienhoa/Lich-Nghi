import streamlit as st
import pandas as pd
from datetime import date, datetime, time
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH ---
SHEET_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

@st.cache_resource
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        return gspread.authorize(Credentials.from_service_account_info(creds_dict))
    except: return None

@st.cache_data(ttl=30)
def get_system_data():
    client = get_gspread_client()
    sh = client.open_by_key(SHEET_ID)
    
    df_taikhoan = pd.DataFrame(sh.worksheet("TaiKhoan").get_all_records())
    df_loai_nghi = pd.DataFrame(sh.worksheet("LoaiNghi").get_all_records())
    df_config = pd.DataFrame(sh.worksheet("Config").get_all_records())
    df_nhanvien = pd.DataFrame(sh.worksheet("Nhanvien").get_all_records())
    df_lich = pd.DataFrame(sh.worksheet("LichNghi").get_all_records())
    
    config_dict = {row['Key']: int(row['Value']) for row in df_config.to_dict('records')}
    return df_taikhoan, df_loai_nghi, config_dict, df_nhanvien, df_lich

# --- ĐĂNG NHẬP ---
df_taikhoan, df_loai_nghi, config, df_nhanvien, df_lich = get_system_data()

if "logged_in" not in st.session_state: st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    with st.form("login_form"):
        user = st.text_input("Tên đăng nhập").strip()
        pwd = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng Nhập"):
            match = df_taikhoan[(df_taikhoan['Tên nhân viên'] == user) & (df_taikhoan['Mật khẩu'] == pwd)]
            if not match.empty:
                st.session_state.logged_in = True
                st.session_state.current_user = user
                st.session_state.current_role = match.iloc[0]['Phân quyền']
                st.rerun()
            else: st.error("Sai thông tin!")
    st.stop()

# --- GIAO DIỆN CHÍNH ---
st.title(f"📊 Tình Hình Nghỉ Phép - {st.session_state.current_user}")
if st.button("🚪 Đăng xuất"):
    st.session_state.logged_in = False
    st.rerun()

# --- NHẬP LỊCH NGHỈ ---
if st.session_state.current_role in ["admin", "letan"]:
    with st.expander("📝 Nhập lịch nghỉ mới"):
        with st.form("nhap_form"):
            nv = st.selectbox("Nhân viên:", df_nhanvien['Tên nhân viên'].tolist())
            ngay = st.date_input("Ngày:", date.today())
            ly_do = st.selectbox("Lý do nghỉ:", df_loai_nghi['Lý do nghỉ'].tolist())
            chitiet = st.text_input("Chi tiết:")
            
            row = df_loai_nghi[df_loai_nghi['Lý do nghỉ'] == ly_do].iloc[0]
            st.write(f"Mức phạt: {row['Phạt vi phạm']:,.0f} VNĐ")
            
            if st.form_submit_button("Lưu"):
                if "phát sinh" in ly_do.lower() and datetime.now().time() < time(9, 0):
                    st.error("Chỉ đăng ký 'Nghỉ phát sinh' sau 09:00 sáng!")
                elif (ngay.weekday() < 5 and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config.get('weekday_limit', 5)) or \
                     (ngay.weekday() >= 5 and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config.get('weekend_limit', 2)):
                    st.error("Đã đủ số người nghỉ cho ngày này!")
                else:
                    client = get_gspread_client()
                    client.open_by_key(SHEET_ID).worksheet("LichNghi").append_row([
                        str(ngay), nv, ly_do, chitiet, row['Số ngày tính phép'], row['Phạt vi phạm'], str(date.today()), st.session_state.current_user
                    ])
                    st.success("Đã ghi nhận!")
                    st.rerun()

# --- QUẢN LÝ QUY TẮC (ADMIN ONLY) ---
if st.session_state.current_role == "admin":
    with st.expander("⚙️ Quy tắc & Mức phạt (Admin)"):
        st.dataframe(df_loai_nghi, use_container_width=True)
        st.write("Cập nhật quy tắc trực tiếp trên sheet 'LoaiNghi' của file Google Sheet.")

# --- HIỂN THỊ DANH SÁCH ---
st.dataframe(df_lich, use_container_width=True)
