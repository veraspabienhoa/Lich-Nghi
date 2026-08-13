import streamlit as st
import pandas as pd

# Cấu hình trang hiển thị full màn hình trên điện thoại/máy tính
st.set_page_config(page_title="Quản Lý Kho Khuôn", layout="wide")

# Hàm tải dữ liệu (có bộ nhớ đệm cache giúp web không phải tải lại file nặng liên tục)
@st.cache_data(ttl=600) # Làm mới dữ liệu sau mỗi 10 phút
def load_data(url):
    try:
        # 1. Chuyển đổi link Google Drive Share thành link Direct Download
        file_id = url.split('/d/')[1].split('/')[0]
        direct_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        
        # 2. Đọc dữ liệu từ file qua Internet
        df = pd.read_excel(direct_url, header=1)
        
        # 3. Trích xuất đúng vị trí các cột như code cũ của bạn
        temp_df = df.iloc[:, [2, 3, 4, 5, 12]].copy()
        temp_df.columns = ["WH", "Location", "Model Name", "Mold Code", "Quantity"]
        
        # 4. Xử lý NaN và tạo cột tìm kiếm
        temp_df = temp_df.astype(object).fillna("")
        temp_df["Mold Code Str"] = temp_df["Mold Code"].astype(str).str.upper()
        
        return temp_df
    except Exception as e:
        st.error(f"Lỗi khi đọc file từ Google Drive: {e}\n(Hãy đảm bảo file của bạn đã được bật 'Bất kỳ ai có liên kết đều có thể xem')")
        return pd.DataFrame()

# --- XÂY DỰNG GIAO DIỆN ---
st.title("🔍 Quản Lý Kho Khuôn")

# Link Google Drive chứa file dữ liệu của bạn
GDRIVE_LINK = "https://drive.google.com/file/d/13s95pPP0jxQaSdTAcl0Ju3RC-miX5Xs1/view?usp=sharing"

with st.spinner("Đang kết nối tới kho dữ liệu..."):
    df_combined = load_data(GDRIVE_LINK)

if not df_combined.empty:
    # Với Streamlit, ô nhập liệu sẽ tự động xử lý khi bạn gõ (không cần nút bấm Tìm kiếm nữa)
    query = st.text_input("Nhập Mold Code:", placeholder="Gõ mã khuôn vào đây...").strip().upper()
    
    if query:
        # Lọc dữ liệu
        results = df_combined[df_combined["Mold Code Str"].str.contains(query, na=False)]
        
        st.success(f"✅ Tìm thấy {len(results)} kết quả.")
        
        # Hiển thị bảng và loại bỏ cột phụ
        display_df = results.drop(columns=["Mold Code Str"])
        st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.info("Bảng dữ liệu đang chờ bạn nhập từ khóa.")