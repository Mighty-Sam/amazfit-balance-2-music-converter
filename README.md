# Amazfit Balance 2 Music Converter

貼上 YouTube 網址，產生可以直接匯入 **Amazfit Balance 2** 的 MP3。重點是中文歌名、歌手在手錶上不會變成亂碼。

```
YouTube → 下載最佳音訊 → FFmpeg 轉 MP3 → 清除舊標籤 → 修復亂碼 / NFC 正規化
       → 寫入全新 ID3v2.3 (UTF-16) → 重新讀取驗證 → 周杰倫 - 晴天.mp3
```

> 請只下載你有權下載的內容（自己上傳的影片、創用 CC 授權內容、或權利人允許下載的影片）。

---

## 1. Python 安裝

需要 **Python 3.12 以上，而且要有 tkinter**（GUI 需要）。

建議用 [uv](https://docs.astral.sh/uv/)，它管理的 Python 內建 tkinter，最省事：

```bash
brew install uv
uv venv --python 3.12 --managed-python .venv
```

或從 [python.org](https://www.python.org/downloads/macos/) 下載官方安裝檔（同樣內建 tkinter）：

```bash
python3.12 -m venv .venv
```

> **注意：** Homebrew 的 `python@3.12` 預設沒有 tkinter（要另外 `brew install python-tk@3.12`）。
> 在 macOS 26 上它的 `pyexpat` 可能無法載入，連 `pip` 都會壞掉。遇到 `Symbol not found: _XML_SetAllocTrackerActivationThreshold` 就改用上面兩種方式。

確認：

```bash
.venv/bin/python -c "import tkinter; print(tkinter.TkVersion)"
```

## 2. FFmpeg 安裝

```bash
brew install ffmpeg      # 同時提供 ffmpeg 與 ffprobe
```

沒有 Homebrew 的話，先到 <https://brew.sh> 依指示安裝。程式啟動時會檢查 ffmpeg 與 ffprobe。找不到時會顯示「找不到 FFmpeg。」和安裝說明，轉換按鈕也會停用。

**另外建議安裝 JavaScript runtime。** 新版 YouTube 需要它，yt-dlp 才能取得音訊格式。已經有 Node.js 的話可以跳過：

```bash
brew install deno
```

## 3. pip install

```bash
# uv
uv pip install --python .venv/bin/python -r requirements.txt
# 或一般 pip
.venv/bin/python -m pip install -r requirements.txt
```

YouTube 常常改版，下載失敗時先更新 yt-dlp：

```bash
.venv/bin/python -m pip install -U "yt-dlp[default]"
```

## 4. 啟動方式

| 方式 | 指令 |
|---|---|
| GUI | `.venv/bin/python app.py` |
| CLI | `.venv/bin/python cli.py "https://www.youtube.com/watch?v=XXXXXXXXXXX"` |
| Mac App | `./build_mac.sh`，然後雙擊 `dist/AmazfitMusicConverter.app`（見第 10 節） |
| 環境自我檢查 | `.venv/bin/python app.py --self-test` |

## 5. 如何輸入 YouTube URL

1. 在「YouTube URL」欄位貼上網址，例如 `https://www.youtube.com/watch?v=XXXXXXXXXXX`
2. 確認「輸出資料夾」（預設 `~/Music/Amazfit`，可按「選擇…」更改）
3. 按 **「轉換成 Amazfit MP3」**

進度區會依序顯示：取得影片資訊 → 下載音訊 → 轉換 MP3 → 讀取 Metadata → 修正中文 Metadata → 驗證 ID3 → 完成。完成後會顯示：

```
完成！

歌曲：
周杰倫 - 晴天

輸出：
~/Music/Amazfit/周杰倫 - 晴天.mp3
```

支援的網址格式：`youtube.com/watch?v=`、`youtu.be/`、`/shorts/`、`/live/`、`/embed/`、`music.youtube.com`。
`&list=`、`?si=` 等參數會被忽略，只下載該支影片。純播放清單網址會提示改貼單一影片。

## 6. 如何批次下載

在網址欄一次貼多個，一行一個（空白、逗號分隔，或 `1.`、`-` 開頭的清單也可以）：

```
https://www.youtube.com/watch?v=AAAAAAAAAAA
https://youtu.be/BBBBBBBBBBB
https://www.youtube.com/watch?v=CCCCCCCCCCC
```

- 依序處理，畫面顯示 `1 / 3`、`2 / 3`、`3 / 3`
- 每首個別成功或失敗，**某一首失敗不會中斷其他首**
- 重複的網址只處理一次
- 按「停止」會在目前這首完成後停止
- 同名檔案預設不覆蓋，會自動改名為 `周杰倫 - 晴天 (2).mp3`。勾選「覆蓋已存在的檔案」則直接覆蓋

CLI 也一樣：`python cli.py URL1 URL2 URL3`

## 7. Amazfit Balance 2 相容模式

GUI 的「Amazfit Balance 2 相容模式」預設開啟。開啟時固定使用以下設定：

| 項目 | 值 |
|---|---|
| 格式 | MP3 (libmp3lame, CBR) |
| 取樣率 | 44.1 kHz |
| 聲道 | Stereo |
| 位元率 | 192 kbps |
| 標籤 | ID3v2.3 |
| 文字編碼 | UTF-16（含 BOM） |
| 清除舊 Metadata | 是（ID3v1、所有 ID3v2、APEv2） |
| Unicode | NFC 正規化 |
| ID3v1 | 移除，不寫入 |
| 驗證 | 是 |

關閉相容模式只會開放位元率選擇（128 / 192 / 256 / 320 kbps）。Metadata 仍採用同樣安全的格式並且驗證。

所有參數集中在 [config.py](config.py)（`BALANCE2_PROFILE`），程式其他地方不寫死任何參數。

## 8. Metadata 編碼策略

**為什麼手錶會亂碼？** 一般下載器產生的 MP3 常常同時有：

- **ID3v2.4 + UTF-8**：很多硬體播放器只完整支援 ID3v2.3，而 ID3v2.3 規格裡沒有 UTF-8
- **ID3v1**：只能存 Latin-1，中文必定是亂碼。播放器若優先讀 ID3v1 就會顯示亂碼
- **已經是亂碼的文字**：UTF-8 被當成 Latin-1 解讀，例如 `晴天` 變成 `æ™´å¤©`，再被原封不動寫進標籤

**本工具的做法：**

1. **不信任原始 Metadata。** FFmpeg 轉檔時用 `-map_metadata -1` 丟掉所有來源標籤
2. **推導歌曲資訊**（[core/metadata.py](core/metadata.py)）：
   - 有 YouTube Music 的 `track` / `artist` 欄位就直接使用
   - 否則解析影片標題，例如 `周杰倫 Jay Chou【晴天 Sunny Day】Official MV`、`周杰倫 - 晴天 [Official Video]`
   - `xxx - Topic` 頻道會以頻道名作為歌手
   - 非「音樂」分類的影片不拆標題；無法可靠判斷歌手時，Artist **留白**
3. **清理每個欄位**（[core/text.py](core/text.py)）：
   修復 mojibake（`æ™´å¤©` → `晴天`，也能處理被錯誤轉碼兩次的情況）→ Unicode **NFC** → 移除 NULL、控制字元、零寬字元、BOM、方向控制字元。中文、日文、韓文、emoji（含 ZWJ 組合）、全形符號都會保留
4. **清除所有舊標籤**：ID3v1、所有 ID3v2（包含重複疊加的多個）、APEv2
5. **寫入全新的 ID3v2.3**，每個文字欄位都是 **UTF-16 + BOM**：

   | Frame | 內容 |
   |---|---|
   | TIT2 | Title（只有歌名，例如「晴天」） |
   | TPE1 | Artist |
   | TALB | Album（沒有就不寫） |
   | TYER | Year（ID3v2.3 沒有 TDRC，會自動轉成 TYER） |
   | TRCK | Track number |
   | TCON | Genre |
6. **檔名與 Metadata 分開**：檔名是 `周杰倫 - 晴天.mp3`，Title 是 `晴天`、Artist 是 `周杰倫`。
   檔名會移除 `[Official Video]`、`(Official Audio)`、`[MV]`、`[HD]`、`[4K]` 等雜訊，但保留 `(Live)`、`(feat. …)` 等版本資訊。
   `/ : ? *` 等非法字元會換成全形版本
7. **驗證**（[core/validator.py](core/validator.py)）：重新讀取剛產生的檔案，同時用 mutagen 和自寫的原始位元組解析器（[core/id3_inspect.py](core/id3_inspect.py)）檢查：
   MP3 可解析、FFprobe 可解析、音訊參數正確、只有一個 ID3v2.3、沒有 ID3v1/APE、所有文字欄位都是 UTF-16 含 BOM、Title/Artist 與寫入值完全相同、Unicode 已正規化、沒有 mojibake。
   任一項失敗就顯示 **「Metadata 驗證失敗，請查看 Log」**，不會顯示成功

`config.STRIP_BILINGUAL_LATIN_SUFFIX`（預設開啟）會把從標題解析出的「周杰倫 Jay Chou」簡化成「周杰倫」，比較適合手錶的小螢幕。這只套用在解析結果，不會改動 YouTube 提供的正式欄位。不想要可以改成 `False`。

### 診斷模式（分析 MP3）

切到 **「分析 MP3」** 分頁，把任何 MP3 拖曳進去（或按「選擇 MP3…」）：

```
File:
周杰倫 - 晴天.mp3

Audio:
MP3
44.1 kHz
192 kbps (CBR)
Stereo

Metadata:

ID3v1: None
ID3v2.3: Yes
ID3v2.4: No

Title:
晴天

Artist:
周杰倫

Encoding:
UTF-16

Unicode:
Valid

Balance 2 Compatibility:
PASS
```

結果為 PASS、WARN 或 FAIL，並列出每個問題的原因。例如「TIT2 用 UTF-8 儲存非 ASCII 文字；ID3v2.3 不支援 UTF-8」、「存在含非 ASCII 字元的 ID3v1」。
手錶再出現亂碼時，先把檔案丟進來看。CLI：`python cli.py --analyze 檔案.mp3`

## 9. 常見問題

**Q：手錶上還是顯示亂碼？**
先用「分析 MP3」檢查那個檔案。若是 PASS 但手錶仍亂碼，可能是手錶仍在使用舊的同名檔案或快取。先從手錶刪除該歌曲再重新匯入。也可以檢查手錶系統語言與韌體版本。

**Q：顯示「找不到 FFmpeg」？**
執行 `brew install ffmpeg`，然後按「重新檢查」或重新開啟程式。程式會自動搜尋 `/opt/homebrew/bin` 與 `/usr/local/bin`，從 Finder 開啟 App 也找得到。

**Q：下載失敗「HTTP 403」或「找不到可下載的音訊格式」？**
YouTube 改版了。更新 yt-dlp：`pip install -U "yt-dlp[default]"`；Mac App 則重新執行 `./build_mac.sh`。另外確認已安裝 deno 或 Node.js。

**Q：「這部影片有年齡限制」「私人影片」「會員限定」？**
這些影片需要登入，本工具不處理帳號登入，所以無法下載。

**Q：Artist 是空白？**
影片標題看不出歌手時，Artist 會刻意留白，避免寫入錯誤資訊。之後可以用任何標籤編輯器補上。如果用的編輯器會存成 ID3v2.4 或 UTF-8，記得再用診斷模式檢查。

**Q：檔名多了 (2)？**
輸出資料夾已有同名檔案，預設不覆蓋。需要覆蓋時勾選「覆蓋已存在的檔案」。

**Q：Mac App 第一次打不開（「無法驗證開發者」）？**
App 沒有經過 Apple 簽署。在 Finder 對 App 按右鍵 → 開啟 → 開啟。只需要做一次。

**Q：Log 在哪裡？**
從原始碼執行時在 `logs/converter-YYYY-MM-DD.log`；Mac App 在 `~/Library/Logs/AmazfitMusicConverter/`。GUI 的「開啟 Log」按鈕會直接開啟。
Log 記錄網址（只保留影片 ID，去除 `si=` 等追蹤參數）、video ID、標題、下載 / FFmpeg / Metadata 狀態、輸出檔案、錯誤。家目錄路徑會顯示成 `~`。

**Q：App 有問題時怎麼回報？**
執行 `dist/AmazfitMusicConverter.app/Contents/MacOS/AmazfitMusicConverter --self-test`，它會檢查 FFmpeg、yt-dlp、GUI，並實際轉換一段測試音訊。

---

## 10. Mac App

```bash
./build_mac.sh            # 建置 dist/AmazfitMusicConverter.app（會先跑測試，失敗就不打包）
./build_mac.sh --install  # 建置並複製到 /Applications
```

使用 PyInstaller 打包。FFmpeg 不會被包進 App，仍需 `brew install ffmpeg`。

## 開發

```bash
.venv/bin/python -m pytest -q        # 全部測試（含 GUI 冒煙測試，需要圖形環境）
```

| 測試 | 內容 |
|---|---|
| `tests/test_metadata.py` | 中日韓英文字寫入 → 儲存 → 重新讀取完全一致、UTF-16、清除舊標籤、mojibake 修復、標題解析 |
| `tests/test_filename.py` | 雜訊清除、非法字元、長度限制、同名處理 |
| `tests/test_validator.py` | 正確檔案通過；ID3v1、ID3v2.4、UTF-8、Latin-1、亂碼、重複標籤、錯誤音訊格式都會被抓出 |
| `tests/test_pipeline.py` | 完整流程、驗收標準（`周杰倫 - 晴天.mp3`）、錯誤處理、URL 解析、診斷模式 |
| `tests/test_batch.py` | 批次、停止、失敗隔離 |
| `tests/test_gui_smoke.py` | 實際建立視窗跑完批次與診斷 |

```
amazfit-music-converter/
├── app.py              GUI 進入點（--self-test 自我檢查）
├── cli.py              命令列介面
├── config.py           所有設定
├── build_mac.sh        打包 Mac App
├── core/
│   ├── downloader.py   yt-dlp：網址驗證、下載最佳音訊
│   ├── converter.py    FFmpeg / FFprobe
│   ├── metadata.py     推導 Metadata、寫入 ID3v2.3 UTF-16
│   ├── validator.py    轉換後驗證
│   ├── filename.py     檔名清理
│   ├── text.py         Unicode 正規化、控制字元、mojibake
│   ├── id3_inspect.py  獨立的 ID3 原始位元組解析器
│   ├── diagnostics.py  診斷模式
│   ├── pipeline.py     單首完整流程
│   ├── batch.py        批次處理
│   ├── logsetup.py     每日 Log
│   └── errors.py       錯誤型別（含使用者看得懂的訊息）
├── gui/main_window.py
├── tests/
├── logs/
└── output/
```
