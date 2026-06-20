import streamlit as st
import pandas as pd
import psycopg2
import plotly.express as px
import plotly.graph_objects as go
import datetime
import urllib.parse
from lunar_python import Lunar, Solar

# ================= 1. 页面与数据库配置 =================
st.set_page_config(page_title="EndSuffering Daily Report", page_icon="💰", layout="wide")
DB_URL = "postgresql://postgres:endsuffering@localhost:5432/stock_db"


@st.cache_data(ttl=60)
def fetch_all_available_dates():
    """💡 核心捞取：从数据库动态捞取所有有数据的交易日名单，供时光机选择"""
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
    """💡 核心联动：根据用户选定的历史账期，精准拉取数据切片"""
    try:
        conn = psycopg2.connect(DB_URL)

        query_today = f"""
            SELECT d.trade_date, d.ts_code, b.name, b.market, d.close, d.high, d.low, d.pct_chg, d.amount,
                   f.is_limit_up, f.is_limit_down, f.is_bomb_board, f.limit_up_type, f.turnover_rate
            FROM daily_data d
            JOIN report_daily_factors f ON d.ts_code = f.ts_code AND d.trade_date::varchar = f.trade_date::varchar
            LEFT JOIN stock_basic b ON d.ts_code = b.ts_code
            WHERE d.trade_date::varchar = '{selected_date}';
        """
        df_today = pd.read_sql(query_today, conn)

        current_idx = all_fetched_dates.index(selected_date) if selected_date in all_fetched_dates else 0
        history_window_dates = all_fetched_dates[current_idx: current_idx + 3]
        date_str_list = ",".join([f"'{d}'" for d in history_window_dates])

        df_history = pd.read_sql(
            f"SELECT trade_date, ts_code, is_limit_up FROM report_daily_factors WHERE trade_date::varchar IN ({date_str_list});",
            conn)

        df_sw = pd.read_sql("SELECT ts_code, industry_name AS industry FROM stock_sw_industry", conn)
        df_concept = pd.read_sql("SELECT ts_code, concept_name AS concept FROM stock_concept", conn)

        conn.close()
        return df_today, df_history, selected_date, df_sw, df_concept
    except Exception as e:
        st.error(f"❌ 数据时光机加载失败: {e}")
        return pd.DataFrame(), pd.DataFrame(), None, pd.DataFrame(), pd.DataFrame()


# ================= 2. 网页时光隧道入口与数据总线 =================
st.title("💰 EndSuffering Daily Report")

# 获取全部账期
available_dates = fetch_all_available_dates()
if not available_dates:
    st.warning("⚠️ 数据库内未监测到任何有效的历史账期切片。请确保管道已正常清洗数据。")
    st.stop()

# 💡 精美的交易账期历史切片选择器
col_pit, col_empty = st.columns([0.25, 0.75])
with col_pit:
    chosen_date = st.selectbox(
        "📅 交易账期历史切片 (PIT Selector)",
        options=available_dates,
        index=0,
        key="global_pit_date"
    )

# 传入用户选择的日期，动态加载底层数据
df_base_raw, df_hist, target_date, df_sw, df_concept = load_production_matrix(chosen_date, available_dates)

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


# ================= 内嵌按钮 HTML 与 图表标签优化引擎 =================
def get_inline_btn_html(name, param_key):
    """黑魔法：生成不换行、不弹新窗口的内嵌 HTML 按钮"""
    safe_name = urllib.parse.quote(name)
    return f'<a href="/?{param_key}={safe_name}" target="_self" style="background-color: #ffffff; border: 1px solid #c8c8c8; border-radius: 4px; padding: 2px 8px; text-decoration: none; color: #333; font-weight: bold; display: inline-block; box-shadow: 0 1px 2px rgba(0,0,0,0.1); margin: 0 2px;">【{name}】</a>'


