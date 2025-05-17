from flask import Flask, request, abort
import json
import requests
import logging
import traceback
import os

from dotenv import load_dotenv

load_dotenv()    # 只呼叫一次

from openai import OpenAI
client = OpenAI()

import os

import openai
openai.api_key = os.getenv("OPENAI_API_KEY")

import json, os, requests, logging, traceback

SYSTEM_PROMPT = """
你是一個詐騙對話階段分類助手。
[Stage definitions]
0 Discovery: ...
1 Bonding/Grooming: ...
2 Testing Trust: ...
3 Crisis Story: ...
4 Payment Coaching: ...
5 Aftermath/Repeat: ...

[輸出格式]
{"stage": <int>, "labels": ["urgency","crisis"]}

[Examples]
<dialog>
User: 嗨～可以認識你嗎？我也住台北！
Assistant: {"stage":1,"labels":["similarity","romance"]}
</dialog>
<dialog>
User: 我急需 5000 付媽媽醫藥費…拜託你幫我！
Assistant: {"stage":3,"labels":["urgency","crisis"]}
</dialog>
<dialog>
User: 這是銀行帳號 000-123-456，現在轉過去就能解凍！
Assistant: {"stage":4,"labels":["payment","urgency"]}
</dialog>

"""
def classify(text: str, timeout=5):
    try:
        rsp = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            timeout=timeout,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ]
        )
        return json.loads(rsp.choices[0].message.content)
    except Exception as e:
        logging.warning(f"GPT 失敗：{e}")
        return {"stage": 0, "labels": []}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


app = Flask(__name__)

load_dotenv()

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

# ==== 詐騙關鍵字字典 ====
RULES = {
    "authority":  ["officer", "bank", "agent", "official", "protocol"],
    "similarity": ["me too", "same", "also", "just like you"],
    "scarcity":   ["last chance", "only today", "limited", "rare"],
    "urgency":    ["urgent", "immediately", "asap", "now", "right away", "快點", "馬上", "立刻"],
    "romance":    ["sweetheart", "my love", "miss you", "never felt", "親愛的", "想你", "寶貝"],
    "crisis":     ["hospital", "surgery", "accident", "fees", "visa", "customs", "醫院", "急診", "手術", "車禍"],
    "payment":    ["transfer", "wire", "crypto", "bitcoin", "gift card", "account number", "匯款", "轉帳", "帳號", "比特幣", "禮物卡"]

}

# === 模擬詐騙分析結果 ===
def analyze_text(text: str) -> dict:
    counter = {k: 0 for k in RULES}     # 每種手法出現次數
    matched  = []                       # 紀錄命中的 label 及片段
    for label, kw_list in RULES.items():
        for kw in kw_list:
            if kw.lower() in text.lower():
                counter[label] += 1
                matched.append((label, kw))

# 如果 keyword 不足兩種，才呼叫 GPT 補語意判斷（省 token）
    if len({m[0] for m in matched}) < 2:
        llm = classify(text)          # <-- 你的 classify() 已經準備好了
        stage  = llm["stage"]
        labels = llm["labels"]
    else:
        stage  = infer_stage(counter)
        labels = [m[0] for m in matched]

    return {"stage": stage, "labels": labels, "hits": matched}


# ==== 情意分析推斷規則 ====
def infer_stage(c):
    # Stage 4：要求付款
    if c["payment"] > 0 or c["crisis"] > 1:
        return 4
    # Stage 3：危機＋緊迫
    if c["crisis"] > 0 and c["urgency"] > 0:
        return 3
    # Stage 2：權威＋測試隔離
    if c["authority"] > 0 and (c["similarity"] > 0 or c["urgency"] > 0):
        return 2
    # Stage 1：高頻 Grooming
    if c["similarity"] + c["romance"] >= 3:
        return 1
    return 0

