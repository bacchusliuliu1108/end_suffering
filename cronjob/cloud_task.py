import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
import requests
import psycopg2

# ================= 1. 配置区 =================
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")

tz_bj = timezone(timedelta(hours=8))


# =============================================

def send_pushplus_msg(title, content):
    """发送 PushPlus 通知"""
    if not PUSHPLUS_TOKEN:
        return
    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "txt"
    }
    try:
        requests.post(url, json=data)
    except Exception as e:
        print(f"❌ 微信推送失败: {e}")


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
    """模块 1：专属操盘风水指引"""
    try:
        from lunar_python import Lunar, Solar
        # 输入的是 YYYYMMDD
        pit_date = datetime.strptime(date_str, "%Y%m%d")
        tomorrow = pit_date + timedelta(days=1)

        target_solar = Solar.fromYmd(tomorrow.year, tomorrow.month, tomorrow.day)
        target_lunar = target_solar.getLunar()
        user_solar = Solar.fromYmdHms(1991, 11, 8, 10, 15, 0)
        user_lunar = user_solar.getLunar()
        user_shengxiao = user_lunar.getYearShengXiao()

        if user_shengxiao in target_lunar.getDayChongDesc():
            warning = f"⚠️ 警告：次日大盘冲{target_lunar.getDayChongDesc()}煞{target_lunar.getDaySha()}，正犯你本命【{user_shengxiao}】！极易震荡，防守为主。"
        else:
            warning = f"✨ 次日大盘与你命局无冲。日主纳音【{target_lunar.getDayNaYin()}】交汇，接力情绪契合，宜果断执行交易纪律。"

        return f"☯️ 次日 ({tomorrow.month}月{tomorrow.day}日) 专属操盘风水指引：\n次日财神居 {target_lunar.getPositionCaiDesc()}方。游资接力幸运色为 {target_lunar.getDayGan()}系。{warning}"
    except ImportError:
        return "☯️ 专属玄学风水指引：敬畏市场，知行合一即是最大财库。"
    except Exception as e:
        return f"☯️ 风水引擎休眠中... ({str(e)})"


def get_macro_commentary(target_date):
    """模块 2：权重股与微盘股、打板情绪点评"""
    date_dash = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()

        # 1. 权重与微盘胜率
        sql_weight = """
        SELECT 
            SUM(CASE WHEN b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000000 THEN 1 ELSE 0 END) as w_tot,
            SUM(CASE WHEN b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000000 AND d.pct_chg > 0 THEN 1 ELSE 0 END) as w_up,
            SUM(CASE WHEN NOT (b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000000) THEN 1 ELSE 0 END) as m_tot,
            SUM(CASE WHEN NOT (b.market IN ('主板', '上海主板', '深圳主板') AND d.amount >= 200000000) AND d.pct_chg > 0 THEN 1 ELSE 0 END) as m_up
        FROM daily_data d LEFT JOIN stock_basic b ON d.ts_code = b.ts_code WHERE d.trade_date = %s;
        """
        cursor.execute(sql_weight, (target_date,))
        w_tot, w_up, m_tot, m_up = cursor.fetchone()

        # 2. 涨停炸板
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
        cursor.close()
        conn.close()

        # 数据清洗防空
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

        # 生成犀利点评
        commentary = "【全局博弈点评】\n"
        if w_rate > 55 and m_rate < 45:
            commentary += f"🏛️ 典型的「二八分化」！权重股胜率达 {w_rate:.1f}%，而微盘股仅 {m_rate:.1f}%。资金在疯狂抱团大票避险，中小盘惨遭抽血，切忌在后排杂毛里逆势寻死。\n"
        elif m_rate > 55 and w_rate < 45:
            commentary += f"🌪️ 「题材唱戏」格局！微盘股胜率达 {m_rate:.1f}% 碾压权重({w_rate:.1f}%)。游资在小票里翻江倒海，轻指数重个股，聚焦前排短线核心。\n"
        elif w_rate > 55 and m_rate > 55:
            commentary += f"📈 「普涨狂欢」！大小盘胜率双双突破55% (权重 {w_rate:.1f}%, 微盘 {m_rate:.1f}%)。系统性做多窗口开启，持股待涨即可。\n"
        else:
            commentary += f"🧊 「混沌冰点」！全盘沉闷，权重胜率 {w_rate:.1f}%，微盘胜率 {m_rate:.1f}%。资金失去方向，观望情绪浓厚。\n"

        commentary += f"【接力情绪测谎】\n"
        if bomb_rate > 35:
            commentary += f"🚨 极度恶劣！今日炸板率飙升至 {bomb_rate:.1f}%，多头资金被惨烈坑杀。空间龙压制在 {max_step} 板，次日严防恐慌性核按钮，管住手！\n"
        else:
            commentary += f"🔥 情绪健康。炸板率 {bomb_rate:.1f}% 处于良性区间，全场跑出 {real_board} 家真实换手板。天梯空间拓荒至 {max_step} 板，存在极佳的连板晋级土壤。"

        return commentary
    except Exception as e:
        return f"全局点评生成失败: {e}"


