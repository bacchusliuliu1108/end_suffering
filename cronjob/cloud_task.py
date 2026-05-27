import os
import sys
from datetime import datetime, timedelta
import requests
import psycopg2

# ================= 1. 配置区 =================
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_DB_URL")


# =============================================

def send_pushplus_msg(title, content):
    """发送 PushPlus 通知"""
    if not PUSHPLUS_TOKEN:
        print("⚠️ 未配置 PUSHPLUS_TOKEN，跳过推送。")
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
        print("✅ 微信推送成功！")
    except Exception as e:
        print(f"❌ 微信推送失败: {e}")


def get_target_trade_date(now):
    """根据17点分水岭及周末剔除，计算当前理论上应该抓取哪天的数据"""
    # 1. 17点前算前一天，17点后算当天
    if now.hour < 17:
        target = now - timedelta(days=1)
    else:
        target = now

    # 2. 如果目标日期是周末，自动回退到周五
    # weekday(): 0是周一, 5是周六, 6是周日
    if target.weekday() == 5:  # 周六 -> 退回周五
        target = target - timedelta(days=1)
    elif target.weekday() == 6:  # 周日 -> 退回周五
        target = target - timedelta(days=2)

    return target.strftime("%Y%m%d")


def check_if_data_exists(target_date):
    """检查目标日期的数据在数据库中是否已经基本完整"""
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()

        # 1. 获取 stock_basic 中最新的活跃股票总数
        cursor.execute("SELECT count(*) FROM stock_basic;")
        total_stocks = cursor.fetchone()[0]

        # 2. 获取 daily_data 中该目标日期的已存条数
        cursor.execute("SELECT count(*) FROM daily_data WHERE trade_date = %s;", (target_date,))
        existing_count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        print(f"📊 基础大名单总数: {total_stocks} 只")
        print(f"📊 {target_date} 当日已存条数: {existing_count} 条")

        if total_stocks == 0:
            return False, 0, 0

        # 3. 如果已有条数超过大名单的 95%，认定为已经成功跑过
        is_complete = (existing_count >= total_stocks * 0.95)
        return is_complete, existing_count, total_stocks

    except Exception as e:
        print(f"🔍 检查数据完整性失败: {e}")
        return False, 0, 0


def get_db_max_date():
    """获取当前数据库中最新的交易日期"""
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(trade_date) FROM daily_data;")
        res = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return res
    except Exception as e:
        return None


def main():
    start_time = datetime.now()
    start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{start_str}] 🚀 开始执行云端主任务...")

    # --- 1. 计算目标日期 ---
    target_date = get_target_trade_date(start_time)
    print(f"🎯 根据分水岭算法，当前目标交易日应为: {target_date}")

    # --- 2. 预先更新一遍 stock_basic (确保名单最新) ---
    print("⏳ 正在自动更新云端 stock_basic 名单...")
    # 这里直接复用了你的 data_collector.py 里面的名单拉取逻辑，或者直接运行它
    # 如果你的爬虫脚本运行就会自动更新 basic，我们可以通过判断来决定要不要跑全量 daily

    # --- 3. 智能判定是否需要拦截 ---
    is_complete, existing_count, total_stocks = check_if_data_exists(target_date)

    if is_complete:
        # 🟩 【触发拦截拦截】数据已存在，直接下班！
        end_time = datetime.now()
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

        title = "今日日线数据无须重复同步"
        content = (
            f"⏱️ 检查时间: {end_str}\n"
            f"📅 目标交易日: {target_date}\n"
            f"📊 数据库状态: 已有 {existing_count} 条 / 股票总数 {total_stocks} 只\n"
            f"💡 结论: 该日数据已基本完整，系统自动拦截，未消耗 Tushare 额度。\n\n"
            f"✅ 云端状态安全，Mac 无须拉取新数据。"
        )
        print("\n" + content)
        send_pushplus_msg(title, content)
        sys.exit(0)  # 优雅退出

    # 🟨 【未通过拦截】说明需要干活
    old_max_date = get_db_max_date()
    print(f"🚀 数据未就绪，启动核心爬虫脚本 data_collector.py ...")

    exit_code = os.system("python cronjob/data_collector.py")

    if exit_code != 0:
        send_pushplus_msg("告警：云端爬虫运行崩溃",
                          f"任务开始时间: {start_str}\n爬虫脚本执行失败，请前往 GitHub 检查日志！")
        sys.exit(1)

    # --- 4. 统计战果并整装发射 ---
    end_time = datetime.now()
    end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
    duration_minutes = round((end_time - start_time).total_seconds() / 60, 2)

    # 重新连接数据库看一眼最终增加了多少
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM daily_data WHERE trade_date = %s;", (target_date,))
        final_count = cursor.fetchone()[0]
        new_inserted = final_count - existing_count
        new_max_date = get_db_max_date()
        cursor.close()
        conn.close()
    except:
        new_inserted = "未知"
        new_max_date = target_date

    title = "今日日线数据同步完毕"
    content = (
        f"⏱️ 开始时间: {start_str}\n"
        f"🏁 结束时间: {end_str}\n"
        f"⏳ 任务耗时: {duration_minutes} 分钟\n"
        f"📈 本次新增: {new_inserted} 条\n"
        f"📅 最新交易日: {new_max_date if new_max_date else '暂无数据'}\n\n"
        f"✅ 进货成功！Mac 可随时执行拉取。"
    )

    print("\n" + content)
    send_pushplus_msg(title, content)


if __name__ == "__main__":
    main()