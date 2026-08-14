import streamlit as st
import pandas as pd
from datetime import date, timedelta, datetime, timezone
import calendar
import requests
import os
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

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
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None

# --- HÀM TẢI MẬT KHẨU VÀ PHÂN QUYỀN ---
@st.cache_data(ttl=30)
def load_credentials():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
            rows = sheet.get_all_values()
            if len(rows) > 1:
                data_list = []
                for idx, row in enumerate(rows[1:], start=2):
                    stt = row[0] if len(row) > 0 else idx - 1
                    ten = row[1] if len(row) > 1 else ""
                    pwd = row[2] if len(row) > 2 else "123456"
                    role = row[3] if len(row) > 3 else "nhanvien"
                    if str(ten).strip() != "":
                        data_list.append({
                            'STT': stt,
                            'Tên nhân viên': str(ten).strip(),
                            'Mật khẩu': str(pwd).strip() if str(pwd).strip() else "123456",
                            'Phân quyền': str(role).strip().lower() if str(role).strip() else "nhanvien"
                        })
                return pd.DataFrame(data_list)
    except Exception:
        pass
    
    try:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_MAT_KHAU_ID}/gviz/tq?tqx=out:csv"
        df = pd.read_csv(url)
        return df
    except Exception as e:
        return pd.DataFrame(columns=['STT', 'Tên nhân viên', 'Mật khẩu', 'Phân quyền'])

# --- TẢI DỮ LIỆU TỪ GOOGLE SHEET DỰ PHÒNG ---
@st.cache_data(ttl=10)
def load_backup_sheet_data():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
            rows = sheet.get_all_values()
            if len(rows) > 1:
                df_bk = pd.DataFrame(rows[1:], columns=rows[0])
                if 'Loại nghỉ' in df_bk.columns:
                    df_bk.rename(columns={'Loại nghỉ': 'Lý do nghỉ'}, inplace=True)
                return df_bk
    except Exception:
        pass
    return pd.DataFrame(columns=["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Phạt vi phạm", "Ngày tạo", "Người tạo"])

# --- TẢI DỮ LIỆU LÝ DO NGHỈ TỪ GOOGLE SHEET DỰ PHÒNG ---
@st.cache_data(ttl=60)
def load_loai_nghi_from_gsheet():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
            rows = sheet.get_all_values()
            if len(rows) > 1:
                df_loai = pd.DataFrame(rows[1:], columns=rows[0])
                return df_loai
    except Exception as e:
        pass
    return pd.DataFrame()


# --- GHI LỊCH NGHỈ VÀO GOOGLE SHEET DỰ PHÒNG ---
def save_lich_nghi_to_backup_sheet(ngay, nv, loai_nghi, chi_tiet, so_ngay, phat_vi_pham, nguoi_tao):
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        all_vals = sheet.get_all_values()
        if len(all_vals) == 0:
            sheet.append_row(["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Phạt vi phạm", "Ngày tạo", "Người tạo"])
        
        sheet.append_row([
            str(ngay),
            str(nv),
            str(loai_nghi),
            str(chi_tiet),
            float(so_ngay),
            float(phat_vi_pham),
            str(date.today()),
            str(nguoi_tao)
        ])
        st.cache_data.clear()
        return True, "Đã ghi nhận lịch nghỉ thành công vào Google Sheet dự phòng!"
    except Exception as e:
        return False, f"Lỗi ghi Google Sheet dự phòng: {e}"

# --- XÓA DÒNG LỊCH NGHỈ TRÊN GOOGLE SHEET DỰ PHÒNG ---
def delete_backup_row(row_index_1_based):
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        sheet.delete_rows(row_index_1_based)
        st.cache_data.clear()
        return True, "Đã xóa lịch nghỉ thành công!"
    except Exception as e:
        return False, f"Lỗi xóa dòng: {e}"