def optimize_scatter_labels(fig, selected_item=None):
    """🌟 核心优化：回归原生 Trace Text，确保图例双击隐藏功能正常，同时通过动态坐标偏移防重叠"""

    # 物理防重叠引擎：文字将在以下四个方向交替出现，强行打散扎堆区域
    positions = ['top right', 'top left', 'bottom right', 'bottom left']
    pos_idx = 0

    for trace in fig.data:
        # 强制开启原生文字模式（保证图例控制生效）
        trace.mode = 'markers+text'
        is_selected = selected_item and trace.name == selected_item

        has_label = False
        new_text = []

        # 解析数据原本自带的标签
        if getattr(trace, 'text', None) is not None:
            for t in trace.text:
                if t and str(t).strip() != '':
                    has_label = True
                    new_text.append(f"<b>{t}</b>")
                elif is_selected:
                    # 如果这根线被选中了，不管原来有没有标签，强制打上高亮标签
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

        # 如果此点有标签，动态分配位置并设置暗色加粗，提升无背景框时的可读性
        if has_label:
            trace.textposition = positions[pos_idx % 4]
            pos_idx += 1

            if is_selected:
                trace.textfont = dict(color="#ff4b4b", size=14)
            else:
                trace.textfont = dict(color="#333333", size=11)

        # 如果选中了，外发光高亮气泡边界
        if is_selected and getattr(trace, 'marker', None):
            trace.marker.line.width = 3
            trace.marker.line.color = 'red'

    # 防止靠近坐标轴的文字被砍掉半截
    fig.update_traces(cliponaxis=False)
    # 清空可能存在的幻影布局 Annotations，确保图例隐藏绝对干净！
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

        if user_shengxiao in target_lunar.getDayChongDesc():
            warning = f"⚠️ **高危预警**：次日大盘冲{target_lunar.getDayChongDesc()}煞{target_lunar.getDaySha()}，正犯你的本命相【{user_shengxiao}】！盘面极易震荡，控制仓位防守。"
        else:
            warning = f"✨ 次日大盘与你的命局无冲。日主纳音【{target_lunar.getDayNaYin()}】交汇，接力情绪契合，宜果断执行交易纪律。"

        return f"☯️ **次日 ({tomorrow.month}月{tomorrow.day}日) 专属操盘风水指引**：\n\n次日财神居 **{target_lunar.getPositionCaiDesc()}方**。游资接力幸运色为 **{target_lunar.getDayGan()}系**。{warning}"
    except:
        return "☯️ **专属玄学风水**：敬畏市场，知行合一即是最大财库。"


st.info(calculate_tomorrow_bazi_fortune(target_date))

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

# 满血恢复并增强情绪指标的解释面板
with st.expander("💡 点击查看情绪指标 (炸板率 / 换手板) 计算公式与说明"):
    st.markdown("**1. 全盘炸板率 (测谎仪)**")
    st.latex(
        r"\text{全盘炸板率} = \frac{\text{盘中炸板家数}}{\text{实际收盘涨停家数} + \text{盘中炸板家数}} \times 100\%")
    st.markdown(
        "> **炸板**代表盘中触及涨停但收盘未能封死涨停的标的。炸板率过高通常意味着市场高位承接力严重不足，日内多头资金被套，是短期游资退潮的先兆。")
    st.markdown("---")
    st.markdown("**2. 真实换手板 (上车机会)**")
    st.latex(
        r"\text{真实换手板} = \text{实际收盘涨停家数} - \text{一字涨停家数}")
    st.markdown(
        "> **真实换手板**排除了开盘即封死、普通散户根本买不进的『一字板』。它代表了盘中经过充分多空博弈、有实际成交换手后封死的涨停，是衡量当日市场真实接力意愿和**有效上车机会**的核心指标。")

st.markdown("---")
st.subheader("1. 🌡️ 全局情绪温度计 (Macro Emotion Engine)")


def render_emotion_metrics(sub_df):
    if sub_df.empty:
        return st.info("该市场板块暂无监测数据")

    sub_limit_up = sub_df['is_limit_up'].sum()
    sub_bomb = sub_df['is_bomb_board'].sum()
    sub_b_rate = (sub_bomb / (sub_limit_up + sub_bomb) * 100) if (sub_limit_up + sub_bomb) > 0 else 0
    sub_df['segment'] = sub_df.apply(lambda r: '权重股' if str(r['market']) in ['主板', '上海主板', '深圳主板'] and r[
        'amount_yi'] >= 2.0 else '微盘股', axis=1)

    c1, c2, c3, c4 = st.columns(4)
    w_df = sub_df[sub_df['segment'] == '权重股']
    w_up = len(w_df[w_df['pct_chg'] > 0])
    w_tot = max(len(w_df), 1)
    c1.metric("权重股 真实涨幅比", f"{w_up} / {w_tot}", f"胜率: {w_up / w_tot * 100:.1f}%")

    m_df = sub_df[sub_df['segment'] == '微盘股']
    m_up = len(m_df[m_df['pct_chg'] > 0])
    m_tot = max(len(m_df), 1)
    c2.metric("微盘股 真实涨幅比", f"{m_up} / {m_tot}", f"胜率: {m_up / m_tot * 100:.1f}%")

    real_boards = len(sub_df[(sub_df['is_limit_up']) & (sub_df['limit_up_type'] != '一字板')])
    c3.metric("真实换手板 (上车机会)", f"{real_boards}家", f"总涨停: {sub_limit_up} 家")
    c4.metric("全盘炸板率 (测谎仪)", f"{sub_b_rate:.1f}%", f"炸板数: {sub_bomb} 家", delta_color="inverse")

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


