# Clash Verge Rev — 擴充腳本

給 [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev)（核心為 mihomo）使用的**訂閱擴充腳本**。放進「擴充腳本」欄位後，會在套用訂閱時動態重建代理組與分流規則。

> 這不是 Shadowrocket 配置。Shadowrocket 用的是完全不同的格式，兩者不通用。

## 檔案

| 檔案 | 用途 |
|---|---|
| `script.js` | Clash Verge Rev 的**擴充腳本**。從訂閱節點即時生成分組，機場改名增減都不用維護 |
| `config.yaml` | **獨立完整配置**，給 iOS 原生 Clash、Mihomo Party 等只吃單一 YAML 的用戶端 |

### config.yaml 與 script.js 的差異

YAML 沒有腳本能力，以下兩項無法移植：

- **中轉節點識別**（腳本靠節點屬性判斷，見下）。`filter` 只能比對節點名稱，讀不到節點屬性，所以「直連（無中轉）」那組不存在。支付與帳號綁定若要求固定出口，得自己手動選節點
- **`PROCESS-NAME` 規則**（10 條，本地播放器直連用）。iOS 沒有進程概念，已移除

其餘完整保留：150 條規則、24 個規則集、DNS 設定（fake-ip 過濾 58 條）、統一延遲、sniffer、geox 鏡像。地區分組改用 `filter` 正則、協議分組改用 `exclude-type`，效果等價。

## 取用地址

| 來源 | 網址 |
|---|---|
| GitHub raw | `https://raw.githubusercontent.com/adrianyusong/shadowrocket/main/clash-verge/script.js` |
| jsDelivr | `https://cdn.jsdelivr.net/gh/adrianyusong/shadowrocket@main/clash-verge/script.js` |
| jsDelivr 鏡像 | `https://testingcf.jsdelivr.net/gh/adrianyusong/shadowrocket@main/clash-verge/script.js` |

境內建議用 jsDelivr 鏡像那個，實測比 GitHub raw 快約三倍。

> **注意**：Clash Verge 的「擴充腳本」是本機貼上的欄位，**不支援從網址自動同步**。上面的位址是給你取用與更新用的（下載後貼進去），不是訂閱連結。
>
> jsDelivr 對 `@main` 有快取（約 12 小時），推送後若拿到舊版，改用 `@<commit-sha>` 形式即可取得指定版本。

## 這份腳本做什麼

從訂閱的節點清單即時生成**約 50 個代理組**（實際數量依訂閱裡有哪些地區與協議而定）與 **160 條規則**，全部由節點屬性推導，機場增減節點或改名都不用手動維護：

- **地區分類**：以正則比對節點名分成港／台／日／新／美／韓／英，未命中的一律歸入「CF／未知」（反向排除法，不會漏節點）
- **中轉識別**：`network === 'ws'`，或伺服器主機名以 `cfyes.` 開頭，就判定為 Cloudflare 前置的中轉節點，另外生成「直連（無中轉）」分組。中轉節點出口是共享邊緣 IP，支付風控命中率高、長連線也容易中斷。第二個條件是防範機場推出走 CF 前置、但不是 ws 的節點；`cfyes.` 是目前這家機場的前置域名，**換機場要改成對應的前綴**
- **協議拆分**：Hysteria2 與 VLESS 各自成組，另有 fallback 型的「穩定」組
- **分流規則**：24 個雲端規則集，涵蓋廣告攔截、HTTPDNS、國內直連、串流解鎖、AI 服務、支付與帳號綁定
- **DNS**：整段 DNS 設定（fake-ip、按域名歸屬分流的 `nameserver-policy`、58 條 fake-ip 過濾）直接寫在腳本裡。前提是 Verge 的「DNS 覆寫」開關保持關閉，見下

## 使用方式

1. Clash Verge Rev →「訂閱」→ 對訂閱卡片點右鍵 →「編輯擴充」→ 貼進 **Script** 欄位
2. 替換檔案裡的佔位符（見下）
3. 儲存後重新套用訂閱

### 必須替換的佔位符

