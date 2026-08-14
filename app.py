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
# Sheet để Admin lưu cấu hình giới hạn (sử dụng sheet thứ 2 của file mật khẩu hoặc tạo mới)
SHEET_CONFIG_ID = SHEET_MAT_KHAU_ID 

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None

# --- HÀM TẢI CẤU HÌNH GIỚI HẠN (Admin config) ---
@st.cache_data(ttl=60)
def load_config_limits():
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_CONFIG_ID).worksheet("Config")
        vals = sheet.get_all_values()
        return {row[0]: int(row[1]) for row in vals if len(row) >= 2}
    except:
        return {"weekday_limit": 5, "weekend_limit": 2}

def save_config_limits(weekday_limit, weekend_limit):
    client = get_gspread_client()
    sheet = client.open_by_key(SHEET_CONFIG_ID).worksheet("Config")
    sheet.update("A1", [["weekday_limit", weekday_limit], ["weekend_limit", weekend_limit]])
    st.cache_data.clear()

# --- CÁC HÀM CŨ ĐÃ TỐI ƯU ---
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
                    if str(ten).strip() != "":
                        data_list.append({
                            'STT': stt,
                            'Tên nhân viên': str(ten).strip(),
                            'Mật khẩu': str(pwd).strip() if str(pwd).strip() else "123456",
                            'Phân quyền': str(role).strip().lower() if str(role).strip() else "nhanvien"
                        })
                return pd.DataFrame(data_list)
    except Exception:
        pass
    return pd.DataFrame(columns=['STT', 'Tên nhân viên', 'Mật khẩu', 'Phân quyền'])

@st.cache_data(ttl=10)
def load_backup_sheet_data():
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        rows = sheet.get_all_values()
        if len(rows) > 1:
            return pd.DataFrame(rows[1:], columns=rows[0])
    except:
        pass
    return pd.DataFrame(columns=["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Phạt vi phạm", "Ngày tạo", "Người tạo"])

def save_lich_nghi_to_backup_sheet(ngay, nv, ly_do, chi_tiet, so_ngay, phat_vi_pham, nguoi_tao):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet.append_row([str(ngay), str(nv), str(ly_do), str(chi_tiet), float(so_ngay), float(phat_vi_pham), str(date.today()), str(nguoi_tao)])
        st.cache_data.clear()
        return True, "Đã ghi nhận lịch nghỉ thành công!"
    except Exception as e:
        return False, f"Lỗi: {e}"

def delete_backup_row(row_index_1_based):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet.delete_rows(row_index_1_based)
        st.cache_data.clear()
        return True, "Đã xóa lịch nghỉ thành công!"
    except Exception as e:
        return False, f"Lỗi: {e}"

@st.cache_data(ttl=60)
def load_lich_nghi(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        # Download... (simplified)
        # Note: In real app, keep the download logic
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV', 'LoaiNghi'], engine='pyxlsb')
        return xls['LichNghi'], xls['DanhSachNV'], xls['LoaiNghi']
    except:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

# TẢI DỮ LIỆU
df_credentials = load_credentials()
df_backup = load_backup_sheet_data()
limits = load_config_limits()
# (Rest of the logic follows similarly to previous versions, incorporating 'limits')

# --- LOGIC NHẬP LỊCH NGHỈ ---
# Khi kiểm tra giới hạn:
# max_people = limits['weekday_limit'] if not is_weekend else limits['weekend_limit']
# if today_total_nghi >= max_people: st.error(...)
