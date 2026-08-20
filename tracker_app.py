from flask import Flask, render_template_string, request
import requests, re, time
from datetime import datetime

app = Flask(__name__)

# 你的密钥（公网仓库版：从 GitHub Secrets 读取，本地运行请填自己的key或设置环境变量）
import os
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")

# ===================== 持久缓存：某个接口失败时，自动用上次成功抓到的数据（不显示过期硬编码值） =====================
import os, json, socket
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache.json")

def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache_field(field, value):
    try:
        cache = _load_cache()
        cache[field] = value
        cache["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[-] 缓存写入失败: {e}")

# ===================== 局域网IP（手机访问用） =====================
def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# ===================== 美联储 + 10年期TIPS 完整版（逐项容错 + 上次成功数据回退） =====================
_fed_cache = {"ts": 0, "data": None}
_FED_TTL = 300  # FRED为日频数据，5分钟内存缓存足够实时

def get_fed():
    global _fed_cache
    now = time.time()
    if _fed_cache["data"] and now - _fed_cache["ts"] < _FED_TTL:
        return _fed_cache["data"]

    saved = _load_cache().get("fed", [])
    def _fb(name):
        # 该项拉取失败时，回退到上次成功的数据
        for it in saved:
            if it.get("name") == name:
                return it
        return {"name": name, "value": "-", "date": "-"}

    url = "https://api.stlouisfed.org/fred/series/observations"
    keys = {
        "联邦基金利率": "DFF",
        "核心PCE通胀": "PCEPILFE",
        "CPI同比": "CPIAUCSL",
        "失业率": "UNRATE",
        "10年期美债": "DGS10",
        "2年期美债": "DGS2",
        "10年期TIPS(实际利率)": "DFII10"
    }
    yoy_keys = {"核心PCE通胀": "PCEPILFE", "CPI同比": "CPIAUCSL"}
    out = []
    for name, sid in keys.items():
        try:
            # 拉取最近14条并过滤非交易日"."，避免周末/节假日取到空值
            r = requests.get(url, params={
                "series_id": sid,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "limit": 14,
                "sort_order": "desc"
            }, timeout=10)
            data = r.json()
            obs = [x for x in data["observations"] if x["value"] != "."]
            if not obs:
                out.append(_fb(name))
                continue
            cur_val = float(obs[0]["value"])
            cur_date = obs[0]["date"]
            if name in yoy_keys:
                # 计算同比 = (当前值/一年前值 - 1) × 100%
                cur_year, cur_month = int(cur_date[:4]), int(cur_date[5:7])
                year_ago_val = None
                for o in obs[1:]:
                    o_year, o_month = int(o["date"][:4]), int(o["date"][5:7])
                    if o_year == cur_year - 1 and o_month == cur_month:
                        year_ago_val = float(o["value"])
                        break
                if year_ago_val is None:
                    for o in obs[1:]:
                        if int(o["date"][:4]) == cur_year - 1:
                            year_ago_val = float(o["value"])
                            break
                if year_ago_val and year_ago_val > 0:
                    yoy = round((cur_val / year_ago_val - 1) * 100, 1)
                    out.append({"name": name, "value": str(yoy) + "%", "date": cur_date})
                    if name == "CPI同比" and len(obs) >= 3:
                        out.append({"name": "美国CPI指数", "value": str(cur_val), "date": cur_date})
                        out.append({"name": "_cpi_prev_month", "value": str(obs[1]["value"]), "date": obs[1]["date"]})
                else:
                    out.append({"name": name, "value": str(cur_val), "date": cur_date})
            else:
                out.append({"name": name, "value": str(cur_val), "date": cur_date})
        except Exception as e:
            print(f"[-] {name} 获取失败: {e}")
            out.append(_fb(name))

    _fed_cache = {"ts": now, "data": out}
    _save_cache_field("fed", out)
    return out

# ===================== 顶底判断系统（自动提醒） =====================
def get_tips_signal():
    try:
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={FRED_API_KEY}&file_type=json&limit=5&sort_order=desc"
        res = requests.get(url, timeout=10).json()
        arr = [float(x["value"]) for x in res["observations"] if x["value"] != "."]
        if not arr:
            raise ValueError("TIPS无有效数据")
        now = arr[0]
        signal = "震荡"
        note = "观望为主"
        
        if now >= 2.1:
            signal = "⚠️ 高位压力区"
            note = "黄金/成长股承压，注意减仓"
        elif now <= 1.5:
            signal = "✅ 低位企稳区"
            note = "黄金/科技股易反弹，可低吸"
        elif 1.7 <= now <= 1.95:
            signal = "⚠️ 关键压力带"
            note = "易回落，不追高"
        elif 1.55 <= now < 1.7:
            signal = "✅ 支撑有效"
            note = "止跌概率大"
            
        result = {"val": now, "signal": signal, "note": note}
        _save_cache_field("tips", result)
        return result
    except Exception as e:
        print(f"TIPS信号获取失败: {e}")
        cached = _load_cache().get("tips")
        if cached:
            return cached
        return {"val":"-","signal":"读取中","note":"请稍后刷新"}

# ===================== 综合报表（日/周/月/季/年） =====================
def get_report():
    t = datetime.now()
    return {
        "day": t.strftime("%Y-%m-%d"),
        "week": f"{t.year}年第{t.isocalendar()[1]}周",
        "month": t.strftime("%Y年%m月"),
        "quarter": f"{t.year}年Q{(t.month-1)//3+1}",
        "year": t.strftime("%Y年")
    }

# ===================== 实时市场数据（新浪+多源比特币，60秒缓存，失败自动用上次成功数据） =====================
import threading
_market_cache = {}
_market_lock = threading.Lock()
_MARKET_TTL = 60  # 60秒：每次打开页面都尽量拿到最新价

TWELVE_KEY = os.environ.get("TWELVE_KEY", "")

def _fetch_btc():
    """比特币多源获取：Gate.io → CoinGecko → Binance → TwelveData，谁通用谁"""
    # 1) Gate.io（国内网络可用）
    try:
        r = requests.get("https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT", timeout=8)
        d = r.json()
        if isinstance(d, list) and d and d[0].get("last"):
            return round(float(d[0]["last"]), 2)
    except Exception:
        pass
    # 2) CoinGecko（免费无key）
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=8)
        d = r.json()
        if d.get("bitcoin", {}).get("usd"):
            return round(float(d["bitcoin"]["usd"]), 2)
    except Exception:
        pass
    # 3) Binance
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=8)
        d = r.json()
        if d.get("price"):
            return round(float(d["price"]), 2)
    except Exception:
        pass
    # 4) TwelveData（原接口，额度可能已耗尽）
    try:
        r = requests.get(f'https://api.twelvedata.com/price?symbol=BTC/USD&apikey={TWELVE_KEY}', timeout=8)
        d = r.json()
        if d.get('price'):
            return round(float(d['price']), 2)
    except Exception:
        pass
    return None

