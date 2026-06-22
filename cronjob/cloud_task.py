import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
import requests
import psycopg2

# ================= 1. 配置区 =================
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")  # 👉 引入 Tushare 令牌

tz_bj = timezone(timedelta(hours=8))


# =============================================

def send_pushplus_msg(title, content):
    if not PUSHPLUS_TOKEN:
        print("⚠️ 未配置 PUSHPLUS_TOKEN，跳过微信推送。")
        return

    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "markdown"
    }
    try:
        res = requests.post(url, json=data, timeout=15)
        print(f"📬 PushPlus 响应: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ 微信推送网络异常: {e}")


def get_target_trade_date(now):
    if now.hour < 17:
        target = now - timedelta(days=1)
    else:
        target = now

    if target.weekday() == 5:
        target = target - timedelta(days=1)
    elif target.weekday() == 6:
        target = target - timedelta(days=2)

    return target.strftime("%Y%m%d")


def check_if_data_exists(target_date):
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM stock_basic;")
        total_stocks = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM daily_data WHERE trade_date = %s;", (target_date,))
        existing_count = cursor.fetchone()[0]
        cursor.close()
        conn.close()

        if total_stocks == 0:
            return False
        return (existing_count >= total_stocks * 0.95)
    except Exception:
        return False


# ================= 核心分析模块 =================
def get_bazi_fortune(date_str):
    try:
        from lunar_python import Lunar, Solar
        pit_date = datetime.strptime(date_str, "%Y%m%d")
        tomorrow = pit_date + timedelta(days=1)

        target_solar = Solar.fromYmd(tomorrow.year, tomorrow.month, tomorrow.day)
        target_lunar = target_solar.getLunar()
        user_solar = Solar.fromYmdHms(1991, 11, 8, 10, 15, 0)
        user_lunar = user_solar.getLunar()
        user_shengxiao = user_lunar.getYearShengXiao()

        if user_shengxiao in target_lunar.getDayChongDesc():
            warning = f"⚠️ **高危预警：** 次日大盘冲{target_lunar.getDayChongDesc()}煞{target_lunar.getDaySha()}，正犯你本命【{user_shengxiao}】！极易震荡，防守为主。"
        else:
            warning = f"✨ 次日大盘与你命局无冲。日主纳音【{target_lunar.getDayNaYin()}】交汇，接力情绪契合，宜果断执行交易纪律。"

        return f"**☯️ 次日 ({tomorrow.month}月{tomorrow.day}日) 专属操盘风水指引：**\n\n次日财神居 **{target_lunar.getPositionCaiDesc()}方**。游资接力幸运色为 **{target_lunar.getDayGan()}系**。\n> {warning}"
    except Exception as e:
        return f"☯️ 风水引擎休眠中... ({str(e)})"


def get_market_index(target_date):
    """调用 Tushare 获取上证指数核心数据"""
    if not TUSHARE_TOKEN:
        return "⚠️ Tushare Token 缺失，无法获取大盘点数。"
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        # 获取上证指数 (000001.SH)
        df = pro.index_daily(ts_code='000001.SH', trade_date=target_date)
        if not df.empty:
            open_pt = df['open'].iloc[0]
            close_pt = df['close'].iloc[0]
            pct_chg = df['pct_chg'].iloc[0]
            sign = "🔴" if pct_chg > 0 else ("🟢" if pct_chg < 0 else "⚪")
            return f"**上证指数：** 开盘 **{open_pt:.2f}** 点 | 收盘 **{close_pt:.2f}** 点 | 涨跌幅 {sign} **{pct_chg:.2f}%**"
        return "大盘点数今日尚未更新。"
    except Exception as e:
        return f"大盘点数获取异常: {e}"


