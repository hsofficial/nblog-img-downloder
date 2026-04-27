# 네이버 블로그 이미지 다운로더

이 폴더는 아래 두 스크립트를 최소 실행 단위로 분리한 배포본입니다.

- `naver_blog_image_downloader.py`
- `naver_blog_image_downloader_w3840.py`

두 스크립트 모두 `links.txt`에 적힌 네이버 블로그 글 URL들을 읽어 이미지 파일을 저장합니다.

## 포함 파일

- `naver_blog_image_downloader.py`
  - Selenium으로 브라우저를 띄운 뒤, 이미지 뷰어를 직접 열고 네트워크 로그를 분석해서 가장 큰 이미지 후보를 저장합니다.
- `naver_blog_image_downloader_w3840.py`
  - HTML 안의 이미지 정보를 파싱한 뒤, 각 이미지 URL을 `?type=w3840` 형태로 강제 변환해서 다운로드합니다.
- `links.txt`
  - 다운로드할 네이버 블로그 글 주소를 한 줄에 하나씩 넣는 입력 파일입니다.
- `requirements.txt`
  - 파이썬 패키지 의존성 목록입니다.
- `chrome-win64/`
  - `naver_blog_image_downloader.py`가 기본 설정으로 사용하는 로컬 크롬 실행 파일 폴더입니다.

## 필요한 파이썬 패키지

`requirements.txt` 기준:

- `requests`
- `selenium`
- `webdriver-manager`

설치:

```bash
pip install -r requirements.txt
```

## 실행 전 준비

`links.txt` 파일에 네이버 블로그 글 URL을 한 줄씩 넣습니다.

예시:

```text
https://blog.naver.com/example/223123456789
https://m.blog.naver.com/example/223987654321
```

## 스크립트별 동작 방식

### 1. `naver_blog_image_downloader.py`

이 스크립트는 정확도를 우선하는 방식입니다.

1. `links.txt`에서 URL 목록을 읽습니다.
2. 로컬 `chrome-win64/chrome.exe`를 사용해 Selenium 브라우저를 실행합니다.
3. 네이버 블로그 본문 프레임(`mainFrame`)으로 진입합니다.
4. 본문 이미지 썸네일을 하나씩 클릭해 이미지 뷰어를 엽니다.
5. 뷰어가 열리는 동안 발생한 브라우저 네트워크 로그를 수집합니다.
6. 이미지 응답들 중에서 파일 크기, URL 패턴, 원본 파일명 일치 여부를 기준으로 가장 좋은 후보를 고릅니다.
7. 고른 이미지를 `downloads/<블로그ID_글번호>/` 아래에 저장합니다.
8. 저장 결과를 `_manifest.json`과 `crawler.log`에 기록합니다.

이 방식은 실제 뷰어에서 로드된 큰 이미지를 잡아내는 데 유리하지만, Selenium과 브라우저가 필요합니다.

### 2. `naver_blog_image_downloader_w3840.py`

이 스크립트는 단순하고 빠른 방식입니다.

1. `links.txt`에서 URL 목록을 읽습니다.
2. `requests`로 블로그 페이지 HTML을 가져옵니다.
3. 본문이 iframe 구조면 `mainFrame` HTML을 다시 가져옵니다.
4. HTML 안의 `data-linkdata` 값을 파싱해 이미지 원본 URL 목록을 뽑습니다.
5. 각 URL을 `?type=w3840`로 변환합니다.
6. 변환된 이미지를 `downloads_w3840/<블로그ID_글번호>/` 아래에 저장합니다.
7. 저장 결과를 `_manifest.json`과 `crawler_w3840.log`에 기록합니다.

이 방식은 브라우저 없이 실행할 수 있지만, 항상 최적 원본이 잡힌다고 보장되지는 않습니다.

## 실행 방법

### `naver_blog_image_downloader.py`

기본 실행:

```bash
python naver_blog_image_downloader.py
```

주요 옵션:

```bash
python naver_blog_image_downloader.py --headed
python naver_blog_image_downloader.py --timeout 30
python naver_blog_image_downloader.py --links-file links.txt
python naver_blog_image_downloader.py --browser-binary "C:\\Path\\To\\chrome.exe"
```

- `--headed`
  - 브라우저 창을 보이게 실행합니다.
- `--timeout`
  - 각 페이지/뷰어 대기 시간을 조정합니다.
- `--browser-binary`
  - 기본 포함된 `chrome-win64` 대신 다른 크롬 실행 파일을 지정합니다.

### `naver_blog_image_downloader_w3840.py`

```bash
python naver_blog_image_downloader_w3840.py
python naver_blog_image_downloader_w3840.py --links-file links.txt
```

## 생성되는 출력물

- `downloads/`
  - Selenium 버전 스크립트의 이미지 저장 폴더
- `downloads_w3840/`
  - `w3840` 스크립트의 이미지 저장 폴더
- `crawler.log`
  - Selenium 버전 실행 로그
- `crawler_w3840.log`
  - `w3840` 버전 실행 로그
- `_manifest.json`
  - 각 게시글 폴더 안에 생성되는 다운로드 결과 메타데이터

## 꼭 필요한 로컬 파일 정리

### 공통

- `links.txt`

### `naver_blog_image_downloader.py` 전용

- `chrome-win64/chrome.exe`
  - 기본 실행 경로가 이 파일로 고정되어 있습니다.

### 패키지 의존성

- `requests`
- `selenium`
- `webdriver-manager`

## 주의사항

- 네이버 페이지 구조가 바뀌면 선택자나 HTML 파싱 로직 수정이 필요할 수 있습니다.
- `webdriver-manager`는 처음 실행 시 크롬 드라이버를 내려받을 수 있어 인터넷 연결이 필요할 수 있습니다.
- 이미지 URL이나 응답 크기 조건에 따라 일부 이미지는 스킵될 수 있습니다.
