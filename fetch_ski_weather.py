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
    t = text.lower()
    if "雪" in text or "snow" in t or "400" in t or "401" in t or "402" in t:
        return "❄️"
    if "雨" in text or "rain" in t or "300" in t or "301" in t or "302" in t:
        return "🌧️"
    if ("晴" in text and "曇" in text) or "101" in t or "201" in t:
        return "⛅"
    if "晴" in text or "sun" in t or "clear" in t or "100" in t:
        return "☀️"
    if "曇" in text or "cloud" in t or "200" in t:
        return "☁️"
    return "🏔️"


def parse_weathernews_spot(page, spot_key, url):
    spot_data = {
        "icon": "--",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        visible_text = page.inner_text("body")
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]

        # 精準定位預報表格表頭結尾 ("降雪" -> "cm")
        start_idx = -1
        for i in range(len(lines) - 1):
            if lines[i] == "降雪" and lines[i + 1] == "cm":
                start_idx = i + 2
                break

        if start_idx != -1 and start_idx + 4 < len(lines):
            # L22 是日期 (如 "25日(水)")，跳過日期行
            curr = start_idx
            if "日(" in lines[curr] or "日（" in lines[curr]:
                curr += 1

            # 依序讀取：curr = 時(5), curr+1 = 気温(-3), curr+2 = 風(2), curr+3 = 降雪(0.2)
            if curr + 3 < len(lines):
                temp_val = lines[curr + 1].replace("−", "-")
                wind_val = lines[curr + 2]
                snow_val = lines[curr + 3]

                if re.match(r"^[\-+]?\d+(?:\.\d+)?$", temp_val):
                    spot_data["temp"] = f"{temp_val}°C"
                if re.match(r"^\d+(?:\.\d+)?$", wind_val):
                    spot_data["wind"] = f"➤ {wind_val} m/s"
                if re.match(r"^\d+(?:\.\d+)?$", snow_val):
                    spot_data["snow"] = f"{snow_val} cm"

        # 抓取天氣預報表格內第一個天氣圖示 (檢查 img 的 alt 或 src 代碼)
        weather_raw = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            for (const img of imgs) {
                const alt = (img.alt || '').trim();
                const src = (img.src || '');
                // Weathernews 預報圖示通常位於預報列內，且 alt 帶有天氣描述或 src 含有圖示編號
                if (alt && /^(晴れ|曇り|雪|雨|晴|曇|小雪|大雪|みぞれ)/.test(alt)) {
                    return alt;
                }
                if (src.includes('/wx') || src.includes('/icon/')) {
                    return alt + ' ' + src;
                }
            }
            return '';
        }""")
        if weather_raw:
            spot_data["icon"] = map_weather_icon(weather_raw)

    except Exception as e:
        print(f"抓取 {spot_key} ({url}) 異常: {e}")

    print(f"[{spot_key}] 精準擷取結果: {spot_data}")
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

        for spot_key, url in WEATHERNEWS_SPOTS.items():
            result[spot_key] = parse_weathernews_spot(page, spot_key, url)

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
