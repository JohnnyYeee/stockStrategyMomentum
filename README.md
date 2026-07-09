# 右侧浅回调筛选器 · 自动刷新静态站

纯静态页面 + 免费定时任务。**你不用跑任何服务器**,数据每天开盘后/收盘后自动更新。

```
index.html          展示层:读取同目录 data.json 并渲染(开着的标签页每 5 分钟自重取一次)
screener.py         筛选脚本:yfinance 拉真实行情 → 生成结果(无需 API key)
data.json           最新结构化结果(由定时任务自动覆盖)
summary.txt         最新纯文本摘要(给 AI / 人类快速读)
data/<日期>.json     每日历史归档;data/index.json 列出可用日期
tickers.txt.example 自定义股票池模板(改名为 tickers.txt 即生效)
.github/workflows/screen.yml  GitHub Actions 定时任务:开盘后+收盘后各跑一次,提交结果
```

## 给 AI / 程序读取
页面上线后,这些 URL 都是同源、可直接抓取的:
- `…/data.json` —— 结构化(日期、generated_at、每只票评分/回调深度/RSI/板块…)
- `…/summary.txt` —— 纯文本排行榜,最省事
- `…/data/<日期>.json` —— 某天的历史快照;`…/data/index.json` 给出 `latest` 和全部日期

## 为什么"纯静态也能自动刷新"
静态文件自己不会按点醒来拉数据,所以靠 **GitHub Actions 的 cron**(白嫖的免费算力)按时跑 `screener.py`,
把新的 `data.json` 提交回仓库;`index.html` 同源读取它。整条链路没有你要运维的后端。

## 部署(GitHub Pages,最省事)
1. 新建一个 GitHub 仓库,把这个文件夹整包推上去(保留 `.github/workflows/` 目录结构)。
2. 仓库 **Settings → Pages** → Source 选 `main` 分支、`/ (root)`,保存。
3. 仓库 **Settings → Actions → General** → 底部 "Workflow permissions" 选 **Read and write**(让任务能提交 data.json)。
4. **Actions** 标签页 → 选中 `refresh-pullback-screen` → "Run workflow" 手动先跑一次,确认生成新的 data.json。
5. 打开 `https://<用户名>.github.io/<仓库名>/` 即可。之后每天开盘/收盘后自动更新。

> 想用自己的域名:在仓库 Pages 里绑定自定义域名(CNAME)即可,页面与 data.json 仍同源,无跨域问题。

## 放到"我自己的网站"上
最简单是上面那样用 Pages + 自定义域名。若一定要把 `index.html` 放在别处主机:
让它读的 `data.json` 必须**同源**,否则会被浏览器跨域拦。最干净的做法是让 Action 在生成后
把 `data.json`(或整站)推送/同步到你的主机(rsync / FTP / 你的部署钩子)。

## 换股票池
把 `tickers.txt.example` 改名为 `tickers.txt`,填你的代码(美股/港股 `1024.HK`/A股 `600519.SS` 都行)。
删掉 `tickers.txt` 就回到标普500全量。

## 调策略参数
编辑 `screener.py` 顶部的 `THRESHOLDS`:
- `DEPTH_MAX` 回调浅度上限(默认 0.13;想更严 0.08,想更宽 0.18)
- `RSI_LOW / RSI_HIGH` 确认 RSI 区间
- `FROM_52W_MAX` 距52周高上限、`ADV_MIN` 流动性门槛、`EXT_EMA10_MAX` 入场偏离上限

## 几点要知道的
- **时区/DST**:workflow 里排了 4 个 cron,夏令时(EDT)和冬令时(EST)的开盘+收盘都覆盖了。
- **GitHub cron 是尽力而为**:可能延迟 5–15 分钟、偶尔跳过;所以收盘那次排在 16:30 ET 之后,确保当日日线已定稿。
- **数据是日线**:盘中频繁刷新意义不大,真正变化在每天收盘。要盘中实时得换成日内分钟级策略(另一套)。
- 本工具仅作技术筛选与研究,不构成投资建议。
