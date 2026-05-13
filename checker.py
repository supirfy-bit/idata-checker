#!/usr/bin/env python3
"""
iDATA Italy Visa Appointment Checker
Runs as a one-shot job on GitHub Actions every 10 minutes.
Alerts via WhatsApp (CallMeBot - free).
"""

import os, sys, time, imaplib, email as email_lib
import re, logging, random
from datetime import datetime
from urllib.parse import quote

import requests
import cloudscraper
from bs4 import BeautifulSoup
import ddddocr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Credentials from GitHub Secrets (env vars) ─────────────────────────────────
IDATA_MEMBERSHIP_NO = os.environ["IDATA_MEMBERSHIP_NO"]
IDATA_EMAIL         = os.environ["IDATA_EMAIL"]
IDATA_PASSWORD      = os.environ["IDATA_PASSWORD"]
GMAIL_ADDRESS       = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD  = os.environ["GMAIL_APP_PASSWORD"]
WHATSAPP_PHONE      = os.environ["WHATSAPP_PHONE"]    # e.g. 905301234567
CALLMEBOT_APIKEY    = os.environ["CALLMEBOT_APIKEY"]
OTP_WAIT_SECONDS    = int(os.environ.get("OTP_WAIT_SECONDS", "90"))

BASE_URL = "https://it-tr-appointment.idata.com.tr"

