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
    """將以正北為 0 度、順時針增加的角度轉換為箭頭"""

    if not wind_raw:
        return "➤"

    try:
        if isinstance(wind_raw, (int, float)):
            deg = float(wind_raw) % 360

        elif str(wind_raw).startswith("ANGLE:"):
            deg = float(str(wind_raw).split(":")[1]) % 360

        else:
            return "➤"

        arrows = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]

        idx = int((deg + 22.5) // 45) % 8

        return arrows[idx]

    except (ValueError, TypeError):
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

        # 1. 偵測「スキー場の天気」預報表第一個天氣方塊底色 + 第一個風向箭頭旋轉角度
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
