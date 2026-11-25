from datetime import datetime, timedelta, timezone
import json
import os
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# 1. Weathernews 四大雪場頁面 (負責 4 張天氣卡片的 天気 / 気温 / 風 / 降雪)
WEATHERNEWS_SPOTS = {
    "yakebitai": "https://weathernews.jp/ski/spot/34803/",   # 第一區：燒額山
    "ichinose": "https://weathernews.jp/ski/spot/34820/",    # 第二區：一之瀨
    "hasuike": "https://weathernews.jp/ski/spot/34818/",     # 第三區：蓮池
    "yokoteyama": "https://weathernews.jp/ski/spot/34804/",  # 第四區：橫手山
}

# 2. 志賀高原索道協會官網 (負責 夜滑開放 與 1-4區纜車運行狀況)
SHIGA_LIFT_URL = "https://www.shigakogen-ski.or.jp/lift/index.php"

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


def parse_weathernews_visible_text(page, spot_key, url):
    """所見即所得：直接讀取瀏覽器畫面上渲染出來的純文字，擷取 天気 / 気温 / 風 / 降雪"""
    spot_data = {
        "icon": "--",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)  # 等待畫面文字渲染完成

        # 稍微往下捲動觸發懶載入區塊
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(1000)

        # 直接取得使用者眼睛在畫面上看到的純文字 (依視覺換行切分)
        visible_text = page.inner_text("body")
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
        joined = " | ".join(lines)

        # 輔助函式：在畫面文字行中尋找「標籤」後方緊接的數值/文字
        def find_value_after_label(labels, pattern, max_lookahead=3):
            for idx, line in enumerate(lines):
                if any(lbl == line or line.startswith(lbl) for lbl in labels):
                    # 1. 先檢查同一行 (例如 "気温 -5℃" 或 "降雪 10cm")
                    m_same = re.search(pattern, line)
                    if m_same:
                        return m_same.group(1)
                    # 2. 再往下檢查緊接的 1~3 行 (例如上一行是 "気温"，下一行是 "-5℃")
                    for offset in range(1, max_lookahead + 1):
                        if idx + offset < len(lines):
                            m_next = re.search(pattern, lines[idx + offset])
                            if m_next:
                                return m_next.group(1)
            return None

        # 1. 抓取「天気」
        weather_str = find_value_after_label(
            ["天気", "天候"],
            r"(晴れ|曇り|雪|雨|晴時々曇|曇時々晴|曇時々雪|雪時々曇|晴のち曇|曇のち雪|小雪|大雪|吹雪)",
        )
        if not weather_str:
            # 備援：從頁面上天氣圖示的 alt 屬性取得
            weather_str = page.evaluate("""() => {
                const imgs = Array.from(document.querySelectorAll('img[alt]'));
                for (const img of imgs) {
                    const alt = img.alt.trim();
                    if (/^(晴れ|曇り|雪|雨|晴|曇)/.test(alt) && alt.length <= 8) return alt;
                }
                return null;
            }""")
        if weather_str:
            spot_data["icon"] = map_weather_icon(weather_str)

        # 2. 抓取「気温」
        temp_val = find_value_after_label(
            ["気温"],
            r"([\-−+]?\d+(?:\.\d+)?)\s*[℃°C]?",
        )
        if not temp_val:
            # 備援：找畫面上第一個出現的 ℃ 數值
            m_t = re.search(r"([\-−+]?\d+(?:\.\d+)?)\s*℃", joined)
            if m_t:
                temp_val = m_t.group(1)
        if temp_val:
            spot_data["temp"] = f"{temp_val.replace('−', '-')}°C"

        # 3. 抓取「風」 (填入風速欄位)
        wind_val = find_value_after_label(
            ["風", "風速", "風向・風速"],
            r"(\d+(?:\.\d+)?)\s*(?:m/s|m)?",
        )
        if not wind_val:
            m_w = re.search(r"(\d+(?:\.\d+)?)\s*m/s", joined)
            if m_w:
                wind_val = m_w.group(1)
        if wind_val:
            spot_data["wind"] = f"➤ {wind_val} m/s"

        # 4. 抓取「降雪」 (填入積雪那欄)
        snow_val = find_value_after_label(
            ["降雪", "降雪量", "積雪", "積雪深"],
            r"(\d+)\s*(?:cm)?",
        )
        if not snow_val:
            m_s = re.search(r"(?:降雪|積雪)[^0-9]{0,10}(\d+)\s*cm", joined)
            if m_s:
                snow_val = m_s.group(1)
        if snow_val:
            spot_data["snow"] = f"{snow_val} cm"

        print(f"[{spot_key}] 視覺文字擷取結果: {spot_data}")

    except Exception as e:
        print(f"抓取 {spot_key} ({url}) 異常: {e}")

    return spot_data