def get_macro_commentary(target_date):
    date_dash = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    try:
        # 获取大盘指数文本
        index_text = get_market_index(target_date)

        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()

        # 1. 抓取主板整体涨跌数据
        sql_market_breadth = """
        SELECT 
            COUNT(d.ts_code) as total,
            SUM(CASE WHEN d.pct_chg > 0 THEN 1 ELSE 0 END) as up_cnt,
            SUM(CASE WHEN d.pct_chg < 0 THEN 1 ELSE 0 END) as down_cnt,
            SUM(CASE WHEN d.pct_chg = 0 THEN 1 ELSE 0 END) as flat_cnt
        FROM daily_data d
        JOIN stock_basic b ON d.ts_code = b.ts_code
        WHERE d.trade_date = %s AND b.market IN ('主板', '上海主板', '深圳主板');
        """
        cursor.execute(sql_market_breadth, (target_date,))
        mb_tot, mb_up, mb_down, mb_flat = cursor.fetchone()

        # 2. 抓取大小盘权重比 (2 亿即 200000 千元)
        sql_weight = """
        SELECT 
            SUM(CASE WHEN b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000 THEN 1 ELSE 0 END) as w_tot,
            SUM(CASE WHEN b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000 AND d.pct_chg > 0 THEN 1 ELSE 0 END) as w_up,
            SUM(CASE WHEN NOT (b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000) THEN 1 ELSE 0 END) as m_tot,
            SUM(CASE WHEN NOT (b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000) AND d.pct_chg > 0 THEN 1 ELSE 0 END) as m_up
        FROM daily_data d LEFT JOIN stock_basic b ON d.ts_code = b.ts_code WHERE d.trade_date = %s;
        """
        cursor.execute(sql_weight, (target_date,))
        w_tot, w_up, m_tot, m_up = cursor.fetchone()

        # 3. 抓取打板情绪指标
        sql_emotion = """
        SELECT 
            SUM(CASE WHEN is_limit_up = true THEN 1 ELSE 0 END) as limit_up,
            SUM(CASE WHEN is_limit_up = true AND limit_up_type != '一字板' THEN 1 ELSE 0 END) as real_board,
            SUM(CASE WHEN is_bomb_board = true THEN 1 ELSE 0 END) as bomb,
            MAX(limit_step) as max_step
        FROM report_daily_factors WHERE trade_date = %s;
        """
        cursor.execute(sql_emotion, (date_dash,))
        limit_up, real_board, bomb, max_step = cursor.fetchone()

        # 4. 提取空间龙名字
        dragon_str = "无"
        if max_step and max_step > 0:
            sql_dragon = """
            SELECT b.name
            FROM report_daily_factors f
            JOIN stock_basic b ON f.ts_code = b.ts_code
            WHERE f.trade_date = %s AND f.limit_step = %s AND f.is_limit_up = true;
            """
            cursor.execute(sql_dragon, (date_dash, max_step))
            dragons = [r[0] for r in cursor.fetchall()]
            if dragons:
                dragon_str = "、".join(dragons)

        cursor.close()
        conn.close()

        # 数据防空兜底
        mb_tot = mb_tot or 0;
        mb_up = mb_up or 0;
        mb_down = mb_down or 0;
        mb_flat = mb_flat or 0
        w_tot = w_tot or 1;
        m_tot = m_tot or 1
        w_up = w_up or 0;
        m_up = m_up or 0
        limit_up = limit_up or 0;
        real_board = real_board or 0;
        bomb = bomb or 0;
        max_step = max_step or 0

        w_rate = (w_up / w_tot) * 100
        m_rate = (m_up / m_tot) * 100
        bomb_rate = (bomb / (limit_up + bomb) * 100) if (limit_up + bomb) > 0 else 0

        # 开始生成文字
        commentary = "### 📊 盘面全景与博弈点评\n\n"
        commentary += f"- {index_text}\n"
        commentary += f"- **主板个股概况：** 今日主板共活跃 **{mb_tot}** 只标的，其中上涨 🔴 **{mb_up}** 只，下跌 🟢 **{mb_down}** 只，平盘 ⚪ **{mb_flat}** 只。\n"

        if w_rate > 55 and m_rate < 45:
            commentary += f"- **大小盘博弈 (二八分化)：** 权重胜率达 **{w_rate:.1f}%** ({w_up}/{w_tot})，微盘仅 **{m_rate:.1f}%** ({m_up}/{m_tot})。资金疯狂抱团大票避险，切忌在后排小票逆势寻死。\n"
        elif m_rate > 55 and w_rate < 45:
            commentary += f"- **大小盘博弈 (题材唱戏)：** 微盘胜率达 **{m_rate:.1f}%** ({m_up}/{m_tot}) 碾压权重 (**{w_rate:.1f}%**)。游资在小票翻江倒海，轻指数重个股。\n"
        elif w_rate > 55 and m_rate > 55:
            commentary += f"- **大小盘博弈 (普涨狂欢)：** 大小盘齐飙，权重胜率 **{w_rate:.1f}%**，微盘 **{m_rate:.1f}%**。系统性做多窗口开启。\n"
        else:
            commentary += f"- **大小盘博弈 (混沌冰点)：** 全盘沉闷，权重胜率 **{w_rate:.1f}%** ({w_up}/{w_tot})，微盘 **{m_rate:.1f}%** ({m_up}/{m_tot})。资金观望情绪浓厚。\n"

        commentary += f"- **打板情绪测温：** 炸板率 **{bomb_rate:.1f}%** ({bomb}家炸板 / {limit_up}家涨停)，真实换手上车板 **{real_board}** 家。\n"
        commentary += f"- **天梯空间龙：** 最高压制在 **{max_step} 连板**，全场龙头标的：【**{dragon_str}**】。"

        return commentary
    except Exception as e:
        return f"全局点评生成失败: {e}"


