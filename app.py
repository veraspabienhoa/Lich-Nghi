import streamlit as st
import pandas as pd
from datetime import date, datetime, time, timedelta
import calendar
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH ---
SHEET_DATA_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"

@st.cache_resource
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        return gspread.authorize(Credentials.from_service_account_info(creds_dict))
    except: return None

@st.cache_data(ttl=60)
def get_system_data():
    client = get_gspread_client()
    # Lấy quy tắc phạt
    df_loai_nghi = pd.DataFrame(client.open_by_key(SHEET_DATA_ID).worksheet("LoaiNghi").get_all_records())
    # Lấy cấu hình giới hạn
    df_config = pd.DataFrame(client.open_by_key(SHEET_DATA_ID).worksheet("Config").get_all_records())
    config_dict = {row['Key']: int(row['Value']) for row in df_config.to_dict('records')}
    # Lấy lịch nghỉ đã đăng ký
    df_lich = pd.DataFrame(client.open_by_key(SHEET_DATA_ID).worksheet("LichNghi").get_all_records())
    return df_loai_nghi, config_dict, df_lich

# --- PHẦN NHẬP LỊCH NGHỈ (LOGIC MỚI) ---
df_loai_nghi, config, df_lich = get_system_data()

with st.expander("📝 Nhập lịch nghỉ"):
    with st.form("nhap_lich_form"):
        nv = st.selectbox("Nhân viên:", ["-- Chọn --"] + df_nv_list) # Lấy từ danh sách nhân viên của anh
        ngay = st.date_input("Ngày:", date.today())
        ly_do = st.selectbox("Lý do nghỉ:", df_loai_nghi['Lý do nghỉ'].tolist())
        
        # Tự động lấy mức phạt
        row_ly_do = df_loai_nghi[df_loai_nghi['Lý do nghỉ'] == ly_do].iloc[0]
        phat = row_ly_do['Phạt vi phạm']
        st.write(f"**Mức phạt:** {phat:,.0f} VNĐ")
        
        if st.form_submit_button("Lưu"):
            # 1. Check giờ (Phát sinh >= 09:00)
            if "phát sinh" in ly_do.lower() and datetime.now().time() < time(9, 0):
                st.error("Chỉ được đăng ký nghỉ phát sinh sau 09:00 sáng!")
            # 2. Check giới hạn số lượng (Weekday/Weekend)
            elif (ngay.weekday() < 5 and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config['weekday_limit']) or \
                 (ngay.weekday() >= 5 and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config['weekend_limit']):
                st.error("Đã đủ số người đăng ký nghỉ cho ngày này!")
            else:
                # Ghi dữ liệu vào sheet LichNghi
                client = get_gspread_client()
                client.open_by_key(SHEET_DATA_ID).worksheet("LichNghi").append_row([str(ngay), nv, ly_do, phat])
                st.success("Đã ghi nhận!")
                st.rerun()
