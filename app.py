from flask import Flask, request, abort
import json, os, requests, logging, traceback, re
from dotenv import load_dotenv
from collections import defaultdict 
import hmac, hashlib, base64
from linebot import LineBotApi
from linebot.models import FlexSendMessage

load_dotenv()    # 只呼叫一次

# ---------- OpenAI ----------
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")

import json, os, requests, logging, traceback

import re
SCAM_PATTERNS = [
    (re.compile(r"醫(藥)?費|醫療|急需|救急"), "crisis"),
    (re.compile(r"帳戶(被)?凍結"), "crisis"),
    (re.compile(r"(轉|匯|借)[^\d]{0,3}(\d{3,})(元|塊|台幣)"), "payment"),
    (re.compile(r"這是.*帳[戶號]"), "payment"),
]

# LLM system prompt 
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
# Flask 
app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

STATE = defaultdict(lambda: {"risk":0, "money_calls":0})


def classify_llm(text, timeout=5):
    try:
        rsp = openai.ChatCompletion.create(
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
def analyze_text(text):
    labels=[lab for pat,lab in SCAM_PATTERNS if pat.search(text)]
    stage = infer_stage_counter(labels) if labels else classify_llm(text)["stage"]
    return {"stage": stage or 0, "labels": labels or ["無異常"]}

# 把剛剛湊到的 label 轉成 counter 再丟原本 infer_stage
def infer_stage_counter(lbls):
    c = {k: 0 for k in ["authority","similarity","scarcity",
                        "urgency","romance","crisis","payment"]}
    for l in lbls:
        c[l] += 1
    # -- very simple rule set --
    if c["payment"]>=1: return 4
    if c["crisis"]>=1:   return 3
    return 0

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

# 文獻對照
STAGE_INFO = {
    0: ("關係建立期", "暫無異常，保持正常互動"),
    1: ("情感操控期", "對方正在加速拉近距離，可嘗試要求視訊驗證"),
    2: ("信任測試期", "可能開始測試你的服從度，避免透露隱私/證件"),
    3: ("危機敘事期", "進入情緒勒索，先暫停匯款並與親友討論"),
    4: ("付款引導期", "金錢索求已出現，建議立即停止匯款並求助 165"),
    5: ("重複索求期", "高度疑似詐騙，蒐證後報警"),
}

LABEL_DESC = {
    "crisis"   : ("情緒觸發：恐懼/同情",   "白騎士情境、醫療急需等危機敘事"),
    "payment"  : ("經濟榨取：金錢索求", "提供帳戶或要求匯款"),
    "urgency"  : ("認知偏誤：稀缺/緊迫", "出現『快點』『立刻』等字眼"),
    "authority": ("認知偏誤：權威依從", "冒充政府/銀行增加可信度"),
}

def build_flex_result(result: dict) -> FlexSendMessage:
    stage_num = result["stage"]
    s_name, advice = STAGE_INFO.get(stage_num, ("未知", ""))
    # 把 LABEL_DESC 轉成「情緒觸發：…、經濟榨取：…」這樣的字串
    reasons = "、".join(
        f"{title}：{desc}"
        for lab in result.get("labels", [])
        for title, desc in [LABEL_DESC.get(lab, (lab, ""))]
    ) or "無風險標籤"

    bubble = {
      "type":"bubble",
      "body":{
        "type":"box","layout":"vertical","contents":[
          {"type":"text","text":f"🔎 目前階段：{stage_num}（{s_name}）","weight":"bold","size":"lg"},
          {"type":"separator","margin":"md"},
          {"type":"text","text":f"📌 觸發因子：{reasons}","wrap":True,"margin":"md"},
          {"type":"separator","margin":"md"},
          {"type":"text","text":f"👉 建議行動：{advice}","wrap":True,"margin":"md"}
        ]
      },
      "footer":{
        "type":"box","layout":"horizontal","contents":[
          {"type":"button","style":"link","height":"sm",
           "action":{"type":"postback","label":"為何這樣判斷？","data":"action=explain"}},
          {"type":"button","style":"link","height":"sm",
           "action":{"type":"postback","label":"如何防範？","data":"action=prevent"}}
        ]
      }
    }
    return FlexSendMessage(alt_text="詐騙偵測結果", contents=bubble)

#def generate_reply(result: dict) -> str:
    stage = result["stage"]
    s_name, advice = STAGE_INFO.get(stage, ("未知", ""))
    # 先把 tuple[0] （title）抽出來再 join
    labels = result.get("labels", [])
    reasons_list = []
    for l in labels:
        tup = LABEL_DESC.get(l)
        if isinstance(tup, tuple):
            reasons_list.append(tup[0])   # 取 tuple 的第一欄
        else:
            reasons_list.append(str(l))   # fallback
    reasons = "、".join(reasons_list) if reasons_list else "無風險標籤"

    return (
        f"🔎 目前階段：{stage}（{s_name}）\n"
        f"📌 觸發因子：{reasons}\n"
        f"👉 建議行動：{advice}"
    )

# 每個label的說明
def enrich_result(result: dict) -> dict:
    stage_num = result["stage"]
    stage_name, stage_advice = STAGE_INFO.get(stage_num, ("未知", ""))
    labels = result.get("labels", [])
    reasons = []

    for l in labels:
        title, desc = LABEL_DESC.get(l, (l, ""))
        reasons.append(f"• **{title}**：{desc}")

    return {
        "stage_num": stage_num,
        "stage_name": stage_name,
        "stage_advice": stage_advice,
        "reason_text": "\n".join(reasons) if reasons else "（未命中風險特徵）",
    }


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
# if False:
#     @app.route("/testhook", methods=["POST"])
#     def testhook():
#         body = request.get_data(as_text=True)

#         try:
#             json_data = json.loads(body)
#             logging.info("\n==== [Log] 接收到的資料 ====\n" + json.dumps(json_data, ensure_ascii=False, indent=2))


#             events = json_data.get("events", [])
#             for event in events:
#                 if event["type"] == "message" and event["message"]["type"] == "text":
#                     reply_token = event["replyToken"]
#                     user_msg = event["message"]["text"]
#                     user_id = event["source"]["userId"]

#                 # 儲存聊天紀錄
#                     user_chat_history.setdefault(user_id, []).append(user_msg)

#                 # 準備分析資料（模擬送出）
#                     analysis_data = prepare_analysis_data(user_id, user_msg)
#                     logging.info("\n==== [Log] 準備送出的分析資料 ====\n" + json.dumps(analysis_data, ensure_ascii=False, indent=2))


#                 # 分析結果
#                 # result = send_to_api(analysis_data)  # 真實分析結果
#                     result = analyze_text(user_msg)  # 模擬分析

#                     reply_msg = generate_reply(result)
#                     if should_warn(result):
#                         reply_msg += "\n" + generate_warning(result)

#                     reply_to_user(reply_token, reply_msg)

#         except Exception as e:
#             logging.error("\n==== [Log] 發生錯誤 ====")
#             logging.error(str(e))
#             logging.error(traceback.format_exc())  
#             abort(400)

#         return "OK"

# === 回傳訊息給使用者（使用 reply API） ===

def reply_text(token, text):
    line_bot_api.reply_message(token, TextSendMessage(text=text))

def reply_flex(token, flex: FlexSendMessage):
    line_bot_api.reply_message(token, flex)

# if False:
#     def reply_to_user(token, text):
#         url = "https://api.line.me/v2/bot/message/reply"
#         headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
#                 "Content-Type":"application/json"}
#         payload = {"replyToken": token,
#                 "messages":[{"type":"text","text":text}]}
#         r = requests.post(url, headers=headers, data=json.dumps(payload))
#         app.logger.info(f"LINE reply status: {r.status_code}")   # <── 新增
#         if r.status_code != 200:
#             app.logger.error(r.text)

#驗證
@app.route("/callback", methods=["POST"])
def line_callback():
    app.logger.info(">>> ENTER /callback") 
    signature = request.headers.get("X-Line-Signature", "")
    if signature in ("", "test"):
        app.logger.info("signature empty, bypass verify")
        data = json.loads(request.data.decode("utf-8"))

    body_bytes = request.get_data()
    hash_bytes = hmac.new(CHANNEL_SECRET.encode(), body_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(base64.b64encode(hash_bytes).decode(), signature):
        return "invalid sig", 403  
    
    payload = json.loads(body_bytes.decode("utf-8"))
    for ev in payload.get("events", []):
        if ev.get("type") == "message" and ev["message"]["type"] == "text":
            token = ev["replyToken"]  
            user_text = ev["message"]["text"]

            result = analyze_text(user_text)
            flex = build_flex_result(result)

            line_bot_api.reply_message(token, flex)

    return "OK", 200

# if False:
#     data = json.loads(body_bytes.decode("utf-8"))
#     for ev in data.get("events", []):
#         if ev.get("type") == "message" and ev["message"]["type"] == "text":
#             user_text = ev["message"]["text"]
#             app.logger.info(f"  event type={ev['type']} text={user_text}")
#             result = analyze_text(user_text)
#             flex = build_flex_result(result)
#             reply_flex(ev["replyToken"], flex)
#     app.logger.info("<<< LEAVE /callback")
#         return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5080)
