# 部署成公開網站（GitHub + Streamlit Cloud）

約 10–15 分鐘。完成後會得到類似：

`https://你的帳號-dse-wenyan-srs.streamlit.app`

---

## 你需要

- GitHub 帳號（免費即可）
- 瀏覽器

> 本機若尚未安裝 Xcode Command Line Tools，可**完全用網頁上傳**，不必用終端機 `git`。

---

## 步驟 1：在 GitHub 建立 repo

1. 打開 https://github.com/new  
2. Repository name 建議：`dse-wenyan-srs`  
3. 設為 **Public**（Streamlit 免費方案較方便用公開 repo）  
4. **不要**勾選 Add README（我們會上傳現成專案）  
5. 按 **Create repository**

---

## 步驟 2：上傳專案檔案

### 方法 A（推薦，免 git）

1. 打開 Finder，到：  
   `/Users/ansonlo/grok-practice/`  
2. 可直接用資料夾 `dse-wenyan-srs`，或已打好的 zip：  
   `dse-wenyan-srs-github.zip`  
3. 在 GitHub 新 repo 頁按 **uploading an existing file**  
4. 把 **zip 解壓後的內容**（`app.py`、`requirements.txt`、`src/`…）拖進網頁  
   - 注意：repo **根目錄**要直接看得到 `app.py`（不要多包一層錯路徑）  
5. Commit message 填 `Initial commit`，按 **Commit changes**

### 方法 B（本機有 git 時）

```bash
# 若尚未安裝工具：
xcode-select --install

cd /Users/ansonlo/grok-practice/dse-wenyan-srs
git init
git add .
git commit -m "Initial commit: HKDSE 文言精華網站"
git branch -M main
git remote add origin https://github.com/<你的帳號>/dse-wenyan-srs.git
git push -u origin main
```

---

## 步驟 3：連到 Streamlit Community Cloud

1. 打開 https://share.streamlit.io  
2. 用 **GitHub** 登入並授權  
3. 按 **New app** / **Create app**  
4. 填寫：

| 欄位 | 值 |
|------|-----|
| Repository | `你的帳號/dse-wenyan-srs` |
| Branch | `main` |
| Main file path | `app.py` |
| App URL（可選） | 例如 `dse-wenyan` |

5. 按 **Deploy**  
6. 等 1–3 分鐘，出現綠色成功後，點開公開網址

---

## 步驟 4：手機與分享

- 網址可加到主畫面、傳給自己／同學  
- 第一次開較慢屬正常（雲端冷啟動）

---

## 使用提醒（雲端）

| 項目 | 說明 |
|------|------|
| 學習進度 | 存在雲端暫時磁碟，**重新部署後可能清空** |
| 建議 | 常用 App 內「設定 → 匯出 JSON 備份」 |
| 隱私 | 公開網址＝知道網址的人都能開；目前**無登入鎖** |
| 語譯 | 仍由你在網站上傳，不會自動帶教育局版權譯文 |

---

## 更新網站內容

之後改了程式：

1. 在 GitHub 上傳／改檔後 Commit  
2. Streamlit Cloud 通常會**自動重新部署**  
3. 或到 App 管理頁按 **Reboot** / **Rerun**

---

## 故障排除

**Deploy 失敗 / Module not found**  
- 確認 `requirements.txt` 在 repo 根目錄  
- 確認 Main file 是 `app.py`

**App 白屏或一直轉**  
- 打開 Streamlit 管理頁的 **Logs**  
- 確認 `src/`、`data/seed/` 有上傳

**找不到示範檔**  
- 確認 `samples/` 資料夾已在 repo 內

**想改成私人 repo**  
- Streamlit 支援 private repo，但需在 share.streamlit.io 授權 private 權限

---

## 完成檢查清單

- [ ] GitHub 看得到 `app.py`  
- [ ] Streamlit Deploy 成功  
- [ ] 瀏覽器開得了公開網址  
- [ ] 能按「載入示範：《魚我所欲也》」  
- [ ] 手機用同一個網址可開  
