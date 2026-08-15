# --- KHỞI TẠO BIẾN GIAO DIỆN TOÀN TRANG ---
if "global_font" not in st.session_state:
    st.session_state.global_font = "Cinzel Decorative"
if "global_size" not in st.session_state:
    st.session_state.global_size = 16
if "global_color" not in st.session_state:
    st.session_state.global_color = "#333333"

# --- ÉP CSS THU GỌN GIAO DIỆN & TÙY CHỈNH TOÀN TRANG ---
st.markdown(f"""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel+Decorative:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Arial:wght@400;700&display=swap');
        
        /* Áp dụng giao diện toàn trang */
        html, body, [class*="css"], [data-testid="stAppViewContainer"], p, div, span, h1, h2, h3, li, a {{
            font-family: '{st.session_state.global_font}', sans-serif !important;
            font-size: {st.session_state.global_size}px;
            color: {st.session_state.global_color};
        }}
        
        .block-container {{
            padding-top: 1.5rem; padding-bottom: 1rem;
        }}
        
        @media (max-width: 768px) {{
            .block-container {{
                padding-top: 1rem !important; padding-left: 0.5rem !important; padding-right: 0.5rem !important;
            }}
        }}
        
        div[data-testid="stVerticalBlock"] > div {{ gap: 0.2rem !important; }}
        button {{ margin-top: 5px !important; }}
        
        .custom-main-title {{
            font-size: 35px !important;
            font-weight: bold;
            margin-bottom: 20px;
            font-family: '{st.session_state.global_font}', sans-serif !important;
        }}
        
        /* Đã giảm kích thước font chữ khu vực nhập lịch nghỉ xuống 1.2rem */
        [data-testid="stExpander"] details summary p {{
            font-size: 1.2rem !important; 
            font-weight: 900 !important;
            color: #d32f2f !important;
            text-transform: uppercase;
        }}
    </style>
""", unsafe_allow_html=True)
# ==========================================
# THIẾT LẬP MENU ĐIỀU HƯỚNG BÊN TRÁI (SIDEBAR)
# ==========================================
is_admin_letan = st.session_state.current_role in ["admin", "letan"]