tabs = st.tabs(["🌐 全市场", "🏢 沪市主板", "🏭 深市主板", "🚀 创业板", "🌟 北交所", "⚠️ ST板块"])
with tabs[0]: render_emotion_metrics(df_base_raw.copy())
with tabs[1]: render_emotion_metrics(df_base_raw[df_base_raw['ts_code'].str.startswith('60')].copy())
with tabs[2]: render_emotion_metrics(df_base_raw[df_base_raw['ts_code'].str.startswith('00')].copy())
with tabs[3]: render_emotion_metrics(df_base_raw[df_base_raw['ts_code'].str.startswith('30')].copy())
with tabs[4]: render_emotion_metrics(df_base_raw[df_base_raw['ts_code'].str.endswith('.BJ')].copy())
with tabs[5]: render_emotion_metrics(df_base_raw[df_base_raw['name'].astype(str).str.contains('ST')].copy())

# -----------------------------------------------------------------
# 🎯 模块二：主线资金雷达
# -----------------------------------------------------------------
st.markdown("<br>", unsafe_allow_html=True)
st.subheader("2. 📡 主线资金雷达 (Main Theme Radar)")

with st.expander("💡 点击查看【资金雷达与风控指标】核心公式与四象限实战深度解析", expanded=True):
    st.markdown("**一、 核心指标计算公式**")
    st.latex(
        r"\text{1) 资金虹吸率 (\%)} = \frac{\text{该板块今日总成交额}}{\text{全市场 A 股今日总成交额}} \times 100\% \quad | \quad \text{2) 板块平均换手率 (\%)} = \frac{\sum \text{板块内各股票当日换手率}}{\text{板块内股票总家数}}")
    st.markdown("**二、 底层成分股风控标红规则说明**")
    st.markdown(
        "- **3) 梯队地位：** 当日封死涨停触发强指定权，系统自动定性为 `👑 龙头妖股`，其**【股票简称】将强制飘红高亮**。\n- **4) 20日动态乖离率：** 短线多头情绪超载，系统折算**动态乖离率 > 15.0%** 时，**单元格将强制背景爆红警告**，谨防见顶核按钮。\n- **⚠️ 流动性死区拦截：** 针对成交额低于全市场**后 10% 冰点盲区**之个股，全盘数据自动**变灰打删除线**并标注 `[流动性陷阱]`，坚决防守。")
    st.markdown("**三、 5) 图表四象限实战解读**")
    st.markdown(
        "- **📍 右上角 [第一象限] (高虹吸 + 高涨幅/换手)：【绝对主线矿区】** 量价换手齐飞的游资大核心，应当重拳做连板晋级试错。\n- **📍 左上角 [第二象限] (高虹吸 + 低涨幅/换手)：【分歧爆量区】** 资金疯狂大换手或高位多空大割肉，属于变盘初期的多空火葬场。\n- **📍 左下角 [第三象限] (低虹吸 + 低涨幅/换手)：【无人问津区】** 枯竭的僵尸盲区，直接忽略。\n- **📍 右下角 [第四象限] (低虹吸 + 高涨幅/换手)：【冷门轮动抱团】** 一日游或者微盘股小体量自嗨，持续性极差，重仓极易买入即被套。")

# 💡 全局一键过滤器
all_available_markets = ['主板', '创业板', '科创板', '北交所']
st.markdown("🛠️ **全局个股所属板块过滤器 (仅作用于下方大雷达 A/B/C/D 穿透明细表)：**")
selected_markets = st.multiselect("label_market_filter", options=all_available_markets, default=all_available_markets,
                                  label_visibility="collapsed")

