import streamlit as st
import pandas as pd
from datetime import date, datetime, time
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH DUY NHẤT ---
SHEET_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

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
    
    # Đọc dữ liệu từ file duy nhất
    df_taikhoan = pd.DataFrame(sh.worksheet("TaiKhoan").get_all_records())
    df_loai_nghi = pd.DataFrame(sh.worksheet("LoaiNghi").get_all_records())
    df_config = pd.DataFrame(sh.worksheet("Config").get_all_records())
    df_nhanvien = pd.DataFrame(sh.worksheet("Nhanvien").get_all_records())
    df_lich = pd.DataFrame(sh.worksheet("LichNghi").get_all_records())
    
    config_dict = {row['Key']: int(row['Value']) for row in df_config.to_dict('records')}
    return df_taikhoan, df_loai_nghi, config_dict, df_nhanvien, df_lich

# --- ĐĂNG NHẬP ---
df_taikhoan, df_loai_nghi, config, df_nhanvien, df_lich = get_system_data()

# [Phần logic đăng nhập sử dụng df_taikhoan thay vì kết nối file cũ]
# [Phần nhập lịch nghỉ sử dụng sheet LichNghi của file duy nhất]
# [Phần quản lý tài khoản sử dụng sheet TaiKhoan của file duy nhất]