def parse_shiga_lifts_and_night(page):
    """解析志賀高原索道協會官網的 夜滑開放 與 1-4區纜車運行狀況"""
    night_ski = ["無"]
    lift_status = {
        "第一區": [],
        "第二區": [],
        "第三區": [],
        "第四區": [],
    }

    try:
        page.goto(SHIGA_LIFT_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3500)
        soup = BeautifulSoup(page.content(), "html.parser")

        for tag in soup.find_all(["nav", "header", "footer"]):
            tag.decompose()

        open_night_spots = []
        blocks = soup.find_all(["section", "article", "div"])

        for block in blocks:
            text = block.get_text(" ", strip=True)
            if len(text) < 15 or len(text) > 3500:
                continue

            for zone_name, resorts in ZONE_RESORTS.items():
                for resort in resorts:
                    if any(kw in text for kw in resort["keywords"]):
                        if "ナイター" in text and any(
                            ok in text for ok in ["運行中", "営業中", "○"]
                        ):
                            if not any(
                                no in text
                                for no in ["ナイター営業なし", "ナイター運休", "ナイター休止"]
                            ):
                                if resort["name"] not in open_night_spots:
                                    open_night_spots.append(resort["name"])

                        for row in block.find_all(["div", "li", "tr", "dl"]):
                            row_text = row.get_text(" ", strip=True)
                            if 6 < len(row_text) < 140 and any(
                                st in row_text for st in ["運行中", "運休中", "見合わせ", "準備中"]
                            ):
                                m_lift = re.match(
                                    r"^(?:\d+\s+)?([^\sA-Za-z○×]+)", row_text
                                )
                                if m_lift:
                                    lift_name = m_lift.group(1).strip()
                                    if len(lift_name) >= 2 and not any(
                                        bad in lift_name
                                        for bad in ["運行", "運休", "最終更新", "天気", "気温", "積雪"]
                                    ):
                                        if "運行中" in row_text:
                                            status_zh = "運行中"
                                        elif "見合わせ" in row_text or "準備中" in row_text:
                                            status_zh = "天候暫停"
                                        else:
                                            status_zh = "運行休止"

                                        entry = {
                                            "resort": resort["name"],
                                            "lift": lift_name,
                                            "status": status_zh,
                                        }
                                        if entry not in lift_status[zone_name]:
                                            lift_status[zone_name].append(entry)

        if open_night_spots:
            night_ski = open_night_spots

    except Exception as e:
        print(f"抓取志賀高原纜車官網異常: {e}")

    return night_ski, lift_status


def main():
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        # 1. 依序讀取 4 個 Weathernews 畫面的 天気 / 気温 / 風 / 降雪
        for spot_key, url in WEATHERNEWS_SPOTS.items():
            result[spot_key] = parse_weathernews_visible_text(page, spot_key, url)

        # 2. 讀取索道協會官網的夜滑與 1-4 區纜車狀態
        night_ski, lift_status = parse_shiga_lifts_and_night(page)
        result["night_ski"] = night_ski
        result["lift_status"] = lift_status

        browser.close()

    with open("ski_weather.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


if __name__ == "__main__":
    main()
