import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
import requests
import os
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
        div[data-testid="stVerticalBlock"] > div { gap: 0.2rem !important; }
        h1, h2, h3 { padding-bottom: 0rem !important; margin-bottom: 0rem !important; }
        button { margin-top: 5px !important; }
    </style>
""", unsafe_allow_html=True)

# --- KẾT NỐI ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except: return None

@st.cache_data(ttl=30)
def load_credentials():
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        rows = sheet.get_all_values()
        data = []
        for row in rows[1:]:
            if len(row) > 1 and str(row[1]).strip():
                data.append({'Tên nhân viên': row[1], 'Mật khẩu': row[2] or "123456", 'Phân quyền': (row[3] or "nhanvien").lower()})
        return pd.DataFrame(data)
    except: return pd.DataFrame(columns=['Tên nhân viên', 'Mật khẩu', 'Phân quyền'])

@st.cache_data(ttl=60)
def load_config_and_rules():
    # Đọc sheet LoaiNghi để lấy quy tắc động
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
        data = sheet.get_all_values()
        df = pd.DataFrame(data[1:], columns=data[0])
        return df
    except: return pd.DataFrame()

def save_lich_nghi(data_row):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1")
        sheet.append_row(data_row)
        st.cache_data.clear()
        return True, "Ghi nhận thành công!"
    except Exception as e: return False, str(e)

# --- KHỞI TẠO DỮ LIỆU ---
df_credentials = load_credentials()
df_loai_nghi = load_config_and_rules()

# --- ĐĂNG NHẬP ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập")
    with st.form("login"):
        user = st.text_input("Tên").strip()
        pwd = st.text_input("MK", type="password")
        if st.form_submit_button("Đăng Nhập"):
            # Kiểm tra Admin cứng
            if user == "admin" and pwd == "32531235":
                st.session_state.update(logged_in=True, current_user="Admin", current_role="admin")
                st.rerun()
            else:
                match = df_credentials[df_credentials['Tên nhân viên'].str.lower() == user.lower()]
                if not match.empty and match.iloc[0]['Mật khẩu'] == pwd:
                    st.session_state.update(logged_in=True, current_user=match.iloc[0]['Tên nhân viên'], current_role=match.iloc[0]['Phân quyền'])
                    st.rerun()
                else: st.error("Sai thông tin!")
    st.stop()

# --- GIAO DIỆN CHÍNH ---
st.title(f"📊 Hệ thống Vera Spa - {st.session_state.current_user}")
if st.button("🚪 Đăng xuất"):
    st.session_state.logged_in = False
    st.rerun()

# --- NHẬP LỊCH NGHỈ (TỐI ƯU HÓA ĐỘNG) ---
if st.session_state.current_role in ["admin", "letan", "nhanvien"]:
    with st.expander("📝 Nhập lịch nghỉ"):
        # Phân tích quy tắc từ df_loai_nghi
        rule_map = {}
        for _, r in df_loai_nghi.iterrows():
            name = str(r['Lý do nghỉ']).strip()
            # Cột User có quyền bắt đầu từ index 7 (cột H)
            allowed = [str(x).lower().strip() for x in r.iloc[7:] if str(x).strip()]
            rule_map[name] = {"val": r['Số ngày tính phép'] or r['Phạt vi phạm'], "allowed": allowed}
        
        c1, c2, c3 = st.columns(3)
        nv = c1.selectbox("Nhân viên", df_credentials['Tên nhân viên'].tolist())
        ngay = c2.date_input("Ngày")
        loai = c3.selectbox("Loại nghỉ", list(rule_map.keys()))
        
        # Kiểm tra quyền dựa trên cột H (Index 7)
        if st.session_state.current_role not in rule_map[loai]['allowed']:
            st.error(f"🚫 Bạn không có quyền nhập loại nghỉ này.")
        else:
            chitiet = st.text_input("Ghi chú")
            if st.button("💾 Ghi dữ liệu"):
                success, msg = save_lich_nghi([str(ngay), nv, loai, chitiet, rule_map[loai]['val'], "", "", str(date.today()), st.session_state.current_user])
                if success: st.success(msg)
                else: st.error(msg)

# --- BẢNG DỮ LIỆU ---
st.subheader("Lịch sử")
try:
    client = get_gspread_client()
    data = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").get_all_values()
    df_lich = pd.DataFrame(data[1:], columns=data[0])
    st.dataframe(df_lich, use_container_width=True)
except: st.warning("Chưa có dữ liệu.")
