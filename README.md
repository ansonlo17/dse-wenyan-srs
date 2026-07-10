# 文言精華 · HKDSE 指定文言複習

漂亮、手機優先的 **網頁程式**（Streamlit），幫助你準備 **HKDSE 中文科指定文言經典（12 篇範文）** 的字詞。

用瀏覽器開啟即可，不需安裝 App。

## 功能

- 上傳教育局原文／語譯（PDF 或 TXT），段落對照閱讀
- 半自動建議「較難文言字眼」（虛詞、古今異義、結構、常見實詞）
- 手動確認／略過／編輯後加入字庫
- **SM-2** 間隔重複：再來／困難／尚可／簡單
- 每日複習、掌握度、弱點分析、JSON 備份

> 程式**不內嵌**官方全文語譯。語譯由你上傳，介面會標示「使用者上傳」。

---

## 一、本機當成網站用（最快）

```bash
cd dse-wenyan-srs
chmod +x start-web.sh
./start-web.sh
```

或手動：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

| 裝置 | 網址 |
|------|------|
| 這台電腦 | http://localhost:8501 |
| 手機（同一 Wi‑Fi） | http://你的電腦IP:8501 |

在 Mac 可於「系統設定 → 網絡」查看 IP，或看 `start-web.sh` 印出的位址。

---

## 二、Docker 一鍵網站（適合長期開著）

```bash
cd dse-wenyan-srs
docker compose up -d --build
```

瀏覽器開：http://localhost:8501  

進度會存在 `./data`（已掛 volume，重啟不丟）。

停止：

```bash
docker compose down
```

---

## 三、部署成公開網站（網路上任何人可開）

### 方案 A：Streamlit Community Cloud（免費、最簡單）

1. 把本專案放到 **GitHub**（公開或私有 repo）
2. 前往 [share.streamlit.io](https://share.streamlit.io) 用 GitHub 登入
3. **New app** → 選 repo → Main file path 填 `app.py`
4. Deploy 後會得到類似：  
   `https://xxxxx.streamlit.app`

**限制：** 免費雲端的檔案系統常是暫時的，重新部署後 SQLite 進度可能清空。請定期在 App「設定」**匯出 JSON 備份**。

### 方案 B：Railway / Render / Fly.io（適合要持久化）

用本專案的 `Dockerfile` 部署，並掛載 volume 到 `/app/data`，進度才會長期保留。

範例（Railway）：連 GitHub → New → Dockerfile → 開 port **8501**。

### 方案 C：家用 NAS / 樹莓派 / 舊筆電

```bash
docker compose up -d
```

再用路由器 port forward 或 Cloudflare Tunnel 暴露到公網（注意：目前無登入帳號，**不要**對全世界開放敏感資料）。

---

## 建議學習流程

1. **文庫** → 選篇章 → 上傳原文／語譯（或點「載入示範：《魚我所欲也》」）
2. **閱讀** → 對照段落 → 點建議難詞加入
3. **難字審核** → 批次確認建議
4. **複習** → SM-2 翻卡
5. **統計** → 看弱點

## 教育局資源

- [指定文言經典學習材料資源](https://www.edb.gov.hk/tc/curriculum-development/kla/chi-edu/nss-lang/settext-index.html)
- [十二篇原文 PDF](https://www.edb.gov.hk/attachment/tc/curriculum-development/kla/chi-edu/nss-lang/Set_text_12.pdf)

## 專案結構

```
dse-wenyan-srs/
  app.py                 # 網站入口
  start-web.sh           # 本機網站啟動腳本
  Dockerfile             # 容器部署
  docker-compose.yml
  .streamlit/config.toml # 網頁主題與伺服器設定
  assets/style.css
  data/                  # SQLite 與上傳檔（執行後產生）
  samples/
  src/
```

## 注意

- 掃描版 PDF 可能抽不到字，請改用 TXT 或先 OCR。
- 學習資料在 `data/app.db`；換機或上雲前請用「設定 → 匯出備份」。
- 若本機 `python3` 失敗，先安裝 Xcode CLT：`xcode-select --install`
- 目前是**單人本機／自架**設計，尚無多使用者帳號系統。
