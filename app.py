import streamlit as st
import pandas as pd
from datetime import date, datetime, time, timedelta
import calendar
import gspread
from google.oauth2.service_account import Credentials

# --- KẾT NỐI & TẢI CẤU HÌNH ---
# ... (Giữ nguyên các hàm kết nối get_gspread_client và load_config_limits như cũ, 
# nhưng bổ sung key 'phat_sinh_limit' vào dictionary trả về) ...

# --- LOGIC NHẬP LỊCH NGHỈ (Đã bổ sung ràng buộc) ---
if st.session_state.current_role in ["admin", "letan"]:
    with st.expander("📝 Nhập lịch nghỉ mới"):
        # ... các lựa chọn nhân viên, ngày, lý do ...
        
        submit_lich = st.form_submit_button("💾 Xác Nhận")
        if submit_lich:
            # 1. RÀO CHẮN THỜI GIAN CHO "NGHỈ PHÁT SINH"
            now = datetime.now().time()
            if "phát sinh" in chosen_lydo.lower() and now < time(9, 0):
                st.error("❌ Chỉ được phép đăng ký 'Nghỉ phát sinh' từ 09:00 sáng trở đi!")
            else:
                # 2. RÀO CHẮN SỐ LƯỢNG "NGHỈ PHÁT SINH" (AI ĐĂNG KÝ TRƯỚC ĐƯỢC DUYỆT)
                if "phát sinh" in chosen_lydo.lower():
                    limit_ps = limits.get('phat_sinh_limit', 1)
                    current_ps_count = len(df_backup[(df_backup['Ngày'] == chosen_date.strftime('%d/%m/%Y')) & 
                                                     (df_backup['Lý do nghỉ'].str.contains("phát sinh", case=False))])
                    if current_ps_count >= limit_ps:
                        st.error(f"❌ Đã hết suất đăng ký 'Nghỉ phát sinh' cho ngày {chosen_date}! (Chỉ giới hạn {limit_ps} người).")
                        st.stop()
                
                # 3. Ghi dữ liệu thành công nếu vượt qua các chốt chặn
                # ... (tiếp tục code ghi sheet như bình thường) ...
