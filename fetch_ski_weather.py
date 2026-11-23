from datetime import datetime, timedelta, timezone
import json
import os
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://www.shigakogen-ski.or.jp/lift/index.php"

WEATHER_SPOTS = {
    "yakebitai": ["焼額山"],
    "ichinose": ["一の瀬ファミリー", "一の瀬"],
    "hasuike": ["蓮池"],
    "yokoteyama": ["横手山"],
}

ZONE_RESORTS = {
    "第一區": [
        {"name": "奧志賀高原", "keywords": ["奥志賀高原"]},
        {"name": "燒額山", "keywords": ["焼額山"]},
    ],
    "第二區": [
        {"name": "一之瀨家庭", "keywords": ["一の瀬ファミリー"]},
        {"name": "一之瀨鑽石", "keywords": ["一の瀬ダイヤモンド"]},
        {"name": "一之瀨山神", "keywords": ["一の瀬山の神"]},
        {"name": "高天原猛獁", "keywords": ["高天ヶ原"]},
        {"name": "冷杉森林貂鼠", "keywords": ["タンネの森"]},
        {"name": "東館山", "keywords": ["東館山"]},
        {"name": "寺小屋", "keywords": ["寺小屋"]},
        {"name": "西館山", "keywords": ["西館山"]},
        {"name": "發哺 BUNA 平", "keywords": ["発哺ブナ平", "ブナ平"]},
        {"name": "巨人 (Giant)", "keywords": ["ジャイアント"]},
    ],
    "第三區": [
        {"name": "蓮池", "keywords": ["蓮池"]},
        {"name": "丸池", "keywords": ["丸池"]},
        {"name": "太陽谷", "keywords": ["サンバレー"]},
    ],
    "第四區": [
        {"name": "木戶池", "keywords": ["木戸池"]},
        {"name": "熊之湯", "keywords": ["熊の湯"]},
        {"name": "橫手山・涉峠", "keywords": ["横手山", "渋峠"]},
    ],
}


def map_weather_icon(text):
    if "雪" in text:
        return "❄️"
    if "雨" in text:
        return "🌧️"
    if "晴" in text and "曇" in text:
        return "⛅"
    if "晴" in text:
        return "☀️"
    if "曇" in text:
        return "☁️"
    return "🏔️"


def normalize_lift_status(raw_text):
    if any(k in raw_text for k in ["運行中", "営業中", "○", "運転中"]):
        return "運行中"
    if any(k in raw_text for k in ["見合わせ", "強風", "天候", "調整中", "準備中"]):
        return "天候暫停"
    if any(k in raw_text for k in ["運休", "終了", "×", "休止", "期間外"]):
        return "運行休止"
    return raw_text.strip()[:10] if raw_text.strip() else "運行休止"


def fetch_with_browser():
    JST = timezone(timedelta(hours=9))
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    result = {
        "last_checked": now_str,
        "night_ski": ["無"],
        "yakebitai": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "ichinose": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "hasuike": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "yokoteyama": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "lift_status": {
            "第一區": [],
            "第二區": [],
            "第三區": [],
            "第四區": [],
        },
    }

    html_content = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            )
            page.goto(URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)  # 等待前端 JS 完整渲染所有雪場卡片
            html_content = page.content()
            browser.close()
    except Exception as e:
        print(f"瀏覽器開啟網頁異常: {e}")
        return result

    soup = BeautifulSoup(html_content, "html.parser")
    # 移除導覽列與頁尾干擾
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()

    open_night_spots = []
    blocks = soup.find_all(["section", "article", "div"])

    for block in blocks:
        text = block.get_text(" ", strip=True)
        # 鎖定單一雪場區塊的合理文字長度
        if len(text) < 15 or len(text) > 2500:
            continue

        # 1. 解析四大代表卡片氣象 (支援: 天気 weather 晴れ / 気温 temperature -5°C / 積雪 snowfall 15cm)
        for key, keywords in WEATHER_SPOTS.items():
            if any(kw in text for kw in keywords) and result[key]["temp"] == "--°C":
                if "気温" in text or "積雪" in text or "天気" in text:
                    m_w = re.search(r"天気\s*(?:weather)?\s*([^\s\d]+)", text, re.I)
                    if m_w:
                        result[key]["icon"] = map_weather_icon(m_w.group(1))

                    m_temp = re.search(
                        r"気温\s*(?:temperature)?\s*([\-−+]?\d+(?:\.\d+)?)\s*[℃°C]?",
                        text,
                        re.I,
                    )
                    if m_temp:
                        result[key]["temp"] = f"{m_temp.group(1).replace('−', '-')}°C"

                    m_wind = re.search(
                        r"風速\s*(?:wind)?\s*(\d+(?:\.\d+)?)\s*m/s", text, re.I
                    )
                    if m_wind:
                        result[key]["wind"] = f"➤ {m_wind.group(1)} m/s"
                    elif "無風" in text:
                        result[key]["wind"] = "0 m/s"

                    m_snow = re.search(
                        r"積雪\s*(?:snowfall)?\s*(\d+)\s*cm", text, re.I
                    )
                    if m_snow:
                        result[key]["snow"] = f"{m_snow.group(1)} cm"

        # 2. 解析全區 1-4 區夜滑與纜車列表
        for zone_name, resorts in ZONE_RESORTS.items():
            for resort in resorts:
                if any(kw in text for kw in resort["keywords"]):
                    # 夜滑判斷
                    if "ナイター" in text and any(
                        ok in text for ok in ["運行中", "営業中", "○"]
                    ):
                        if not any(
                            no in text
                            for no in ["ナイター営業なし", "ナイター運休", "ナイター休止"]
                        ):
                            if resort["name"] not in open_night_spots:
                                open_night_spots.append(resort["name"])

                    # 纜車列表解析
                    for tr in block.find_all("tr"):
                        cells = [
                            c.get_text(" ", strip=True)
                            for c in tr.find_all(["th", "td"])
                            if c.get_text(strip=True)
                        ]
                        if len(cells) >= 2:
                            lift_name = cells[0]
                            raw_status = " ".join(cells[1:])
                            if any(
                                k in lift_name
                                for k in [
                                    "リフト",
                                    "ゴンドラ",
                                    "ペア",
                                    "トリプル",
                                    "クワッド",
                                    "スカイレーター",
                                ]
                            ):
                                entry = {
                                    "resort": resort["name"],
                                    "lift": lift_name,
                                    "status": normalize_lift_status(raw_status),
                                }
                                if entry not in result["lift_status"][zone_name]:
                                    result["lift_status"][zone_name].append(entry)

    if open_night_spots:
        result["night_ski"] = open_night_spots

    return result


if __name__ == "__main__":
    final_data = fetch_with_browser()
    with open("ski_weather.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    print("瀏覽器渲染擷取完成：", json.dumps(final_data, ensure_ascii=False))
