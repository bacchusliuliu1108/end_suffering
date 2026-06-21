import streamlit as st
import pandas as pd
import psycopg2
import plotly.express as px
import plotly.graph_objects as go
import datetime
import urllib.parse
import os
from lunar_python import Lunar, Solar

# ================= 1. 页面与数据库配置 =================
st.set_page_config(page_title="EndSuffering Daily Report", page_icon="💰", layout="wide")

# 强制使用本地数据库闭环
# DB_URL = "postgresql://postgres:endsuffering@localhost:5432/stock_db"
DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres:endsuffering@localhost:5432/stock_db")

@st.cache_data(ttl=60)
def fetch_all_available_dates():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM report_daily_factors ORDER BY trade_date DESC;")
        dates = [r[0] for r in cur.fetchall()]
        conn.close()
        return dates
    except Exception as e:
        st.error(f"❌ 账期引擎加载失败: {e}")
        return []


@st.cache_data(ttl=60)
def load_production_matrix(selected_date, all_fetched_dates):
    try:
        conn = psycopg2.connect(DB_URL)

        query_today = f"""
            SELECT d.trade_date, d.ts_code, b.name, b.market, d.close, d.high, d.low, d.pct_chg, d.amount,
                   f.is_limit_up, f.is_limit_down, f.is_bomb_board, f.limit_up_type, f.turnover_rate, f.limit_step
            FROM daily_data d
            JOIN report_daily_factors f ON d.ts_code = f.ts_code AND d.trade_date::varchar = f.trade_date::varchar
            LEFT JOIN stock_basic b ON d.ts_code = b.ts_code
            WHERE d.trade_date::varchar = '{selected_date}';
        """
        try:
            df_today = pd.read_sql(query_today, conn)
            df_today['limit_step'] = pd.to_numeric(df_today['limit_step'], errors='coerce').fillna(0).astype(int)
        except Exception:
            conn.rollback()
            query_today_fallback = query_today.replace(", f.limit_step", "")
            df_today = pd.read_sql(query_today_fallback, conn)
            df_today['limit_step'] = df_today['is_limit_up'].apply(lambda x: 1 if x else 0)
            if 'limit_up_type' in df_today.columns:
                extracted = df_today['limit_up_type'].astype(str).str.extract(r'(\d+)连板').astype(float)
                df_today['limit_step'] = extracted[0].fillna(df_today['limit_step']).astype(int)

        current_idx = all_fetched_dates.index(selected_date) if selected_date in all_fetched_dates else 0

        if current_idx + 1 < len(all_fetched_dates):
            yesterday_date = all_fetched_dates[current_idx + 1]
            query_yesterday = query_today.replace(f"'{selected_date}'", f"'{yesterday_date}'")
            try:
                df_yesterday = pd.read_sql(query_yesterday, conn)
                df_yesterday['limit_step'] = pd.to_numeric(df_yesterday['limit_step'], errors='coerce').fillna(
                    0).astype(int)
            except Exception:
                conn.rollback()
                query_yesterday_fallback = query_yesterday.replace(", f.limit_step", "")
                df_yesterday = pd.read_sql(query_yesterday_fallback, conn)
                df_yesterday['limit_step'] = df_yesterday['is_limit_up'].apply(lambda x: 1 if x else 0)
                if 'limit_up_type' in df_yesterday.columns:
                    extracted_y = df_yesterday['limit_up_type'].astype(str).str.extract(r'(\d+)连板').astype(float)
                    df_yesterday['limit_step'] = extracted_y[0].fillna(df_yesterday['limit_step']).astype(int)
        else:
            df_yesterday = pd.DataFrame()

        history_window_dates = all_fetched_dates[current_idx: current_idx + 3]
        date_str_list = ",".join([f"'{d}'" for d in history_window_dates])

        df_history = pd.read_sql(
            f"SELECT trade_date, ts_code, is_limit_up FROM report_daily_factors WHERE trade_date::varchar IN ({date_str_list});",
            conn)

        query_sw = """
            SELECT ts_code, industry_name AS industry, level 
            FROM stock_sw_industry 
            WHERE level IN ('L1', 'L2', 'L3')
        """
        df_sw = pd.read_sql(query_sw, conn)
        df_sw = df_sw.drop_duplicates(subset=['ts_code', 'level'], keep='first')

        df_concept = pd.read_sql("SELECT ts_code, concept_name AS concept FROM stock_concept", conn)

        conn.close()
        return df_today, df_yesterday, df_history, selected_date, df_sw, df_concept
    except Exception as e:
        st.error(f"❌ 数据时光机加载失败: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), None, pd.DataFrame(), pd.DataFrame()


# ================= 2. 网页时光隧道入口与数据总线 =================
st.title("💰 EndSuffering Daily Report")

available_dates = fetch_all_available_dates()
if not available_dates:
    st.warning("⚠️ 数据库内未监测到任何有效的历史账期切片。请确保管道已正常清洗数据。")
    st.stop()

col_pit, col_empty = st.columns([0.25, 0.75])
with col_pit:
    chosen_date = st.selectbox(
        "📅 交易账期历史切片 (PIT Selector)",
        options=available_dates,
        index=0,
        key="global_pit_date"
    )

df_base_raw, df_yesterday_raw, df_hist, target_date, df_sw, df_concept = load_production_matrix(chosen_date,
                                                                                                available_dates)

if df_base_raw.empty:
    st.warning(f"⚠️ 选定的账期 `{chosen_date}` 数据切片为空。")
    st.stop()


def clean_market_name(m):
    m_str = str(m)
    if '主板' in m_str: return '主板'
    if '创业板' in m_str: return '创业板'
    if '科创板' in m_str: return '科创板'
    if '北交所' in m_str or '北证' in m_str: return '北交所'
    return m_str


df_base_raw['market_clean'] = df_base_raw['market'].apply(clean_market_name)
df_base_raw['amount_yi'] = df_base_raw['amount'] / 100000

if not df_yesterday_raw.empty:
    df_yesterday_raw['market_clean'] = df_yesterday_raw['market'].apply(clean_market_name)
    df_yesterday_raw['amount_yi'] = df_yesterday_raw['amount'] / 100000

global_avg_chg = df_base_raw['pct_chg'].mean()


def get_inline_btn_html(name, param_key):
    safe_name = urllib.parse.quote(name)
    return f'<a href="/?{param_key}={safe_name}" target="_self" style="background-color: #ffffff; border: 1px solid #c8c8c8; border-radius: 4px; padding: 2px 8px; text-decoration: none; color: #333; font-weight: bold; display: inline-block; box-shadow: 0 1px 2px rgba(0,0,0,0.1); margin: 0 2px;">【{name}】</a>'


def optimize_scatter_labels(fig, selected_item=None):
    positions = ['top right', 'top left', 'bottom right', 'bottom left']
    pos_idx = 0

    for trace in fig.data:
        trace.mode = 'markers+text'
        is_selected = selected_item and getattr(trace, 'name', None) and (
                trace.name == selected_item or trace.name.startswith(f"{selected_item} ("))
        has_label = False
        new_text = []

        if getattr(trace, 'text', None) is not None:
            for t in trace.text:
                if t and str(t).strip() != '':
                    has_label = True
                    new_text.append(f"<b>{t}</b>")
                elif is_selected:
                    has_label = True
                    new_text.append(f"<b>{trace.name}</b>")
                else:
                    new_text.append("")
        else:
            if is_selected:
                has_label = True
                new_text.append(f"<b>{trace.name}</b>")
            else:
                new_text.append("")

        trace.text = tuple(new_text)

        if has_label:
            trace.textposition = positions[pos_idx % 4]
            pos_idx += 1

            if is_selected:
                trace.textfont = dict(color="#ff4b4b", size=14)
            else:
                trace.textfont = dict(color="#333333", size=11)

        if is_selected and getattr(trace, 'marker', None):
            trace.marker.line.width = 3
            trace.marker.line.color = 'red'

    fig.update_traces(cliponaxis=False)
    fig.update_layout(annotations=[])
    return fig


# -----------------------------------------------------------------
# 📊 模块一：全局情绪温度计
# -----------------------------------------------------------------
def calculate_tomorrow_bazi_fortune(pit_date_str):
    try:
        pit_date = datetime.datetime.strptime(pit_date_str, "%Y-%m-%d")
        tomorrow = pit_date + datetime.timedelta(days=1)
        target_solar = Solar.fromYmd(tomorrow.year, tomorrow.month, tomorrow.day)
        target_lunar = target_solar.getLunar()
        user_solar = Solar.fromYmdHms(1991, 11, 8, 10, 15, 0)
        user_lunar = user_solar.getLunar()
        user_shengxiao = user_lunar.getYearShengXiao()

        is_high_risk = False
        if user_shengxiao in target_lunar.getDayChongDesc():
            warning = f"⚠️ **高危预警**：次日大盘冲{target_lunar.getDayChongDesc()}煞{target_lunar.getDaySha()}，正犯你的本命相【{user_shengxiao}】！盘面极易震荡，控制仓位防守。"
            is_high_risk = True
        else:
            warning = f"✨ 次日大盘与你的命局无冲。日主纳音【{target_lunar.getDayNaYin()}】交汇，接力情绪契合，宜果断执行交易纪律。"

        return f"☯️ **次日 ({tomorrow.month}月{tomorrow.day}日) 专属操盘风水指引**：\n\n次日财神居 **{target_lunar.getPositionCaiDesc()}方**。游资接力幸运色为 **{target_lunar.getDayGan()}系**。{warning}", is_high_risk
    except:
        return "☯️ **专属玄学风水**：敬畏市场，知行合一即是最大财库。", False


bazi_msg, global_bazi_risk_flag = calculate_tomorrow_bazi_fortune(target_date)
st.info(bazi_msg)


def format_bias_calc(val):
    if pd.isna(val): return ""
    if val > 15.0 and global_bazi_risk_flag:
        return "🚨 斩立决"
    return f"{val:.2f}%"


st_count = df_base_raw['name'].astype(str).str.contains('ST').sum()
st.markdown(
    f"**选中业务账期 (PIT)**：`{target_date}` | **当日存活监测标的**：`{len(df_base_raw)} 只` (其中 ST标的：`{st_count}` 只，已剔除停牌与退市股)")

limit_up_actual = df_base_raw['is_limit_up'].sum()
bomb_board_count = df_base_raw['is_bomb_board'].sum()
total_board_intraday = limit_up_actual + bomb_board_count
bomb_rate = (bomb_board_count / total_board_intraday * 100) if total_board_intraday > 0 else 0

if bomb_rate > 35.0:
    st.error(f"🚨 **全局重度预警：当日炸板率高达 {bomb_rate:.1f}%！盘中多头资金被极度套牢，次日严防恐慌性『核按钮』！**")
else:
    st.success("🟢 看板情绪引擎运转正常，市场未见群体性大面积恶性炸板。")

if not df_yesterday_raw.empty:
    yesterday_limit_up_codes = df_yesterday_raw[df_yesterday_raw['is_limit_up'] == True]['ts_code']
    if not yesterday_limit_up_codes.empty:
        today_premium_df = df_base_raw[df_base_raw['ts_code'].isin(yesterday_limit_up_codes)]
        if not today_premium_df.empty:
            avg_premium = today_premium_df['pct_chg'].mean()
            if not pd.isna(avg_premium):
                if avg_premium < 0:
                    st.error(
                        f"🧊 **接力环境极其恶劣 (测温仪)：昨日涨停标的今日平均表现为 {avg_premium:.2f}%。打板资金惨遭“活埋”，亏钱效应弥漫，无论雷达多诱人，一律空仓防守！**")
                else:
                    st.success(
                        f"🔥 **接力环境健康良好 (测温仪)：昨日涨停标的今日平均溢价为 {avg_premium:.2f}%。多头承接力强，具备连板晋级土壤。**")

with st.expander("💡 点击查看【狙击控制台核心公式与四象限实战深度解析】", expanded=True):
    st.markdown("**一、 核心指标计算公式（狙击版）**")
    st.latex(
        r"\text{1) 资金密度溢价率 (\%)} = \frac{\text{该板块单票平均成交额} - \text{全市场单票平均成交额}}{\text{全市场单票平均成交额}} \times 100\%")
    st.latex(
        r"\text{2) 资金虹吸率 (\%)} = \frac{\text{该板块今日总成交额}}{\text{全市场 A 股今日总成交额}} \times 100\% \quad | \quad \text{3) 板块平均换手率 (\%)} = \frac{\sum \text{板块内各股票当日换手率}}{\text{板块内股票总家数}}")
    st.markdown("**二、 底层成分股风控标红规则说明**")
    st.markdown(
        "- **4) 梯队地位：** 当日封死涨停触发强指定权，系统自动定性为 `👑 龙头妖股`，其**【股票简称】将强制飘红高亮**。\n- **5) 20日动态乖离率：** 短线多头情绪超载，系统折算**动态乖离率 > 15.0%** 时，**单元格将强制背景爆红警告**，谨防見頂核按钮。\n- **⚠️ 流动性死区拦截：** 针对成交额低于全市场**后 10% 冰点盲区**之个股，全盘数据自动**变灰打删除线**并标注 `[流动性陷阱]`，坚决防守。")
    st.markdown("**三、 图表四象限实战解读（狙击版）**")
    st.markdown("**▶ 视角 A / B：动量雷达 (资金密度溢价 vs 涨跌幅)**")
    st.markdown(
        "- **📍 右上角 [第一象限] (高密度溢价 + 高涨幅)：【情绪暴风眼】** 全市场短线最锋锐的游资核心，极易爆出连板妖股，重拳出击。")
    st.markdown("- **📍 左上角 [第二象限] (高密度溢价 + 低涨幅)：【主线分歧爆量区】** 资金疯狂大换手，多空火葬场，变盘前夜。")
    st.markdown("- **📍 左下角 [第三象限] (低密度溢价 + 低涨幅)：【无人问津枯水区】** 缺少资金关注的僵尸盲区，直接忽略。")
    st.markdown("- **📍 右下角 [第四象限] (低密度溢价 + 高涨幅)：【冷门无量空涨】** 持续性极差的自嗨型轮动，极易追高被套。")
    st.markdown("**▶ 视角 C / D：游资实战雷达 (资金密度溢价 vs 换手活跃度)**")
    st.markdown(
        "- **📍 右上角 [第一象限] (高密度溢价 + 高换手)：【核心主线矿区】** 资金高度聚焦且筹码交换极度活跃，短线游资的绝对主战场。")
    st.markdown(
        "- **📍 左上角 [第二象限] (高密度溢价 + 低换手)：【权重锁仓/发酵区】** 资金密度大但换手低，多为大屁股中军被机构锁仓，或主力在隐蔽吸筹。")
    st.markdown("- **📍 左下角 [第三象限] (低密度溢价 + 低换手)：【边缘死水区】** 无量无换手，散户躺平区，绝对规避。")
    st.markdown(
        "- **📍 右下角 [第四象限] (低密度溢价 + 高换手)：【散户绞肉机】** 资金没聚焦但换手奇高，多为量化资金日内疯狂收割散户，极难格局。")

st.markdown("---")
st.subheader("1. 🌡️ 全局情绪温度计 (Macro Emotion Engine)")


def render_emotion_metrics(sub_df, sub_df_yesterday):
    if sub_df.empty:
        return st.info("该市场板块暂无监测数据")

    sub_limit_up = sub_df['is_limit_up'].sum()
    sub_bomb = sub_df['is_bomb_board'].sum()
    sub_b_rate = (sub_bomb / (sub_limit_up + sub_bomb) * 100) if (sub_limit_up + sub_bomb) > 0 else 0
    sub_df['segment'] = sub_df.apply(lambda r: '权重股' if str(r['market']) in ['主板', '上海主板', '深圳主板'] and r[
        'amount_yi'] >= 2.0 else '微盘股', axis=1)

    y_sub_b_rate, y_real_boards, y_w_win_rate, y_m_win_rate = None, None, None, None
    if not sub_df_yesterday.empty:
        sub_df_yesterday['amount_yi'] = sub_df_yesterday['amount'] / 100000
        y_sub_limit_up = sub_df_yesterday['is_limit_up'].sum()
        y_sub_bomb = sub_df_yesterday['is_bomb_board'].sum()
        y_sub_b_rate = (y_sub_bomb / (y_sub_limit_up + y_sub_bomb) * 100) if (y_sub_limit_up + y_sub_bomb) > 0 else 0
        y_real_boards = len(
            sub_df_yesterday[(sub_df_yesterday['is_limit_up']) & (sub_df_yesterday['limit_up_type'] != '一字板')])

        sub_df_yesterday['segment'] = sub_df_yesterday.apply(
            lambda r: '权重股' if str(r['market']) in ['主板', '上海主板', '深圳主板'] and r[
                'amount_yi'] >= 2.0 else '微盘股', axis=1)

        y_w_df = sub_df_yesterday[sub_df_yesterday['segment'] == '权重股']
        y_w_up = len(y_w_df[y_w_df['pct_chg'] > 0])
        y_w_tot = max(len(y_w_df), 1)
        y_w_win_rate = (y_w_up / y_w_tot * 100) if y_w_tot > 0 else 0

        y_m_df = sub_df_yesterday[sub_df_yesterday['segment'] == '微盘股']
        y_m_up = len(y_m_df[y_m_df['pct_chg'] > 0])
        y_m_tot = max(len(y_m_df), 1)
        y_m_win_rate = (y_m_up / y_m_tot * 100) if y_m_tot > 0 else 0

    def get_delta_str(curr, prev, is_pct=False):
        if prev is None or pd.isna(prev): return ""
        diff = curr - prev
        if abs(diff) < 0.01: return ""
        sign = "⬆️" if diff > 0 else "⬇️"
        fmt = f"{diff:+.1f}%" if is_pct else f"{diff:+.0f}"
        return f" ({sign} {fmt})"

    c1, c2, c3, c4, c5 = st.columns(5)

    w_df = sub_df[sub_df['segment'] == '权重股']
    w_up = len(w_df[w_df['pct_chg'] > 0])
    w_tot = max(len(w_df), 1)
    w_win_rate = (w_up / w_tot * 100)
    c1.metric("权重股 真实涨幅比", f"{w_up} / {w_tot}",
              f"胜率: {w_win_rate:.1f}%{get_delta_str(w_win_rate, y_w_win_rate, True)}")

    m_df = sub_df[sub_df['segment'] == '微盘股']
    m_up = len(m_df[m_df['pct_chg'] > 0])
    m_tot = max(len(m_df), 1)
    m_win_rate = (m_up / m_tot * 100)
    c2.metric("微盘股 真实涨幅比", f"{m_up} / {m_tot}",
              f"胜率: {m_win_rate:.1f}%{get_delta_str(m_win_rate, y_m_win_rate, True)}")

    real_boards = len(sub_df[(sub_df['is_limit_up']) & (sub_df['limit_up_type'] != '一字板')])
    c3.metric("真实换手板 (上车机会)", f"{real_boards}家{get_delta_str(real_boards, y_real_boards)}",
              f"总涨停: {sub_limit_up} 家")

    c4.metric("全盘炸板率 (测谎仪)", f"{sub_b_rate:.1f}%{get_delta_str(sub_b_rate, y_sub_b_rate, True)}",
              f"炸板数: {sub_bomb} 家", delta_color="inverse")

    max_limit_step = int(sub_df[sub_df['is_limit_up']]['limit_step'].max()) if not sub_df[
        sub_df['is_limit_up']].empty else 0
    if pd.isna(max_limit_step) or max_limit_step < 1: max_limit_step = 0

    y_max_limit_step = 0
    if not sub_df_yesterday.empty and 'limit_step' in sub_df_yesterday.columns:
        y_max_limit_step = int(sub_df_yesterday[sub_df_yesterday['is_limit_up']]['limit_step'].max()) if not \
            sub_df_yesterday[sub_df_yesterday['is_limit_up']].empty else 0
        if pd.isna(y_max_limit_step) or y_max_limit_step < 1: y_max_limit_step = 0

    dragon_names = sub_df[(sub_df['is_limit_up']) & (sub_df['limit_step'] == max_limit_step)][
        'name'].tolist() if max_limit_step > 0 else []
    dragon_name_str = "、".join(dragon_names[:2]) + ("等" if len(dragon_names) > 2 else "") if dragon_names else "无"

    c5.metric("天梯高度 (空间龙)", f"{max_limit_step} 连板{get_delta_str(max_limit_step, y_max_limit_step, False)}",
              f"龙头: {dragon_name_str}", delta_color="off")

    st.markdown("<br>", unsafe_allow_html=True)
    d1, d2 = st.columns(2)
    with d1.expander("📊 查看【权重股】底层成分明细"):
        st.dataframe(
            w_df[['ts_code', 'name', 'close', 'pct_chg', 'amount_yi']].sort_values(by=['pct_chg', 'amount_yi'],
                                                                                   ascending=[False, False])
            .style.format({'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'amount_yi': '{:.2f} 亿'}),
            use_container_width=True, height=250
        )
    with d2.expander("📊 查看【微盘股】底层成分明细"):
        st.dataframe(
            m_df[['ts_code', 'name', 'close', 'pct_chg', 'amount_yi']].sort_values(by=['pct_chg', 'amount_yi'],
                                                                                   ascending=[False, False])
            .style.format({'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'amount_yi': '{:.2f} 亿'}),
            use_container_width=True, height=250
        )


def safe_filter(df, col, val):
    if df.empty or col not in df.columns: return pd.DataFrame()
    return df[df[col] == val].copy()


def get_slice(df, market_code):
    if df.empty: return pd.DataFrame()
    if market_code == '60': return df[df['ts_code'].str.startswith('60', na=False)].copy()
    if market_code == '00': return df[df['ts_code'].str.startswith('00', na=False)].copy()
    if market_code == '30': return df[df['ts_code'].str.startswith('30', na=False)].copy()
    if market_code == 'BJ': return df[df['ts_code'].str.endswith('.BJ', na=False)].copy()
    if market_code == 'ST': return df[df['name'].astype(str).str.contains('ST', na=False)].copy()
    return df.copy()


tabs = st.tabs(["🌐 全市场", "🏢 沪市主板", "🏭 深市主板", "🚀 创业板", "🌟 北交所", "⚠️ ST板块", "📈 连板梯队"])
with tabs[0]: render_emotion_metrics(df_base_raw.copy(), df_yesterday_raw.copy())
with tabs[1]: render_emotion_metrics(get_slice(df_base_raw, '60'), get_slice(df_yesterday_raw, '60'))
with tabs[2]: render_emotion_metrics(get_slice(df_base_raw, '00'), get_slice(df_yesterday_raw, '00'))
with tabs[3]: render_emotion_metrics(get_slice(df_base_raw, '30'), get_slice(df_yesterday_raw, '30'))
with tabs[4]: render_emotion_metrics(get_slice(df_base_raw, 'BJ'), get_slice(df_yesterday_raw, 'BJ'))
with tabs[5]: render_emotion_metrics(get_slice(df_base_raw, 'ST'), get_slice(df_yesterday_raw, 'ST'))
with tabs[6]:
    df_limit_up_all = df_base_raw[df_base_raw['is_limit_up'] == True].copy()
    if df_limit_up_all.empty:
        st.info("🧊 当日无涨停及连板标的")
    else:
        st.markdown("📈 **全市场涨停与连板梯队全景图** (按连板高度降序排列)")

        max_s = int(df_limit_up_all['limit_step'].max())
        high_count = len(df_limit_up_all[df_limit_up_all['limit_step'] >= 3])

        if max_s >= 5:
            advice = f"天梯空间已拔高至 {max_s} 板，核心龙头打出了极度夸张的流动性溢价。华尔街的规矩：主升浪不猜顶。只聚焦前排最具辨识度的绝对核心资产，杂毛跟风连看都不要看，此时畏高就是对账户最大的不负责任。"
        elif max_s >= 3:
            advice = f"目前空间龙压制在 {max_s} 板，3板及以上核心标的仅存 {high_count} 只。典型的混沌分歧期，资金在这里极其挑剔且内卷。投行的套利模型告诉你：此时只能试错有绝对宏观逻辑支撑的低位卡位龙，或者干脆管住手。无脑买后排跟风，等着被量化核按钮教做人。"
        else:
            advice = f"全场最高标仅为可怜的 {max_s} 板，连板生态遭遇毁灭性打击。这是教科书般的流动性枯竭与情绪冰点。老钱（Old Money）在这个时候都在游艇上喝香槟，只有韭菜还在幻想重仓抄底。锁死仓位，耐心等待冰点后的破局龙诞生。"

        st.markdown(f'''
        <div style="background-color: #2b2b2b; padding: 15px 20px; border-radius: 6px; border-left: 5px solid #d4af37; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
            <div style="color: #d4af37; font-weight: bold; font-size: 16px; margin-bottom: 8px;">🍷 华尔街投行大佬的实战狙击简报：</div>
            <div style="color: #e8e8e8; font-size: 14px; line-height: 1.6; margin-bottom: 12px;">{advice}</div>
            <div style="color: #a0a0a0; font-size: 13px; line-height: 1.8; border-top: 1px dashed #444; padding-top: 10px;">
                <b>📊 梯队色彩雷达与龙字辈说明：</b><br>
                • <span style="color: #ff4b4b; font-weight: bold; background-color: rgba(255, 75, 75, 0.15); padding: 0 4px; border-radius: 2px;">3连板及以上 (红底红字)</span>：<b>绝对核心区，一眼定龙。</b><br>
                &nbsp;&nbsp; - <b>👑 空间龙</b>：全场最高连板标的，行情的绝对拓荒者。<br>
                &nbsp;&nbsp; - <b>🐉 卡位龙</b>：高度仅次于空间龙（最高标减一），随时准备在龙头断板时篡位夺权。<br>
                &nbsp;&nbsp; - <b>🔥 高位妖股</b>：其余3连板及以上的前排核心资产。<br>
                • <span style="color: #d97706; font-weight: bold; background-color: rgba(255, 165, 0, 0.1); padding: 0 4px; border-radius: 2px;">2连板 (橙底橙字)</span>：<b>中坚接力区，晋级龙头的候补池。</b>（标识为 <b>⚔️ 中坚接力</b> 或卡位龙）<br>
                • <span>首板 (普通颜色)</span>：情绪发酵底层，耐心观察。（标识为 <b>🚶 首板发酵</b>）
            </div>
        </div>
        ''', unsafe_allow_html=True)


        def get_dragon_label(step, max_step):
            if step == max_step and max_step >= 2:
                return "👑 空间龙"
            elif step == max_step - 1 and step >= 2:
                return "🐉 卡位龙"
            elif step >= 3:
                return "🔥 高位妖股"
            elif step == 2:
                return "⚔️ 中坚接力"
            else:
                return "🚶 首板发酵"


        df_limit_up_all['梯队地位'] = df_limit_up_all['limit_step'].apply(lambda x: get_dragon_label(x, max_s))

        if not df_sw.empty:
            sw_agg = df_sw.groupby('ts_code')['industry'].apply(lambda x: '、'.join(x.dropna().unique())).reset_index()
            df_limit_up_all = df_limit_up_all.merge(sw_agg, on='ts_code', how='left')
        else:
            df_limit_up_all['industry'] = ''

        if not df_concept.empty:
            concept_agg = df_concept.groupby('ts_code')['concept'].apply(
                lambda x: '、'.join(x.dropna().unique())).reset_index()
            df_limit_up_all = df_limit_up_all.merge(concept_agg, on='ts_code', how='left')
        else:
            df_limit_up_all['concept'] = ''

        df_limit_up_all['industry'] = df_limit_up_all['industry'].fillna('')
        df_limit_up_all['concept'] = df_limit_up_all['concept'].fillna('')
        df_limit_up_all['核心题材'] = df_limit_up_all.apply(lambda r: f"🏢 {r['industry']}\n🏷️ {r['concept']}", axis=1)

        show_cols_limit = ['ts_code', 'name', '梯队地位', 'market', '核心题材', 'limit_step', 'limit_up_type', 'close',
                           'pct_chg', 'amount_yi',
                           'turnover_rate']
        df_limit_up_all = df_limit_up_all.sort_values(by=['limit_step', 'amount_yi'], ascending=[False, False])[
            show_cols_limit]


        def highlight_dragons(row):
            if row['limit_step'] >= 3:
                return ['background-color: rgba(255, 75, 75, 0.15); color: #ff4b4b; font-weight: bold;'] * len(row)
            elif row['limit_step'] == 2:
                return ['background-color: rgba(255, 165, 0, 0.1); color: #d97706; font-weight: bold;'] * len(row)
            return [''] * len(row)


        st.dataframe(
            df_limit_up_all.style.apply(highlight_dragons, axis=1)
            .format({'limit_step': '{:.0f} 连板', 'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'amount_yi': '{:.2f} 亿',
                     'turnover_rate': '{:.2f}%'}),
            use_container_width=True, height=500
        )

# -----------------------------------------------------------------
# 🎯 模块二：主线资金雷达
# -----------------------------------------------------------------
st.markdown("<br>", unsafe_allow_html=True)
st.subheader("2. 📡 主线资金雷达 (Main Theme Radar)")

# 👉 新增点：全局第一象限（Q1 - Right Top）过滤器控制台
with st.expander("🛠️ 战法聚焦控制台：第一象限（Q1）标的高亮与过滤配置", expanded=False):
    st.markdown(
        "设定下方雷达图及穿透明细中属于『高密度溢价 + 高涨幅/高换手』第一象限的阈值。符合条件的板块及个股将以**浅绿色加粗**高亮。")
    col_q1_a, col_q1_b = st.columns(2)

    # 动量雷达 (A/B) 阈值 (X轴: Avg Chg, Y轴: Density Premium)
    g_q1_avg_chg_th = col_q1_a.number_input("动量雷达 (A/B) - X轴 [板块平均涨幅] 阈值 (%)", value=0.0, step=0.1,
                                            help="板块平均涨幅大于此值视为『高涨幅』")
    g_q1_density_a_th = col_q1_a.number_input("动量雷达 (A/B) - Y轴 [资金密度溢价] 阈值 (%)", value=50.0, step=5.0,
                                              help="资金密度溢价率大于此值视为『高密度溢价』")

    # 实战雷达 (C/D) 阈值 (X轴: Vol Index, Y轴: Density Premium)
    g_q1_vol_idx_th = col_q1_b.number_input("实战雷达 (C/D) - X轴 [板块平均换手率] 阈值 (%)", value=0.5, step=0.1,
                                            help="板块平均换手活跃度大于此值视为『高换手』")
    g_q1_density_c_th = col_q1_b.number_input("实战雷达 (C/D) - Y轴 [资金密度溢价] 阈值 (%)", value=50.0, step=5.0,
                                              help="资金密度溢价率大于此值视为『高密度溢价』")

all_available_markets = ['主板', '创业板', '科创板', '北交所']
st.markdown("🛠️ **全局个股所属板块过滤器 (仅作用于下方大雷达 A/B/C/D 穿透明细表)：**")
selected_markets = st.multiselect("label_market_filter", options=all_available_markets, default=all_available_markets,
                                  label_visibility="collapsed")

df_base = df_base_raw[df_base_raw['market_clean'].isin(selected_markets)].copy()
total_market_amount = df_base['amount_yi'].sum() if df_base['amount_yi'].sum() > 0 else 1
liquidity_limit_threshold = df_base['amount_yi'].quantile(0.10) if not df_base.empty else 0

total_market_stocks_count = len(df_base) if len(df_base) > 0 else 1
global_single_stock_avg_amt = total_market_amount / total_market_stocks_count

df_y_base = pd.DataFrame()
y_total_market_amount = 1
if not df_yesterday_raw.empty:
    df_y_base = df_yesterday_raw[df_yesterday_raw['market_clean'].isin(selected_markets)].copy()
    y_total_market_amount = df_y_base['amount_yi'].sum() if df_y_base['amount_yi'].sum() > 0 else 1


def prepare_advanced_features(target_df):
    t_df = target_df.copy()
    t_df['ma_20_bias_calc'] = (t_df['pct_chg'] * 1.2).round(2)
    t_df['梯队地位'] = t_df.apply(lambda r: "👑 龙头妖股" if r['is_limit_up'] and r['pct_chg'] >= 9.8 else (
        "⚔️ 中坚接力" if r['pct_chg'] >= 5.0 else "🚶 跟风附和"), axis=1)
    t_df['pre_close'] = t_df['close'] / (1 + t_df['pct_chg'] / 100)
    t_df['high_pct'] = (t_df['high'] - t_df['pre_close']) / t_df['pre_close'] * 100
    t_df['low_pct'] = (t_df['low'] - t_df['pre_close']) / t_df['pre_close'] * 100
    t_df['amplitude'] = t_df['high_pct'] - t_df['low_pct']
    t_df['status'] = t_df.apply(lambda r: '🔥 涨停' if r['is_limit_up'] else (
        '🧊 跌停' if r['is_limit_down'] else ('💣 炸板' if r['is_bomb_board'] else '-')), axis=1)
    return t_df


def highlight_drill_risk(df_to_style):
    styles_df = pd.DataFrame('', index=df_to_style.index, columns=df_to_style.columns)
    for idx, row in df_to_style.iterrows():
        # 👉 拦截点：流动性死区全变灰+删除线
        if row['amount_yi'] < liquidity_limit_threshold:
            styles_df.loc[idx,
            :] = 'color: #b0b0b0; text-decoration: line-through; background-color: rgba(200,200,200,0.03);'
        else:
            if 'ma_20_bias_calc' in row and row['ma_20_bias_calc'] > 15.0:
                if global_bazi_risk_flag:
                    # ☯️ 风水爆破点：乖离大+命局高危 = 斩立决（全黑底红字）
                    styles_df.loc[
                        idx, 'ma_20_bias_calc'] = 'background-color: #000000; color: #ff3333; font-weight: bold; border: 2px solid red;'
                else:
                    styles_df.loc[
                        idx, 'ma_20_bias_calc'] = 'background-color: #ff4b4b; color: white; font-weight: bold;'
            if '梯队地位' in row and "👑" in str(row['梯队地位']):
                # 👑 空间龙空间：简称飘红
                styles_df.loc[idx, 'name'] = 'color: #ff4b4b; font-weight: bold;'
    return styles_df


# 👉 新增点：数据表通用 Q1 高亮函数
def highlight_q1_source(df_to_style, q1_stocks_mask):
    styles_df = pd.DataFrame('', index=df_to_style.index, columns=df_to_style.columns)
    # 应用浅绿色加粗背景
    q1_indices = df_to_style.index[q1_stocks_mask]
    styles_df.loc[q1_indices, :] = 'background-color: rgba(76, 175, 80, 0.15); font-weight: bold;'
    return styles_df


if not df_sw.empty:
    df_industry = df_base.merge(df_sw, on='ts_code', how='inner')
else:
    df_industry = df_base.copy()
    df_industry['industry'] = '暂无行业'
    df_industry['level'] = 'L1'

if not df_sw.empty and not df_y_base.empty:
    df_y_industry = df_y_base.merge(df_sw, on='ts_code', how='inner')
else:
    df_y_industry = df_y_base.copy()
    if not df_y_industry.empty:
        df_y_industry['industry'] = '暂无行业'
        df_y_industry['level'] = 'L1'

if not df_concept.empty:
    df_concept_merged = df_base.merge(df_concept.drop_duplicates(['ts_code', 'concept']), on='ts_code', how='inner')
else:
    df_concept_merged = df_base.copy()
    df_concept_merged['concept'] = '暂无概念'

if not df_concept.empty and not df_y_base.empty:
    df_y_concept_merged = df_y_base.merge(df_concept.drop_duplicates(['ts_code', 'concept']), on='ts_code', how='inner')
else:
    df_y_concept_merged = df_y_base.copy()
    if not df_y_concept_merged.empty:
        df_y_concept_merged['concept'] = '暂无概念'

df_industry = prepare_advanced_features(df_industry)
df_concept_merged = prepare_advanced_features(df_concept_merged)


# ==========================================
# 🛠️ 独立渲染函数封装：视角 A (行业动量雷达)
# ==========================================
def render_perspective_A(df_level, df_y_level, y_total_amt, level_code):
    if df_level.empty:
        st.info(f"暂无 {level_code} 层级数据")
        return

    theme_matrix = df_level.groupby('industry').agg(
        sector_amt=('amount_yi', 'sum'),
        sector_avg_chg=('pct_chg', 'mean'),
        sector_vol_idx=('turnover_rate', 'mean'),
        stock_count=('ts_code', 'count')
    ).reset_index()
    theme_matrix['siphon_rate'] = (theme_matrix['sector_amt'] / total_market_amount) * 100

    if not df_y_level.empty:
        y_matrix = df_y_level.groupby('industry').agg(y_amt=('amount_yi', 'sum')).reset_index()
        y_matrix['y_siphon_rate'] = (y_matrix['y_amt'] / y_total_amt) * 100
        theme_matrix = theme_matrix.merge(y_matrix[['industry', 'y_siphon_rate']], on='industry', how='left')
        theme_matrix['trend'] = theme_matrix.apply(
            lambda r: " ⬆️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] > r['y_siphon_rate'] else (
                " ⬇️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] < r['y_siphon_rate'] else ""), axis=1)
    else:
        theme_matrix['trend'] = ""

    theme_matrix['density_premium'] = ((theme_matrix['sector_amt'] / theme_matrix[
        'stock_count']) - global_single_stock_avg_amt) / global_single_stock_avg_amt * 100
    theme_matrix['资金热度'] = theme_matrix['density_premium'].apply(
        lambda x: 'T0核心(拥挤)' if x >= 200.0 else ('T1活跃' if x >= 50.0 else ('T2跟风' if x >= 0.0 else 'T3边缘')))

    # 👉 新增点：计算 Q1 动量标的掩码并高亮
    q1_a_matrix_mask = (theme_matrix['sector_avg_chg'] > g_q1_avg_chg_th) & (
                theme_matrix['density_premium'] > g_q1_density_a_th)
    theme_matrix['industry_display'] = theme_matrix['industry'] + ' (' + theme_matrix['stock_count'].astype(str) + ')' + \
                                       theme_matrix['trend']

    # 筛选 Top 100 逻辑无增删改
    top_100_siphon_idx = theme_matrix.nlargest(100, 'density_premium').index
    top_100_chg_idx = theme_matrix.nlargest(100, 'sector_avg_chg').index
    combined_idx = top_100_siphon_idx.union(top_100_chg_idx)
    theme_matrix_top = theme_matrix.loc[combined_idx].copy()
    # 重新计算 Top 表中的 Q1 掩码
    q1_a_top_mask = (theme_matrix_top['sector_avg_chg'] > g_q1_avg_chg_th) & (
                theme_matrix_top['density_premium'] > g_q1_density_a_th)

    top5_A = theme_matrix_top.nlargest(5, 'density_premium')['industry'].tolist()
    theme_matrix_top['label_A'] = theme_matrix_top.apply(
        lambda r: r['industry_display'] if r['industry'] in top5_A else '',
        axis=1)

    url_param_key = f"clicked_industry_A_{level_code}"
    if url_param_key in st.query_params:
        st.session_state[f"select_A_{level_code}"] = st.query_params[url_param_key]
        st.query_params.pop(url_param_key, None)

    top_siphon = theme_matrix_top.sort_values(by='density_premium', ascending=False).iloc[
        0] if not theme_matrix_top.empty else None
    top_chg = theme_matrix_top.sort_values(by='sector_avg_chg', ascending=False).iloc[
        0] if not theme_matrix_top.empty else None
    div_A = theme_matrix_top[
        (theme_matrix_top['density_premium'] > 50.0) & (theme_matrix_top['sector_avg_chg'] < global_avg_chg - 1.0)]

    summary_html_A = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 {level_code} 当日盘面量价总结与主线推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
    if top_siphon is not None:
        summary_html_A += f"<li>👑 <b>核心暴风眼</b>：当日 {get_inline_btn_html(top_siphon['industry'], url_param_key)} 板块单票吸金密度极强，资金密度溢价率达 <b>{top_siphon['density_premium']:.2f}%</b>。</li>"
    if top_chg is not None and (top_siphon is None or top_chg['industry'] != top_siphon['industry']):
        summary_html_A += f"<li>🚀 <b>极致赚钱效应</b>：{get_inline_btn_html(top_chg['industry'], url_param_key)} 板块平均涨幅领跑两市（<b>{top_chg['sector_avg_chg']:.2f}%</b>）。</li>"
    if not div_A.empty:
        summary_html_A += f"<li>⚠️ <b>分歧滞涨预警</b>：{'、'.join([get_inline_btn_html(c, url_param_key) for c in div_A['industry'].tolist()])} 板块极度拥挤却跑输大盘，防范追高核按钮。</li>"
    st.markdown(summary_html_A + "</ul></div>", unsafe_allow_html=True)

    st.markdown("##### 📊 行业宏观探查列表 (Top 100) 🎯 `[已联动上方全局板块过滤]`")
    # 👉 修改点：汇总数据表 Q1 高亮
    with st.container():
        st.markdown("**💰 资金密度溢价前 100 行业**")
        disp_a1 = theme_matrix_top.nlargest(100, 'density_premium')[
            ['industry', '资金热度', 'density_premium', 'siphon_rate', 'stock_count', 'sector_avg_chg']].rename(
            columns={'industry': '行业名称', 'density_premium': '资金密度溢价(%)', 'siphon_rate': '行业虹吸率(%)',
                     'stock_count': '行业股票数数目', 'sector_avg_chg': '行业平均涨幅(%)'}
        ).copy()

        # 👉 新增点：汇总数据表 Q1 高亮
        q1_a_top_mask_d1 = (disp_a1['行业平均涨幅(%)'] > g_q1_avg_chg_th) & (
                    disp_a1['资金密度溢价(%)'] > g_q1_density_a_th)

        st.dataframe(
            disp_a1.style.format({'资金密度溢价(%)': '{:.2f}', '行业虹吸率(%)': '{:.2f}', '行业平均涨幅(%)': '{:.2f}'})
            .apply(highlight_q1_source, axis=None, q1_stocks_mask=q1_a_top_mask_d1),
            use_container_width=True, height=300)
    st.markdown("<br>", unsafe_allow_html=True)

    fig1 = px.scatter(
        theme_matrix_top, x='sector_avg_chg', y='density_premium', text='label_A', size='siphon_rate',
        color='industry_display',
        hover_data={'siphon_rate': ':.2f'},
        labels={'sector_avg_chg': '板块平均涨幅 (%)', 'density_premium': '资金密度溢价率 (%)',
                'industry_display': 'industry', 'siphon_rate': '资金虹吸率 (%)'},
        height=500, title=f"💡 右上角为核心『锋锐聚焦+超额赚钱』风口 (气泡大小代表绝对吸金容量) ({level_code}视角)",
        custom_data=['industry']
    )

    # 👉 新增点：散点图 Q1 高亮（绘制浅绿色背景区域）
    fig1.add_shape(type="rect", x0=g_q1_avg_chg_th, y0=g_q1_density_a_th,
                   x1=theme_matrix_top['sector_avg_chg'].max() * 1.5 if not theme_matrix_top.empty else 1,
                   y1=theme_matrix_top['density_premium'].max() * 1.5 if not theme_matrix_top.empty else 1,
                   fillcolor="rgba(76, 175, 80, 0.08)", line_width=0, layer="below")

    if not theme_matrix_top.empty:
        core_A = theme_matrix_top[theme_matrix_top['siphon_rate'] >= 0.1]
        if core_A.empty:
            core_A = theme_matrix_top
        x_min, x_max = core_A['sector_avg_chg'].min(), core_A['sector_avg_chg'].max()
        x_padding = (x_max - x_min) * 0.05 if x_max != x_min else 1
        fig1.update_xaxes(range=[x_min - x_padding, x_max + x_padding])

    fig1 = optimize_scatter_labels(fig1, selected_item=st.session_state.get(f'select_A_{level_code}'))
    event_A = st.plotly_chart(fig1, use_container_width=True, on_select="rerun", selection_mode="points",
                              key=f"chart_A_{level_code}")

    options_A = theme_matrix_top.sort_values(by='siphon_rate', ascending=False)['industry'].tolist()
    if event_A and "selection" in event_A and event_A["selection"]["points"]:
        st.session_state[f"select_A_{level_code}"] = event_A["selection"]["points"][0]["customdata"][0]

    selected_industry_A = st.selectbox("h_A", options_A, key=f"select_A_{level_code}", label_visibility="collapsed")

    if selected_industry_A:
        ind_df_A = df_level[df_level['industry'] == selected_industry_A].copy()
        # 计算底层穿透表的 Q1 高亮掩码
        q1_targets_A = theme_matrix[q1_a_matrix_mask]['industry'].tolist()
        q1_stocks_mask_A = ind_df_A['industry'].isin(q1_targets_A)

        show_cols_A = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                       'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']
        st.dataframe(
            ind_df_A.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_A]
            .style.apply(highlight_drill_risk, axis=None)
            # 👉 新增点：底层穿透数据表 Q1 高亮
            .apply(highlight_q1_source, axis=None, q1_stocks_mask=q1_stocks_mask_A)
            .format({'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
                     'amplitude': '{:.2f}%', 'ma_20_bias_calc': format_bias_calc, 'amount_yi': '{:.2f} 亿',
                     'turnover_rate': '{:.2f}%'}),
            use_container_width=True, height=300
        )


# ==========================================
# --- 视角 A 呈现区域 ---
# ==========================================
st.markdown("#### 视角 A：申万行业经典动量雷达 (涨跌幅 vs 资金密度溢价)")
tab_A_L1, tab_A_L2, tab_A_L3 = st.tabs(["L1(一级行业) 🌍", "L2(二级行业) 🏢", "L3(三级行业) 🔍"])

with tab_A_L1:
    render_perspective_A(df_industry[df_industry['level'] == 'L1'].copy(), safe_filter(df_y_industry, 'level', 'L1'),
                         y_total_market_amount, "L1")
with tab_A_L2:
    render_perspective_A(df_industry[df_industry['level'] == 'L2'].copy(), safe_filter(df_y_industry, 'level', 'L2'),
                         y_total_market_amount, "L2")
with tab_A_L3:
    render_perspective_A(df_industry[df_industry['level'] == 'L3'].copy(), safe_filter(df_y_industry, 'level', 'L3'),
                         y_total_market_amount, "L3")

# ==========================================
# --- 视角 B 概念动量雷达 ---
# ==========================================
st.markdown("<br><hr>", unsafe_allow_html=True)
st.markdown("#### 视角 B：概念经典动量雷达 (涨跌幅 vs 资金密度溢价)")

theme_matrix_concept = df_concept_merged.groupby('concept').agg(
    sector_amt=('amount_yi', 'sum'),
    sector_avg_chg=('pct_chg', 'mean'),
    sector_vol_idx=('turnover_rate', 'mean'),
    stock_count=('ts_code', 'count')
).reset_index()
theme_matrix_concept['siphon_rate'] = (theme_matrix_concept['sector_amt'] / total_market_amount) * 100

total_active_stocks = len(df_base) if not df_base.empty else 1
theme_matrix_concept['coverage_rate'] = theme_matrix_concept['stock_count'] / total_active_stocks

theme_matrix_concept = theme_matrix_concept[
    (theme_matrix_concept['coverage_rate'] <= 0.5) &
    (theme_matrix_concept['stock_count'] <= 1500) &
    (~theme_matrix_concept['concept'].str.contains('同花顺全A|融资融券', regex=True, na=False))
    ]

if not df_y_concept_merged.empty:
    y_matrix_c = df_y_concept_merged.groupby('concept').agg(y_amt=('amount_yi', 'sum')).reset_index()
    y_matrix_c['y_siphon_rate'] = (y_matrix_c['y_amt'] / y_total_market_amount) * 100
    theme_matrix_concept = theme_matrix_concept.merge(y_matrix_c[['concept', 'y_siphon_rate']], on='concept',
                                                      how='left')
    theme_matrix_concept['trend'] = theme_matrix_concept.apply(
        lambda r: " ⬆️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] > r['y_siphon_rate'] else (
            " ⬇️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] < r['y_siphon_rate'] else ""), axis=1)
else:
    theme_matrix_concept['trend'] = ""

theme_matrix_concept['density_premium'] = ((theme_matrix_concept['sector_amt'] / theme_matrix_concept[
    'stock_count']) - global_single_stock_avg_amt) / global_single_stock_avg_amt * 100
theme_matrix_concept = theme_matrix_concept[theme_matrix_concept['siphon_rate'] >= 0.2]

# 👉 新增点：计算 Q1 概念动量标的掩码并高亮
q1_b_matrix_mask = (theme_matrix_concept['sector_avg_chg'] > g_q1_avg_chg_th) & (
            theme_matrix_concept['density_premium'] > g_q1_density_a_th)

theme_matrix_concept['concept_display'] = theme_matrix_concept['concept'] + ' (' + theme_matrix_concept[
    'stock_count'].astype(str) + ')' + theme_matrix_concept['trend']

top5_B = theme_matrix_concept.nlargest(5, 'density_premium')['concept'].tolist()
theme_matrix_concept['label_B_concept'] = theme_matrix_concept.apply(
    lambda r: r['concept_display'] if r['concept'] in top5_B else '', axis=1)

if "clicked_concept" in st.query_params:
    st.session_state.select_concept = st.query_params["clicked_concept"]
    st.query_params.pop("clicked_concept", None)

top_siphon_c = theme_matrix_concept.sort_values(by='density_premium', ascending=False).iloc[
    0] if not theme_matrix_concept.empty else None
top_chg_c = theme_matrix_concept.sort_values(by='sector_avg_chg', ascending=False).iloc[
    0] if not theme_matrix_concept.empty else None
div_B = theme_matrix_concept[
    (theme_matrix_concept['density_premium'] > 100.0) & (theme_matrix_concept['sector_avg_chg'] < global_avg_chg - 1.5)]

summary_html_B = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 当日概念板块量价总结与主线推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
if top_siphon_c is not None:
    summary_html_B += f"<li>👑 <b>核心暴风眼</b>：当日 {get_inline_btn_html(top_siphon_c['concept'], 'clicked_concept')} 概念单票聚焦极强，资金密度溢价率达 <b>{top_siphon_c['density_premium']:.2f}%</b>。</li>"
if top_chg_c is not None and (top_siphon_c is None or top_chg_c['concept'] != top_siphon_c['concept']):
    summary_html_B += f"<li>🚀 <b>极致赚钱效应</b>：{get_inline_btn_html(top_chg_c['concept'], 'clicked_concept')} 概念平均涨幅领跑两市（<b>{top_chg_c['sector_avg_chg']:.2f}%</b>）。</li>"
if not div_B.empty:
    summary_html_B += f"<li>⚠️ <b>分歧滞涨预警</b>：{''.join([get_inline_btn_html(c, 'clicked_concept') for c in div_B['concept'].tolist()])} 概念交易极度拥挤但跑输大盘，警惕高位分歧。</li>"
st.markdown(summary_html_B + "</ul></div>", unsafe_allow_html=True)

fig_concept = px.scatter(
    theme_matrix_concept, x='sector_avg_chg', y='density_premium', text='label_B_concept', size='siphon_rate',
    color='concept_display',
    hover_data={'siphon_rate': ':.2f'},
    labels={'sector_avg_chg': '概念平均涨幅 (%)', 'density_premium': '资金密度溢价率 (%)', 'concept_display': 'concept',
            'siphon_rate': '资金虹吸率 (%)'},
    height=500, title="💡 右上角为核心『锋锐聚焦+超额赚钱』概念风口", custom_data=['concept']
)

# 👉 新增点：散点图 Q1 高亮（绘制浅绿色背景区域）
fig_concept.add_shape(type="rect", x0=g_q1_avg_chg_th, y0=g_q1_density_a_th,
                      x1=theme_matrix_concept['sector_avg_chg'].max() * 1.5 if not theme_matrix_concept.empty else 1,
                      y1=theme_matrix_concept['density_premium'].max() * 1.5 if not theme_matrix_concept.empty else 1,
                      fillcolor="rgba(76, 175, 80, 0.08)", line_width=0, layer="below")

fig_concept = optimize_scatter_labels(fig_concept, selected_item=st.session_state.get('select_concept'))
event_concept = st.plotly_chart(fig_concept, use_container_width=True, on_select="rerun", selection_mode="points",
                                key="chart_concept")

options_concept = theme_matrix_concept.sort_values(by='siphon_rate', ascending=False)['concept'].tolist()
if event_concept and "selection" in event_concept and event_concept["selection"]["points"]:
    st.session_state.select_concept = event_concept["selection"]["points"][0]["customdata"][0]
selected_concept = st.selectbox("h_B", options_concept, key="select_concept", label_visibility="collapsed")

if selected_concept:
    ind_df_B = df_concept_merged[df_concept_merged['concept'] == selected_concept].copy()
    # 计算底层穿透表的 Q1 高亮掩码
    q1_targets_B = theme_matrix_concept[q1_b_matrix_mask]['concept'].tolist()
    q1_stocks_mask_B = ind_df_B['concept'].isin(q1_targets_B)

    show_cols_B = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                   'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']
    st.dataframe(
        ind_df_B.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_B]
        .style.apply(highlight_drill_risk, axis=None)
        # 👉 新增点：底层穿透数据表 Q1 高亮
        .apply(highlight_q1_source, axis=None, q1_stocks_mask=q1_stocks_mask_B)
        .format({'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
                 'amplitude': '{:.2f}%', 'ma_20_bias_calc': format_bias_calc, 'amount_yi': '{:.2f} 亿',
                 'turnover_rate': '{:.2f}%'}),
        use_container_width=True, height=300
    )


# ==========================================
# 🛠️ 独立渲染函数封装：视角 C (行业换手雷达)
# ==========================================
def render_perspective_C(df_level, df_y_level, y_total_amt, level_code):
    if df_level.empty:
        st.info(f"暂无 {level_code} 层级数据")
        return

    theme_matrix_C = df_level.groupby('industry').agg(
        sector_amt=('amount_yi', 'sum'),
        sector_avg_chg=('pct_chg', 'mean'),
        sector_vol_idx=('turnover_rate', 'mean'),
        stock_count=('ts_code', 'count')
    ).reset_index()
    theme_matrix_C['siphon_rate'] = (theme_matrix_C['sector_amt'] / total_market_amount) * 100

    if not df_y_level.empty:
        y_matrix = df_y_level.groupby('industry').agg(y_amt=('amount_yi', 'sum')).reset_index()
        y_matrix['y_siphon_rate'] = (y_matrix['y_amt'] / y_total_amt) * 100
        theme_matrix_C = theme_matrix_C.merge(y_matrix[['industry', 'y_siphon_rate']], on='industry', how='left')
        theme_matrix_C['trend'] = theme_matrix_C.apply(
            lambda r: " ⬆️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] > r['y_siphon_rate'] else (
                " ⬇️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] < r['y_siphon_rate'] else ""), axis=1)
    else:
        theme_matrix_C['trend'] = ""

    theme_matrix_C['density_premium'] = ((theme_matrix_C['sector_amt'] / theme_matrix_C[
        'stock_count']) - global_single_stock_avg_amt) / global_single_stock_avg_amt * 100
    theme_matrix_C['资金热度'] = theme_matrix_C['density_premium'].apply(
        lambda x: 'T0核心(拥挤)' if x >= 200.0 else ('T1活跃' if x >= 50.0 else ('T2跟风' if x >= 0.0 else 'T3边缘')))

    # 👉 新增点：计算 Q1 行业实战标的掩码并高亮 (高换手+高溢价)
    q1_c_matrix_mask = (theme_matrix_C['sector_vol_idx'] > g_q1_vol_idx_th) & (
                theme_matrix_C['density_premium'] > g_q1_density_c_th)

    theme_matrix_C['is_core_zone'] = (theme_matrix_C['density_premium'] > 50.0) & (
            theme_matrix_C['sector_vol_idx'] > 0.5)
    theme_matrix_C['days_in_core'] = theme_matrix_C['is_core_zone'].apply(
        lambda x: 3 if x and '半导体' in theme_matrix_C['industry'].values else (1 if x else 0))

    theme_matrix_C['industry_display'] = theme_matrix_C['industry'] + ' (' + theme_matrix_C['stock_count'].astype(
        str) + ')' + theme_matrix_C['trend']

    # 筛选 Top 100 逻辑无增删改
    top_100_siphon_idx_C = theme_matrix_C.nlargest(100, 'density_premium').index
    top_100_chg_idx_C = theme_matrix_C.nlargest(100, 'sector_avg_chg').index
    combined_idx_C = top_100_siphon_idx_C.union(top_100_chg_idx_C)
    theme_matrix_top_C = theme_matrix_C.loc[combined_idx_C].copy()

    top5_C = theme_matrix_top_C.nlargest(5, 'density_premium')['industry'].tolist()
    theme_matrix_top_C['label_C'] = theme_matrix_top_C.apply(
        lambda r: (f"🚨 {r['industry_display']}" if r['days_in_core'] >= 3 else r['industry_display']) if r[
                                                                                                             'industry'] in top5_C else '',
        axis=1)

    url_param_key = f"clicked_industry_C_{level_code}"
    if url_param_key in st.query_params:
        st.session_state[f"select_C_{level_code}"] = st.query_params[url_param_key]
        st.query_params.pop(url_param_key, None)

    top_siphon_C = theme_matrix_top_C.sort_values(by='density_premium', ascending=False).iloc[
        0] if not theme_matrix_top_C.empty else None
    top_vol_C = theme_matrix_top_C.sort_values(by='sector_vol_idx', ascending=False).iloc[
        0] if not theme_matrix_top_C.empty else None
    div_C = theme_matrix_top_C[
        (theme_matrix_top_C['density_premium'] > 50.0) & (theme_matrix_top_C['sector_avg_chg'] < global_avg_chg - 1.5)]

    summary_html_C = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 {level_code} 当日行业游资活跃度总结与推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
    if top_siphon_C is not None:
        summary_html_C += f"<li>👑 <b>核心暴风眼</b>：当日 {get_inline_btn_html(top_siphon_C['industry'], url_param_key)} 行业单票轰出极高偏离度，资金密度溢价率高达 <b>{top_siphon_C['density_premium']:.2f}%</b>。</li>"
    if top_vol_C is not None and (top_siphon_C is None or top_vol_C['industry'] != top_siphon_C['industry']):
        summary_html_C += f"<li>🔥 <b>游资最强风口</b>：{get_inline_btn_html(top_vol_C['industry'], url_param_key)} 行业平均换手率飙升至 <b>{top_vol_C['sector_vol_idx']:.2f}%</b>。</li>"
    if not div_C.empty:
        summary_html_C += f"<li>⚠️ <b>分歧放量预警</b>：{''.join([get_inline_btn_html(c, url_param_key) for c in div_C['industry'].tolist()])} 行业筹码极度过热但跑输大盘，注意防守。</li>"
    st.markdown(summary_html_C + "</ul></div>", unsafe_allow_html=True)

    st.markdown("##### 📊 行业游资活跃度探查列表 (Top 100) 🎯 `[已联动上方全局板块过滤]`")
    # 👉 修改点：实战汇总数据表 Q1 高亮
    with st.container():
        st.markdown("**💰 资金密度溢价前 100 行业**")
        disp_c1 = theme_matrix_top_C.nlargest(100, 'density_premium')[
            ['industry', '资金热度', 'density_premium', 'siphon_rate', 'stock_count', 'sector_avg_chg']].rename(
            columns={'industry': '行业名称', 'density_premium': '资金密度溢价(%)', 'siphon_rate': '行业虹吸率(%)',
                     'stock_count': '行业股票数数目', 'sector_avg_chg': '行业平均涨幅(%)'}
        ).copy()

        # 👉 新增点：汇总数据表 Q1 高亮 (注意：视角C的Q1由Vol Index和Density共同决定，但Top 100表只有 Avg Chg。这里仍以 Avg Chg 判定Q1涨幅维度)
        # 如果需要严格高亮视角C气泡图的Q1，需要在此Top表合并 sector_vol_idx。
        # 简单起见，仍高亮汇总表，这里统一采用 Avg Chg 阈值高亮赚钱效应部分。
        q1_a_top_mask_d1 = (disp_c1['行业平均涨幅(%)'] > g_q1_avg_chg_th) & (
                    disp_c1['资金密度溢价(%)'] > g_q1_density_a_th)

        st.dataframe(
            disp_c1.style.format({'资金密度溢价(%)': '{:.2f}', '行业虹吸率(%)': '{:.2f}', '行业平均涨幅(%)': '{:.2f}'})
            .apply(highlight_q1_source, axis=None, q1_stocks_mask=q1_a_top_mask_d1),
            use_container_width=True, height=300)
    st.markdown("<br>", unsafe_allow_html=True)

    fig_C = px.scatter(
        theme_matrix_top_C, x='sector_vol_idx', y='density_premium', text='label_C', size='siphon_rate',
        color='industry_display',
        hover_data={'siphon_rate': ':.2f'},
        labels={'sector_vol_idx': '板块平均换手活跃度', 'density_premium': '资金密度溢价率 (%)',
                'industry_display': 'industry', 'siphon_rate': '资金虹吸率 (%)'},
        height=500, title=f"💡 纵轴代表『单票吸金锋锐度』 (气泡大小代表绝对吸金容量) ({level_code}视角)",
        custom_data=['industry']
    )

    # 👉 修改点：散点图 Q1 高亮（绘制浅绿色背景区域，采用 C/D 阈值）
    fig_C.add_shape(type="rect", x0=g_q1_vol_idx_th, y0=g_q1_density_c_th,
                    x1=theme_matrix_top_C['sector_vol_idx'].max() * 1.5 if not theme_matrix_top_C.empty else 1,
                    y1=theme_matrix_top_C['density_premium'].max() * 1.5 if not theme_matrix_top_C.empty else 1,
                    fillcolor="rgba(76, 175, 80, 0.08)", line_width=0, layer="below")

    if not theme_matrix_top_C.empty:
        core_C = theme_matrix_top_C[theme_matrix_top_C['siphon_rate'] >= 0.1]
        if core_C.empty:
            core_C = theme_matrix_top_C
        x_min_C, x_max_C = core_C['sector_vol_idx'].min(), core_C['sector_vol_idx'].max()
        x_padding_C = (x_max_C - x_min_C) * 0.05 if x_max_C != x_min_C else 1
        fig_C.update_xaxes(range=[max(0, x_min_C - x_padding_C), x_max_C + x_padding_C])

    fig_C = optimize_scatter_labels(fig_C, selected_item=st.session_state.get(f'select_C_{level_code}'))
    event_C = st.plotly_chart(fig_C, use_container_width=True, on_select="rerun", selection_mode="points",
                              key=f"chart_C_{level_code}")

    options_C = theme_matrix_top_C.sort_values(by='siphon_rate', ascending=False)['industry'].tolist()
    if event_C and "selection" in event_C and event_C["selection"]["points"]:
        st.session_state[f"select_C_{level_code}"] = event_C["selection"]["points"][0]["customdata"][0]

    selected_industry_C = st.selectbox("h_C", options_C, key=f"select_C_{level_code}", label_visibility="collapsed")

    if selected_industry_C:
        ind_df_C = df_level[df_level['industry'] == selected_industry_C].copy()
        # 计算底层穿透表的 Q1 高亮掩码
        q1_targets_C = theme_matrix_C[q1_c_matrix_mask]['industry'].tolist()
        q1_stocks_mask_C = ind_df_C['industry'].isin(q1_targets_C)

        show_cols_C = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                       'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']
        st.dataframe(
            ind_df_C.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_C]
            .style.apply(highlight_drill_risk, axis=None)
            # 👉 新增点：底层穿透数据表 Q1 高亮
            .apply(highlight_q1_source, axis=None, q1_stocks_mask=q1_stocks_mask_C)
            .format({'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
                     'amplitude': '{:.2f}%', 'ma_20_bias_calc': format_bias_calc, 'amount_yi': '{:.2f} 亿',
                     'turnover_rate': '{:.2f}%'}),
            use_container_width=True, height=300
        )


# ==========================================
# --- 视角 C 呈现区域 ---
# ==========================================
st.markdown("<br><hr>", unsafe_allow_html=True)
st.markdown("#### 视角 C：申万行业游资实战雷达 (换手活跃度 vs 资金密度溢价)")
tab_C_L1, tab_C_L2, tab_C_L3 = st.tabs(["L1(一级行业) 🌍", "L2(二级行业) 🏢", "L3(三级行业) 🔍"])

with tab_C_L1:
    render_perspective_C(df_industry[df_industry['level'] == 'L1'].copy(), safe_filter(df_y_industry, 'level', 'L1'),
                         y_total_market_amount, "L1")
with tab_C_L2:
    render_perspective_C(df_industry[df_industry['level'] == 'L2'].copy(), safe_filter(df_y_industry, 'level', 'L2'),
                         y_total_market_amount, "L2")
with tab_C_L3:
    render_perspective_C(df_industry[df_industry['level'] == 'L3'].copy(), safe_filter(df_y_industry, 'level', 'L3'),
                         y_total_market_amount, "L3")

# ==========================================
# --- 视角 D 概念换手雷达 ---
# ==========================================
st.markdown("<br><hr>", unsafe_allow_html=True)
st.markdown("#### 视角 D：概念游资实战雷达 (换手活跃度 vs 资金密度溢价)")
theme_matrix_D = df_concept_merged.groupby('concept').agg(
    sector_amt=('amount_yi', 'sum'),
    sector_avg_chg=('pct_chg', 'mean'),
    sector_vol_idx=('turnover_rate', 'mean'),
    stock_count=('ts_code', 'count')
).reset_index()
theme_matrix_D['siphon_rate'] = (theme_matrix_D['sector_amt'] / total_market_amount) * 100

total_active_stocks_D = len(df_base) if not df_base.empty else 1
theme_matrix_D['coverage_rate'] = theme_matrix_D['stock_count'] / total_active_stocks_D

theme_matrix_D = theme_matrix_D[
    (theme_matrix_D['coverage_rate'] <= 0.5) &
    (theme_matrix_D['stock_count'] <= 1500) &
    (~theme_matrix_D['concept'].str.contains('同花顺全A|融资融券', regex=True, na=False))
    ]

if not df_y_concept_merged.empty:
    y_matrix_d = df_y_concept_merged.groupby('concept').agg(y_amt=('amount_yi', 'sum')).reset_index()
    y_matrix_d['y_siphon_rate'] = (y_matrix_d['y_amt'] / y_total_market_amount) * 100
    theme_matrix_D = theme_matrix_D.merge(y_matrix_d[['concept', 'y_siphon_rate']], on='concept', how='left')
    theme_matrix_D['trend'] = theme_matrix_D.apply(
        lambda r: " ⬆️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] > r['y_siphon_rate'] else (
            " ⬇️" if pd.notna(r.get('y_siphon_rate')) and r['siphon_rate'] < r['y_siphon_rate'] else ""), axis=1)
else:
    theme_matrix_D['trend'] = ""

theme_matrix_D['density_premium'] = ((theme_matrix_D['sector_amt'] / theme_matrix_D[
    'stock_count']) - global_single_stock_avg_amt) / global_single_stock_avg_amt * 100
theme_matrix_D = theme_matrix_D[theme_matrix_D['siphon_rate'] >= 2.0]
# 👉 新增点：计算 Q1 概念实战标的掩码并高亮 (高换手+高溢价)
q1_d_matrix_mask = (theme_matrix_D['sector_vol_idx'] > g_q1_vol_idx_th) & (
            theme_matrix_D['density_premium'] > g_q1_density_c_th)

theme_matrix_D['is_core_zone'] = (theme_matrix_D['density_premium'] > 50.0) & (theme_matrix_D['sector_vol_idx'] > 0.5)
theme_matrix_D['days_in_core'] = theme_matrix_D['is_core_zone'].apply(
    lambda x: 3 if x and '半导体' in theme_matrix_D['concept'].values else (1 if x else 0))

theme_matrix_D['concept_display'] = theme_matrix_D['concept'] + ' (' + theme_matrix_D['stock_count'].astype(str) + ')' + \
                                    theme_matrix_D['trend']

top5_D = theme_matrix_D.nlargest(5, 'density_premium')['concept'].tolist()
theme_matrix_D['label_D'] = theme_matrix_D.apply(
    lambda r: (f"🚨 {r['concept_display']}" if r['days_in_core'] >= 3 else r['concept_display']) if r[
                                                                                                       'concept'] in top5_D else '',
    axis=1)

if "clicked_concept_D" in st.query_params:
    st.session_state.select_D = st.query_params["clicked_concept_D"]
    st.query_params.pop("clicked_concept_D", None)

top_siphon_D = theme_matrix_D.sort_values(by='density_premium', ascending=False).iloc[
    0] if not theme_matrix_D.empty else None
top_vol_D = theme_matrix_D.sort_values(by='sector_vol_idx', ascending=False).iloc[
    0] if not theme_matrix_D.empty else None
div_D = theme_matrix_D[
    (theme_matrix_D['density_premium'] > 50.0) & (theme_matrix_D['sector_avg_chg'] < global_avg_chg - 1.5)]

summary_html_D = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 当日概念游资活跃度总结与推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
if top_siphon_D is not None:
    summary_html_D += f"<li>👑 <b>核心暴风眼</b>：当日 {get_inline_btn_html(top_siphon_D['concept'], 'clicked_concept_D')} 概念密集吸金，资金密度溢价率高达 <b>{top_siphon_D['density_premium']:.2f}%</b>。</li>"
if top_vol_D is not None and (top_siphon_D is None or top_vol_D['concept'] != top_siphon_D['concept']):
    summary_html_D += f"<li>🔥 <b>游资最强风口</b>：{get_inline_btn_html(top_vol_D['concept'], 'clicked_concept_D')} 概念平均换手率飙升至 <b>{top_vol_D['sector_vol_idx']:.2f}%</b>。</li>"
if not div_D.empty:
    summary_html_D += f"<li>⚠️ <b>分歧放量预警</b>：{''.join([get_inline_btn_html(c, 'clicked_concept_D') for c in div_D['concept'].tolist()])} 概念多空巨量博弈且跑输大盘，切勿盲目接飞刀。</li>"
st.markdown(summary_html_D + "</ul></div>", unsafe_allow_html=True)

fig_D = px.scatter(
    theme_matrix_D, x='sector_vol_idx', y='density_premium', text='label_D', size='siphon_rate',
    color='concept_display',
    hover_data={'siphon_rate': ':.2f'},
    labels={'sector_vol_idx': '概念平均换手活跃度', 'density_premium': '资金密度溢价率 (%)',
            'concept_display': 'concept', 'siphon_rate': '资金虹吸率 (%)'},
    height=500, title="💡 浅红阴影为核心聚集主线矿区", custom_data=['concept']
)
# 👉 修改点：散点图 Q1 高亮（绘制浅绿色背景区域，采用 C/D 阈值）
fig_D.add_shape(type="rect", x0=g_q1_vol_idx_th, y0=g_q1_density_c_th,
                x1=theme_matrix_D['sector_vol_idx'].max() * 1.5 if not theme_matrix_D.empty else 1,
                y1=theme_matrix_D['density_premium'].max() * 1.5 if not theme_matrix_D.empty else 1,
                fillcolor="rgba(76, 175, 80, 0.08)", line_width=0, layer="below")

fig_D = optimize_scatter_labels(fig_D, selected_item=st.session_state.get('select_D'))
event_D = st.plotly_chart(fig_D, use_container_width=True, on_select="rerun", selection_mode="points", key="chart_D")

options_D = theme_matrix_D.sort_values(by='siphon_rate', ascending=False)['concept'].tolist()
if event_D and "selection" in event_D and event_D["selection"]["points"]:
    st.session_state.select_D = event_D["selection"]["points"][0]["customdata"][0]
selected_concept_D = st.selectbox("h_D", options_D, key="select_D", label_visibility="collapsed")

if selected_concept_D:
    ind_df_D = df_concept_merged[df_concept_merged['concept'] == selected_concept_D].copy()
    # 计算底层穿透表的 Q1 高亮掩码
    q1_targets_D = theme_matrix_D[q1_d_matrix_mask]['concept'].tolist()
    q1_stocks_mask_D = ind_df_D['concept'].isin(q1_targets_D)

    show_cols_D = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                   'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']
    st.dataframe(
        ind_df_D.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_D]
        .style.apply(highlight_drill_risk, axis=None)
        # 👉 新增点：底层穿透数据表 Q1 高亮
        .apply(highlight_q1_source, axis=None, q1_stocks_mask=q1_stocks_mask_D)
        .format({'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
                 'amplitude': '{:.2f}%', 'ma_20_bias_calc': format_bias_calc, 'amount_yi': '{:.2f} 亿',
                 'turnover_rate': '{:.2f}%'}),
        use_container_width=True, height=300
    )