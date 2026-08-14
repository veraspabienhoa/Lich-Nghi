import streamlit as st
import pandas as pd
from datetime import date
import requests
import os

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Hệ Thống Lịch Nghỉ - Massage Vera", page_icon="📅", layout="wide")

# --- HÀM TẢI FILE TỪ GOOGLE DRIVE CHỐNG CHẶN ---
def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    
    token = None
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            token = value
            break
            
    if token:
        params = {'id': id, 'confirm': token}
        response = session.get(URL, params=params, stream=True)
        
    with open(destination, "wb") as f:
        for chunk in response.iter_content(32768):
            if chunk:
                f.write(chunk)

# --- HÀM LẤY VÀ XỬ LÝ DỮ LIỆU ---
@st.cache_data(ttl=60)
def load_data(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        
        # Tải file về máy chủ
        download_file_from_google_drive(file_id, temp_file)
        
        # Đọc file bằng pyxlsb 
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV'], engine='pyxlsb')
        df_lich = xls['LichNghi']
        df_nv = xls['DanhSachNV']
        
        # Xóa file tạm
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
        # Chuẩn hóa 10 cột đầu
        df_lich = df_lich.iloc[:, :10]
        df_lich.columns = [
            'Ngày', 'Tên nhân viên', 'Loại nghỉ', 'Chi tiết', 
            'Số ngày tính', 'Số ngày đã nghỉ trong tháng', 
            'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật'
        ]
        
        # Hàm đọc ngày tháng chuyên trị số sê-ri của xlsb
        def safe_date_parse(val):
            try:
                if pd.isna(val): return pd.NaT
                if hasattr(val, 'date'): return val.date() 
                if isinstance(val, (int, float)): 
                    return pd.to_datetime(val, unit='D', origin='1899-12-30').date()
                s = str(val).strip().split(' ')[0]
                return pd.to_datetime(s, dayfirst=True).date()
            except:
                return pd.NaT
                
        # Hàm chuyển đổi giờ từ số thập phân của Excel
        def safe_time_parse(val):
            try:
                if pd.isna(val): return ""
                if isinstance(val, (int, float)):
                    total_seconds = int(round(val * 86400))
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    seconds = total_seconds % 60
                    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                return str(val).strip()
            except:
                return str(val)
                
        # 1. Xử lý cột 'Ngày'
        df_lich['Ngày'] = df_lich['Ngày'].apply(safe_date_parse)
        df_lich = df_lich.dropna(subset=['Ngày'])
        
        # 2. Xử lý số liệu
        df_lich['Số ngày tính'] = pd.to_numeric(df_lich['Số ngày tính'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        df_lich['Phạt vi phạm'] = pd.to_numeric(df_lich['Phạt vi phạm'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        
        # 3. Ép kiểu và định dạng chuẩn cho Ngày/Giờ cập nhật
        df_lich['Ngày cập nhật'] = df_lich['Ngày cập nhật'].apply(safe_date_parse)
        df_lich['Ngày cập nhật'] = pd.to_datetime(df_lich['Ngày cập nhật'], errors='coerce').dt.strftime('%d/%m/%Y').fillna("")
        df_lich['Giờ cập nhật'] = df_lich['Giờ cập nhật'].apply(safe_time_parse)
        
        df_nv = df_nv.dropna(subset=['Tên nhân viên'])
        
        return df_lich, df_nv
    except Exception as e:
        st.error(f"Lỗi đọc dữ liệu: {e}")
        return pd.DataFrame(), pd.DataFrame()

# Link gốc
GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

with st.spinner("Đang tải dữ liệu từ Google Drive..."):
    df_lich, df_nv = load_data(GDRIVE_LINK)

if df_lich.empty or df_nv.empty:
    st.warning("Hệ thống chưa tìm thấy dữ liệu.")
    if st.button("🔄 Tải lại dữ liệu"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# --- HỆ THỐNG ĐĂNG NHẬP TRÊN WEB ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_user" not in st.session_state:
    st.session_state.current_user = ""

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    
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
            elif username_input == "admin" and password_input == "admin": 
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.rerun()
            else:
                st.error("❌ Sai tên đăng nhập hoặc mật khẩu! (Mật khẩu mặc định là 123456)")
    st.stop()

# --- GIAO DIỆN BẢNG ĐIỀU KHIỂN ---
col_title, col_logout = st.columns([8, 2])
with col_title:
    st.title(f"📊 Bảng Tra Cứu Tình Hình Nghỉ Phép - {st.session_state.current_user}")
with col_logout:
    if st.button("🚪 Đăng xuất", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.current_user = ""
        st.rerun()
        
st.markdown("---")

# Bộ lọc
st.subheader("🔍 Lọc Dữ Liệu")
col_date, col_name, col_refresh = st.columns([4, 4, 2])

with col_date:
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

with col_name:
    st.write("") 
    list_nv = ["- Tất cả nhân viên -"] + sorted(df_nv['Tên nhân viên'].dropna().astype(str).str.strip().unique().tolist())
    selected_nv = st.selectbox("👤 Tìm kiếm / Chọn tên nhân viên:", list_nv)

with col_refresh:
    st.write("") 
    st.write("") 
    if st.button("🔄 Lấy Dữ Liệu Mới Nhất", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# 1. Lọc theo ngày
mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
filtered_df = df_lich[mask_date]

# 2. Lọc theo tên nhân viên 
if selected_nv != "- Tất cả nhân viên -":
    filtered_df = filtered_df[filtered_df['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]

# Phân loại
co_phep_df = filtered_df[filtered_df['Số ngày tính'] > 0]
khong_phep_df = filtered_df[(filtered_df['Số ngày tính'] == 0) & (filtered_df['Phạt vi phạm'] > 0)]

# Hiển thị số liệu KPI
st.markdown("---")
col1, col2, col3 = st.columns(3)
col1.metric("Tổng số lượt nghỉ", len(filtered_df))
col2.metric("✅ Số người nghỉ CÓ phép", len(co_phep_df))
col3.metric("❌ Số người nghỉ KHÔNG phép", len(khong_phep_df))
st.markdown("---")

# Cột cần ẩn khỏi giao diện
cols_to_hide = ['Phạt vi phạm']

st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")

# --- NÂNG CẤP HIỂN THỊ KHI KHÔNG CÓ DỮ LIỆU ---
if filtered_df.empty:
    # 1. Báo cáo chung
    available_dates = df_lich['Ngày'].dropna().unique()
    if len(available_dates) > 0:
        available_dates = sorted(available_dates, reverse=True)[:5]
        dates_str = ", ".join([d.strftime('%d/%m/%Y') for d in available_dates])
        st.info(f"💡 Hệ thống không thấy dữ liệu trong khoảng thời gian này.\n\nCác ngày đang có dữ liệu chung trên hệ thống: **{dates_str}**")
        
    # 2. Báo cáo riêng biệt cho nhân viên + Nút hiển thị
    if selected_nv != "- Tất cả nhân viên -":
        # Lọc dữ liệu GỐC để tìm lịch sử của người này (không bị giới hạn bởi bộ lọc ngày tháng ở trên)
        nv_history_df = df_lich[df_lich['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]
        
        if not nv_history_df.empty:
            # Dùng Expander hoạt động như một nút bấm thả xuống
            with st.expander(f"📅 Bấm vào đây để xem TẤT CẢ lịch sử nghỉ phép của nhân viên {selected_nv}"):
                st.dataframe(nv_history_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
        else:
            st.warning(f"Nhân viên **{selected_nv}** hiện chưa từng có dữ liệu nghỉ phép trên hệ thống.")

# --- HIỂN THỊ BẢNG CHI TIẾT THEO TAB ---
tab1, tab2, tab3 = st.tabs(["Tất cả danh sách", "Danh sách Nghỉ CÓ phép", "Danh sách Nghỉ KHÔNG phép"])

with tab1:
    st.dataframe(filtered_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
with tab2:
    if co_phep_df.empty:
        st.info("Không có dữ liệu nhân viên nghỉ có phép.")
    else:
        st.dataframe(co_phep_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
with tab3:
    if khong_phep_df.empty:
        st.success("Tuyệt vời! Không có nhân viên nào nghỉ không phép.")
    else:
        st.dataframe(khong_phep_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
