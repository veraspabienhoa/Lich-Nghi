import streamlit as st
import pandas as pd
from datetime import date, datetime, time
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH ---
SHEET_DATA_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

@st.cache_resource
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        return gspread.authorize(Credentials.from_service_account_info(creds_dict))
    except: return None

@st.cache_data(ttl=30)
def get_system_data():
    client = get_gspread_client()
    sh = client.open_by_key(SHEET_DATA_ID)
    
    # Đọc dữ liệu từ các sheet
    df_loai_nghi = pd.DataFrame(sh.worksheet("LoaiNghi").get_all_records())
    df_config = pd.DataFrame(sh.worksheet("Config").get_all_records())
    df_nhanvien = pd.DataFrame(sh.worksheet("Nhanvien").get_all_records())
    df_lich = pd.DataFrame(sh.worksheet("LichNghi").get_all_records())
    
    config_dict = {row['Key']: int(row['Value']) for row in df_config.to_dict('records')}
    return df_loai_nghi, config_dict, df_nhanvien, df_lich

# --- GIAO DIỆN NHẬP LỊCH NGHỈ ---
df_loai_nghi, config, df_nhanvien, df_lich = get_system_data()

with st.expander("📝 Nhập lịch nghỉ mới"):
    with st.form("nhap_lich_form"):
        # Lấy danh sách nhân viên từ sheet Nhanvien
        nv_list = df_nhanvien['Tên nhân viên'].tolist()
        nv = st.selectbox("Chọn nhân viên:", ["-- Chọn --"] + nv_list)
        ngay = st.date_input("Ngày:", date.today())
        ly_do = st.selectbox("Lý do nghỉ:", df_loai_nghi['Lý do nghỉ'].tolist())
        
        # Tra cứu mức phạt chuẩn
        row_ly_do = df_loai_nghi[df_loai_nghi['Lý do nghỉ'] == ly_do].iloc[0]
        phat = row_ly_do['Phạt vi phạm']
        st.write(f"**Mức phạt:** {phat:,.0f} VNĐ")
        
        chitiet = st.text_input("Chi tiết vi phạm:")
        
        if st.form_submit_button("Lưu lịch nghỉ"):
            # 1. Kiểm tra giờ đối với Nghỉ phát sinh
            if "phát sinh" in ly_do.lower() and datetime.now().time() < time(9, 0):
                st.error("❌ Chỉ được đăng ký 'Nghỉ phát sinh' từ 09:00 sáng trở đi!")
            
            # 2. Kiểm tra giới hạn số người trong ngày (Weekday: 5, Weekend: 2)
            elif (ngay.weekday() < 5 and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config.get('weekday_limit', 5)) or \
                 (ngay.weekday() >= 5 and len(df_lich[df_lich['Ngày'] == str(ngay)]) >= config.get('weekend_limit', 2)):
                st.error(f"❌ Đã đủ số lượng người đăng ký nghỉ cho ngày {ngay}!")
                
            # 3. Kiểm tra giới hạn số lượng "Nghỉ phát sinh"
            elif "phát sinh" in ly_do.lower() and len(df_lich[(df_lich['Ngày'] == str(ngay)) & (df_lich['Lý do nghỉ'].str.contains("phát sinh", case=False))]) >= config.get('phat_sinh_limit', 1):
                st.error("❌ Đã hết suất 'Nghỉ phát sinh' trong ngày!")
                
            else:
                # Ghi vào Google Sheet
                client = get_gspread_client()
                client.open_by_key(SHEET_DATA_ID).worksheet("LichNghi").append_row([str(ngay), nv, ly_do, chitiet, row_ly_do['Số ngày tính phép'], phat, str(date.today()), st.session_state.current_user])
                st.success("✅ Đã ghi nhận lịch nghỉ thành công!")
                st.rerun()
