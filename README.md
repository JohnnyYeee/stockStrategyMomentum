# Mom 12-1 动量策略 · 自动刷新静态站

纯静态页面 + 免费定时任务。**你不用跑任何服务器**,名单每个交易日收盘后自动重算。

每月第 10 个交易日,从「标普 500 + AI 名单」里按 **12-1 动量**取前 10 只等权持有。页面每天重算「当前」前 10 作实时参考,并列出紧邻的观察区。

```
index.html          展示层:读取同目录 data.json 并渲染(开着的标签页每 5 分钟自重取)
screener.py         选股脚本:yfinance 拉复权收盘价 → 算 12-1 动量 → 生成结果(无需 API key)
tickers.txt         AI/自选增量名单(与标普 500 一并纳入动量排名, 页面标 ★)
data.json           最新结构化结果(由定时任务自动覆盖)
summary.txt         最新纯文本名单(给 AI / 人类快速读)
data/<日期>.json     每日历史归档;data/index.json 列出可用日期
.github/workflows/screen.yml  GitHub Actions 定时任务:每个交易日收盘后重跑并提交
```

## 策略规则
1. **池子**:标普 500 现成分(Wikipedia 自动抓取)+ `tickers.txt` 的 AI 名单,去重约 550 只
2. **信号**:`动量分 = 复权价[t-21] / 复权价[t-252] − 1`(近 12 个月涨幅,剔除最近 1 个月)
3. **过滤**:现价 > $5,且有至少 252 天历史
4. **选股**:动量分降序取前 10,等权各 10%
5. **换仓**:每月第 10 个交易日收盘;掉出前 10 即卖,配平回各 10%
6. **成本假设**:单边 15bps

## 给 AI / 程序读取
页面上线后,这些 URL 都是同源、可直接抓取的:
- `…/data.json` —— 结构化(数据日、generated_at、每只票动量分/近1月/12月涨幅/权重/板块…)
- `…/summary.txt` —— 纯文本名单,最省事
- `…/data/<日期>.json` —— 某天的历史快照;`…/data/index.json` 给出 `latest` 和全部日期

## 为什么"纯静态也能自动刷新"
静态文件自己不会按点醒来拉数据,所以靠 **GitHub Actions 的 cron**(白嫖的免费算力)在收盘后跑
`screener.py`,把新的 `data.json` 提交回仓库;`index.html` 同源读取它。整条链路没有你要运维的后端。

## 部署(GitHub Pages,最省事)
1. 新建一个 GitHub 仓库,把这个文件夹整包推上去(保留 `.github/workflows/` 目录结构)。
2. 仓库 **Settings → Pages** → Source 选 `main` 分支、`/ (root)`,保存。
3. 仓库 **Settings → Actions → General** → 底部 "Workflow permissions" 选 **Read and write**(让任务能提交 data.json)。
4. **Actions** 标签页 → 选中 `refresh-momentum-screen` → "Run workflow" 手动先跑一次,确认生成新的 data.json。
5. 打开 `https://<用户名>.github.io/<仓库名>/` 即可。之后每个交易日收盘后自动更新。

> 想用自己的域名:在仓库 Pages 里绑定自定义域名(CNAME)即可,页面与 data.json 仍同源,无跨域问题。

## 本地跑一次
```
pip install -r requirements.txt
python screener.py          # 拉数据 → 生成 data.json / summary.txt / data/<日期>.json
```

## 换股票池 / 调参数
- **AI 增量名单**:编辑 `tickers.txt`(逗号/空格/换行分隔,支持 `1024.HK`、`600519.SS`)。这些票标 ★,与标普 500 现成分一并进入动量排名。删掉文件则只用标普 500。
- **策略参数**:编辑 `screener.py` 顶部 `CONFIG`:
  - `TOP_N` 持仓只数(默认 10)、`WATCH_EXTRA` 观察区数量
  - `LOOKBACK / SKIP` 动量窗口(默认 252 / 21,即 12-1)
  - `REB_DAY` 换仓日(每月第 N 个交易日)、`MIN_PRICE` 价格门槛、`COST_BPS` 成本假设

## ⚠️ 已知水分(做资金规划前必读)
1. 标普 500 名单为今日成分股,含成分偏差(近年才入指的票会"事后"出现在历史里)。
2. AI 名单为回看挑选(look-ahead bias);真实独家贡献只是少数标普外小票。
3. 样本期为 AI 超级牛市;合理长期预期应大幅打折,且每 3–5 年可能一次 −35%~−45% 回撤。
4. 几乎全为短期资本利得,应税账户税后打折,优先放 IRA。
5. 页面展示的是"当前"名单;真正下单只在每月第 10 个交易日收盘按名单调仓。

## 几点要知道的
- **GitHub cron 是尽力而为**:可能延迟 5–15 分钟、偶尔跳过;收盘那次排在 16:45 ET 之后,确保当日日线已定稿。
- **数据是日线**:动量是月度慢信号,盘中刷新意义不大,真正变化在每天收盘。
- 本工具仅作研究之用,不构成投资建议。
