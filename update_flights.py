import os
import requests
import json

# 確保這抓到的是你設在 GitHub Secrets 的變數名稱
API_KEY = os.getenv("SERPAPI_KEY") 

def fetch_price(out_date, ret_date):
    params = {
        "engine": "google_flights",
        "departure_id": "TPE",
        "arrival_id": "TYO",
        "outbound_date": out_date,
        "return_date": ret_date,
        "currency": "TWD",
        "hl": "zh-tw",
        "api_key": API_KEY
    }
    
    # 【偵錯重點】：發送請求前，先把參數印出來檢查
    print(f"開始搜尋: {out_date} 到 {ret_date}")
    
    try:
        response = requests.get("https://serpapi.com/search.json", params=params)
        data = response.json()
        
        # 【偵錯重點】：強迫機器人把 API 回傳的原始資料印出來
        # 如果這是空的，代表 API 金鑰有問題，或者搜尋區間沒航班
        print(f"API 回傳資料檢查: {json.dumps(data)[:500]}") 

        if 'best_flights' in data:
            return data['best_flights'][0]['price']
        elif 'other_flights' in data:
            return data['other_flights'][0]['price']
        else:
            print(f"警告：找不到航班數據 - {out_date} 到 {ret_date}")
            return None
            
    except Exception as e:
        print(f"發生錯誤: {e}")
        return None
