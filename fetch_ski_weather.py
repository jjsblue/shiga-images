from datetime import datetime, timedelta, timezone
import json
import os
import re
from playwright.sync_api import sync_playwright

WEATHERNEWS_SPOTS = {
    "yakebitai": "https://weathernews.jp/ski/spot/34803/",   # 第一區：燒額山
    "ichinose": "https://weathernews.jp/ski/spot/34820/",    # 第二區：一之瀨
    "hasuike": "https://weathernews.jp/ski/spot/34818/",     # 第三區：蓮池
    "yokoteyama": "https://weathernews.jp/ski/spot/34804/",  # 第四區：橫手山
}

SHIGA_LIFT_URL = "https://www.shigakogen-ski.or.jp/lift/index.php"

ZONE_RESORTS = [
    ("第一區", "奧志賀高原", ["奥志賀高原"]),
    ("第一區", "燒額山", ["焼額山"]),
    ("第二區", "一之瀨家庭", ["一の瀬ファミリー"]),
    ("第二區", "一之瀨鑽石", ["一の瀬ダイヤモンド"]),
    ("第二區", "一之瀨山神", ["一の瀬山の神"]),
    ("第二區", "高天原猛獁", ["高天ヶ原"]),
    ("第二區", "冷杉森林貂鼠", ["タンネの森"]),
    ("第二區", "東館山", ["東館山"]),
    ("第二區", "寺小屋", ["寺小屋"]),
    ("第二區", "西館山", ["西館山"]),
    ("第二區", "發哺 BUNA 平", ["発哺ブナ平", "ブナ平"]),
    ("第二區", "巨人 (Giant)", ["ジャイアント"]),
    ("第三區", "蓮池", ["蓮池"]),
    ("第三區", "丸池", ["丸池"]),
    ("第三區", "太陽谷", ["サンバレー"]),
    ("第四區", "木戶池", ["木戸池"]),
    ("第四區", "熊之湯", ["熊の湯"]),
    ("第四區", "橫手山・涉峠", ["横手山", "渋峠"]),
]


def parse_weathernews_spot(page, spot_key, url):
    """擷取 Weathernews 逐時預報的第一筆天氣圖示、氣溫、風速、降雪"""
    spot_data = {
        "icon": "⛅",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        weather_cell_info = page.evaluate("""() => {
            const allEls = Array.from(document.querySelectorAll('th, td, div, span, p'));
            const labelEl = allEls.find(el => el.textContent.trim() === '天気' && el.children.length === 0);
            if (!labelEl) return null;

            let row = labelEl.closest('tr') || labelEl.parentElement;
            if (row && row.textContent.trim() === '天気' && row.parentElement) {
                row = row.parentElement;
            }
            if (!row) return null;

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
                bgColors: bgColors
            };
        }""")

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

        icon_found = ""
        if weather_cell_info:
            combined = f"{weather_cell_info.get('imgAlt', '')} {weather_cell_info.get('imgSrc', '')} {weather_cell_info.get('svgClass', '')}".lower()
            bgs = " ".join(weather_cell_info.get("bgColors", []))

            if any(k in combined for k in ["晴", "sun", "clear", "/100", "_100", "101"]):
                icon_found = "☀️"
            elif any(k in combined for k in ["雪", "snow", "/400", "_400", "401", "402"]):
                icon_found = "❄️"
            elif any(k in combined for k in ["曇", "cloud", "/200", "_200", "201"]):
                icon_found = "☁️"
            elif any(k in combined for k in ["雨", "rain", "/300", "_300"]):
                icon_found = "🌧️"

            if not icon_found and bgs:
                m_rgb = re.search(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", bgs)
                if m_rgb:
                    r, g, b = int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3))
                    if r > 200 and g > 90 and b < 80:
                        icon_found = "☀️"
                    elif b > 180 and r < 120:
                        icon_found = "❄️" if snow_num > 0 else "🌧️"
                    elif abs(r - g) < 25 and abs(g - b) < 25 and 100 < r < 210:
                        icon_found = "☁️"

        if icon_found:
            spot_data["icon"] = icon_found
        elif snow_num > 0:
            spot_data["icon"] = "❄️"
        else:
            spot_data["icon"] = "⛅"

    except Exception as e:
        print(f"抓取 {spot_key} ({url}) 異常: {e}")

    print(f"[{spot_key}] 天氣結果: {spot_data}")
    return spot_data


def parse_shiga_lifts_and_night(page):
    """使用純視覺文字逐行掃描志賀高原索道協會官網的纜車與夜滑狀態"""
    night_ski = ["無"]
    lift_status = {
        "第一區": [],
        "第二區": [],
        "第三區": [],
        "第四區": [],
    }

    try:
        page.goto(SHIGA_LIFT_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        visible_text = page.inner_text("body")
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]

        current_zone = None
        current_resort = None
        open_night_spots = []

        for idx, line in enumerate(lines):
            if len(line) <= 25 and ("スキー場" in line or "ゲレンデ" in line or any(
                line.startswith(kw) for _, _, kws in ZONE_RESORTS for kw in kws
            )):
                for z_name, r_name, kws in ZONE_RESORTS:
                    if any(kw in line for kw in kws):
                        current_zone = z_name
                        current_resort = r_name
                        break

            if not current_zone or not current_resort:
                continue

            if "ナイター" in line and any(ok in line for ok in ["運行中", "営業中", "○"]):
                if not any(no in line for no in ["営業なし", "運休", "休止"]):
                    if current_resort not in open_night_spots:
                        open_night_spots.append(current_resort)

            window_text = " ".join(lines[idx : min(idx + 3, len(lines))])

            is_lift_line = bool(re.match(r"^\d{1,2}\s+\S+", line)) or any(
                k in line for k in ["リフト", "ゴンドラ", "スカイ", "ペア", "トリプル", "クワッド", "ロマンス", "第1", "第2", "第3", "第4", "第5"]
            )

            if is_lift_line and len(line) <= 45 and any(
                st in window_text for st in ["運行中", "運休中", "見合わせ", "準備中"]
            ):
                clean_name = re.sub(r"^\d{1,2}\s+", "", line)
                clean_name = re.split(r"\s+[A-Za-z○×]", clean_name)[0].strip()

                if len(clean_name) >= 2 and not any(
                    bad in clean_name for bad in ["運行", "運休", "見合わせ", "最終更新", "天気", "気温", "積雪", "スキー場"]
                ):
                    if "運行中" in window_text:
                        status_zh = "運行中"
                    elif "見合わせ" in window_text or "準備中" in window_text:
                        status_zh = "天候暫停"
                    else:
                        status_zh = "運行休止"

                    entry = {
                        "resort": current_resort,
                        "lift": clean_name,
                        "status": status_zh,
                    }
                    if entry not in lift_status[current_zone]:
                        lift_status[current_zone].append(entry)

        if open_night_spots:
            night_ski = open_night_spots

    except Exception as e:
        print(f"抓取志賀高原纜車官網異常: {e}")

    total_lifts = sum(len(v) for v in lift_status.values())
    print(f"纜車掃描完成，總共抓到 {total_lifts} 條纜車資訊！")
    for z, items in lift_status.items():
        print(f"  {z}: {len(items)} 條")

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
