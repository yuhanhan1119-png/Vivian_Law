# 法規整理 Vivian_Law

把法規條文整理成**可檢索、可註記、可比對**的個人知識庫，並附一個**可安裝到手機的 App**（PWA，離線也能查）。

只用 Python 標準函式庫，不需要安裝任何第三方套件。

```
法規純文字  ─▶  解析（編章節條項款目）  ─▶  SQLite 知識庫  ─▶  App／命令列／匯出
                    ＋ 交互參照擷取            ＋ 標籤筆記收藏      ＋ 修法比對
```

---

## 功能

| 功能 | 說明 |
| --- | --- |
| 條文結構化 | 自動辨識 **編／章／節／款／目** 與 **條／項／款／目**，支援「第 12-1 條」「第十二條之一」與「（刪除）」 |
| 全文檢索 | SQLite FTS5 trigram 索引，中文可做子字串檢索，結果附關鍵字前後文；完全相符找不到時會自動改用字序寬鬆比對（打「誠實信用」也找得到「誠實**及**信用」） |
| 交互參照 | 擷取「準用／適用／依據／參照／除外／處罰」關係，支援跨法規（民法第 197 條）、範圍（第 5 條至第 8 條），並提供**被引用**反查 |
| 標籤與筆記 | 每一條可加標籤（爭點、考點、案件類型）、寫筆記、加入收藏 |
| 修法比對 | 同一部法規的兩個版本逐條比對，標示新增／刪除／修正，並顯示逐字差異 |
| 匯出 | Markdown、JSON、CSV、交互參照表、純文字（可再匯入）、App 離線資料包 |
| App | 手機優先介面，可「加入主畫面」，離線可瀏覽與檢索，離線所做的註記會在連線後自動同步 |

---

## 快速開始

