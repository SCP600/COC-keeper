# 守秘人檔案櫃

以 AI 擔任守秘人（KP）的《克蘇魯的呼喚》單人跑團網頁。整個遊戲是一個靜態網頁，沒有後端。

## 怎麼玩

打開網站後，到右上角的「設定」選一家模型服務，貼上你自己的 API 金鑰，就可以開始。

- 支援：Google Gemini、Claude API（Anthropic）、相容 OpenAI 介面的端點（例如 OpenRouter）。
- 金鑰只存在你的瀏覽器裡，只會送給你選的那家服務。請在該服務的後台替金鑰設用量上限。
- 進度、調查員名冊和設定也都只存在你的瀏覽器裡，換裝置不會跟著走。
- 一場大約會用掉 70 萬到 100 萬個輸入 token，實際費用依模型而定。遊戲內的觀察面板會顯示實際用量。

## 劇透警告

`src/bible.md` 和 `src/bible2.md` 是兩個劇本的守秘人手冊，內含完整真相。想自己玩的人不要打開。
`index.html` 裡的手冊只做了編碼，懂技術的人可以解開。

## 檔案

| 路徑 | 內容 |
|---|---|
| `index.html` | 部署用的成品，由 `build.py` 產生，不要手動編輯 |
| `src/template.html` | 網頁原始碼（畫面、規則、骰子、音效、模型轉接層） |
| `src/bible.md`、`src/bible2.md` | 守秘人手冊（有劇透） |
| `build.py` | 把上面三個檔組成 `index.html` |

## 修改與更新

1. 改 `src/` 裡的檔案。
2. 執行 `python3 build.py`。
3. 提交並推送。GitHub Pages 會在一兩分鐘內自動更新，網址不變，玩家的存檔不受影響。

## 部署（GitHub Pages）

儲存庫的 Settings → Pages → Build and deployment：Source 選「Deploy from a branch」，Branch 選 `main`、資料夾選 `/ (root)`。

## 目前的狀態

外部模型的連線、串流和錯誤處理已用模擬的 API 回應測過，還沒有用真實金鑰跑過任何一家。
主持規則是照 Claude 調整的，換成其他模型後，格式遵守程度和主持品質都需要實測再調整。
