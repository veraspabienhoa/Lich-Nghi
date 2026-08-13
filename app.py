import streamlit as st
import pandas as pd
from datetime import date

# Cấu hình trang hiển thị
st.set_page_config(page_title="Bảng Tra Cứu Lịch Nghỉ - Massage Vera", page_icon="📅", layout="wide")

# --- HÀM TẢI DỮ LIỆU ---
@st.cache_data(ttl=300) # Cập nhật lại dữ liệu mỗi 5 phút nếu file Excel có thay đổi
def load_data(file_path):
    try:
        # Chỉ đọc Sheet "LichNghi"
        df = pd.read_excel(file_path, sheet_name="LichNghi")
        
        # Chuyển đổi cột 'Ngày' sang định dạng date để dễ dàng so sánh
        df['Ngày'] = pd.to_datetime(df['Ngày']).dt.date
        
        # Đảm bảo Cột E (Số ngày tính) và Cột G (Phạt vi phạm) là dạng số để xét điều kiện
        df['Số ngày tính'] = pd.to_numeric(df['Số ngày tính'], errors='coerce').fillna(0)
        df['Phạt vi phạm'] = pd.to_numeric(df['Phạt vi phạm'], errors='coerce').fillna(0)
        
        return df
    except Exception as e:
        st.error(f"Lỗi khi đọc file: {e}\nHãy chắc chắn file 'LichNghi.xlsx' đang mở hoặc nằm cùng thư mục.")
        return pd.DataFrame()

# Tên file Excel gốc
FILE_NAME = "LichNghi.xlsx"
df_lich = load_data(FILE_NAME)

if df_lich.empty:
    st.stop() # Dừng chạy nếu không có dữ liệu

# --- GIAO DIỆN CHÍNH ---
st.title("📊 Bảng Tra Cứu Tình Hình Nghỉ Phép")

# 1. BỘ LỌC THỜI GIAN
st.subheader("🔍 Lọc Dữ Liệu")

today = date.today()

# Cho phép chọn 1 trong 3 chế độ xem
filter_type = st.radio(
    "Chọn chế độ xem thời gian:", 
    ["Hôm nay", "Chọn ngày cụ thể", "Chọn khoảng thời gian"], 
    horizontal=True
)

# Xử lý logic chọn ngày
if filter_type == "Hôm nay":
    start_date = today
    end_date = today
    
elif filter_type == "Chọn ngày cụ thể":
    start_date = st.date_input("Chọn ngày:", today)
    end_date = start_date
    
else:
    # Trả về 1 tuple gồm 2 ngày (Bắt đầu, Kết thúc)
    date_range = st.date_input("Chọn từ ngày - đến ngày:", [today, today])
    if len(date_range) == 2:
        start_date, end_date = date_range
    else:
        # Nếu người dùng mới click 1 ngày, tạm thời lấy ngày đó làm mốc
        start_date = date_range[0]
        end_date = date_range[0]

# --- 2. XỬ LÝ LỌC & TÍNH TOÁN THEO YÊU CẦU ---

# Lọc các dòng nằm trong khoảng thời gian đã chọn
mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
filtered_df = df_lich[mask_date]

# Phân loại theo định nghĩa của anh
# Nghỉ có phép: Cột E (Số ngày tính) > 0
co_phep_df = filtered_df[filtered_df['Số ngày tính'] > 0]

# Nghỉ không phép: Cột E (Số ngày tính) == 0 VÀ Cột G (Phạt vi phạm) > 0
khong_phep_df = filtered_df[(filtered_df['Số ngày tính'] == 0) & (filtered_df['Phạt vi phạm'] > 0)]

num_co_phep = len(co_phep_df)
num_khong_phep = len(khong_phep_df)

# --- 3. HIỂN THỊ CHỈ SỐ (KPI) ---
st.markdown("---")
col1, col2, col3 = st.columns(3)

# Dùng thẻ metric để hiển thị số lớn cho trực quan
col1.metric("Tổng số lượt nghỉ (trong giai đoạn)", len(filtered_df))
col2.metric("✅ Số người nghỉ CÓ phép", num_co_phep)
col3.metric("❌ Số người nghỉ KHÔNG phép", num_khong_phep)

st.markdown("---")

# --- 4. HIỂN THỊ CHI TIẾT BẰNG TAB ---
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
