# -*- coding: utf-8 -*-
"""
GitHub Pages 在线版 · 数据更新器
复用 tracker_app.py 的全部抓数与报表逻辑，把实时数据渲染成静态 HTML 快照，
由 GitHub Actions 每 15 分钟自动运行一次。抓不到的数据自动沿用上次快照。
"""
import os, sys, json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import tracker_app as app  # 复用原版全部逻辑（不修改原逻辑）

SITE_DIR = os.path.join(BASE, "site")
os.makedirs(SITE_DIR, exist_ok=True)
LATEST_JSON = os.path.join(SITE_DIR, "latest.json")


def load_prev():
    """读取上次快照，作为本次抓取失败时的回退数据"""
    try:
        with open(LATEST_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def seed_cache(prev):
    """把上次快照预置进 data_cache.json，供 tracker_app 失败回退使用"""
    cache = {
        "fed": prev.get("fed", []),
        "tips": prev.get("tips", {}),
        "market": prev.get("market", {}),
        "saved_at": prev.get("saved_at", ""),
    }
    try:
        with open(app.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        print("[-] 预置回退缓存失败:", e)


def health_ok(fed, tips, live):
    """关键数据健康检查：缺失则不发布，保留上次快照"""
    def num(v):
        try:
            return float(str(v).replace("%", "").replace("$", "").replace(",", ""))
        except Exception:
            return None

    fed_map = app._fed_lookup(fed)
    checks = {
        "CPI同比": num(fed_map.get("CPI同比")) is not None,
        "联邦基金利率": num(fed_map.get("联邦基金利率")) is not None,
        "10年期TIPS": isinstance(tips.get("val"), (int, float)),
        "标普500": live.get("标普500", {}).get("price") is not None,
        "纳斯达克": live.get("纳斯达克", {}).get("price") is not None,
        "黄金": live.get("黄金", {}).get("price") is not None,
        "比特币": live.get("比特币", {}).get("price") is not None,
    }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        print(f"[!] 关键数据缺失: {bad} → 本次不发布，保留上次快照")
        return False
    return True


def staticize(html, is_index=False):
    """把 Flask 动态链接替换为静态文件链接，适配 GitHub Pages"""
    html = html.replace('href="/?period=day"', 'href="report_day.html"')
    html = html.replace('href="/?period=week"', 'href="report_week.html"')
    html = html.replace('href="/?period=month"', 'href="report_month.html"')
    html = html.replace('href="/?period=quarter"', 'href="report_quarter.html"')
    html = html.replace('href="/?period=year"', 'href="report_year.html"')
    html = html.replace('href="/?module=trump"', 'href="report_trump.html"')
    html = html.replace('href="/?module=conflict"', 'href="report_conflict.html"')
    html = html.replace('href="/?module=wall"', 'href="report_wall.html"')
    html = html.replace('href="/"', 'href="index.html"')
    # 主页的“手机看盘（局域网）”提示 → 在线版提示
    html = html.replace(
        '<div class="lan">📱 手机看盘：<a href="http://pages:5000/">http://pages:5000</a>（手机与电脑连同一WiFi即可打开）</div>',
        '<div class="lan">📱 在线版 · 数据每15分钟自动更新 · 电脑/手机随时打开</div>'
    )
    if is_index:
        # 首页每10分钟自动刷新，保证打开时看到最新快照
        html = html.replace(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta http-equiv="refresh" content="600">'
        )
    return html


def main():
    prev = load_prev()
    seed_cache(prev)

    # 抓取实时数据（复用原版函数）
    fed = app.get_fed()
    tips = app.get_tips_signal()
    live = app.get_live_market()
    rp = app.get_report()

    if not health_ok(fed, tips, live):
        sys.exit(1)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("抓取完成:", now_str)

    with app.app.app_context():  # Flask 渲染需要应用上下文
        # --- 渲染主页 ---
        home = app.render_template_string(
            app.HOME_PAGE,
            fed=fed,
            tips=tips,
            rp=rp,
            trump=app.get_trump(),
            conflict=app.get_conflict(),
            wall=app.get_wall(),
            lan_ip="pages",
            now=now_str,
        )
        with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
            f.write(staticize(home, is_index=True))

        # --- 渲染各报表详情页 ---
        for period in ["day", "week", "month", "quarter", "year", "trump", "conflict", "wall"]:
            try:
                detail = app.get_detail_data(period)
                html = app.render_template_string(app.DETAIL_PAGE, detail=detail)
                with open(os.path.join(SITE_DIR, f"report_{period}.html"), "w", encoding="utf-8") as f:
                    f.write(staticize(html))
            except Exception as e:
                print(f"[-] {period} 页渲染失败: {e}")

    # --- 保存最新数据快照（供下次回退 + 展示更新时间） ---
    snapshot = {
        "saved_at": now_str,
        "fed": fed,
        "tips": tips,
        "market": {k: v for k, v in live.items() if k != "ts"},
    }
    with open(LATEST_JSON, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    # 防 Jekyll 处理
    open(os.path.join(SITE_DIR, ".nojekyll"), "w").close()

    print("静态页面生成完毕 →", SITE_DIR)


if __name__ == "__main__":
    main()
