from datetime import datetime, timedelta, timezone
import json
import os
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# 1. Weathernews 四大雪場頁面 (負責 4 張天氣卡片的 天氣/氣溫/風速/積雪)
WEATHERNEWS_SPOTS = {
    "yakebitai": "https://weathernews.jp/ski/spot/34820/",   # 第一區：燒額山
    "ichinose": "https://weathernews.jp/ski/spot/34818/",    # 第二區：一之瀨
    "hasuike": "https://weathernews.jp/ski/spot/34804/",     # 第三區：蓮池
    "yokoteyama": "https://weathernews.jp/ski/spot/34803/",  # 第四區：橫手山
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


def parse_weathernews_page(page, spot_key, url):
    """針對 Weathernews 滑雪場頁面精準擷取 天氣、氣溫、風速、積雪"""
    spot_data = {
        "icon": "--",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",
    }
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # 優先檢查 Weathernews 頁面是否含有 __NEXT_DATA__ JSON (結構化最準確)
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if next_data_tag and next_data_tag.string:
            raw_json = next_data_tag.string
            # 從 JSON 結構中提取積雪、氣溫、風速
            m_j_snow = re.search(r'"snow(?:Depth|fall)?"\s*:\s*"?(\d+)"?', raw_json, re.I)
            m_j_temp = re.search(r'"temp(?:erature)?"\s*:\s*"?([\-−+]?\d+(?:\.\d+)?)"?', raw_json, re.I)
            m_j_wind = re.search(r'"wind(?:Speed)?"\s*:\s*"?(\d+(?:\.\d+)?)"?', raw_json, re.I)
            if m_j_snow:
                spot_data["snow"] = f"{m_j_snow.group(1)} cm"
            if m_j_temp:
                spot_data["temp"] = f"{m_j_temp.group(1).replace('−', '-')}°C"
            if m_j_wind:
                spot_data["wind"] = f"➤ {m_j_wind.group(1)} m/s"

        # 移除頁首頁尾與腳本，從渲染後的可見文字擷取主卡片資訊
        for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
            tag.decompose()

        text = soup.get_text(" ", strip=True)
        print(f"[{spot_key}] 頁面擷取前300字: {text[:300]}")

        # 1. 積雪 (例如: 積雪 15cm / 積雪量 15cm / 積雪深 15cm)
        if spot_data["snow"] == "-- cm":
            m_snow = re.search(r"積雪(?:量|深)?\s*[：:\s]*(\d+)\s*cm", text)
            if m_snow:
                spot_data["snow"] = f"{m_snow.group(1)} cm"

        # 2. 氣溫 (抓主區域出現的攝氏溫度，例如 -5℃ 或 2.5℃)
        if spot_data["temp"] == "--°C":
            m_temp = re.search(r"([\-−+]?\d+(?:\.\d+)?)\s*℃", text)
            if m_temp:
                val = m_temp.group(1).replace("−", "-")
                spot_data["temp"] = f"{val}°C"

        # 3. 風速 (Weathernews 通常直接顯示 "3m/s" 或 "3.5m/s" 或 "風 3m/s"，不一定寫"風速"二字)
        if spot_data["wind"] == "-- m/s":
            m_wind = re.search(r"(\d+(?:\.\d+)?)\s*m/s", text)
            if m_wind:
                spot_data["wind"] = f"➤ {m_wind.group(1)} m/s"

        # 4. 天氣圖示 (比對主區塊內的 img alt 或關鍵字)
        weather_alt = ""
        for img in soup.find_all("img", alt=True):
            alt = img["alt"].strip()
            if any(k in alt for k in ["晴", "曇", "雪", "雨"]) and len(alt) <= 10:
                weather_alt = alt
                break

        if weather_alt:
            spot_data["icon"] = map_weather_icon(weather_alt)
        else:
            # 從主標題後方的文字找天氣狀態詞
            m_w = re.search(r"(?:天気|予報)\s*([^\s\d]{1,8})", text)
            if m_w:
                spot_data["icon"] = map_weather_icon(m_w.group(1))
            elif any(k in text[:400] for k in ["雪", "晴", "曇", "雨"]):
                spot_data["icon"] = map_weather_icon(text[:400])

    except Exception as e:
        print(f"抓取 Weathernews ({url}) 異常: {e}")

    print(f"[{spot_key}] 最終解析結果: {spot_data}")
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

        # 1. 依序抓取 4 個 Weathernews 雪場頁面
        for spot_key, url in WEATHERNEWS_SPOTS.items():
            result[spot_key] = parse_weathernews_page(page, spot_key, url)

        # 2. 抓取索道協會官網的夜滑與 1-4 區纜車狀態
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
