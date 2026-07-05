"""內建 Claude 模型清單（M9）。

對話區的 Claude 模型下拉為程式碼內建固定清單（非設定頁可改），
第一項為預設值。前端 client.ts 持一份對應 {value,label}，值需與此一致
（單一事實來源：前端顯示、後端校驗各自一份常數）。
"""

CLAUDE_MODELS = ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]  # 第一項＝預設
