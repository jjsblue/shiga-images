from datetime import datetime, timedelta, timezone
import json
import math
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

# 嚴格依照指定列表直接對應 1 ~ 4 區
ZONE_RESORTS = [
    # 第一區
    ("第一區", "燒額山", "焼額山スキー場"),
    ("第一區", "奧志賀高原", "奥志賀高原スキー場"),
    # 第二區
    ("第二區", "一之瀨山神", "一の瀬山の神スキー場"),
    ("第二區", "一之瀨鑽石", "一の瀬ダイヤモンドスキー場"),
    ("第二區", "一之瀨家庭", "一の瀬ファミリースキー場"),
    ("第二區", "冷杉森林貂鼠", "タンネの森オコジョスキー場"),
    ("第二區", "高天原猛獁", "高天ヶ原マンモススキー場"),
    ("第二區", "寺小屋", "寺小屋スキー場"),
    ("第二區", "東館山", "東館山スキー場"),
    ("第二區", "發哺 BUNA 平", "発哺ブナ平スキー場"),
    # 第三區
    ("第三區", "西館山", "西館山スキー場"),
    ("第三區", "巨人 (Giant)", "ジャイアントスキー場"),
    ("第三區", "蓮池", "蓮池スキー場"),
    ("第三區", "丸池", "丸池スキー場"),
    ("第三區", "太陽谷", "サンバレースキー場"),
    # 第四區
    ("第四區", "涉峠", "渋峠スキー場"),
    ("第四區", "橫手山", "横手山スキー場"),
    ("第四區", "熊之湯", "熊の湯スキー場"),
]