def get_radar_summary(target_date):
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()

        sql_ind = """
        WITH global_avg AS (
            SELECT SUM(amount)/NULLIF(COUNT(ts_code),0) as g_avg, SUM(amount) as g_tot FROM daily_data WHERE trade_date = %s
        )
        SELECT 
            i.industry_name,
            SUM(d.amount) / NULLIF((SELECT g_tot FROM global_avg), 0) * 100 as sip_rate,
            ((SUM(d.amount)/NULLIF(COUNT(d.ts_code),0)) - (SELECT g_avg FROM global_avg)) / NULLIF((SELECT g_avg FROM global_avg), 0) * 100 as den_prem,
            AVG(d.pct_chg) as avg_chg,
            AVG(f.turnover_rate) as avg_turn
        FROM daily_data d
        JOIN report_daily_factors f ON d.ts_code = f.ts_code AND d.trade_date::varchar = f.trade_date
        JOIN stock_sw_industry i ON d.ts_code = i.ts_code AND i.level = 'L2'
        WHERE d.trade_date = %s
        GROUP BY i.industry_name HAVING COUNT(d.ts_code) >= 5
        """

        sql_con = """
        WITH global_avg AS (
            SELECT SUM(amount)/NULLIF(COUNT(ts_code),0) as g_avg, SUM(amount) as g_tot FROM daily_data WHERE trade_date = %s
        )
        SELECT 
            c.concept_name,
            SUM(d.amount) / NULLIF((SELECT g_tot FROM global_avg), 0) * 100 as sip_rate,
            ((SUM(d.amount)/NULLIF(COUNT(d.ts_code),0)) - (SELECT g_avg FROM global_avg)) / NULLIF((SELECT g_avg FROM global_avg), 0) * 100 as den_prem,
            AVG(d.pct_chg) as avg_chg,
            AVG(f.turnover_rate) as avg_turn
        FROM daily_data d
        JOIN report_daily_factors f ON d.ts_code = f.ts_code AND d.trade_date::varchar = f.trade_date
        JOIN stock_concept c ON d.ts_code = c.ts_code
        WHERE d.trade_date = %s AND c.concept_name NOT LIKE '%%同花顺%%' AND c.concept_name NOT LIKE '%%融资%%'
        GROUP BY c.concept_name HAVING COUNT(d.ts_code) BETWEEN 5 AND 1000
        """

        cursor.execute(sql_ind, (target_date, target_date))
        ind_rows = cursor.fetchall()
        cursor.execute(sql_con, (target_date, target_date))
        con_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not ind_rows or not con_rows: return "雷达探测数据不足。"

        top_A = sorted(ind_rows, key=lambda x: (x[2] or 0) + (x[3] or 0) * 5, reverse=True)[0]
        top_B = sorted(con_rows, key=lambda x: (x[2] or 0) + (x[3] or 0) * 5, reverse=True)[0]
        top_C = sorted(ind_rows, key=lambda x: (x[4] or 0), reverse=True)[0]
        top_D = sorted(con_rows, key=lambda x: (x[4] or 0), reverse=True)[0]

        summary = "### 📡 核心资金主线透视 (基于动量与换手)\n\n"
        summary += f"- **📍 视角A (行业动量)：** 资金重仓突击【**{top_A[0]}**】！该板块资金密度溢价率高达 **{top_A[2]:.1f}%**，逆势斩获 **{top_A[3]:.1f}%** 的平均涨幅。逻辑：兼具高度聚焦与超额赚钱效应的绝对主线。\n"
        summary += f"- **📍 视角B (概念风口)：** 【**{top_B[0]}**】概念领跑全场！资金虹吸率达 **{top_B[1]:.1f}%**，单票吸金极度夸张(密度溢价 **{top_B[2]:.1f}%**)，多头合力拉升最坚决。\n"
        summary += f"- **📍 视角C (行业游资)：** 【**{top_C[0]}**】成为游资换手矿区！平均换手率飙升至 **{top_C[4]:.1f}%**。逻辑：筹码交换极度活跃，若资金溢价配合，极易发酵出前排连板大妖。\n"
        summary += f"- **📍 视角D (概念过热预警)：** 【**{top_D[0]}**】概念博弈白热化，平均换手率达 **{top_D[4]:.1f}%**。逻辑：警惕高位分歧，若板块出现「天量滞涨」，随时防范获利盘派发与核按钮风险。\n"

        return summary
    except Exception as e:
        return f"雷达引擎解析失败: {e}"


