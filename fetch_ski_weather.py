from datetime import datetime, timedelta, timezone
import json
import os
import re
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.shigakogen-ski.or.jp"
INDEX_URL = f"{BASE_URL}/lift/index.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# 1. 四張代表雪場卡片：直接對應官網各雪場專屬即時頁面 (確保 100% 抓到正確數據)
SPOT_PAGES = {
    "yakebitai": f"{BASE_URL}/lift/yakebitaiyama/",
    "ichinose": f"{BASE_URL}/lift/ichinosefamily/",
    "hasuike": f"{BASE_URL}/lift/hasuike/",
    "yokoteyama": f"{BASE_URL}/lift/yokoteyama-shibutoge/",
}

# 2. 全區 18 座雪場子頁面與 1-4 區分類對照
ZONE_RESORTS = {
    "第一區": [
        {"name": "奧志賀高原", "url": f"{BASE_URL}/lift/okushigakogen/"},
        {"name": "燒額山", "url": f"{BASE_URL}/lift/yakebitaiyama/"},
    ],
    "第二區": [
        {"name": "一之瀨家庭", "url": f"{BASE_URL}/lift/ichinosefamily/"},
        {"name": "一之瀨鑽石", "url": f"{BASE_URL}/lift/ichinosediamond/"},
        {"name": "一之瀨山神", "url": f"{BASE_URL}/lift/ichinoseyamanokami/"},
        {"name": "高天原猛獁", "url": f"{BASE_URL}/lift/takamagahara/"},
        {"name": "冷杉森林貂鼠", "url": f"{BASE_URL}/lift/tannenomoriokojo/"},
        {"name": "東館山", "url": f"{BASE_URL}/lift/higashitateyama/"},
        {"name": "寺小屋", "url": f"{BASE_URL}/lift/terakoya/"},
        {"name": "西館山", "url": f"{BASE_URL}/lift/nishitateyma/"},
        {"name": "發哺 BUNA 平", "url": f"{BASE_URL}/lift/hoppobunadaira/"},
        {"name": "巨人 (Giant)", "url": f"{BASE_URL}/lift/giant/"},
    ],
    "第三區": [
        {"name": "蓮池", "url": f"{BASE_URL}/lift/hasuike/"},
        {"name": "丸池", "url": f"{BASE_URL}/lift/maruike/"},
        {"name": "太陽谷", "url": f"{BASE_URL}/lift/sunvalley/"},
    ],
    "第四區": [
        {"name": "熊之湯", "url": f"{BASE_URL}/lift/kumanoyu/"},
        {"name": "橫手山・涉峠", "url": f"{BASE_URL}/lift/yokoteyama-shibutoge/"},
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


def parse_spot_page(url):
    """直接解析單一雪場頁面的 天気/気温/風速/積雪 與 纜車列表"""
    info = {
        "icon": "--",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",
        "lifts": [],
        "night_open": False,
    }
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            return info

        soup = BeautifulSoup(resp.text, "html.parser")
        # 移除導覽列與頁尾干擾，只看主內容區
        for tag in soup.find_all(["nav", "header", "footer"]):
            tag.decompose()

        text = soup.get_text(" ", strip=True)

        # 1. 解析 天気 (支援: 天気 weather 晴れ)
        m_weather = re.search(r"天気\s*(?:weather)?\s*([^\s\d]+)", text, re.I)
        if m_weather:
            info["icon"] = map_weather_icon(m_weather.group(1))

        # 2. 解析 気温 (支援: 気温 temperature -5°C 或 -5℃)
        m_temp = re.search(
            r"気温\s*(?:temperature)?\s*([\-−+]?\d+(?:\.\d+)?)\s*[℃°C]?",
            text,
            re.I,
        )
        if m_temp:
            val = m_temp.group(1).replace("−", "-")
            info["temp"] = f"{val}°C"

        # 3. 解析 風速 (支援: 風速 wind 1m/s 或 無風)
        m_wind = re.search(
            r"風速\s*(?:wind)?\s*(\d+(?:\.\d+)?)\s*m/s", text, re.I
        )
        if m_wind:
            info["wind"] = f"➤ {m_wind.group(1)} m/s"
        elif "無風" in text:
            info["wind"] = "0 m/s"

        # 4. 解析 積雪 (支援: 積雪 snowfall 15cm)
        m_snow = re.search(
            r"積雪\s*(?:snowfall)?\s*(\d+)\s*cm", text, re.I
        )
        if m_snow:
            info["snow"] = f"{m_snow.group(1)} cm"

        # 5. 解析夜滑狀態
        if "ナイター" in text and any(
            ok in text for ok in ["運行中", "営業中", "○"]
        ):
            if not any(
                no in text
                for no in ["ナイター営業なし", "ナイター運休", "ナイター休止"]
            ):
                info["night_open"] = True

        # 6. 解析纜車運行表格 (<tr>)
        for tr in soup.find_all("tr"):
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
                    info["lifts"].append({
                        "lift": lift_name,
                        "status": normalize_lift_status(raw_status),
                    })

    except Exception as e:
        print(f"抓取 {url} 異常: {e}")

    return info


def fetch_all():
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

    open_night_spots = []
    cache_pages = {}

    # 依第一區～第四區逐一抓取各雪場頁面並快取
    for zone_name, resorts in ZONE_RESORTS.items():
        for resort in resorts:
            url = resort["url"]
            page_data = parse_spot_page(url)
            cache_pages[url] = page_data

            if page_data["night_open"] and resort["name"] not in open_night_spots:
                open_night_spots.append(resort["name"])

            for lift_item in page_data["lifts"]:
                result["lift_status"][zone_name].append({
                    "resort": resort["name"],
                    "lift": lift_item["lift"],
                    "status": lift_item["status"],
                })

    # 從快取中填入 4 張代表雪場卡片的天氣數據
    for spot_key, url in SPOT_PAGES.items():
        if url in cache_pages:
            p = cache_pages[url]
            result[spot_key] = {
                "icon": p["icon"],
                "temp": p["temp"],
                "wind": p["wind"],
                "snow": p["snow"],
            }

    if open_night_spots:
        result["night_ski"] = open_night_spots

    return result


if __name__ == "__main__":
    final_data = fetch_all()
    with open("ski_weather.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    print("更新完成：", json.dumps(final_data, ensure_ascii=False))
