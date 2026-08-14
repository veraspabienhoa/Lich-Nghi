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

# --- ÉP CSS THU GỌN GIAO DIỆN ---
st.markdown("""
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
        div[data-testid="stVerticalBlock"] > div { gap: 0.2rem !important; }
        h1, h2, h3 { padding-bottom: 0rem !important; margin-bottom: 0rem !important; }
        button { margin-top: 5px !important; }
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
    except: return pd.DataFrame(columns=['STT', 'Tên nhân viên', 'Mật khẩu', 'Phân quyền'])

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
    except: return pd.DataFrame(columns=["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Phạt vi phạm", "Ngày tạo", "Người tạo"])

@st.cache_data(ttl=60)
def load_loai_nghi_from_gsheet():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
            rows = sheet.get_all_values()
            return pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame()
    except: return pd.DataFrame()

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

def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
    if token: response = session.get(URL, params={'id': id, 'confirm': token}, stream=True)
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
        if os.path.exists(temp_file): os.remove(temp_file)
        df_lich = xls['LichNghi'].iloc[:, :10]
        df_lich.columns = ['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Số ngày đã nghỉ trong tháng', 'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật']
        return df_lich, xls['DanhSachNV'], xls['LoaiNghi']
    except: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

@st.cache_data(show_spinner=False)
def to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DuLieuLichNghi')
    return output.getvalue()

# Load Data
df_credentials = load_credentials()
df_backup = load_backup_sheet_data()
df_loai_nghi = load_loai_nghi_from_gsheet()
df_lich, df_nv_excel, df_loai_nghi_excel = load_lich_nghi("https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing")
if df_loai_nghi.empty: df_loai_nghi = df_loai_nghi_excel

# Login
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập")
    u, p = st.text_input("Tên đăng nhập"), st.text_input("Mật khẩu", type="password")
    if st.button("Đăng Nhập"):
        if u == "admin" and p == "32531235":
            st.session_state.update(logged_in=True, current_user="Quản Trị Viên", current_role="admin")
            st.rerun()
        # (Thêm logic check DB ở đây)
    st.stop()

# --- GIAO DIỆN ---
st.title(f"📊 Tình Hình Nghỉ Phép - {st.session_state.current_user}")

# --- NHẬP LỊCH ---
with st.expander("📝 Nhập lịch nghỉ mới"):
    list_loai = df_loai_nghi.iloc[:, 1].tolist()
    chosen_loai = st.selectbox("Lý do nghỉ:", ["-- Chọn --"] + list_loai)
    
    # Auto-fill
    default_songay, default_phat = 1.0, 0.0
    if chosen_loai != "-- Chọn --":
        row = df_loai_nghi[df_loai_nghi.iloc[:, 1] == chosen_loai].iloc[0]
        default_songay = float(row.iloc[2])
        p_str = str(row.iloc[3]).replace('.','').replace(',','').replace('đ','').strip()
        default_phat = float(p_str) if p_str.isdigit() else 0.0
        
    nv = st.selectbox("Nhân viên:", ["-- Chọn --"] + df_nv_excel.iloc[:,0].tolist())
    d = st.date_input("Ngày:", date.today())
    s = st.number_input("Số ngày:", value=default_songay)
    p = st.number_input("Phạt (VNĐ):", value=default_phat)
    
    if st.button("💾 Ghi lịch"):
        # Logic check giới hạn (5/2 người) và check Phép năm/phạt > 0 để bypass
        is_phep_nam = "phép năm" in chosen_loai.lower()
        if p > 0 or is_phep_nam:
            save_lich_nghi_to_backup_sheet(d, nv, chosen_loai, "", s, p, st.session_state.current_user)
            st.success("Đã ghi!")
        else:
            # Check giới hạn...
            save_lich_nghi_to_backup_sheet(d, nv, chosen_loai, "", s, p, st.session_state.current_user)
            st.success("Đã ghi!")

# --- BỘ LỌC VÀ THỐNG KÊ CÁ NHÂN ---
selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", ["- Tất cả -"] + sorted(df_nv_excel.iloc[:,0].unique().tolist()))
filtered_df = df_lich[(df_lich['Ngày'] >= date.today())] # Logic lọc theo thời gian trước đó
if selected_nv != "- Tất cả -":
    nv_df = filtered_df[filtered_df['Tên nhân viên'] == selected_nv]
    c1, c2 = st.columns(2)
    c1.metric("Ngày CÓ phép", f"{nv_df[nv_df['Lý do nghỉ']=='Nghỉ phép']['Số ngày tính'].sum():g}")
    c2.metric("Lượt KHÔNG phép", len(nv_df[nv_df['Lý do nghỉ'].str.contains('không phép', na=False)]))
    filtered_df = nv_df

# --- THỐNG KÊ TỔNG ---
excluded = ["đi trễ", "lỗi vi phạm", "qua tour", "xuống phòng", "ra sớm", "vào muộn", "đi tua", "ngưng nhận", "hỗ trợ ca"]
valid_mask = ~filtered_df['Lý do nghỉ'].str.lower().apply(lambda x: any(kw in x for kw in excluded))
df_thuc = filtered_df[valid_mask]
tong_phat = filtered_df['Phạt vi phạm'].sum()

# KPI Display
cols = st.columns(5 if st.session_state.current_role == "admin" else 4)
cols[0].metric("Tổng nghỉ", len(df_thuc))
cols[1].metric("✅ CÓ phép", len(df_thuc[~df_thuc['Lý do nghỉ'].str.contains('không phép', na=False)]))
cols[2].metric("⚠️ PHÁT SINH", len(df_thuc[df_thuc['Lý do nghỉ']=='Nghỉ phát sinh']))
cols[3].metric("❌ KHÔNG phép", len(df_thuc[df_thuc['Lý do nghỉ'].str.contains('không phép', na=False)]))
if st.session_state.current_role == "admin":
    cols[4].metric("💰 Tiền phạt", f"{tong_phat:,.0f} đ".replace(",", "."))

# --- HIỂN THỊ BẢNG & EXCEL ---
st.dataframe(filtered_df)
df_ex = filtered_df.copy()
if st.session_state.current_role == "admin":
    df_ex.loc[len(df_ex)] = [None, "TỔNG TIỀN PHẠT:", None, None, None, None, tong_phat, None, None, None]
st.download_button("📥 Tải Excel", to_excel(df_ex), "lichnghi.xlsx")
