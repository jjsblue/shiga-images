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
    if "雪" in text or "snow" in text.lower():
        return "❄️"
    if "雨" in text or "rain" in text.lower():
        return "🌧️"
    if ("晴" in text and "曇" in text) or "partly" in text.lower():
        return "⛅"
    if "晴" in text or "sun" in text.lower() or "clear" in text.lower():
        return "☀️"
    if "曇" in text or "cloud" in text.lower():
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
        page.wait_for_timeout(4500)

        # 取得畫面上所有可見文字並按行切分
        visible_text = page.inner_text("body")
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]

        # 印出前 80 行實際文字到 GitHub Actions Log，方便精準核對
        print(f"=== [{spot_key}] 畫面文字前 80 行 ===")
        for i, l in enumerate(lines[:80]):
            print(f"  L{i:02d}: {l}")

        # 專門針對 Weathernews 橫向預報表：
        # 標題行通常是 "気温" / "気温(℃)"、"風" / "風(m/s)"、"降雪" / "降雪(cm)"
        # 緊接在標題行後方的第 1~3 行就是第一個時段的純數字！
        def extract_row_first_number(label_prefixes, allow_negative=False):
            num_pat = r"^([\-−+]?\d+(?:\.\d+)?)$" if allow_negative else r"^(\d+(?:\.\d+)?)$"
            inline_pat = (
                r"(?:気温|風|降雪)[^\d\-+]{0,10}([\-−+]?\d+(?:\.\d+)?)"
                if allow_negative
                else r"(?:気温|風|降雪)[^\d]{0,10}(\d+(?:\.\d+)?)"
            )
            for idx, line in enumerate(lines):
                # 排除過長的說明文，只鎖定表格列標題
                if len(line) <= 15 and any(line.startswith(p) for p in label_prefixes):
                    # 情況 A：同一行內有數字 (例如 "降雪 0cm" 或 "気温 -3")
                    m_in = re.search(inline_pat, line)
                    if m_in:
                        return m_in.group(1)
                    # 情況 B：標題在獨立一行，數值在緊接的下一行 (橫向表格最常見)
                    for step in range(1, 4):
                        if idx + step < len(lines):
                            candidate = lines[idx + step].replace("℃", "").replace("cm", "").replace("m/s", "").strip()
                            m_next = re.match(num_pat, candidate)
                            if m_next:
                                return m_next.group(1)
            return None

        # 1. 抓 気温 (允許負數)
        t_val = extract_row_first_number(["気温"], allow_negative=True)
        if t_val is not None:
            spot_data["temp"] = f"{t_val.replace('−', '-')}°C"

        # 2. 抓 風 (不允許負數)
        w_val = extract_row_first_number(["風"], allow_negative=False)
        if w_val is not None:
            spot_data["wind"] = f"➤ {w_val} m/s"

        # 3. 抓 降雪 (不允許負數，避免抓到其他區塊的 25cm)
        s_val = extract_row_first_number(["降雪"], allow_negative=False)
        if s_val is not None:
            spot_data["snow"] = f"{s_val} cm"

        # 4. 抓 天気 圖示 (檢查 "天気" 標題列附近的 img alt 或 src 圖檔編號)
        weather_info = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            for (const img of imgs) {
                const alt = (img.alt || '').trim();
                const src = (img.src || '').toLowerCase();
                if (/^(晴れ|曇り|雪|雨|晴|曇)/.test(alt) && alt.length <= 10) {
                    return alt;
                }
                // Weathernews 天氣小圖示網址通常包含 wx / weather 關鍵字
                if ((src.includes('/wx') || src.includes('weather')) && alt) {
                    return alt;
                }
            }
            return '';
        }""")
        if weather_info:
            spot_data["icon"] = map_weather_icon(weather_info)
        else:
            for idx, line in enumerate(lines):
                if line in ["天気", "天候"] and idx + 1 < len(lines):
                    if any(k in lines[idx + 1] for k in ["晴", "曇", "雪", "雨"]):
                        spot_data["icon"] = map_weather_icon(lines[idx + 1])
                        break

    except Exception as e:
        print(f"抓取 {spot_key} ({url}) 異常: {e}")

    print(f"[{spot_key}] 最終寫入: {spot_data}")
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