# === 傳送資料到 API 伺服器並接收回覆語句 + 詐騙風險分析 ===
def send_to_api(data):
    try:
        api_url = "https://example.com/api/analyze"  # 替換成正式 API URL
        headers = {"Content-Type": "application/json"}
        res = requests.post(api_url, headers=headers, data=json.dumps(data), timeout=5)
        if res.status_code == 200:
            print(res.json())  # 印出回傳內容方便 debug
            return res.json()
        else:
            print(f"API 回應錯誤：{res.status_code}")
            return {"label": "unknown", "confidence": 0.0, "reply": "目前系統繁忙，請稍後再試。"}
    except Exception as e:
        print(f"傳送 API 發生錯誤：{e}")
        return {"label": "unknown", "confidence": 0.0, "reply": "目前系統無法使用，請晚點再聊。"}

# 回傳生成的詐騙訊息
def generate_reply(result):
    stage = result.get("stage")
    labels = ", ".join(result.get("labels", []))
    return f"偵測階段：{stage}；風險標籤：{labels}"

# 判斷是否需要警示訊息
def should_warn(result):
    # rule-based：到 Stage 3 以上 or 出現 payment 關鍵字
    high_risk = result.get("stage", 0) >= 3 or "payment" in (result.get("labels") or [])
    return high_risk

# 如果需要警示，產生警示內容
def generate_warning(_):
    return "[警示] 你可能正被詐騙，請提高警覺！"

# === 獲取使用者基本資料 ===
def get_user_profile(user_id):
    try:
        url = f"https://api.line.me/v2/bot/profile/{user_id}"
        headers = {
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
        }
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            return res.json()
        else:
            logging.warning(f"取得使用者資料失敗，狀態碼：{res.status_code}")
    except Exception as e:
        logging.error("[get_user_profile 錯誤]")
        logging.error(traceback.format_exc())
    return {}  


# === 整合資料給模型 / API 使用 ===
def prepare_analysis_data(user_id, message):
    profile = get_user_profile(user_id)
    history = user_chat_history.get(user_id, [])
    return {
        "user_id": user_id,
        "display_name": profile.get("displayName", ""),
        "picture_url": profile.get("pictureUrl", ""),
        "language": profile.get("language", ""),
        "current_message": message,
        "chat_history": history
    }

# === 儲存聊天紀錄（記憶體版） ===
user_chat_history = {}  # key: userId, value: list of text messages

# === 接收來自 LINE 的訊息 ===
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)

    try:
        json_data = json.loads(body)
        logging.info("\n==== [Log] 接收到的資料 ====\n" + json.dumps(json_data, ensure_ascii=False, indent=2))


        events = json_data.get("events", [])
        for event in events:
            if event["type"] == "message" and event["message"]["type"] == "text":
                reply_token = event["replyToken"]
                user_msg = event["message"]["text"]
                user_id = event["source"]["userId"]

                # 儲存聊天紀錄
                user_chat_history.setdefault(user_id, []).append(user_msg)

                # 準備分析資料（模擬送出）
                analysis_data = prepare_analysis_data(user_id, user_msg)
                logging.info("\n==== [Log] 準備送出的分析資料 ====\n" + json.dumps(analysis_data, ensure_ascii=False, indent=2))


                # 分析結果
                # result = send_to_api(analysis_data)  # 真實分析結果
                result = analyze_text(user_msg)  # 模擬分析

                reply_msg = generate_reply(result)
                if should_warn(result):
                    reply_msg += "\n" + generate_warning(result)

                reply_to_user(reply_token, reply_msg)

    except Exception as e:
        logging.error("\n==== [Log] 發生錯誤 ====")
        logging.error(str(e))
        logging.error(traceback.format_exc())  
        abort(400)



    return "OK"

# === 回傳訊息給使用者（使用 reply API） ===
def reply_to_user(reply_token, text):
    try:
        url = "https://api.line.me/v2/bot/message/reply"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
        }
        payload = {
            "replyToken": reply_token,
            "messages": [
                {
                    "type": "text",
                    "text": text
                }
            ]
        }
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code != 200:
            logging.warning(f"回傳訊息失敗，狀態碼：{res.status_code}, 回傳內容：{res.text}")
    except Exception as e:
        logging.error("[reply_to_user 錯誤]")
        logging.error(traceback.format_exc())


# === 測試首頁 ===
@app.route("/")
def index():
    return "Hello, Scam Bot!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