# ── WhatsApp via CallMeBot (free) ──────────────────────────────────────────────
def send_whatsapp(message: str):
    url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={WHATSAPP_PHONE}&text={quote(message)}&apikey={CALLMEBOT_APIKEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        log.info(f"WhatsApp: {r.status_code}")
    except Exception as e:
        log.error(f"WhatsApp error: {e}")

# ── Gmail OTP reader ───────────────────────────────────────────────────────────
def get_email_otp(wait_seconds: int = 90) -> str | None:
    log.info(f"Polling Gmail for OTP (up to {wait_seconds}s)...")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select("inbox")
            _, data = mail.search(None, '(UNSEEN FROM "idata")')
            ids = data[0].split()
            if not ids:
                _, data = mail.search(None, '(UNSEEN SUBJECT "kod")')
                ids = data[0].split()
            if ids:
                _, msg_data = mail.fetch(ids[-1], "(RFC822)")
                msg = email_lib.message_from_bytes(msg_data[0][1])
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
                    mail.store(ids[-1], "+FLAGS", "\\Seen")
                    mail.logout()
                    log.info(f"OTP: {codes[0]}")
                    return codes[0]
            mail.logout()
        except Exception as e:
            log.error(f"IMAP: {e}")
        time.sleep(6)
    return None

# ── CAPTCHA solver ─────────────────────────────────────────────────────────────
def solve_captcha(image_bytes: bytes) -> str:
    ocr = ddddocr.DdddOcr(show_ad=False)
    result = ocr.classification(image_bytes).strip()
    log.info(f"CAPTCHA: '{result}'")
    return result

# ── Session factory ────────────────────────────────────────────────────────────
def make_session():
    # cloudscraper automatically handles Cloudflare challenges (403 bypass)
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    s.headers.update({
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    })
    return s

# ── Login ──────────────────────────────────────────────────────────────────────
def login(session: requests.Session) -> bool:
    for attempt in range(1, 4):
        log.info(f"Login attempt {attempt}/3")
        try:
            r = session.get(BASE_URL + "/", timeout=30)
            if r.status_code != 200:
                log.error(f"HTTP {r.status_code} on login page")
                return False
            soup = BeautifulSoup(r.text, "html.parser")

            # Solve CAPTCHA
            captcha_code = ""
            img = (soup.find("img", src=re.compile(r"captcha|dogrulama|verify", re.I)) or
                   soup.find("img", id=re.compile(r"captcha", re.I)))
            if img:
                src = img.get("src","")
                if src.startswith("/"): src = BASE_URL + src
                captcha_code = solve_captcha(session.get(src, timeout=15).content)

            # Build payload from hidden fields + credentials
            form = soup.find("form")
            payload = {}
            if form:
                for inp in form.find_all("input"):
                    t = inp.get("type","text").lower()
                    n, v = inp.get("name",""), inp.get("value","")
                    if n and t not in ("submit","button","image"):
                        payload[n] = v

            for inp in soup.find_all("input"):
                ph = (inp.get("placeholder") or "").lower()
                n  = inp.get("name","")
                if not n: continue
                if any(k in ph for k in ("üyelik","membership","numara")):
                    payload[n] = IDATA_MEMBERSHIP_NO
                elif any(k in ph for k in ("e-posta","eposta","email","mail")):
                    payload[n] = IDATA_EMAIL
                elif any(k in ph for k in ("şifre","sifre","password")):
                    payload[n] = IDATA_PASSWORD
                elif any(k in ph for k in ("doğrulama","captcha","kod","code")):
                    payload[n] = captcha_code

            # Fallback field names
            for fname, val in {
                "MembershipNo": IDATA_MEMBERSHIP_NO, "membershipNo": IDATA_MEMBERSHIP_NO,
                "Email": IDATA_EMAIL, "email": IDATA_EMAIL,
                "Password": IDATA_PASSWORD, "password": IDATA_PASSWORD,
                "CaptchaInput": captcha_code, "CaptchaCode": captcha_code, "captchaCode": captcha_code,
            }.items():
                if fname not in payload: payload[fname] = val

            action = BASE_URL + "/"
            if form:
                a = form.get("action","/")
                action = (BASE_URL + a) if a.startswith("/") else a

            r2 = session.post(action, data=payload, timeout=30)
            soup2 = BeautifulSoup(r2.text, "html.parser")
            text2 = soup2.get_text().lower()

            if any(k in text2 for k in ("captcha","doğrulama kodu hatalı","yanlış kod")):
                log.warning("Wrong CAPTCHA — retrying")
                time.sleep(2)
                continue

            # Handle OTP step
            otp_input = soup2.find("input", {"placeholder": re.compile(r"kod|code|otp|doğrulama|sms", re.I)})
            if otp_input or "doğrulama kodu" in text2 or "e-posta kodu" in text2:
                otp = get_email_otp(OTP_WAIT_SECONDS)
                if not otp:
                    log.error("No OTP received")
                    return False
                otp_form = soup2.find("form")
                otp_payload = {}
                if otp_form:
                    for inp in otp_form.find_all("input"):
                        t = inp.get("type","text").lower()
                        n, v = inp.get("name",""), inp.get("value","")
                        if n and t not in ("submit","button"): otp_payload[n] = v
                otp_payload[otp_input.get("name","OtpCode") if otp_input else "OtpCode"] = otp
                otp_a = otp_form.get("action","/verify") if otp_form else "/verify"
                otp_a = (BASE_URL + otp_a) if otp_a.startswith("/") else otp_a
                r3 = session.post(otp_a, data=otp_payload, timeout=30)
                soup3 = BeautifulSoup(r3.text, "html.parser")
                text3 = soup3.get_text().lower()
            else:
                soup3, text3 = soup2, text2

            # Dismiss marketing consent popup
            for f in soup3.find_all("form"):
                decline = next((b for b in f.find_all("button")
                                if "onaylamıyorum" in (b.get_text() or "").lower()), None)
                if decline:
                    c_pay = {i.get("name",""): i.get("value","")
                             for i in f.find_all("input") if i.get("name")}
                    if decline.get("name"): c_pay[decline["name"]] = decline.get("value","0")
                    ca = f.get("action","/")
                    session.post((BASE_URL + ca) if ca.startswith("/") else ca,
                                 data=c_pay, timeout=30)
                    time.sleep(1)
                    break

            if any(k in text3 for k in ("çıkış yap","randevu al","randevu düzenle","duyurular","profil")):
                log.info("✅ Login successful")
                return True

            log.error(f"Login attempt {attempt} unclear. Snippet: {text3[:200]}")

        except Exception as e:
            log.exception(f"Login attempt {attempt} error: {e}")
            time.sleep(3)

    return False

# ── Appointment check ──────────────────────────────────────────────────────────
NO_SLOT = [
    "uygun randevu tarihi bulunmamaktadır",
    "randevu açılmamıştır",
    "no appointment available",
]
APPT_PATHS = ["/randevu-al", "/appointment/new", "/appointment", "/randevu"]

def check_appointments(session: requests.Session) -> list[str]:
    page_r = None
    for path in APPT_PATHS:
        r = session.get(BASE_URL + path, timeout=30)
        if r.status_code == 200 and "randevu" in r.text.lower():
            page_r = r
            log.info(f"Appointment page at {path}")
            break
    if not page_r:
        log.error("Appointment page not found")
        return []

    soup = BeautifulSoup(page_r.text, "html.parser")
    form = soup.find("form")
    if not form:
        log.error("No form on appointment page")
        return []

    payload = {i.get("name",""): i.get("value","")
               for i in form.find_all("input", {"type":"hidden"}) if i.get("name")}

    TARGETS = [("muğla","muğla"),("izmir","izmir"),("turistik","turistik"),("standart","standart")]
    matched = {k: False for _,k in TARGETS}
    for sel in form.find_all("select"):
        sname = sel.get("name") or sel.get("id") or ""
        for opt in sel.find_all("option"):
            ot = opt.get_text(strip=True).lower()
            ov = opt.get("value","")
            for kw, key in TARGETS:
                if kw in ot and not matched[key]:
                    payload[sname] = ov
                    matched[key] = True
                    log.info(f"  ✓ {sname} = '{opt.get_text(strip=True)}'")
                    break

    action = form.get("action", APPT_PATHS[0])
    if action.startswith("/"): action = BASE_URL + action
    elif not action.startswith("http"): action = BASE_URL + "/" + action

    r2 = session.post(action, data=payload, timeout=30)
    body = BeautifulSoup(r2.text, "html.parser").get_text()

    if any(p in body.lower() for p in NO_SLOT):
        log.info("No slots available")
        return []

    dates = list(dict.fromkeys(
        re.findall(r'\d{2}[./]\d{2}[./]\d{4}', body) +
        re.findall(r'\d{4}-\d{2}-\d{2}', body)
    ))
    return dates or ["Slot open — book now!"]

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log.info("=== iDATA Checker run started ===")
    session = make_session()

    if not login(session):
        send_whatsapp("⚠️ iDATA checker: Login failed! Check credentials.")
        sys.exit(1)

    time.sleep(2 + random.uniform(0, 2))
    slots = check_appointments(session)

    if slots:
        msg = (
            f"RANDEVU ACILDI! 🎉\n"
            f"Izmir Ofisi - Turistik\n"
            f"Tarih: {', '.join(slots[:5])}\n"
            f"it-tr-appointment.idata.com.tr\n"
            f"{datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        send_whatsapp(msg)
        log.info("WhatsApp alert sent!")
    else:
        log.info("No slots. Next check in ~10 min.")

    log.info("=== Run complete ===")

if __name__ == "__main__":
    main()