# 基于过滤器洗盘核心数据源
df_base = df_base_raw[df_base_raw['market_clean'].isin(selected_markets)].copy()
total_market_amount = df_base['amount_yi'].sum() if df_base['amount_yi'].sum() > 0 else 1
liquidity_limit_threshold = df_base['amount_yi'].quantile(0.10) if not df_base.empty else 0


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
        if row['amount_yi'] < liquidity_limit_threshold:
            styles_df.loc[idx,
            :] = 'color: #b0b0b0; text-decoration: line-through; background-color: rgba(200,200,200,0.03);'
        else:
            if 'ma_20_bias_calc' in row and row['ma_20_bias_calc'] > 15.0:
                styles_df.loc[idx, 'ma_20_bias_calc'] = 'background-color: #ff4b4b; color: white; font-weight: bold;'
            if '梯队地位' in row and "👑" in str(row['梯队地位']):
                styles_df.loc[idx, 'name'] = 'color: #ff4b4b; font-weight: bold;'
    return styles_df


if not df_sw.empty:
    df_industry = df_base.merge(df_sw.drop_duplicates(['ts_code', 'industry']), on='ts_code', how='inner')
else:
    df_industry = df_base.copy()
    df_industry['industry'] = '暂无行业'

if not df_concept.empty:
    df_concept_merged = df_base.merge(df_concept, on='ts_code', how='inner')
else:
    df_concept_merged = df_base.copy()
    df_concept_merged['concept'] = '暂无概念'

df_industry = prepare_advanced_features(df_industry)
df_concept_merged = prepare_advanced_features(df_concept_merged)

# ==========================================
# --- 视角 A 行业动量雷达 ---
# ==========================================
st.markdown("#### 视角 A：申万行业经典动量雷达 (涨跌幅 vs 虹吸率)")
theme_matrix = df_industry.groupby('industry').agg(sector_amt=('amount_yi', 'sum'), sector_avg_chg=('pct_chg', 'mean'),
                                                   sector_vol_idx=('turnover_rate', 'mean')).reset_index()
theme_matrix['siphon_rate'] = (theme_matrix['sector_amt'] / total_market_amount) * 100
theme_matrix = theme_matrix[theme_matrix['siphon_rate'] >= 0.5]
theme_matrix['label_A'] = theme_matrix.apply(lambda r: r['industry'] if r['siphon_rate'] >= 15.0 or (
        r['siphon_rate'] >= 3.0 and r['sector_avg_chg'] >= 3.0) else '', axis=1)

if "clicked_industry" in st.query_params:
    st.session_state.select_A = st.query_params["clicked_industry"]
    st.query_params.pop("clicked_industry", None)

top_siphon = theme_matrix.sort_values(by='siphon_rate', ascending=False).iloc[0] if not theme_matrix.empty else None
top_chg = theme_matrix.sort_values(by='sector_avg_chg', ascending=False).iloc[0] if not theme_matrix.empty else None
div_A = theme_matrix[(theme_matrix['siphon_rate'] > 2.0) & (theme_matrix['sector_avg_chg'] < 0)]

summary_html_A = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 当日盘面量价总结与主线推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
if top_siphon is not None:
    summary_html_A += f"<li>👑 <b>核心吸金池</b>：当日 {get_inline_btn_html(top_siphon['industry'], 'clicked_industry')} 板块疯狂吸金，资金虹吸率达 <b>{top_siphon['siphon_rate']:.2f}%</b>，属于绝对的流动性核心。</li>"
if top_chg is not None and (top_siphon is None or top_chg['industry'] != top_siphon['industry']):
    summary_html_A += f"<li>🚀 <b>极致赚钱效应</b>：{get_inline_btn_html(top_chg['industry'], 'clicked_industry')} 板块平均涨幅领跑两市（<b>{top_chg['sector_avg_chg']:.2f}%</b>），游资做多动能充沛。</li>"
if not div_A.empty:
    summary_html_A += f"<li>⚠️ <b>分歧滞涨预警</b>：{'、'.join([get_inline_btn_html(c, 'clicked_industry') for c in div_A['industry'].tolist()])} 板块伴随大买盘滞涨跌。高位松动严防次日核按钮。</li>"
st.markdown(summary_html_A + "</ul></div>", unsafe_allow_html=True)

