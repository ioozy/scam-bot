from app import analyze_text
test_msgs = [
    "嗨嗨～可以認識你嗎？我也住台北！",
    "我急需 5000 醫藥費，能先匯給我嗎？",
    "這是銀行帳號 000-123-456，現在轉帳就能解凍",
]
for m in test_msgs:
    print(m, "→", analyze_text(m))