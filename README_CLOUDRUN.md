# Vera Spa V75 — Cloud Run + PostgreSQL

## Mục tiêu

Bản này giữ nguyên Streamlit nhưng thêm PostgreSQL dùng chung giữa các Cloud Run instance để giảm mạnh số lần đọc Google Sheets khi nhiều người đăng nhập cùng lúc.

### Kiến trúc V75

```text
Người dùng
   │
   ▼
Cloud Run / Streamlit (2 vCPU, 4 GiB, min 1, max 5, concurrency 8)
   │
   ├── PostgreSQL / Cloud SQL
   │      ├── shared dataset cache
   │      ├── advisory lock chống nhiều instance cùng refresh
   │      └── stale-while-refresh khi Google API chậm/quota
   │
   └── Google Sheets + Drive
          └── nguồn đồng bộ/backup trong giai đoạn chuyển đổi an toàn
```

PostgreSQL được dùng cho các nhóm đọc nhiều:

- Hồ sơ/tài khoản nhân viên
- Lịch nghỉ chính
- Lịch nghỉ nguồn thứ hai
- Tích lũy
- Nghĩa vụ vi phạm
- Lịch sử bảng lương

Khi dữ liệu PostgreSQL hết TTL, chỉ **một** Cloud Run instance được quyền refresh nguồn nhờ PostgreSQL advisory lock. Các instance khác dùng snapshot cũ trong thời gian refresh thay vì cùng gọi Google Sheets.

## Tại sao chưa bỏ Google Sheets ngay trong một lần

Ứng dụng hiện có nhiều nghiệp vụ gắn với vị trí dòng vật lý trên Google Sheet, tự xếp lại vi phạm, lịch sử lương, Tích lũy, phân quyền và các macro nghiệp vụ khác. V75 dùng chiến lược chuyển đổi an toàn: PostgreSQL nhận tải đọc trước; Google Sheets vẫn là write-through/backup trong thời gian kiểm tra đối chiếu. Schema đã chuẩn bị sẵn các bảng normalized để chuyển CRUD sang PostgreSQL-primary sau khi xác nhận dữ liệu đối chiếu đúng.

## 1. Tạo Cloud SQL PostgreSQL

Chạy:

```bash
export PROJECT_ID="your-project-id"
export REGION="asia-southeast1"
./create_cloudsql.sh
```

Script tạo PostgreSQL 16 ban đầu với 2 vCPU / 4 GB RAM, SSD và tự tăng dung lượng.

## 2. Secrets

Tạo Gmail app password trong Secret Manager:

```bash
printf '%s' 'YOUR_GMAIL_APP_PASSWORD' | \
  gcloud secrets create vera-smtp-app-password --data-file=-
```

DB password được `create_cloudsql.sh` tạo vào secret `vera-db-password`.

Google Sheets có 2 cách:

1. Khuyến nghị: chạy Cloud Run bằng service account được chia sẻ quyền trực tiếp vào các Google Sheets/Drive hiện có; app dùng Application Default Credentials.
2. Hoặc lưu JSON service account hiện tại vào Secret Manager và map sang `GOOGLE_SERVICE_ACCOUNT_JSON`.

## 3. Khởi tạo schema

Có thể chạy `schema.sql` bằng psql hoặc chạy container/job với:

```bash
python init_postgres.py
```

Lưu ý app cũng tự tạo 2 bảng cache cốt lõi khi kết nối lần đầu.

## 4. Deploy Cloud Run

```bash
export PROJECT_ID="your-project-id"
export REGION="asia-southeast1"
./deploy_cloudrun.sh
```

Thiết lập mặc định trong script:

- 2 vCPU
- 4 GiB RAM
- min instances = 1
- max instances = 5
- concurrency = 8
- request/WebSocket timeout = 3600 giây
- session affinity bật sau deploy
- PostgreSQL pool size 8, overflow 12

## 5. Đồng bộ lần đầu

Đăng nhập Admin → **Giao diện tùy chỉnh** → mở:

**⚡ Hạ tầng & hiệu năng (Cloud Run + PostgreSQL)**

Bấm:

**⚡ Đồng bộ dữ liệu nặng Google Sheets → PostgreSQL**

Sau đó bảng trạng thái PostgreSQL cho biết dataset, số dòng, thời điểm cập nhật và TTL.

## 6. Kiểm tra tải trước khi tắt Streamlit Community Cloud

Không chuyển toàn bộ traffic ngay. Chạy Cloud Run song song vài ngày và kiểm tra:

- đăng nhập đồng thời nhiều user
- Đăng ký nghỉ phép
- Quản lý lịch nghỉ / sửa batch / xóa
- Bảng tour
- Tính lương
- Lịch sử bảng lương
- Email bảng lương
- Tích lũy / nợ vi phạm
- quyền admin/letan/quanly/nhanvien/leader/locker/tapvu

Khi các dữ liệu đối chiếu đúng, bước tiếp theo là chuyển write path sang các bảng normalized (`employees`, `leave_records`, `payroll_history_rows`, `app_config`) và dùng `sync_outbox` để đẩy bản backup/reporting sang Google Sheets.

## 7. Biến môi trường chính

Xem `.env.example`.

Quan trọng:

- `VERA_DB_ENABLED=1`
- `DB_NAME`
- `DB_USER`
- `DB_PASS`
- `INSTANCE_CONNECTION_NAME`
- `SMTP_APP_PASSWORD`

Không đưa mật khẩu DB, Gmail app password hoặc service-account private key vào Git/source image.

## Fallback

Nếu PostgreSQL chưa bật hoặc tạm lỗi, V75 vẫn quay về các loader Google Sheets cũ để hệ thống không bị ngừng hoàn toàn.