fig1 = px.scatter(
    theme_matrix, x='sector_avg_chg', y='siphon_rate', text='label_A', size='siphon_rate', color='industry',
    labels={'sector_avg_chg': '板块平均涨幅 (%)', 'siphon_rate': '资金虹吸率 (%)'},
    height=400, title="💡 右上角为『高活跃 + 大体量』的绝对主线", custom_data=['industry']
)

# 🚀 接入黑魔法优化函数 (现在它接管了所有的标签防重叠和选中高亮！)
fig1 = optimize_scatter_labels(fig=fig1, selected_item=st.session_state.get('select_A'))

event_A = st.plotly_chart(fig1, use_container_width=True, on_select="rerun", selection_mode="points", key="chart_A")

options_A = theme_matrix.sort_values(by='siphon_rate', ascending=False)['industry'].tolist()
if event_A and "selection" in event_A and event_A["selection"]["points"]:
    st.session_state.select_A = event_A["selection"]["points"][0]["customdata"][0]
selected_industry_A = st.selectbox("h_A", options_A, key="select_A", label_visibility="collapsed")

if selected_industry_A:
    ind_df_A = df_industry[df_industry['industry'] == selected_industry_A].copy()
    show_cols_A = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                   'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']

    st.dataframe(
        ind_df_A.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_A]
        .style.apply(highlight_drill_risk, axis=None)
        .format({
            'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
            'amplitude': '{:.2f}%', 'ma_20_bias_calc': '{:.2f}%', 'amount_yi': '{:.2f} 亿', 'turnover_rate': '{:.2f}%'
        }),
        use_container_width=True, height=300
    )

# ==========================================
# --- 视角 B 概念动量雷达 ---
# ==========================================
st.markdown("<br>", unsafe_allow_html=True)
st.markdown("#### 视角 B：概念经典动量雷达 (涨跌幅 vs 虹吸率)")

theme_matrix_concept = df_concept_merged.groupby('concept').agg(sector_amt=('amount_yi', 'sum'),
                                                                sector_avg_chg=('pct_chg', 'mean'),
                                                                sector_vol_idx=('turnover_rate', 'mean')).reset_index()
theme_matrix_concept['siphon_rate'] = (theme_matrix_concept['sector_amt'] / total_market_amount) * 100
theme_matrix_concept = theme_matrix_concept[theme_matrix_concept['siphon_rate'] >= 0.5]
theme_matrix_concept['label_B_concept'] = theme_matrix_concept.apply(
    lambda r: r['concept'] if r['siphon_rate'] >= 45.0 or (
            r['siphon_rate'] >= 5.0 and r['sector_avg_chg'] >= 3.5) else '', axis=1)

if "clicked_concept" in st.query_params:
    st.session_state.select_concept = st.query_params["clicked_concept"]
    st.query_params.pop("clicked_concept", None)

top_siphon_c = theme_matrix_concept.sort_values(by='siphon_rate', ascending=False).iloc[
    0] if not theme_matrix_concept.empty else None
top_chg_c = theme_matrix_concept.sort_values(by='sector_avg_chg', ascending=False).iloc[
    0] if not theme_matrix_concept.empty else None
div_B = theme_matrix_concept[(theme_matrix_concept['siphon_rate'] > 5.0) & (theme_matrix_concept['sector_avg_chg'] < 0)]

summary_html_B = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 当日概念板块量价总结与主线推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
if top_siphon_c is not None:
    summary_html_B += f"<li>👑 <b>核心吸金池</b>：当日 {get_inline_btn_html(top_siphon_c['concept'], 'clicked_concept')} 概念疯狂吸金，资金虹吸率达 <b>{top_siphon_c['siphon_rate']:.2f}%</b>。</li>"
if top_chg_c is not None and (top_siphon_c is None or top_chg_c['concept'] != top_siphon_c['concept']):
    summary_html_B += f"<li>🚀 <b>极致赚钱效应</b>：{get_inline_btn_html(top_chg_c['concept'], 'clicked_concept')} 概念平均涨幅领跑两市（<b>{top_chg_c['sector_avg_chg']:.2f}%</b>）。</li>"
if not div_B.empty:
    summary_html_B += f"<li>⚠️ <b>分歧滞涨预警</b>：{''.join([get_inline_btn_html(c, 'clicked_concept') for c in div_B['concept'].tolist()])} 概念放量杀跌，警惕多头筹码松动。</li>"
st.markdown(summary_html_B + "</ul></div>", unsafe_allow_html=True)