# ================= 主控制流 =================
def generate_and_send_report(target_date, is_already_ready=False):
    print("⏳ 开始生成战报文本...")
    date_dash = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    bazi_text = get_bazi_fortune(target_date)
    macro_text = get_macro_commentary(target_date)
    radar_text = get_radar_summary(target_date)

    header = f"🚀 [完工] {date_dash} 游资实战复盘与资金雷达" if not is_already_ready else f"🔍 [就绪] {date_dash} 游资实战复盘与资金雷达"

    content = f"{bazi_text}\n\n{macro_text}\n\n{radar_text}\n\n---\n🔗 **穿透图表与个股明细，请直接打开狙击控制台：**\n[点击跳转 EndSuffering 终端](https://endsuffering-x2pgf68rrwm75l2fvgde8r.streamlit.app/)"

    send_pushplus_msg(header, content)


def main():
    print("🛠️ 启动云端任务调度中心...")
    start_time = datetime.now(tz_bj)
    target_date = get_target_trade_date(start_time)

    is_complete = check_if_data_exists(target_date)

    if is_complete:
        print("✅ 今日数据已全量入库，直接生成战报！")
        generate_and_send_report(target_date, is_already_ready=True)
        sys.exit(0)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    print("🕸️ 启动爬虫...")
    collector_path = os.path.join(project_root, "stock_basic_info", "data_collector.py")
    try:
        subprocess.run([sys.executable, collector_path], check=True)
    except subprocess.CalledProcessError:
        send_pushplus_msg("🚨 异常：爬虫引擎崩溃", "请查阅 GitHub Action 日志。")
        sys.exit(1)

    print("⚙️ 启动 ETL 因子计算...")
    etl_path = os.path.join(project_root, "etl", "etl_pipeline.py")
    try:
        subprocess.run([sys.executable, etl_path], check=True)
    except subprocess.CalledProcessError:
        send_pushplus_msg("🚨 异常：ETL 因子萃取中断", "请查阅 GitHub Action 日志。")
        sys.exit(1)

    print("✅ 全线贯通，准备下发战报！")
    generate_and_send_report(target_date, is_already_ready=False)


if __name__ == "__main__":
    main()