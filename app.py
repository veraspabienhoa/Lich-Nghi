# --- HÀM TẢI QUY ĐỊNH LOẠI NGHỈ LINH HOẠT ---
@st.cache_data(ttl=60)
def load_loai_nghi_rules(df_loai_nghi):
    """
    Phân tích sheet LoaiNghi:
    - Cột 0: STT
    - Cột 1: Lý do nghỉ
    - Cột 2: Loại nghỉ (Có phép/Không phép)
    - Cột 3: Số ngày hoặc Số tiền phạt
    - Cột 4: Ngày áp dụng (Cuối tuần)
    - Cột 5-n: User có quyền
    """
    rules = {}
    for _, row in df_loai_nghi.iterrows():
        ly_do = str(row.iloc[1]).strip()
        if not ly_do or ly_do.lower() == "lý do nghỉ": continue
        
        # Phân tích giá trị (Số ngày tính hoặc tiền phạt)
        val = str(row.iloc[3]).replace(',', '').strip()
        try:
            val = float(val)
        except:
            val = 0.0
            
        # Lấy danh sách user có quyền (từ cột 5 trở đi)
        allowed_users = [str(u).strip().lower() for u in row.iloc[5:] if pd.notna(u) and str(u).strip()]
        
        rules[ly_do.lower()] = {
            "loai": str(row.iloc[2]).strip(),
            "value": val,
            "allowed_roles": allowed_users
        }
    return rules

# --- LOGIC KIỂM TRA QUYỀN TRONG GIAO DIỆN ---
def is_user_allowed(loai_nghi, current_role):
    # Logic kiểm tra xem current_role có nằm trong danh sách quyền của loại nghỉ đó không
    # Bạn có thể gọi hàm này trước khi hiển thị nút "Xác Nhận"
    pass

# --- TỐI ƯU CẬP NHẬT LOGIC NHẬP LIỆU ---
# Trong phần tab_input_lich, thay vì hard-code list, bạn dùng:
rules = load_loai_nghi_rules(df_loai_nghi)
list_loai_nghi = list(rules.keys())

# Lấy thông tin luật dựa trên lựa chọn
chosen_loai_info = rules.get(chosen_loai.lower())

# Kiểm tra quyền:
current_role_norm = st.session_state.current_role.lower()
if chosen_loai_info and current_role_norm not in chosen_loai_info['allowed_roles']:
    st.warning(f"🚫 Bạn ({st.session_state.current_role}) không có quyền nhập loại nghỉ: {chosen_loai}")
    submit_lich = False # Vô hiệu hóa nút xác nhận
else:
    # Hiển thị nút xác nhận bình thường
    submit_lich = st.form_submit_button("💾 Xác Nhận Ghi Lịch Nghỉ")
