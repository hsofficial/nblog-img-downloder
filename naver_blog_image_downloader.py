import argparse
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


ROOT = Path(__file__).resolve().parent
LINKS_FILE = ROOT / "links.txt"
CHROME_BINARY = ROOT / "chrome-win64" / "chrome.exe"
DOWNLOAD_ROOT = ROOT / "downloads"
WDM_CACHE = ROOT / ".wdm"
LOG_FILE = ROOT / "crawler.log"

IMAGE_LINK_SELECTOR = "a.se-module-image-link[data-linktype='img']"
IMAGE_RESOURCE_SELECTOR = "img.se-image-resource, .se-module-image img"
VIEWER_IMAGE_SELECTOR = ".se-popup img, .PhotoViewer img, .civ__content img"


def read_links(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_line(message: str) -> None:
    stamped = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(stamped)
    with LOG_FILE.open("a", encoding="utf-8") as fp:
        fp.write(stamped + "\n")


def build_options(headless: bool, user_data_dir: Path, binary_path: Path) -> Options:
    options = Options()
    options.binary_location = str(binary_path)
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--remote-debugging-pipe")
    options.add_argument("--window-size=1600,2200")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--force-device-scale-factor=1")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--hide-scrollbars")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return options


def create_driver(headless: bool, driver_version: Optional[str], browser_binary: Optional[Path]) -> webdriver.Chrome:
    WDM_CACHE.mkdir(exist_ok=True)
    os.environ["WDM_LOCAL"] = "1"
    binary_path = browser_binary or CHROME_BINARY
    if not binary_path.exists():
        raise FileNotFoundError(f"Chrome binary not found: {binary_path}")

    if driver_version:
        driver_path = ChromeDriverManager(driver_version=driver_version).install()
    else:
        driver_path = ChromeDriverManager().install()

    service = Service(driver_path)
    user_data_dir = Path(tempfile.mkdtemp(prefix="chrome-profile-", dir=str(ROOT)))
    try:
        log_line(f"trying browser: {binary_path}")
        driver = webdriver.Chrome(service=service, options=build_options(headless, user_data_dir, binary_path))
        driver._codex_user_data_dir = user_data_dir
        driver._codex_binary_path = binary_path
        log_line(f"browser started: {binary_path}")
        return driver
    except Exception as exc:
        shutil.rmtree(user_data_dir, ignore_errors=True)
        error_text = f"{binary_path} -> {type(exc).__name__}: {exc}"
        log_line(f"browser failed: {error_text}")
        raise RuntimeError(f"Unable to start browser.\n{error_text}") from exc


def cleanup_driver_profile(driver: webdriver.Chrome) -> None:
    user_data_dir = getattr(driver, "_codex_user_data_dir", None)
    if user_data_dir:
        shutil.rmtree(user_data_dir, ignore_errors=True)


def switch_to_post_frame(driver: webdriver.Chrome, timeout: int) -> None:
    driver.switch_to.default_content()
    try:
        WebDriverWait(driver, timeout).until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))
    except TimeoutException:
        pass


def wait_for_images(driver: webdriver.Chrome, timeout: int) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, IMAGE_LINK_SELECTOR) or d.find_elements(By.CSS_SELECTOR, IMAGE_RESOURCE_SELECTOR)
    )


def load_post(driver: webdriver.Chrome, url: str, timeout: int) -> None:
    driver.get(url)
    switch_to_post_frame(driver, timeout)
    wait_for_images(driver, timeout)
    time.sleep(1)


def settle_after_click(headless: bool) -> None:
    time.sleep(1.6 if headless else 1.0)


def click_element(driver: webdriver.Chrome, element) -> None:
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def open_viewer_with_retry(driver: webdriver.Chrome, index: int, timeout: int, headless: bool) -> bool:
    for attempt in range(3):
        thumbs = driver.find_elements(By.CSS_SELECTOR, IMAGE_LINK_SELECTOR)
        if index - 1 >= len(thumbs):
            return False

        thumb = thumbs[index - 1]
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", thumb)
        time.sleep(0.5 if headless else 0.3)
        consume_performance_events(driver)
        click_element(driver, thumb)

        try:
            WebDriverWait(driver, timeout).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, VIEWER_IMAGE_SELECTOR)
                or len(consume_performance_events(d)) > 0
            )
            settle_after_click(headless)
            return True
        except TimeoutException:
            close_image_viewer(driver)
            time.sleep(0.8 if headless else 0.4)
    return False


def sanitize_filename(name: str) -> str:
    safe = re.sub(r'[<>:"/\\\\|?*]+', "_", name)
    safe = safe.strip().strip(".")
    return safe or "image"


def derive_w3840(url: str) -> str:
    if not url:
        return url
    return f"{url.split('?', 1)[0]}?type=w3840"


def infer_extension(url: str, content_type: Optional[str]) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        if path.endswith(ext):
            return ext
    if content_type:
        if "png" in content_type:
            return ".png"
        if "gif" in content_type:
            return ".gif"
        if "webp" in content_type:
            return ".webp"
    return ".jpg"


