import argparse
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests


ROOT = Path(__file__).resolve().parent
LINKS_FILE = ROOT / "links.txt"
DOWNLOAD_ROOT = ROOT / "downloads_w3840"
LOG_FILE = ROOT / "crawler_w3840.log"


def log_line(message: str) -> None:
    print(message)
    with LOG_FILE.open("a", encoding="utf-8") as fp:
        fp.write(message + "\n")


def read_links(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def post_folder_name(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() != "postview.naver":
        return f"{parts[-2]}_{parts[-1]}"

    query = parse_qs(parsed.query)
    return f"{query.get('blogId', ['blog'])[0]}_{query.get('logNo', ['post'])[0]}"


def derive_w3840(url: str) -> str:
    return f"{url.split('?', 1)[0]}?type=w3840"


def infer_extension(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        if path.endswith(ext):
            return ext
    if "png" in content_type:
        return ".png"
    if "gif" in content_type:
        return ".gif"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def extract_dom_images(html: str) -> list[dict]:
    images: list[dict] = []
    seen: set[str] = set()
    for raw in re.findall(r"data-linkdata='([^']+)'", html):
        try:
            data = json.loads(unescape(raw))
        except json.JSONDecodeError:
            continue
        src = data.get("src")
        if not src or src in seen:
            continue
        seen.add(src)
        images.append(
            {
                "src": src,
                "download_url": derive_w3840(src),
                "original_width": int(data.get("originalWidth") or 0),
                "original_height": int(data.get("originalHeight") or 0),
                "expected_file_size": int(data.get("fileSize") or 0),
            }
        )
    return images


def fetch_post_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    text = response.text

    main_frame = re.search(r'<iframe[^>]+id="mainFrame"[^>]+src="([^"]+)"', text)
    if main_frame:
        frame_url = urljoin(response.url, main_frame.group(1))
        frame_response = session.get(frame_url, timeout=30)
        frame_response.raise_for_status()
        return frame_response.text

    return text


def should_skip(actual_size: int, expected_size: int, content_type: str) -> bool:
    if not content_type.startswith("image/"):
        return True
    if actual_size < 10_000:
        return True
    if expected_size and actual_size < max(10_000, expected_size // 50):
        return True
    return False


def download_post(session: requests.Session, url: str) -> int:
    html = fetch_post_html(session, url)
    items = extract_dom_images(html)
    if not items:
        log_line(f"[warn] no DOM images: {url}")
        return 0

    target_dir = DOWNLOAD_ROOT / post_folder_name(url)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    saved = 0

    for index, item in enumerate(items, start=1):
        response = session.get(item["download_url"], timeout=60)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "")
        actual_size = len(content)
        if should_skip(actual_size, item["expected_file_size"], content_type):
            log_line(f"[skip] index={index} bytes={actual_size} url={item['download_url']}")
            continue

        filename = f"{index:03d}_{Path(urlparse(item['src']).path).stem}{infer_extension(item['download_url'], content_type)}"
        destination = target_dir / filename
        destination.write_bytes(content)
        saved += 1
        manifest.append(
            {
                "index": index,
                "filename": filename,
                "source_url": item["src"],
                "download_url": item["download_url"],
                "original_width": item["original_width"],
                "original_height": item["original_height"],
                "expected_file_size": item["expected_file_size"],
                "actual_file_size": actual_size,
                "content_type": content_type,
            }
        )
        log_line(f"[saved] {destination.name} bytes={actual_size}")

    (target_dir / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log_line(f"[done] {url} -> {saved}")
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Naver blog images by forcing the w3840 variant.")
    parser.add_argument("--links-file", type=Path, default=LINKS_FILE)
    args = parser.parse_args()

    if not args.links_file.exists():
        raise FileNotFoundError(f"Links file not found: {args.links_file}")

    LOG_FILE.write_text("", encoding="utf-8")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.56 Safari/537.36",
            "Referer": "https://blog.naver.com/",
        }
    )

    total = 0
    for url in read_links(args.links_file):
        total += download_post(session, url)
    log_line(f"[summary] total_saved={total}")


if __name__ == "__main__":
    main()
