import os
import requests
import json

# 從環境變數獲取 API Key
API_KEY = os.getenv("SERPAPI_KEY") 

def fetch_price(out_date, ret_date):
    """查詢單一日期區段的機票價格"""
    params = {
        "engine": "google_flights",
        "departure_id": "TPE",
        "arrival_id": "NRT",
        "outbound_date": out_date,
        "return_date": ret_date,
        "currency": "TWD",
        "hl": "zh-tw",
        "api_key": API_KEY,
        # 強制加入桌機版 Chrome 的 User-Agent 與語言，偽裝成真實使用者點擊，降低被 Google 阻擋的機率
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get("https://serpapi.com/search.json", params=params)
        data = response.json()
        
        # 【抓出真正問題點】如果 Google 擋爬蟲或出現異常，直接印出 Google 報錯內容
        if 'error' in data:
            print(f"GOOGLE 報錯內容: {data.get('error')}")
            return None
            
        if 'best_flights' not in data and 'other_flights' not in data:
            print(f"DEBUG: 找不到最佳航班欄位！")
            print(f"回傳的 JSON 根目錄所有 Keys: {list(data.keys())}")
            return None

        if 'best_flights' in data:
            return data['best_flights'][0].get('price')
        elif 'other_flights' in data:
            return data['other_flights'][0].get('price')
        
        print(f"搜尋不到航班數據: {out_date} -> {ret_date}")
        return None
            
    except Exception as e:
        print(f"發生異常 ({out_date} -> {ret_date}): {e}")
        return None

# --- 主程式區塊 ---
def main():
    real_prices = {}
    outbound_dates = ["2027-01-11", "2027-01-13", "2027-01-15", "2027-01-17"]
    return_dates = ["2027-01-16", "2027-01-19", "2027-01-22", "2027-01-24"]

    for out_date in outbound_dates:
        for ret_date in return_dates:
            # 邏輯防呆
            if ret_date <= out_date:
                continue
                
            print(f"開始搜尋: {out_date} 到 {ret_date}")
            price = fetch_price(out_date, ret_date)
            
            if price:
                # 格式轉換: "2027-01-11" -> "1/11"
                out_fmt = f"{int(out_date[5:7])}/{out_date[8:10]}"
                ret_fmt = f"{int(ret_date[5:7])}/{ret_date[8:10]}"
                key = f"{out_fmt}-{ret_fmt}"
                real_prices[key] = price
                print(f"成功記錄: {key} = NT${price}")

    # 寫入檔案並確保執行完畢
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(real_prices, f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())
        
    print("所有日期處理完畢，data.json 已更新")

if __name__ == "__main__":
    main()