# --- QUẢN LÝ TÀI KHOẢN (ADMIN) ---
def admin_manage_account(action, target_name, new_name="", new_pass="", new_role="nhanvien"):
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối Google Sheets (Secrets)."
        
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        
        if action == "Thêm mới":
            all_values = sheet.get_all_values()
            for r in all_values[1:]:
                if len(r) > 1 and str(r[1]).strip().lower() == target_name.strip().lower():
                    return False, f"Nhân viên '{target_name}' đã tồn tại trong hệ thống tài khoản!"
            next_stt = len(all_values)
            pass_to_set = new_pass.strip() if new_pass.strip() else "123456"
            sheet.append_row([next_stt, target_name.strip(), pass_to_set, new_role])
            st.cache_data.clear()
            return True, f"Đã thêm thành công tài khoản: {target_name.strip()}"
            
        elif action == "Chỉnh sửa":
            cells = sheet.findall(target_name, in_column=2)
            if not cells:
                all_values = sheet.get_all_values()
                next_stt = len(all_values)
                sheet.append_row([next_stt, target_name.strip(), "123456", new_role])
                st.cache_data.clear()
                return True, f"Đã khởi tạo và cập nhật tài khoản: {target_name}"
                
            row_idx = cells[0].row
            final_name = new_name.strip() if new_name and new_name.strip() else target_name
            if final_name != target_name:
                sheet.update_cell(row_idx, 2, final_name)
            if new_pass and new_pass.strip():
                sheet.update_cell(row_idx, 3, new_pass.strip())
            if new_role:
                sheet.update_cell(row_idx, 4, new_role)
                
            st.cache_data.clear()
            return True, f"Đã cập nhật thành công tài khoản: {final_name}"
            
        elif action == "Xóa":
            cells = sheet.findall(target_name, in_column=2)
            if not cells:
                return False, "Không tìm thấy tài khoản cần xóa."
            sheet.delete_rows(cells[0].row)
            st.cache_data.clear()
            return True, f"Đã xóa thành công tài khoản: {target_name}"
            
        return False, "Hành động không hợp lệ."
    except Exception as e:
        return False, f"Lỗi thực thi: {e}"

def update_password_in_sheet(username, new_password):
    try:
        client = get_gspread_client()
        if not client:
            return False, "Chưa cấu hình quyền kết nối."
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        cells = sheet.findall(username, in_column=2)
        if cells:
            sheet.update_cell(cells[0].row, 3, str(new_password))
        else:
            all_values = sheet.get_all_values()
            next_stt = len(all_values)
            sheet.append_row([next_stt, username, str(new_password), "nhanvien"])
        st.cache_data.clear() 
        return True, "Đổi mật khẩu thành công!"
    except Exception as e:
        return False, f"Lỗi cập nhật: {e}"

