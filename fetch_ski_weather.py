import json
import os
import re
import requests
from bs4 import BeautifulSoup

# 志賀高原索道協會官網即時運行頁面
URL = "https://www.shigakogen-ski.or.jp/lift/index.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# 1. 對應前端 4 張代表雪場卡片的鍵值與官網日文關鍵字
WEATHER_SPOTS = {
    "yakebitai": ["焼額山"],
    "ichinose": ["一の瀬ファミリー", "一の瀬"],
    "hasuike": ["蓮池"],
    "yokoteyama": ["横手山"]
}

# 2. 依照網站 1-4 區分類全區 18 座雪場 (用於「當日纜車運行狀況」與「夜滑」掃描)
ZONE_RESORTS = {
    "第一區": [
        {"name": "奧志賀高原", "keywords": ["奥志賀高原"]},
        {"name": "燒額山", "keywords": ["焼額山"]}
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
        {"name": "巨人 (Giant)", "keywords": ["ジャイアント"]}
    ],
    "第三區": [
        {"name": "蓮池", "keywords": ["蓮池"]},
        {"name": "丸池", "keywords": ["丸池"]},
        {"name": "太陽谷", "keywords": ["サンバレー"]}
    ],
    "第四區": [
        {"name": "木戶池", "keywords": ["木戸池"]},
        {"name": "熊之湯", "keywords": ["熊の湯"]},
        {"name": "橫手山・涉峠", "keywords": ["横手山", "渋峠"]}
    ]
}

def map_weather_icon(text):
    """將日文天氣描述或圖示 alt 轉換為對應 Emoji"""
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
    """將官網日文纜車狀態統一標準化，方便前端套用綠/黃/紅燈號"""
    if any(k in raw_text for k in ["運行中", "営業中", "○", "運転中"]):
        return "運行中"
    if any(k in raw_text for k in ["見合わせ", "強風", "天候", "調整中", "準備中"]):
        return "天候暫停"
    if any(k in raw_text for k in ["運休", "終了", "×", "休止"]):
        return "運行休止"
    return raw_text.strip()[:10] if raw_text.strip() else "運行休止"

def fetch_shigakogen_data():
    # 初始化完整輸出結構 (即使非雪季或連線失敗也能保證前端不報錯)
    result = {
        "night_ski": ["無"],
        "yakebitai": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "ichinose": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "hasuike": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "yokoteyama": {"icon": "--", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm"},
        "lift_status": {
            "第一區": [],
            "第二區": [],
            "第三區": [],
            "第四區": []
        }
    }

    try:
        resp = requests.get(URL, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            print(f"官網連線失敗，HTTP 狀態碼：{resp.status_code}")
            return result

        soup = BeautifulSoup(resp.text, "html.parser")
        blocks = soup.find_all(['section', 'article', 'div'])
        open_night_spots = []

        for block in blocks:
            text = block.get_text(" ", strip=True)
            # 過濾過大的外層容器，避免不同雪場資料互相污染
            if len(text) > 2200:
                continue

            # ==========================================
            # A. 擷取 4 張代表天氣卡片的氣象欄位
            # ==========================================
            for key, keywords in WEATHER_SPOTS.items():
                if any(kw in text for kw in keywords) and result[key]["temp"] == "--°C":
                    if "気温" in text or "積雪" in text:
                        # 1. 天氣圖示
                        w_alt = "".join(img["alt"] for img in block.find_all("img", alt=True))
                        result[key]["icon"] = map_weather_icon(w_alt if w_alt else text)

                        # 2. 氣溫 (支援負號與小數點)
                        m_temp = re.search(r'気温[：:\s]*([\-−+]?\d+(?:\.\d+)?)', text)
                        if m_temp:
                            clean_temp = m_temp.group(1).replace("−", "-")
                            result[key]["temp"] = f"{clean_temp}°C"

                        # 3. 風速
                        m_wind = re.search(r'風速[：:\s]*(\d+(?:\.\d+)?)\s*m/s', text)
                        if m_wind:
                            result[key]["wind"] = f"➤ {m_wind.group(1)} m/s"
                        elif "無風" in text:
                            result[key]["wind"] = "0 m/s"

                        # 4. 積雪
                        m_snow = re.search(r'積雪[：:\s]*(\d+)\s*cm', text)
                        if m_snow:
                            result[key]["snow"] = f"{m_snow.group(1)} cm"

            # ==========================================
            # B. 擷取全區 1-4 區夜滑狀態 & 各條纜車運行資訊
            # ==========================================
            for zone_name, resorts in ZONE_RESORTS.items():
                for resort in resorts:
                    if any(kw in text for kw in resort["keywords"]):
                        # 1. 檢查夜滑 (ナイター)
                        if "ナイター" in text and any(ok in text for ok in ["運行中", "営業中", "○"]):
                            if not any(no in text for no in ["ナイター営業なし", "ナイター運休", "ナイター休止"]):
                                if resort["name"] not in open_night_spots:
                                    open_night_spots.append(resort["name"])

                        # 2. 檢查該雪場底下的纜車表格列 (<tr>)
                        for tr in block.find_all("tr"):
                            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                            if len(cells) >= 2:
                                lift_name = cells[0]
                                raw_status = " ".join(cells[1:])
                                # 篩選出纜車名稱列
                                if any(k in lift_name for k in ["リフト", "ゴンドラ", "ペア", "トリプル", "クワッド", "スカイレーター"]):
                                    status_zh = normalize_lift_status(raw_status)
                                    entry = {
                                        "resort": resort["name"],
                                        "lift": lift_name,
                                        "status": status_zh
                                    }
                                    if entry not in result["lift_status"][zone_name]:
                                        result["lift_status"][zone_name].append(entry)

        if open_night_spots:
            result["night_ski"] = open_night_spots

    except Exception as e:
        print(f"解析官網資料發生異常: {e}")

    return result

if __name__ == "__main__":
    final_data = fetch_shigakogen_data()
    with open("ski_weather.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    print("已成功產出 ski_weather.json：", json.dumps(final_data, ensure_ascii=False))
