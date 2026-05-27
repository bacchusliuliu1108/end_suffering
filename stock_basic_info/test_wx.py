import requests


def test_pushplus():
    # 👇 替换成你在 PushPlus 官网拿到的 Token
    token = "f32af33fe94545dca29b94036ca1b900"

    url = "http://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": "今日量化数据同步完毕",  # 👉 这里填什么，微信里的“工单名称”就显示什么
        "content": "共更新了5500只股票...",  # 👉 这里填什么，就会显示在下面
        "template": "txt"
    }

    print("🚀 正在向 PushPlus 平台发射测试信号...")
    try:
        res = requests.post(url, json=data)
        print(f"📡 平台返回状态码: {res.status_code}")
        print(f"💬 平台返回详细内容: {res.text}")
    except Exception as e:
        print(f"❌ 发送失败: {e}")


if __name__ == "__main__":
    test_pushplus()