def get_wind_arrow(wind_raw):
    """支援 CSS matrix、SVG rotate 角度，以及 Weathernews 方位檔名/類名"""
    if not wind_raw:
        return "➤"

    raw_lower = wind_raw.lower()

    # 1. 先比對檔名或 class 裡的方位代碼 (16方位或8方位縮寫)
    dir_map = [
        ("nne", "↗"), ("ene", "↗"), ("ne", "↗"),
        ("ese", "↘"), ("sse", "↘"), ("se", "↘"),
        ("ssw", "↙"), ("wsw", "↙"), ("sw", "↙"),
        ("wnw", "↖"), ("nnw", "↖"), ("nw", "↖"),
        ("north", "↑"), ("south", "↓"), ("east", "→"), ("west", "←"),
        ("_n.", "↑"), ("_s.", "↓"), ("_e.", "→"), ("_w.", "←"),
        ("/n.", "↑"), ("/s.", "↓"), ("/e.", "→"), ("/w.", "←"),
    ]
    for code, symbol in dir_map:
        if code in raw_lower:
            return symbol

    # 2. 比對數字編號 (Weathernews 常見 1~16 或 0~15 方位圖示編號)
    m_num = re.search(r"(?:wind|arrow|dir)[_\-]?0?(\d{1,2})\b", raw_lower)
    if m_num:
        idx16 = int(m_num.group(1)) % 16
        arrows16 = ["↑", "↗", "↗", "↗", "→", "↘", "↘", "↘", "↓", "↙", "↙", "↙", "←", "↖", "↖", "↖"]
        return arrows16[idx16]

    # 3. 比對旋轉角度 rotate(deg) 或 CSS matrix(a, b, ...)
    deg = None
    m_rot = re.search(r"rotate\(\s*([\-+]?\d+(?:\.\d+)?)", wind_raw)
    if m_rot:
        deg = float(m_rot.group(1))
    else:
        m_mat = re.search(r"matrix\(\s*([\-+]?\d+(?:\.\d+)?)\s*,\s*([\-+]?\d+(?:\.\d+)?)", wind_raw)
        if m_mat:
            a = float(m_mat.group(1))
            b = float(m_mat.group(2))
            deg = math.degrees(math.atan2(b, a))

    if deg is not None:
        # 加上 180 度校正 Weathernews 箭頭的預設朝向基準，使其與畫面視覺一致
        deg = (deg + 180) % 360
        arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
        idx = int((deg + 22.5) // 45) % 8
        return arrows[idx]

    return "➤"


def parse_weathernews_spot(page, spot_key, url):
    """擷取 Weathernews 的 現在の積雪(snow) 以及逐時預報 天気/気温/風/降雪(snowfall)"""
    spot_data = {
        "icon": "⛅",
        "temp": "--°C",
        "wind": "-- m/s",
        "snow": "-- cm",      # 現在の積雪
        "snowfall": "-- cm",  # 預報降雪
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        # 1. 偵測「スキー場の天気」預報表第一個天氣方塊底色 + 「風」列第一個風向箭頭旋轉角度
        visual_info = page.evaluate("""() => {
            const headings = Array.from(document.querySelectorAll('h2, h3, div, p'));
            const titleEl = headings.find(el => el.textContent.trim() === 'スキー場の天気');
            const container = titleEl ? titleEl.parentElement : document.body;

            const imgs = Array.from(container.querySelectorAll('img'));
            const svgs = Array.from(container.querySelectorAll('svg'));
            const boxes = Array.from(container.querySelectorAll('div, td, span'));

            let firstImgSrc = imgs.length > 0 ? (imgs[0].src || '') : '';
            let firstImgAlt = imgs.length > 0 ? (imgs[0].alt || '') : '';
            let firstSvgClass = svgs.length > 0 ? (svgs[0].getAttribute('class') || '') : '';

            // 抓取第一個天氣圖示底色
            let firstWeatherBg = '';
            for (const b of boxes) {
                const rect = b.getBoundingClientRect();
                if (rect.width >= 20 && rect.width <= 90 && rect.height >= 20 && rect.height <= 90) {
                    const bg = window.getComputedStyle(b).backgroundColor;
                    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'rgb(255, 255, 255)' && bg !== 'rgb(245, 245, 245)') {
                        firstWeatherBg = bg;
                        break;
                    }
                }
            }

            // 精準鎖定「風」這一列，取得第一個時間格子的風向箭頭旋轉角度或檔名
            let windRaw = '';
            const leafNodes = Array.from(container.querySelectorAll('*'));
            const windLabel = leafNodes.find(el => el.children.length === 0 && el.textContent.trim() === '風');
            let searchArea = container;
            if (windLabel) {
                // 往上尋找包含「風」標籤與時間資料格的共同列容器
                let p = windLabel.parentElement;
                for (let depth = 0; depth < 4 && p; depth++) {
                    if (p.querySelectorAll('svg, img').length > 0) {
                        searchArea = p;
                        break;
                    }
                    p = p.parentElement;
                }
            }

            const arrowCandidates = Array.from(searchArea.querySelectorAll('svg, svg g, svg path, svg polygon, img'));
            for (const el of arrowCandidates) {
                const rect = el.getBoundingClientRect();
                if (rect.width >= 6 && rect.width <= 26 && rect.height >= 6 && rect.height <= 26) {
                    const styleTransform = window.getComputedStyle(el).transform;
                    const attrTransform = el.getAttribute('transform') || '';
                    const src = el.getAttribute('src') || '';
                    const cls = el.getAttribute('class') || '';

                    if (styleTransform && styleTransform !== 'none') {
                        windRaw = styleTransform;
                        break;
                    }
                    if (attrTransform.includes('rotate')) {
                        windRaw = attrTransform;
                        break;
                    }
                    if (src && (src.includes('wind') || src.includes('arrow') || /\\/[nsew]{1,3}\\./i.test(src))) {
                        windRaw = src;
                        break;
                    }
                    if (cls && (cls.includes('wind') || cls.includes('arrow'))) {
                        windRaw = cls;
                        break;
                    }
                }
            }

            return {
                imgSrc: firstImgSrc,
                imgAlt: firstImgAlt,
                svgClass: firstSvgClass,
                bgColor: firstWeatherBg,
                windTransform: windRaw
            };
        }""")

        print(f"[{spot_key}] 風向原始偵測: {visual_info.get('windTransform')}")

        visible_text = page.inner_text("body")
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]

        # 2. 擷取畫面上方的「現在の積雪」 -> 存入 snow (支援同一行或跨行、有無 cm 單位)
        m_snow_block = re.search(r"現在の積雪\s*([^\n\r]{1,20})", visible_text)
        if m_snow_block:
            raw_depth = m_snow_block.group(1).strip()
            m_num = re.search(r"(\d+(?:\.\d+)?)", raw_depth)
            if m_num:
                spot_data["snow"] = f"{m_num.group(1)} cm"
            else:
                spot_data["snow"] = "-- cm"

        # 3. 擷取逐時預報表的 気温 / 風 / 降雪 -> 降雪存入 snowfall
        start_idx = -1
        for i in range(len(lines) - 1):
            if lines[i] == "降雪" and lines[i + 1] == "cm":
                start_idx = i + 2
                break

        snowfall_num = 0.0
        if start_idx != -1 and start_idx + 4 < len(lines):
            curr = start_idx
            if "日(" in lines[curr] or "日（" in lines[curr]:
                curr += 1

            if curr + 3 < len(lines):
                temp_val = lines[curr + 1].replace("−", "-")
                wind_val = lines[curr + 2]
                snowfall_val = lines[curr + 3]

                if re.match(r"^[\-+]?\d+(?:\.\d+)?$", temp_val):
                    spot_data["temp"] = f"{temp_val}°C"
                if re.match(r"^\d+(?:\.\d+)?$", wind_val):
                    arrow = get_wind_arrow(visual_info.get("windTransform", "") if visual_info else "")
                    spot_data["wind"] = f"{arrow} {wind_val} m/s"
                if re.match(r"^\d+(?:\.\d+)?$", snowfall_val):
                    spot_data["snowfall"] = f"{snowfall_val} cm"
                    snowfall_num = float(snowfall_val)

        # 4. 判定天氣圖示 (支援 鮮橘晴天 / 淺藍下雪 / 深藍下雨 / 灰色陰天)
        icon_found = ""
        if visual_info:
            combined = f"{visual_info.get('imgAlt', '')} {visual_info.get('imgSrc', '')} {visual_info.get('svgClass', '')}".lower()
            bg = visual_info.get("bgColor", "")

            if any(k in combined for k in ["晴", "sun", "clear", "/100", "_100", "101"]):
                icon_found = "☀️"
            elif any(k in combined for k in ["雪", "snow", "/400", "_400", "401", "402"]):
                icon_found = "❄️"
            elif any(k in combined for k in ["曇", "cloud", "/200", "_200", "201"]):
                icon_found = "☁️"
            elif any(k in combined for k in ["雨", "rain", "/300", "_300"]):
                icon_found = "🌧️"

            if not icon_found and bg:
                m_rgb = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", bg)
                if m_rgb:
                    r, g, b = int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3))
                    if r > 200 and g > 80 and b < 90:
                        icon_found = "☀️"
                    elif b > 180 and r >= 95 and g >= 145:
                        icon_found = "❄️"
                    elif b > 180 and r < 95:
                        icon_found = "🌧️"
                    elif abs(r - g) < 25 and abs(g - b) < 25 and 90 < r < 220:
                        icon_found = "☁️"

        if icon_found:
            spot_data["icon"] = icon_found
        elif snowfall_num > 0:
            spot_data["icon"] = "❄️"
        else:
            spot_data["icon"] = "⛅"

    except Exception as e:
        print(f"抓取 {spot_key} ({url}) 異常: {e}")

    print(f"[{spot_key}] 天氣結果: {spot_data}")
    return spot_data


