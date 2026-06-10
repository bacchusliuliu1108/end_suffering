import tushare as ts
import psycopg2
from psycopg2.extras import execute_values
import time

# ================= 1. 配置区 =================
TUSHARE_TOKEN = 'c0a463a9b587204cfb009f1d79370fbc899c3e68cb50d87567378e40'
DB_URL = "postgresql://postgres:endsuffering@localhost:5432/stock_db"
# ============================================

pro = ts.pro_api(TUSHARE_TOKEN)


def create_tables(cursor):
    """在本地数据库创建行业和概念两张表"""
    print("🛠️ 初始化标签数据表...")

    # 1. 申万行业成分表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_sw_industry (
            ts_code VARCHAR(20),
            industry_code VARCHAR(20),
            industry_name VARCHAR(100),
            level VARCHAR(10),
            PRIMARY KEY (ts_code, industry_code)
        );
    """)

    # 2. 概念板块成分表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_concept (
            ts_code VARCHAR(20),
            concept_code VARCHAR(20),
            concept_name VARCHAR(100),
            PRIMARY KEY (ts_code, concept_code)
        );
    """)
    print("✅ 数据表创建完成！")


def sync_sw_industry(cursor, conn):
    """获取申万2021版细分行业（一级到三级）"""
    print("🔍 正在拉取申万行业目录 (SW2021)...")

    # 清空旧数据，保持最新
    cursor.execute("TRUNCATE TABLE stock_sw_industry;")

    df_classify = pro.index_classify(level='', src='SW2021')
    total = len(df_classify)
    print(f"📊 共发现 {total} 个申万细分行业，开始拉取成分股...")

    insert_query = """
        INSERT INTO stock_sw_industry (ts_code, industry_code, industry_name, level)
        VALUES %s ON CONFLICT DO NOTHING
    """

    count = 0
    for _, row in df_classify.iterrows():
        idx_code = row['index_code']
        idx_name = row['industry_name']
        level = row['level']

        try:
            # 拉取该行业下的所有股票
            df_member = pro.index_member(index_code=idx_code)
            if not df_member.empty:
                data_tuples = [
                    (r['con_code'], idx_code, idx_name, level)
                    for _, r in df_member.iterrows()
                ]
                execute_values(cursor, insert_query, data_tuples)
                conn.commit()

            count += 1
            print(f"[{count}/{total}] 🟢 {idx_name} ({level}) 成分股入库成功")

            # Tushare 接口有限频，必须温柔一点，防止被封 IP
            time.sleep(0.3)

        except Exception as e:
            print(f"❌ 拉取 {idx_name} 失败: {e}")
            time.sleep(1)


def sync_concepts(cursor, conn):
    """获取所有概念板块及其成分股"""
    print("\n🔍 正在拉取热门概念板块目录...")

    cursor.execute("TRUNCATE TABLE stock_concept;")

    df_concept = pro.concept()
    total = len(df_concept)
    print(f"📊 共发现 {total} 个概念板块，开始拉取成分股...")

    insert_query = """
        INSERT INTO stock_concept (ts_code, concept_code, concept_name)
        VALUES %s ON CONFLICT DO NOTHING
    """

    count = 0
    for _, row in df_concept.iterrows():
        c_code = row['code']
        c_name = row['name']

        try:
            df_detail = pro.concept_detail(id=c_code)
            if not df_detail.empty:
                data_tuples = [
                    (r['ts_code'], c_code, c_name)
                    for _, r in df_detail.iterrows()
                ]
                execute_values(cursor, insert_query, data_tuples)
                conn.commit()

            count += 1
            if count % 10 == 0:
                print(f"[{count}/{total}] 🔥 已同步至概念: {c_name}")

            time.sleep(0.3)

        except Exception as e:
            print(f"❌ 拉取概念 {c_name} 失败: {e}")
            time.sleep(1)


def main():
    try:
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()

        create_tables(cursor)

        # 1. 洗入申万行业
        sync_sw_industry(cursor, conn)

        # 2. 洗入热门概念
        sync_concepts(cursor, conn)

        print("\n🎉 终极大丰收！所有行业与概念数据已全部洗入本地 PostgreSQL！")

    except Exception as e:
        print(f"❌ 数据库连接或执行引发致命错误: {e}")
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()


if __name__ == "__main__":
    main()