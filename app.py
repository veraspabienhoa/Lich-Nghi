import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar
import io
import gspread
from google.oauth2.service_account import Credentials

# --- CẤU HÌNH ---
st.set_page_config(page_title="Lịch Nghỉ Vera Spa", page_icon="📅", layout="wide")
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
    # Tải danh sách NV
    rows_nv = client.open_by_key(SHEET_MAT_KHAU_ID).worksheet("Sheet1").get_all_values()
    df_nv = pd.DataFrame(rows_nv[1:], columns=rows_nv[0])
    # Tải quy tắc
    rows_rule = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("LoaiNghi").get_all_values()
    df_rule = pd.DataFrame(rows_rule[1:], columns=rows_rule[0])
    return df_nv, df_rule

# --- ĐĂNG NHẬP ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if not st.session_state.logged_in:
    st.title("🔐 Đăng Nhập")
    df_nv, _ = load_data()
    with st.form("login"):
        u = st.text_input("Tên").strip()
        p = st.text_input("MK", type="password")
        if st.form_submit_button("Đăng Nhập"):
            match = df_nv[df_nv['Tên nhân viên'].str.lower() == u.lower()]
            if (u=="admin" and p=="32531235") or (not match.empty and match.iloc[0]['Mật khẩu'] == p):
                st.session_state.update(logged_in=True, user=u, role="admin" if u=="admin" else match.iloc[0]['Phân quyền'])
                st.rerun()
            else: st.error("Sai!")
    st.stop()

# --- XỬ LÝ LỊCH NGHỈ ---
df_nv, df_rule = load_data()

with st.expander("📝 Nhập lịch nghỉ mới", expanded=True):
    # Mapping quy tắc
    rules = {}
    for _, r in df_rule.iterrows():
        # r[1]: Lý do, r[2]: Loại nghỉ, r[3]: Giá trị, r[7:]: Quyền
        rules[r[1]] = {"loai": r[2], "val": r[3], "allowed": [x.lower() for x in r[7:] if x]}
    
    c1, c2, c3 = st.columns(3)
    nv = c1.selectbox("Nhân viên", df_nv['Tên nhân viên'].tolist())
    ngay = c2.date_input("Ngày")
    loai = c3.selectbox("Loại nghỉ", list(rules.keys()))
    
    # Kiểm tra quyền
    if st.session_state.role not in rules[loai]['allowed']:
        st.error("🚫 Bạn không có quyền nhập loại này.")
    else:
        # LOGIC KIỂM TRA GIỚI HẠN
        is_weekend = ngay.weekday() >= 5
        is_khong_phep = "không phép" in rules[loai]['loai'].lower()
        
        can_book = True
        if not is_khong_phep:
            # Chỉ kiểm tra giới hạn nếu là CÓ PHÉP
            client = get_gspread_client()
            lich_data = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").get_all_values()
            df_lich = pd.DataFrame(lich_data[1:], columns=lich_data[0])
            count_today = len(df_lich[df_lich['Ngày'] == str(ngay)])
            limit = 2 if is_weekend else 5
            if count_today >= limit:
                st.warning(f"⚠️ Đã đạt giới hạn {limit} người nghỉ (Có phép) ngày này!")
                can_book = False
        
        if can_book:
            if st.button("💾 Xác nhận"):
                # Ghi vào Sheet1
                client = get_gspread_client()
                client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").append_row(
                    [str(ngay), nv, loai, "", rules[loai]['val'], "", "", str(date.today()), st.session_state.user]
                )
                st.success("Đã ghi!")
                st.rerun()

# --- HIỂN THỊ BẢNG ---
st.subheader("Lịch sử đăng ký")
client = get_gspread_client()
data = client.open_by_key(SHEET_DU_PHONG_ID).worksheet("Sheet1").get_all_values()
st.dataframe(pd.DataFrame(data[1:], columns=data[0]), use_container_width=True)