# --- TẢI FILE TỪ GOOGLE DRIVE ---
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
        
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV', 'LoaiNghi'], engine='pyxlsb')
        df_lich = xls['LichNghi']
        df_nv_excel = xls['DanhSachNV']
        df_loai_nghi = xls['LoaiNghi']
        
        if os.path.exists(temp_file):
            os.remove(temp_file)
            
        df_lich = df_lich.iloc[:, :10]
        df_lich.columns = [
            'Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 
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
        
        return df_lich, df_nv_excel, df_loai_nghi
    except Exception as e:
        st.error(f"Lỗi đọc dữ liệu: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

@st.cache_data(show_spinner=False)
def to_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DuLieuLichNghi')
    return output.getvalue()

# Tải dữ liệu 
df_credentials = load_credentials() 
df_backup = load_backup_sheet_data()
df_loai_nghi_gsheet = load_loai_nghi_from_gsheet()

GDRIVE_LINK = "https://drive.google.com/file/d/1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT/view?usp=sharing"

with st.spinner("Đang tải dữ liệu hệ thống..."):
    df_lich, df_nv_excel, df_loai_nghi_excel = load_lich_nghi(GDRIVE_LINK) 

if not df_loai_nghi_gsheet.empty:
    df_loai_nghi = df_loai_nghi_gsheet
else:
    df_loai_nghi = df_loai_nghi_excel

if df_lich.empty or df_nv_excel.empty:
    st.warning("Hệ thống chưa tìm thấy dữ liệu. Vui lòng kiểm tra lại cấu hình.")
    if st.button("🔄 Tải lại dữ liệu"):
        st.cache_data.clear()
        st.rerun()
    st.stop()


# --- HỆ THỐNG ĐĂNG NHẬP & PHÂN QUYỀN ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_user" not in st.session_state:
    st.session_state.current_user = ""
if "current_role" not in st.session_state:
    st.session_state.current_role = ""

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ thống")
    
    with st.form("login_form"):
        username_input = st.text_input("Tên đăng nhập").strip()
        password_input = st.text_input("Mật khẩu", type="password")
        submit = st.form_submit_button("Đăng Nhập")
        
        if submit:
            user_found = False
            user_chuan = ""
            user_role = "nhanvien"
            
            if username_input == "admin" and password_input == "32531235":
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.session_state.current_role = "admin"
                st.rerun()
            else:
                for _, row in df_credentials.iterrows():
                    db_name = str(row['Tên nhân viên']).strip()
                    db_pass = str(row['Mật khẩu']).strip()
                    db_role = str(row.get('Phân quyền', 'nhanvien')).strip().lower()
                    
                    if username_input.lower() == db_name.lower():
                        if password_input == db_pass:
                            user_found = True
                            user_chuan = db_name
                            user_role = db_role if db_role else "nhanvien"
                            break
                
                if user_found:
                    st.session_state.logged_in = True
                    st.session_state.current_user = user_chuan
                    st.session_state.current_role = user_role
                    st.rerun()
                else:
                    st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop()


# --- GIAO DIỆN CHÍNH ---
col_title, col_logout = st.columns([7, 3]) 
with col_title:
    role_label = "Quản Trị Viên" if st.session_state.current_role == "admin" else ("Lễ Tân" if st.session_state.current_role == "letan" else "Nhân Viên")
    st.title(f"📊 Tình Hình Nghỉ Phép - {st.session_state.current_user} ({role_label})")
with col_logout:
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.session_state.current_role == "admin":
            btn_manage_account = st.button("🛠 Quản lý TK", use_container_width=True)
        else:
            btn_manage_account = st.button("🔑 Đổi mật khẩu", use_container_width=True)
    with col_btn2:
        if st.button("🚪 Đăng xuất", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.current_user = ""
            st.session_state.current_role = ""
            st.rerun()

# Modal Quản lý tài khoản / Đổi mật khẩu
if 'show_modal' not in st.session_state:
    st.session_state.show_modal = False

if btn_manage_account:
    st.session_state.show_modal = not st.session_state.show_modal

if st.session_state.show_modal:
    if st.session_state.current_role == "admin":
        st.subheader("🛠 Quản lý tài khoản & Phân quyền")
        tab_add, tab_edit, tab_del = st.tabs(["➕ Thêm nhân viên mới", "✏️ Chỉnh sửa / Đổi vai trò", "🗑️ Xóa tài khoản"])
        
        users_from_sheet = df_credentials['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_credentials.empty else []
        users_from_excel = df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_nv_excel.empty else []
        existing_users = sorted(list(set(users_from_sheet + users_from_excel)))
        
        with tab_add:
            with st.form("form_add_acc"):
                new_name_in = st.text_input("Tên nhân viên mới:").strip()
                new_pass_in = st.text_input("Mật khẩu ban đầu (Để trống sẽ là 123456):", type="password")
                new_role_in = st.selectbox("Vai trò / Phân quyền:", ["nhanvien", "letan", "admin"], index=0, key="role_add")
                submit_add = st.form_submit_button("Thêm Tài Khoản")
                if submit_add:
                    if not new_name_in:
                        st.error("❌ Vui lòng nhập tên nhân viên mới!")
                    else:
                        success, msg = admin_manage_account("Thêm mới", new_name_in, new_name_in, new_pass_in, new_role_in)
                        if success:
                            st.success(f"✅ {msg}")
                            st.session_state.show_modal = False
                        else:
                            st.error(f"❌ {msg}")
                            
        with tab_edit:
            with st.form("form_edit_acc"):
                target_edit = st.selectbox("Chọn tài khoản cần chỉnh sửa:", existing_users) if existing_users else ""
                edit_name_in = st.text_input("Tên mới (Để trống nếu giữ nguyên):").strip()
                edit_pass_in = st.text_input("Mật khẩu mới (Để trống nếu giữ nguyên):", type="password")
                curr_role_val = "nhanvien"
                if not df_credentials.empty and target_edit:
                    match_row = df_credentials[df_credentials['Tên nhân viên'].astype(str).str.strip().str.lower() == target_edit.lower()]
                    if not match_row.empty:
                        curr_role_val = str(match_row.iloc[0].get('Phân quyền', 'nhanvien')).strip().lower()
                role_indices = {"nhanvien": 0, "letan": 1, "admin": 2}
                default_idx = role_indices.get(curr_role_val, 0)
                edit_role_in = st.selectbox("Vai trò / Phân quyền mới:", ["nhanvien", "letan", "admin"], index=default_idx, key="role_edit")
                submit_edit = st.form_submit_button("Cập Nhật")
                if submit_edit:
                    if not target_edit:
                        st.error("❌ Vui lòng chọn tài khoản cần chỉnh sửa!")
                    else:
                        success, msg = admin_manage_account("Chỉnh sửa", target_edit, edit_name_in, edit_pass_in, edit_role_in)
                        if success:
                            st.success(f"✅ {msg}")
                            st.session_state.show_modal = False
                        else:
                            st.error(f"❌ {msg}")
                            
        with tab_del:
            with st.form("form_del_acc"):
                target_del = st.selectbox("Chọn tài khoản cần xóa:", existing_users) if existing_users else ""
                submit_del = st.form_submit_button("Xóa Tài Khoản")
                if submit_del:
                    if not target_del:
                        st.error("❌ Vui lòng chọn tài khoản cần xóa!")
                    else:
                        success, msg = admin_manage_account("Xóa", target_del)
                        if success:
                            st.success(f"✅ {msg}")
                            st.session_state.show_modal = False
                        else:
                            st.error(f"❌ {msg}")
    else:
        with st.form("change_pass_form"):
            st.subheader(f"Đổi mật khẩu cho: {st.session_state.current_user}")
            old_pass = st.text_input("Mật khẩu hiện tại", type="password")
            new_pass = st.text_input("Mật khẩu mới", type="password")
            confirm_pass = st.text_input("Xác nhận mật khẩu mới", type="password")
            submit_pass = st.form_submit_button("Cập Nhật Mật Khẩu")
            if submit_pass:
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

# --- TAB QUY TẮC & MỨC PHẠT (ADMIN) ---
if st.session_state.current_role == "admin":
    with st.expander("⚙️ Cấu hình Quy tắc & Mức phạt chuẩn (Độc quyền Admin)", expanded=False):
        st.markdown("### 📋 Danh Mục Lý Do Nghỉ & Mức Phạt Chuẩn")
        st.info("Bảng dưới đây hiển thị toàn bộ quy định lý do nghỉ, số ngày tính và mức phạt hiện đang được hệ thống áp dụng tự động.")
        
        if not df_loai_nghi.empty:
            if 'Lý do nghỉ' in df_loai_nghi.columns:
                cols_available = [col for col in ["Lý do nghỉ", "Chi tiết / Ghi chú", "Số ngày tính", "Phạt vi phạm"] if col in df_loai_nghi.columns]
                df_display_rule = df_loai_nghi[cols_available].dropna(subset=["Lý do nghỉ"])
            elif 'Loại nghỉ' in df_loai_nghi.columns:
                cols_available = [col for col in ["Loại nghỉ", "Chi tiết / Ghi chú", "Số ngày tính", "Phạt vi phạm"] if col in df_loai_nghi.columns]
                df_display_rule = df_loai_nghi[cols_available].dropna(subset=["Loại nghỉ"])
                df_display_rule.rename(columns={'Loại nghỉ': 'Lý do nghỉ'}, inplace=True)
            else:
                cols_to_use = [1, 2, 3, 4] if len(df_loai_nghi.columns) > 4 else [1, 2, 3]
                df_display_rule = df_loai_nghi.iloc[:, cols_to_use].dropna(subset=[df_loai_nghi.columns[1]])
                if len(df_display_rule.columns) == 4:
                    df_display_rule.columns = ["Lý do nghỉ", "Chi tiết / Ghi chú", "Số ngày tính", "Phạt vi phạm"]
                else:
                    df_display_rule.columns = ["Lý do nghỉ", "Chi tiết / Ghi chú", "Số ngày tính"]
            
            st.dataframe(df_display_rule, use_container_width=True, hide_index=True)
            st.markdown("*(Lưu ý: Hệ thống hiện đang ưu tiên lấy bảng quy tắc từ Google Sheets. Bạn có thể thay đổi số liệu trực tiếp trên file Sheets).*")
        else:
            st.warning("Chưa tải được dữ liệu bảng quy tắc lý do nghỉ.")

st.markdown("---")

# --- KHU VỰC NHẬP LỊCH NGHỈ & QUẢN LÝ ---
if st.session_state.current_role in ["admin", "letan"]:
    with st.expander("📝 Nhập lịch nghỉ mới & Quản lý lịch đã đăng ký (Dành cho Lễ Tân & Admin)", expanded=False):
        tab_input_lich, tab_manage_lich = st.tabs(["➕ Nhập lịch nghỉ mới", "✏️ Quản lý / Xóa lịch đã đăng ký"])
        
        users_s = df_credentials['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_credentials.empty else []
        users_e = df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_nv_excel.empty else []
        list_nv_input = sorted(list(set(users_s + users_e)))
        
        list_loai_nghi = []
        loai_nghi_dict = {}
        if not df_loai_nghi.empty:
            for idx, row in df_loai_nghi.iterrows():
                l_name = ""
                if len(row) > 1:
                    l_name = str(row.iloc[1]).strip()
                
                if not l_name or l_name.lower() in ["nan", "none"]:
                    l_name = str(row.get('Lý do nghỉ', row.get('Loại nghỉ', ''))).strip()
                
                if l_name and l_name.lower() not in ["nan", "loại nghỉ", "lý do nghỉ", "none", ""]:
                    list_loai_nghi.append(l_name)
                    
                    try:
                        s_ngay_raw = str(row.get('Số ngày tính', row.iloc[3] if len(row)>3 else 1.0))
                        s_ngay = float(s_ngay_raw.replace(',', '').strip()) if s_ngay_raw.strip() else 1.0
                    except:
                        s_ngay = 1.0
                    
                    try:
                        p_raw = str(row.get('Phạt vi phạm', row.iloc[4] if len(row)>4 else "0")).strip()
                        p_str = p_raw.replace(',', '').replace(' ', '')
                        p_val = 0.0 if p_str in ["", "-", "nan", "None"] else float(p_str)
                    except:
                        p_val = 0.0
                        
                    loai_nghi_dict[l_name.lower()] = [s_ngay, p_val]
                    
        if not list_loai_nghi:
            list_loai_nghi = ["Nghỉ phép", "Nghỉ không phép", "Nghỉ phát sinh", "Đi trễ không phép"]

        with tab_input_lich:
            chosen_nv = st.selectbox("Chọn nhân viên:", ["-- Chọn nhân viên --"] + list_nv_input, key="sb_chosen_nv")
            chosen_date = st.date_input("Chọn ngày nghỉ:", date.today(), key="sb_chosen_date")
            chosen_loai = st.selectbox("Lý do nghỉ:", ["-- Chọn lý do nghỉ --"] + list_loai_nghi, key="sb_loai_nghi_live")
            
            default_songay = 1.0
            default_phat = 0.0
            if chosen_loai and chosen_loai != "-- Chọn lý do nghỉ --" and chosen_loai.lower() in loai_nghi_dict:
                default_songay = loai_nghi_dict[chosen_loai.lower()][0]
                default_phat = loai_nghi_dict[chosen_loai.lower()][1]

            is_weekend = chosen_date.weekday() >= 5
            count_same_day = 0
            if not df_lich.empty and chosen_loai != "-- Chọn lý do nghỉ --":
                count_same_day = len(df_lich[(df_lich['Ngày'] == chosen_date) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == chosen_loai.lower())])
            
            auto_extra_penalty = 0.0
            norm_loai = chosen_loai.lower() if chosen_loai else ""
            if not is_weekend and "ra ngoài vào muộn" not in norm_loai and count_same_day >= 2:
                auto_extra_penalty = (count_same_day - 1) * 100000.0
            
            final_phat = default_phat + auto_extra_penalty

            with st.form("form_nhap_lich_inner"):
                input_chitiet = st.text_input("Chi tiết vi phạm / Ghi chú (nếu có):").strip()
                
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    val_songay = st.number_input("Số ngày tính:", value=float(default_songay), step=0.5, key="num_songay_input")
                with col_p2:
                    val_phat = st.number_input("Mức phạt vi phạm (VNĐ):", value=float(final_phat), step=50000.0, key=f"num_phat_{chosen_loai}")

                submit_lich = st.form_submit_button("💾 Xác Nhận Ghi Lịch Nghỉ")
                
                if submit_lich:
                    if chosen_nv == "-- Chọn nhân viên --" or not chosen_nv:
                        st.error("❌ Vui lòng chọn nhân viên cần nhập lịch nghỉ!")
                    elif chosen_loai == "-- Chọn lý do nghỉ --" or not chosen_loai:
                        st.error("❌ Vui lòng chọn lý do nghỉ!")
                    else:
                        norm_loai_submit = chosen_loai.strip().lower()
                        can_proceed = True
                        
                        if norm_loai_submit == "nghỉ phát sinh":
                            vn_tz = timezone(timedelta(hours=7))
                            current_hour = datetime.now(vn_tz).hour
                            
                            if current_hour < 9 or current_hour >= 17:
                                st.error("❌ Lý do 'Nghỉ phát sinh' chỉ được phép nhập vào hệ thống trong khung giờ từ 09:00 đến 17:00!")
                                can_proceed = False
                            elif is_weekend:
                                st.error("❌ Thứ 7 và Chủ nhật không được phép 'Nghỉ phát sinh'!")
                                can_proceed = False
                            else:
                                count_ps = 0
                                if not df_lich.empty:
                                    count_ps += len(df_lich[(df_lich['Ngày'] == chosen_date) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == "nghỉ phát sinh")])
                                
                                if not df_backup.empty:
                                    col_ly_do = 'Lý do nghỉ' if 'Lý do nghỉ' in df_backup.columns else 'Loại nghỉ'
                                    count_ps += len(df_backup[(df_backup['Ngày'].astype(str).str.strip() == chosen_date.strftime('%d/%m/%Y')) & (df_backup[col_ly_do].astype(str).str.strip().str.lower() == "nghỉ phát sinh")])
                                
                                if count_ps >= 2:
                                    st.error("❌ Ngày này đã đạt giới hạn tối đa 2 người 'Nghỉ phát sinh'!")
                                    can_proceed = False

                        if can_proceed:
                            already_booked_today = False
                            if not df_backup.empty:
                                col_ly_do = 'Lý do nghỉ' if 'Lý do nghỉ' in df_backup.columns else 'Loại nghỉ'
                                match_dup = df_backup[(df_backup['Ngày'].astype(str).str.strip() == chosen_date.strftime('%d/%m/%Y')) & 
                                                      (df_backup['Tên nhân viên'].astype(str).str.strip().str.lower() == chosen_nv.lower()) & 
                                                      (df_backup[col_ly_do].astype(str).str.strip().str.lower() == norm_loai_submit)]
                                if not match_dup.empty:
                                    already_booked_today = True
                            
                            if not df_lich.empty and not already_booked_today:
                                match_dup_main = df_lich[(df_lich['Ngày'] == chosen_date) & 
                                                           (df_lich['Tên nhân viên'].astype(str).str.strip().str.lower() == chosen_nv.lower()) & 
                                                           (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == norm_loai_submit)]
                                if not match_dup_main.empty:
                                    already_booked_today = True

                            if already_booked_today:
                                st.error(f"❌ Nhân viên **{chosen_nv}** đã được ghi nhận lịch nghỉ với lý do **'{chosen_loai}'** vào ngày {chosen_date.strftime('%d/%m/%Y')} rồi.")
                            else:
                                max_people = 5 if not is_weekend else 2
                                today_total_nghi = 0
                                if not df_lich.empty:
                                    today_total_nghi = len(df_lich[(df_lich['Ngày'] == chosen_date) & (df_lich['Số ngày tính'] > 0)])
                                
                                if val_songay > 0 and "phep nam" not in norm_loai_submit and today_total_nghi >= max_people:
                                    st.error(f"❌ Ngày {chosen_date.strftime('%d/%m/%Y')} đã đạt giới hạn tối đa {max_people} người nghỉ/ngày.")
                                else:
                                    success_bk, msg_bk = save_lich_nghi_to_backup_sheet(
                                        chosen_date.strftime('%d/%m/%Y'),
                                        chosen_nv,
                                        chosen_loai,
                                        input_chitiet,
                                        val_songay,
                                        val_phat,
                                        st.session_state.current_user
                                    )
                                    if success_bk:
                                        st.success(f"✅ Đã ghi nhận lịch nghỉ cho **{chosen_nv}** thành công!")
                                        st.cache_data.clear()
                                    else:
                                        st.error(f"❌ {msg_bk}")

        with tab_manage_lich:
            st.markdown("### 🗑️ Xóa / Quản lý lịch nghỉ đã đăng ký")
            if df_backup.empty:
                st.info("Chưa có lịch nghỉ nào được đăng ký gần đây trên hệ thống dự phòng.")
            else:
                st.dataframe(df_backup, use_container_width=True, hide_index=True)
                with st.form("form_delete_backup_row"):
                    col_ly_do_disp = 'Lý do nghỉ' if 'Lý do nghỉ' in df_backup.columns else 'Loại nghỉ'
                    row_options = [f"Dòng {i+1}: {row.get('Ngày')} - {row.get('Tên nhân viên')} - {row.get(col_ly_do_disp, '')}" for i, row in df_backup.iterrows()]
                    selected_row_str = st.selectbox("Chọn dòng lịch nghỉ cần xóa:", row_options)
                    submit_del_row = st.form_submit_button("🗑️ Xóa Lịch Nghỉ Đã Chọn")
                    
                    if submit_del_row and selected_row_str:
                        selected_idx = row_options.index(selected_row_str)
                        real_sheet_row = selected_idx + 2
                        success_del, msg_del = delete_backup_row(real_sheet_row)
                        if success_del:
                            st.success(f"✅ {msg_del}")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(f"❌ {msg_del}")

st.markdown("---")

# Bộ lọc thời gian & nhân viên
col_date, col_name, col_refresh = st.columns([4, 4, 2])

with col_date:
    today = date.today()
    filter_type = st.selectbox(
        "Lọc thời gian:", 
        ["Hôm nay", "Hôm qua", "Tuần này", "Tuần trước", "Tháng này", "Tháng trước", "Chọn ngày", "Khoảng thời gian"]
    )
    
    if filter_type == "Hôm nay":
        start_date = end_date = today
    elif filter_type == "Hôm qua":
        start_date = end_date = today - timedelta(days=1)
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
        start_date = end_date = st.date_input("Chọn ngày:", today, label_visibility="collapsed")
    elif filter_type == "Khoảng thời gian":
        date_range = st.date_input("Chọn khoảng thời gian:", [today, today], label_visibility="collapsed")
        start_date, end_date = (date_range[0], date_range[1]) if len(date_range) == 2 else (date_range[0], date_range[0])

with col_name:
    users_s = df_credentials['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_credentials.empty else []
    users_e = df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_nv_excel.empty else []
    all_nv_list = sorted(list(set(users_s + users_e)))
    list_nv = ["- Tất cả nhân viên -"] + all_nv_list
    selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", list_nv)

with col_refresh:
    st.write("") 
    if st.button("🔄 Cập Nhật Dữ Liệu", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Lọc dữ liệu theo thời gian và nhân viên
mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
filtered_df = df_lich[mask_date]

if selected_nv != "- Tất cả nhân viên -":
    filtered_df = filtered_df[filtered_df['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]


# --- ĐOẠN MỚI CẬP NHẬT: Loại trừ các lý do không tính vào KPI Nghỉ ---
# Sử dụng từ khóa (substring) để bắt mọi biến thể (có dấu, không dấu, viết hoa, viết thường)
# "phép năm" và "phep nam" đã được xóa khỏi danh sách này để chúng được tính là CÓ PHÉP
excluded_keywords = [
    "đi trễ", "di tre",
    "không dọn vệ sinh", "khong don ve sinh",
    "lỗi vi phạm", "loi vi pham",
    "qua tour",
    "xuống phòng", "xuong phong",
    "ra sớm", "ra som",
    "vào muộn", "vao muon",
    "đi tua", "di tua",
    "ngưng nhận", "ngung nhan",
    "hỗ trợ ca", "ho tro ca"
]

def is_excluded(reason):
    r = str(reason).lower()
    for kw in excluded_keywords:
        if kw in r:
            return True
    return False

valid_nghi_mask = ~filtered_df['Lý do nghỉ'].apply(is_excluded)
df_thuc_nghi = filtered_df[valid_nghi_mask]
ly_do_thuc_nghi_lower = df_thuc_nghi['Lý do nghỉ'].astype(str).str.strip().str.lower()

# Chia các nhóm dựa trên df_thuc_nghi để các số liệu khớp với "Tổng số người nghỉ"
phat_sinh_df = df_thuc_nghi[ly_do_thuc_nghi_lower == 'nghỉ phát sinh']
khong_phep_df = df_thuc_nghi[ly_do_thuc_nghi_lower.str.contains('không phép', na=False)]
co_phep_df = df_thuc_nghi[(ly_do_thuc_nghi_lower != 'nghỉ phát sinh') & (~ly_do_thuc_nghi_lower.str.contains('không phép', na=False))]

# Thống kê KPI mới (Bao gồm 4 cột như yêu cầu)
st.write("") 
col1, col2, col3, col4 = st.columns(4)
col1.metric("Tổng số người nghỉ", len(df_thuc_nghi))
col2.metric("✅ Số người nghỉ CÓ phép", len(co_phep_df))
col3.metric("⚠️ Số người nghỉ PHÁT SINH", len(phat_sinh_df))
col4.metric("❌ Số người nghỉ KHÔNG phép", len(khong_phep_df))

cols_to_hide = ['Phạt vi phạm']
# export_df chứa TẤT CẢ dữ liệu (bao gồm cả vi phạm) để xem và tải xuống
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
        st.button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", disabled=True, use_container_width=True)

# Hiển thị bảng chi tiết theo Tab (Cập nhật thêm tab Phát Sinh)
tab1, tab2, tab3, tab4 = st.tabs(["Tất cả danh sách", "Danh sách Nghỉ CÓ phép", "Danh sách Nghỉ PHÁT SINH", "Danh sách Nghỉ KHÔNG phép"])

with tab1:
    st.dataframe(export_df, use_container_width=True, hide_index=True)
with tab2:
    if co_phep_df.empty:
        st.info("Không có dữ liệu nhân viên nghỉ có phép.")
    else:
        st.dataframe(co_phep_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
with tab3:
    if phat_sinh_df.empty:
        st.info("Không có dữ liệu nhân viên nghỉ phát sinh.")
    else:
        st.dataframe(phat_sinh_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
with tab4:
    if khong_phep_df.empty:
        st.success("Tuyệt vời! Không có nhân viên nào nghỉ không phép.")
    else:
        st.dataframe(khong_phep_df.drop(columns=cols_to_hide, errors='ignore'), use_container_width=True, hide_index=True)
