import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
import requests
import os
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Hệ Thống Lịch Nghỉ - Massage Vera", page_icon="📅", layout="wide")

# --- ÉP CSS THU GỌN GIAO DIỆN ---
st.markdown("""
    <style>
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 1rem;
        }
        div[data-testid="stVerticalBlock"] > div {
            gap: 0.2rem !important;
        }
        h1, h2, h3 {
            padding-bottom: 0rem !important;
            margin-bottom: 0rem !important;
        }
        button {
            margin-top: 5px !important;
        }
    </style>
""", unsafe_allow_html=True)

# --- KẾT NỐI GSPREAD ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None

# --- HÀM TẢI MẬT KHẨU TỪ GOOGLE SHEET ---
@st.cache_data(ttl=30)
def load_credentials():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            data = sheet.get_all_records()
            df = pd.DataFrame(data)
            return df
    except Exception:
        pass
    
    try:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_MAT_KHAU_ID}/gviz/tq?tqx=out:csv"
        df = pd.read_csv(url)
        return df
    except Exception as e:
        return pd.DataFrame(columns=['STT', 'Tên nhân viên', 'Mật khẩu'])

# --- HÀM CHO NHÂN VIÊN ĐỔI MẬT KHẨU ---
def update_password_in_sheet(username, new_password):
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets (Secrets)."
        
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        # Sử dụng findall để tránh lỗi khi nhân viên mới chưa có tên trong sheet
        cells = sheet.findall(username, in_column=2)
        if cells:
            sheet.update_cell(cells[0].row, 3, str(new_password))
        else:
            # Nếu nhân viên mới tinh, tự động thêm vào cuối danh sách Google Sheet
            all_records = sheet.get_all_records()
            next_stt = len(all_records) + 1
            sheet.append_row([next_stt, username, str(new_password)])
            
        st.cache_data.clear() 
        return True, "Đổi mật khẩu thành công!"
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- HÀM CHO ADMIN QUẢN LÝ ---
def update_account_by_admin(old_username, new_username, new_password):
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets (Secrets)."
        
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        cells = sheet.findall(old_username, in_column=2)
        
        final_name = new_username.strip() if new_username and new_username.strip() else old_username
        final_pass = new_password.strip() if new_password and new_password.strip() else None
        
        if cells:
            if final_name != old_username:
                sheet.update_cell(cells[0].row, 2, final_name)
            if final_pass:
                sheet.update_cell(cells[0].row, 3, final_pass)
        else:
            # Nếu nhân viên chưa có trên Google Sheet, tạo mới cho họ
            if not final_pass:
                final_pass = "123456" 
            all_records = sheet.get_all_records()
            next_stt = len(all_records) + 1
            sheet.append_row([next_stt, final_name, final_pass])
            
        st.cache_data.clear() 
        return True, f"Cập nhật thành công tài khoản: {final_name}!"
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- HÀM TẢI FILE LỊCH NGHỈ VÀ DANH SÁCH TỪ EXCEL ---
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