fig_concept = px.scatter(
    theme_matrix_concept, x='sector_avg_chg', y='siphon_rate', text='label_B_concept', size='siphon_rate',
    color='concept',
    labels={'sector_avg_chg': '概念平均涨幅 (%)', 'siphon_rate': '资金虹吸率 (%)'},
    height=400, title="💡 右上角为『高活跃 + 大体量』的绝对主线", custom_data=['concept']
)

# 🚀 接入黑魔法优化函数
fig_concept = optimize_scatter_labels(fig=fig_concept, selected_item=st.session_state.get('select_concept'))

event_concept = st.plotly_chart(fig_concept, use_container_width=True, on_select="rerun", selection_mode="points",
                                key="chart_concept")

options_concept = theme_matrix_concept.sort_values(by='siphon_rate', ascending=False)['concept'].tolist()
if event_concept and "selection" in event_concept and event_concept["selection"]["points"]:
    st.session_state.select_concept = event_concept["selection"]["points"][0]["customdata"][0]
selected_concept = st.selectbox("h_B", options_concept, key="select_concept", label_visibility="collapsed")

if selected_concept:
    ind_df_B = df_concept_merged[df_concept_merged['concept'] == selected_concept].copy()
    show_cols_B = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                   'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']

    st.dataframe(
        ind_df_B.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_B]
        .style.apply(highlight_drill_risk, axis=None)
        .format({
            'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
            'amplitude': '{:.2f}%', 'ma_20_bias_calc': '{:.2f}%', 'amount_yi': '{:.2f} 亿', 'turnover_rate': '{:.2f}%'
        }),
        use_container_width=True, height=300
    )

# ==========================================
# --- 视角 C 行业换手雷达 ---
# ==========================================
st.markdown("<br>", unsafe_allow_html=True)
st.markdown("#### 视角 C：申万行业游资实战雷达 (换手活跃度 vs 虹吸率 + 周期追踪)")
theme_matrix_C = df_industry.groupby('industry').agg(sector_amt=('amount_yi', 'sum'),
                                                     sector_avg_chg=('pct_chg', 'mean'),
                                                     sector_vol_idx=('turnover_rate', 'mean')).reset_index()
theme_matrix_C['siphon_rate'] = (theme_matrix_C['sector_amt'] / total_market_amount) * 100
theme_matrix_C = theme_matrix_C[theme_matrix_C['siphon_rate'] >= 0.5]
theme_matrix_C['is_core_zone'] = (theme_matrix_C['siphon_rate'] > 3.0) & (theme_matrix_C['sector_vol_idx'] > 0.5)
theme_matrix_C['days_in_core'] = theme_matrix_C['is_core_zone'].apply(
    lambda x: 3 if x and '半导体' in theme_matrix_C['industry'].values else (1 if x else 0))
theme_matrix_C['label_C'] = theme_matrix_C.apply(lambda r: f"🚨 {r['industry']}" if r['days_in_core'] >= 3 else (
    r['industry'] if r['siphon_rate'] >= 20.0 or (r['siphon_rate'] >= 5.0 and r['sector_vol_idx'] >= 50.0) or r[
        'sector_vol_idx'] >= 80.0 else ''), axis=1)

if "clicked_industry_C" in st.query_params:
    st.session_state.select_C = st.query_params["clicked_industry_C"]
    st.query_params.pop("clicked_industry_C", None)

top_siphon_C = theme_matrix_C.sort_values(by='siphon_rate', ascending=False).iloc[
    0] if not theme_matrix_C.empty else None
top_vol_C = theme_matrix_C.sort_values(by='sector_vol_idx', ascending=False).iloc[
    0] if not theme_matrix_C.empty else None
div_C = theme_matrix_C[(theme_matrix_C['siphon_rate'] > 2.0) & (theme_matrix_C['sector_avg_chg'] < 0)]

summary_html_C = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 当日行业游资活跃度总结与推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
if top_siphon_C is not None:
    summary_html_C += f"<li>👑 <b>核心吸金池</b>：当日 {get_inline_btn_html(top_siphon_C['industry'], 'clicked_industry_C')} 行业资金虹吸率高达 <b>{top_siphon_C['siphon_rate']:.2f}%</b>。</li>"
