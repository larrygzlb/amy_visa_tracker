#!/usr/bin/env python3
"""
Fully Automatic Belgium Visa Tracker
- VFS Global + DOFI/Infovisa
- Uses 2Captcha to solve image CAPTCHA
"""

import os
import time
import base64
import requests
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# ====================== CONFIG ======================
# All personal data and keys come from environment variables, so the code can be shared
# (e.g. on GitHub) without them. Locally they are read from .env (see .env.example);
# on GitHub Actions from the repository secrets.
def load_dotenv(path: Path = Path(__file__).parent / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


def env_list(name: str) -> list:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


API_KEY_2CAPTCHA = os.environ.get("API_KEY_2CAPTCHA", "")

# WxPusher SPTs (simple push tokens, max 10, comma-separated): every one gets a notification
# after every check. Leave empty to turn notifications off.
WXPUSHER_SPTS    = env_list("WXPUSHER_SPTS")

# Full tracking URL including the "?q=..." part: open VFS passport tracking in your
# browser and copy the address bar. Without "?q=" VFS redirects to "Invalid Request".
VFS_URL           = os.environ.get("VFS_URL", "")

VFS_REFERENCE     = os.environ.get("VFS_REFERENCE", "")
LAST_NAME         = os.environ.get("LAST_NAME", "")
EMBASSY           = os.environ.get("EMBASSY", "Beijing (Pékin)")

# Infovisa inputs to try, as comma-separated "field:value". Field is "visumnr" (visa
# application number) or "refnum" (reference number). Each one is searched separately.
INFOVISA_QUERIES = [tuple(q.split(":", 1)) for q in env_list("INFOVISA_QUERIES")]
# ====================================================


def solve_2captcha(task: dict, first_wait: int = 5) -> str:
    """Submit a task to 2Captcha and poll until it returns the solution"""
    result = requests.post(
        "https://2captcha.com/in.php",
        data={"key": API_KEY_2CAPTCHA, "json": 1, **task},
        timeout=30,
    ).json()

    if result.get("status") != 1:
        raise Exception(f"2Captcha upload failed: {result}")

    captcha_id = result["request"]
    print(f"→ CAPTCHA ID: {captcha_id}. Waiting for solution...")
    time.sleep(first_wait)

    # Poll for result
    for attempt in range(36):  # max ~3 minutes
        res = requests.get(
            "https://2captcha.com/res.php",
            params={
                "key": API_KEY_2CAPTCHA,
                "action": "get",
                "id": captcha_id,
                "json": 1,
            },
            timeout=30,
        ).json()

        if res.get("status") == 1:
            print("→ CAPTCHA solved")
            return res["request"].strip()

        if res.get("request") != "CAPCHA_NOT_READY":
            raise Exception(f"2Captcha error: {res}")
        time.sleep(5)

    raise Exception("Timeout waiting for CAPTCHA solution from 2Captcha")


def solve_recaptcha(page) -> bool:
    """Solve a reCAPTCHA v2 on the page via 2Captcha. Returns False if there is none."""
    site_key = page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]');
        if (el) return el.getAttribute('data-sitekey');
        const frame = document.querySelector('iframe[src*="recaptcha"][src*="k="]');
        return frame ? new URL(frame.src).searchParams.get('k') : null;
    }""")
    if not site_key:
        return False

    print("→ Sending reCAPTCHA to 2Captcha (usually 20-60 s)...")
    token = solve_2captcha(
        {"method": "userrecaptcha", "googlekey": site_key, "pageurl": page.url},
        first_wait=15,
    )

    # Put the token where the form reads it, and fire the widget callback if it has one
    page.evaluate("""(token) => {
        document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(t => {
            t.style.display = 'block';
            t.value = token;
        });
        const el = document.querySelector('[data-callback]');
        const cb = el && window[el.getAttribute('data-callback')];
        if (typeof cb === 'function') cb(token);
    }""", token)
    return True


def solve_image_captcha(page) -> bool:
    """Solve a 'type the text in the image' CAPTCHA via 2Captcha. Returns False if there is none."""
    captcha_input = page.locator("#CaptchaInputText")
    if captcha_input.count() == 0:
        return False

    # The image just before the input is the refresh icon, so use the CAPTCHA image's own id
    captcha_img = page.locator("#CaptchaImage")
    if captcha_img.count() == 0:
        raise Exception("Found the CAPTCHA input but not the CAPTCHA image")

    print("→ Uploading CAPTCHA image to 2Captcha...")
    image_b64 = base64.b64encode(captcha_img.screenshot()).decode("utf-8")
    text = solve_2captcha({"method": "base64", "body": image_b64, "regsense": 1})
    captcha_input.fill(text)
    return True


def check_vfs(page) -> str:
    print("\n=== Checking VFS Global ===")
    if "?q=" not in VFS_URL:
        raise Exception("VFS_URL is not set: paste the full tracking URL (with '?q=...') at the top of this file")

    # 2Captcha is occasionally wrong, so reload (new CAPTCHA) and retry a few times
    for attempt in range(1, 4):
        page.goto(VFS_URL, wait_until="networkidle", timeout=60000)
        if "InValidRequest" in page.url:
            raise Exception("VFS says 'Invalid Request': the '?q=' link has expired or is incomplete, copy it again")
        if page.locator("#RefNo").count() == 0:
            raise Exception(f"Tracking form not found on {page.url}")

        page.fill("#RefNo", VFS_REFERENCE)
        page.fill("input[name=LastName]", LAST_NAME)

        try:
            if not (solve_recaptcha(page) or solve_image_captcha(page)):
                print("→ No CAPTCHA found, submitting directly")
        except Exception as e:
            print(f"→ Attempt {attempt}: {e}")
            continue

        page.click("#submitButton")

        # Wait for either the result line (repeats the reference number) or the wrong-CAPTCHA message
        try:
            page.wait_for_function(
                """ref => document.body && (document.body.innerText.includes(ref)
                    || document.body.innerText.includes('verification words are incorrect'))""",
                arg=VFS_REFERENCE,
                timeout=45000,
            )
        except PlaywrightTimeout:
            print("→ Result text did not appear within 45 s")
        time.sleep(1)

        content = page.inner_text("body")
        if "verification words are incorrect" not in content:
            break
        print(f"→ Attempt {attempt}: VFS rejected the CAPTCHA answer, retrying")
    else:
        raise Exception("CAPTCHA failed 3 times in a row")

    # Extract status
    for line in content.splitlines():
        line = line.strip()
        if VFS_REFERENCE in line and len(line) > len(VFS_REFERENCE) + 10:
            print(f"→ Status: {line}")
            return line

    if "under process" in content.lower():
        print("→ Status contains 'under process' (full extract below)")
        print(content[:1200])
        return "Under process at Embassy (see details above)"

    print("→ Could not find clear status. Full page text (first 1500 chars):")
    print(content[:1500])
    return content[:300]


def check_infovisa(page) -> str:
    print("\n=== Checking DOFI / Infovisa ===")
    results = []
    for field, value in INFOVISA_QUERIES:
        entry = {"field": field, "value": value, "ok": True}
        try:
            page.goto("https://infovisa.ibz.be/InfovisaFr.aspx", wait_until="networkidle", timeout=60000)
            page.select_option("#DropDownList1", label=EMBASSY)
            page.fill(f"#{field}", value)
            with page.expect_navigation(wait_until="networkidle", timeout=60000):
                page.click("#search")

            content = page.inner_text("body")
            if "aucun résultat" in content or "ne donne pas de résultats" in content:
                entry["status"] = "No Result"
            else:
                entry["status"] = content[:400].strip()
        except Exception as e:
            entry.update(ok=False, status=f"Error: {e}")

        print(f"→ {field}={value}: {entry['status']}")
        results.append(entry)

    return results


def summarize_infovisa(results: list) -> str:
    """One-line summary: the first real result, otherwise 'No Result'"""
    for r in results:
        if r["ok"] and r["status"] != "No Result":
            return f"[{r['field']}={r['value']}] {r['status']}"
    if not any(r["ok"] for r in results):
        return "Error: every Infovisa search failed"
    return "No Result"


def send_wxpusher(vfs: dict, ibz: dict, checked_at: str):
    """Send the check result to WeChat via WxPusher. Errors are printed, never raised."""
    if not WXPUSHER_SPTS:
        return
    from html import escape

    def line(r):
        color = "#b42318" if not r["ok"] else "#1d2330"
        return f'<span style="color:{color}">{escape(r["status"])}</span>'

    rows = "".join(
        f"<li>{'签证号' if q['field'] == 'visumnr' else '参考号'} {escape(q['value'])}: {line(q)}</li>"
        for q in ibz.get("queries", [])
    )
    content = (
        f"<h3>比利时签证查询结果</h3><p>时间: {checked_at}</p>"
        f"<p><b>VFS:</b> {line(vfs)}</p>"
        f"<p><b>移民局 (IBZ):</b> {line(ibz)}</p><ul>{rows}</ul>"
    )
    vfs_short = "VFS处理中" if "under process" in vfs["status"] else ("VFS出错" if not vfs["ok"] else "VFS有更新")
    ibz_short = "IBZ无结果" if ibz["status"] == "No Result" else ("IBZ出错" if not ibz["ok"] else "IBZ有结果")
    payload = {"content": content, "summary": f"签证: {vfs_short} / {ibz_short}"[:20],
               "contentType": 2, "sptList": WXPUSHER_SPTS}

    # WxPusher's server is sometimes slow to answer, so retry a few times
    for attempt in range(1, 4):
        try:
            res = requests.post(
                "https://wxpusher.zjiecode.com/api/send/message/simple-push",
                json=payload,
                timeout=30,
            ).json()
            if res.get("success"):
                print("→ WxPusher notification sent")
                return
            print(f"→ WxPusher failed: {res}")
            return  # the server answered with an error (e.g. bad SPT); retrying won't help
        except Exception as e:
            print(f"→ WxPusher attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(5)


def new_page(browser):
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return context.new_page()


def run_check() -> dict:
    """Run both checks once in a fresh browser and return the results (used by app.py)"""
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = new_page(browser)
            try:
                results["vfs"] = {"ok": True, "status": check_vfs(page)}
            except Exception as e:
                results["vfs"] = {"ok": False, "status": str(e)}

            queries = check_infovisa(page)
            summary = summarize_infovisa(queries)
            results["ibz"] = {"ok": not summary.startswith("Error"), "status": summary, "queries": queries}
        finally:
            browser.close()
    results["checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    send_wxpusher(results["vfs"], results["ibz"], results["checked_at"])
    return results


def main(loop: bool = False, interval_minutes: int = 360):
    print(f"Starting Visa Tracker at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = new_page(browser)

        try:
            while True:
                print(f"\n{'='*65}")
                print(f"Check started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*65}")

                try:
                    vfs_status = check_vfs(page)
                except Exception as e:
                    print(f"VFS check failed: {e}")
                    vfs_status = f"Error: {e}"

                ibz_results = check_infovisa(page)

                print(f"\n--- Summary ---")
                print(f"VFS : {vfs_status}")
                for r in ibz_results:
                    print(f"IBZ : {r['field']}={r['value']}: {r['status']}")
                checked_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"Time: {checked_at}")

                ibz_summary = summarize_infovisa(ibz_results)
                send_wxpusher(
                    {"ok": not vfs_status.startswith("Error"), "status": vfs_status},
                    {"ok": not ibz_summary.startswith("Error"), "status": ibz_summary, "queries": ibz_results},
                    checked_at,
                )

                if not loop:
                    break

                print(f"\nSleeping {interval_minutes} minutes until next check... (Ctrl+C to stop)")
                time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            browser.close()
            print("Browser closed.")


if __name__ == "__main__":
    # ========== CHOOSE ONE ==========
    main(loop=False)                          # Run once
    # main(loop=True, interval_minutes=360)   # Run every 6 hours