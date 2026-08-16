import streamlit as st
import pandas as pd
from datetime import date, timedelta, datetime, timezone
import calendar
import requests
import os
import io
import gspread
import re
from google.oauth2.service_account import Credentials
import streamlit.components.v1 as components
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import unicodedata

# --- CẤU HÌNH MÚI GIỜ VIỆT NAM ---
VN_TZ = timezone(timedelta(hours=7))

def get_vn_today():
    return datetime.now(VN_TZ).date()

# --- CHUẨN HÓA TIẾNG VIỆT (LOẠI BỎ DẤU CHỮ HOA/THƯỜNG) ĐỂ ĐĂNG NHẬP ---
def remove_accents(s):
    s = str(s).strip()
    # Chuyển đổi Unicode chuẩn để xử lý triệt để chữ Tổ Hợp và Dựng Sẵn
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
    # Xử lý riêng chữ đ/Đ vì nó không phải là dấu
    s = s.replace('đ', 'd').replace('Đ', 'd')
    return s.lower()

def normalize_name(name):
    """Đồng nhất cách gõ Thúy/Thuý để tránh lỗi so sánh"""
    return str(name).replace("Thuý", "Thúy").replace("thuý", "thúy").strip()

# --- HÀM ĐỊNH DẠNG BẢNG HIỂN THỊ TRỰC QUAN ---
def format_display_df(df):
    d = df.copy()
    def fmt_num(val):
        if pd.isna(val) or val == "": return ""
        try:
            v = float(val)
            if v == 0: return ""
            return str(int(v)) if v.is_integer() else str(v)
        except: return str(val)
    
    for col in ['Số ngày tính', 'Số ngày phép cộng dồn']:
        if col in d.columns:
            d[col] = d[col].apply(fmt_num)
            
    if 'Ngày' in d.columns:
        d['Ngày'] = pd.to_datetime(d['Ngày'], errors='coerce').dt.strftime('%d/%m/%Y').fillna(d['Ngày'])
        
    return d

