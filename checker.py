#!/usr/bin/env python3
"""
iDATA Italy Visa Appointment Checker - Playwright version
Bypasses Cloudflare using a real browser.
"""
import os, sys, time, imaplib, email as email_lib
import re, logging
from datetime import datetime
from urllib.parse import quote

import requests
import ddddocr
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

IDATA_MEMBERSHIP_NO = os.environ["IDATA_MEMBERSHIP_NO"]
IDATA_EMAIL         = os.environ["IDATA_EMAIL"]
IDATA_PASSWORD      = os.environ["IDATA_PASSWORD"]
GMAIL_ADDRESS       = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD  = os.environ["GMAIL_APP_PASSWORD"]
WHATSAPP_PHONE      = os.environ["WHATSAPP_PHONE"]
CALLMEBOT_APIKEY    = os.environ["CALLMEBOT_APIKEY"]
OTP_WAIT_SECONDS    = int(os.environ.get("OTP_WAIT_SECONDS", "90"))

BASE_URL = "https://it-tr-appointment.idata.com.tr"
NO_SLOT  = "uygun randevu tarihi bulunmamaktadır"

def send_whatsapp(msg):
    url = (f"https://api.callmebot.com/whatsapp.php"
           f"?phone={WHATSAPP_PHONE}&text={quote(msg)}&apikey={CALLMEBOT_APIKEY}")
    try:
        r = requests.get(url, timeout=15)
        log.info(f"WhatsApp: {r.status_code}")
    except Exception as e:
        log.error(f"WhatsApp: {e}")

def get_email_otp(wait_seconds=90):
    deadline = time.time() + wait_seconds
    log.info(f"Polling Gmail up to {wait_seconds}s...")
    while time.time() < deadline:
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            m.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            m.select("inbox")
            for q in ['(UNSEEN FROM "idata")', '(UNSEEN SUBJECT "kod")']:
                _, d = m.search(None, q)
                ids = d[0].split()
                if ids:
                    _, md = m.fetch(ids[-1], "(RFC822)")
                    msg = email_lib.message_from_bytes(md[0][1])
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() in ("text/plain","text/html"):
                                body += part.get_payload(decode=True).decode(
                                    part.get_content_charset() or "utf-8", errors="ignore")
                    else:
                        body = msg.get_payload(decode=True).decode(
                            msg.get_content_charset() or "utf-8", errors="ignore")
                    codes = re.findall(r'\b(\d{4,8})\b', body)
                    if codes:
                        m.store(ids[-1], "+FLAGS", "\\Seen")
                        m.logout()
                        log.info(f"OTP: {codes[0]}")
                        return codes[0]
            m.logout()
        except Exception as e:
            log.error(f"IMAP: {e}")
        time.sleep(6)
    return None

def solve_captcha(img_bytes):
    result = ddddocr.DdddOcr(show_ad=False).classification(img_bytes).strip()
    log.info(f"CAPTCHA: '{result}'")
    return result

def try_fill(page, selectors, value, label):
    for sel in selectors:
        try:
            page.fill(sel, value, timeout=3000)
            log.info(f"Filled {label} via {sel}")
            return True
        except Exception:
            continue
    log.warning(f"Could not fill: {label}")
    return False

def try_click(page, selectors, label):
    for sel in selectors:
        try:
            page.click(sel, timeout=3000)
            log.info(f"Clicked {label} via {sel}")
            return True
        except Exception:
            continue
    log.warning(f"Could not click: {label}")
    return False

