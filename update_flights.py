import os
import json
import requests
import time

# 從 GitHub Secrets 中讀取你的 SerpApi 金鑰
API_KEY = os.environ.get('SERPAPI_KEY')

# 1. 鎖定「精華日期」：4個出發日 x 4個回程日 = 最多 16 次請求
outbound_dates = ["2027-01-08", "2027-01-10", "2027-01-15", "2027-01-22"]
return_dates = ["2027-01-12", "2027-01-14", "2027-01-19", "2027-01-26"]

real_prices = {}

print("開始抓取真實機票價格...")

for out_date in outbound_dates:
    for ret_date in return_dates:
        # 邏輯防呆：回程日必須大於出發日
        if ret_date <= out_date:
            continue
            
        params = {
          "engine": "google_flights",
          "departure_id": "TPE", # 台北
          "arrival_id": "TYO",   # 東京
          "outbound_date": out_date,
          "return_date": ret_date,
          "currency": "TWD",
          "hl": "zh-tw",
          "api_key": API_KEY
        }
        
        try:
            response = requests.get("https://serpapi.com/search", params=params)
            data = response.json()
            
            # 優先抓取「最佳航班 (best_flights)」，若無則抓取「其他航班 (other_flights)」
            price = None
            if data.get('best_flights'):
                price = data['best_flights'][0].get('price')
            elif data.get('other_flights'):
                price = data['other_flights'][0].get('price')
                
            if price:
                # 轉成前端要的格式: 例如 "2027-01-08" -> "1/08"
                out_formatted = f"{int(out_date[5:7])}/{out_date[8:10]}"
                ret_formatted = f"{int(ret_date[5:7])}/{ret_date[8:10]}"
                key = f"{out_formatted}-{ret_formatted}"
                
                real_prices[key] = price
                print(f"成功抓取 {key}: NT$ {price}")
            else:
                print(f"找不到 {out_date} 到 {ret_date} 的航班價格")
                
        except Exception as e:
            print(f"抓取 {out_date} 到 {ret_date} 發生錯誤: {e}")
            
        # 暫停 1.5 秒，避免瞬間發出太多請求被 API 伺服器阻擋
        time.sleep(1.5)

# 2. 將抓到的結果寫入 data.json
with open('data.json', 'w', encoding='utf-8') as f:
    json.dump(real_prices, f, ensure_ascii=False, indent=4)
    
print("機票資料更新完成並已存入 data.json！")
