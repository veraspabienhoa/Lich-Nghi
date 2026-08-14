import streamlit as st
import pandas as pd
from datetime import date, datetime, time, timedelta
import calendar
import requests
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

# --- KẾT NỐI & TẢI CẤU HÌNH ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        return gspread.authorize(Credentials.from_service_account_info(creds_dict, scopes=scope))
    except:
        return None

@st.cache_data(ttl=60)
def load_config_limits():
    try:
        client = get_gspread_client()
        # Đọc từ sheet 'Config' trong file Mật khẩu (hoặc file anh đã cấu hình)
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).worksheet("Config")
        vals = sheet.get_all_values()
        return {row[0]: int(row[1]) for row in vals[1:] if len(row) >= 2}
    except:
        return {"weekday_limit": 5, "weekend_limit": 2, "phat_sinh_limit": 1}

# --- CÁC HÀM XỬ LÝ LỊCH NGHỈ (TỐI ƯU) ---
# (Các hàm load_lich_nghi, save_lich_nghi_to_backup_sheet... giữ nguyên như logic trước)

# --- KHU VỰC NHẬP LỊCH NGHỈ (LOGIC MỚI) ---
if st.session_state.current_role in ["admin", "letan"]:
    with st.expander("📝 Nhập lịch nghỉ mới"):
        # ... [Giữ nguyên phần chọn nhân viên, ngày, lý do] ...
        
        # LOGIC KIỂM TRA PHÁT SINH
        submit_lich = st.form_submit_button("💾 Xác Nhận")
        if submit_lich:
            # 1. RÀO CHẮN GIỜ CHO "NGHỈ PHÁT SINH"
            if "phát sinh" in chosen_lydo.lower():
                # Lấy giờ hiện tại Việt Nam
                now = datetime.now().time()
                if now < time(9, 0):
                    st.error("❌ Chỉ được phép đăng ký 'Nghỉ phát sinh' từ 09:00 sáng trở đi!")
                    st.stop()
                
                # 2. KIỂM TRA GIỚI HẠN SỐ LƯỢNG "NGHỈ PHÁT SINH"
                limit_ps = load_config_limits().get('phat_sinh_limit', 1)
                df_bk = load_backup_sheet_data()
                current_ps_count = len(df_bk[(df_bk['Ngày'] == chosen_date.strftime('%d/%m/%Y')) & 
                                             (df_bk['Lý do nghỉ'].str.contains("phát sinh", case=False))])
                if current_ps_count >= limit_ps:
                    st.error(f"❌ Đã hết suất đăng ký 'Nghỉ phát sinh' cho ngày {chosen_date}! (Giới hạn: {limit_ps}).")
                    st.stop()

            # ... [Ghi dữ liệu vào sheet dự phòng] ...