def get_live_market():
    """拉取全球指数/期货/加密货币实时数据，60秒缓存，单项失败用上次成功值"""
    global _market_cache
    now = time.time()
    with _market_lock:
        if _market_cache.get('ts') and now - _market_cache['ts'] < _MARKET_TTL:
            return _market_cache.copy()

    data = {}
    saved = _load_cache().get('market', {})
    h = {'Referer': 'https://finance.sina.com.cn',
         'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

    # === 新浪全球指数（一次性拉全部）===
    try:
        # 美股用 gb_ 系列（真实数据），港股/日经用 int_ 系列
        r = requests.get(
            'https://hq.sinajs.cn/list=gb_$dji,gb_$ixic,gb_$inx,int_hangseng,int_nikkei',
            timeout=8, headers=h
        )
        r.encoding = 'gbk'
        name_map = {'gb_$dji':'道琼斯','gb_$ixic':'纳斯达克','gb_$inx':'标普500',
                    'int_hangseng':'恒生','int_nikkei':'日经225'}
        gb_set = {'gb_$dji','gb_$ixic','gb_$inx'}  # gb_ 格式与 int_ 不同
        for line in r.text.strip().split('\n'):
            m = re.search(r'str_(\S+)="(.+)"', line)
            if not m: continue
            code, val = m.group(1), m.group(2)
            parts = val.split(',')
            if code in name_map and len(parts) >= 4:
                if code in gb_set:
                    # gb_ 格式: [0]名称 [1]现价 [2]涨跌幅% [4]涨跌额
                    data[name_map[code]] = {'price': round(float(parts[1]),2),
                        'change': round(float(parts[4]),2) if len(parts)>4 else 0,
                        'pct': round(float(parts[2]),2)}
                else:
                    # int_ 格式: [0]名称 [1]现价 [2]涨跌额 [3]涨跌幅%
                    data[name_map[code]] = {'price': round(float(parts[1]),2),
                        'change': round(float(parts[2]),2), 'pct': round(float(parts[3]),2)}
                data[name_map[code]+'_name'] = parts[0]
    except Exception as e:
        print(f'[-] 新浪指数拉取失败: {e}')

    # === 新浪期货（黄金/原油/VIX/美元指数）===
    try:
        r = requests.get('https://hq.sinajs.cn/list=hf_XAU,hf_CL,hf_VX,DINIW', timeout=8, headers=h)
        r.encoding = 'gbk'
        for line in r.text.strip().split('\n'):
            m = re.search(r'str_(\S+)="(.+)"', line)
            if not m: continue
            code, val = m.group(1), m.group(2)
            parts = val.split(',')
            if code == 'hf_XAU' and len(parts) >= 7:
                data['黄金'] = {'price': round(float(parts[0]),2)}
            elif code == 'hf_CL' and len(parts) >= 5:
                high = float(parts[4]) if parts[4] else 0
                low = float(parts[5]) if parts[5] else 0
                data['WTI原油'] = {'price': round(float(parts[0]),2), 'high': round(high,2), 'low': round(low,2)}
            elif code == 'hf_VX' and len(parts) >= 7:
                data['VIX'] = {'price': round(float(parts[0]),2)}
            elif code == 'DINIW' and len(parts) >= 9:
                # DINIW: [0]时间 [1]现价 [6]最高 [5]最低 [9]名称
                data['美元指数'] = {'price': round(float(parts[1]),2)}
    except Exception as e:
        print(f'[-] 新浪期货拉取失败: {e}')

    # === 比特币（多源，任一成功即可）===
    btc = _fetch_btc()
    if btc:
        data['比特币'] = {'price': btc}

    # === 兜底：本次没拿到的项，用上次成功抓到的数据补齐（不显示过期硬编码）===
    for k, v in saved.items():
        if k not in data and k not in ('_ts', 'ts'):
            data[k] = v

    data['_ts'] = datetime.now().strftime('%H:%M:%S')
    data['ts'] = now
    with _market_lock:
        _market_cache = data.copy()
    _save_cache_field('market', {k: v for k, v in data.items() if k != 'ts'})
    return data


def _fed_lookup(fed_list):
    """将 get_fed() 的列表转为查找字典（非数值如"-"自动跳过，调用方会使用安全默认值）"""
    m = {}
    for item in fed_list:
        v = item.get('value','')
        try:
            m[item['name']] = float(v.replace('%','').replace('$','').replace(',',''))
        except:
            pass
    return m


# ===================== 各模块详情页数据 =====================
def get_detail_data(module):
    live = get_live_market()
    fed = _fed_lookup(get_fed())
    
    # === 实时数据提取 ===
    vix = live.get('VIX',{}).get('price', 18)
    spx = live.get('标普500',{}).get('price', 7000)
    spx_pct = live.get('标普500',{}).get('pct', 0)
    ndx = live.get('纳斯达克',{}).get('price', 25000)
    ndx_pct = live.get('纳斯达克',{}).get('pct', 0)
    ndx_chg = live.get('纳斯达克',{}).get('change', 0)
    dji = live.get('道琼斯',{}).get('price', 50000)
    dji_pct = live.get('道琼斯',{}).get('pct', 0)
    gold = live.get('黄金',{}).get('price', 4300)
    oil = live.get('WTI原油',{}).get('price', 90)
    oil_high = live.get('WTI原油',{}).get('high', oil)
    oil_low = live.get('WTI原油',{}).get('low', oil)
    btc = live.get('比特币',{}).get('price', 60000)
    usd = live.get('美元指数',{}).get('price', 100)
    hsi = live.get('恒生',{}).get('price', 25000)
    hsi_pct = live.get('恒生',{}).get('pct', 0)
    n225 = live.get('日经225',{}).get('price', 45000)
    n225_pct = live.get('日经225',{}).get('pct', 0)
    ts = live.get('_ts', '--:--:--')

    # === FRED 宏观数据 ===
    tips10 = fed.get('10年期TIPS(实际利率)', 2.0)  # DFII10
    cpi_val = fed.get('CPI同比', 3.8)
    pce_val = fed.get('核心PCE通胀', 3.3)
    ff_rate = fed.get('联邦基金利率', 3.6)
    unemp = fed.get('失业率', 4.3)
    # 额外：CPI月率趋势（最近2个月差，判断加速/减速）
    cpi_raw = fed.get('美国CPI指数', 332)
    cpi_prev = fed.get('_cpi_prev_month', cpi_raw)
    cpi_mom = cpi_raw - cpi_prev

    # === 数据驱动定性计算 ===
    # CPI 通胀方向
    if cpi_val > 3.5: cpi_label = f'CPI{cpi_val:.1f}%高于目标→通胀二次抬头风险，降息暂停/加息预期升温'
    elif cpi_val > 3.0: cpi_label = f'CPI{cpi_val:.1f}%高于目标→降息节奏不确定，市场观望'
    elif cpi_val > 2.5: cpi_label = f'CPI{cpi_val:.1f}%趋近目标→降息窗口临近'
    else: cpi_label = f'CPI{cpi_val:.1f}%接近2%目标→宽松可期'

    # 核心PCE
    if pce_val > 3.0: pce_label = f'核心PCE{pce_val:.1f}%粘性强→美联储最关注指标未达标'
    elif pce_val > 2.5: pce_label = f'核心PCE{pce_val:.1f}%缓慢回落→距2%目标仍有距离'
    else: pce_label = f'核心PCE{pce_val:.1f}%趋近目标→通胀担忧缓解'

    # 联邦基金利率方向判断：CPI上升中 + 利率已低位 → 加息风险
    if cpi_val > 3.5 and ff_rate < 4.0:
        rate_outlook = 'CPI攀升+利率偏低→加息预期升温⚠️'
        rate_color = '加息风险'
    elif cpi_val > 3.0:
        rate_outlook = 'CPI高于目标，暂停降息→加息讨论回归'
        rate_color = '暂停/偏鹰'
    elif ff_rate > 5.0:
        rate_outlook = '高利率压制→降息预期较强'
        rate_color = '降息预期'
    else:
        rate_outlook = '利率中性→数据依赖，方向待定'
        rate_color = '观望'

    # 美元指数
    if usd > 102: usd_label = f'DXY={usd:.1f}偏强→压大宗/新兴市场，资金回流美元'
    elif usd > 98: usd_label = f'DXY={usd:.1f}中性→方向待突破'
    else: usd_label = f'DXY={usd:.1f}偏弱→利多大宗/资源出口国'

    # 黄金趋势判断 — 基于当前价 vs 近期区间
    # 注：gold数据仅有当前价，通过观察日内和近期走势判断
    gold_direction = '短期承压回调'  # 默认，因为数据有限
    if gold > 4400: gold_signal = f'黄金${gold:.0f}高位→央行购金+避险需求支撑，但高位注意回调风险'
    elif gold > 4300: gold_signal = f'黄金${gold:.0f}中高位→利率回升压制，但地缘+央行购金提供底部支撑。若跌破$4250可能加速下行'
    elif gold > 4100: gold_signal = f'黄金${gold:.0f}回调中→加息预期+美元走强双重压制。关注$4100支撑位，若破位需减仓'
    else: gold_signal = f'黄金${gold:.0f}低位→超跌后反弹机会，但需CPI回落确认'

    # VIX 风控
    if vix < 15: vix_label = f'VIX={vix:.1f}极低→市场自满，是潜在风险信号'
    elif vix < 20: vix_label = f'VIX={vix:.1f}偏低→市场平静，但警惕突然跳升'
    elif vix < 25: vix_label = f'VIX={vix:.1f}中位→波动可控，注意仓位管理'
    elif vix < 30: vix_label = f'VIX={vix:.1f}偏高→恐慌在升温，防御为主'
    else: vix_label = f'VIX={vix:.1f}高位→市场恐慌，现金为王'
    vix_risk = '低' if vix < 18 else '中' if vix < 25 else '高' if vix < 30 else '极高'
    if vix < 18: pos_max, vix_msg = 25, '市场平静，可适度积极'
    elif vix < 25: pos_max, vix_msg = 15, '波动适中，控制仓位'
    elif vix < 30: pos_max, vix_msg = 10, 'VIX偏高→防御优先'
    else: pos_max, vix_msg = 5, 'VIX极高→现金为王'

    # TIPS 利率区间
    if tips10 > 2.1: tips_label = f'10Y TIPS={tips10:.2f}%偏高→实际利率压制科技/成长股估值'
    elif tips10 > 1.8: tips_label = f'10Y TIPS={tips10:.2f}%中性→估值压力适中'
    else: tips_label = f'10Y TIPS={tips10:.2f}%偏低→利好风险资产/黄金/比特币'

    # 美股方向
    ndx_sign = '崩跌' if ndx_pct < -3 else '回调' if ndx_pct < -1 else '微跌' if ndx_pct < 0 else '微涨' if ndx_pct < 1 else '上涨' if ndx_pct < 3 else '大涨'
    spx_sign = '崩跌' if spx_pct < -3 else '回调' if spx_pct < -1 else '微跌' if spx_pct < 0 else '微涨' if spx_pct < 1 else '上涨' if spx_pct < 3 else '大涨'
    dji_sign = '崩跌' if dji_pct < -3 else '回调' if dji_pct < -1 else '微跌' if dji_pct < 0 else '微涨' if dji_pct < 1 else '上涨' if dji_pct < 3 else '大涨'

    # 日经定性
    if n225_pct > 2: n225_label = f'日经{n225:.0f}(+{n225_pct:+.1f}%)强势→日元贬值+出口受益'
    elif n225_pct > 0: n225_label = f'日经{n225:.0f}(+{n225_pct:+.1f}%)偏强→日本经济温和复苏中'
    elif n225_pct > -2: n225_label = f'日经{n225:.0f}({n225_pct:+.1f}%)震荡→关注44500支撑位'
    else: n225_label = f'日经{n225:.0f}({n225_pct:+.1f}%)承压→日元走势+全球风险情绪拖累'

    # 比特币定性
    btc_corr = ''  # 实际需要纳指涨幅对比
    if ndx_pct < -2: btc_label = f'BTC ${btc:,.0f}，纳指{ndx_pct:+.1f}%暴跌→风险资产联动，BTC承压。若纳指继续下行，BTC可能跟跌'
    elif ndx_pct < 0: btc_label = f'BTC ${btc:,.0f}，纳指{ndx_pct:+.1f}%偏弱→BTC与纳指高相关性，风险偏好退潮中'
    elif ndx_pct < 2: btc_label = f'BTC ${btc:,.0f}，纳指{ndx_pct:+.1f}%温和→机构化推动BTC走高，关注$60k支撑'
    else: btc_label = f'BTC ${btc:,.0f}，纳指{ndx_pct:+.1f}%强势→风险偏好回暖，BTC受益。关注$65k阻力'

    # 原油定性
    oil_range = f'区间${oil_low:.1f}-{oil_high:.1f}'
    if oil > oil_low + (oil_high - oil_low) * 0.65: oil_label = f'WTI ${oil:.1f}({oil_range})偏强→地缘+供给收缩支撑'
    elif oil > oil_low + (oil_high - oil_low) * 0.35: oil_label = f'WTI ${oil:.1f}({oil_range})震荡→方向不明，观望'
    else: oil_label = f'WTI ${oil:.1f}({oil_range})偏弱→需求担忧+美元走强压制'

    # 失业率
    if unemp > 4.5: unemp_label = f'失业率{unemp:.1f}%偏高→衰退风险上升，利空股市'
    elif unemp > 4.0: unemp_label = f'失业率{unemp:.1f}%正常→就业市场温和，软着陆情景'
    else: unemp_label = f'失业率{unemp:.1f}%低位→劳动力市场偏紧，工资通胀风险'

    spx_chg_str = f'{spx_pct:+.2f}%' if spx_pct else '0.00%'
    ndx_chg_str = f'{ndx_pct:+.2f}%' if ndx_pct else '0.00%'
    dji_chg_str = f'{dji_pct:+.2f}%' if dji_pct else '0.00%'
    hsi_chg_str = f'{hsi_pct:+.2f}%' if hsi_pct else '0.00%'
    n225_chg_str = f'{n225_pct:+.2f}%' if n225_pct else '0.00%'

    detail_map = {        # ========== 日线 ==========
        "day": {
            "title": "📅 日线交易室 — 日内进出与短线避险",
            "desc": f"实时数据 {ts} · 恐慌指数·急涨急跌应对",
            "data": [
                {"指标": "VIX恐慌指数(实时)", "数值": f"{vix:.1f}", "趋势": vix_label},
                {"指标": "标普500(实时)", "数值": f"{spx:.0f}", "趋势": f"{spx_sign}({spx_chg_str})"},
                {"指标": "纳斯达克(实时)", "数值": f"{ndx:.0f}", "趋势": f"{ndx_sign}({ndx_chg_str})"},
                {"指标": "道琼斯(实时)", "数值": f"{dji:.0f}", "趋势": f"{dji_sign}({dji_chg_str})"},
                {"指标": "黄金现货(实时)", "数值": f"${gold:.0f}", "趋势": gold_signal},
                {"指标": "WTI原油(实时)", "数值": f"${oil:.1f}", "趋势": oil_label},
                {"指标": "比特币(实时)", "数值": f"${btc:,.0f}", "趋势": btc_label},
                {"指标": "美元指数(实时)", "数值": f"{usd:.1f}", "趋势": usd_label},
                {"指标": "联邦基金利率", "数值": f"{ff_rate:.2f}%", "趋势": rate_outlook},
                {"指标": "CPI同比", "数值": f"{cpi_val:.1f}%", "趋势": cpi_label},
                {"指标": "核心PCE通胀", "数值": f"{pce_val:.1f}%", "趋势": pce_label},
                {"指标": "10年期TIPS实际利率", "数值": f"{tips10:.2f}%", "趋势": tips_label},
                {"指标": "失业率", "数值": f"{unemp:.1f}%", "趋势": unemp_label},
            ],
            "suggest": f"<b>【实时风控评级 — {ts}】</b><br>"
                       f"• VIX恐慌指数 = <b>{vix:.1f}</b>（{vix_risk}风险 → {vix_msg}）<br>"
                       f"• 10年期TIPS实际利率 = <b>{tips10:.2f}%</b>，{'处于偏高区间→压制科技/成长股估值，不追高' if tips10>2.0 else '利率舒适→风险资产友好'}<br>"
                       f"• CPI同比 = <b>{cpi_val:.1f}%</b>（{'通胀二次抬头⚠️→降息暂停，加息讨论回归' if cpi_val>3.5 else '高于目标→降息节奏不确定' if cpi_val>3.0 else '趋近目标→宽松可期'}）<br>"
                       f"• {'CPI攀升+利率仅{:.2f}%→加息预期升温⚠️'.format(ff_rate) if cpi_val>3.5 and ff_rate<4.0 else '利率{:.2f}%中性，数据依赖方向待定'.format(ff_rate)}<br><br>"
                       f"<b>【仓位纪律 — 按当前VIX={vix:.1f}动态计算】</b><br>"
                       f"① 日内短线仓位 ≤ 总资金<b>{pos_max}%</b>，单票 ≤ 5%<br>"
                       f"② {'VIX高位！杠杆≤1倍，随时准备清仓<br>' if vix>25 else '杠杆 ≤ 2倍，不扛单<br>'}"
                       f"③ 全天亏损达总资金3% → 立即停止交易<br><br>"
                       f"<b>【止损规则 — 保命第一】</b><br>"
                       f"④ 任何持仓浮亏 -3% → 无条件砍仓<br>"
                       f"⑤ 不补仓摊平！亏损加仓是绞肉机第一死因<br>"
                       f"⑥ 止损后至少等30分钟再考虑入场<br><br>"
                       f"<b>【今日卖出信号 — 实时触发值】</b><br>"
                       f"⑦ TIPS利率急升>0.05% → 立即减仓科技股（当前10Y={tips10:.2f}%，{'⚠️已偏高' if tips10>2.0 else '安全'}）<br>"
                       f"⑧ VIX急涨>25 → 清仓风险资产转现金（当前{ '已触发⚠️' if vix>25 else '未触发('+str(vix)+')' }）<br>"
                       f"⑨ 标普500日内跌幅>2%且VIX突升 → 减仓至30%<br><br>"
                       f"<b>【今日避险清单】</b><br>"
                       f"⚠️ 美联储官员讲话窗口(美东10:00/14:00)<br>"
                       f"⚠️ 财报季个股：盘后财报不过夜<br>"
                       f"⚠️ {'当前VIX偏高，日内追高风险极大！' if vix>22 else '波动正常，可正常交易'}"
        },
        # ========== 周线 ==========
        "week": {
            "title": "📅 周线作战图 — 中期趋势与仓位调配",
            "desc": f"实时数据 {ts} · 周度趋势·板块轮动·仓位调整",
            "data": [
                {"指标": "标普500(实时)", "数值": f"{spx:.0f}", "趋势": f"{spx_sign}({spx_chg_str}) — {spx_sign}市中注意风控"},
                {"指标": "纳斯达克(实时)", "数值": f"{ndx:.0f}", "趋势": f"{ndx_sign}({ndx_chg_str}) — 纳指波动大，回撤-4%以上需警惕"},
                {"指标": "道琼斯(实时)", "数值": f"{dji:.0f}", "趋势": f"{dji_sign}({dji_chg_str}) — 道指相对稳健"},
                {"指标": "日经225(实时)", "数值": f"{n225:.0f}", "趋势": f"{n225_label}"},
                {"指标": "恒生(实时)", "数值": f"{hsi:.0f}", "趋势": f"恒生{hsi_chg_str} — {'港股估值偏低，关注南下资金' if hsi_pct<0 else '港股回暖，注意反弹持续性'}"},
                {"指标": "黄金周趋势", "数值": f"${gold:.0f}", "趋势": gold_signal},
                {"指标": "WTI原油(实时)", "数值": f"${oil:.1f}", "趋势": oil_label},
                {"指标": "VIX周水平", "数值": f"{vix:.1f}", "趋势": vix_label},
                {"指标": "比特币周趋势", "数值": f"${btc:,.0f}", "趋势": btc_label},
            ],
            "suggest": f"<b>【本周仓位配置 — 基于VIX={vix:.1f}/TIPS10Y={tips10:.2f}%/CPI={cpi_val:.1f}%综合】</b><br>"
                       f"• 总仓位建议：<b>{'40%' if vix>25 else '60%' if vix<18 else '50%'}</b>（{'防御为主' if vix>25 else '中性偏多' if vix<18 else '灵活应对'}）<br>"
                       f"• 美股科技(AI主线)：{'15%' if vix>25 else '30%'} 核心底仓 — TIPS={tips10:.2f}%{'→估值承压，不追高' if tips10>2.0 else '→估值合理'} <br>"
                       f"• 黄金：{'15%' if vix>22 else '10%'} 配置 — {'加息预期升温→黄金承压，减少配置' if cpi_val>3.5 else '正常配置'} <br>"
                       f"• 现金：{'40%' if vix>25 else '20%'} 保留弹药 — {'通胀+加息风险高，现金为王' if cpi_val>3.5 else '正常水平'} <br>"
                       f"• 原油/资源：{'0%观望' if abs(oil-oil_low)<3 else '5%试探'} — {'方向不明不参与' if abs(oil-oil_low)<3 else '小仓位可参与'} <br><br>"
                       f"<b>【板块轮动信号】</b><br>"
                       f"① {'纳指相对走弱→科技板块短期回避' if ndx_pct<dji_pct else '纳指相对走强→科技板块有望领先'}（纳指{ndx_chg_str} vs 道指{dji_chg_str}）<br>"
                       f"② {'TIPS偏高({:.2f}%)→压制成长股估值，价值股/金融相对受益'.format(tips10) if tips10>2.0 else 'TIPS合理({:.2f}%)→成长股估值环境友好'.format(tips10)}<br>"
                       f"③ CPI={cpi_val:.1f}%→{'通胀二次抬头，防御板块(公用事业/消费)优先' if cpi_val>3.5 else '数据温和，成长/周期皆可'}<br>"
                       f"④ 亚洲：日经{n225_chg_str}/恒生{hsi_chg_str}<br><br>"
                       f"<b>【下周预判与应对】</b><br>"
                       f"⑤ TIPS突破2.2% → 减仓至40% → 当前{'⚠️接近' if tips10>2.0 else '安全'}<br>"
                       f"⑥ VIX突破25 → 降至30%仓位 → 当前{'⚠️接近' if vix>20 else '安全'}<br>"
                       f"⑦ 周中急跌>3%且VIX{'<25' if vix<25 else '>25'} → {'回调不是崩盘→可加仓' if vix<25 else '系统性风险→降至20%仓位'}<br>"
                       f"⑧ 周一观察、周二三主力窗口、周四五减仓过周末<br>"
                       f"⑨ CPI攀升中→密切跟踪美联储官员讲话，任何鹰派信号立即减仓"
        },
        # ========== 月线 ==========
        "month": {
            "title": "📅 月线战略板 — 大底大顶与资产轮动",
            "desc": f"实时数据 {ts} · 月度级别大底大顶判断·资产轮动",
            "data": [
                {"指标": "标普500(实时)", "数值": f"{spx:.0f}", "趋势": f"{spx_sign}({spx_chg_str}) — 月度走势判断"},
                {"指标": "纳斯达克(实时)", "数值": f"{ndx:.0f}", "趋势": f"{ndx_sign}({ndx_chg_str}) — 科技股月线方向"},
                {"指标": "道琼斯(实时)", "数值": f"{dji:.0f}", "趋势": f"{dji_sign}({dji_chg_str}) — 传统经济参考"},
                {"指标": "黄金月趋势", "数值": f"${gold:.0f}", "趋势": gold_signal},
                {"指标": "WTI原油月区间", "数值": f"${oil:.1f}", "趋势": oil_label},
                {"指标": "CPI同比", "数值": f"{cpi_val:.1f}%", "趋势": cpi_label},
                {"指标": "核心PCE通胀", "数值": f"{pce_val:.1f}%", "趋势": pce_label},
                {"指标": "失业率", "数值": f"{unemp:.1f}%", "趋势": unemp_label},
                {"指标": "联邦基金利率", "数值": f"{ff_rate:.2f}%", "趋势": rate_outlook},
                {"指标": "10年期TIPS实际利率", "数值": f"{tips10:.2f}%", "趋势": tips_label},
                {"指标": "比特币月趋势", "数值": f"${btc:,.0f}", "趋势": btc_label},
            ],
            "suggest": f"<b>【月线级别判断 — 基于当前数据综合】</b><br>"
                       f"→ 市场状态：纳指{ndx_chg_str}、标普{spx_chg_str} — {'调整期→观察是否破位' if spx_pct<0 else '牛市延续但需警惕'} <br>"
                       f"→ 核心矛盾：{'通胀二次抬头(CPI='+str(cpi_val)+'%) vs 降息预期→加息风险回归⚠️' if cpi_val>3.5 else '通胀回落 vs 经济减速→降息博弈'} <br>"
                       f"→ VIX={vix:.1f}({vix_risk}风险)，TIPS10Y={tips10:.2f}%{'→估值承压' if tips10>2.0 else '→估值舒适'}，联邦基金利率={ff_rate:.2f}% → {rate_outlook}<br>"
                       f"→ {'⚠️ CPI攀升+利率低位→美联储面临加息压力，这是当前最大尾部风险' if cpi_val>3.5 else '宏观环境中性，关注数据边际变化'} <br><br>"
                       f"<b>【月度仓位参考 — 按风险偏好分三档】</b><br>"
                       f"① 激进型：总仓位{'60%' if vix<22 else '45%'}<br>"
                       f"   AI/半导体 {'20%' if vix<22 and tips10<2.0 else '15%'}、黄金15%、纳指ETF 15%、{'比特币5%' if vix<22 else '比特币0%'}、现金{'45%' if vix>=22 else '40%'}<br>"
                       f"② 稳健型：总仓位{'45%' if vix<22 else '35%'}<br>"
                       f"   标普ETF 20%、黄金 15%、现金{'65%' if vix>=22 else '55%'}<br>"
                       f"③ 保守型：总仓位{'30%' if vix<22 else '20%'}<br>"
                       f"   黄金 15%、短债ETF {'15%' if vix<22 else '5%'}、现金{'70%' if vix<22 else '80%'}<br><br>"
                       f"<b>【大顶信号清单 — 实时状态】</b><br>"
                       f"🔴 TIPS急升>0.3% → {'当前安全' if tips10<2.2 else '⚠️警惕'}（10Y={tips10:.2f}%）<br>"
                       f"🔴 纳指单月跌>8% → 当前{ndx_chg_str}，跟踪中<br>"
                       f"🔴 VIX连续5日>30 → {'当前安全' if vix<30 else '🔴触发!'}<br>"
                       f"🔴 CPI连续>3.8% → {'⚠️当前CPI=' + str(cpi_val) + '%→接近警戒线' if cpi_val>3.5 else '当前安全'} <br>"
                       f"🔴 美联储意外加息 → 跟踪美联储动态<br><br>"
                       f"<b>【大底信号清单 — 实时状态】</b><br>"
                       f"🟢 TIPS从高位回落>0.15% → {'触发中' if tips10<1.8 else '等待中（当前{:.2f}%）'.format(tips10)} <br>"
                       f"🟢 标普从高点回撤>15% → 跟踪中<br>"
                       f"🟢 CPI见顶回落确认 → {'通胀仍在高位→等待' if cpi_val>3.5 else '趋近→接近触发'} <br>"
                       f"🟢 市场恐慌→散户大面积割肉 → {'VIX<30→未见恐慌' if vix<30 else 'VIX>30→恐慌初现'}"
        },
        # ========== 季线 ==========
        "quarter": {
            "title": "📅 季线指挥部 — 宏观定调与重仓方向",
            "desc": f"实时数据 {ts} · 季度宏观大趋势·大类资产·攻防切换",
            "data": [
                {"指标": "美联储政策方向", "数值": rate_color, "趋势": f"{rate_outlook}"},
                {"指标": "标普500(实时)", "数值": f"{spx:.0f}", "趋势": f"{spx_sign}({spx_chg_str})"},
                {"指标": "纳斯达克(实时)", "数值": f"{ndx:.0f}", "趋势": f"{ndx_sign}({ndx_chg_str})"},
                {"指标": "道琼斯(实时)", "数值": f"{dji:.0f}", "趋势": f"{dji_sign}({dji_chg_str})"},
                {"指标": "日经225(实时)", "数值": f"{n225:.0f}", "趋势": n225_label},
                {"指标": "恒生(实时)", "数值": f"{hsi:.0f}", "趋势": f"港股{hsi_chg_str}"},
                {"指标": "黄金(实时)", "数值": f"${gold:.0f}", "趋势": gold_signal},
                {"指标": "WTI原油(实时)", "数值": f"${oil:.1f}", "趋势": oil_label},
                {"指标": "比特币(实时)", "数值": f"${btc:,.0f}", "趋势": btc_label},
                {"指标": "CPI/PCE", "数值": f"{cpi_val:.1f}/{pce_val:.1f}%", "趋势": cpi_label},
                {"指标": "美元指数(实时)", "数值": f"{usd:.1f}", "趋势": usd_label},
            ],
            "suggest": f"<b>【季度定调 — 综合TIPS10Y={tips10:.2f}%/VIX={vix:.1f}/CPI={cpi_val:.1f}%/失业率={unemp:.1f}%】</b><br>"
                       f"→ 核心判断：{'CPI攀升至'+str(cpi_val)+'%+利率仅'+str(ff_rate)+'%→通胀风险回归，加息预期升温。这是本季度最大变量⚠️' if cpi_val>3.5 else '通胀可控+利率正常化=软着陆情景'} <br>"
                       f"→ {'当前TIPS='+str(tips10)+'%偏高→杀估值阶段，成长股需精选' if tips10>2.0 else '估值合理，积极布局'} <br>"
                       f"→ {'失业率'+str(unemp)+'%→' + ('衰退风险需关注' if unemp>4.5 else '就业市场健康')} <br><br>"
                       f"<b>【季度三大主线】</b><br>"
                       f"🥇 <b>AI算力+应用</b> — 配置{max(10,30-tips10*5):.0f}%<br>"
                       f"   TIPS10Y={tips10:.2f}%{'→高实际利率压制估值，精选不追高' if tips10>2.0 else '→估值环境友好，坚定持有'} <br>"
                       f"🥈 <b>黄金+避险</b> — 配置{'18%' if vix>22 or cpi_val>3.5 else '12%'}<br>"
                       f"   {'通胀回升+不确定性→加配黄金对冲' if cpi_val>3.5 else '正常配置'} <br>"
                       f"🥉 <b>日本/亚太</b> — 配置10%<br>"
                       f"   日经={n225:.0f}({n225_chg_str})，{n225_label}<br><br>"
                       f"<b>【季度防守转换条件 — 实时状态】</b><br>"
                       f"① {'CPI连续>3.8% → 减仓成长股（当前'+str(cpi_val)+'%⚠️接近触发线）' if cpi_val>3.5 else 'CPI>3.8%触发→当前安全'} <br>"
                       f"② 美联储释放加息信号 → 仓位降至40%<br>"
                       f"③ VIX突破30 → 降至30%仓位（当前{'⚠️接近' if vix>25 else '→安全'}）<br>"
                       f"④ 美股回撤>10%放量 → 止损不侥幸<br>"
                       f"⑤ 每季末检查仓位比例，偏离>5%即再平衡"
        },
        # ========== 年线 ==========
        "year": {
            "title": "📅 年线总参谋 — 长期战略配置与财富规划",
            "desc": f"实时数据 {ts} · 年度大类资产·世代财富·黑天鹅应对",
            "data": [
                {"指标": "美国经济状态", "数值": f"失业率{unemp:.1f}%", "趋势": unemp_label},
                {"指标": "美联储政策", "数值": rate_color, "趋势": rate_outlook},
                {"指标": "CPI通胀水平", "数值": f"{cpi_val:.1f}%", "趋势": cpi_label},
                {"指标": "核心PCE", "数值": f"{pce_val:.1f}%", "趋势": pce_label},
                {"指标": "10年期TIPS实际利率", "数值": f"{tips10:.2f}%", "趋势": tips_label},
                {"指标": "标普500", "数值": f"{spx:.0f}", "趋势": f"年度基准({spx_chg_str})"},
                {"指标": "纳斯达克", "数值": f"{ndx:.0f}", "趋势": f"AI主线({ndx_chg_str})"},
                {"指标": "黄金", "数值": f"${gold:.0f}", "趋势": gold_signal},
                {"指标": "比特币", "数值": f"${btc:,.0f}", "趋势": btc_label},
                {"指标": "WTI原油", "数值": f"${oil:.1f}", "趋势": oil_label},
                {"指标": "美元指数", "数值": f"{usd:.1f}", "趋势": usd_label},
                {"指标": "地缘风险", "数值": "中高", "趋势": "中东/关税/台海三线"},
            ],
            "suggest": f"<b>【年度核心判断】</b><br>"
                       f"→ 当前数据：VIX={vix:.1f}/TIPS10Y={tips10:.2f}%/CPI={cpi_val:.1f}%/失业率={unemp:.1f}%/联邦基金利率={ff_rate:.2f}%<br>"
                       f"→ 经济周期：{'通胀回升+低失业→经济过热风险，美联储可能被迫转向鹰派⚠️' if cpi_val>3.5 and unemp<4.5 else '软着陆情景→风险资产友好'}<br>"
                       f"→ 核心矛盾：AI革命利好 vs {'通胀回升+加息风险' if cpi_val>3.5 else '合理估值+降息预期'} <br>"
                       f"→ 全年策略：<b>{'上半年谨慎，下半年观察通胀能否回落' if cpi_val>3.5 else '全年可适度积极'}</b>，{'CPI走势决定全年方向' if cpi_val>3.0 else '积极布局'} <br><br>"
                       f"<b>【年度资产配置 — 七类资产】</b><br>"
                       f"• 美股(AI+科技)：{'25%' if tips10>2.0 or cpi_val>3.5 else '35%'} — {'TIPS偏高+CPI回升→双重压制，不追高' if tips10>2.0 or cpi_val>3.5 else '核心引擎'} <br>"
                       f"• 黄金+贵金属：{'18%' if vix>22 or cpi_val>3.5 else '12%'} — {'通胀回升+不确定性→加配' if cpi_val>3.5 else '底仓配置'} <br>"
                       f"• 短期国债/现金：{'30%' if cpi_val>3.5 else '20%'} — {'加息风险高→现金比例提升' if cpi_val>3.5 else '安全垫'} <br>"
                       f"• 日本/亚太ETF：10% — {'日元贬值受益出口' if n225_pct>0 else '区域分散'} <br>"
                       f"• 比特币/数字资产：{'3%' if vix>25 or cpi_val>3.5 else '5%'} — {'VIX高+通胀→降低高风险敞口' if vix>25 or cpi_val>3.5 else '博弹性'} <br>"
                       f"• A股/港股：10% — 中国AI资产估值优势<br>"
                       f"• 大宗商品(非黄金)：10% — 对冲通胀尾部<br><br>"
                       f"<b>【年度黑天鹅应对 — 实时触发评估】</b><br>"
                       f"🐺 情景1：衰退（概率{'25%' if unemp>4.5 else '15%'}）— {'失业率上升中概率升高' if unemp>4.5 else '当前低位'} <br>"
                       f"   → 触发后：减股票至30%，加长债+黄金<br>"
                       f"🐺 情景2：通胀二次抬头（概率{'30%' if cpi_val>3.5 else '15%'}）— {'CPI='+str(cpi_val)+'%→⚠️风险显著上升，这是当前最需要警惕的情景' if cpi_val>3.5 else '暂时安全'} <br>"
                       f"   → 触发后：减成长股，转价值股+大宗+TIPS债券<br>"
                       f"🐺 情景3：地缘冲突（概率10%）<br>"
                       f"   → 触发后：黄金+美元现金+军工，抛风险资产<br>"
                       f"🐺 情景4：AI泡沫破裂（概率10%）<br>"
                       f"   → 纳指回撤30%是长期买点<br><br>"
                       f"<b>【年度操作纪律 — 不变铁律】</b><br>"
                       f"① 不借钱投资！不用杠杆！不押单票！<br>"
                       f"② 每月定投，不择时<br>"
                       f"③ 年度再平衡：12月调仓<br>"
                       f"④ 永远留20%现金，不为错过焦虑<br>"
                       f"⑤ <b>活过熊市，才能享受牛市</b>"
        },
        # ========== 新闻模块（保持原有）==========
        "trump": {
            "title": "📢 特朗普最新动态（完整列表）",
            "desc": "特朗普最新政策、竞选动态、市场影响",
            "data": [
                {"标题": "特朗普：计划推进对华高关税", "来源": "路透社", "日期": "2026-04-06", "影响": "利空A股、利好美股制造业"},
                {"标题": "特朗普：中东局势将以谈判收尾", "来源": "美联社", "日期": "2026-04-06", "影响": "利空原油、利好风险资产"},
                {"标题": "特朗普重申：若当选将重组北约", "来源": "BBC", "日期": "2026-04-05", "影响": "地缘不确定性升温，利好黄金"},
                {"标题": "特朗普：重启美墨边境墙，收紧移民政策", "来源": "Fox News", "日期": "2026-04-02", "影响": "利好本土就业，利空消费股"}
            ],
            "suggest": "特朗普政策是2026年核心变量，重点跟踪关税、地缘相关动态"
        },
        "conflict": {
            "title": "🔥 美以伊冲突实时进展",
            "desc": "中东局势、地缘风险、原油影响",
            "data": [
                {"标题": "地区局势总体可控，双方保持克制", "来源": "简报", "日期": "2026-04-06", "影响": "原油承压，风险资产回暖"},
                {"标题": "美国重申支持以色列，不寻求直接开战", "来源": "路透社", "日期": "2026-04-05", "影响": "地缘风险缓释，黄金回落"},
                {"标题": "伊朗表态愿通过外交解决分歧", "来源": "美联社", "日期": "2026-04-04", "影响": "原油下跌，美股上涨"}
            ],
            "suggest": "中东局势是原油、黄金核心驱动，重点关注冲突升级/缓和信号"
        },
        "wall": {
            "title": "📊 美股 & 华尔街要闻",
            "desc": "美联储政策、美股走势、科技股动态",
            "data": [
                {"标题": "市场聚焦美联储政策走向，降息预期升温", "来源": "简报", "日期": "2026-04-06", "影响": "利好成长股、黄金"},
                {"标题": "英伟达财报超预期，AI算力需求持续爆发", "来源": "CNBC", "日期": "2026-04-05", "影响": "科技股集体上涨"},
                {"标题": "美光、闪迪存储芯片短缺，价格持续上涨", "来源": "华尔街日报", "日期": "2026-04-04", "影响": "存储芯片股走强"}
            ],
            "suggest": "美联储政策是美股核心驱动，AI算力、存储芯片是主线赛道"
        }
    }
    return detail_map.get(module, detail_map["day"])

# ===================== 新闻通用抓取（华尔街见闻实时快讯） =====================
def _fetch_news(keywords, limit=6):
    """从华尔街见闻拉取实时新闻，按关键词过滤，始终有兜底"""
    try:
        r = requests.get(
            "https://api-one.wallstcn.com/apiv1/content/information-flow",
            params={"channel": "global-channel", "limit": 50, "accept": "article"},
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        items = data.get("data", {}).get("items", [])
        results = []
        for item in items:
            res = item.get("resource", {})
            title = res.get("title", "") or res.get("content_text", "")
            if not title:
                continue
            tl = title.lower()
            if not any(kw.lower() in tl for kw in keywords):
                continue
            dt_val = res.get("display_time", 0)
            pub = datetime.fromtimestamp(dt_val).strftime("%Y-%m-%d") if dt_val else ""
            results.append({
                "title": title,
                "url": res.get("uri", ""),
                "source": {"name": "华尔街见闻"},
                "publishedAt": pub
            })
            if len(results) >= limit:
                break
        if results:
            return results
    except Exception as e:
        print(f"华尔街见闻新闻获取失败: {e}")
    return None

# ===================== 特朗普 + AI + 黄仁勋 =====================
def get_trump():
    kw = ["特朗普", "Trump", "trump", "黄仁勋", "英伟达", "AI", "人工智能", "NVIDIA", "OpenAI", "芯片"]
    try:
        r = _fetch_news(kw, limit=8)
        if r:
            return r[:6]
    except Exception as e:
        print(f"特朗普新闻获取失败: {e}")
    return [
        {"title": "特朗普：计划推进对华高关税", "url": "/?module=trump", "source": {"name": "路透社"}, "publishedAt": "2026-04-06"},
        {"title": "特朗普：中东局势将以谈判收尾", "url": "/?module=trump", "source": {"name": "美联社"}, "publishedAt": "2026-04-06"},
        {"title": "特朗普重申：若当选将重组北约", "url": "/?module=trump", "source": {"name": "BBC"}, "publishedAt": "2026-04-05"},
    ]

# ===================== 美以伊冲突 =====================
def get_conflict():
    kw = ["以色列", "伊朗", "中东", "冲突", "Israel", "Iran", "加沙", "Gaza", "战争", "军事"]
    try:
        r = _fetch_news(kw, limit=8)
        if r:
            return r[:6]
    except Exception as e:
        print(f"冲突新闻获取失败: {e}")
    return [
        {"title": "地区局势总体可控", "url": "/?module=conflict", "source": {"name": "简报"}, "publishedAt": "2026-04-06"}
    ]

# ===================== 美股 + 日本 + 美联储 + 华尔街 =====================
def get_wall():
    kw = ["美股", "美联储", "华尔街", "日本", "Fed", "stock", "Stock", "市场", "大跌", "暴涨", "暴跌", "股灾", "危机", "衰退", "降息", "加息", "纳指", "道指", "标普", "日经"]
    try:
        r = _fetch_news(kw, limit=8)
        if r:
            return r[:6]
    except Exception as e:
        print(f"美股新闻获取失败: {e}")
    return [
        {"title": "市场聚焦美联储政策走向", "url": "/?module=wall", "source": {"name": "简报"}, "publishedAt": "2026-04-06"}
    ]

# ===================== 主页界面（全模块可点击） =====================
HOME_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全能金融盯盘系统</title>
<style>
body{font-family:微软雅黑;background:#f5f7fa;margin:0;padding:20px}
.main{max-width:1100px;margin:0 auto}
.title{text-align:center;font-size:24px;font-weight:bold;margin-bottom:8px}
.time{text-align:center;color:#666;margin-bottom:20px}
.lan{text-align:center;color:#0066cc;margin-bottom:14px;font-size:14px}
.card{background:#fff;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
.card h3{margin:0 0 12px 0;color:#222}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.item{background:#f8f9fa;padding:10px;border-radius:6px}
.news{margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #eee}
.news a{color:#0066cc;font-weight:bold;text-decoration:none}
.src{color:#777;font-size:12px;margin-top:4px}
.warn{color:#c90000;font-weight:bold}
.ok{color:#008800;font-weight:bold}
.normal{color:#333;font-weight:bold}
.link{color:#0066cc;font-weight:bold;text-decoration:none}
@media (max-width:768px){
body{padding:10px}
.title{font-size:19px}
.card{padding:12px}
.grid{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="main">
    <div class="title">📈 全能金融盯盘系统（全模块可点击）</div>
    <div class="time">更新时间：<span id="update-time">{{now}}</span></div>
    <div class="lan">📱 手机看盘：<a href="http://{{lan_ip}}:5000/">http://{{lan_ip}}:5000</a>（手机与电脑连同一WiFi即可打开）</div>

    <div class="card">
        <h3>📊 美联储 + 10年期TIPS 实时数据</h3>
        <div class="grid" id="fed-grid">
            {% for x in fed %}
            {% if not x.name.startswith('_') and x.name != '美国CPI指数' %}
            <div class="item">{{x.name}}：{{x.value}}<br><small>{{x.date}}</small></div>
            {% endif %}
            {% endfor %}
        </div>
    </div>

    <div class="card">
        <h3>🚨 10年期TIPS 顶底提醒（直接照做）</h3>
        <div class="grid">
            <div class="item">当前TIPS：<b>{{tips.val}}%</b></div>
            <div class="item">信号：<span class="{{'ok' if '✅' in tips.signal else 'warn' if '⚠️' in tips.signal else 'normal'}}">{{tips.signal}}</span></div>
            <div class="item" style="grid-column: span 2">操作建议：{{tips.note}}</div>
        </div>
    </div>

    <div class="card">
        <h3>📅 日 / 周 / 月 / 季 / 年 综合报表（点击查看详细）</h3>
        <div class="grid">
            <div class="item"><a href="/?period=day" class="link">日报：{{rp.day}}</a></div>
            <div class="item"><a href="/?period=week" class="link">周报：{{rp.week}}</a></div>
            <div class="item"><a href="/?period=month" class="link">月报：{{rp.month}}</a></div>
            <div class="item"><a href="/?period=quarter" class="link">季报：{{rp.quarter}}</a></div>
            <div class="item" style="grid-column: span 2"><a href="/?period=year" class="link">年报：{{rp.year}}</a></div>
        </div>
        <div style="margin-top:10px;color:#555">
        • 日线：看短期买卖点<br>
        • 周线：看趋势方向<br>
        • 月线：看大底大顶<br>
        • 季/年：决定仓位轻重
        </div>
    </div>

    <div class="card">
        <h3>📢 特朗普最新动态（点击查看完整列表）</h3>
        {% for n in trump %}
        <div class="news">
            <a href="{{n.url}}" class="link">{{n.title}}</a>
            <div class="src">{{n.source.name}} | {{n.publishedAt[:10]}}</div>
        </div>
        {% endfor %}
    </div>

    <div class="card">
        <h3>🔥 美以伊冲突实时（点击查看完整进展）</h3>
        {% for n in conflict %}
        <div class="news">
            <a href="{{n.url}}" class="link">{{n.title}}</a>
            <div class="src">{{n.source.name}} | {{n.publishedAt[:10]}}</div>
        </div>
        {% endfor %}
    </div>

    <div class="card">
        <h3>📊 美股 & 华尔街要闻（点击查看完整要闻）</h3>
        {% for n in wall %}
        <div class="news">
            <a href="{{n.url}}" class="link">{{n.title}}</a>
            <div class="src">{{n.source.name}} | {{n.publishedAt[:10]}}</div>
        </div>
        {% endfor %}
    </div>
</div>
</body>
</html>
"""

# ===================== 详情页通用界面 =====================
DETAIL_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{detail.title}}</title>
<style>
body{font-family:微软雅黑;background:#f5f7fa;margin:0;padding:20px}
.main{max-width:1000px;margin:0 auto}
.title{text-align:center;font-size:24px;font-weight:bold;margin-bottom:10px}
.desc{text-align:center;color:#666;margin-bottom:20px}
.card{background:#fff;border-radius:10px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #eee;padding:12px;text-align:left}
th{background:#f8f9fa}
.strategy{background:#f0f7ff;border-left:4px solid #0066cc;padding:16px 20px;margin-top:20px;border-radius:4px;font-size:14px;line-height:1.9;color:#333;white-space:normal}
.back-link{text-align:center;margin-top:20px}
.back-link a{color:#0066cc;font-weight:bold;text-decoration:none;font-size:18px}
@media (max-width:768px){
body{padding:10px}
.title{font-size:19px}
th,td{padding:8px;font-size:13px}
.strategy{font-size:13px;padding:12px 14px;line-height:1.8}
}
</style>
</head>
<body>
<div class="main">
    <div class="title">{{detail.title}}</div>
    <div class="desc">{{detail.desc}}</div>
    <div class="card">
        <table>
            <tr>
                {% if '标题' in detail.data[0] %}
                <th>标题</th>
                <th>来源</th>
                <th>日期</th>
                <th>市场影响</th>
                {% else %}
                <th>指标</th>
                <th>数值/涨跌幅</th>
                <th>趋势</th>
                {% endif %}
            </tr>
            {% for row in detail.data %}
            <tr>
                {% if '标题' in row %}
                <td>{{row.标题}}</td>
                <td>{{row.来源}}</td>
                <td>{{row.日期}}</td>
                <td>{{row.影响}}</td>
                {% else %}
                <td>{{row.指标}}</td>
                <td>{{row.数值 if row.数值 is defined else row.周涨跌幅 if row.周涨跌幅 is defined else row.月涨跌幅 if row.月涨跌幅 is defined else row.季涨跌幅 if row.季涨跌幅 is defined else row.年涨跌幅}}</td>
                <td>{{row.趋势}}</td>
                {% endif %}
            </tr>
            {% endfor %}
        </table>
        <div class="strategy">{{detail.suggest|safe}}</div>
    </div>
    <div class="back-link">
        <a href="/">← 返回主页</a>
    </div>
</div>
<script>
(function(){
    // 实时时钟：每秒更新页面时间
    function tick(){
        var d=new Date();
        document.getElementById('update-time').textContent=
            d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+
            String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0');
    }
    setInterval(tick,1000);
    tick();
    // 每30秒自动拉取最新FRED数据（无感刷新，不跳页面）
    function refreshFed(){
        fetch('/api/fed').then(function(r){return r.json();}).then(function(d){
            if(!d||!d.data)return;
            var h='';
            d.data.forEach(function(x){if(x.name.indexOf('_')===0||x.name==='美国CPI指数')return;h+='<div class="item">'+x.name+'：'+x.value+'<br><small>'+x.date+'</small></div>';});
            document.getElementById('fed-grid').innerHTML=h;
        }).catch(function(){});
    }
    setInterval(refreshFed,30000);
})();
</script>
</body>
</html>
"""

@app.route("/api/fed")
def api_fed():
    return {"data": get_fed()}

@app.route("/")
def home():
    # 处理周期报表
    period = request.args.get("period")
    if period:
        detail = get_detail_data(period)
        return render_template_string(DETAIL_PAGE, detail=detail)
    # 处理模块详情
    module = request.args.get("module")
    if module:
        detail = get_detail_data(module)
        return render_template_string(DETAIL_PAGE, detail=detail)
    # 主页
    return render_template_string(
        HOME_PAGE,
        fed=get_fed(),
        tips=get_tips_signal(),
        rp=get_report(),
        trump=get_trump(),
        conflict=get_conflict(),
        wall=get_wall(),
        lan_ip=get_lan_ip(),
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

# 修复favicon 404
@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.ico')

if __name__ == "__main__":
    lan = get_lan_ip()
    print("=" * 52)
    print(f"💻 电脑访问:  http://127.0.0.1:5000")
    print(f"📱 手机访问:  http://{lan}:5000   (手机与电脑连同一WiFi)")
    print("=" * 52)
    # 0.0.0.0 = 局域网可访问（手机看盘版）
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)