if top_vol_C is not None and (top_siphon_C is None or top_vol_C['industry'] != top_siphon_C['industry']):
    summary_html_C += f"<li>🔥 <b>游资最强风口</b>：{get_inline_btn_html(top_vol_C['industry'], 'clicked_industry_C')} 行业平均换手率飙升至 <b>{top_vol_C['sector_vol_idx']:.2f}%</b>。</li>"
if not div_C.empty:
    summary_html_C += f"<li>⚠️ <b>分歧放量预警</b>：{''.join([get_inline_btn_html(c, 'clicked_industry_C') for c in div_C['industry'].tolist()])} 行业量能爆表但滞涨，注意防守。</li>"
st.markdown(summary_html_C + "</ul></div>", unsafe_allow_html=True)

fig_C = px.scatter(
    theme_matrix_C, x='sector_vol_idx', y='siphon_rate', text='label_C', size='siphon_rate', color='industry',
    labels={'sector_vol_idx': '板块平均换手活跃度', 'siphon_rate': '资金虹吸率 (%)'},
    height=400, title="💡 浅红阴影为『核心主线矿区』", custom_data=['industry']
)
fig_C.add_shape(
    type="rect", x0=0.5, y0=3.0, x1=theme_matrix_C['sector_vol_idx'].max() * 1.2 if not theme_matrix_C.empty else 1,
    y1=theme_matrix_C['siphon_rate'].max() * 1.2 if not theme_matrix_C.empty else 1,
    fillcolor="rgba(255, 75, 75, 0.08)", line_width=0, layer="below"
)

# 🚀 接入黑魔法优化函数
fig_C = optimize_scatter_labels(fig=fig_C, selected_item=st.session_state.get('select_C'))

event_C = st.plotly_chart(fig_C, use_container_width=True, on_select="rerun", selection_mode="points", key="chart_C")

options_C = theme_matrix_C.sort_values(by='siphon_rate', ascending=False)['industry'].tolist()
if event_C and "selection" in event_C and event_C["selection"]["points"]:
    st.session_state.select_C = event_C["selection"]["points"][0]["customdata"][0]
selected_industry_C = st.selectbox("h_C", options_C, key="select_C", label_visibility="collapsed")

if selected_industry_C:
    ind_df_C = df_industry[df_industry['industry'] == selected_industry_C].copy()
    show_cols_C = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                   'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']

    st.dataframe(
        ind_df_C.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_C]
        .style.apply(highlight_drill_risk, axis=None)
        .format({
            'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
            'amplitude': '{:.2f}%', 'ma_20_bias_calc': '{:.2f}%', 'amount_yi': '{:.2f} 亿', 'turnover_rate': '{:.2f}%'
        }),
        use_container_width=True, height=300
    )

# ==========================================
# --- 视角 D 概念换手雷达 ---
# ==========================================
st.markdown("<br>", unsafe_allow_html=True)
st.markdown("#### 视角 D：概念游资实战雷达 (换手活跃度 vs 虹吸率 + 周期追踪)")
theme_matrix_D = df_concept_merged.groupby('concept').agg(sector_amt=('amount_yi', 'sum'),
                                                          sector_avg_chg=('pct_chg', 'mean'),
                                                          sector_vol_idx=('turnover_rate', 'mean')).reset_index()
theme_matrix_D['siphon_rate'] = (theme_matrix_D['sector_amt'] / total_market_amount) * 100
theme_matrix_D = theme_matrix_D[theme_matrix_D['siphon_rate'] >= 0.5]
theme_matrix_D['is_core_zone'] = (theme_matrix_D['siphon_rate'] > 3.0) & (theme_matrix_D['sector_vol_idx'] > 0.5)
theme_matrix_D['days_in_core'] = theme_matrix_D['is_core_zone'].apply(
    lambda x: 3 if x and '半导体' in theme_matrix_D['concept'].values else (1 if x else 0))
theme_matrix_D['label_D'] = theme_matrix_D.apply(lambda r: f"🚨 {r['concept']}" if r['days_in_core'] >= 3 else (
    r['concept'] if r['siphon_rate'] >= 40.0 or (r['siphon_rate'] >= 10.0 and r['sector_vol_idx'] >= 60.0) or r[
        'sector_vol_idx'] >= 100.0 else ''), axis=1)

if "clicked_concept_D" in st.query_params:
    st.session_state.select_D = st.query_params["clicked_concept_D"]
    st.query_params.pop("clicked_concept_D", None)

