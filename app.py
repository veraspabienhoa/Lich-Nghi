import streamlit as st
import pandas as pd
from datetime import date

# Cấu hình trang hiển thị
st.set_page_config(page_title="Hệ Thống Lịch Nghỉ - Massage Vera", page_icon="📅", layout="wide")

# --- 1. HÀM TẢI DỮ LIỆU TỪ GOOGLE DRIVE ---
@st.cache_data(ttl=300) 
def load_data(url):
    try:
        # Chuyển đổi link GDrive
        file_id = url.split('/d/')[1].split('/')[0]
        direct_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        
        # ĐỌC FILE .XLSB bằng engine pyxlsb
        xls = pd.read_excel(direct_url, sheet_name=['LichNghi', 'DanhSachNV'], engine='pyxlsb')
        df_lich = xls['LichNghi']
        df_nv = xls['DanhSachNV']
        
        # Chỉ lấy 10 cột đầu tiên
        df_lich = df_lich.iloc[:, :10]
        
        # Đặt lại tên cột chuẩn xác
        df_lich.columns = [
            'Ngày', 'Tên nhân viên', 'Loại nghỉ', 'Chi tiết', 
            'Số ngày tính', 'Số ngày đã nghỉ trong tháng', 
            'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật'
        ]
        
        # --- HÀM BÓC TÁCH NGÀY THÁNG ---
        def safe_date_parse(val):
            try:
                if pd.isna(val): return pd.NaT
                if hasattr(val, 'date'): return val.date() 
                s = str(val).strip().split(' ')[0]
                return pd.to_datetime(s, dayfirst=True).date()
            except:
                return pd.NaT
                
        df_lich['Ngày'] = df_lich['Ngày'].apply(safe_date_parse)
        df_lich = df_lich.dropna(subset=['Ngày'])
        
        # Làm sạch số liệu
        df_lich['Số ngày tính'] = pd.to_numeric(df_lich['Số ngày tính'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        df_lich['Phạt vi phạm'] = pd.to_numeric(df_lich['Phạt vi phạm'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        
        df_nv = df_nv.dropna(subset=['Tên nhân viên'])
        
        return df_lich, df_nv
    except Exception as e:
        st.error(f"Lỗi tải dữ liệu từ Google Drive: {e}")
        return pd.DataFrame(), pd.DataFrame()

# Link Google Drive (vẫn dùng link cũ của anh)
GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

with st.spinner("Đang kết nối tới máy chủ dữ liệu..."):
    df_lich, df_nv = load_data(GDRIVE_LINK)

if df_lich.empty or df_nv.empty:
    st.warning("Hệ thống chưa tìm thấy dữ liệu. Vui lòng kiểm tra lại cấu trúc file Excel.")
    if st.button("🔄 Tải lại dữ liệu (Xóa Cache)"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# --- 2. HỆ THỐNG ĐĂNG NHẬP ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_user" not in st.session_state:
    st.session_state.current_user = ""

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    
    if st.button("🔄 Tải Lại Dữ Liệu Mới Nhất Từ GDrive", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
        
    with st.form("login_form"):
        username_input = st.text_input("Tên đăng nhập (Tên nhân viên)").strip()
        password_input = st.text_input("Mật khẩu", type="password")
        submit = st.form_submit_button("Đăng Nhập")
        
        if submit:
            danh_sach_nhan_vien = df_nv['Tên nhân viên'].astype(str).str.strip().tolist()
            is_valid_user = False
            user_chuan = ""
            
            for name in danh_sach_nhan_vien:
                if username_input.lower() == name.lower():
                    is_valid_user = True
                    user_chuan = name 
                    break
            
            if is_valid_user and password_input == "123456":
                st.session_state.logged_in = True
                st.session_state.current_user = user_chuan
                st.rerun()
            elif username_input == "admin" and password_input == "admin": # CẬP NHẬT MẬT KHẨU ADMIN
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.rerun()
            else:
                st.error("❌ Sai tên đăng nhập hoặc mật khẩu! (Mật khẩu mặc định là 123456)")
    st.stop()

# --- 3. GIAO DIỆN CHÍNH (DASHBOARD) ---
col_title, col_logout = st.columns([8, 2])
with col_title:
    st.title(f"📊 Bảng Tra Cứu Tình Hình Nghỉ Phép - {st.session_state.current_user}")
with col_logout:
    if st.button("🚪 Đăng xuất", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.current_user = ""
        st.rerun()
        
st.markdown("---")

col_filter, col_refresh = st.columns([8, 2])

with col_filter:
    st.subheader("🔍 Lọc Dữ Liệu")
    today = date.today()
    filter_type = st.radio(
        "Chọn chế độ xem thời gian:", 
        ["Hôm nay", "Chọn ngày cụ thể", "Chọn khoảng thời gian"], 
        horizontal=True
    )

with col_refresh:
    if st.button("🔄 Tải Lại Dữ Liệu", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

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

if filtered_df.empty:
    available_dates = df_lich['Ngày'].dropna().unique()
    if len(available_dates) > 0:
        available_dates = sorted(available_dates, reverse=True)[:5]
        dates_str = ", ".join([d.strftime('%d/%m/%Y') for d in available_dates])
        st.info(f"💡 Hệ thống hiện không thấy ai nghỉ vào ngày {start_date.strftime('%d/%m/%Y')}.\n\nCác ngày đang có dữ liệu trong file Excel: **{dates_str}**")

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
