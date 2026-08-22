"""VERA SPA - robust runtime wrapper for Auto Check 20:00.

V93.4 runtime hardening:
- Giữ nguyên 2 Google Sheet cũ.
- Retry đăng nhập/xác minh session TimeSoft.
- Auto Tour hỗ trợ TourVera Input hiện tại: B=Tên nhân viên, S=Giờ ra,
  U=Giờ vào, V=Ghi chú; cột V chỉ được xem là vi phạm khi có chuỗi
  "vao tre N phut". Nếu tương lai có cột "Vào trễ" riêng thì vẫn ưu tiên cột đó.
- Ghi một dòng AutoCheckRunLog cho mọi lần chạy, kể cả FAILED.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime

import timesoft_sync_job as ts


_ORIGINAL_CREATE_SESSION = ts.create_authenticated_session


def _safe_error(exc) -> str:
    """Log lỗi kỹ thuật nhưng không để lộ credential."""
    text = f"{type(exc).__name__}: {exc}"
    for secret in (getattr(ts, "USERNAME", ""), getattr(ts, "PASSWORD", "")):
        secret = str(secret or "")
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


def _robust_login_with_playwright(page, verify_url: str):
    """Đăng nhập TimeSoft và xác minh bằng page mới để tránh navigation conflict."""
    password_box = ts._visible_input(page, [
        'input[type="password"]', 'input[name*="password" i]',
        'input[id*="password" i]', 'input[name*="pass" i]',
        'input[id*="pass" i]',
    ])
    if password_box is None:
        return True, "session-existing"

    username_box = ts._visible_input(page, [
        'input[name="UserName"]', 'input[name="Username"]',
        'input[name*="username" i]', 'input[id*="username" i]',
        'input[name*="user" i]', 'input[id*="user" i]',
        'input[name*="account" i]', 'input[id*="account" i]',
        'input[name*="login" i]', 'input[id*="login" i]',
        'input[type="email"]', 'input[type="text"]',
    ])
    if username_box is None:
        return False, "Không nhận diện được ô tài khoản TimeSoft."

    try:
        username_box.click(timeout=8000)
        username_box.fill(ts.USERNAME)
        password_box.click(timeout=8000)
        password_box.fill(ts.PASSWORD)
        try:
            password_box.press("Tab")
        except Exception:
            pass
        page.wait_for_timeout(350)
    except Exception as exc:
        return False, "Không nhập được form TimeSoft: " + _safe_error(exc)

    submit = None
    for selector in [
        'button[type="submit"]', 'input[type="submit"]',
        'input[type="button"][value*="đăng" i]',
        'input[type="button"][value*="login" i]',
        'button:has-text("Đăng nhập")', 'button:has-text("Đăng Nhập")',
        'button:has-text("Login")', 'a:has-text("Đăng nhập")',
        'a:has-text("Đăng Nhập")', '[onclick*="login" i]', '[id*="login" i]',
    ]:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                node = loc.nth(i)
                if node.is_visible() and node.is_enabled():
                    submit = node
                    break
            if submit is not None:
                break
        except Exception:
            continue

    try:
        if submit is not None:
            submit.click(timeout=8000)
        else:
            password_box.press("Enter")
    except Exception as exc:
        ts._log("TIMESOFT LOGIN click/navigation WARN: " + _safe_error(exc))

    try:
        page.wait_for_timeout(2200)
    except Exception:
        pass

    last_error = ""
    for attempt in range(1, 4):
        probe = None
        try:
            if not page.is_closed():
                current_url = str(page.url or "")
                still_password = ts._visible_input(
                    page,
                    ['input[type="password"]', 'input[name*="password" i]'],
                )
                if "/user/login" not in current_url.lower() and still_password is None:
                    return True, f"login-ok-current-page-attempt-{attempt}"

            probe = page.context.new_page()
            probe.goto(verify_url, wait_until="domcontentloaded", timeout=35000)
            try:
                probe.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            probe.wait_for_timeout(900)

            final_url = str(probe.url or "")
            still_password = ts._visible_input(
                probe,
                ['input[type="password"]', 'input[name*="password" i]'],
            )
            if "/user/login" not in final_url.lower() and still_password is None:
                return True, f"login-ok-probe-attempt-{attempt}"

            err = ts._login_error_text(probe)
            last_error = "TimeSoft vẫn ở trang Login" + (f": {err}" if err else "")
        except Exception as exc:
            last_error = _safe_error(exc)
            ts._log(f"TIMESOFT LOGIN verify attempt {attempt}/3 WARN: {last_error}")
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

        try:
            page.wait_for_timeout(1200 * attempt)
        except Exception:
            time.sleep(1.2 * attempt)

    return False, "Không xác minh được session TimeSoft sau 3 lần: " + (last_error or "unknown")


def _create_authenticated_session_retry():
    last_error = None
    for attempt in range(1, 4):
        try:
            ts._log(f"TIMESOFT SESSION attempt {attempt}/3")
            session = _ORIGINAL_CREATE_SESSION()
            ts._log(f"TIMESOFT SESSION OK attempt {attempt}/3")
            return session
        except Exception as exc:
            last_error = exc
            ts._log(f"TIMESOFT SESSION attempt {attempt}/3 FAILED: {_safe_error(exc)}")
            if attempt < 3:
                time.sleep(2 * attempt)
    raise RuntimeError(
        "Không tạo được session TimeSoft sau 3 lần: " + _safe_error(last_error)
    ) from last_error


# Patch session trước khi import daily job.
ts._login_with_playwright = _robust_login_with_playwright
ts.create_authenticated_session = _create_authenticated_session_retry

import auto_penalty_daily_job as daily  # noqa: E402


RUN_LOG_WORKSHEET = "AutoCheckRunLog"
RUN_LOG_HEADERS = [
    "Run ID", "Ngày xử lý", "Bắt đầu", "Kết thúc", "Trạng thái", "Exit code",
    "Sheet nhân viên ID", "Sheet lịch nghỉ ID",
    "TimeSoft checkin_rows", "TimeSoft total",
    "TimeSoft eligible", "TimeSoft added", "TimeSoft skipped", "TimeSoft errors",
    "Tour eligible", "Tour added", "Tour skipped", "Tour errors",
    "Absence eligible", "Absence added", "Absence skipped", "Absence errors",
    "Thời gian chạy (giây)", "Chi tiết lỗi",
]

_CAPTURE = {
    "timesoft_rows": 0,
    "timesoft_total": 0,
    "timesoft": {},
    "tour": {},
    "absence": {},
    "messages": [],
}


def _capture_message(message) -> None:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text:
        return
    if "ERROR" in text.upper() or "FAILED" in text.upper() or "FATAL" in text.upper():
        _CAPTURE["messages"].append(text[:700])
        _CAPTURE["messages"] = _CAPTURE["messages"][-8:]


_ORIGINAL_DAILY_LOG = daily._log


def _daily_log_capture(message):
    _capture_message(message)
    return _ORIGINAL_DAILY_LOG(message)


daily._log = _daily_log_capture


_ORIGINAL_FETCH_CHECKIN = ts.fetch_checkin


def _fetch_checkin_capture(session, target_date):
    df, meta = _ORIGINAL_FETCH_CHECKIN(session, target_date)
    try:
        today = datetime.now(ts.VN_TZ).date()
        if target_date == today:
            _CAPTURE["timesoft_rows"] = int(len(df)) if df is not None else 0
            _CAPTURE["timesoft_total"] = int((meta or {}).get("Total") or 0)
    except Exception:
        pass
    return df, meta


ts.fetch_checkin = _fetch_checkin_capture


_ORIGINAL_PROCESS_TIMESOFT = daily.process_timesoft_today
_ORIGINAL_PROCESS_ABSENCE = daily.process_absence_without_checkin_today


def _process_timesoft_capture(*args, **kwargs):
    result, rows = _ORIGINAL_PROCESS_TIMESOFT(*args, **kwargs)
    _CAPTURE["timesoft"] = dict(result or {})
    return result, rows


def _process_absence_capture(*args, **kwargs):
    result, rows = _ORIGINAL_PROCESS_ABSENCE(*args, **kwargs)
    _CAPTURE["absence"] = dict(result or {})
    return result, rows


daily.process_timesoft_today = _process_timesoft_capture
daily.process_absence_without_checkin_today = _process_absence_capture


def _tour_note_late_minutes(value):
    """Chỉ nhận 'vao tre N phut'; số thuần ở Ghi chú là phút CÒN LẠI."""
    text = ts._norm(value)
    if not text or "vao tre" not in text:
        return None
    m = re.search(r"\bvao\s+tre\s+(\d+(?:[.,]\d+)?)\s*phut\b", text)
    if not m:
        m = re.search(r"\bvao\s+tre\b.*?(\d+(?:[.,]\d+)?)", text)
    if not m:
        return None
    try:
        return max(0.0, float(m.group(1).replace(",", ".")))
    except Exception:
        return None


def _process_tour_current_input(client, cfg: dict, employee_map: dict, catalog: dict):
    """Auto Tour theo TourVera Input hiện tại: B/S/U/V."""
    result = {"eligible": 0, "added": 0, "skipped": 0, "errors": 0}
    added_rows = []
    try:
        df = ts.load_bang_tour_input()
    except Exception as exc:
        daily._log(f"AUTO TOUR ERROR: {type(exc).__name__}: {exc}")
        result["errors"] += 1
        _CAPTURE["tour"] = dict(result)
        return result, added_rows

    if df is None or df.empty:
        _CAPTURE["tour"] = dict(result)
        return result, added_rows

    name_col = (
        ts._find_col(df, "Tên nhân viên")
        or ts._find_col(df, "Tên Nhân Viên")
        or ts._find_col(df, "Nhân viên")
        or ts._find_col(df, "NV")
    )
    late_col = ts._find_col(df, "Vào trễ")
    note_col = ts._find_col(df, "Ghi chú") or ts._find_col(df, "Ghi chu")
    out_col = ts._find_col(df, "Giờ ra")
    in_col = ts._find_col(df, "Giờ vào")

    if name_col is None or (late_col is None and note_col is None):
        daily._log(
            "AUTO TOUR ERROR: thiếu Tên nhân viên và/hoặc nguồn Vào trễ/Ghi chú. "
            f"columns={list(df.columns)}"
        )
        result["errors"] += 1
        _CAPTURE["tour"] = dict(result)
        return result, added_rows

    source_label = "Vào trễ" if late_col is not None else "Ghi chú"
    daily._log(
        f"AUTO TOUR source={source_label}; name_col={name_col}; "
        f"out_col={out_col}; in_col={in_col}"
    )

    threshold = max(
        ts.AUTO_PENALTY_MINUTES,
        int(cfg.get("threshold_minutes", ts.AUTO_PENALTY_MINUTES)),
    )
    today = datetime.now(ts.VN_TZ).date()

    for _, row in df.iterrows():
        if late_col is not None:
            minutes = ts._tour_late_minutes(row.get(late_col, ""))
        else:
            minutes = _tour_note_late_minutes(row.get(note_col, ""))

        if minutes is None or minutes < threshold:
            continue

        result["eligible"] += 1
        raw_name = row.get(name_col, "")
        employee = ts.canonical_employee(raw_name, employee_map)
        if not employee:
            result["skipped"] += 1
            daily._log(f"AUTO TOUR SKIP: không khớp nhân viên '{raw_name}'")
            continue

        reason_item = ts._outside_reason(minutes, catalog)
        if not reason_item:
            result["errors"] += 1
            daily._log(
                f"AUTO TOUR ERROR: chưa có loại phù hợp trong LoaiNghi cho {minutes:.0f} phút"
            )
            continue

        detail_parts = [f"Auto Update Bảng tour · vào muộn {int(round(minutes))} phút"]
        if out_col is not None and str(row.get(out_col, "")).strip():
            detail_parts.append(f"Giờ ra {str(row.get(out_col)).strip()}")
        if in_col is not None and str(row.get(in_col, "")).strip():
            detail_parts.append(f"Giờ vào {str(row.get(in_col)).strip()}")

        ok, msg = daily._save_auto_violation_new(
            client,
            today,
            employee,
            reason_item,
            " · ".join(detail_parts),
            daily.DAILY_ACTOR_TOUR,
        )
        if ok and msg == "SKIP_DUPLICATE":
            result["skipped"] += 1
        elif ok:
            result["added"] += 1
            added = daily._read_added_row(client, msg)
            if added:
                added["__minutes"] = int(round(minutes))
                added_rows.append(added)
            daily._log(
                f"AUTO TOUR ADDED: {employee} · {reason_item['name']} · {minutes:.0f} phút"
            )
        else:
            result["errors"] += 1
            daily._log(f"AUTO TOUR ERROR: {employee}: {msg}")

    _CAPTURE["tour"] = dict(result)
    return result, added_rows


daily.process_tour_today = _process_tour_current_input


def _stat(component, key):
    try:
        return int((_CAPTURE.get(component) or {}).get(key, 0) or 0)
    except Exception:
        return 0


def _append_run_log(started_at, ended_at, exit_code, extra_error=""):
    """Best-effort: lỗi ghi log không được làm job phạt bị fail."""
    try:
        client = ts.get_gspread_client()
        ss = client.open_by_key(ts.SHEET_DU_PHONG_ID)
        try:
            ws = ss.worksheet(RUN_LOG_WORKSHEET)
        except Exception:
            ws = ss.add_worksheet(title=RUN_LOG_WORKSHEET, rows=5000, cols=24)

        header = ws.get("A1:X1")
        existing = list(header[0]) if header else []
        if existing[:24] != RUN_LOG_HEADERS:
            ws.update(
                range_name="A1:X1",
                values=[RUN_LOG_HEADERS],
                value_input_option="USER_ENTERED",
            )

        component_errors = (
            _stat("timesoft", "errors")
            + _stat("tour", "errors")
            + _stat("absence", "errors")
        )
        if int(exit_code or 0) != 0:
            status = "FAILED"
        elif component_errors > 0:
            status = "SUCCESS_WITH_ERRORS"
        else:
            status = "SUCCESS"

        run_id = (
            str(os.getenv("CLOUD_RUN_EXECUTION", "") or "").strip()
            or f"manual-{started_at.strftime('%Y%m%d-%H%M%S')}"
        )
        messages = list(_CAPTURE.get("messages") or [])
        if extra_error:
            messages.append(str(extra_error))
        detail_error = " | ".join(messages[-8:])[:4000]

        duration = max(0.0, (ended_at - started_at).total_seconds())
        row = [
            run_id,
            started_at.astimezone(ts.VN_TZ).strftime("%d/%m/%Y"),
            started_at.astimezone(ts.VN_TZ).strftime("%d/%m/%Y %H:%M:%S"),
            ended_at.astimezone(ts.VN_TZ).strftime("%d/%m/%Y %H:%M:%S"),
            status,
            int(exit_code or 0),
            ts.SHEET_MAT_KHAU_ID,
            ts.SHEET_DU_PHONG_ID,
            int(_CAPTURE.get("timesoft_rows", 0) or 0),
            int(_CAPTURE.get("timesoft_total", 0) or 0),
            _stat("timesoft", "eligible"),
            _stat("timesoft", "added"),
            _stat("timesoft", "skipped"),
            _stat("timesoft", "errors"),
            _stat("tour", "eligible"),
            _stat("tour", "added"),
            _stat("tour", "skipped"),
            _stat("tour", "errors"),
            _stat("absence", "eligible"),
            _stat("absence", "added"),
            _stat("absence", "skipped"),
            _stat("absence", "errors"),
            round(duration, 3),
            detail_error,
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        ts._log(
            f"AUTOCHECK RUN LOG: status={status}; TimeSoft rows={row[8]}; "
            f"TS={row[10]}/{row[11]}/{row[12]}/{row[13]}; "
            f"Tour={row[14]}/{row[15]}/{row[16]}/{row[17]}"
        )
    except Exception as exc:
        ts._log("AUTOCHECK RUN LOG WARN: " + _safe_error(exc))


def _run_daily_with_persistent_log():
    started_at = datetime.now(ts.VN_TZ)
    exit_code = 1
    extra_error = ""
    try:
        exit_code = int(daily.run_daily() or 0)
        return exit_code
    except Exception as exc:
        extra_error = _safe_error(exc)
        ts._log("AUTOCHECK WRAPPER FATAL: " + extra_error)
        exit_code = 1
        return exit_code
    finally:
        ended_at = datetime.now(ts.VN_TZ)
        _append_run_log(started_at, ended_at, exit_code, extra_error)


if __name__ == "__main__":
    sys.exit(_run_daily_with_persistent_log())