def post_folder_name(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() != "postview.naver":
        return sanitize_filename(f"{parts[-2]}_{parts[-1]}")

    query = parse_qs(parsed.query)
    blog_id = query.get("blogId", ["blog"])[0]
    log_no = query.get("logNo", ["post"])[0]
    return sanitize_filename(f"{blog_id}_{log_no}")


def parse_linkdata(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def image_candidates_from_dom(driver: webdriver.Chrome) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    links = driver.find_elements(By.CSS_SELECTOR, IMAGE_LINK_SELECTOR)

    for index, element in enumerate(links, start=1):
        data = parse_linkdata(element.get_attribute("data-linkdata"))
        if not data:
            continue
        src = data.get("src")
        if not src or src in seen:
            continue
        seen.add(src)
        items.append(
            {
                "index": index,
                "src": src,
                "download_url": src,
                "original_width": data.get("originalWidth"),
                "original_height": data.get("originalHeight"),
                "expected_file_size": int(data.get("fileSize") or 0),
            }
        )

    if items:
        return items

    images = driver.find_elements(By.CSS_SELECTOR, IMAGE_RESOURCE_SELECTOR)
    for index, image in enumerate(images, start=1):
        src = image.get_attribute("data-lazy-src") or image.get_attribute("src")
        if not src:
            continue
        src = src.split("?", 1)[0]
        if src in seen:
            continue
        seen.add(src)
        items.append({"index": index, "src": src, "download_url": src})
    return items


def consume_performance_events(driver: webdriver.Chrome) -> list[dict]:
    events: list[dict] = []
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, json.JSONDecodeError, TypeError):
            continue
        events.append(message)
    return events


def image_url_score(url: str) -> int:
    score = 0
    lowered = url.lower()
    if any(host in lowered for host in ("postfiles.pstatic.net", "blogfiles.pstatic.net", "phinf.pstatic.net")):
        score += 100
    if "w3840" in lowered:
        score += 20
    if "type=w" in lowered:
        score += 10
    if any(token in lowered for token in ("w80_blur", "w966", "w773", "type=s")):
        score -= 30
    return score


def url_filename(url: str) -> str:
    return Path(urlparse(url).path).name.lower()


def build_network_records(events: list[dict]) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for event in events:
        method = event.get("method")
        params = event.get("params", {})
        request_id = params.get("requestId")
        if not request_id:
            continue

        record = records.setdefault(request_id, {})
        if method == "Network.responseReceived":
            response = params.get("response", {})
            record["url"] = response.get("url")
            record["mime_type"] = response.get("mimeType")
            record["resource_type"] = params.get("type")
            try:
                encoded = int(response.get("encodedDataLength") or 0)
            except (TypeError, ValueError):
                encoded = 0
            if encoded:
                record["encoded_size"] = encoded
        elif method == "Network.loadingFinished":
            try:
                record["encoded_size"] = int(params.get("encodedDataLength") or 0)
            except (TypeError, ValueError):
                pass
    return records


def pick_best_network_image(records: dict[str, dict], dom_item: Optional[dict]) -> Optional[dict]:
    candidates: list[dict] = []
    dom_base = ""
    dom_filename = ""
    if dom_item and dom_item.get("src"):
        dom_base = dom_item["src"].split("?", 1)[0]
        dom_filename = url_filename(dom_base)

    for record in records.values():
        url = record.get("url") or ""
        mime_type = (record.get("mime_type") or "").lower()
        resource_type = record.get("resource_type")
        encoded_size = int(record.get("encoded_size") or 0)

        if not url.startswith("http"):
            continue
        if resource_type != "Image" and not mime_type.startswith("image/"):
            continue
        if encoded_size < 10_000:
            continue

        score = image_url_score(url)
        filename_match = False
        if dom_filename and url_filename(url) == dom_filename:
            filename_match = True
            score += 400
        if dom_base and dom_base in url:
            score += 200

        candidates.append(
            {
                "download_url": url,
                "network_size": encoded_size,
                "content_type": mime_type or None,
                "score": score,
                "filename_match": filename_match,
            }
        )

    if not candidates:
        return None

    if dom_filename:
        exact = [item for item in candidates if item["filename_match"]]
        if exact:
            exact.sort(key=lambda item: (item["score"], item["network_size"]), reverse=True)
            return exact[0]

    candidates.sort(key=lambda item: (item["score"], item["network_size"]), reverse=True)
    return candidates[0]


def close_image_viewer(driver: webdriver.Chrome) -> None:
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    time.sleep(0.6)


def collect_by_clicking(driver: webdriver.Chrome, dom_items: list[dict], timeout: int, headless: bool) -> list[dict]:
    items: list[dict] = []
    total = len(driver.find_elements(By.CSS_SELECTOR, IMAGE_LINK_SELECTOR))

    for index in range(1, total + 1):
        dom_item = dom_items[index - 1] if index - 1 < len(dom_items) else {"index": index}
        if not open_viewer_with_retry(driver, index, timeout, headless):
            fallback = dict(dom_item)
            fallback["index"] = index
            fallback["download_url"] = derive_w3840(dom_item.get("src", ""))
            fallback["fallback"] = "dom_w3840_click_failed"
            items.append(fallback)
            print(f"[warn] viewer open failed for index={index}, using DOM w3840 fallback")
            continue

        events = consume_performance_events(driver)
        if headless and not events:
            time.sleep(1.0)
            events = consume_performance_events(driver)
        records = build_network_records(events)
        best = pick_best_network_image(records, dom_item)
        if best:
            item = dict(dom_item)
            item["index"] = index
            item["download_url"] = best["download_url"]
            item["network_size"] = best["network_size"]
            item["network_content_type"] = best["content_type"]
            items.append(item)
            print(
                f"[pick] index={index} bytes={best['network_size']} "
                f"url={best['download_url']}"
            )
        else:
            fallback = dict(dom_item)
            fallback["index"] = index
            fallback["download_url"] = derive_w3840(dom_item.get("src", ""))
            fallback["fallback"] = "dom_w3840"
            items.append(fallback)
            print(f"[warn] no network image selected for index={index}, using DOM w3840 fallback")

        close_image_viewer(driver)

    return items


def session_from_driver(driver: webdriver.Chrome) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": driver.execute_script("return navigator.userAgent;"),
            "Referer": "https://blog.naver.com/",
        }
    )
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"), path=cookie.get("path"))
    return session


