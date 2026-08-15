# --- ĐỒNG BỘ DỮ LIỆU TỪ EXCEL SANG GOOGLE SHEETS (CHỈ ADMIN) ---
def admin_sync_excel_to_gsheet():
    try:
        client = get_gspread_client()
        if not client: return False, "Chưa cấu hình quyền kết nối Google Sheets."
        
        file_id = "1xTjmi6BaQFSqsgn9-EM7MjVS2n2FNuxT"
        temp_file = "temp_sync.xlsb"
        download_file_from_google_drive(file_id, temp_file)
        
        xls = pd.read_excel(temp_file, sheet_name='LichNghi', engine='pyxlsb')
        if os.path.exists(temp_file): os.remove(temp_file)
        
        # Lấy thô từ cột A đến J (10 cột)
        df_raw = xls.iloc[:, :10].copy()
        
        # Hàm định dạng chuẩn chuỗi thuần túy cho từng ô
        def clean_val(val, is_date=False, is_time=False):
            try:
                if pd.isna(val) or str(val).strip() in ["nan", "NaT", "None", ""]:
                    return ""
                if is_time:
                    if hasattr(val, 'strftime'): return val.strftime('%H:%M:%S')
                    if isinstance(val, (int, float)):
                        total_seconds = int(round(val * 86400))
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        seconds = total_seconds % 60
                        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    return str(val).strip()
                
                if is_date or hasattr(val, 'strftime'):
                    if hasattr(val, 'strftime'): return val.strftime('%d/%m/%Y')
                    if isinstance(val, (int, float)):
                        return pd.to_datetime(val, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
                    s = str(val).strip().split(' ')[0]
                    return pd.to_datetime(s, dayfirst=True).strftime('%d/%m/%Y')
                
                return str(val).strip()
            except:
                return str(val).strip()

        # Làm sạch từng cột cụ thể để không bị dính index hoặc lỗi kiểu dữ liệu
        cols = df_raw.columns.tolist()
        if len(cols) > 0: df_raw[cols[0]] = df_raw[cols[0]].apply(lambda x: clean_val(x, is_date=True))
        if len(cols) > 7: df_raw[cols[7]] = df_raw[cols[7]].apply(lambda x: clean_val(x, is_date=True))
        if len(cols) > 8: df_raw[cols[8]] = df_raw[cols[8]].apply(lambda x: clean_val(x, is_time=True))
        
        # Làm sạch các cột còn lại
        for c in cols:
            if c != cols[0] and c != cols[7] and c != cols[8]:
                df_raw[c] = df_raw[c].apply(lambda x: clean_val(x))

        df_raw = df_raw.fillna("")
        while len(df_raw.columns) < 10: 
            df_raw[f"Col{len(df_raw.columns)}"] = ""
        
        # Chuyển đổi an toàn sang mảng list thuần túy
        values = df_raw.astype(str).values.tolist()
        sheet_dp = client.open_by_key(SHEET_DU_PHONG_ID).get_worksheet(0)
        
        try:
            sheet_dp.batch_clear(["A2:J"])
        except:
            pass
        
        if values:
            try:
                sheet_dp.update('A2', values, value_input_option='USER_ENTERED')
            except:
                sheet_dp.update(values, 'A2')
                
        st.cache_data.clear()
        return True, f"Đã sao chép và dán {len(values)} dòng dữ liệu từ Excel lên Sheet thành công!"
    except Exception as e:
        return False, f"Lỗi đồng bộ: {e}"