def parse_shiga_lifts_and_night(page):
    """直接依指定雪場全名對照表，逐行歸類 1~4 區纜車與夜滑狀態"""
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
            for z_name, r_name, jp_full_name in ZONE_RESORTS:
                if jp_full_name in line:
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
                k in line for k in ["リフト", "ゴンドラ", "スカイ", "ペア", "トリプル", "クワッド", "ロマンス", "高速", "第1", "第2", "第3", "第4", "第5"]
            )

            if is_lift_line and len(line) <= 50 and any(
                st in window_text for st in ["運行中", "運休中", "見合わせ", "準備中"]
            ):
                clean_name = re.sub(r"^\d{1,2}\s+", "", line)
                clean_name = re.split(r"\s+[A-Za-z○×△]", clean_name)[0].strip()

                if len(clean_name) >= 2 and not any(
                    bad in clean_name for bad in ["運行", "運休", "見合わせ", "準備中", "最終更新", "天気", "気温", "積雪", "スキー場"]
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
        "yakebitai": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm", "snowfall": "-- cm"},
        "ichinose": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm", "snowfall": "-- cm"},
        "hasuike": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm", "snowfall": "-- cm"},
        "yokoteyama": {"icon": "⛅", "temp": "--°C", "wind": "-- m/s", "snow": "-- cm", "snowfall": "-- cm"},
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