def filename_for_item(index: int, url: str, content_type: Optional[str]) -> str:
    parsed_name = Path(urlparse(url).path).name
    stem = sanitize_filename(Path(parsed_name).stem or f"image_{index:03d}")
    ext = infer_extension(url, content_type)
    return f"{index:03d}_{stem}{ext}"


def is_suspicious_download(item: dict, actual_size: int, content_type: Optional[str]) -> bool:
    if not content_type or not content_type.startswith("image/"):
        return True
    if actual_size < 10_000:
        return True

    expected_size = int(item.get("expected_file_size") or 0)
    if expected_size and actual_size < max(10_000, expected_size // 50):
        return True
    network_size = int(item.get("network_size") or 0)
    if network_size and actual_size < max(10_000, network_size // 50):
        return True
    return False


def download_images(session: requests.Session, items: Iterable[dict], target_dir: Path) -> int:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    manifest: list[dict] = []

    for item in items:
        url = item["download_url"]
        response = session.get(url, timeout=60)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type")
        actual_size = len(content)

        if is_suspicious_download(item, actual_size, content_type):
            print(
                f"[warn] suspicious response skipped index={item['index']} "
                f"bytes={actual_size} expected={item.get('expected_file_size', 0)} url={url}"
            )
            continue

        filename = filename_for_item(item["index"], url, content_type)
        destination = target_dir / filename
        destination.write_bytes(content)
        saved += 1
        manifest.append(
            {
                "index": item["index"],
                "filename": destination.name,
                "download_url": url,
                "source_url": item.get("src"),
                "original_width": item.get("original_width"),
                "original_height": item.get("original_height"),
                "expected_file_size": item.get("expected_file_size"),
                "network_size": item.get("network_size"),
                "actual_file_size": actual_size,
                "content_type": content_type,
            }
        )
        print(
            f"[saved] {destination.name} "
            f"bytes={actual_size} expected={item.get('expected_file_size', 0)} <- {url}"
        )

    (target_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return saved


def process_url(driver: webdriver.Chrome, url: str, timeout: int, click_fallback: bool, headless: bool) -> int:
    log_line(f"open {url}")
    load_post(driver, url, timeout)

    dom_items = image_candidates_from_dom(driver)
    items = collect_by_clicking(driver, dom_items, timeout, headless)

    if not items and click_fallback:
        log_line("network selection failed, falling back to DOM candidates")
        items = dom_items

    if not items:
        log_line("no images found")
        return 0

    target_dir = DOWNLOAD_ROOT / post_folder_name(url)
    session = session_from_driver(driver)
    count = download_images(session, items, target_dir)
    log_line(f"done {count} files -> {target_dir}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the largest Naver blog image variant visible from the viewer network.")
    parser.add_argument("--links-file", type=Path, default=LINKS_FILE)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--driver-version", default="147.0.7727.56")
    parser.add_argument("--browser-binary", type=Path, default=None, help="Explicit browser executable path.")
    parser.add_argument("--headed", action="store_true", help="Run with a visible browser window.")
    parser.add_argument(
        "--click-fallback",
        action="store_true",
        help="If network selection fails, fall back to DOM-derived source URLs.",
    )
    args = parser.parse_args()

    if not args.links_file.exists():
        raise FileNotFoundError(f"Links file not found: {args.links_file}")

    urls = read_links(args.links_file)
    if not urls:
        raise ValueError(f"No URLs found in {args.links_file}")

    LOG_FILE.write_text("", encoding="utf-8")
    driver = create_driver(headless=not args.headed, driver_version=args.driver_version, browser_binary=args.browser_binary)
    try:
        total = 0
        for url in urls:
            total += process_url(driver, url, args.timeout, args.click_fallback, headless=not args.headed)
        log_line(f"summary total_saved={total}")
    finally:
        try:
            driver.quit()
        finally:
            cleanup_driver_profile(driver)


if __name__ == "__main__":
    main()
