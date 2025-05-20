#!/usr/bin/env python3
import time
import psutil
import os
import json
import subprocess
import logging
from memory_profiler import memory_usage
from datetime import datetime
import sqlite3

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('benchmark.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def setup_test_data(db_path: str, table_name: str, num_rows: int, modify: bool = False):
    """Set up test data in SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            employee_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            salary REAL,
            hire_date TEXT
        )
    """)
    # Clear existing data
    cursor.execute(f"DELETE FROM {table_name}")
    
    # Insert test data
    data = [
        (i, f'John{i}', f'Doe{i}', 50000 + i if not modify else 60000 + i, '2025-05-19')
        for i in range(1, num_rows + 1)
    ]
    cursor.executemany(
        f"INSERT INTO {table_name} (employee_id, first_name, last_name, salary, hire_date) VALUES (?, ?, ?, ?, ?)",
        data
    )
    conn.commit()
    conn.close()

def update_config(config_path: str, batch_size: int, max_threads: int) -> str:
    """Update config file with new batch size and thread count."""
    with open(config_path, 'r') as f:
        config = json.load(f)
    config['batch_size'] = batch_size
    config['max_threads'] = max_threads
    temp_config_path = f"temp_config_{batch_size}_{max_threads}.json"
    with open(temp_config_path, 'w') as f:
        json.dump(config, f, indent=4)
    return temp_config_path

def run_benchmark(config_path: str, table_sizes: list, batch_sizes: list, thread_counts: list):
    """Run benchmark for different configurations and table sizes."""
    process = psutil.Process()
    results = []

    for table_size in table_sizes:
        logger.info(f"Benchmarking for table size: {table_size} rows")
        # Set up test data
        setup_test_data('prod.db', 'employees', table_size)
        setup_test_data('cob.db', 'employees', table_size - 100, modify=True)  # 100 missing rows, some modified

        for batch_size in batch_sizes:
            for max_threads in thread_counts:
                logger.info(f"Testing batch_size={batch_size}, max_threads={max_threads}")
                temp_config_path = update_config(config_path, batch_size, max_threads)

                # Measure memory usage
                start_time = time.time()
                # Run subprocess and measure memory usage
                command = ['python', 'table_compare.py', temp_config_path]
                try:
                    # Use subprocess.Popen to monitor memory during execution
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    mem_usage = []
                    while proc.poll() is None:
                        try:
                            mem_usage.append(psutil.Process(proc.pid).memory_info().rss / 1024 / 1024)  # MB
                        except psutil.NoSuchProcess:
                            break
                        time.sleep(0.1)
                    proc.wait()
                    if proc.returncode != 0:
                        stdout, stderr = proc.communicate()
                        logger.error(f"Subprocess failed: {stderr.decode()}")
                        raise subprocess.CalledProcessError(proc.returncode, command)
                except subprocess.CalledProcessError as e:
                    logger.error(f"Command failed: {e}")
                    continue
                end_time = time.time()

                # Measure CPU usage
                cpu_percent = process.cpu_percent(interval=None)

                execution_time = end_time - start_time
                peak_memory = max(mem_usage) if mem_usage else 0

                result = {
                    'table_size': table_size,
                    'batch_size': batch_size,
                    'max_threads': max_threads,
                    'execution_time_s': execution_time,
                    'peak_memory_mb': peak_memory,
                    'cpu_percent': cpu_percent
                }
                results.append(result)
                logger.info(f"Result: {result}")

                # Clean up temp config
                os.remove(temp_config_path)

    # Save benchmark results
    with open(f"benchmark_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", 'w') as f:
        json.dump(results, f, indent=4)

if __name__ == "__main__":
    config_path = 'config.json'
    table_sizes = [1000, 10000, 100000]  # Test with 1K, 10K, 100K rows
    batch_sizes = [100, 1000, 5000]
    thread_counts = [1, 4, 8]
    run_benchmark(config_path, table_sizes, batch_sizes, thread_counts)