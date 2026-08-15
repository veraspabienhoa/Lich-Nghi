import zoneinfo
import calendar
from datetime import datetime, timedelta

# Thiết lập hằng số múi giờ Việt Nam (UTC+7)
VN_TZ = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")

class VietnamTimeFilter:
    """
    Class chuyên dụng để tính toán các khoảng thời gian cho bộ lọc dữ liệu,
    đảm bảo luôn chính xác theo múi giờ Việt Nam.
    """
    @staticmethod
    def get_current_time():
        return datetime.now(VN_TZ)

    @classmethod
    def get_date_range(cls, filter_type, custom_start=None, custom_end=None):
        """
        Trả về (start_datetime, end_datetime) theo chuẩn múi giờ VN dựa trên loại bộ lọc.
        """
        now = cls.get_current_time()
        # Đưa về 00:00:00 của ngày hôm nay
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        if filter_type == "Hôm nay":
            start = today_start
            end = today_start + timedelta(days=1, microseconds=-1)
            
        elif filter_type == "Hôm qua":
            start = today_start - timedelta(days=1)
            end = today_start - timedelta(microseconds=1)
            
        elif filter_type == "Tuần này":
            # Thứ 2 là 0, Chủ nhật là 6
            start = today_start - timedelta(days=today_start.weekday())
            end = start + timedelta(days=7, microseconds=-1)
            
        elif filter_type == "Tuần trước":
            start = today_start - timedelta(days=today_start.weekday() + 7)
            end = start + timedelta(days=7, microseconds=-1)
            
        elif filter_type == "Tháng này":
            start = today_start.replace(day=1)
            last_day = calendar.monthrange(start.year, start.month)[1]
            end = start.replace(day=last_day) + timedelta(days=1, microseconds=-1)
            
        elif filter_type == "Tháng trước":
            first_day_this_month = today_start.replace(day=1)
            last_day_prev_month = first_day_this_month - timedelta(days=1)
            start = last_day_prev_month.replace(day=1)
            end = first_day_this_month - timedelta(microseconds=1)
            
        elif filter_type == "Chọn ngày":
            # custom_start format: "YYYY-MM-DD"
            if custom_start:
                start = datetime.strptime(custom_start, "%Y-%m-%d").replace(tzinfo=VN_TZ)
                end = start + timedelta(days=1, microseconds=-1)
            else:
                return None, None
                
        elif filter_type == "Khoảng thời gian":
            # custom_start, custom_end format: "YYYY-MM-DD"
            if custom_start and custom_end:
                start = datetime.strptime(custom_start, "%Y-%m-%d").replace(tzinfo=VN_TZ)
                end_day = datetime.strptime(custom_end, "%Y-%m-%d").replace(tzinfo=VN_TZ)
                end = end_day + timedelta(days=1, microseconds=-1)
            else:
                return None, None
        else:
            return None, None
            
        return start, end

# =====================================================================
# VÍ DỤ CÁCH GỌI VÀ SỬ DỤNG TRONG API CỦA BẠN (FLASK ROUTE DEMO)
# =====================================================================
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/api/lich-nghi/filter', methods=['GET'])
def filter_schedule():
    # Lấy thông số từ bộ lọc trên website gửi xuống
    # Ví dụ: ?type=Tháng này hoặc ?type=Khoảng thời gian&start=2026-08-01&end=2026-08-15
    filter_type = request.args.get('type', 'Hôm nay')
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    
    # Đưa vào Class xử lý
    start_dt, end_dt = VietnamTimeFilter.get_date_range(
        filter_type=filter_type, 
        custom_start=start_date_str, 
        custom_end=end_date_str
    )
    
    if not start_dt or not end_dt:
        return jsonify({"error": "Thiếu tham số ngày tháng hoặc bộ lọc không hợp lệ"}), 400

    # Tại đây, bạn sẽ dùng start_dt và end_dt để query database.
    # ... logic truy vấn cơ sở dữ liệu nghỉ phép / thu chi ...

    return jsonify({
        "filter_applied": filter_type,
        "query_start_vn": start_dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "query_end_vn": end_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
