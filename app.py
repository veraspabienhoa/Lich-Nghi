import streamlit as st
import pandas as pd
from datetime import date

# Cấu hình trang hiển thị
st.set_page_config(page_title="Bảng Tra Cứu Lịch Nghỉ", page_icon="📅", layout="wide")

# --- 1. HỆ THỐNG ĐĂNG NHẬP ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

# Nếu chưa đăng nhập, hiển thị form đăng nhập và chặn các nội dung bên dưới
if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    with st.form("login_form"):
        username = st.text_input("Tên đăng nhập")
        password = st.text_input("Mật khẩu", type="password")
        submit = st.form_submit_button("Đăng Nhập")
        
        if submit:
            # Kiểm tra tài khoản và mật khẩu anh đã cung cấp
            if username == "admin" and password == "32531235":
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop() # Dừng chạy các code bên dưới

# --- 2. HÀM TẢI DỮ LIỆU TỪ GOOGLE DRIVE ---
@st.cache_data(ttl=300) # Làm mới dữ liệu mỗi 5 phút
def load_data(url):
    try:
        # Chuyển đổi link GDrive sang dạng tải trực tiếp
        file_id = url.split('/d/')[1].split('/')[0]
        direct_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        
        df = pd.read_excel(direct_url, sheet_name="LichNghi")
        df['Ngày'] = pd.to_datetime(df['Ngày']).dt.date
        df['Số ngày tính'] = pd.to_numeric(df['Số ngày tính'], errors='coerce').fillna(0)
        df['Phạt vi phạm'] = pd.to_numeric(df['Phạt vi phạm'], errors='coerce').fillna(0)
        return df
    except Exception as e:
        st.error(f"Lỗi tải dữ liệu từ Google Drive: {e}")
        return pd.DataFrame()

# Link Google Drive của anh
GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

with st.spinner("Đang tải dữ liệu từ Google Drive..."):
    df_lich = load_data(GDRIVE_LINK)

if df_lich.empty:
    st.stop()

# --- 3. GIAO DIỆN CHÍNH (DASHBOARD) ---
col_title, col_logout = st.columns([8, 2])
with col_title:
    st.title("📊 Bảng Tra Cứu Tình Hình Nghỉ Phép")
with col_logout:
    if st.button("🚪 Đăng xuất", use_container_width=True):
        st.session_state.logged_in = False
        st.rerun()

st.subheader("🔍 Lọc Dữ Liệu")

today = date.today()

filter_type = st.radio(
    "Chọn chế độ xem thời gian:", 
    ["Hôm nay", "Chọn ngày cụ thể", "Chọn khoảng thời gian"], 
    horizontal=True
)

if filter_type == "Hôm nay":
    start_date = today
    end_date = today
elif filter_type == "Chọn ngày cụ thể":
    start_date = st.date_input("Chọn ngày:", today)
    end_date = start_date
else:
    date_range = st.date_input("Chọn từ ngày - đến ngày:", [today, today])
    if len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = date_range[0]
        end_date = date_range[0]

# Lọc & Phân loại
mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
filtered_df = df_lich[mask_date]

co_phep_df = filtered_df[filtered_df['Số ngày tính'] > 0]
khong_phep_df = filtered_df[(filtered_df['Số ngày tính'] == 0) & (filtered_df['Phạt vi phạm'] > 0)]

# Hiển thị số liệu
st.markdown("---")
col1, col2, col3 = st.columns(3)
col1.metric("Tổng số lượt nghỉ (trong giai đoạn)", len(filtered_df))
col2.metric("✅ Số người nghỉ CÓ phép", len(co_phep_df))
col3.metric("❌ Số người nghỉ KHÔNG phép", len(khong_phep_df))
st.markdown("---")

# Hiển thị bảng
st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")

tab1, tab2, tab3 = st.tabs(["Tất cả danh sách", "Danh sách Nghỉ CÓ phép", "Danh sách Nghỉ KHÔNG phép"])

with tab1:
    st.dataframe(filtered_df, use_container_width=True, hide_index=True)
with tab2:
    if co_phep_df.empty:
        st.info("Không có dữ liệu nhân viên nghỉ có phép.")
    else:
        st.dataframe(co_phep_df, use_container_width=True, hide_index=True)
with tab3:
    if khong_phep_df.empty:
        st.success("Tuyệt vời! Không có nhân viên nào nghỉ không phép.")
    else:
        st.dataframe(khong_phep_df, use_container_width=True, hide_index=True)