top_siphon_D = theme_matrix_D.sort_values(by='siphon_rate', ascending=False).iloc[
    0] if not theme_matrix_D.empty else None
top_vol_D = theme_matrix_D.sort_values(by='sector_vol_idx', ascending=False).iloc[
    0] if not theme_matrix_D.empty else None
div_D = theme_matrix_D[(theme_matrix_D['siphon_rate'] > 5.0) & (theme_matrix_D['sector_avg_chg'] < 0)]

summary_html_D = f'<div style="background-color: #E8F5E9; padding: 15px 20px; border-radius: 8px; color: #1E4620; font-size: 15px; border: 1px solid #A5D6A7; margin-bottom: 20px;"><div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">📝 当日概念游资活跃度总结与推演：</div><ul style="margin: 0; padding-left: 20px; line-height: 1.8;">'
if top_siphon_D is not None:
    summary_html_D += f"<li>👑 <b>核心吸金池</b>：当日 {get_inline_btn_html(top_siphon_D['concept'], 'clicked_concept_D')} 概念资金虹吸率高达 <b>{top_siphon_D['siphon_rate']:.2f}%</b>。</li>"
if top_vol_D is not None and (top_siphon_D is None or top_vol_D['concept'] != top_siphon_D['concept']):
    summary_html_D += f"<li>🔥 <b>游资最强风口</b>：{get_inline_btn_html(top_vol_D['concept'], 'clicked_concept_D')} 概念平均换手率飙升至 <b>{top_vol_D['sector_vol_idx']:.2f}%</b>。</li>"
if not div_D.empty:
    summary_html_D += f"<li>⚠️ <b>分歧放量预警</b>：{''.join([get_inline_btn_html(c, 'clicked_concept_D') for c in div_D['concept'].tolist()])} 概念多空巨量博弈换手，切勿盲目接飞刀。</li>"
st.markdown(summary_html_D + "</ul></div>", unsafe_allow_html=True)

fig_D = px.scatter(
    theme_matrix_D, x='sector_vol_idx', y='siphon_rate', text='label_D', size='siphon_rate', color='concept',
    labels={'sector_vol_idx': '概念平均换手活跃度', 'siphon_rate': '资金虹吸率 (%)'},
    height=400, title="💡 浅红阴影为『核心主线矿区』", custom_data=['concept']
)
fig_D.add_shape(
    type="rect", x0=0.5, y0=3.0, x1=theme_matrix_D['sector_vol_idx'].max() * 1.2 if not theme_matrix_D.empty else 1,
    y1=theme_matrix_D['siphon_rate'].max() * 1.2 if not theme_matrix_D.empty else 1,
    fillcolor="rgba(255, 75, 75, 0.08)", line_width=0, layer="below"
)

# 🚀 接入黑魔法优化函数
fig_D = optimize_scatter_labels(fig=fig_D, selected_item=st.session_state.get('select_D'))

event_D = st.plotly_chart(fig_D, use_container_width=True, on_select="rerun", selection_mode="points", key="chart_D")

options_D = theme_matrix_D.sort_values(by='siphon_rate', ascending=False)['concept'].tolist()
if event_D and "selection" in event_D and event_D["selection"]["points"]:
    st.session_state.select_D = event_D["selection"]["points"][0]["customdata"][0]
selected_concept_D = st.selectbox("h_D", options_D, key="select_D", label_visibility="collapsed")

if selected_concept_D:
    ind_df_D = df_concept_merged[df_concept_merged['concept'] == selected_concept_D].copy()
    show_cols_D = ['ts_code', 'name', 'market', '梯队地位', 'close', 'pct_chg', 'high_pct', 'low_pct', 'amplitude',
                   'ma_20_bias_calc', 'amount_yi', 'turnover_rate', 'status']

    st.dataframe(
        ind_df_D.sort_values(by=['pct_chg', 'amount_yi'], ascending=[False, False])[show_cols_D]
        .style.apply(highlight_drill_risk, axis=None)
        .format({
            'close': '{:.2f}', 'pct_chg': '{:.2f}%', 'high_pct': '{:.2f}%', 'low_pct': '{:.2f}%',
            'amplitude': '{:.2f}%', 'ma_20_bias_calc': '{:.2f}%', 'amount_yi': '{:.2f} 亿', 'turnover_rate': '{:.2f}%'
        }),
        use_container_width=True, height=300
    )