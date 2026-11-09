# 抓價細節（改抓價程式時才需要讀）

## 萬豪：`BrowserFetcher.marriott_range()`
- 先開 `https://www.marriott.com/default.mi`，再在頁面內 POST `/mi/query/phoenixShopADFSearchProductsByProperty`。
- 必須帶官網原始 query 文字（`hotelmon/marriott_calendar.graphql`）與 header `graphql-operation-signature: 887375892e1ad2a43f46a9c95c55ea47cf6eca3af03331c2134f1b440cff3f9f`、`graphql-require-safelisting: true`、`apollographql-client-name: phoenix_shop`。
- 一次最多 35 天；`rateRequestTypes:[{type:"REDEMPTION"}]` 回傳 `pointsPerQuantity.points`；沒出現在 edges 的日期＝無積分房。
- 現金用 `type:"STANDARD"` 讀 `lowestAverageRate.amount`（除以 10^decimalPoint）— 尚未實測成功，失敗會設 `mar_cash_broken` 跳過。
- 回應含 Access Denied 就設 `mar_blocked`，這次執行不再對萬豪發任何請求。
- API 失敗但不是被擋時，才退回逐日開 `search/availabilityCalendar.mi?...flexibleDateSearch=false`（會轉到 rateListMenu.mi，解析 "N Points / Stay"），連續 3 次 Access Denied 就停止。
- 查太快會被暫時封鎖約 1–2 小時（2026-11-01 逐日抓 33 天後被擋；23:58 連開 3 頁也被擋）。

## IHG
- 電腦：`IHG_JS` 在 ihg.com 頁面 fetch。網頁：`site/index.html` 的 `ihgQuote()` 直接 fetch（API 允許 fd6815.github.io 跨網域）。兩邊解析規則要一致。
- `https://apis.ihg.com/availability/v3/hotels/offers?fieldset=rateDetails`，header `x-ihg-api-key: hsL8GAAz7drdIbEjBCejdA1Ud2MVhxoo`（官網前端公開金鑰）。
- 積分＝`ratePlanCode=="IVANI"` 的 `rewardNights.pointsOnly.totalPoints`；現金＝其他 offer 的 `totalRate.amountBeforeTax` 最低值。HTTP 400 + "los" ＝最少住宿天數限制。
- 月曆 API（超過 3 天時用，電腦 `ihg_range`／網頁 `ihgCalendar`）：`POST https://apis.ihg.com/availability/v1/calendar`，header `x-ihg-api-key: se9ym5iAzaW8pxfBjkmgbuGjJcr3Pj6Y`、`ihg-language: en-US`，body `{hotelMnemonics:[代碼], startDate, endDate（含）, lengthOfStay:1, guestCounts:[{otaCode:"AQC10",count:人數}], rates:{ratePlanCodes:["IVANI"]}, options:{...}}`。一次一間飯店、最多 31 天，跨網域可從 fd6815.github.io 呼叫。積分＝`data.hotels[].calendar[]` 中 IVANI offer 的 `checkInPoints` 最低值；沒有積分房的日期不會出現（視為無積分房）。現金不可靠，不用，沿用前次。來源：awardbuddy 開源外掛；2026-11-09 實測 12-22／12-23／12-28／12-29 與逐日查一致。

## 計算（`hotelmon/report.py`，網頁 `applyLive()`／`recalcHotel()` 同規則）
- 標準房搶空只剩高階房：中位數 1.7 倍判定為 `premium`，網頁顯示「搶空」。
- 匯率：`hotelmon/fx.py`（open.er-api.com，每日快取 data/fx.json），也寫進 data.json 的 `fx`。
- 「每萬點價值」＝台幣現金 ÷ 點數 × 10000；排該飯店前 25% 為「划算」（粗體）。

## 手機請求（watch/requests.json）
- `open`：查監控日期，有帶 hotel/month 時加查該月。`month`：只補 60 天以後。`daily`：未來 60 天＋監控日期，並檢查降價。
- `chains` 限定集團（網頁已自己查 IHG 時只送 `["marriott"]`）。
- 抓取失敗（`alerts.hotel_failed`：出錯 3 筆以上或全部出錯）時請求不算完成：該集團暫停（`blocked:<chain>`，最短 30 分鐘，間隔等於已被擋多久，最長 6 小時，daily／watch 也遵守；data.json 的 `blocked` 讓網頁顯示重試時間），之後 poll 只重試還沒成功的飯店（`reqh:<id>:<hotel>`），超過 6 小時放棄。任一次 daily／watch／poll 成功就解除。