def get_radar_summary(target_date):
    """模块 3：A/B/C/D 视角透视汇总"""
    date_dash = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()

        # 提取 A/C 视角：行业维度
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
        JOIN report_daily_factors f ON d.ts_code = f.ts_code AND d.trade_date = f.trade_date
        JOIN stock_sw_industry i ON d.ts_code = i.ts_code AND i.level = 'L2'
        WHERE d.trade_date = %s
        GROUP BY i.industry_name HAVING COUNT(d.ts_code) >= 5
        """

        # 提取 B/D 视角：概念维度
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
        JOIN report_daily_factors f ON d.ts_code = f.ts_code AND d.trade_date = f.trade_date
        JOIN stock_concept c ON d.ts_code = c.ts_code
        WHERE d.trade_date = %s AND c.concept_name NOT LIKE '%%同花顺%%' AND c.concept_name NOT LIKE '%%融资%%'
        GROUP BY c.concept_name HAVING COUNT(d.ts_code) BETWEEN 5 AND 1000
        """

        # 抓行业数据
        cursor.execute(sql_ind, (target_date, date_dash))
        ind_rows = cursor.fetchall()
        # 抓概念数据
        cursor.execute(sql_con, (target_date, date_dash))
        con_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not ind_rows or not con_rows: return "雷达探测数据不足。"

        # 排序寻找最强因子
        top_A = sorted(ind_rows, key=lambda x: x[2] + x[3] * 5, reverse=True)[0]  # 密度溢价与涨幅综合最强
        top_B = sorted(con_rows, key=lambda x: x[2] + x[3] * 5, reverse=True)[0]
        top_C = sorted(ind_rows, key=lambda x: x[4], reverse=True)[0]  # 换手活跃度最强
        top_D = sorted(con_rows, key=lambda x: x[4], reverse=True)[0]

        summary = "【A/B/C/D 资金雷达透视】\n"
        summary += f"📍 视角A (行业动量)：【{top_A[0]}】成为今日吸金暴风眼。资金密度溢价率高达 {top_A[2]:.1f}% 且逆势斩获 {top_A[3]:.1f}% 涨幅。逻辑：资金在此处绝对聚焦且超额赚钱。\n"
        summary += f"📍 视角B (概念风口)：【{top_B[0]}】领跑全场题材。单票吸血虹吸极强(溢价 {top_B[2]:.1f}%)，多头合力极度坚决。\n"
        summary += f"📍 视角C (行业游资)：【{top_C[0]}】换手率飙升至 {top_C[4]:.1f}%。筹码高强度换手，若资金密度配合度高，极易爆出连板大妖。\n"
        summary += f"📍 视角D (题材火葬场/加速区)：【{top_D[0]}】概念资金博弈到达白热化，平均换手高达 {top_D[4]:.1f}%，警惕高位分歧派发或核按钮。\n"

        return summary
    except Exception as e:
        return f"雷达引擎解析失败: {e}"


# ================= 主控制流 =================
def generate_and_send_report(target_date, is_already_ready=False):
    date_dash = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    bazi_text = get_bazi_fortune(target_date)
    macro_text = get_macro_commentary(target_date)
    radar_text = get_radar_summary(target_date)

    header = f"🚀 [完工] {date_dash} 游资实战复盘与资金雷达\n" if not is_already_ready else f"🔍 [就绪] {date_dash} 游资实战复盘与资金雷达\n"

    content = f"{bazi_text}\n\n{macro_text}\n\n{radar_text}\n\n🔗 穿透图表与个股明细，请打开狙击控制台：\nhttps://endsuffering-x2pgf68rrwm75l2fvgde8r.streamlit.app/"

    send_pushplus_msg(header, content)


def main():
    start_time = datetime.now(tz_bj)
    target_date = get_target_trade_date(start_time)

    # 1. 检查数据是否完整
    is_complete = check_if_data_exists(target_date)

    if is_complete:
        # 如果爬虫之前跑过了，直接生成深度报告
        generate_and_send_report(target_date, is_already_ready=True)
        sys.exit(0)

    # 2. 如果不完整，执行爬虫
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    collector_path = os.path.join(project_root, "stock_basic_info", "data_collector.py")

    try:
        subprocess.run([sys.executable, collector_path], check=True)
    except subprocess.CalledProcessError:
        send_pushplus_msg("🚨 异常：爬虫引擎崩溃", f"日期: {target_date}\ndata_collector.py 执行受阻。")
        sys.exit(1)

    # 3. 执行 ETL
    etl_path = os.path.join(project_root, "etl", "etl_pipeline.py")
    try:
        subprocess.run([sys.executable, etl_path], check=True)
    except subprocess.CalledProcessError:
        send_pushplus_msg("🚨 异常：ETL 因子萃取中断", f"日期: {target_date}\nreport_daily_factors 算子崩溃。")
        sys.exit(1)

    # 4. 全部洗完，生成终极战报
    generate_and_send_report(target_date, is_already_ready=False)


if __name__ == "__main__":
    main()