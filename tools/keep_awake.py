"""공개 데모(Streamlit Community Cloud)를 열고, 잠들어 있으면 깨운다. (GitHub Actions에서 6시간마다 실행)

사용법: python tools/keep_awake.py https://factorylens-1.streamlit.app
"""
import sys

from playwright.sync_api import sync_playwright


def _text(frame) -> str:
    try:
        return frame.content()
    except Exception:  # 다시 그려지는 중인 프레임
        return ""


def main(url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(5_000)
        wake = page.get_by_role("button", name="Yes, get this app back up!")
        if wake.count() > 0:
            print("잠든 상태 → 깨우는 중")
            wake.first.click()
        # 앱 화면이 실제로 그려질 때까지 기다린다 (앱은 iframe 안에서 실행된다)
        for _ in range(24):
            page.wait_for_timeout(5_000)
            if any("FactoryLens" in _text(f) for f in page.frames):
                print("데모 정상")
                browser.close()
                return
        browser.close()
        raise SystemExit("데모가 2분 안에 뜨지 않았습니다")


if __name__ == "__main__":
    main(sys.argv[1])
