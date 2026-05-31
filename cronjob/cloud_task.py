import os
import sys
import subprocess
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
    if now.hour < 17:
        target = now - timedelta(days=1)
    else:
        target = now

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

        cursor.execute("SELECT count(*) FROM stock_basic;")
        total_stocks = cursor.fetchone()[0]

        cursor.execute("SELECT count(*) FROM daily_data WHERE trade_date = %s;", (target_date,))
        existing_count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        print(f"📊 基础大名单总数: {total_stocks} 只")
        print(f"📊 {target_date} 当日已存条数: {existing_count} 条")

        if total_stocks == 0:
            return False, 0, 0

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

    target_date = get_target_trade_date(start_time)
    print(f"🎯 根据分水岭算法，当前目标交易日应为: {target_date}")

    # 将 YYYYMMDD 格式化为 YYYY-MM-DD，用于生成终极暗号
    biz_date_str = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    print("⏳ 正在自动更新云端 stock_basic 名单...")

    is_complete, existing_count, total_stocks = check_if_data_exists(target_date)

    if is_complete:
        end_time = datetime.now()
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

        # 🟩 【修改点 1】提前下班也要发送带有日期的终极暗号！
        title = f"[DATAFEED_SUCCESS_{biz_date_str}] 今日数据云端已存在，无需重复同步"
        content = (
            f"⏱️ 检查时间: {end_str}\n"
            f"📅 目标交易日: {target_date}\n"
            f"📊 数据库状态: 已有 {existing_count} 条 / 股票总数 {total_stocks} 只\n"
            f"💡 结论: 该日数据已基本完整，系统自动拦截，未消耗 Tushare 额度。\n\n"
            f"✅ 云端状态安全，Mac 本地哨兵即将自动回吸验证。"
        )
        print("\n" + content)
        send_pushplus_msg(title, content)
        sys.exit(0)

    # 🟨 【未通过拦截】说明需要干活
    old_max_date = get_db_max_date()
    print(f"🚀 数据未就绪，启动核心爬虫脚本 data_collector.py ...")

    # ================= 核心修改区：跨文件夹绝对路径执行 =================
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    collector_path = os.path.join(project_root, "stock_basic_info", "data_collector.py")
    print(f"📁 锁定真实爬虫路径: {collector_path}")

    try:
        subprocess.run([sys.executable, collector_path], check=True)
        exit_code = 0
    except subprocess.CalledProcessError as e:
        exit_code = e.returncode
    # ==============================================================

    if exit_code != 0:
        send_pushplus_msg("告警：云端爬虫运行崩溃",
                          f"任务开始时间: {start_str}\n爬虫脚本执行失败，请前往 GitHub 检查日志！")
        sys.exit(1)

    # 爬虫执行成功，统计战果
    end_time = datetime.now()
    end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
    duration_minutes = round((end_time - start_time).total_seconds() / 60, 2)

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

    # 🟩 【修改点 2】正常干活完毕，发送带有日期的终极暗号！
    title = f"[DATAFEED_SUCCESS_{biz_date_str}] 今日云端日线数据进货完毕"
    content = (
        f"⏱️ 开始时间: {start_str}\n"
        f"🏁 结束时间: {end_str}\n"
        f"⏳ 任务耗时: {duration_minutes} 分钟\n"
        f"📈 本次新增: {new_inserted} 条\n"
        f"📅 最新交易日: {new_max_date if new_max_date else '暂无数据'}\n\n"
        f"✅ 进货成功！Mac 本地哨兵即将自动回吸。"
    )

    print("\n" + content)
    send_pushplus_msg(title, content)


if __name__ == "__main__":
    main()