| 佔位符 | 所在檔案 | 說明 |
|---|---|---|
| `<YOUR-SUB-HOST>` / `<YOUR-TOKEN>` | script.js | 第二個訂閱的網址（以 `proxy-providers` 形式併入）。用不到就把整個 `proxy-providers` 區塊和兩個 EdgeTunnel 分組刪掉。註解裡也會出現 `<YOUR-SUB-HOST>`，那些不影響執行 |
| `<YOUR-SUBSCRIPTION-URL>` | config.yaml | 機場訂閱網址 |
| `<YOUR-IPTV-PLAYLIST-HOST>` | 兩者 | IPTV 播放列表的來源主機。用不到可刪除該行 |

### 需要搭配的設定

- **控制面設定到 GUI 改**：統一延遲、IPv6、控制器密鑰與 CORS、埠號、模式、日誌等級都歸 Verge 管。v2.5.x 會在所有 Merge 與腳本之後，用「Clash 設定」的值強制蓋回去，腳本裡設了無效。**統一延遲要開、IPv6 要關**
- 分流依賴 `find-process-mode: strict`（腳本內已設）才能讓 `PROCESS-NAME` 規則生效
- **「DNS 覆寫」開關要保持關閉**。開啟時 Verge 會在腳本之後再套一次 `dns_config.yaml`，把腳本的 DNS 蓋掉。要改 DNS 就改腳本

## 幾個踩過坑才寫進去的地方

註解裡都有說明原因，這裡列出比較不直覺的：

- **測速位址一律用 `https://`**。開了統一延遲後 mihomo 會送兩次 HEAD 請求，機場若劫持了測速位址就會在第二次逾時，好節點被誤判成失敗而踢出候選
- **規則集透過代理抓取**。直連時 CDN 一被污染，所有 `RULE-SET` 會靜默失效，流量整批掉到兜底規則，而且不會報錯
- **DNS 寫在腳本，Verge 的 DNS 覆寫保持關閉**。Verge v2.5.4 起的「DNS 覆寫保護」會自動關掉這個開關，GUI 更新也曾把 IPv6 開關翻回開啟，兩次都是悄悄發生、沒有提示。DNS 放進腳本後就不怕開關被關；IPv6 則只能靠 GUI，腳本裡的 `ipv6 = false` 鎖不住，只能當警報：GUI 一旦被翻開，這個值會被丟棄，Verge 會跳出通知
- **`RULE-SET,YouTube` 必須排在 `RULE-SET,Google` 之前**。Google 規則集裡有 `DOMAIN-KEYWORD,google`，會把 `googlevideo.com`（影片流本體）撈走
- **Stripe 各子域必須跟 PayPal 共用出口**。`r.stripe.com` 是風險訊號端點，與 `api.stripe.com` 來自不同 IP 會直接觸發拒付
- **`statsigapi.net`、`qdp.qidian.com` 用 `REJECT-DROP` 而非 `REJECT`**。主動拒絕會讓客戶端毫秒級重試（實測起點的廣告端點每分鐘重試 20–130 次，佔掉整份日誌的 85%），靜默丟棄則要等它自己逾時
- **上游規則集的誤判要逐條搶回，而且要排在對應的 `RULE-SET` 之前**：
  - `pki.goog`、`mtalk.google.com`、`safebrowsing.googleapis.com`：被判成直連或廣告，會導致 TLS 握手變慢、推播不通、瀏覽器釣魚防護失效
  - `digicert.com`：被收進巴哈姆特規則集，憑證吊銷檢查被導去台灣節點而逾時
  - `recaptcha.net`：被 ChinaMax 判成直連，但實際直連會逾時，驗證碼載不出來
  - `filedownload.lenovo.com`：被 ChinaMax 按 `lenovo.com` 判成直連，但主機在境外 Akamai。用 `DOMAIN` 精確匹配，同層其他聯想域名直連正常，不能一起拉進代理
- **STUN 一律直連**。經代理探測到的是節點位址而不是本機真實的 NAT 映射，對端連不過來；走 CF 中轉時 UDP 更是直接失敗。影響是視訊與語音通話只能退回中繼
- **`geox-url` 指向鏡像**。預設來源的 GeoIP 資料庫有 17 MB，直連幾乎必定逾時

## 授權

自用配置，隨意取用。規則集來自 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) 與 [MetaCubeX/meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat)。
