from datetime import datetime, timedelta, timezone
import json
import os
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

WEATHERNEWS_SPOTS = {
    "yakebitai": "https://weathernews.jp/ski/spot/34820/",   # 第一區：燒額山
    "ichinose": "https://weathernews.jp/ski/spot/34818/",    # 第二區：一之瀨
    "hasuike": "https://weathernews.jp/ski/spot/34804/",     # 第三區：蓮池
    "yokoteyama": "https://weathernews.jp/ski/spot/34803/",  # 第四區：橫手山
}

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
    return ""


def parse_weathernews_spot(page, spot_key, url):
    spot_data = {
        "icon": "⛅",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        # 1. 直接從 DOM 中鎖定「天気」標題格右邊的第一個天氣圖示儲存格
        weather_cell_info = page.evaluate("""() => {
            // 尋找文字剛好為「天気」的元素
            const allEls = Array.from(document.querySelectorAll('th, td, div, span, p'));
            const labelEl = allEls.find(el => el.textContent.trim() === '天気' && el.children.length === 0);
            if (!labelEl) return null;

            // 往上找該列容器 (tr 或橫向 flex/grid 列)
            let row = labelEl.closest('tr') || labelEl.parentElement;
            if (!row) return null;

            // 如果 parentElement 只有「天気」兩個字，再往上一層找整列容器
            if (row.textContent.trim() === '天気' && row.parentElement) {
                row = row.parentElement;
            }

            // 取得該列裡的第一個圖片、SVG 或帶有背景色的天氣方塊
            const img = row.querySelector('img');
            const svg = row.querySelector('svg');
            const boxes = Array.from(row.querySelectorAll('div, td, span'));

            let bgColors = [];
            for (const b of boxes) {
                const bg = window.getComputedStyle(b).backgroundColor;
                if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'rgb(255, 255, 255)') {
                    bgColors.push(bg);
                }
            }

            return {
                imgSrc: img ? (img.src || '') : '',
                imgAlt: img ? (img.alt || '') : '',
                svgClass: svg ? (svg.getAttribute('class') || '') : '',
                svgHtml: svg ? svg.outerHTML.slice(0, 300) : '',
                bgColors: bgColors
            };
        }""")

        print(f"[{spot_key}] 天氣格原始偵測: {weather_cell_info}")

        # 2. 取得氣溫、風速、降雪文字
        visible_text = page.inner_text("body")
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]

        start_idx = -1
        for i in range(len(lines) - 1):
            if lines[i] == "降雪" and lines[i + 1] == "cm":
                start_idx = i + 2
                break

        snow_num = 0.0
        if start_idx != -1 and start_idx + 4 < len(lines):
            curr = start_idx
            if "日(" in lines[curr] or "日（" in lines[curr]:
                curr += 1

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
                    snow_num = float(snow_val)

        # 3. 精準判定天氣圖示 (優先比對檔名/alt -> 再比對 Weathernews 方塊底色 -> 最後才用降雪量)
        icon_found = ""
        if weather_cell_info:
            combined = f"{weather_cell_info.get('imgAlt', '')} {weather_cell_info.get('imgSrc', '')} {weather_cell_info.get('svgClass', '')}".lower()
            bgs = " ".join(weather_cell_info.get("bgColors", []))

            # A. 依圖檔代碼或文字判斷 (Weathernews 經典代碼: 100系=晴, 200系=陰, 300系=雨, 400系=雪)
            if any(k in combined for k in ["晴", "sun", "clear", "/100", "_100", "101"]):
                icon_found = "☀️"
            elif any(k in combined for k in ["雪", "snow", "/400", "_400", "401", "402"]):
                icon_found = "❄️"
            elif any(k in combined for k in ["曇", "cloud", "/200", "_200", "201"]):
                icon_found = "☁️"
            elif any(k in combined for k in ["雨", "rain", "/300", "_300"]):
                icon_found = "🌧️"

            # B. 若是純 SVG 無檔名，直接讀取 Weathernews 招牌方塊底色 (橘色=晴天, 灰=陰天, 藍=雪/雨)
            if not icon_found and bgs:
                # 擷取 rgb(R, G, B)
                m_rgb = re.search(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", bgs)
                if m_rgb:
                    r, g, b = int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3))
                    if r > 200 and g > 90 and b < 80:      # 橘色底 = 晴天
                        icon_found = "☀️"
                    elif b > 180 and r < 120:              # 藍色底 = 下雪或下雨
                        icon_found = "❄️" if snow_num > 0 else "🌧️"
                    elif abs(r - g) < 25 and abs(g - b) < 25 and 100 < r < 210:  # 灰色底 = 陰天
                        icon_found = "☁️"

        if icon_found:
            spot_data["icon"] = icon_found
        elif snow_num > 0:
            spot_data["icon"] = "❄️"
        else:
            spot_data["icon"] = "⛅"

    except Exception as e:
        print(f"抓取 {spot_key} ({url}) 異常: {e}")

    print(f"[{spot_key}] 最終結果: {spot_data}")
    return spot_data


def parse_shiga_lifts_and_night(page):
    """完整掃描志賀高原索道協會官網的 夜滑開放 與 1-4區纜車運行狀況"""
    night_ski = ["無"]
    lift_status = {
        "第一區": [],
        "第二區": [],
        "第三區": [],
        "第四區": [],
    }

    try:
        page.goto(SHIGA_LIFT_URL, wait_until="networkidle", timeout=35000)
        page.wait_for_timeout(4000)

        # 捲動頁面確保所有區塊皆已載入
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)

        soup = BeautifulSoup(page.content(), "html.parser")
        open_night_spots = []

        # 不做 decompose() 破壞結構，直接掃描包含雪場關鍵字的區塊
        blocks = soup.find_all(["section", "article", "div"])

        for block in blocks:
            text = block.get_text(" ", strip=True)
            if len(text) < 15 or len(text) > 4500:
                continue

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
                        for row in block.find_all(["div", "li", "tr", "dl"]):
                            row_text = row.get_text(" ", strip=True)
                            if 6 < len(row_text) < 160 and any(
                                st in row_text for st in ["運行中", "運休中", "見合わせ", "準備中"]
                            ):
                                m_lift = re.match(
                                    r"^(?:\d+\s+)?([^\sA-Za-z○×]+)", row_text
                                )
                                if m_lift:
                                    lift_name = m_lift.group(1).strip()
                                    if len(lift_name) >= 2 and not any(
                                        bad in lift_name
                                        for bad in ["運行", "運休", "最終更新", "天気", "気温", "積雪", "メニュー"]
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

    total_lifts = sum(len(v) for v in lift_status.values())
    print(f"總共抓到 {total_lifts} 條纜車資訊")
    return night_ski, lift_status


def main():
    JST = timezone(timedelta(hours=9))
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    result = {
        "last_checked": now_str,
        "night_ski": ["無"],
        "yakebitai": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "ichinose": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "hasuike": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "yokoteyama": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
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
