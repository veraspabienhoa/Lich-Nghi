"""Cloud Run Job: đồng bộ TimeSoft -> PostgreSQL mỗi lần được Cloud Scheduler gọi.

Thiết kế:
- Credentials lấy từ environment / Google Secret Manager, không dùng Streamlit.
- Playwright chỉ đăng nhập để lấy cookie session.
- Sau đó gọi trực tiếp 2 API TimeSoft bằng requests.
- Ghi snapshot hôm nay + hôm qua vào vera_dataset_cache qua vera_postgres.write_dataset.
- Ghi timesoft_background_status để app hiển thị trạng thái lần chạy gần nhất.
- Dùng PostgreSQL advisory lock để tránh 2 Job chồng nhau.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import pandas as pd
import requests
from sqlalchemy import text

import vera_postgres as vpg

VN_TZ = timezone(timedelta(hours=7))
BASE_URL = str(os.getenv("TIMESOFT_BASE_URL", "https://vera.timesoft.vn") or "https://vera.timesoft.vn").rstrip("/")
USERNAME = str(os.getenv("TIMESOFT_USERNAME", "") or "").strip()
PASSWORD = str(os.getenv("TIMESOFT_PASSWORD", "") or "")
SYNC_DAYS = max(1, min(7, int(os.getenv("TIMESOFT_SYNC_DAYS", "2") or 2)))
CHECKIN_PAGE_SIZE = max(20, min(500, int(os.getenv("TIMESOFT_CHECKIN_PAGE_SIZE", "100") or 100)))
MAX_CHECKIN_PAGES = 500
LOCK_NAME = "vera-timesoft-background-sync-v82"

REPORT_SUMMARY_PAGE = "/Report/ReportSummaryInvoice/Index"
REPORT_CHECKIN_PAGE = "/Report/ReportEmployeeCheckin/Index"
API_SUMMARY = "/Report/ReportSummaryInvoice/SearchFullText"
API_CHECKIN = "/Report/ReportEmployeeCheckin/SearchElastic"


def _log(msg: str) -> None:
    now = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now} +07] {msg}", flush=True)


def _date_range_text(start_date: date, end_date: date) -> str:
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"


def _visible_input(page, selectors):
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 12)):
                node = loc.nth(i)
                if node.is_visible():
                    return node
        except Exception:
            continue
    return None


def _login_error_text(page) -> str:
    selectors = [
        '.validation-summary-errors', '.field-validation-error', '.alert-danger',
        '.alert-warning', '.error', '.error-message', '.text-danger', '[role="alert"]',
        '.toast-message', '.notifyjs-bootstrap-error',
    ]
    messages = []
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 8)):
                node = loc.nth(i)
                if not node.is_visible():
                    continue
                txt = re.sub(r"\s+", " ", str(node.inner_text() or "")).strip()
                if txt and txt not in messages:
                    messages.append(txt[:200])
        except Exception:
            continue
    return " | ".join(messages[:3])


def _login_with_playwright(page, verify_url: str) -> tuple[bool, str]:
    password_box = _visible_input(page, [
        'input[type="password"]', 'input[name*="password" i]', 'input[id*="password" i]',
        'input[name*="pass" i]', 'input[id*="pass" i]',
    ])
    if password_box is None:
        return True, "session-existing"

    username_box = _visible_input(page, [
        'input[name="UserName"]', 'input[name="Username"]', 'input[name*="username" i]',
        'input[id*="username" i]', 'input[name*="user" i]', 'input[id*="user" i]',
        'input[name*="account" i]', 'input[id*="account" i]', 'input[name*="login" i]',
        'input[id*="login" i]', 'input[type="email"]', 'input[type="text"]',
    ])
    if username_box is None:
        return False, "Không nhận diện được ô tài khoản TimeSoft."

    try:
        username_box.click()
        username_box.fill(USERNAME)
        password_box.click()
        password_box.fill(PASSWORD)
        try:
            password_box.press("Tab")
        except Exception:
            pass
        page.wait_for_timeout(250)
    except Exception as exc:
        return False, f"Không nhập được form TimeSoft: {type(exc).__name__}"

    submit = None
    for selector in [
        'button[type="submit"]', 'input[type="submit"]',
        'input[type="button"][value*="đăng" i]', 'input[type="button"][value*="login" i]',
        'button:has-text("Đăng nhập")', 'button:has-text("Đăng Nhập")', 'button:has-text("Login")',
        'a:has-text("Đăng nhập")', 'a:has-text("Đăng Nhập")', '[onclick*="login" i]', '[id*="login" i]',
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
            submit.click(timeout=8_000)
        else:
            password_box.press("Enter")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        page.wait_for_timeout(1_500)
        page.goto(verify_url, wait_until="domcontentloaded", timeout=35_000)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        page.wait_for_timeout(700)
    except Exception as exc:
        return False, f"Không xác minh được session TimeSoft: {type(exc).__name__}"

    final_url = str(page.url or "")
    still_password = _visible_input(page, ['input[type="password"]', 'input[name*="password" i]'])
    if "/user/login" in final_url.lower() and still_password is not None:
        err = _login_error_text(page)
        return False, "Đăng nhập TimeSoft thất bại" + (f": {err}" if err else ".")
    return True, "login-ok"


def _requests_session_from_browser(cookies, user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        "User-Agent": str(user_agent or "Mozilla/5.0"),
    })
    for cookie in cookies or []:
        name = str(cookie.get("name", "") or "")
        value = str(cookie.get("value", "") or "")
        if not name:
            continue
        kwargs = {}
        domain = str(cookie.get("domain", "") or "").strip()
        path = str(cookie.get("path", "") or "/").strip() or "/"
        if domain:
            kwargs["domain"] = domain
        if path:
            kwargs["path"] = path
        try:
            session.cookies.set(name, value, **kwargs)
        except Exception:
            session.cookies.set(name, value)
    return session


def create_authenticated_session() -> requests.Session:
    if not USERNAME or not PASSWORD:
        raise RuntimeError("Thiếu TIMESOFT_USERNAME/TIMESOFT_PASSWORD trong Secret Manager.")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("Image chưa cài Playwright/Chromium.") from exc

    verify_url = urljoin(BASE_URL + "/", REPORT_SUMMARY_PAGE.lstrip("/"))
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            context = browser.new_context(
                ignore_https_errors=False,
                viewport={"width": 1440, "height": 1000},
                locale="vi-VN",
            )
            page = context.new_page()
            page.goto(verify_url, wait_until="domcontentloaded", timeout=35_000)
            page.wait_for_timeout(500)
            ok, msg = _login_with_playwright(page, verify_url)
            if not ok:
                raise RuntimeError(msg)
            cookies = context.cookies()
            try:
                user_agent = str(page.evaluate("navigator.userAgent") or "Mozilla/5.0")
            except Exception:
                user_agent = "Mozilla/5.0"
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()
    return _requests_session_from_browser(cookies, user_agent)


def post_json(session: requests.Session, api_path: str, referer_path: str, payload: dict, timeout: int = 60) -> dict:
    url = urljoin(BASE_URL + "/", api_path.lstrip("/"))
    referer = urljoin(BASE_URL + "/", referer_path.lstrip("/"))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": referer,
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = session.post(url, json=payload, headers=headers, timeout=timeout, allow_redirects=True)
    if resp.status_code in {401, 403} or "/user/login" in str(resp.url or "").lower():
        raise RuntimeError("Phiên TimeSoft hết hạn trong lúc gọi API.")
    resp.raise_for_status()
    ctype = str(resp.headers.get("content-type", "") or "").lower()
    if "json" not in ctype:
        raise RuntimeError("TimeSoft API không trả JSON.")
    data = resp.json()
    code = str((data or {}).get("ReturnCode", "") or "")
    if code and code != "000000":
        raise RuntimeError(f"TimeSoft ReturnCode={code}: {(data or {}).get('Message', '')}")
    return data or {}


def fetch_summary(session: requests.Session, target_date: date) -> tuple[pd.DataFrame, dict]:
    payload = {
        "objectSearch": {
            "TypeData": 0,
            "TypeInvoice": 1,
            "TachInvoiceSpa": 0,
            "CreateTimeRange": _date_range_text(target_date, target_date),
        },
        "pageSize": 0,
    }
    data = post_json(session, API_SUMMARY, REPORT_SUMMARY_PAGE, payload)
    rows = data.get("Data") or []
    df = pd.json_normalize(rows, sep=".") if isinstance(rows, list) and rows else pd.DataFrame()
    meta_keys = [
        "Total", "TotalMoney", "TotalDiscount", "TotalQuantity", "TotalInvoice",
        "TotalActualRevenu", "TotalPromotion", "TotalPayment", "TotalActualTerm",
        "TotalDebt", "ReportByYear", "Message",
    ]
    meta = {k: data.get(k) for k in meta_keys if k in data}
    return df, meta


def fetch_checkin(session: requests.Session, target_date: date) -> tuple[pd.DataFrame, dict]:
    all_rows = []
    total_expected = None
    for page_index in range(1, MAX_CHECKIN_PAGES + 1):
        payload = {
            "objectSearch": {"CreateDateRange": _date_range_text(target_date, target_date)},
            "pageIndex": page_index,
            "pageSize": CHECKIN_PAGE_SIZE,
        }
        data = post_json(session, API_CHECKIN, REPORT_CHECKIN_PAGE, payload)
        if total_expected is None:
            try:
                total_expected = int(data.get("Total") or 0)
            except Exception:
                total_expected = 0
        page_rows = data.get("Data") or []
        if not isinstance(page_rows, list) or not page_rows:
            break
        all_rows.extend(page_rows)
        if total_expected and len(all_rows) >= total_expected:
            break
    df = pd.json_normalize(all_rows, sep=".") if all_rows else pd.DataFrame()
    return df, {"Total": total_expected if total_expected is not None else len(all_rows)}


def _key(prefix: str, target_date: date) -> str:
    return f"{prefix}_{target_date.strftime('%Y%m%d')}"


def write_snapshot(target_date: date, invoice_df: pd.DataFrame, invoice_meta: dict,
                   checkin_df: pd.DataFrame, checkin_meta: dict) -> None:
    source_version = target_date.isoformat()
    vpg.write_dataset(_key("timesoft_summary_invoice", target_date), invoice_df, ttl_seconds=86400, source_version=source_version)
    vpg.write_dataset(_key("timesoft_summary_totals", target_date), pd.DataFrame([invoice_meta]), ttl_seconds=86400, source_version=source_version)
    vpg.write_dataset(_key("timesoft_employee_checkin", target_date), checkin_df, ttl_seconds=86400, source_version=source_version)

    # Alias tiện đọc cho ngày hiện tại.
    if target_date == datetime.now(VN_TZ).date():
        vpg.write_dataset("timesoft_summary_invoice_today", invoice_df, ttl_seconds=1800, source_version=source_version)
        vpg.write_dataset("timesoft_summary_totals_today", pd.DataFrame([invoice_meta]), ttl_seconds=1800, source_version=source_version)
        vpg.write_dataset("timesoft_employee_checkin_today", checkin_df, ttl_seconds=1800, source_version=source_version)


def write_status(status: str, started_at: datetime, details: list[dict], error: str = "") -> None:
    now = datetime.now(VN_TZ)
    today_detail = next((x for x in details if x.get("date") == now.date().isoformat()), {})
    row = {
        "status": status,
        "synced_at": now.isoformat(),
        "synced_at_vn": now.strftime("%d/%m/%Y %H:%M:%S"),
        "started_at": started_at.isoformat(),
        "duration_seconds": round((now - started_at).total_seconds(), 3),
        "days_synced": len(details),
        "invoice_rows": int(today_detail.get("invoice_rows", 0) or 0),
        "checkin_rows": int(today_detail.get("checkin_rows", 0) or 0),
        "total_money": float(today_detail.get("total_money", 0) or 0),
        "total_discount": float(today_detail.get("total_discount", 0) or 0),
        "total_actual_revenue": float(today_detail.get("total_actual_revenue", 0) or 0),
        "error": str(error or "")[:500],
    }
    vpg.write_dataset("timesoft_background_status", pd.DataFrame([row]), ttl_seconds=1800, source_version=now.isoformat())


def run_sync() -> int:
    started_at = datetime.now(VN_TZ)
    _log(f"Bắt đầu TimeSoft background sync; days={SYNC_DAYS}")
    if not vpg.is_enabled():
        _log("ERROR: PostgreSQL chưa được bật.")
        return 2

    engine = vpg.get_engine()
    lock_conn = engine.connect()
    got_lock = False
    details: list[dict] = []
    try:
        got_lock = bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": LOCK_NAME}).scalar())
        if not got_lock:
            _log("Một lần đồng bộ TimeSoft khác đang chạy; bỏ qua lần này.")
            return 0

        session = create_authenticated_session()
        today = datetime.now(VN_TZ).date()
        dates = [today - timedelta(days=i) for i in range(SYNC_DAYS)]
        for target_date in dates:
            invoice_df, invoice_meta = fetch_summary(session, target_date)
            checkin_df, checkin_meta = fetch_checkin(session, target_date)
            write_snapshot(target_date, invoice_df, invoice_meta, checkin_df, checkin_meta)
            detail = {
                "date": target_date.isoformat(),
                "invoice_rows": len(invoice_df),
                "checkin_rows": len(checkin_df),
                "checkin_total": int(checkin_meta.get("Total") or len(checkin_df)),
                "total_money": float(invoice_meta.get("TotalMoney") or 0),
                "total_discount": float(invoice_meta.get("TotalDiscount") or 0),
                "total_actual_revenue": float(invoice_meta.get("TotalActualRevenu") or 0),
            }
            details.append(detail)
            _log(
                f"Đã đồng bộ {target_date.isoformat()}: invoice_rows={detail['invoice_rows']}; "
                f"checkin_rows={detail['checkin_rows']}"
            )

        write_status("success", started_at, details)
        _log(f"Hoàn tất TimeSoft background sync trong {(datetime.now(VN_TZ)-started_at).total_seconds():.1f}s")
        return 0
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: {exc}"
        _log(f"ERROR: {safe_error}")
        try:
            write_status("error", started_at, details, safe_error)
        except Exception as status_exc:
            _log(f"Không ghi được sync status: {type(status_exc).__name__}")
        return 1
    finally:
        if got_lock:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": LOCK_NAME})
            except Exception:
                pass
        try:
            lock_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(run_sync())