def main():
    log.info("=== iDATA Checker started ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"
        ])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="tr-TR", viewport={"width": 1280, "height": 800}
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            log.info("Loading login page (real browser — bypasses Cloudflare)...")
            page.goto(BASE_URL + "/", wait_until="networkidle", timeout=40000)
            time.sleep(3)
            log.info(f"URL: {page.url} | Title: {page.title()}")

            for btn in ["text=Anladım", "text=ONAYLAMIYORUM"]:
                try: page.click(btn, timeout=3000)
                except Exception: pass

            try_fill(page, [
                'input[placeholder*="yelik"]','input[placeholder*="Numara"]',
                'input[name*="ember" i]','input[name*="uyeNo" i]'
            ], IDATA_MEMBERSHIP_NO, "MembershipNo")

            try_fill(page, [
                'input[type="email"]','input[placeholder*="Posta" i]',
                'input[placeholder*="mail" i]','input[name*="mail" i]'
            ], IDATA_EMAIL, "Email")

            try_fill(page, [
                'input[type="password"]','input[placeholder*="ifre" i]'
            ], IDATA_PASSWORD, "Password")

            captcha_code = ""
            for sel in ['img[src*="captcha" i]','img[src*="dogrulama" i]',
                        'img[id*="captcha" i]','.captcha img']:
                try:
                    img_bytes = page.locator(sel).first.screenshot()
                    captcha_code = solve_captcha(img_bytes)
                    break
                except Exception:
                    continue

            if captcha_code:
                try_fill(page, [
                    'input[placeholder*="oğrulama" i]','input[placeholder*="aptcha" i]',
                    'input[placeholder*="Kod" i]','input[name*="aptcha" i]'
                ], captcha_code, "CAPTCHA")

            try_click(page, [
                'button[type="submit"]','button:has-text("Giriş")','input[type="submit"]'
            ], "Login")
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(3)

            pt = page.inner_text("body").lower()
            if any(k in pt for k in ("doğrulama kodu","e-posta kodu","sms kodu")):
                log.info("OTP step.")
                otp = get_email_otp(OTP_WAIT_SECONDS)
                if not otp:
                    send_whatsapp("⚠️ iDATA: No OTP from Gmail!")
                    sys.exit(1)
                try_fill(page, [
                    'input[placeholder*="od" i]','input[placeholder*="otp" i]',
                    'input[name*="otp" i]','input[name*="Otp"]'
                ], otp, "OTP")
                try_click(page, [
                    'button[type="submit"]','button:has-text("Doğrula")',
                    'button:has-text("Giriş")'
                ], "OTP submit")
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(2)

            try: page.click("text=ONAYLAMIYORUM", timeout=4000); time.sleep(1)
            except Exception: pass

            pt = page.inner_text("body").lower()
            if not any(k in pt for k in ("randevu al","çıkış yap","duyurular")):
                log.error(f"Login failed. Snippet: {pt[:300]}")
                send_whatsapp("⚠️ iDATA: Login failed!")
                sys.exit(1)
            log.info("✅ Logged in.")

            try:
                page.click('a:has-text("Randevu Al")', timeout=5000)
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                page.goto(BASE_URL + "/randevu-al", wait_until="networkidle", timeout=15000)
            time.sleep(2)

            for sel_el in page.locator("select").all():
                try:
                    options = sel_el.locator("option").all_text_contents()
                    for target in ["muğla","izmir","turistik","standart"]:
                        match = next((o for o in options if target in o.lower()), None)
                        if match:
                            sel_el.select_option(label=match)
                            log.info(f"Selected: {match}")
                            time.sleep(1.5)
                            break
                except Exception as e:
                    log.warning(f"Dropdown: {e}")

            time.sleep(2)
            body = page.inner_text("body")
            log.info(f"Result: {body[:500]}")

            if NO_SLOT in body.lower():
                log.info("No slots — next check in ~10 min.")
            else:
                dates = list(dict.fromkeys(re.findall(r'\d{2}[./]\d{2}[./]\d{4}', body)))
                slots = dates or ["Slot open!"]
                log.info(f"🎉 SLOTS: {slots}")
                send_whatsapp(
                    f"RANDEVU ACILDI! 🎉\n"
                    f"Izmir Ofisi - Turistik\n"
                    f"Tarih: {', '.join(slots[:5])}\n"
                    f"it-tr-appointment.idata.com.tr\n"
                    f"{datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
        except Exception as e:
            log.exception(f"Error: {e}")
            send_whatsapp(f"⚠️ iDATA error: {str(e)[:200]}")
            sys.exit(1)
        finally:
            browser.close()
    log.info("=== Done ===")

if __name__ == "__main__":
    main()