需要 Python 3.10 以上（Windows 可到 [python.org](https://www.python.org/downloads/) 安裝，安裝時勾選 *Add python.exe to PATH*）。

### Windows

```bat
git clone https://github.com/yuhanhan1119-png/Vivian_Law.git
cd Vivian_Law

law init                                  :: 建立 D:\Vivian_Law 知識庫
law import samples\民法-節錄.txt          :: 匯入法規
law search 損害賠償                        :: 檢索
```

啟動 App：**直接雙擊 `啟動App.bat`**，瀏覽器會自動打開。

### macOS / Linux

```bash
git clone https://github.com/yuhanhan1119-png/Vivian_Law.git
cd Vivian_Law

./law init
./law import samples/
./law serve
```

---

## 資料存放在哪裡？

所有資料集中在**一個資料夾**（資料根目錄），預設就是你指定的 `D:\Vivian_Law`：

```
D:\Vivian_Law\
├── lawkit.db        ← 知識庫本體（條文、參照、標籤、筆記、收藏）
├── config.json      ← 本機設定
├── sources\         ← 匯入時自動保留的原始法規文字檔
├── exports\         ← 匯出的 Markdown／JSON／CSV
├── bundles\         ← App 離線資料包 bundle.json
└── backups\         ← law backup 產生的資料庫備份
```

位置的決定順序（前面的優先）：

1. 指令參數 `--data-dir D:\其他位置`
2. 環境變數 `LAWKIT_HOME`
3. 專案目錄中的 `lawkit.json`（內含 `{"data_dir": "..."}`，可參考 `lawkit.example.json`）
4. 平台預設值：Windows 為 `D:\Vivian_Law`（沒有 D 槽時改用使用者家目錄），macOS／Linux 為 `~/Vivian_Law`

隨時可以用這個指令確認目前用的是哪一個資料夾：

```bat
law where
```

想固定改成別的位置：

```bat
setx LAWKIT_HOME D:\Vivian_Law\正式庫
```

> 備份很簡單：整個 `D:\Vivian_Law` 資料夾複製走即可；或執行 `law backup` 產生資料庫快照。

---

## 執行檔一覽

| 檔案 | 平台 | 用途 |
| --- | --- | --- |
| `啟動App.bat` | Windows | **雙擊即可啟動 App**，自動開啟瀏覽器 |
| `law.bat` | Windows | 命令列進入點，例如 `law import 民法.txt` |
| `law` | macOS／Linux | 命令列進入點，例如 `./law serve` |
| `law.py` | 全平台 | 同上，`python law.py <指令>` |
| `python -m lawkit` | 全平台 | 不使用啟動腳本時的等效寫法 |

也可以安裝成系統指令（之後在任何目錄都能用 `law`）：

```bash
pip install -e .
law where
```

---

## 做成 App

App 是 **PWA**：用瀏覽器開啟後即可安裝到主畫面，有圖示、全螢幕、離線可用，不必上架商店。

1. 在電腦上啟動伺服器：雙擊 `啟動App.bat`（或執行 `law serve`）。畫面會列出可用網址，例如：

   ```
   網址　：http://127.0.0.1:8383/
   網址　：http://192.168.1.23:8383/      ← 手機用這個
   ```

2. **手機**：連上同一個 Wi-Fi，用瀏覽器開啟 `http://192.168.1.23:8383/`
   - Android Chrome：選單 →「安裝應用程式」或「加到主畫面」
   - iPhone Safari：分享 →「加入主畫面」
3. **電腦**：Chrome／Edge 網址列右側的「安裝」圖示。

安裝後：

- 首次連線會自動下載離線資料包，之後**沒有網路也能瀏覽與檢索**（設定頁可手動更新）。
- 離線時新增的標籤、筆記、收藏會排入佇列，回到有伺服器的環境時自動補送。
- App 內就能貼上條文匯入新法規（「法規 → ＋ 匯入」）。

想做成 **Android／iOS 原生安裝檔**時，這個 App 已符合條件，可用 [Capacitor](https://capacitorjs.com/) 或 [PWABuilder](https://www.pwabuilder.com/) 直接包裝 `lawkit/web`（需搭配 `law bundle --for-app` 產生的離線資料，才能不連伺服器獨立執行）。

---

## 常用指令

```bash
law init                    # 建立資料夾與資料庫
law where                   # 顯示資料存放位置
law import <檔案|資料夾>     # 匯入法規（支援 UTF-8／Big5，可整個資料夾）
law import 民法.txt --version 112.06.21 --replace
law laws                    # 已匯入的法規清單
law tree 民法 --articles     # 編章節結構
law show 民法 184            # 看單一條文（含參照、標籤、筆記）
law search 損害賠償 --law 民法
law refs 民法 184            # 本條引用；加 --incoming 看被誰引用
law tag add 考點 --law 民法 --article 184
law note set --law 民法 --article 184 --body "第一項為一般侵權行為"
law bookmark 民法 184
law diff 示範資料保護法 --from "民國 108 年 05 月 10 日" --to "民國 112 年 11 月 20 日"
law export markdown --law 民法     # 也支援 json / csv / refs / text / bundle
law bundle --for-app        # 產生 App 離線資料包
law stats                   # 知識庫統計
law backup                  # 備份資料庫
law serve --port 8383       # 啟動 App
```

---

## 支援的法規文字格式

以[全國法規資料庫](https://law.moj.gov.tw/)的純文字條文為主，也接受自行整理的檔案：

```
法規名稱：中華民國民法
修正日期：民國 110 年 01 月 13 日

第 一 編 總則
第 一 章 法例
第 1 條
民事，法律所未規定者，依習慣；無習慣者，依法理。
第 2 條
本法用詞，定義如下：
一、甲：指第一種情形。
二、乙：指第二種情形。
　　前項各款情形，準用第一條之規定。
第 3 條
（刪除）
```

- 條號可寫 `第 1 條`、`第1條`、`第 12-1 條`、`第十二條之一`
- 項＝一行一項；款＝`一、`；目＝`（一）`
- 條文中提到的「第一款」「第五條」不會被誤判為章節或新條號
- `samples/` 內附民法節錄，以及可示範修法比對的兩個版本示範法規

---

## 專案結構

```
lawkit/
├── numerals.py    中文數字與條號互轉
├── models.py      法規／編章節／條／項／款／目資料模型
├── parser.py      法規純文字解析
├── crossref.py    交互參照擷取（準用、適用、依據……）
├── config.py      資料夾位置與設定（知識庫存在哪裡）
├── storage.py     SQLite 儲存、全文檢索、標籤筆記收藏、遷移
├── diff.py        修法比對
├── exporter.py    匯出格式（可註冊擴充）
├── cli.py         命令列（指令可註冊擴充）
├── server.py      HTTP API（路由可註冊擴充）
└── web/           App 前端（PWA）
tests/             139 個測試
samples/           範例法規
```

---

## 開發與測試

```bash
python -m unittest discover -s tests -t .   # 全部測試
python -m unittest tests.test_parser -v     # 單一模組
```

要新增功能（新指令、新 API、新匯出格式、新資料表、App 新頁面）時，
請參考 **[docs/擴充指南.md](docs/擴充指南.md)**，每一層都設計成「加一段程式就會自動出現」。

---

## 說明

- 本工具是**整理與檢索**用途；條文內容以[全國法規資料庫](https://law.moj.gov.tw/)公告版本為準，請勿直接引用本知識庫作為法律意見。
- `samples/示範資料保護法-*.txt` 為**虛構法規**，僅用於示範解析與比對功能。
- 授權：MIT
