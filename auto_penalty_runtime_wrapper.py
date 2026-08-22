"""VERA SPA - robust runtime wrapper for Auto Check 20:00.

Khong thay doi Google Sheet ID. Wrapper chi tang do ben cho dang nhap TimeSoft
trong Cloud Run Job va giu nguyen nghiep vu cua auto_penalty_daily_job.py.
"""
from __future__ import annotations

import re
import sys
import time

import timesoft_sync_job as ts


_ORIGINAL_CREATE_SESSION = ts.create_authenticated_session


def _safe_error(exc) -> str:
    """Log loi Playwright nhung khong de lo credential."""
    text = f"{type(exc).__name__}: {exc}"
    for secret in (getattr(ts, "USERNAME", ""), getattr(ts, "PASSWORD", "")):
        secret = str(secret or "")
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1000]


def _robust_login_with_playwright(page, verify_url: str):
    """Dang nhap TimeSoft va xac minh bang page moi de tranh navigation conflict.

    Loi cu xay ra khi click Login dang tao navigation, sau do code goi page.goto()
    tren chinh page do va Playwright nem generic Error/ERR_ABORTED. Page probe moi
    dung chung browser context/cookie nhung khong tranh chap navigation voi page Login.
    """
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
        return False, "Khong nhan dien duoc o tai khoan TimeSoft."

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
        return False, "Khong nhap duoc form TimeSoft: " + _safe_error(exc)

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

    # Click co the nem Error trong luc navigation da thuc su bat dau. Khong ket luan
    # that bai ngay; tiep tuc cho va probe session bang page khac cung context.
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
            # Neu page Login da chuyen thanh trang ung dung thi session da hop le.
            if not page.is_closed():
                current_url = str(page.url or "")
                still_password = ts._visible_input(
                    page,
                    ['input[type="password"]', 'input[name*="password" i]'],
                )
                if "/user/login" not in current_url.lower() and still_password is None:
                    return True, f"login-ok-current-page-attempt-{attempt}"

            # Quan trong: verify bang PAGE MOI, khong page dang xu ly submit Login.
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
            last_error = (
                "TimeSoft van o trang Login"
                + (f": {err}" if err else "")
            )
        except Exception as exc:
            last_error = _safe_error(exc)
            ts._log(
                f"TIMESOFT LOGIN verify attempt {attempt}/3 WARN: {last_error}"
            )
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

    return False, "Khong xac minh duoc session TimeSoft sau 3 lan: " + (last_error or "unknown")


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
            ts._log(
                f"TIMESOFT SESSION attempt {attempt}/3 FAILED: {_safe_error(exc)}"
            )
            if attempt < 3:
                time.sleep(2 * attempt)
    raise RuntimeError(
        "Khong tao duoc session TimeSoft sau 3 lan: " + _safe_error(last_error)
    ) from last_error


# Monkey-patch chi trong process Auto Check nay.
ts._login_with_playwright = _robust_login_with_playwright
ts.create_authenticated_session = _create_authenticated_session_retry

import auto_penalty_daily_job as daily  # noqa: E402


if __name__ == "__main__":
    sys.exit(daily.run_daily())
