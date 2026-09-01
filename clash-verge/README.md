# Clash Verge Rev — 擴充腳本

給 [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev)（核心為 mihomo）使用的**訂閱擴充腳本**。放進「擴充腳本」欄位後，會在套用訂閱時動態重建代理組與分流規則。

> 這不是 Shadowrocket 配置。Shadowrocket 用的是完全不同的格式，兩者不通用。

## 這份腳本做什麼

從訂閱的節點清單即時生成 **51 個代理組**與 **153 條規則**，全部由節點屬性推導，機場增減節點或改名都不用手動維護：

- **地區分類**：以正則比對節點名分成港／台／日／新／美／韓／英，未命中的一律歸入「CF／未知」（反向排除法，不會漏節點）
- **中轉識別**：以 `network === 'ws'` 判斷出 Cloudflare 前置的中轉節點，另外生成「直連（無中轉）」分組。中轉節點出口是共享邊緣 IP，支付風控命中率高、長連線也容易中斷
- **協議拆分**：Hysteria2 與 VLESS 各自成組，另有 fallback 型的「穩定」組
- **分流規則**：24 個雲端規則集，涵蓋廣告攔截、HTTPDNS、國內直連、串流解鎖、AI 服務、支付與帳號綁定

## 使用方式

1. Clash Verge Rev →「訂閱」→ 對訂閱卡片點右鍵 →「編輯擴充」→ 貼進 **Script** 欄位
2. 替換檔案裡的佔位符（見下）
3. 儲存後重新套用訂閱

### 必須替換的佔位符

| 佔位符 | 說明 |
|---|---|
| `<YOUR-CONTROLLER-SECRET>` | mihomo 外部控制器密鑰。也可以整行刪掉，改在 Verge 的「Clash 設定」裡設 |
| `<YOUR-SUB-HOST>` / `<YOUR-TOKEN>` | 第二個訂閱的網址（以 `proxy-providers` 形式併入）。用不到就把整個 `proxy-providers` 區塊和兩個 EdgeTunnel 分組刪掉 |
| `<YOUR-IPTV-PLAYLIST-HOST>` | IPTV 播放列表的來源主機。用不到可刪除該行 |

### 需要搭配的設定

- **統一延遲**要開啟（Clash 設定）。腳本設不了這個值，Verge 會在腳本之後覆寫
- 分流依賴 `find-process-mode: strict`（腳本內已設）才能讓 `PROCESS-NAME` 規則生效

## 幾個踩過坑才寫進去的地方

註解裡都有說明原因，這裡列出比較不直覺的：

- **測速位址一律用 `https://`**。開了統一延遲後 mihomo 會送兩次 HEAD 請求，機場若劫持了測速位址就會在第二次逾時，好節點被誤判成失敗而踢出候選
- **規則集透過代理抓取**。直連時 CDN 一被污染，所有 `RULE-SET` 會靜默失效，流量整批掉到兜底規則，而且不會報錯
- **`RULE-SET,YouTube` 必須排在 `RULE-SET,Google` 之前**。Google 規則集裡有 `DOMAIN-KEYWORD,google`，會把 `googlevideo.com`（影片流本體）撈走
- **Stripe 各子域必須跟 PayPal 共用出口**。`r.stripe.com` 是風險訊號端點，與 `api.stripe.com` 來自不同 IP 會直接觸發拒付
- **`statsigapi.net` 用 `REJECT-DROP` 而非 `REJECT`**。主動拒絕會讓客戶端毫秒級重試，靜默丟棄則要等它自己逾時
- **`pki.goog` / `mtalk.google.com` / `safebrowsing.googleapis.com` 需要白名單**。它們分別被上游規則集判成直連或廣告，實際會導致 TLS 握手變慢、推播不通、瀏覽器釣魚防護失效
- **`geox-url` 指向鏡像**。預設來源的 GeoIP 資料庫有 17 MB，直連幾乎必定逾時

## 授權

自用配置，隨意取用。規則集來自 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) 與 [MetaCubeX/meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat)。