if is_admin_letan:
    st.sidebar.title("📌 MENU CHỨC NĂNG")
    menu_options = ["📊 Tình Hình Nghỉ Phép", "⏰ Thiết Lập Ca Làm Việc", "👥 Quản Lý Nhân Sự"]
    
    if st.session_state.current_role == "admin":
        st.sidebar.markdown("---")
        with st.sidebar.expander("🎨 Tùy chỉnh Giao diện Toàn trang"):
            fonts_list = ["Cinzel Decorative", "Arial", "Roboto", "Times New Roman", "Tahoma"]
            current_font_index = fonts_list.index(st.session_state.global_font) if st.session_state.global_font in fonts_list else 0
            sel_font = st.selectbox("Font chữ:", fonts_list, index=current_font_index)
            sel_size = st.slider("Cỡ chữ (px):", 12, 24, st.session_state.global_size)
            sel_color = st.color_picker("Màu chữ chính:", st.session_state.global_color)
            if st.button("💾 Lưu giao diện toàn trang"):
                st.session_state.global_font = sel_font
                st.session_state.global_size = sel_size
                st.session_state.global_color = sel_color
                st.rerun()
                
        st.sidebar.markdown("---")
        st.sidebar.subheader("🛠 CÔNG CỤ ĐỒNG BỘ")
        
        if st.sidebar.button("🔄 Đồng Bộ Excel ➡️ Google Sheets", help="Chỉ thêm dữ liệu mới từ Excel sang GSheet"):
            with st.spinner("Đang kiểm tra và đồng bộ các dòng mới..."):
                res, msg = admin_sync_excel_to_gsheet()
                if res: st.sidebar.success(msg)
                else: st.sidebar.error(msg)
                
        if st.sidebar.button("🔄 Đồng Bộ GSheet ➡️ Excel", help="Lấy dòng mới từ GSheet gộp vào file Excel tải xuống"):
            with st.spinner("Đang chuẩn bị file gộp..."):
                res, msg = admin_sync_gsheet_to_excel()
                if res: st.sidebar.success(msg)
                else: st.sidebar.error(msg)
                
        if "pending_excel_download" in st.session_state:
            st.sidebar.download_button(
                "📥 Tải xuống File Excel đã đồng bộ", 
                data=to_excel(st.session_state.pending_excel_download), 
                file_name=f"LichNghi_Merged_{get_vn_today().strftime('%d%m%Y')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
    selected_page = st.sidebar.radio("Chọn trang:", menu_options)
else:
    selected_page = "📊 Tình Hình Nghỉ Phép"
    # --- GIAO DIỆN HEADER CHÍNH BÊN PHẢI ---
st.write("")
col_title, col_logout = st.columns([7, 3]) 
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
    # --- HÀM ĐỒNG BỘ EXCEL -> GSHEET (CHỈ LẤY DÒNG MỚI) ---
def admin_sync_excel_to_gsheet():
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        file_id = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
        temp_file = "temp_sync.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file): os.remove(temp_file)
        
        df_excel = xls.iloc[:, :10].copy() # Lấy bao gồm cột ẩn
        df_excel.columns = ["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"]
        df_excel['Ngày'] = df_excel['Ngày'].apply(safe_date_str)
        df_excel = df_excel.fillna("")
        
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        gs_data = sheet_dp.get_all_values()
        df_gsheet = pd.DataFrame(gs_data[1:], columns=gs_data[0]) if len(gs_data) > 1 else pd.DataFrame(columns=df_excel.columns)
        
        # Tạo khóa nhận diện để tránh trùng (Ngày + Tên + Lý do)
        df_excel['uid'] = df_excel['Ngày'].astype(str).str.strip() + "_" + df_excel['Tên nhân viên'].astype(str).str.strip() + "_" + df_excel['Lý do nghỉ'].astype(str).str.strip()
        df_gsheet['uid'] = df_gsheet['Ngày'].astype(str).str.strip() + "_" + df_gsheet['Tên nhân viên'].astype(str).str.strip() + "_" + df_gsheet.get('Lý do nghỉ', df_gsheet.get('Loại nghỉ', '')).astype(str).str.strip()
        
        new_rows = df_excel[~df_excel['uid'].isin(df_gsheet['uid'])].copy()
        new_rows = new_rows.drop(columns=['uid'])
        
        values_to_append = new_rows.values.tolist()
        if values_to_append:
            sheet_dp.append_rows(values_to_append, value_input_option='USER_ENTERED')
            st.cache_data.clear()
            return True, f"Thành công! Đã thêm {len(values_to_append)} dòng MỚI từ Excel lên Google Sheets."
        return True, "Dữ liệu đã đồng bộ. Không có dòng mới nào từ Excel."
    except Exception as e:
        return False, f"Lỗi đồng bộ: {e}"

# --- HÀM ĐỒNG BỘ GSHEET -> EXCEL (GỘP VÀ XUẤT FILE TẢI VỀ) ---
def admin_sync_gsheet_to_excel():
    try:
        df_gsheet = load_backup_sheet_data()
        
        file_id = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
        temp_file = "temp_sync2.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file): os.remove(temp_file)
        
        df_excel = xls.iloc[:, :10].copy()
        df_excel.columns = ["Ngày", "Tên nhân viên", "Lý do nghỉ", "Chi tiết", "Số ngày tính", "Số ngày phép cộng dồn", "Phạt vi phạm", "Ngày cập nhật", "Giờ cập nhật", "Người cập nhật"]
        df_excel['Ngày'] = df_excel['Ngày'].apply(safe_date_str)
        df_excel = df_excel.fillna("")
        
        df_excel['uid'] = df_excel['Ngày'].astype(str).str.strip() + "_" + df_excel['Tên nhân viên'].astype(str).str.strip() + "_" + df_excel['Lý do nghỉ'].astype(str).str.strip()
        df_gsheet['uid'] = df_gsheet['Ngày'].astype(str).str.strip() + "_" + df_gsheet['Tên nhân viên'].astype(str).str.strip() + "_" + df_gsheet['Lý do nghỉ'].astype(str).str.strip()
        
        new_rows = df_gsheet[~df_gsheet['uid'].isin(df_excel['uid'])].copy()
        new_rows = new_rows.drop(columns=['uid'])
        
        if new_rows.empty:
            return True, "Dữ liệu đã đồng bộ. Không có dòng mới nào từ GSheet."
            
        st.session_state.pending_excel_download = pd.concat([df_excel.drop(columns=['uid']), new_rows], ignore_index=True)
        return True, f"Đã gộp {len(new_rows)} dòng mới. Vui lòng bấm nút 'Tải xuống File Excel' xuất hiện bên dưới Menu."
    except Exception as e:
        return False, f"Lỗi đồng bộ: {e}"
        # ==========================================
# PAGE 3: 👥 QUẢN LÝ NHÂN SỰ
# ==========================================
if selected_page == "👥 Quản Lý Nhân Sự" and is_admin_letan:
    st.subheader("Quản Lý Hệ Thống Nhân Viên")
    
    tab_add, tab_edit = st.tabs(["➕ Thêm / Xóa Nhân Viên", "🔍 Xem / Chỉnh Sửa Hồ Sơ"])
    
    with tab_add:
        col_ad1, col_ad2 = st.columns(2)
        with col_ad1:
            st.write("### Cấp tài khoản mới")
            with st.form("form_add_nv"):
                new_nv_name = st.text_input("Tên đăng nhập (Tên nhân viên) *")
                new_nv_pass = st.text_input("Mật khẩu *", value="123456")
                new_nv_role = st.selectbox("Phân quyền", ["nhanvien", "letan", "admin"])
                
                if st.form_submit_button("➕ Thêm Nhân Viên"):
                    if new_nv_name.strip():
                        try:
                            client = get_gspread_client()
                            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                            next_stt = len(sheet.get_all_values())
                            sheet.append_row([next_stt, new_nv_name.strip(), new_nv_pass.strip(), new_nv_role, "", "", "", "", "", "0", "0", "0", "", "", ""])
                            st.success(f"✅ Đã thêm tài khoản cho {new_nv_name}!")
                            st.cache_data.clear()
                        except Exception as e: st.error(f"Lỗi: {e}")
                    else: st.error("❌ Tên nhân viên không được để trống.")
        
        with col_ad2:
            st.write("### Xóa tài khoản")
            with st.form("form_delete_nv"):
                list_del = df_credentials['Tên nhân viên'].dropna().tolist()
                del_nv_name = st.selectbox("Chọn nhân viên cần xóa:", ["-- Chọn --"] + list_del)
                
                if st.form_submit_button("🗑️ Xóa Nhân Viên"):
                    if del_nv_name != "-- Chọn --":
                        try:
                            client = get_gspread_client()
                            sheet = client.open_by_key(SHEET_MAT_KHAU_ID).get_worksheet(0)
                            cell = sheet.find(del_nv_name, in_column=2)
                            if cell:
                                sheet.delete_rows(cell.row)
                                st.success(f"✅ Đã xóa hoàn toàn {del_nv_name} khỏi hệ thống!")
                                st.cache_data.clear()
                        except Exception as e: st.error(f"Lỗi: {e}")
                    else: st.error("❌ Vui lòng chọn tài khoản hợp lệ.")

    with tab_edit:
        if st.session_state.current_role == "admin":
            st.write("### Tra cứu và Cập nhật Hồ Sơ (Chỉ dành cho Admin)")
            edit_nv_name = st.selectbox("Lựa chọn hồ sơ nhân viên cần xem:", df_credentials['Tên nhân viên'].dropna().tolist())
            
            if edit_nv_name:
                nv_data = df_credentials[df_credentials['Tên nhân viên'] == edit_nv_name].iloc[0]
                with st.form("form_edit_profile"):
                    c1, c2 = st.columns(2)
                    with c1:
                        e_pass = st.text_input("Mật khẩu", value=nv_data.get('Mật khẩu', ''))
                        e_fullname = st.text_input("Họ và tên đầy đủ", value=nv_data.get('Họ và tên đầy đủ', ''))
                        e_dob = st.text_input("Ngày sinh", value=nv_data.get('Ngày sinh', ''))
                    with c2:
                        e_phone = st.text_input("Điện thoại", value=str(nv_data.get('Điện thoại', '')).replace("'", ""))
                        e_email = st.text_input("Email", value=nv_data.get('Email', ''))
                        e_address = st.text_input("Địa chỉ", value=nv_data.get('Địa chỉ', ''))
                    
                    if st.form_submit_button("💾 Ghi đè Hồ Sơ Mới"):
                        success, msg = update_user_profile(edit_nv_name, e_pass, e_fullname, e_dob, e_phone, e_email, e_address)
                        if success: st.success("✅ Đã ghi đè thông tin hồ sơ thành công!")
                        else: st.error(msg)
        else:
            st.warning("⚠️ Lễ Tân không có quyền xem chi tiết thông tin cá nhân (SĐT, Mật khẩu, Địa chỉ) của nhân sự khác. Vui lòng liên hệ Admin.")
            
