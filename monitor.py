"""
카카오 선물하기 상품 재고/가격 모니터링 스크립트

- 상품 전체 상태 (가격, 품절여부)
- 옵션(타이틀)별 재고 상태
두 가지를 확인해서, 이전 실행 결과와 달라진 점이 있으면 Discord로 알림을 보냅니다.

이전 상태는 state.json 파일에 저장되고, GitHub Actions가 실행될 때마다
이 파일을 커밋해서 다음 실행 때 "이전 값"으로 비교합니다.
"""

import json
import os
import sys
import urllib.request

PRODUCT_ID = 6415759

PRODUCT_URL = f"https://gift.kakao.com/a/product-detail/v3/products/{PRODUCT_ID}"
OPTIONS_URL = f"https://gift.kakao.com/a/product-detail/v1/products/{PRODUCT_ID}/options"

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
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def build_current_state() -> dict:
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


def diff_states(old: dict, new: dict) -> list[str]:
    messages = []

    if not old:
        return messages  # 첫 실행이면 비교할 대상이 없으니 알림 없이 상태만 저장

    if old.get("status") != new.get("status"):
        messages.append(f"📦 상품 상태 변경: {old.get('status')} → {new.get('status')}")

    if old.get("soldOut") != new.get("soldOut"):
        messages.append(f"🔔 전체 품절 상태 변경: {old.get('soldOut')} → {new.get('soldOut')}")

    if old.get("sellingPrice") != new.get("sellingPrice"):
        messages.append(
            f"💰 가격 변동: {old.get('sellingPrice'):,}원 → {new.get('sellingPrice'):,}원"
        )

    old_options = old.get("options", {})
    new_options = new.get("options", {})

    for option_id, new_info in new_options.items():
        old_info = old_options.get(option_id)
        if old_info is None:
            continue  # 새로 생긴 옵션은 재입고 알림 대상 아님 (별도 처리 원하면 여기 추가)

        old_stock = old_info["stock"]
        new_stock = new_info["stock"]

        if old_stock == 0 and new_stock > 0:
            messages.append(f"✅ 재입고! 「{new_info['name']}」 재고 {new_stock}개")
        elif old_stock > 0 and new_stock == 0:
            messages.append(f"❌ 품절됨: 「{new_info['name']}」")

    return messages


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("경고: DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.", file=sys.stderr)

    old_state = load_previous_state()
    new_state = build_current_state()

    changes = diff_states(old_state, new_state)

    if changes:
        product_link = f"https://gift.kakao.com/product/{PRODUCT_ID}"
        content = "**카카오 선물하기 상품 변경 감지**\n" + "\n".join(changes) + f"\n{product_link}"
        print(content)
        if webhook_url:
            send_discord_message(webhook_url, content)
    else:
        print("변경 사항 없음.")

    save_state(new_state)


if __name__ == "__main__":
    main()
