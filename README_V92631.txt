V92.6.31
Cơ chế LoaiNghi:
1. Bình thường KHÔNG đọc Google Sheet LoaiNghi.
2. Dùng snapshot PostgreSQL nếu đã có.
3. Nếu chưa có snapshot PostgreSQL, dùng 45 quy định được embedded trong app.py.
4. Chỉ Admin thấy nút 'Cập nhật qui định'.
5. Khi bấm: đọc Google Sheet 1 lần -> validate -> write PostgreSQL -> áp dụng ngay.
6. Các instance Cloud Run khác đọc snapshot PostgreSQL, cache tối đa 60 giây.
7. Nếu PostgreSQL không lưu được, rule mới chỉ có hiệu lực trong phiên hiện tại; app báo cảnh báo rõ.
