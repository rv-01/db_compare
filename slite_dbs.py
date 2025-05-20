import sqlite3

def setup_test_data(db_path, table_name, num_rows, modify=False):
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
    cursor.execute(f"DELETE FROM {table_name}")
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

# Create test data
setup_test_data('prod.db', 'employees', 1000)  # 1000 rows in source
setup_test_data('cob.db', 'employees', 900, modify=True)  # 900 rows in target, with modified salaries