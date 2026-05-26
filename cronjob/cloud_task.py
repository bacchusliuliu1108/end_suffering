import os
import psycopg2
import datetime
import subprocess
import requests

# ================= 1. 获取系统环境变量 =================
DB_URL = os.getenv("SUPABASE_DB_URL")
WXPUSHER_TOKEN = os.getenv("WXPUSHER_TOKEN")
WXPUSHER_UID = os.getenv("WXPUSHER_UID")


# ================= 2. 微信推送通知函数 =================
def send_wechat_msg(title, content):
    """用于发送运行结果到微信"""
    if not WXPUSHER_TOKEN or not WXPUSHER_UID:
        print("未配置微信 Token 或 UID，跳过推送。")
        return
    url = "https://wxpusher.zjiecode.com/api/send/message"
    data = {
        "appToken": WXPUSHER_TOKEN,
        "content": content,
        "summary": title,
        "contentType": 1,
        "uids": [WXPUSHER_UID]
    }
    try:
        requests.post(url, json=data)
    except Exception as e:
        print(f"微信推送失败: {e}")


# ================= 3. 主程序逻辑 =================
def main():
    start_time = datetime.datetime.now()
    print(f"[{start_time}] 开始执行云端主任务...")

    # ---------------- 步骤 A: 运行你的爬虫脚本 ----------------
    print("正在调用爬虫脚本 data_collector.py ...")

    # 注意：这里的路径是以项目最外层为起点的相对路径
    result = subprocess.run(
        ["python", "stock_basic_info/data_collector.py"],
        capture_output=True,
        text=True
    )

    # 检查爬虫是否报错退出
    if result.returncode != 0:
        error_msg = f"❌ 爬虫脚本执行崩溃！\n报错日志:\n{result.stderr}"
        print(error_msg)
        send_wechat_msg("告警：云端爬虫失败", error_msg)
        raise Exception("爬虫脚本异常退出")
    else:
        print(f"✅ 爬虫执行成功！部分输出日志:\n{result.stdout[:500]}...")  # 只打印前500字防止日志太长

    # ---------------- 步骤 B: 瘦身清理云端旧数据 ----------------
    print("正在连接云数据库进行过期缓存清理...")
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cursor:
            # 👇👇👇👇👇 【你唯一需要修改的地方】 👇👇👇👇👇
            # 请把 【你的表名】 换成真实的表，把 【你的时间字段】 换成真实的时间列（比如 created_at 或 trade_date）
            clean_sql = """
                            DELETE FROM daily_data 
                            WHERE created_at < CURRENT_DATE - INTERVAL '3 days';
                        """
            # 👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆👆

            cursor.execute(clean_sql)
            deleted_rows = cursor.rowcount
            print(f"清理完成，共删除了 {deleted_rows} 条 3 天前的过期数据。")

        conn.commit()

        # ---------------- 步骤 C: 全链路成功，发送微信捷报 ----------------
        success_msg = f"✅ 今日云端任务全部完成！\n1. 爬虫脚本已成功拉取新数据。\n2. 云端数据库已自动清理 {deleted_rows} 条过期缓存。\n\n目前云端缓冲池状态健康，等待本地同步拉取。"
        send_wechat_msg("✅ 云端爬虫运行成功", success_msg)

    except Exception as e:
        conn.rollback()
        error_msg = f"清理旧数据时连接数据库失败:\n{str(e)}"
        print(error_msg)
        send_wechat_msg("告警：云端数据库操作失败", error_msg)
        raise e
    finally:
        conn.close()


if __name__ == "__main__":
    main()