# 📈 全能金融盯盘系统 · 在线版

自动抓取美联储利率/通胀 + 全球股市/黄金/原油/VIX/比特币等实时数据，
每 **15 分钟** 自动更新一次，通过 GitHub Actions + GitHub Pages 免费托管，
**电脑 / 手机随时打开网址即可查看最新报告**，无需本地运行。

## 🔗 访问地址

**https://zengtao1989.github.io/market-tracker-web/**

## 📅 报表内容

- 首页：美联储 + 10年期TIPS 实时数据、TIPS 顶底提醒、日/周/月/季/年综合报表入口、实时新闻
- 详情页：日报、周报、月报、季报、年报（点击主页入口进入）
- 数据源：FRED（利率/通胀）、新浪财经（指数/期货/VIX/美元指数）、Gate.io（比特币）
- 抓取失败时自动沿用上一次成功的数据，不会显示过期硬编码值

## 🛠 工作原理

1. GitHub Actions 每 15 分钟运行 `update_data.py`
2. 脚本复用 `tracker_app.py` 全部抓数与报表逻辑，渲染出静态 HTML 快照
3. 快照推送到 `gh-pages` 分支，GitHub Pages 托管展示

## 🔑 配置密钥（仓库 Secrets）

API 密钥通过 GitHub Secrets 注入，不在代码库中明文出现：

| Secret | 说明 |
|---|---|
| `FRED_API_KEY` | FRED 美联储数据 API Key |
| `NEWS_API_KEY` | 新闻 API Key（预留） |
| `TWELVE_KEY` | TwelveData Key（预留，比特币已改用 Gate.io） |

设置位置：仓库 `Settings → Secrets and variables → Actions`

## 🚀 手动刷新

仓库 `Actions → Update Market Data → Run workflow` 可立即触发一次更新。
