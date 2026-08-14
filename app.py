import streamlit as st
import pandas as pd
from datetime import date
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH TRANG ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
        div[data-testid="stVerticalBlock"] > div { gap: 0.2rem !important; }
        h1, h2, h3 { padding-bottom: 0rem !important; margin-bottom: 0rem !important; }
        button { margin-top: 5px !important; }
    </style>
""", unsafe_allow_html=True)

# --- THÔNG TIN KẾT NỐI ---
SHEET_MAT_KHAU_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
SHEET_DU_PHONG_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"

@st.cache_resource
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        return gspread.authorize(creds)
    except: return None

@st.cache_data(ttl=30)
def load_data():
    client = get_gspread_client()
    # Tải danh sách NV từ sheet Mật khẩu
    rows_nv = client.open_by_key(SHEET_MAT_KHAU_ID).worksheet("Sheet1").get_all_values()
    df_nv = pd.DataFrame(rows_nv[1:], columns=rows_nv[0])
    df_nv.columns = [str(c).strip() for c in df_nv.columns]
    
    # Tải quy tắc từ sheet LoaiNghi (lấy dạng mảng thô để không bị lỗi KeyError với tên cột)
    sheet_rule = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi")
    rule_values = sheet_rule.get_all_values()
    
    return df_nv, rule_values

# --- HỆ THỐNG ĐĂNG NHẬP ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    df_nv, _ = load_data()
    
    col_ten = [c for c in df_nv.columns if 'tên' in c.lower() or 'nhân viên' in c.lower()]
    col_ten = col_ten[0] if col_ten else df_nv.columns[1]
    
    col_mk = [c for c in df_nv.columns if 'mật khẩu' in c.lower() or 'pass' in c.lower()]
    col_mk = col_mk[0] if col_mk else df_nv.columns[2]
    
    col_role = [c for c in df_nv.columns if 'phân quyền' in c.lower() or 'role' in c.lower()]
    col_role = col_role[0] if col_role else df_nv.columns[3] if len(df_nv.columns) > 3 else col_mk

    with st.form("login_form"):
        u = st.text_input("Tên đăng nhập").strip()
        p = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng Nhập"):
            match = df_nv[df_nv[col_ten].astype(str).str.strip().str.lower() == u.lower()]
            
            db_pass = str(match.iloc[0][col_mk]).strip() if not match.empty else ""
            db_role = str(match.iloc[0][col_role]).strip().lower() if not match.empty else "nhanvien"
            
            if (u == "admin" and p == "32531235") or (not match.empty and db_pass == p):
                st.session_state.update(logged_in=True, user=u, role="admin" if u == "admin" else db_role)
                st.rerun()
            else: st.error("❌ Sai tên đăng nhập hoặc mật khẩu!")
    st.stop()

# --- GIAO DIỆN CHÍNH ---
st.title(f"📊 Lịch Nghỉ Vera Spa - {st.session_state.user}")
if st.button("🚪 Đăng xuất"):
    st.session_state.logged_in = False
    st.rerun()

df_nv, rule_values = load_data()
col_ten_nv = [c for c in df_nv.columns if 'tên' in c.lower() or 'nhân viên' in c.lower()]
col_ten_nv = col_ten_nv[0] if col_ten_nv else df_nv.columns[1]

# --- NHẬP LỊCH NGHỈ ---
with st.expander("📝 Nhập lịch nghỉ mới", expanded=True):
    rules = {}
    # Bỏ qua dòng tiêu đề (index 0) của sheet LoaiNghi
    for row in rule_values[1:]:
        if len(row) > 1 and str(row[1]).strip():
            ly_do = str(row[1]).strip()
            loai_nghi = str(row[2]).lower() if len(row) > 2 else ""
            val = row[3] if len(row) > 3 else 0
            # Lấy danh sách user có quyền từ cột H trở đi (index 7)
            allowed = [str(x).lower().strip() for x in row[7:] if str(x).strip()] if len(row) > 7 else []
            
            rules[ly_do] = {
                "loai": loai_nghi,
                "val": val,
                "allowed": allowed
            }
    
    if not rules:
        st.warning("⚠️ Không tìm thấy quy tắc nào trong sheet LoaiNghi.")
    else:
        c1, c2, c3 = st.columns(3)
        list_nv = df_nv[col_ten_nv].dropna().astype(str).str.strip().tolist()
        nv = c1.selectbox("Chọn nhân viên", list_nv)
        ngay = c2.date_input("Chọn ngày nghỉ")
        loai = c3.selectbox("Chọn loại nghỉ", list(rules.keys()))
        
        # Kiểm tra phân quyền từ cột H
        allowed_list = rules[loai]['allowed']
        if allowed_list and st.session_state.role not in allowed_list and st.session_state.role != "admin":
            st.error(f"🚫 Bạn ({st.session_state.role}) không có quyền nhập loại nghỉ: {loai}")
        else:
            is_khong_phep = "không phép" in rules[loai]['loai']
            can_book = True
            
            # Nếu là có phép thì kiểm tra giới hạn số người nghỉ trong ngày
            if not is_khong_phep:
                client = get_gspread_client()
                lich_data = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").get_all_values()
                if len(lich_data) > 1:
                    df_lich = pd.DataFrame(lich_data[1:], columns=lich_data[0])
                    count_today = len(df_lich[df_lich['Ngày'] == str(ngay)])
                    limit = 2 if ngay.weekday() >= 5 else 5
                    
                    if count_today >= limit:
                        st.warning(f"⚠️ Đã đạt giới hạn {limit} người nghỉ (Có phép) cho ngày này!")
                        can_book = False
            
            if can_book:
                chitiet = st.text_input("Chi tiết/Ghi chú")
                if st.button("💾 Xác nhận và Ghi dữ liệu"):
                    client = get_gspread_client()
                    try:
                        client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").append_row(
                            [str(ngay), nv, loai, chitiet, rules[loai]['val'], "", "", str(date.today()), st.session_state.user]
                        )
                        st.success("✅ Đã ghi nhận lịch nghỉ thành công!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Lỗi ghi dữ liệu: {e}")

# --- HIỂN THỊ BẢNG LỊCH SỬ ---
st.subheader("📅 Lịch sử đăng ký lịch nghỉ")
try:
    client = get_gspread_client()
    data = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").get_all_values()
    if len(data) > 1:
        df_lich = pd.DataFrame(data[1:], columns=data[0])
        st.dataframe(df_lich, use_container_width=True)
    else:
        st.info("Chưa có dữ liệu lịch nghỉ.")
except: 
    st.info("Chưa có dữ liệu lịch nghỉ.")
