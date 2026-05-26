import os
import pandas as pd
import psycopg2
import time
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Set, Dict, Tuple
import sys
from pathlib import Path

# ====================================================
# 👉 路径动态自适应配置
# ====================================================
BASE_DATA_DIR = Path(__file__).resolve().parent / "stock_data"

# ====================================================
# 👉 兼容性引入本地配置
# ====================================================
try:
    from config import pro, DB_CONFIG
except ImportError:
    pro = None
    DB_CONFIG = {}

if pro is None:
    import tushare as ts

    tushare_token = os.getenv("TUSHARE_TOKEN")
    if tushare_token:
        ts.set_token(tushare_token)
        pro = ts.pro_api()


class TokenBucket:
    """令牌桶算法实现，用于精确控制请求频率"""

    def __init__(self, rate_per_minute, capacity=None):
        self._rate = rate_per_minute / 60.0
        self._capacity = capacity if capacity is not None else rate_per_minute
        self._tokens = self._capacity
        self._last_time = time.time()

    def consume(self, tokens=1):
        now = time.time()
        elapsed = now - self._last_time
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_time = now

        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def acquire(self, tokens=1, max_wait=60):
        start_time = time.time()
        while time.time() - start_time < max_wait:
            if self.consume(tokens):
                return True
            time.sleep(0.01)
        return False


