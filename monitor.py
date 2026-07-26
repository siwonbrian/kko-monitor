"""
카카오 선물하기 상품 재고/가격 모니터링 스크립트

감시 대상 두 가지:
1. "택1" 상품(6415759) - 25종 타이틀 개별 재입고/품절 + 전체 가격
2. "닌텐도" 검색 결과 중 게임타이틀(standardCategory.id==533) 전체 147개
   - 가격 변동 감지
   - 목록에서 사라짐(품절/판매중지 추정) 감지

이전 상태는 state.json 파일에 저장되고, GitHub Actions가 실행될 때마다
이 파일을 커밋해서 다음 실행 때 "이전 값"으로 비교합니다.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

PRODUCT_ID = 6415759
SEARCH_QUERY = "닌텐도"
GAME_TITLE_CATEGORY_ID = 533

PRODUCT_URL = f"https://gift.kakao.com/a/product-detail/v3/products/{PRODUCT_ID}"
OPTIONS_URL = f"https://gift.kakao.com/a/product-detail/v1/products/{PRODUCT_ID}/options"
SEARCH_URL = "https://gift.kakao.com/a/gift-explorer/v1/search/products"

STATE_FILE = "state.json"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://gift.kakao.com/product/{PRODUCT_ID}",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_previous_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_discord_message(webhook_url: str, content: str) -> None:
    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        # 디스코드 전송이 실패해도 전체 스크립트가 죽지 않도록 함
        # (웹훅 URL이 잘못됐거나 만료된 경우 등)
        print(f"경고: 디스코드 전송 실패 ({e.code} {e.reason}). 상태 저장은 계속 진행합니다.", file=sys.stderr)


# ---------------------------------------------------------------------------
# 1. "택1" 상품 - 개별 타이틀 재입고/품절 감시
# ---------------------------------------------------------------------------

def build_pick_one_state() -> dict:
    product = fetch_json(PRODUCT_URL)
    options = fetch_json(OPTIONS_URL)

    option_stock = {}
    for combo in options.get("combinationOptions", []):
        option_stock[str(combo["id"])] = {
            "name": combo["value"],
            "stock": combo["stockQuantity"],
            "unlimited": combo["unlimitedStockQuantity"],
        }

    return {
        "status": product.get("status"),
        "soldOut": product.get("soldOut"),
        "sellingPrice": product.get("price", {}).get("sellingPrice"),
        "basicPrice": product.get("price", {}).get("basicPrice"),
        "discountRate": product.get("price", {}).get("discountRate"),
        "options": option_stock,
    }


def diff_pick_one(old: dict, new: dict) -> list[str]:
    messages = []
    if not old:
        return messages

    if old.get("status") != new.get("status"):
        messages.append(f"📦 [택1상품] 상태 변경: {old.get('status')} → {new.get('status')}")

    if old.get("soldOut") != new.get("soldOut"):
        messages.append(f"🔔 [택1상품] 전체 품절 상태 변경: {old.get('soldOut')} → {new.get('soldOut')}")

    if old.get("sellingPrice") != new.get("sellingPrice"):
        messages.append(
            f"💰 [택1상품] 가격 변동: {old.get('sellingPrice'):,}원 → {new.get('sellingPrice'):,}원"
        )

    old_options = old.get("options", {})
    new_options = new.get("options", {})

    for option_id, new_info in new_options.items():
        old_info = old_options.get(option_id)
        if old_info is None:
            continue

        old_stock = old_info["stock"]
        new_stock = new_info["stock"]

        if old_stock == 0 and new_stock > 0:
            messages.append(f"✅ 재입고! 「{new_info['name']}」 재고 {new_stock}개")
        elif old_stock > 0 and new_stock == 0:
            messages.append(f"❌ 품절됨: 「{new_info['name']}」")

    return messages


# ---------------------------------------------------------------------------
# 2. "닌텐도" 검색 결과 - 게임타이틀 카테고리 전체 가격 감시
# ---------------------------------------------------------------------------

def build_game_titles_state() -> dict:
    """size=600 한 번의 호출로 전체 검색 결과를 받아서, 게임타이틀 카테고리만 필터링."""
    params = urllib.parse.urlencode({"query": SEARCH_QUERY, "page": 0, "size": 600})
    data = fetch_json(f"{SEARCH_URL}?{params}")
    contents = data.get("products", {}).get("contents", [])

    titles = {}
    for p in contents:
        category = p.get("standardCategory", {})
        if category.get("id") != GAME_TITLE_CATEGORY_ID:
            continue
        titles[str(p["id"])] = {
            "name": p.get("name"),
            "sellingPrice": p.get("price", {}).get("sellingPrice"),
        }

    return titles


# 이 비율보다 목록이 급격히 줄어들면, 진짜 품절이 아니라 API 응답 이상으로 간주한다.
# (예: 카카오 서버가 자동화된 요청을 감지해 size=600을 무시하고 일부만 돌려준 경우)
DROP_SUSPICION_THRESHOLD = 0.5
DROP_SUSPICION_MIN_OLD_COUNT = 20


def diff_game_titles(old: dict, new: dict):
    """
    반환값: (알림 메시지 목록, 다음 실행을 위해 저장할 game_titles 상태)
    """
    if not old:
        return [], new  # 첫 실행이면 비교 대상 없이 그대로 저장

    old_count = len(old)
    new_count = len(new)

    suspicious_drop = (
        old_count >= DROP_SUSPICION_MIN_OLD_COUNT
        and new_count < old_count * DROP_SUSPICION_THRESHOLD
    )

    if suspicious_drop:
        # 급감 상황: "사라짐 = 품절" 판정을 걸지 않는다.
        # 대신 겹치는 상품에 대해서만 가격 변동은 확인하고,
        # 다음 실행이 다시 온전한 이전 데이터와 비교할 수 있도록 old 상태를 그대로 유지해서 저장한다.
        messages = [
            f"⚠️ 게임타이틀 목록이 이전 {old_count}개 → 이번 {new_count}개로 급감했습니다. "
            f"카카오 API 응답 이상으로 보여 이번 회차는 품절 판정을 건너뜁니다."
        ]
        for product_id, new_info in new.items():
            old_info = old.get(product_id)
            if old_info and old_info["sellingPrice"] != new_info["sellingPrice"]:
                messages.append(
                    f"💰 [{new_info['name']}] 가격 변동: "
                    f"{old_info['sellingPrice']:,}원 → {new_info['sellingPrice']:,}원"
                )
        return messages, old  # 다음 비교를 위해 이전(온전한) 데이터를 그대로 유지

    messages = []

    # 가격 변동 감지
    for product_id, new_info in new.items():
        old_info = old.get(product_id)
        if old_info is None:
            continue  # 새로 목록에 추가된 상품은 가격 비교 대상 아님
        if old_info["sellingPrice"] != new_info["sellingPrice"]:
            messages.append(
                f"💰 [{new_info['name']}] 가격 변동: "
                f"{old_info['sellingPrice']:,}원 → {new_info['sellingPrice']:,}원"
            )

    # 목록에서 사라짐 (품절/판매중지 추정) 감지
    for product_id, old_info in old.items():
        if product_id not in new:
            messages.append(f"❌ 품절/판매중지 추정: 「{old_info['name']}」 (검색 결과에서 사라짐)")

    return messages, new


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("경고: DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.", file=sys.stderr)

    old_state = load_previous_state()

    old_pick_one = old_state.get("pick_one", {})
    old_game_titles = old_state.get("game_titles", {})

    new_pick_one = build_pick_one_state()
    new_game_titles_raw = build_game_titles_state()

    game_title_messages, game_titles_to_save = diff_game_titles(old_game_titles, new_game_titles_raw)

    messages = []
    messages.extend(diff_pick_one(old_pick_one, new_pick_one))
    messages.extend(game_title_messages)

    if messages:
        product_link = f"https://gift.kakao.com/product/{PRODUCT_ID}"
        content = "**카카오 선물하기 상품 변경 감지**\n" + "\n".join(messages) + f"\n{product_link}"
        print(content)
        if webhook_url:
            send_discord_message(webhook_url, content)
    else:
        print("변경 사항 없음.")

    save_state({"pick_one": new_pick_one, "game_titles": game_titles_to_save})


if __name__ == "__main__":
    main()
