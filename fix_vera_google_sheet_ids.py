#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VERA SPA - Chuyển toàn bộ Python jobs sang 2 Google Sheet mới
Ngày tạo: 2026-08-22

Cách dùng:
1) Đặt file này tại thư mục gốc repo Vera-Spa (cùng cấp app.py).
2) Chạy:
       python fix_vera_google_sheet_ids.py
3) Nếu tất cả hiện [OK], commit/push các file được liệt kê.

File sẽ:
- Quét toàn bộ *.py trong repo.
- Thay ID Google Sheet nhân viên cũ -> ID mới.
- Thay ID LichNghi_VeraSpa cũ -> ID mới.
- Không sửa file binary / .git / virtualenv.
- Tự py_compile các file đã thay đổi.
- Quét lại để bảo đảm ID cũ = 0.
"""

from __future__ import annotations

import py_compile
import sys
from pathlib import Path

OLD_EMPLOYEE_SHEET_ID = "1DGXy3kPyMPwtz-3CnG8i6BiQbXFDApasoXVFzSmUe24"
NEW_EMPLOYEE_SHEET_ID = "1NCMm2RApdukIiqAma7OF1E_8cq9yTjeK3k7JbMfgSoU"

OLD_LEAVE_SHEET_ID = "1Kz0aw-JatptAN9G7YSwZ6rJO09urOPaD-rS-18eZSY0"
NEW_LEAVE_SHEET_ID = "1udft7erC-VxpjAa97TyD6KMVlDYBxEd-hsD7Bd_zLnE"

REPLACEMENTS = {
    OLD_EMPLOYEE_SHEET_ID: NEW_EMPLOYEE_SHEET_ID,
    OLD_LEAVE_SHEET_ID: NEW_LEAVE_SHEET_ID,
}

SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", "node_modules",
}

# Các file production quan trọng cần đặc biệt xác nhận sau khi chạy.
IMPORTANT_FILES = [
    "timesoft_sync_job.py",
    "auto_penalty_daily_job.py",
    "vera_daily_ops_job.py",
    "timesoft_snapshot_job.py",
    "migrate_leave_sheet_to_AL.py",
    "app.py",
]


def iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == Path(__file__).name:
            continue
        yield path


def read_utf8(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    root = Path(__file__).resolve().parent
    print("=" * 72)
    print("VERA SPA - FIX GOOGLE SHEET IDs")
    print(f"Repo: {root}")
    print("=" * 72)

    changed = []
    total_old_hits_before = 0

    for path in sorted(iter_python_files(root)):
        text = read_utf8(path)
        if text is None:
            continue

        hits = sum(text.count(old) for old in REPLACEMENTS)
        if hits <= 0:
            continue

        total_old_hits_before += hits
        new_text = text
        for old, new in REPLACEMENTS.items():
            new_text = new_text.replace(old, new)

        if new_text != text:
            # Backup ngay cạnh file để có thể khôi phục thủ công khi cần.
            backup = path.with_suffix(path.suffix + ".before_sheet_move.bak")
            if not backup.exists():
                backup.write_text(text, encoding="utf-8", newline="\n")

            path.write_text(new_text, encoding="utf-8", newline="\n")
            changed.append(path.relative_to(root))

    print(f"\nID cũ tìm thấy trước khi sửa: {total_old_hits_before}")

    if changed:
        print("\nĐÃ SỬA:")
        for path in changed:
            print(f"  - {path}")
    else:
        print("\nKhông có file nào cần thay ID. Có thể repo đã được sửa trước đó.")

    # Quét lại toàn repo.
    remaining = []
    new_employee_hits = 0
    new_leave_hits = 0

    for path in sorted(iter_python_files(root)):
        text = read_utf8(path)
        if text is None:
            continue
        for old in REPLACEMENTS:
            if old in text:
                remaining.append((path.relative_to(root), old))
        new_employee_hits += text.count(NEW_EMPLOYEE_SHEET_ID)
        new_leave_hits += text.count(NEW_LEAVE_SHEET_ID)

    print("\nKIỂM TRA ID SAU KHI SỬA:")
    print(f"  ID nhân viên mới: {new_employee_hits} lần")
    print(f"  ID lịch nghỉ mới: {new_leave_hits} lần")
    print(f"  ID cũ còn lại: {len(remaining)}")

    if remaining:
        print("\n[ERROR] Vẫn còn ID cũ:")
        for path, old in remaining:
            print(f"  - {path}: {old}")
        return 2

    # Kiểm tra syntax cho toàn bộ file vừa thay.
    syntax_errors = []
    print("\nPY_COMPILE:")
    for rel in changed:
        path = root / rel
        try:
            py_compile.compile(str(path), doraise=True)
            print(f"  [OK] {rel}")
        except Exception as exc:
            syntax_errors.append((rel, exc))
            print(f"  [ERROR] {rel}: {exc}")

    if syntax_errors:
        print("\nCó lỗi cú pháp. Hãy dùng file .before_sheet_move.bak để khôi phục.")
        return 3

    print("\nKIỂM TRA FILE PRODUCTION:")
    for name in IMPORTANT_FILES:
        path = root / name
        if not path.exists():
            print(f"  [INFO] Không có {name}")
            continue
        text = read_utf8(path) or ""
        has_old = OLD_EMPLOYEE_SHEET_ID in text or OLD_LEAVE_SHEET_ID in text
        has_new_employee = NEW_EMPLOYEE_SHEET_ID in text
        has_new_leave = NEW_LEAVE_SHEET_ID in text

        if has_old:
            status = "ERROR còn ID cũ"
        elif has_new_employee or has_new_leave:
            status = "OK dùng ID mới"
        else:
            # Một số file như auto_penalty_daily_job.py chỉ import timesoft_sync_job,
            # nên không nhất thiết phải chứa ID trực tiếp.
            status = "OK không hard-code ID (có thể dùng qua import)"

        print(f"  [{status}] {name}")

    print("\n" + "=" * 72)
    print("[OK] Hoàn tất.")
    print("Google Sheet nhân viên mới:")
    print(f"  {NEW_EMPLOYEE_SHEET_ID}")
    print("Google Sheet lịch nghỉ mới:")
    print(f"  {NEW_LEAVE_SHEET_ID}")
    print("\nBước tiếp theo:")
    print("  git status")
    print("  git add *.py")
    print('  git commit -m "fix: move VERA jobs to new Google Sheets"')
    print("  git push origin main")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