class StockDataCollector:
    def __init__(self):
        # 数据库连接智能自适应
        db_url = os.getenv("SUPABASE_DB_URL")
        if db_url:
            self.conn = psycopg2.connect(db_url)
        else:
            self.conn = psycopg2.connect(**DB_CONFIG)

        self.conn.autocommit = False
        self.cursor = self.conn.cursor()
        self.setup_logging()
        self.existing_dates_cache: Dict[str, Set[str]] = {}

        self.parquet_available, self.parquet_engine = self.detect_parquet_engine()
        if not self.parquet_available:
            self.logger.warning("Parquet引擎不可用，Parquet存储功能将被禁用。")

        self.rate_limiter = TokenBucket(rate_per_minute=45, capacity=45)

    def detect_parquet_engine(self) -> Tuple[bool, Optional[str]]:
        try:
            import pyarrow
            import pyarrow.parquet
            return True, 'pyarrow'
        except ImportError:
            try:
                import fastparquet
                return True, 'fastparquet'
            except ImportError:
                return False, None

    def setup_logging(self):
        log_dir = BASE_DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / f'collector_{datetime.now().strftime("%Y%m%d")}.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)

    def get_all_stocks(self) -> List[str]:
        self.cursor.execute("SELECT ts_code FROM stock_basic ORDER BY ts_code")
        return [row[0] for row in self.cursor.fetchall()]

    def get_existing_dates_for_stock(self, ts_code: str) -> Set[str]:
        if ts_code in self.existing_dates_cache:
            return self.existing_dates_cache[ts_code]

        self.cursor.execute(
            "SELECT trade_date FROM daily_data WHERE ts_code = %s",
            (ts_code,)
        )
        dates = {row[0].strftime('%Y%m%d') for row in self.cursor.fetchall()}
        self.existing_dates_cache[ts_code] = dates
        return dates

    def get_existing_dates_batch(self, ts_codes: List[str]) -> Dict[str, Set[str]]:
        if not ts_codes:
            return {}

        placeholders = ','.join(['%s'] * len(ts_codes))
        query = f"SELECT ts_code, trade_date FROM daily_data WHERE ts_code IN ({placeholders})"
        self.cursor.execute(query, ts_codes)

        result = {}
        for ts_code, trade_date in self.cursor.fetchall():
            if ts_code not in result:
                result[ts_code] = set()
            result[ts_code].add(trade_date.strftime('%Y%m%d'))

        for ts_code in ts_codes:
            if ts_code not in result:
                result[ts_code] = set()
            self.existing_dates_cache[ts_code] = result[ts_code]

        return result

    def get_last_trade_date(self, ts_code: str) -> Optional[str]:
        existing_dates = self.get_existing_dates_for_stock(ts_code)
        if existing_dates:
            return max(existing_dates)
        return None

    def fetch_daily_data(self, ts_code: str, start_date: str = None, end_date: str = None) -> Optional[pd.DataFrame]:
        max_retries = 3
        retry_delay = 5

        if pro is None:
            self.logger.error("Tushare API 未初始化，请检查 config.py 或环境变量 TUSHARE_TOKEN")
            return None

        for attempt in range(max_retries):
            try:
                if not self.rate_limiter.acquire(tokens=1):
                    self.logger.warning(f"频率控制器超时，等待令牌中...")
                    time.sleep(2)
                    continue

                params = {"ts_code": ts_code}
                if start_date:
                    params["start_date"] = start_date
                if end_date:
                    params["end_date"] = end_date

                df = pro.daily(**params)

                if df is not None and not df.empty:
                    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
                    return df
                return None

            except Exception as e:
                error_msg = str(e)
                self.logger.error(f"获取 {ts_code} 数据失败 (尝试 {attempt + 1}/{max_retries}): {error_msg}")

                if "每分钟最多访问该接口" in error_msg or "request limit reached" in error_msg:
                    self.logger.warning(f"触发频率限制，等待60秒后重试...")
                    time.sleep(60)
                    self.rate_limiter = TokenBucket(rate_per_minute=45, capacity=45)

                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    self.logger.error(f"获取 {ts_code} 数据失败，已达最大重试次数")
                    return None

    def filter_existing_data(self, df: pd.DataFrame, ts_code: str) -> pd.DataFrame:
        if df.empty:
            return df

        existing_dates = self.get_existing_dates_for_stock(ts_code)
        if not existing_dates:
            return df

        df_dates = df['trade_date'].dt.strftime('%Y%m%d')
        mask = ~df_dates.isin(existing_dates)
        filtered_df = df[mask].copy()

        if len(filtered_df) < len(df):
            self.logger.info(f"{ts_code}: 过滤掉 {len(df) - len(filtered_df)} 条已存在的数据")

        return filtered_df

    def save_to_postgres_batch(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        try:
            records = []
            for _, row in df.iterrows():
                records.append((
                    row['ts_code'], row['trade_date'], row['open'], row['high'],
                    row['low'], row['close'], row['pre_close'], row['change'],
                    row['pct_chg'], row['vol'], row['amount']
                ))

            sql = """
                INSERT INTO daily_data 
                (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ts_code, trade_date) DO NOTHING
            """

            self.cursor.executemany(sql, records)
            self.conn.commit()

            ts_code = df.iloc[0]['ts_code']
            new_dates = set(df['trade_date'].dt.strftime('%Y%m%d'))
            if ts_code in self.existing_dates_cache:
                self.existing_dates_cache[ts_code].update(new_dates)
            else:
                self.existing_dates_cache[ts_code] = new_dates

            return len(records)

        except Exception as e:
            self.conn.rollback()
            self.logger.error(f"批量保存数据失败: {e}")
            return 0

    def save_to_parquet(self, df: pd.DataFrame, ts_code: str):
        if df.empty or not self.parquet_available:
            return

        try:
            for (year, month), group in df.groupby([df['trade_date'].dt.year, df['trade_date'].dt.month]):
                file_path = BASE_DATA_DIR / "parquet" / ts_code / f"year={year}" / f"month={month}" / "data.parquet"
                compression = 'snappy' if self.parquet_engine == 'pyarrow' else 'gzip'

                if file_path.exists():
                    try:
                        existing_df = pd.read_parquet(file_path, engine=self.parquet_engine)
                        combined_df = pd.concat([existing_df, group]).drop_duplicates(
                            subset=['ts_code', 'trade_date'], keep='last'
                        ).sort_values('trade_date')

                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        combined_df.to_parquet(file_path, index=False, compression=compression,
                                               engine=self.parquet_engine)
                    except Exception as e:
                        self.logger.warning(f"读取现有Parquet文件失败，创建新文件: {e}")
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        group.to_parquet(file_path, index=False, compression=compression, engine=self.parquet_engine)
                else:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    group.to_parquet(file_path, index=False, compression=compression, engine=self.parquet_engine)

        except Exception as e:
            self.logger.error(f"保存Parquet失败: {e}")

    def save_to_csv_as_fallback(self, df: pd.DataFrame, ts_code: str):
        if df.empty:
            return

        try:
            for (year, month), group in df.groupby([df['trade_date'].dt.year, df['trade_date'].dt.month]):
                file_path = BASE_DATA_DIR / "csv" / ts_code / f"year={year}" / f"month={month}" / "data.csv"

                if file_path.exists():
                    try:
                        existing_df = pd.read_csv(file_path)
                        existing_df['trade_date'] = pd.to_datetime(existing_df['trade_date'])
                        combined_df = pd.concat([existing_df, group]).drop_duplicates(
                            subset=['ts_code', 'trade_date'], keep='last'
                        ).sort_values('trade_date')

                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        combined_df.to_csv(file_path, index=False)
                    except Exception as e:
                        self.logger.warning(f"读取现有CSV文件失败，创建新文件: {e}")
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        group.to_csv(file_path, index=False)
                else:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    group.to_csv(file_path, index=False)

        except Exception as e:
            self.logger.error(f"保存CSV失败: {e}")

    def update_single_stock(self, ts_code: str, force_update: bool = False) -> bool:
        try:
            last_date = self.get_last_trade_date(ts_code)

            if last_date and not force_update:
                start_date = (pd.Timestamp(last_date) + pd.Timedelta(days=1)).strftime('%Y%m%d')
                today = datetime.now().strftime('%Y%m%d')

                if start_date > today:
                    self.logger.info(f"{ts_code} 数据已是最新")
                    return True
            else:
                # ====================================================
                # 👉 【已修改】首次拉取或全新同步时，严格限制从 20260526 开始
                # ====================================================
                start_date = '20260525'

            end_date = datetime.now().strftime('%Y%m%d')
            self.logger.info(f"更新 {ts_code}: {start_date} 到 {end_date}")

            df = self.fetch_daily_data(ts_code, start_date, end_date)

            if df is not None and not df.empty:
                new_df = self.filter_existing_data(df, ts_code)

                if not new_df.empty:
                    saved_count = self.save_to_postgres_batch(new_df)

                    if self.parquet_available:
                        self.save_to_parquet(new_df, ts_code)
                    else:
                        self.save_to_csv_as_fallback(new_df, ts_code)

                    self.logger.info(f"{ts_code} 更新完成，新增 {saved_count} 条记录")
                    return True
                else:
                    self.logger.info(f"{ts_code} 无新数据")
                    return False
            else:
                self.logger.warning(f"{ts_code} 无数据返回")
                return False

        except Exception as e:
            self.logger.error(f"更新 {ts_code} 失败: {e}")
            return False

    def batch_update_stocks(self, batch_size: int = 50):
        all_stocks = self.get_all_stocks()
        total = len(all_stocks)

        checkpoint_file = BASE_DATA_DIR / "checkpoint.txt"
        if checkpoint_file.exists():
            with open(checkpoint_file, 'r') as f:
                last_stock = f.read().strip()
                if last_stock in all_stocks:
                    start_idx = all_stocks.index(last_stock) + 1
                    self.logger.info(f"从断点继续: {last_stock} (索引: {start_idx})")
                else:
                    start_idx = 0
        else:
            start_idx = 0

        self.logger.info(f"开始批量更新 {total} 只股票，从索引 {start_idx} 开始")

        for i in range(start_idx, total, batch_size):
            batch = all_stocks[i:i + batch_size]
            success_count = 0

            existing_data = self.get_existing_dates_batch(batch)

            for ts_code in batch:
                try:
                    if ts_code not in existing_data:
                        existing_data[ts_code] = set()
                    self.existing_dates_cache[ts_code] = existing_data[ts_code]

                    if self.update_single_stock(ts_code):
                        success_count += 1
                except Exception as e:
                    self.logger.error(f"更新 {ts_code} 时出错: {e}")

                with open(checkpoint_file, 'w') as f:
                    f.write(ts_code)

            self.logger.info(f"批次 {i // batch_size + 1}: 成功 {success_count}/{len(batch)}")

            if i + batch_size < total:
                wait_time = 60
                self.logger.info(f"批次完成，等待 {wait_time} 秒后继续...")
                time.sleep(wait_time)

        if checkpoint_file.exists():
            checkpoint_file.unlink()

    def update_today_data(self):
        today = datetime.now().strftime('%Y%m%d')
        self.logger.info(f"开始更新 {today} 的数据")

        try:
            if not self.rate_limiter.acquire(tokens=1):
                self.logger.error("无法获取令牌，今日数据更新终止")
                return False

            df = pro.daily(trade_date=today)

            if df is not None and not df.empty:
                df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')

                existing_data = self.get_existing_dates_batch(df['ts_code'].unique().tolist())

                filtered_rows = []
                for _, row in df.iterrows():
                    ts_code = row['ts_code']
                    trade_date_str = row['trade_date'].strftime('%Y%m%d')

                    if ts_code not in existing_data or trade_date_str not in existing_data[ts_code]:
                        filtered_rows.append(row)

                if filtered_rows:
                    new_df = pd.DataFrame(filtered_rows)
                    saved_count = self.save_to_postgres_batch(new_df)
                    self.logger.info(f"今日数据更新完成，新增 {saved_count} 条记录")

                    for ts_code in new_df['ts_code'].unique():
                        stock_data = new_df[new_df['ts_code'] == ts_code]
                        if self.parquet_available:
                            self.save_to_parquet(stock_data, ts_code)
                        else:
                            self.save_to_csv_as_fallback(stock_data, ts_code)
                else:
                    self.logger.info("今日数据已全部存在，无需更新")

                return True
            else:
                self.logger.warning("今日无交易数据")
                return False

        except Exception as e:
            self.logger.error(f"更新今日数据失败: {e}")
            return False

    def cleanup_old_data(self, days_to_keep: int = 30):
        try:
            if self.parquet_available:
                parquet_dir = BASE_DATA_DIR / "parquet"
                cutoff_date = datetime.now() - timedelta(days=days_to_keep)

                if parquet_dir.exists():
                    for stock_dir in parquet_dir.iterdir():
                        if stock_dir.is_dir():
                            for year_dir in stock_dir.iterdir():
                                if year_dir.is_dir():
                                    year = int(year_dir.name.split('=')[1])
                                    for month_dir in year_dir.iterdir():
                                        if month_dir.is_dir():
                                            month = int(month_dir.name.split('=')[1])
                                            if year < cutoff_date.year or (
                                                    year == cutoff_date.year and month < cutoff_date.month):
                                                for parquet_file in month_dir.glob('*.parquet'):
                                                    parquet_file.unlink()
                                                month_dir.rmdir()
                    self.logger.info(f"已清理 {days_to_keep} 天前的Parquet文件")

            csv_dir = BASE_DATA_DIR / "csv"
            if csv_dir.exists():
                cutoff_date = datetime.now() - timedelta(days=days_to_keep)

                for stock_dir in csv_dir.iterdir():
                    if stock_dir.is_dir():
                        for year_dir in stock_dir.iterdir():
                            if year_dir.is_dir():
                                year = int(year_dir.name.split('=')[1])
                                for month_dir in year_dir.iterdir():
                                    if month_dir.is_dir():
                                        month = int(month_dir.name.split('=')[1])
                                        if year < cutoff_date.year or (
                                                year == omnibus_date.year and month < cutoff_date.month):
                                            for csv_file in month_dir.glob('*.csv'):
                                                csv_file.unlink()
                                            month_dir.rmdir()
                self.logger.info(f"已清理 {days_to_keep} 天前的CSV文件")

        except Exception as e:
            self.logger.error(f"清理旧数据失败: {e}")

    def close(self):
        self.cursor.close()
        self.conn.close()


def main():
    collector = StockDataCollector()

    try:
        if len(sys.argv) > 1:
            if sys.argv[1] == 'full':
                collector.batch_update_stocks(batch_size=50)
            elif sys.argv[1] == 'today':
                collector.update_today_data()
            elif sys.argv[1] == 'single':
                if len(sys.argv) > 2:
                    collector.update_single_stock(sys.argv[2], force_update=True)
                else:
                    print("请指定股票代码，例如: python data_collector.py single 000001.SZ")
            elif sys.argv[1] == 'cleanup':
                days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
                collector.cleanup_old_data(days)
            elif sys.argv[1] == 'resume':
                collector.batch_update_stocks(batch_size=50)
            elif sys.argv[1] == 'check':
                if len(sys.argv) > 2:
                    ts_code = sys.argv[2]
                    dates = collector.get_existing_dates_for_stock(ts_code)
                    print(f"{ts_code} 共有 {len(dates)} 条数据")
                    if dates:
                        print(f"最早: {min(dates)}，最晚: {max(dates)}")
                else:
                    print("请指定股票代码")
            elif sys.argv[1] == 'install-deps':
                print("正在安装依赖...")
                os.system("pip install pyarrow")
                print("安装完成，请重新运行脚本。")
        else:
            collector.batch_update_stocks(batch_size=50)

    except KeyboardInterrupt:
        collector.logger.info("用户中断执行")
    except Exception as e:
        collector.logger.error(f"执行失败: {e}")
    finally:
        collector.close()


if __name__ == "__main__":
    main()