# --- HÀM GỬI EMAIL BÁO CÁO ---
def send_email_report(sender_email, sender_password, to_email, emp_name, df_emp, total_phat, start_str, end_str):
    try:
        subject = f"Báo cáo chi tiết lịch nghỉ và vi phạm - {emp_name} ({start_str} đến {end_str})"
        df_display = format_display_df(df_emp[['Ngày', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Phạt vi phạm']])
        df_display['Phạt vi phạm'] = df_display['Phạt vi phạm'].apply(lambda x: f"{float(x):,.0f}" if float(x) > 0 else "")
        
        html_table = df_display.to_html(index=False, justify='center')
        html_table = html_table.replace('<table border="1" class="dataframe">', '<table style="width:100%; border-collapse: collapse; border: 1px solid #ddd; font-family: Arial, sans-serif;">')
        html_table = html_table.replace('<th>', '<th style="background-color: #f2f2f2; border: 1px solid #ddd; padding: 8px; text-align: center;">')
        html_table = html_table.replace('<td>', '<td style="border: 1px solid #ddd; padding: 8px; text-align: center;">')
        
        html_content = f"""
        <html>
        <body>
            <p>Chào <b>{emp_name}</b>,</p>
            <p>Hệ thống quản lý Vera Spa gửi bạn chi tiết lịch nghỉ và vi phạm trong giai đoạn từ <b>{start_str}</b> đến <b>{end_str}</b>:</p>
            <br>
            {html_table}
            <br>
            <h3 style="color: red;">Tổng tiền phạt vi phạm: {total_phat:,.0f} VNĐ</h3>
            <p><i>Vui lòng kiểm tra lại thông tin. Nếu có bất kỳ sai sót nào, xin vui lòng phản hồi lại trong thời gian sớm nhất.</i></p>
            <br>
            <p>Trân trọng,</p>
            <p><b>VERA SPA</b></p>
        </body>
        </html>
        """
        msg = MIMEMultipart()
        msg['From'] = f"Vera Spa <{sender_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_content, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True, "Thành công"
    except Exception as e:
        return False, str(e)


# --- THEO DÕI SỐ NGƯỜI ĐANG TRUY CẬP & TRẠNG THÁI HỆ THỐNG ---
@st.cache_resource
def get_active_users():
    return {}

@st.cache_resource
def get_system_status():
    return {"lock_nv": False, "lock_login": False}

active_users = get_active_users()
system_status = get_system_status()

if st.session_state.get("logged_in") and st.session_state.get("current_user"):
    active_users[st.session_state.current_user] = time.time()

current_t = time.time()
for u in list(active_users.keys()):
    if current_t - active_users[u] > 300: 
        del active_users[u]

online_users_count = len(active_users)
online_users_list = list(active_users.keys())

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide", initial_sidebar_state="auto")

# --- ĐIỀU HƯỚNG PAGE QUA SESSION STATE ---
if "selected_page_nav" not in st.session_state:
    st.session_state.selected_page_nav = "📊 Tình Hình Nghỉ Phép"

# --- CHẶN SỰ KIỆN PHÍM TẮT CLEAR CACHE BẰNG JAVASCRIPT ---
components.html("""
<script>
    const parentDoc = window.parent.document;
    parentDoc.addEventListener('keydown', function(event) {
        if ((event.key === 'c' || event.key === 'C')) {
            const tag = event.target.tagName.toLowerCase();
            if (tag !== 'input' && tag !== 'textarea') {
                event.stopPropagation();
            }
        }
    }, true);
</script>
""", height=0, width=0)

# --- ÉP CSS GIAO DIỆN CỐ ĐỊNH ---
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Arial:wght@400;700&display=swap');
        
        html, body, [class*="st-"], .stMarkdown, .stText, div, span, p {
            font-family: 'Roboto', sans-serif !important;
            color: #333333 !important;
        }
        
        span.material-symbols-rounded, 
        [data-testid="stIconMaterial"], 
        .stIcon, 
        span[class*="stIcon"] {
            font-family: "Material Symbols Rounded" !important;
        }
        
        p, .stText, [data-testid="stMarkdownContainer"] {
            font-size: 16px !important;
        }
        
        .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
        
        @media (max-width: 768px) {
            .block-container { padding-top: 1rem !important; padding-left: 0.5rem !important; padding-right: 0.5rem !important; }
        }
        
        div[data-testid="stVerticalBlock"] > div { gap: 0.2rem !important; }
        button { margin-top: 5px !important; }
        
        .custom-main-title {
            font-family: 'Roboto', sans-serif !important;
            font-size: 35px; font-weight: bold; margin-bottom: 5px; color: #333 !important;
        }
        
        [data-testid="stExpander"] details summary p {
            font-size: 1.3rem !important;
            font-weight: 700 !important;
            color: #d32f2f !important;
            text-transform: uppercase;
        }
        
        /* Chỉnh nút Back, Next, Home */
        .nav-btn button {
            background-color: #f0f2f6; border: 1px solid #ccc; font-weight: bold;
        }
    </style>
""", unsafe_allow_html=True)

# --- KẾT NỐI GSPREAD ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
SHEET_CHINH_ID = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT" 

@st.cache_resource
def get_gspread_client():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        return None

# --- ĐỒNG BỘ EXCEL SANG GOOGLE SHEETS ---
def admin_sync_excel_to_gsheet():
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        file_id = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
        temp_file = "temp_sync.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file): os.remove(temp_file)
        
        df_excel = xls.iloc[:, :10].copy()
        
        def clean_val(val, is_date=False):
            try:
                if pd.isna(val) or str(val).strip() in ["nan", "NaT", "None", ""]: return ""
                if is_date or hasattr(val, 'strftime'):
                    if hasattr(val, 'strftime'): return val.strftime('%d/%m/%Y')
                    if isinstance(val, (int, float)): return pd.to_datetime(val, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
                    return pd.to_datetime(str(val).strip().split(' ')[0], dayfirst=True).strftime('%d/%m/%Y')
                return str(val).strip()
            except: return str(val).strip()

        cols = df_excel.columns.tolist()
        if len(cols) > 0: df_excel[cols[0]] = df_excel[cols[0]].apply(lambda x: clean_val(x, is_date=True))
        if len(cols) > 7: df_excel[cols[7]] = df_excel[cols[7]].apply(lambda x: clean_val(x, is_date=True))
        for c in cols:
            if c not in [cols[0], cols[7]] if len(cols)>7 else [cols[0]]:
                df_excel[c] = df_excel[c].apply(lambda x: clean_val(x))

        df_excel = df_excel.fillna("")
        df_excel.columns = ["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"]
        
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        gsheet_data = sheet_dp.get_all_values()
        
        if len(gsheet_data) > 1:
            df_gsheet = pd.DataFrame(gsheet_data[1:], columns=gsheet_data[0])
        else:
            df_gsheet = pd.DataFrame(columns=df_excel.columns)

        df_gsheet['Merge_Key'] = df_gsheet['Ngày'].astype(str) + "_" + df_gsheet['Tên nhân viên'].apply(normalize_name) + "_" + df_gsheet.get('Lý do nghỉ', df_gsheet.get('Loại nghỉ', '')).astype(str)
        df_excel['Merge_Key'] = df_excel['Ngày'].astype(str) + "_" + df_excel['Tên nhân viên'].apply(normalize_name) + "_" + df_excel['Lý do nghỉ'].astype(str)
        
        new_rows_df = df_excel[~df_excel['Merge_Key'].isin(df_gsheet['Merge_Key'])].drop(columns=['Merge_Key'])
        
        if new_rows_df.empty: return True, "Không có dữ liệu mới nào từ Excel để đồng bộ."

        values_to_append = new_rows_df.values.tolist()
        sheet_dp.append_rows(values_to_append, value_input_option='USER_ENTERED')
        st.cache_data.clear()
        return True, f"Đã thêm mới {len(values_to_append)} dòng dữ liệu từ Excel lên Sheet!"
    except Exception as e:
        return False, f"Lỗi đồng bộ: {e}"

def admin_sync_gsheet_to_excel(df_gsheet, df_excel_goc):
    df_gsheet['Merge_Key'] = df_gsheet['Ngày'].astype(str) + "_" + df_gsheet['Tên nhân viên'].apply(normalize_name) + "_" + df_gsheet.get('Lý do nghỉ', df_gsheet.get('Loại nghỉ', '')).astype(str)
    df_excel_goc['Merge_Key'] = df_excel_goc.iloc[:, 0].astype(str) + "_" + df_excel_goc.iloc[:, 1].apply(normalize_name) + "_" + df_excel_goc.iloc[:, 2].astype(str)
    
    new_rows = df_gsheet[~df_gsheet['Merge_Key'].isin(df_excel_goc['Merge_Key'])].copy()
    if new_rows.empty: return df_excel_goc, False
        
    new_rows = new_rows.drop(columns=['Merge_Key'], errors='ignore')
    df_excel_merged = pd.concat([df_excel_goc.drop(columns=['Merge_Key'], errors='ignore'), new_rows], ignore_index=True)
    return df_excel_merged, True

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
                    # Bổ sung các cột rỗng nếu dòng bị cắt ngắn (do ô trống ở cuối dòng)
                    while len(row) < 15: 
                        row.append("")
                        
                    ten = str(row[1]).strip()
                    if ten:
                        data_list.append({
                            'STT': row[0] if row[0].strip() else idx - 1,
                            'Tên nhân viên': ten,
                            'Mật khẩu': row[2].strip() if row[2].strip() else "123456",
                            'Phân quyền': row[3].strip().lower() if row[3].strip() else "nhanvien",
                            'Họ và tên đầy đủ': row[4].strip(),
                            'Ngày sinh': row[5].strip(),
                            'Điện thoại': row[6].strip(),
                            'Email': row[7].strip(),
                            'Địa chỉ': row[8].strip(),
                            'Phát sinh tháng': row[9].strip() if row[9].strip() else "0",
                            'Có phép tháng': row[10].strip() if row[10].strip() else "0",
                            'Phép năm': row[11].strip() if row[11].strip() else "0",
                            'Ca làm việc': row[12].strip(),
                            'Ngày bắt đầu ca': row[13].strip(),
                            'Chu kỳ': row[14].strip()
                        })
                return pd.DataFrame(data_list)
    except Exception: pass
    return pd.DataFrame(columns=['STT', 'Tên nhân viên', 'Mật khẩu', 'Phân quyền', 'Họ và tên đầy đủ', 'Ngày sinh', 'Điện thoại', 'Email', 'Địa chỉ', 'Phát sinh tháng', 'Có phép tháng', 'Phép năm', 'Ca làm việc', 'Ngày bắt đầu ca', 'Chu kỳ'])

def update_user_profile(username, new_pass, fullname, dob, phone, email, address):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        cells = sheet.findall(username, in_column=2)
        if cells:
            row_idx = cells[0].row
            if new_pass: sheet.update_cell(row_idx, 3, str(new_pass))
            sheet.update_cell(row_idx, 5, str(fullname))
            sheet.update_cell(row_idx, 6, str(dob))
            sheet.update_cell(row_idx, 7, f"'{phone}") 
            sheet.update_cell(row_idx, 8, str(email))
            sheet.update_cell(row_idx, 9, str(address))
            st.cache_data.clear() 
            return True, "Cập nhật hồ sơ thành công!"
        return False, "Không tìm thấy tài khoản."
    except Exception as e: return False, f"Lỗi cập nhật: {e}"

def batch_update_shift_schedule(edited_df):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
        all_vals = sheet.get_all_values()
        
        shift_map = {}
        for _, r in edited_df.iterrows():
            nv_name = str(r['Tên nhân viên']).strip().lower()
            shift_map[nv_name] = {
                'ca': str(r.get('Ca làm việc', '')).replace("nan", "").strip(),
                'ngay': str(r.get('Ngày bắt đầu ca', '')).replace("nan", "").strip(),
                'chuky': str(r.get('Chu kỳ', '')).replace("nan", "").strip()
            }
        
        for i, row in enumerate(all_vals):
            if i == 0: continue 
            if len(row) > 1:
                nv_name = str(row[1]).strip().lower()
                if nv_name in shift_map:
                    while len(row) < 15: row.append("") 
                    row[12] = shift_map[nv_name]['ca']
                    row[13] = shift_map[nv_name]['ngay']
                    row[14] = shift_map[nv_name]['chuky']
                    all_vals[i] = row
        
        try: sheet.update('A1', all_vals)
        except: sheet.update(all_vals) 
        st.cache_data.clear()
        return True, "Đã lưu đồng loạt cấu hình Ca làm việc thành công!"
    except Exception as e: return False, f"Lỗi cập nhật: {e}"

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
                df_bk = df_bk.loc[:, df_bk.columns.astype(str).str.strip() != '']
                df_bk = df_bk.loc[:, ~df_bk.columns.duplicated(keep='first')]
                return df_bk
    except Exception: pass
    return pd.DataFrame(columns=["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"])

@st.cache_data(ttl=60)
def load_loai_nghi_from_gsheet():
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
            rows = sheet.get_all_values()
            if len(rows) > 1: return pd.DataFrame(rows[1:], columns=rows[0])
    except Exception: pass
    return pd.DataFrame()

def save_lich_nghi_to_backup_sheet(ngay, nv, loai_nghi, chi_tiet, so_ngay, so_ngay_cong_don, phat_vi_pham, role):
    try:
        client = get_gspread_client()
        ngay_cn = get_vn_today().strftime('%d/%m/%Y')
        gio_cn = datetime.now(VN_TZ).strftime('%H:%M:%S')
        
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        if len(sheet_dp.get_all_values()) == 0:
            sheet_dp.append_row(["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"])
        
        row_insert = [
            str(ngay), str(nv), str(loai_nghi).replace("🔴 ", ""), str(chi_tiet),
            float(so_ngay) if so_ngay is not None else 0.0, 
            float(so_ngay_cong_don), float(phat_vi_pham), 
            str(ngay_cn), str(gio_cn), str(role)
        ]
        sheet_dp.append_row(row_insert)

        try:
            sheet_chinh_lich = client.open_by_key(SHEET_CHINH_ID).worksheet("LichNghi")
            sheet_chinh_lich.append_row(row_insert)
        except Exception: pass

        st.cache_data.clear()
        return True, "Đã ghi nhận lịch nghỉ thành công!"
    except Exception as e: return False, f"Lỗi ghi dữ liệu: {e}"

def delete_backup_row_exact(ngay, ten, lydo):
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        all_rows = sheet.get_all_values()
        
        col_ngay = all_rows[0].index("Ngày")
        col_ten = all_rows[0].index("Tên nhân viên")
        col_lydo = all_rows[0].index("Lý do nghỉ") if "Lý do nghỉ" in all_rows[0] else all_rows[0].index("Loại nghỉ")
        
        # Xóa ngược từ dưới lên để không làm hỏng index
        deleted = False
        for i in range(len(all_rows)-1, 0, -1):
            r = all_rows[i]
            if str(r[col_ngay]) == str(ngay) and str(r[col_ten]) == str(ten) and str(r[col_lydo]) == str(lydo).replace("🔴 ", ""):
                sheet.delete_rows(i + 1)
                deleted = True
                break # Xóa 1 dòng duy nhất tương ứng
        
        if deleted:
            st.cache_data.clear()
            return True, "Đã xóa lịch nghỉ thành công!"
        return False, "Không tìm thấy dữ liệu để xóa trên Sheet."
    except Exception as e:
        return False, f"Lỗi xóa dòng: {e}"

def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    token = next((v for k, v in response.cookies.items() if k.startswith('download_warning')), None)
    if token:
        response = session.get(URL, params={'id': id, 'confirm': token}, stream=True)
    with open(destination, "wb") as f:
        for chunk in response.iter_content(32768):
            if chunk: f.write(chunk)

@st.cache_data(ttl=60)
def load_lich_nghi(url):
    try:
        file_id = url.split('/d/')[1].split('/')[0]
        temp_file = "temp_lichnghi.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name=['LichNghi', 'DanhSachNV', 'LoaiNghi'], engine='pyxlsb')
        df_lich = xls['LichNghi'].iloc[:, :10]
        df_lich.columns = ['Ngày', 'Tên nhân viên', 'Lý do nghỉ', 'Chi tiết', 'Số ngày tính', 'Số ngày phép cộng dồn', 'Phạt vi phạm', 'Ngày cập nhật', 'Giờ cập nhật', 'Người cập nhật']
        if os.path.exists(temp_file): os.remove(temp_file)
            
        def safe_date_parse(val):
            try:
                if pd.isna(val): return pd.NaT
                if hasattr(val, 'date'): return val.date() 
                if isinstance(val, (int, float)): return pd.to_datetime(val, unit='D', origin='1899-12-30').date()
                s = str(val).strip().split(' ')[0]
                return pd.to_datetime(s, dayfirst=True).date()
            except: return pd.NaT
                
        df_lich['Ngày'] = df_lich['Ngày'].apply(safe_date_parse)
        df_lich = df_lich.dropna(subset=['Ngày'])
        df_lich['Số ngày tính'] = pd.to_numeric(df_lich['Số ngày tính'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        df_lich['Phạt vi phạm'] = pd.to_numeric(df_lich['Phạt vi phạm'].astype(str).str.replace(',', '').str.replace('-', '').str.strip(), errors='coerce').fillna(0)
        
        def format_excel_date(val):
            if pd.isna(val) or str(val).strip() == "": return ""
            try:
                if isinstance(val, (int, float)): return pd.to_datetime(val, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
                if hasattr(val, 'strftime'): return val.strftime('%d/%m/%Y')
                return str(val).split(' ')[0]
            except: return str(val)

        def format_excel_time(val):
            if pd.isna(val) or str(val).strip() == "": return ""
            try:
                if isinstance(val, (int, float)):
                    s = int(round(val * 86400))
                    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
                if hasattr(val, 'strftime'): return val.strftime('%H:%M:%S')
                return str(val)
            except: return str(val)

        if 'Ngày cập nhật' in df_lich.columns: df_lich['Ngày cập nhật'] = df_lich['Ngày cập nhật'].apply(format_excel_date)
        if 'Giờ cập nhật' in df_lich.columns: df_lich['Giờ cập nhật'] = df_lich['Giờ cập nhật'].apply(format_excel_time)
            
        df_nv_excel = xls['DanhSachNV'].dropna(subset=['Tên nhân viên'])
        return df_lich, df_nv_excel, xls['LoaiNghi']
    except Exception as e:
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

df_loai_nghi = df_loai_nghi_gsheet if not df_loai_nghi_gsheet.empty else df_loai_nghi_excel

if df_lich.empty or df_nv_excel.empty:
    st.warning("Hệ thống chưa tìm thấy dữ liệu.")
    st.stop()

# --- XỬ LÝ NÚT NHỚ MẬT KHẨU BẰNG COOKIES (JAVASCRIPT) ---
cookies = st.query_params.get("saved_usr", "")
if cookies and "username_input" not in st.session_state:
    st.session_state["username_input"] = cookies
    
# --- ĐĂNG NHẬP ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = ""
    st.session_state.current_role = ""

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    with st.form("login_form"):
        username_input = st.text_input("Tên đăng nhập", value=st.session_state.get("username_input", ""))
        password_input = st.text_input("Mật khẩu", type="password")
        remember_me = st.checkbox("Ghi nhớ tên đăng nhập")
        
        if st.form_submit_button("Đăng Nhập"):
            usr_norm = remove_accents(username_input)
            pwd_norm = password_input.strip()
            
            # Xử lý nhớ mật khẩu (lưu vào URL params cho tiện lợi, chỉ lưu User)
            if remember_me:
                st.query_params["saved_usr"] = username_input
            else:
                if "saved_usr" in st.query_params:
                    del st.query_params["saved_usr"]
                    
            if usr_norm == "admin" and pwd_norm == "32531235":
                st.session_state.logged_in = True
                st.session_state.current_user = "Quản Trị Viên"
                st.session_state.current_role = "admin"
                st.rerun()
            else:
                user_found = False
                for _, row in df_credentials.iterrows():
                    db_name = remove_accents(str(row['Tên nhân viên']))
                    db_pwd = str(row['Mật khẩu']).strip()
                    
                    if usr_norm == db_name and pwd_norm == db_pwd:
                        role = str(row.get('Phân quyền', 'nhanvien')).strip().lower()
                        
                        # CHẶN ĐĂNG NHẬP NẾU BỊ KHÓA
                        if role in ['nhanvien', 'letan'] and system_status.get("lock_login", False):
                            st.error("❌ Đăng nhập đang bị khóa tạm thời bởi Admin!")
                            user_found = True
                            break
                            
                        st.session_state.logged_in = True
                        st.session_state.current_user = str(row['Tên nhân viên']).strip()
                        st.session_state.current_role = role
                        user_found = True
                        break
                        
                if user_found and st.session_state.logged_in: st.rerun()
                elif not user_found: st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop()


# ==========================================
# ẨN HOÀN TOÀN SIDEBAR NẾU LÀ NHÂN VIÊN
# ==========================================
if st.session_state.current_role == "nhanvien":
    st.markdown("""
        <style>
            [data-testid="collapsedControl"] { display: none !important; }
            [data-testid="stSidebar"] { display: none !important; }
        </style>
    """, unsafe_allow_html=True)


# ==========================================
# THIẾT LẬP MENU ĐIỀU HƯỚNG BÊN TRÁI (SIDEBAR)
# ==========================================
is_admin_letan = st.session_state.current_role in ["admin", "letan"]
menu_options = ["📊 Tình Hình Nghỉ Phép", "⏰ Thiết Lập Ca Làm Việc", "👥 Quản Lý Nhân Sự"]

if is_admin_letan:
    st.sidebar.title("📌 MENU CHỨC NĂNG")
    
    # Sync selected_page with sidebar
    selected_page = st.sidebar.radio("Chọn trang:", menu_options, index=menu_options.index(st.session_state.selected_page_nav))
    st.session_state.selected_page_nav = selected_page
    
    if st.session_state.current_role == "admin":
        st.sidebar.markdown("---")
        st.sidebar.subheader("🔒 ĐIỀU KHIỂN HỆ THỐNG")
        
        # Khóa toàn bộ Đăng nhập
        system_status["lock_login"] = st.sidebar.checkbox("Khóa Đăng Nhập (Lễ Tân & Nhân Viên)", value=system_status.get("lock_login", False))
        
        if system_status["lock_nv"]:
            st.sidebar.warning("🔴 Đang KHÓA quyền Đăng ký (Nhân Viên)")
            if st.sidebar.button("🔓 Mở lại Quyền Đăng Ký", use_container_width=True):
                system_status["lock_nv"] = False
                st.rerun()
        else:
            st.sidebar.success("🟢 Đang MỞ quyền Đăng ký (Nhân Viên)")
            if st.sidebar.button("🔒 Khóa Quyền Đăng Ký Tạm Thời", use_container_width=True):
                system_status["lock_nv"] = True
                st.rerun()
                
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 CÔNG CỤ ĐỒNG BỘ")
        if st.sidebar.button("🔄 Đồng Bộ Excel ➡️ Google Sheets"):
            with st.spinner("Đang kiểm tra và đồng bộ..."):
                res, msg = admin_sync_excel_to_gsheet()
                if res: st.sidebar.success(msg)
                else: st.sidebar.error(msg)
                
        if st.sidebar.button("⬇️ Xuất Excel Mới (GSheet ➡️ Excel)"):
            with st.spinner("Đang tạo file..."):
                df_merged, has_new = admin_sync_gsheet_to_excel(df_backup, df_lich)
                if has_new:
                    st.sidebar.download_button("📥 Bấm tải file Excel cập nhật", data=to_excel(df_merged), file_name="LichNghi_CapNhat.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else:
                    st.sidebar.info("Excel gốc đã cập nhật đủ dữ liệu, không có dòng mới.")
else:
    selected_page = st.session_state.selected_page_nav

# --- GIAO DIỆN HEADER CHÍNH BÊN PHẢI (KÈM NÚT ĐIỀU HƯỚNG) ---
st.write("")
col_nav1, col_nav2, col_nav3, col_title, col_logout = st.columns([0.5, 0.5, 0.5, 6, 2.5]) 
curr_idx = menu_options.index(selected_page) if selected_page in menu_options else 0

with col_nav1:
    st.markdown("<div class='nav-btn'>", unsafe_allow_html=True)
    if st.button("🏠"): 
        st.session_state.selected_page_nav = menu_options[0]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
with col_nav2:
    st.markdown("<div class='nav-btn'>", unsafe_allow_html=True)
    if st.button("⬅️"): 
        st.session_state.selected_page_nav = menu_options[max(0, curr_idx-1)]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
with col_nav3:
    st.markdown("<div class='nav-btn'>", unsafe_allow_html=True)
    if st.button("➡️"): 
        st.session_state.selected_page_nav = menu_options[min(len(menu_options)-1, curr_idx+1)]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

with col_title:
    admin_view_online = ""
    if st.session_state.current_role == "admin" and online_users_list:
        admin_view_online = f"<br><span style='font-size: 13px; font-weight: normal; color: #666;'>👤 Chi tiết: {', '.join(online_users_list)}</span>"
        
    st.markdown(f"""
        <div class='custom-main-title'>
            WELCOME TO VERA SPA
            <div style="float: right; text-align: right; margin-top: 8px;">
                <span style="font-size: 16px; font-family: Arial; font-weight: normal; color: #28a745;">
                    🟢 Đang trực tuyến: {online_users_count}
                </span>
                {admin_view_online}
            </div>
        </div>
    """, unsafe_allow_html=True)

with col_logout:
    c_btn1, c_btn2 = st.columns(2)
    with c_btn1:
        btn_manage_account = st.button("🛠 Hồ sơ Cá Nhân", use_container_width=True)
    with c_btn2:
        if st.button("🚪 Đăng xuất", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()

# --- MODAL HỒ SƠ CÁ NHÂN ---
if 'show_modal' not in st.session_state:
    st.session_state.show_modal = False
if btn_manage_account:
    st.session_state.show_modal = not st.session_state.show_modal

if st.session_state.show_modal:
    st.subheader(f"Cập nhật hồ sơ cá nhân: {st.session_state.current_user}")
    cred_row = df_credentials[df_credentials['Tên nhân viên'].str.lower() == st.session_state.current_user.lower()]
    
    curr_fullname = str(cred_row.iloc[0].get('Họ và tên đầy đủ', '')).strip() if not cred_row.empty else ""
    curr_dob = str(cred_row.iloc[0].get('Ngày sinh', '')).strip() if not cred_row.empty else ""
    curr_phone = str(cred_row.iloc[0].get('Điện thoại', '')).strip().replace("'", "") if not cred_row.empty else ""
    curr_email = str(cred_row.iloc[0].get('Email', '')).strip() if not cred_row.empty else ""
    curr_address = str(cred_row.iloc[0].get('Địa chỉ', '')).strip() if not cred_row.empty else ""
    
    with st.form("change_pass_form"):
        old_pass = st.text_input("Mật khẩu hiện tại (🔴 **Bắt buộc** để lưu)", type="password")
        new_pass = st.text_input("Mật khẩu mới (Bỏ trống nếu không đổi)", type="password")
        in_fullname = st.text_input("Họ và tên đầy đủ", value=curr_fullname)
        in_dob = st.text_input("Ngày sinh (Ví dụ: 15/08/1990)", value=curr_dob)
        in_phone = st.text_input("Số điện thoại", value=curr_phone)
        in_email = st.text_input("Email", value=curr_email)
        in_address = st.text_input("Địa chỉ", value=curr_address)
        
        if st.form_submit_button("Lưu Thay Đổi"):
            db_old_pass = str(cred_row.iloc[0]['Mật khẩu']).strip() if not cred_row.empty else "123456"
            if old_pass != db_old_pass:
                st.error("❌ Mật khẩu hiện tại không chính xác!")
            elif new_pass and len(new_pass.strip()) < 4:
                st.error("❌ Mật khẩu mới quá ngắn.")
            else:
                success, msg = update_user_profile(
                    st.session_state.current_user, 
                    new_pass.strip(), 
                    in_fullname.strip(), 
                    in_dob.strip(), 
                    in_phone.strip(), 
                    in_email.strip(), 
                    in_address.strip()
                )
                if success:
                    st.success(f"✅ {msg}")
                    st.session_state.show_modal = False
                else: st.error(f"❌ {msg}")


# ==========================================
# PAGE 1: ⏰ THIẾT LẬP CA LÀM VIỆC (CHỈ ADMIN/LỄ TÂN)
# ==========================================
if selected_page == "⏰ Thiết Lập Ca Làm Việc" and is_admin_letan:
    st.subheader("Cấu Hình Phân Ca Nhân Viên")
    st.info("Chỉnh sửa trực tiếp trên bảng dưới đây để phân ca đồng loạt cho toàn bộ nhân viên.")
    
    df_shifts = df_credentials[['Tên nhân viên', 'Ca làm việc', 'Ngày bắt đầu ca', 'Chu kỳ']].copy()
    calc_height = (len(df_shifts) * 36) + 42 
    
    edited_df = st.data_editor(
        df_shifts,
        height=calc_height,
        column_config={
            "Tên nhân viên": st.column_config.TextColumn("Tên nhân viên", disabled=True),
            "Ca làm việc": st.column_config.SelectboxColumn(
                "Ca làm việc",
                options=["Ca 1 (10:00 - 23:00)", "Ca 2 (13:00 - 00:00)", "Cố định Ca 1 (Không đổi)", "Cố định Ca 2 (Không đổi)"],
                width="large"
            ),
            "Ngày bắt đầu ca": st.column_config.TextColumn("Ngày bắt đầu (DD/MM/YYYY)"),
            "Chu kỳ": st.column_config.SelectboxColumn(
                "Chu kỳ luân phiên",
                options=["Luân phiên (14 ngày)", "Theo chu kỳ Tháng", "Cố định (Không đổi)"],
                width="medium"
            )
        },
        hide_index=True,
        use_container_width=True
    )
    
    st.write("")
    if st.button("💾 Lưu Toàn Bộ Cấu Hình Ca", use_container_width=True):
        with st.spinner("Đang lưu đồng loạt vào hệ thống..."):
            res, msg = batch_update_shift_schedule(edited_df)
            if res: st.success(msg)
            else: st.error(msg)


# ==========================================
# PAGE 2: 👥 QUẢN LÝ NHÂN SỰ
# ==========================================
elif selected_page == "👥 Quản Lý Nhân Sự" and is_admin_letan:
    st.subheader("Quản Lý Hồ Sơ Nhân Viên")
    
    tab_add, tab_edit_delete = st.tabs(["➕ Thêm Nhân Viên Mới", "✏️ Chỉnh sửa / 🗑️ Xóa"])
    
    with tab_add:
        with st.form("form_add_emp"):
            st.write("Nhập thông tin nhân viên mới:")
            col1, col2 = st.columns(2)
            with col1:
                new_usr = st.text_input("Tên đăng nhập (Bắt buộc)")
                new_pwd = st.text_input("Mật khẩu", value="123456")
                new_role = st.selectbox("Phân quyền", ["nhanvien", "letan", "admin"])
            with col2:
                new_fn = st.text_input("Họ và tên đầy đủ")
                new_phone = st.text_input("Số điện thoại")
            
            if st.form_submit_button("Lưu Nhân Viên Mới"):
                if new_usr:
                    try:
                        client = get_gspread_client()
                        sheet_mk = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                        all_emps = sheet_mk.col_values(2)
                        if new_usr in all_emps:
                            st.error("Tên đăng nhập đã tồn tại!")
                        else:
                            stt_new = len(all_emps)
                            row_data = [stt_new, new_usr, new_pwd, new_role, new_fn, "", new_phone, "", "", "0", "0", "0", "", "", ""]
                            sheet_mk.append_row(row_data)
                            st.cache_data.clear()
                            st.success(f"Đã thêm thành công: {new_usr}")
                    except Exception as e: st.error(f"Lỗi: {e}")
                else: st.error("Vui lòng nhập Tên đăng nhập.")

    with tab_edit_delete:
        col_action1, col_action2 = st.columns(2)
        with col_action1:
            st.markdown("#### 🗑️ Xóa nhân viên")
            del_usr = st.selectbox("Chọn nhân viên cần xóa:", [""] + df_credentials['Tên nhân viên'].tolist())
            if st.button("Xác Nhận Xóa"):
                if del_usr:
                    try:
                        client = get_gspread_client()
                        sheet_mk = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                        cell = sheet_mk.find(del_usr, in_column=2)
                        sheet_mk.delete_rows(cell.row)
                        st.cache_data.clear()
                        st.success(f"Đã xóa nhân viên: {del_usr}")
                    except Exception as e: st.error(f"Lỗi xóa: {e}")
                
        with col_action2:
            st.markdown("#### ✏️ Chỉnh sửa hồ sơ (Chỉ Admin)")
            if st.session_state.current_role == "admin":
                edit_usr = st.selectbox("Chọn nhân viên cần sửa:", [""] + df_credentials['Tên nhân viên'].tolist(), key='sb_edit')
                if edit_usr:
                    usr_data = df_credentials[df_credentials['Tên nhân viên'] == edit_usr].iloc[0]
                    with st.form("form_edit_emp_admin"):
                        e_pass = st.text_input("Mật khẩu", value=usr_data['Mật khẩu'])
                        e_fn = st.text_input("Họ Tên", value=usr_data['Họ và tên đầy đủ'])
                        e_phone = st.text_input("SĐT", value=usr_data['Điện thoại'])
                        if st.form_submit_button("Cập nhật dữ liệu"):
                            update_user_profile(edit_usr, e_pass, e_fn, usr_data['Ngày sinh'], e_phone, usr_data['Email'], usr_data['Địa chỉ'])
                            st.success("Đã cập nhật!")
            else:
                st.info("Chỉ tài khoản Admin mới được phép chỉnh sửa chi tiết hồ sơ người khác.")


# ==========================================
# PAGE 3: 📊 TÌNH HÌNH NGHỈ PHÉP
# ==========================================
elif selected_page == "📊 Tình Hình Nghỉ Phép":

    with st.expander("📝 ĐĂNG KÝ - THAY ĐỔI LỊCH NGHỈ", expanded=False):
        tabs = st.tabs(["➕ Nhập lịch nghỉ mới", "✏️ Quản lý / Xóa lịch đã đăng ký"])
        tab_input_lich, tab_manage_lich = tabs[0], tabs[1]
            
        users_s = df_credentials['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_credentials.empty else []
        users_e = df_nv_excel['Tên nhân viên'].dropna().astype(str).str.strip().tolist() if not df_nv_excel.empty else []
        all_users = sorted(list(set(users_s + users_e)))
        
        # Keyword cho loại nghỉ đặc biệt
        special_leave_kws = ['bệnh có giấy khám', 'được quản lý duyệt', 'đám hiếu', 'leader nghỉ phép', 'leader về sớm', 'leader đi trễ']
        
        with tab_input_lich:
            if st.session_state.current_role == "nhanvien" and system_status["lock_nv"]:
                st.error("🔒 Tính năng đăng ký lịch nghỉ hiện đang bị Admin tạm khóa. Vui lòng liên hệ Admin hoặc Lễ Tân để được hỗ trợ!")
            else:
                col_i1, col_i2 = st.columns(2)
                with col_i1:
                    if is_admin_letan:
                        chosen_nvs = st.multiselect("Chọn nhân viên (Có thể chọn nhiều người):", all_users, key="sb_chosen_nv_multi")
                    else:
                        chosen_nvs = [st.session_state.current_user]
                        st.multiselect("Nhân viên:", chosen_nvs, default=chosen_nvs, disabled=True)
                with col_i2:
                    if is_admin_letan:
                        chosen_dates = st.date_input("Chọn ngày nghỉ (Khoảng thời gian nếu là Phép năm):", value=(get_vn_today(), get_vn_today()), key="sb_chosen_date")
                    else:
                        chosen_dates = st.date_input("Chọn ngày nghỉ:", get_vn_today(), key="sb_chosen_date")
                
                if isinstance(chosen_dates, tuple):
                    if len(chosen_dates) == 2: start_date, end_date = chosen_dates
                    elif len(chosen_dates) == 1: start_date = end_date = chosen_dates[0]
                    else: start_date = end_date = get_vn_today()
                else:
                    start_date = end_date = chosen_dates
                
                # --- BỘ LỌC ĐỘNG CHO LÝ DO NGHỈ ---
                list_loai_nghi = []
                loai_nghi_dict = {}
                current_role = st.session_state.current_role.lower()

                if not df_loai_nghi.empty:
                    for idx, row in df_loai_nghi.iterrows():
                        row_vals = row.tolist()
                        l_name = str(row_vals[1]).strip() if len(row_vals) > 1 else ""
                        if not l_name or l_name.lower() in ["nan", "none"]:
                            l_name = str(row.get('Lý do nghỉ', row.get('Loại nghỉ', ''))).strip()
                        
                        if l_name and l_name.lower() not in ["nan", "loại nghỉ", "lý do nghỉ", "none", ""]:
                            dk_ngay = str(row_vals[6]).strip().lower() if len(row_vals) > 6 else ""
                            dk_role = str(row_vals[7]).strip().lower() if len(row_vals) > 7 else ""
                            
                            role_allowed = True
                            if dk_role and dk_role not in ["nan", "none", "tất cả", "all", ""]:
                                if current_role not in dk_role: role_allowed = False
                                    
                            day_allowed = True
                            if dk_ngay and dk_ngay not in ["nan", "none", "tất cả", "all", ""]:
                                wd = start_date.weekday()
                                wd_map = {
                                    0: ["hai", "t2"], 1: ["ba", "t3"], 2: ["tư", "tu", "t4"],
                                    3: ["năm", "nam", "t5"], 4: ["sáu", "sau", "t6"],
                                    5: ["bảy", "bẩy", "t7", "cuối tuần"], 6: ["chủ nhật", "chu nhat", "cn", "cuối tuần"]
                                }
                                day_allowed = any(k in dk_ngay for k in wd_map[wd])

                            if day_allowed and role_allowed:
                                if "không phép" in l_name.lower(): l_name = f"🔴 {l_name}"
                                list_loai_nghi.append(l_name)
                                try:
                                    s_ngay_str = str(row_vals[4]).replace(',', '').strip() if len(row_vals) > 4 else ""
                                    s_ngay = float(s_ngay_str) if s_ngay_str != "" else 0.0
                                except: s_ngay = 0.0
                                
                                try:
                                    p_str = str(row_vals[5] if len(row_vals)>5 else "0").replace('.', '').replace(',', '').replace(' ', '').replace('đ', '').replace('VNĐ', '').replace('VND', '')
                                    p_val = 0.0 if p_str.lower() in ["", "-", "nan", "none"] else float(p_str)
                                except: p_val = 0.0
                                    
                                loai_nghi_dict[l_name.lower()] = [s_ngay, p_val]
                                
                if not list_loai_nghi:
                    list_loai_nghi = ["Nghỉ phép", "🔴 Nghỉ không phép", "Nghỉ phát sinh", "🔴 Đi trễ không phép"]
                    loai_nghi_dict = {l.lower(): [0.0, 0.0] for l in list_loai_nghi}

                chosen_loai = st.selectbox("Lý do nghỉ:", ["-- Chọn lý do nghỉ --"] + list_loai_nghi, key="sb_loai_nghi_live")
                
                default_songay = 0.0
                default_phat = 0.0
                if chosen_loai and chosen_loai != "-- Chọn lý do nghỉ --" and chosen_loai.lower() in loai_nghi_dict:
                    default_songay = loai_nghi_dict[chosen_loai.lower()][0]
                    default_phat = loai_nghi_dict[chosen_loai.lower()][1]

                is_loi_vi_pham = "lỗi vi phạm khác" in chosen_loai.lower() if chosen_loai else False
                is_nghi_ly_do_khac = "nghỉ lý do khác" in chosen_loai.lower() if chosen_loai else False
                if is_loi_vi_pham: default_songay = 0.0
                
                norm_loai_temp = chosen_loai.strip().lower() if chosen_loai else ""
                is_special_leave = any(kw in norm_loai_temp for kw in special_leave_kws)

                # --- CẢNH BÁO SỚM SỐ NGƯỜI NGHỈ ---
                early_warning = ""
                if chosen_loai and chosen_loai != "-- Chọn lý do nghỉ --":
                    num_days_temp = (end_date - start_date).days + 1
                    if num_days_temp > 1 and "phép năm" not in norm_loai_temp:
                        early_warning = "❌ Chọn Khoảng thời gian nhiều ngày chỉ áp dụng cho 'Nghỉ Phép năm'."
                    elif not is_nghi_ly_do_khac and default_phat <= 0 and "phép năm" not in norm_loai_temp and not is_loi_vi_pham and not is_special_leave:
                        for i in range(num_days_temp):
                            chk_d = start_date + timedelta(days=i)
                            chk_is_we = chk_d.weekday() >= 5
                            
                            # Tính tổng số người đang nghỉ không chứa từ khóa đặc biệt
                            if not df_lich.empty:
                                non_special_condition = ~df_lich['Lý do nghỉ'].str.lower().str.contains('|'.join(special_leave_kws), na=False)
                                
                                if norm_loai_temp == "nghỉ phát sinh":
                                    c_ps = len(df_lich[(df_lich['Ngày'] == chk_d) & (df_lich['Lý do nghỉ'].astype(str).str.strip().str.lower() == "nghỉ phát sinh")])
                                    if c_ps >= 2:
                                        early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} đã đạt giới hạn 2 người 'Nghỉ phát sinh'!"
                                        break
                                else:
                                    m_ppl = 5 if not chk_is_we else 3
                                    c_nghi = len(df_lich[(df_lich['Ngày'] == chk_d) & (df_lich['Số ngày tính'] > 0) & non_special_condition])
                                    if c_nghi >= m_ppl:
                                        early_warning = f"❌ Ngày {chk_d.strftime('%d/%m/%Y')} đã đạt giới hạn {m_ppl} người nghỉ chung/ngày."
                                        break

                if early_warning: st.error(early_warning)

                dyn_key_suffix = f"{chosen_loai}_{start_date}"

                with st.form("form_nhap_lich_inner"):
                    txt_chitiet_label = "Chi tiết vi phạm / Ghi chú (🔴 **Bắt buộc**):" if (is_loi_vi_pham or is_nghi_ly_do_khac) else "Chi tiết vi phạm / Ghi chú (nếu có):"
                    input_chitiet = st.text_input(txt_chitiet_label).strip()
                    
                    col_p1, col_p2 = st.columns(2)
                    with col_p1:
                        val_songay = st.number_input("Số ngày tính:", value=float(default_songay), step=0.5, key=f"num_songay_{dyn_key_suffix}", disabled=is_loi_vi_pham)
                    
                    with col_p2:
                        txt_phat_label = "Mức phạt vi phạm VNĐ (🔴 **Bắt buộc**):" if is_loi_vi_pham else "Mức phạt vi phạm (VNĐ):"
                        val_phat = st.number_input(txt_phat_label, value=float(default_phat), step=50000.0, key=f"num_phat_{dyn_key_suffix}")
                    
                    confirm_multiple = st.checkbox("Tôi xác nhận đăng ký này là đúng (Dùng khi báo cảnh báo trùng lặp)")

                    submit_lich = st.form_submit_button("💾 Xác Nhận Ghi Lịch Nghỉ")
                    
                    if submit_lich:
                        today = get_vn_today()
                        # Xác định giới hạn ngày của nhân viên
                        next_month = today.month + 1 if today.month < 12 else 1
                        next_month_year = today.year if today.month < 12 else today.year + 1
                        last_day_nm = calendar.monthrange(next_month_year, next_month)[1]
                        max_nv_date = date(next_month_year, next_month, last_day_nm)
                        
                        can_proceed = True
                        
                        if current_role == "nhanvien":
                            if start_date < today:
                                st.error("❌ Lỗi: Không được đăng ký lịch trong quá khứ.")
                                can_proceed = False
                            elif start_date > max_nv_date:
                                st.error("❌ Lỗi: Chỉ được đăng ký lịch trong tháng hiện tại và một tháng kế tiếp.")
                                can_proceed = False
                                
                        elif current_role == "letan" and start_date < today:
                            st.error("❌ Lỗi: Tài khoản LỄ TÂN không được đăng ký lịch trong **QUÁ KHỨ**. Vui lòng liên hệ Admin.")
                            can_proceed = False
                            
                        if can_proceed:
                            if not chosen_nvs:
                                st.error("❌ Vui lòng chọn ít nhất 1 nhân viên!")
                            elif chosen_loai == "-- Chọn lý do nghỉ --" or not chosen_loai:
                                st.error("❌ Vui lòng chọn lý do nghỉ!")
                            elif early_warning:
                                st.error(f"❌ Không thể lưu: {early_warning}")
                            else:
                                norm_loai = chosen_loai.strip().lower().replace("🔴 ", "")
                                num_days_selected = (end_date - start_date).days + 1
                                
                                if is_loi_vi_pham:
                                    val_songay = 0.0 
                                    if not input_chitiet:
                                        st.error("❌ Bắt buộc nhập Chi tiết vi phạm / Ghi chú đối với 'Lỗi vi phạm khác'.")
                                        can_proceed = False
                                    if val_phat <= 0 and st.session_state.current_role == "admin":
                                        st.error("❌ Bắt buộc nhập số tiền Phạt vi phạm > 0 đối với 'Lỗi vi phạm khác'.")
                                        can_proceed = False
                                
                                if is_nghi_ly_do_khac and not input_chitiet:
                                    st.error("❌ Bắt buộc nhập Chi tiết vi phạm / Ghi chú đối với 'Nghỉ lý do khác'.")
                                    can_proceed = False
                                
                                if can_proceed:
                                    total_success = 0
                                    for chosen_nv in chosen_nvs:
                                        nv_info = df_credentials[df_credentials['Tên nhân viên'].str.lower() == chosen_nv.lower()]
                                        limit_ps = pd.to_numeric(nv_info.iloc[0].get('Phát sinh tháng', 0), errors='coerce') if not nv_info.empty else 0
                                        limit_cp = pd.to_numeric(nv_info.iloc[0].get('Có phép tháng', 0), errors='coerce') if not nv_info.empty else 0
                                        limit_pn = pd.to_numeric(nv_info.iloc[0].get('Phép năm', 0), errors='coerce') if not nv_info.empty else 0
                                        if pd.isna(limit_ps): limit_ps = 0
                                        if pd.isna(limit_cp): limit_cp = 0
                                        if pd.isna(limit_pn): limit_pn = 0
                                        
                                        user_hist = df_lich[df_lich['Tên nhân viên'] == chosen_nv] if not df_lich.empty else pd.DataFrame(columns=['Ngày', 'Lý do nghỉ', 'Số ngày tính'])
                                        user_hist['Ngày_DT'] = pd.to_datetime(user_hist['Ngày'], errors='coerce')
                                        user_hist['M'] = user_hist['Ngày_DT'].dt.month
                                        user_hist['Y'] = user_hist['Ngày_DT'].dt.year
                                        
                                        curr_m = start_date.month
                                        curr_y = start_date.year
                                        
                                        total_phep_required = val_songay * num_days_selected
                                        accumulated_month = user_hist[(user_hist['M'] == curr_m) & (user_hist['Y'] == curr_y)]['Số ngày tính'].sum()
                                        
                                        local_can_proceed = True
                                        
                                        if "phép năm" in norm_loai:
                                            used_pn = user_hist[(user_hist['Y'] == curr_y) & (user_hist['Lý do nghỉ'].str.lower().str.contains("phép năm", na=False))]['Số ngày tính'].sum()
                                            if limit_pn > 0 and (used_pn + total_phep_required > limit_pn):
                                                st.error(f"❌ Nhân viên {chosen_nv}: Cần {total_phep_required} ngày nhưng quỹ Phép Năm chỉ còn {limit_pn - used_pn} ngày.")
                                                local_can_proceed = False
                                                
                                        elif "phát sinh" in norm_loai:
                                            used_ps = len(user_hist[(user_hist['M'] == curr_m) & (user_hist['Y'] == curr_y) & (user_hist['Lý do nghỉ'].str.lower().str.contains("phát sinh", na=False))])
                                            if limit_ps > 0 and (used_ps >= limit_ps):
                                                st.error(f"❌ Nhân viên {chosen_nv}: Vượt giới hạn Phát sinh tháng ({limit_ps} lần).")
                                                local_can_proceed = False
                                                
                                        elif not is_nghi_ly_do_khac and "không phép" not in norm_loai and val_songay > 0 and not is_special_leave:
                                            used_cp = user_hist[(user_hist['M'] == curr_m) & (user_hist['Y'] == curr_y) & (~user_hist['Lý do nghỉ'].str.lower().str.contains("không phép|phát sinh|lý do khác", na=False, regex=True))]['Số ngày tính'].sum()
                                            if limit_cp > 0 and (used_cp + total_phep_required > limit_cp):
                                                st.error(f"❌ Nhân viên {chosen_nv}: Vượt số ngày Có phép trong tháng (Tối đa {limit_cp} ngày).")
                                                local_can_proceed = False

                                        if local_can_proceed:
                                            all_saved = True
                                            for i in range(num_days_selected):
                                                curr_date_iter = start_date + timedelta(days=i)
                                                is_weekend_iter = curr_date_iter.weekday() >= 5
                                                
                                                if val_songay is not None: accumulated_month += val_songay
                                                else: val_songay = 0.0
                                                
                                                # Check Limits specifically for this day again
                                                if not is_nghi_ly_do_khac and val_phat <= 0 and "phép năm" not in norm_loai and not is_loi_vi_pham and not is_special_leave:
                                                    if norm_loai == "nghỉ phát sinh":
                                                        current_hour = datetime.now(VN_TZ).hour
                                                        if current_hour < 9 or current_hour >= 17:
                                                            st.error(f"❌ NV {chosen_nv}: Đăng ký 'Phát sinh' chỉ cho phép từ 09:00 đến 17:00!")
                                                            all_saved = False; break
                                                        elif is_weekend_iter:
                                                            st.error(f"❌ NV {chosen_nv}: Ngày {curr_date_iter.strftime('%d/%m/%Y')} là cuối tuần, không được 'Phát sinh'!")
                                                            all_saved = False; break
                                                        
                                                # Check if exact same leave already exists today to prevent duplication unless confirmed
                                                if not df_lich.empty:
                                                    ex_df = df_lich[(df_lich['Tên nhân viên'] == chosen_nv) & (df_lich['Ngày'] == curr_date_iter)]
                                                    existing_td = ex_df['Lý do nghỉ'].astype(str).str.strip().tolist()
                                                    if chosen_loai.replace("🔴 ", "") in existing_td and not confirm_multiple:
                                                        st.error(f"❌ Nhân viên {chosen_nv} bị trùng lịch cùng lý do ngày {curr_date_iter.strftime('%d/%m/%Y')}. Vui lòng check 'Xác nhận' nếu đây là bản ghi mới.")
                                                        all_saved = False; break

                                                if all_saved:
                                                    success_bk, msg_bk = save_lich_nghi_to_backup_sheet(
                                                        curr_date_iter.strftime('%d/%m/%Y'), chosen_nv, chosen_loai.replace("🔴 ", ""), 
                                                        input_chitiet, val_songay, accumulated_month, val_phat, st.session_state.current_role
                                                    )
                                                    if not success_bk:
                                                        st.error(f"❌ LỖI GOOGLE SHEETS (NV {chosen_nv}): {msg_bk}")
                                                        all_saved = False
                                                        break
                                            
                                            if all_saved:
                                                total_success += 1
                                    
                                    if total_success > 0:
                                        st.success(f"✅ Đã ghi nhận lịch nghỉ thành công cho {total_success} nhân viên!")
                                        st.cache_data.clear()

        with tab_manage_lich:
            st.markdown("### 🗑️ Xóa / Quản lý lịch nghỉ đã đăng ký")
            
            df_backup_view = df_backup.copy()
            if st.session_state.current_role == "nhanvien":
                df_backup_view = df_backup_view[df_backup_view['Tên nhân viên'] == st.session_state.current_user]

            if df_backup_view.empty: 
                st.info("Chưa có lịch nghỉ nào được đăng ký.")
            else:
                df_view_display = df_backup_view.copy()
                if st.session_state.current_role != "admin" and "Phạt vi phạm" in df_view_display.columns:
                    df_view_display = df_view_display.drop(columns=["Phạt vi phạm"])
                
                df_view_display = format_display_df(df_view_display)
                st.dataframe(df_view_display, use_container_width=True, hide_index=True)
                
                if st.session_state.current_role == "nhanvien" and system_status["lock_nv"]:
                    st.error("🔒 Tính năng xóa lịch nghỉ hiện đang bị Admin tạm khóa. Vui lòng liên hệ Admin để được hỗ trợ!")
                else:
                    with st.form("form_delete_backup_row"):
                        col_ly_do_disp = 'Lý do nghỉ' if 'Lý do nghỉ' in df_backup_view.columns else 'Loại nghỉ'
                        
                        row_options = []
                        valid_indices = []
                        for i, row in df_backup.iterrows():
                            if st.session_state.current_role == "nhanvien" and row.get('Tên nhân viên') != st.session_state.current_user:
                                continue
                            row_options.append(f"Dòng {i+1}: {row.get('Ngày')} - {row.get('Tên nhân viên')} - {row.get(col_ly_do_disp, '')}")
                            valid_indices.append((i, str(row.get('Ngày'))))
                            
                        selected_row_str = st.selectbox("Chọn dòng lịch nghỉ cần xóa:", row_options)
                        
                        if st.form_submit_button("🗑️ Xóa Lịch Nghỉ Đã Chọn") and selected_row_str:
                            sel_idx = row_options.index(selected_row_str)
                            real_i, sel_date_str = valid_indices[sel_idx]
                            
                            try:
                                sel_date = pd.to_datetime(sel_date_str, format='%d/%m/%Y').date()
                            except:
                                sel_date = get_vn_today()
                            
                            can_delete = True
                            today = get_vn_today()
                            if st.session_state.current_role == "nhanvien" and sel_date <= today:
                                st.error("❌ Lỗi: Tài khoản NHÂN VIÊN chỉ được xóa lịch của **NGÀY MAI** trở đi. Vui lòng liên hệ Lễ tân/Admin.")
                                can_delete = False
                            elif st.session_state.current_role == "letan" and sel_date < today:
                                st.error("❌ Lỗi: Tài khoản LỄ TÂN không được xóa lịch trong **QUÁ KHỨ**. Vui lòng liên hệ Admin.")
                                can_delete = False
                                
                            if can_delete:
                                success_del, msg_del = delete_backup_row(real_i + 2)
                                if success_del:
                                    st.success(f"✅ {msg_del}")
                                    st.cache_data.clear()
                                    st.rerun()
                                else: st.error(f"❌ {msg_del}")

    st.markdown("---")

    # Bộ lọc thời gian & nhân viên
    col_date, col_name, col_refresh = st.columns([5, 4, 2])

    with col_date:
        today = get_vn_today() 
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            filter_type = st.selectbox(
                "Lọc thời gian:", 
                ["Hôm nay", "Hôm qua", "Ngày mai", "Chọn ngày", "Khoảng thời gian", "Tuần này", "Tuần trước", "Tuần sau", "Tháng này", "Tháng sau"]
            )
        with col_d2:
            if filter_type == "Hôm nay": start_date = end_date = today
            elif filter_type == "Hôm qua": start_date = end_date = today - timedelta(days=1)
            elif filter_type == "Ngày mai": start_date = end_date = today + timedelta(days=1)
            elif filter_type == "Tuần này":
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif filter_type == "Tuần trước":
                start_date = today - timedelta(days=today.weekday() + 7)
                end_date = start_date + timedelta(days=6)
            elif filter_type == "Tuần sau":
                start_date = today - timedelta(days=today.weekday()) + timedelta(days=7)
                end_date = start_date + timedelta(days=6)
            elif filter_type == "Tháng này":
                start_date = today.replace(day=1)
                end_date = today.replace(day=calendar.monthrange(today.year, today.month)[1])
            elif filter_type == "Tháng trước":
                end_date = today.replace(day=1) - timedelta(days=1)
                start_date = end_date.replace(day=1)
            elif filter_type == "Tháng sau":
                start_date = today.replace(year=today.year + 1, month=1, day=1) if today.month == 12 else today.replace(month=today.month + 1, day=1)
                end_date = start_date.replace(day=calendar.monthrange(start_date.year, start_date.month)[1])
            elif filter_type == "Chọn ngày":
                start_date = end_date = st.date_input("Chọn ngày:", today)
            elif filter_type == "Khoảng thời gian":
                date_range = st.date_input("Chọn khoảng thời gian:", [today, today])
                start_date, end_date = (date_range[0], date_range[1]) if len(date_range) == 2 else (date_range[0], date_range[0])
            else: start_date = end_date = today

    with col_name:
        list_nv = ["- Tất cả nhân viên -"] + sorted(list(set(df_credentials['Tên nhân viên'].dropna().tolist() + (df_nv_excel['Tên nhân viên'].dropna().tolist() if not df_nv_excel.empty else []))))
        selected_nv = st.selectbox("👤 Tìm kiếm nhân viên:", list_nv)

    with col_refresh:
        st.write("") 
        if st.button("🔄 Cập Nhật Dữ Liệu", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Lọc dữ liệu
    mask_date = (df_lich['Ngày'] >= start_date) & (df_lich['Ngày'] <= end_date)
    filtered_df = df_lich[mask_date].copy()
    if selected_nv != "- Tất cả nhân viên -": filtered_df = filtered_df[filtered_df['Tên nhân viên'].astype(str).str.strip().str.lower() == selected_nv.lower()]

    # --- THỐNG KÊ ---
    excluded_keywords = ["đi trễ", "di tre", "không dọn vệ sinh", "khong don ve sinh", "lỗi vi phạm", "loi vi pham", "qua tour", "xuống phòng", "xuong phong", "ra sớm", "ra som", "vào muộn", "vao muon", "đi tua", "di tua", "ngưng nhận", "ngung nhan", "hỗ trợ ca", "ho tro ca"]
    def is_excluded(r): return any(kw in str(r).lower() for kw in excluded_keywords)
    
    special_leave_kws = ['bệnh có giấy khám', 'được quản lý duyệt', 'đám hiếu', 'leader nghỉ phép', 'leader về sớm', 'leader đi trễ']

    if filtered_df.empty:
        df_thuc_nghi = phat_sinh_df = khong_phep_df = co_phep_df = pd.DataFrame(columns=df_lich.columns)
        tong_phat = 0.0
    else:
        df_thuc_nghi = filtered_df[~filtered_df['Lý do nghỉ'].apply(is_excluded)].copy()
        if df_thuc_nghi.empty: phat_sinh_df = khong_phep_df = co_phep_df = pd.DataFrame(columns=df_lich.columns)
        else:
            ly_do_lower = df_thuc_nghi['Lý do nghỉ'].astype(str).str.strip().str.lower()
            phat_sinh_df = df_thuc_nghi[ly_do_lower == 'nghỉ phát sinh']
            khong_phep_df = df_thuc_nghi[ly_do_lower.str.contains('không phép', na=False)]
            co_phep_df = df_thuc_nghi[(ly_do_lower != 'nghỉ phát sinh') & (~ly_do_lower.str.contains('không phép', na=False))]
        tong_phat = filtered_df['Phạt vi phạm'].sum()

    st.write("") 
    if st.session_state.current_role == "admin":
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tổng số người nghỉ", len(df_thuc_nghi))
        c2.metric("✅ CÓ phép", len(co_phep_df))
        c3.metric("⚠️ PHÁT SINH", len(phat_sinh_df))
        c4.metric("❌ KHÔNG phép", len(khong_phep_df))
        c5.metric("💰 Tổng tiền phạt", f"{tong_phat:,.0f} đ".replace(",", "."))
        cols_to_hide = []
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tổng số người nghỉ", len(df_thuc_nghi))
        c2.metric("✅ CÓ phép", len(co_phep_df))
        c3.metric("⚠️ PHÁT SINH", len(phat_sinh_df))
        c4.metric("❌ KHÔNG phép", len(khong_phep_df))
        cols_to_hide = ['Phạt vi phạm']

    st.markdown("### 📅 Thống kê chi tiết theo từng ngày")
    if not df_thuc_nghi.empty:
        daily_stats = []
        for d in sorted(filtered_df['Ngày'].dropna().unique()):
            day_df = filtered_df[filtered_df['Ngày'] == d]
            day_thuc_nghi = day_df[~day_df['Lý do nghỉ'].apply(is_excluded)]
            d_loai = day_thuc_nghi['Lý do nghỉ'].astype(str).str.strip().str.lower()
            
            c_cophep = len(day_thuc_nghi[(d_loai != 'nghỉ phát sinh') & (~d_loai.str.contains('không phép', na=False))])
            c_phatsinh = len(day_thuc_nghi[d_loai == 'nghỉ phát sinh'])
            
            stat_row = {
                "Ngày": d.strftime('%d/%m/%Y'),
                "Tổng số người nghỉ": len(day_thuc_nghi),
                "✅ CÓ phép": c_cophep,
                "⚠️ PHÁT SINH": c_phatsinh,
                "❌ KHÔNG phép": len(day_thuc_nghi[d_loai.str.contains('không phép', na=False)])
            }
            if st.session_state.current_role == "admin":
                stat_row["💰 Tổng tiền phạt"] = f"{day_df['Phạt vi phạm'].sum():,.0f} đ".replace(",", ".")
                
            daily_stats.append(stat_row)
            
        df_stats = pd.DataFrame(daily_stats)
        
        # TÔ MÀU ĐỎ CHO BẢNG THỐNG KÊ KHI VƯỢT HẠN MỨC
        def style_stats(row):
            bg = ''
            try:
                dt_obj = datetime.strptime(row['Ngày'], '%d/%m/%Y')
                is_we = dt_obj.weekday() >= 5
                limit = 3 if is_we else 5
                
                # Check số phát sinh >= 2
                ps_over = int(row['⚠️ PHÁT SINH']) >= 2
                # Tính lại bằng df_lich gốc cho chính xác
                day_df_raw = df_lich[df_lich['Ngày'] == dt_obj.date()]
                non_special = day_df_raw[~day_df_raw['Lý do nghỉ'].str.lower().str.contains('|'.join(special_leave_kws), na=False)]
                total_nghi_raw = len(non_special[non_special['Số ngày tính'] > 0])
                
                nghi_over = total_nghi_raw >= limit
                
                styles = []
                for col in row.index:
                    if col == '⚠️ PHÁT SINH' and ps_over: styles.append('background-color: #ffcccc; font-weight: bold; color: #d32f2f;')
                    elif col == '✅ CÓ phép' and nghi_over: styles.append('background-color: #ffcccc; font-weight: bold; color: #d32f2f;')
                    else: styles.append('')
                return styles
            except:
                return ['' for _ in row.index]

        st.dataframe(df_stats.style.apply(style_stats, axis=1), use_container_width=True, hide_index=True)
    else: st.info("Không có dữ liệu báo nghỉ trong khoảng thời gian đã chọn.")

    st.markdown("---")

    export_df = format_display_df(filtered_df.drop(columns=cols_to_hide, errors='ignore'))
    df_for_excel = export_df.copy()
    if st.session_state.current_role == "admin" and not df_for_excel.empty:
        tong_cong_row = pd.Series(index=df_for_excel.columns, dtype=object)
        tong_cong_row['Tên nhân viên'] = "TỔNG TIỀN PHẠT:"
        tong_cong_row['Phạt vi phạm'] = tong_phat
        df_for_excel = pd.concat([df_for_excel, tong_cong_row.to_frame().T], ignore_index=True)

    col_header, col_download = st.columns([7, 3])
    with col_header: st.subheader(f"Chi tiết danh sách (Từ {start_date.strftime('%d/%m/%Y')} đến {end_date.strftime('%d/%m/%Y')})")
    
    # --- XÓA / SỬA TRỰC TIẾP TỪ BẢNG CHI TIẾT (CHO ADMIN/LETAN) ---
    if st.session_state.current_role in ["admin", "letan"] and not export_df.empty:
        df_editable = export_df.copy()
        df_editable.insert(0, "Chọn", False)
        
        calc_h = min((len(df_editable) + 1) * 35 + 40, 800)
        edited_df = st.data_editor(
            df_editable,
            hide_index=True,
            use_container_width=True,
            height=calc_h,
            column_config={"Chọn": st.column_config.CheckboxColumn("Chọn để Xóa", default=False)}
        )
        
        selected_rows = edited_df[edited_df["Chọn"] == True]
        if not selected_rows.empty:
            if st.button("🗑️ Xóa các dòng đã chọn", type="primary"):
                with st.spinner("Đang xóa dữ liệu..."):
                    success_count = 0
                    for _, row in selected_rows.iterrows():
                        res, msg = delete_backup_row_exact(row['Ngày'], row['Tên nhân viên'], row['Lý do nghỉ'])
                        if res: success_count += 1
                    
                    if success_count > 0:
                        st.success(f"✅ Đã xóa thành công {success_count} dòng dữ liệu.")
                        st.rerun()
                    else:
                        st.error("❌ Không thể xóa dữ liệu trên Google Sheets (không tìm thấy bản ghi tương ứng).")
    else:
        # Nếu là nhân viên, chỉ hiển thị bảng bình thường
        def highlight_khong_phep(val):
            if isinstance(val, str) and "không phép" in val.lower():
                return 'color: red; font-weight: bold;'
            return ''
        st.dataframe(export_df.style.map(highlight_khong_phep), use_container_width=True, hide_index=True)

    with col_download:
        st.write("") 
        if st.session_state.current_role != "nhanvien":
            if not export_df.empty:
                st.download_button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", data=to_excel(df_for_excel), file_name=f"LichNghi_{start_date.strftime('%d%m%Y')}_to_{end_date.strftime('%d%m%Y')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
            else: st.button("📥 Tải Dữ Liệu Lọc Xuống (Excel)", disabled=True, use_container_width=True)

    # --- GỬI EMAIL BÁO CÁO (CHỈ ADMIN) ---
    if st.session_state.current_role == "admin" and not filtered_df.empty:
        with st.expander("📧 GỬI BÁO CÁO QUA EMAIL CHO NHÂN VIÊN"):
            st.info("Hệ thống sẽ tự động tách dữ liệu của từng nhân viên và gửi đến đúng Email của họ. Bạn có thể chọn gửi cho 1 người, nhiều người hoặc tất cả.")
            
            unique_employees_in_filter = filtered_df['Tên nhân viên'].dropna().unique().tolist()
            
            with st.form("form_send_email"):
                selected_to_send = st.multiselect(
                    "Chọn nhân viên nhận báo cáo:", 
                    options=unique_employees_in_filter, 
                    default=unique_employees_in_filter
                )
                
                sender_email = "veraspabienhoa@gmail.com"
                sender_pass = "zvtgbysfmdaqxaau"
                st.write(f"📧 **Email gửi đi mặc định:** `{sender_email}`")
                
                if st.form_submit_button("🚀 Xác Nhận Gửi Email"):
                    if not selected_to_send:
                        st.warning("⚠️ Vui lòng chọn ít nhất 1 nhân viên để gửi!")
                    else:
                        success_count = 0
                        error_messages = []
                        progress_bar = st.progress(0)
                        
                        for i, emp in enumerate(selected_to_send):
                            df_emp = filtered_df[filtered_df['Tên nhân viên'] == emp]
                            total_phat = df_emp['Phạt vi phạm'].sum()
                            
                            emp_row = df_credentials[df_credentials['Tên nhân viên'].str.lower() == str(emp).strip().lower()]
                            emp_email = str(emp_row.iloc[0].get('Email', '')).strip() if not emp_row.empty else ""
                            
                            if not emp_email or "@" not in emp_email:
                                error_messages.append(f"⚠️ Bỏ qua {emp}: Không có Email hợp lệ.")
                            else:
                                res, msg = send_email_report(
                                    sender_email, sender_pass, emp_email, emp, df_emp, 
                                    total_phat, start_date.strftime('%d/%m/%Y'), end_date.strftime('%d/%m/%Y')
                                )
                                if res: success_count += 1
                                else: error_messages.append(f"❌ Lỗi gửi {emp}: {msg}")
                                    
                            progress_bar.progress((i + 1) / len(selected_to_send))
                            time.sleep(0.5)
                        
                        if success_count > 0: st.success(f"✅ Đã gửi thành công {success_count} email báo cáo!")
                        if error_messages:
                            for err in error_messages: st.error(err)