@st.cache_data(ttl=60)
def load_lich_nghi(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        # Đọc CẢ HAI sheet từ Excel
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV'], engine='pyxlsb')
        df_lich = xls['LichNghi']
        df_nv_excel = xls['DanhSachNV']
        
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
        df_lich = df_lich.iloc[:, :10]
        df_lich.columns = [
            'Ngày', 'Tên nhân viên', 'Loại nghỉ', 'Chi tiết', 
            'Số ngày tính', 'Số ngày đã nghỉ trong tháng', 
            'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật'
        ]
        
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
                
        df_lich['Ngày'] = df_lich['Ngày'].apply(safe_date_parse)
        df_lich = df_lich.dropna(subset=['Ngày'])
        
        df_lich['Số ngày tính'] = pd.to_numeric(df_lich['Số ngày tính'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        df_lich['Phạt vi phạm'] = pd.to_numeric(df_lich['Phạt vi phạm'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        
        df_lich['Ngày cập nhật'] = df_lich['Ngày cập nhật'].apply(safe_date_parse)
        df_lich['Ngày cập nhật'] = pd.to_datetime(df_lich['Ngày cập nhật'], errors='coerce').dt.strftime('%d/%m/%Y').fillna("")
        df_lich['Giờ cập nhật'] = df_lich['Giờ cập nhật'].apply(safe_time_parse)
        
        df_nv_excel = df_nv_excel.dropna(subset=['Tên nhân viên'])
        
        return df_lich, df_nv_excel
    except Exception as e:
        st.error(f"Lỗi đọc dữ liệu: {e}")
        return pd.DataFrame(), pd.DataFrame()

@st.cache_data(show_spinner=False)
def to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DuLieuLichNghi')
    return output.getvalue()

# Tải dữ liệu 
df_credentials = load_credentials() # Lấy pass từ GG Sheet
GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

with st.spinner("Đang tải dữ liệu hệ thống..."):
    df_lich, df_nv_excel = load_lich_nghi(GDRIVE_LINK) # Lấy danh sách nhân viên TỪ EXCEL

if df_lich.empty or df_nv_excel.empty:
    st.warning("Hệ thống chưa tìm thấy dữ liệu. Vui lòng kiểm tra lại cấu hình.")
    if st.button("🔄 Tải lại dữ liệu"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# --- HỆ THỐNG ĐĂNG NHẬP ---
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
            user_found = False
            user_chuan = ""
            
            # Kiểm tra tài khoản admin (Cứng)
            if username_input == "admin" and password_input == "32531235":
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.rerun()
            else:
                # Dùng danh sách từ file Excel làm gốc
                danh_sach_excel = df_nv_excel['Tên nhân viên'].astype(str).str.strip().tolist()
                
                for name in danh_sach_excel:
                    if username_input.lower() == name.lower():
                        user_found = True
                        user_chuan = name 
                        break
                
                if user_found:
                    # Kiểm tra xem đã có pass trong GG Sheet chưa, nếu chưa thì 123456
                    mat_khau_thuc_te = "123456" 
                    cred_row = df_credentials[df_credentials['Tên nhân viên'].astype(str).str.strip().str.lower() == user_chuan.lower()]
                    
                    if not cred_row.empty:
                        mat_khau_thuc_te = str(cred_row.iloc[0]['Mật khẩu']).strip()
                    
                    if password_input == mat_khau_thuc_te:
                        st.session_state.logged_in = True
                        st.session_state.current_user = user_chuan
                        st.rerun()
                    else:
                        st.error("❌ Sai mật khẩu!")
                else:
                    st.error("❌ Tên đăng nhập không tồn tại trong danh sách hệ thống!")
    st.stop()

# --- GIAO DIỆN CHÍNH & TÍNH NĂNG ĐỔI MẬT KHẨU/QUẢN LÝ TÀI KHOẢN ---
col_title, col_logout = st.columns([7, 3]) 
with col_title:
    st.title(f"📊 Tình Hình Nghỉ Phép - {st.session_state.current_user}")
with col_logout:
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.session_state.current_user == "Quản Trị Viên":
            btn_manage_account = st.button("🛠 Quản lý TK", use_container_width=True)
        else:
            btn_manage_account = st.button("🔑 Đổi mật khẩu", use_container_width=True)
    with col_btn2:
        if st.button("🚪 Đăng xuất", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.current_user = ""
            st.rerun()

# Hộp thoại mở rộng
if 'show_modal' not in st.session_state:
    st.session_state.show_modal = False

if btn_manage_account:
    st.session_state.show_modal = not st.session_state.show_modal

if st.session_state.show_modal:
    # 1. Giao diện ADMIN
    if st.session_state.current_user == "Quản Trị Viên":
        with st.form("admin_manage_form"):
            st.subheader("🛠 Quản lý tài khoản nhân viên")
            # Danh sách để sửa dựa trên Excel 
            list_nv_to_edit = sorted(df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist())
            target_nv = st.selectbox("Chọn nhân viên cần chỉnh sửa:", list_nv_to_edit)
            
            new_name = st.text_input("Tên mới (Để trống nếu chỉ muốn đổi mật khẩu)")
            new_pass = st.text_input("Mật khẩu mới (Để trống nếu chỉ muốn đổi tên)")
            
            submit_admin = st.form_submit_button("Cập Nhật Tài Khoản")
            if submit_admin:
                if not new_name.strip() and not new_pass.strip():
                    st.warning("Vui lòng nhập Tên mới hoặc Mật khẩu mới để cập nhật.")
                else:
                    success, msg = update_account_by_admin(target_nv, new_name, new_pass)
                    if success:
                        st.success(f"✅ {msg}")
                        st.session_state.show_modal = False
                    else:
                        st.error(f"❌ {msg}")
                        
    # 2. Giao diện NHÂN VIÊN
    else:
        with st.form("change_pass_form"):
            st.subheader(f"Đổi mật khẩu cho: {st.session_state.current_user}")
            old_pass = st.text_input("Mật khẩu hiện tại", type="password")
            new_pass = st.text_input("Mật khẩu mới", type="password")
            confirm_pass = st.text_input("Xác nhận mật khẩu mới", type="password")
            submit_pass = st.form_submit_button("Cập Nhật Mật Khẩu")
            
            if submit_pass:
                # Lấy mật khẩu hiện tại (có thể là 123456 nếu nhân viên mới tinh)
                db_old_pass = "123456" 
                cred_row = df_credentials[df_credentials['Tên nhân viên'].astype(str).str.strip().str.lower() == st.session_state.current_user.lower()]
                if not cred_row.empty:
                    db_old_pass = str(cred_row.iloc[0]['Mật khẩu']).strip()
                
                if old_pass != db_old_pass:
                    st.error("❌ Mật khẩu hiện tại không chính xác!")
                elif not new_pass or len(new_pass.strip()) < 4:
                    st.error("❌ Mật khẩu mới quá ngắn (tối thiểu 4 ký tự).")
                elif new_pass != confirm_pass:
                    st.error("❌ Xác nhận mật khẩu mới không khớp!")
                else:
                    success, msg = update_password_in_sheet(st.session_state.current_user, new_pass.strip())
                    if success:
                        st.success(f"✅ {msg}")
                        st.session_state.show_modal = False
                    else:
                        st.error(f"❌ {msg}")

st.markdown("---")

# Bộ lọc 
col_date, col_name, col_refresh = st.columns([4, 4, 2])

with col_date:
    today = date.today()
    filter_type = st.selectbox(
        "Lọc thời gian:", 
        ["Hôm nay", "Hôm qua", "Tuần này", "Tuần trước", "Tháng này", "Tháng trước", "Chọn ngày", "Khoảng thời gian"]
    )
    
    if filter_type == "Hôm nay":
        start_date = today
        end_date = today
    elif filter_type == "Hôm qua":
        start_date = today - timedelta(days=1)
        end_date = start_date
    elif filter_type == "Tuần này":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif filter_type == "Tuần trước":
        start_date = today - timedelta(days=today.weekday() + 7)
        end_date = start_date + timedelta(days=6)
    elif filter_type == "Tháng này":
        start_date = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end_date = today.replace(day=last_day)
    elif filter_type == "Tháng trước":
        first_day_this_month = today.replace(day=1)
        end_date = first_day_this_month - timedelta(days=1)
        start_date = end_date.replace(day=1)
    elif filter_type == "Chọn ngày":
        start_date = st.date_input("Chọn ngày:", today, label_visibility="collapsed")
        end_date = start_date
    elif filter_type == "Khoảng thời gian":
        date_range = st.date_input("Chọn khoảng thời gian:", [today, today], label_visibility="collapsed")
        if len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date = date_range[0]
            end_date = date_range[0]

with col_name:
    list_nv = ["- Tất cả nhân viên -"] + sorted(df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().unique().tolist())
    selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", list_nv)

with col_refresh:
    st.write("") 
    if st.button("🔄 Cập Nhật Dữ Liệu", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Lọc dữ liệu
mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
filtered_df = df_lich[mask_date]

if selected_nv != "- Tất cả nhân viên -":
    filtered_df = filtered_df[filtered_df['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]

co_phep_df = filtered_df[filtered_df['Số ngày tính'] > 0]
khong_phep_df = filtered_df[(filtered_df['Số ngày tính'] == 0) & (filtered_df['Phạt vi phạm'] > 0)]

tong_ngay_co_phep = co_phep_df['Số ngày tính'].sum()

# Thống kê KPI
st.write("") 
col1, col2, col3 = st.columns(3)
col1.metric("Tổng lượt ghi nhận", len(filtered_df))
col2.metric("✅ Số NGÀY nghỉ CÓ phép", f"{tong_ngay_co_phep:g}")
col3.metric("❌ Số LƯỢT nghỉ KHÔNG phép", len(khong_phep_df))

cols_to_hide = ['Phạt vi phạm']
export_df = filtered_df.drop(columns=cols_to_hide, errors='ignore')

# Nút Export Excel
col_header, col_download = st.columns([7, 3])
with col_header:
    st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")
with col_download:
    st.write("") 
    if not export_df.empty:
        excel_data = to_excel(export_df)
        file_name = f"LichNghi_{start_date.strftime('%d%m%Y')}_to_{end_date.strftime('%d%m%Y')}.xlsx"
        st.download_button(
            label="📥 Tải Dữ Liệu Lọc Xuống (Excel)",
            data=excel_data,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    else:
        st.button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", disabled=True, use_container_width=True, help="Không có dữ liệu để tải về.")

if filtered_df.empty:
    available_dates = df_lich['Ngày'].dropna().unique()
    if len(available_dates) > 0:
        available_dates = sorted(available_dates, reverse=True)[:5]
        dates_str = ", ".join([d.strftime('%d/%m/%Y') for d in available_dates])
        st.info(f"💡 Hệ thống không thấy dữ liệu trong khoảng thời gian này.\n\nCác ngày đang có dữ liệu chung trên hệ thống: **{dates_str}**")
        
    if selected_nv != "- Tất cả nhân viên -":
        nv_history_df = df_lich[df_lich['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]
        
        if not nv_history_df.empty:
            nv_co_phep = nv_history_df[nv_history_df['Số ngày tính'] > 0]
            nv_khong_phep = nv_history_df[(nv_history_df['Số ngày tính'] == 0) & (nv_history_df['Phạt vi phạm'] > 0)]
            
            with st.expander(f"📅 Bấm vào đây để xem TẤT CẢ lịch sử nghỉ phép của nhân viên {selected_nv}"):
                col_hist_text, col_hist_btn = st.columns([7, 3])
                with col_hist_text:
                    st.markdown(f"**Thống kê toàn thời gian:** Đã nghỉ **{nv_co_phep['Số ngày tính'].sum():g}** ngày CÓ phép | Vi phạm **{len(nv_khong_phep)}** lượt KHÔNG phép.")
                with col_hist_btn:
                    nv_export_df = nv_history_df.drop(columns=cols_to_hide, errors='ignore')
                    nv_excel_data = to_excel(nv_export_df)
                    st.download_button(
                        label=f"📥 Tải Toàn Bộ Lịch Sử",
                        data=nv_excel_data,
                        file_name=f"LichSu_{selected_nv}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                st.dataframe(nv_history_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
        else:
            st.warning(f"Nhân viên **{selected_nv}** hiện chưa từng có dữ liệu nghỉ phép trên hệ thống.")

# Hiển thị bảng chi tiết theo Tab
tab1, tab2, tab3 = st.tabs(["Tất cả danh sách", "Danh sách Nghỉ CÓ phép", "Danh sách Nghỉ KHÔNG phép"])

with tab1:
    st.dataframe(export_df, use_container_width=True, hide_index=True)
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
