import os
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
import warnings

# 忽略 pandas 的小警告，保持终端干净
warnings.filterwarnings('ignore', category=UserWarning)

# ================= 线上云端配置区 =================
# 👉 核心修改：切换为 Supabase 线上数据库直连
CLOUD_DB_URL = os.environ.get("SUPABASE_DB_URL", "postgresql://postgres.aaxiztvfxwkcnvrgdizt:kFM*2aZ4K-G6?K6@aws-1-us-east-1.pooler.supabase.com:6543/postgres")


def get_cloud_connection():
    return psycopg2.connect(CLOUD_DB_URL)


def init_cloud_tables():
    """确保线上云端因子表结构正确"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS report_daily_factors (
        trade_date VARCHAR(10),
        ts_code VARCHAR(15),
        close_price NUMERIC,
        amount NUMERIC,
        turnover_rate NUMERIC,
        ma_20_bias NUMERIC,
        is_limit_up BOOLEAN,
        limit_up_type VARCHAR(10),
        is_limit_down BOOLEAN,
        is_bomb_board BOOLEAN,
        limit_step INT DEFAULT 0, -- 连板天梯高度计数
        PRIMARY KEY (trade_date, ts_code)
    );
    """
    alter_table_sql = "ALTER TABLE report_daily_factors ADD COLUMN IF NOT EXISTS limit_step INT DEFAULT 0;"

    with get_cloud_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(create_table_sql)
            cur.execute(alter_table_sql)
        conn.commit()


def get_missing_dates_cloud():
    """线上对账：看看云端原始行情有哪几天，因子表还缺哪几天"""
    with get_cloud_connection() as conn:
        with conn.cursor() as cur:
            # 获取云端已经算过因子的日子
            cur.execute("SELECT DISTINCT trade_date FROM report_daily_factors;")
            fact_dates = [str(r[0]) for r in cur.fetchall()]

            # 获取云端最近30天的行情日子
            cur.execute("SELECT DISTINCT trade_date FROM daily_data ORDER BY trade_date DESC LIMIT 30;")
            daily_dates = [str(r[0]) for r in cur.fetchall()]

    # 差集对比（sorted 默认从小到大正序排列，确保时序计算的递进性）
    missing = sorted([d for d in daily_dates if d not in fact_dates])
    return missing


def calculate_and_save(target_date):
    """提取云端行情，计算因子，直接存入云端因子表"""
    print(f"⏳ 正在计算云端特征: {target_date}...")

    with get_cloud_connection() as conn:
        query = f"SELECT trade_date, ts_code, open, high, low, close, vol, amount, pct_chg FROM daily_data WHERE trade_date = '{target_date}';"
        df = pd.read_sql(query, conn)

    if df.empty:
        return 0

    # 开始算基础涨停因子
    df['pre_close'] = df['close'] / (1 + df['pct_chg'] / 100)
    df['limit_ratio'] = df['ts_code'].apply(lambda x: 0.20 if x.startswith('30') or x.startswith('68') else 0.10)
    df['expected_limit_price'] = (df['pre_close'] * (1 + df['limit_ratio'])).round(2)
    df['is_limit_up'] = df['pct_chg'] >= (df['limit_ratio'] * 100 - 0.5)
    df['is_limit_down'] = df['pct_chg'] <= -(df['limit_ratio'] * 100 - 0.5)
    df['is_bomb_board'] = (df['high'] >= df['expected_limit_price'] * 0.995) & (~df['is_limit_up'])
    df['close_price'] = df['close']
    df['limit_up_type'] = df.apply(lambda r: '一字板' if r['is_limit_up'] and r['low'] == r['high'] else (
        '实体板' if r['is_limit_up'] else '未涨停'), axis=1)
    df['turnover_rate'] = (df['vol'] / 10000).round(2)
    df['ma_20_bias'] = 0.0

    # 时序动态回溯，抓取上一个实际交易日因子的连板记录
    prev_streaks = {}
    with get_cloud_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT max(trade_date) FROM daily_data WHERE trade_date < '{target_date}';")
            res = cur.fetchone()
            prev_date = res[0] if res else None

    if prev_date:
        with get_cloud_connection() as conn:
            query_prev = f"SELECT ts_code, limit_step FROM report_daily_factors WHERE trade_date = '{prev_date}';"
            try:
                df_prev = pd.read_sql(query_prev, conn)
                if not df_prev.empty:
                    # 映射为字典 {'000001.SZ': 3, '000002.SZ': 0}
                    prev_streaks = dict(zip(df_prev['ts_code'], df_prev['limit_step']))
            except Exception:
                pass

    # 应用连板递增算法：今日涨停则连板数 = 昨日连板 + 1，否则归零
    df['limit_step'] = df.apply(
        lambda r: int(prev_streaks.get(r['ts_code'], 0) + 1) if r['is_limit_up'] else 0,
        axis=1
    )

    # 将 limit_step 追加进保存矩阵
    factors_df = df[['trade_date', 'ts_code', 'close_price', 'amount', 'turnover_rate',
                     'ma_20_bias', 'is_limit_up', 'limit_up_type', 'is_limit_down', 'is_bomb_board', 'limit_step']]

    records = factors_df.values.tolist()

    insert_sql = """
        INSERT INTO report_daily_factors
        (trade_date, ts_code, close_price, amount, turnover_rate, ma_20_bias, is_limit_up, limit_up_type, is_limit_down, is_bomb_board, limit_step)
        VALUES %s
        ON CONFLICT (trade_date, ts_code) DO UPDATE SET
            close_price = EXCLUDED.close_price, amount = EXCLUDED.amount, turnover_rate = EXCLUDED.turnover_rate,
            is_limit_up = EXCLUDED.is_limit_up, limit_up_type = EXCLUDED.limit_up_type,
            is_limit_down = EXCLUDED.is_limit_down, is_bomb_board = EXCLUDED.is_bomb_board,
            limit_step = EXCLUDED.limit_step;
    """

    with get_cloud_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, insert_sql, records, page_size=5000)
        conn.commit()

    return len(records)

# 👉 新增精简战术：永远只保留最近的 30 个交易日因子数据，避免云端库撑爆
def clean_expired_data():
    delete_sql = """
    DELETE FROM report_daily_factors 
    WHERE trade_date NOT IN (
        SELECT trade_date FROM (
            SELECT DISTINCT trade_date 
            FROM report_daily_factors 
            ORDER BY trade_date DESC 
            LIMIT 30
        ) AS recent_dates
    );
    """
    try:
        with get_cloud_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(delete_sql)
                deleted_rows = cur.rowcount
            conn.commit()
        if deleted_rows > 0:
            print(f"🧹 云端碎片清理完毕：已自动清除了 {deleted_rows} 条过期(30个交易日之前)的因子记录。")
    except Exception as e:
        print(f"⚠️ 云端碎片清理失败: {e}")


def main():
    print("🚀 [线上云端计算引擎] 启动...")
    init_cloud_tables()

    missing = get_missing_dates_cloud()
    if not missing:
        print("✅ 云端特征表已是最新，无缺失数据。")
        # 即使没有新数据，也顺手清理一下过期数据
        clean_expired_data()
        return

    print(f"🔍 发现云端因子表缺少 {len(missing)} 天的数据，开始补齐...")

    for d in missing:
        count = calculate_and_save(d)
        print(f"✅ [{d}] 云端入库成功，共 {count} 条因子数据。")

    # 👉 核心修改：每日数据补齐后，执行云端 30 天精简清理
    clean_expired_data()
    print("🎉 云端全量数据清洗与精简完毕，现在网页前端可以读到最新数据了！")


if __name__ == '__main__':
    main()