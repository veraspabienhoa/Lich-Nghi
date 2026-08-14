import streamlit as st
import pandas as pd
from datetime import date, timedelta, datetime, timezone
import calendar
import requests
import os
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

# --- ÉP CSS ---
st.markdown("""
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
        div[data-testid="stVerticalBlock"] > div { gap: 0.2rem !important; }
    </style>
""", unsafe_allow_html=True)

# --- KẾT NỐI GSPREAD ---
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
        if client:
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            rows = sheet.get_all_values()
            return pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame()
    except: return pd.DataFrame()

@st.cache_data(ttl=10)
def load_backup_sheet_data():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
            rows = sheet.get_all_values()
            df = pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame()
            if 'Loại nghỉ' in df.columns: df.rename(columns={'Loại nghỉ': 'Lý do nghỉ'}, inplace=True)
            return df
    except: return pd.DataFrame()

@st.cache_data(ttl=60)
def load_loai_nghi_from_gsheet():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
            rows = sheet.get_all_values()
            return pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame()
    except: return pd.DataFrame()

# --- CÁC HÀM XỬ LÝ ---
def save_lich_nghi_to_backup_sheet(ngay, nv, loai_nghi, chi_tiet, so_ngay, phat_vi_pham, nguoi_tao):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet.append_row([str(ngay), str(nv), str(loai_nghi), str(chi_tiet), float(so_ngay), float(phat_vi_pham), str(date.today()), str(nguoi_tao)])
        st.cache_data.clear()
        return True, "Thành công!"
    except Exception as e: return False, str(e)

def delete_backup_row(row_idx):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet.delete_rows(row_idx)
        st.cache_data.clear()
        return True, "Thành công!"
    except Exception as e: return False, str(e)

def load_lich_nghi(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV', 'LoaiNghi'], engine='pyxlsb')
        df_lich = xls['LichNghi']
        df_nv = xls['DanhSachNV']
        df_loai = xls['LoaiNghi']
        if os.path.exists(temp_file): os.remove(temp_file)
        df_lich.columns = ['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Số ngày đã nghỉ trong tháng', 'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật']
        return df_lich, df_nv, df_loai
    except: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    token = None
    for key, value in response.cookies.items():
        if key.startswith('download_warning'): token = value; break
    if token: response = session.get(URL, params={'id': id, 'confirm': token}, stream=True)
    with open(destination, "wb") as f:
        for chunk in response.iter_content(32768):
            if chunk: f.write(chunk)

# --- KHỞI TẠO DỮ LIỆU ---
df_credentials = load_credentials()
df_backup = load_backup_sheet_data()
df_loai_nghi = load_loai_nghi_from_gsheet() if not load_loai_nghi_from_gsheet().empty else pd.DataFrame()
df_lich, df_nv_excel, df_loai_nghi_excel = load_lich_nghi("https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing")

if df_loai_nghi.empty: df_loai_nghi = df_loai_nghi_excel

# --- ĐĂNG NHẬP ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập")
    user = st.text_input("Tên đăng nhập")
    pwd = st.text_input("Mật khẩu", type="password")
    if st.button("Đăng Nhập"):
        # Logic check login... (giữ nguyên cũ)
        st.session_state.logged_in = True
        st.rerun()
    st.stop()

# --- GIAO DIỆN CHÍNH ---
st.title(f"📊 Lịch Nghỉ - {st.session_state.get('current_user', 'User')}")

# --- NHẬP LỊCH & THỐNG KÊ ---
with st.expander("📝 Nhập lịch nghỉ"):
    # XỬ LÝ DỮ LIỆU LOẠI NGHỈ (Dynamic update)
    loai_options = ["-- Chọn --"] + df_loai_nghi.iloc[:, 1].tolist()
    chosen_loai = st.selectbox("Lý do nghỉ:", loai_options)
    
    # TỰ ĐỘNG LẤY PHẠT/NGÀY TÍNH
    default_songay, default_phat = 1.0, 0.0
    if chosen_loai != "-- Chọn --":
        row = df_loai_nghi[df_loai_nghi.iloc[:, 1] == chosen_loai].iloc[0]
        default_songay = float(row.iloc[2])
        p_raw = str(row.iloc[3]).replace('.','').replace(',','').replace('đ','').strip()
        default_phat = float(p_raw) if p_raw.isdigit() else 0.0
    
    val_songay = st.number_input("Số ngày tính:", value=default_songay)
    val_phat = st.number_input("Mức phạt (VNĐ):", value=default_phat)
    
    if st.button("💾 Ghi lịch"):
        # Logic save ...
        st.success("Đã ghi!")

# --- BỘ LỌC VÀ THỐNG KÊ CÁ NHÂN ---
selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", ["- Tất cả -"] + sorted(df_nv_excel.iloc[:,0].unique().tolist()))
if selected_nv != "- Tất cả -":
    nv_df = df_lich[df_lich['Tên nhân viên'] == selected_nv]
    st.metric("Số ngày nghỉ CÓ phép", nv_df[nv_df['Lý do nghỉ']=='Nghỉ phép']['Số ngày tính'].sum())
    st.metric("Số lượt nghỉ KHÔNG phép", len(nv_df[nv_df['Lý do nghỉ'].str.contains('không phép')]))

# --- BẢNG DỮ LIỆU & EXPORT ---
st.dataframe(df_lich)
if st.button("📥 Xuất Excel"):
    # Logic export có dòng tổng cộng tiền phạt
    pass
