"""
올리브영 재고 / 랭킹 모니터 → 슬랙 알림
MODE=stock : 상품별 재고(옵션별 품절) 체크
MODE=rank  : 랭킹 페이지에서 상품 순위 체크

환경변수
  MODE               (필수) stock 또는 rank
  SLACK_WEBHOOK_URL  (필수) 슬랙 Incoming Webhook 주소
  GOODS_NO           (필수) 상품번호, 쉼표로 여러 개
  RANK_URLS          (선택) 랭킹 페이지, "이름|URL" 을 쉼표로 여러 개. 안 넣으면 전체 랭킹만
  ALWAYS_NOTIFY      (선택) 1이면 변화 없어도 매번 상세 알림
  NOTIFY_NO_CHANGE   (선택) 0이면 '변경점 없음' 알림을 보내지 않음 (기본 1)
"""
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

MODE = os.environ.get("MODE", "stock").strip().lower()
WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
RAW_GOODS = os.environ.get("GOODS_NO", "")
GOODS = list(dict.fromkeys(g.strip() for g in RAW_GOODS.split(",") if g.strip()))
ALWAYS = os.environ.get("ALWAYS_NOTIFY") == "1"
NOTIFY_NO_CHANGE = os.environ.get("NOTIFY_NO_CHANGE", "1") != "0"

DEFAULT_RANK = "전체|https://www.oliveyoung.co.kr/store/main/getBestList.do"
MAIN_URL = "https://www.oliveyoung.co.kr/store/main/main.do"
BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / f"olive_{MODE}_state.json"
STOCK_STATE_FILE = BASE_DIR / "olive_stock_state.json"   # 랭킹 모드에서 상품명 참고용
SLACK_CHUNK = 3000

NAV_TIMEOUT = 25000      # 페이지 하나를 여는 데 기다리는 최대 시간(ms)
TIME_LIMIT = 9 * 60      # 전체 실행 최대 시간(초)
MAX_CONSEC_FAIL = 4      # 연속 실패가 이만큼이면 접속 차단으로 보고 중단
RANK_RETRY = 3           # 랭킹 페이지 최대 시도 횟수
RETRY_429 = 2            # 429가 뜨면 같은 상품을 최대 몇 번 다시 시도할지
WAIT_429 = (20, 40)      # 429가 떴을 때 기다리는 시간 범위(초)
GAP = (1.0, 2.0)         # 평소 상품 사이 대기 시간 범위(초)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR', 'ko', 'en-US']}); window.chrome = {runtime: {}};"
KST = timezone(timedelta(hours=9))

T0 = time.time()
PRICE_RE = re.compile(r"\d[\d,]*\s*원")
NOISE_RE = re.compile(r"^(예약|일시품절|품절|재입고|오늘드림|옵션을 선택|배송|구매수량|총\s)|배송시작|^\d+\s*%|^\(")


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def now_ts():
    return datetime.now(KST).strftime("%m/%d %H:%M")


def load_state(path):
    """상태 파일 형식: {"items": {상품번호: {...}}, "errors": {키: 메시지}}"""
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "items" in data:
            data.setdefault("errors", {})
            return data
    except Exception:
        pass
    return {"items": {}, "errors": {}}


def parse_rank_urls():
    raw = os.environ.get("RANK_URLS") or DEFAULT_RANK
    pages = []
    for item in raw.split(","):
        if not item.strip():
            continue
        if "|" in item:
            name, url = item.split("|", 1)
        else:
            name, url = item, item
        pages.append((name.strip(), url.strip()))
    return pages


def detail_url(goods_no):
    return f"https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo={goods_no}"


def open_page(page, url):
    """페이지를 열고 HTTP 상태코드를 돌려준다. 400 이상이면 예외."""
    resp = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    status = resp.status if resp else None
    if status and status >= 400:
        raise RuntimeError(f"HTTP {status}")
    page.wait_for_timeout(2000)
    return status


# ---------- 슬랙 ----------

def post_slack(text):
    if not WEBHOOK:
        log("[경고] SLACK_WEBHOOK_URL 이 비어 있어 슬랙 대신 로그에 출력합니다.")
        print(text)
        return
    req = urllib.request.Request(WEBHOOK, data=json.dumps({"text": text}).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as res:
        log(f"[슬랙] 전송 완료 ({res.status})")


def send_slack(header, blocks):
    chunks, cur = [], header
    for b in blocks:
        if
