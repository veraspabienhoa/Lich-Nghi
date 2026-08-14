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
    # Tải quy tắc từ sheet LoaiNghi
    rows_rule = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi").get_all_values()
    df_rule = pd.DataFrame(rows_rule[1:], columns=rows_rule[0])
    return df_nv, df_rule

# --- HỆ THỐNG ĐĂNG NHẬP ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập Hệ Thống")
    df_nv, _ = load_data()
    with st.form("login_form"):
        u = st.text_input("Tên đăng nhập").strip()
        p = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng Nhập"):
            match = df_nv[df_nv['Tên nhân viên'].str.lower() == u.lower()]
            if (u=="admin" and p=="32531235") or (not match.empty and match.iloc[0]['Mật khẩu'] == p):
                st.session_state.update(logged_in=True, user=u, role="admin" if u=="admin" else match.iloc[0]['Phân quyền'])
                st.rerun()
            else: st.error("❌ Sai thông tin đăng nhập!")
    st.stop()

# --- GIAO DIỆN CHÍNH ---
st.title(f"📊 Lịch Nghỉ Vera Spa - {st.session_state.user}")
if st.button("🚪 Đăng xuất"):
    st.session_state.logged_in = False
    st.rerun()

df_nv, df_rule = load_data()

# --- NHẬP LỊCH NGHỈ (TỐI ƯU HÓA ĐỘNG) ---
with st.expander("📝 Nhập lịch nghỉ mới", expanded=True):
    # Xây dựng bảng quy tắc động từ sheet LoaiNghi
    rules = {}
    for _, r in df_rule.iterrows():
        # r[1]: Lý do, r[2]: Loại nghỉ, r[3]: Giá trị, r[7:]: Quyền (từ cột H)
        if str(r[1]).strip():
            rules[str(r[1]).strip()] = {
                "loai": str(r[2]).lower(), 
                "val": r[3], 
                "allowed": [str(x).lower().strip() for x in r.iloc[7:] if str(x).strip()]
            }
    
    c1, c2, c3 = st.columns(3)
    nv = c1.selectbox("Chọn nhân viên", df_nv['Tên nhân viên'].tolist())
    ngay = c2.date_input("Chọn ngày nghỉ")
    loai = c3.selectbox("Chọn loại nghỉ", list(rules.keys()))
    
    # Kiểm tra phân quyền từ cột H (User có quyền)
    if st.session_state.role not in rules[loai]['allowed']:
        st.error(f"🚫 Bạn ({st.session_state.role}) không có quyền nhập loại nghỉ: {loai}")
    else:
        # LOGIC KIỂM TRA GIỚI HẠN (CHỈ ÁP DỤNG CHO LOẠI NGHỈ CÓ PHÉP)
        is_khong_phep = "không phép" in rules[loai]['loai']
        can_book = True
        
        if not is_khong_phep:
            client = get_gspread_client()
            lich_data = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").get_all_values()
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
    df_lich = pd.DataFrame(data[1:], columns=data[0])
    st.dataframe(df_lich, use_container_width=True)
except: 
    st.info("Chưa có dữ liệu lịch nghỉ.")
