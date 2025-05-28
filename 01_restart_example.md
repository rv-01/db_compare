# Practical Restart Example - Step by Step

Let's trace through a real example of how the restart logic works when comparing 3 Oracle tables.

## 🏁 Initial Setup

**Configuration (config.yaml):**
```yaml
source_database:
  host: "prod-oracle.company.com"
  port: 1521
  database: "PROD"
  username: "readonly_user"
  password: "secure_pass"

target_database:
  host: "dr-oracle.company.com" 
  port: 1521
  database: "DRPROD"
  username: "readonly_user"
  password: "secure_pass"

tables:
  - table_name: "EMPLOYEES"      # 50,000 rows
    primary_key: ["EMPLOYEE_ID"]
    chunk_size: 10000           # 5 chunks
    
  - table_name: "CUSTOMERS"     # 80,000 rows  
    primary_key: ["CUSTOMER_ID"]
    chunk_size: 10000           # 8 chunks
    
  - table_name: "ORDERS"        # 200,000 rows
    primary_key: ["ORDER_ID"] 
    chunk_size: 10000           # 20 chunks
```

---

## 🚀 First Run - Script Execution

```bash
$ python oracle_comparator.py config.yaml
```

### Time: 10:00:00 - Session Creation
```
2024-01-15 10:00:00 - INFO - Created new session: abc123-def456-789ghi
2024-01-15 10:00:00 - INFO - Initializing comparison for 3 tables
```

**Files Created:**
```
comparison_state/
└── session_abc123-def456-789ghi.json
```

**Initial State:**
```json
{
  "session_id": "abc123-def456-789ghi",
  "start_time": "2024-01-15T10:00:00",
  "total_tables": 3,
  "completed_tables": 0,
  "status": "started",
  "table_progress": {
    "EMPLOYEES": {"status": "pending", "completed_chunks": 0, "total_chunks": 0},
    "CUSTOMERS": {"status": "pending", "completed_chunks": 0, "total_chunks": 0},
    "ORDERS": {"status": "pending", "completed_chunks": 0, "total_chunks": 0}
  }
}
```

### Time: 10:00:30 - EMPLOYEES Table Processing
```
2024-01-15 10:00:30 - INFO - Starting comparison for table: EMPLOYEES
2024-01-15 10:00:30 - INFO - COMPARISON_START - Run ID: run-001-emp, Table: EMPLOYEES
2024-01-15 10:00:32 - INFO - Source table rows: 50,000
2024-01-15 10:00:32 - INFO - Target table rows: 50,000
2024-01-15 10:00:32 - INFO - Will process in 5 chunks of 10,000 rows each
```

**State Update:**
```json
{
  "table_progress": {
    "EMPLOYEES": {
      "status": "in_progress",
      "total_chunks": 5,
      "completed_chunks": 0,
      "processed_rows": 0
    }
  }
}
```

### Time: 10:01:15 - EMPLOYEES Chunk Processing
```
2024-01-15 10:01:15 - INFO - Processed chunk 1/5
2024-01-15 10:01:45 - INFO - Processed chunk 2/5
2024-01-15 10:02:20 - INFO - Processed chunk 3/5
2024-01-15 10:02:55 - INFO - Processed chunk 4/5
2024-01-15 10:03:30 - INFO - Processed chunk 5/5
2024-01-15 10:03:35 - INFO - COMPARISON_END - Table: EMPLOYEES, Results: {matching: 49995, missing_in_target: 3, different: 2}
2024-01-15 10:03:35 - INFO - Generated SQL files: sync_target_EMPLOYEES_20240115_100335_abc12345.sql
2024-01-15 10:03:35 - INFO - Completed table: EMPLOYEES
```

**Files After EMPLOYEES:**
```
comparison_state/
└── session_abc123-def456-789ghi.json

sync_target_EMPLOYEES_20240115_100335_abc12345.sql
comparison_summary_EMPLOYEES_20240115_100335.txt
```

**State After EMPLOYEES:**
```json
{
  "completed_tables": 1,
  "table_progress": {
    "EMPLOYEES": {
      "status": "completed",
      "total_chunks": 5,
      "completed_chunks": 5,
      "processed_rows": 50000
    },
    "CUSTOMERS": {"status": "pending"},
    "ORDERS": {"status": "pending"}
  }
}
```

### Time: 10:03:40 - CUSTOMERS Table Processing Starts
```
2024-01-15 10:03:40 - INFO - Starting comparison for table: CUSTOMERS
2024-01-15 10:03:42 - INFO - Source table rows: 80,000
2024-01-15 10:03:42 - INFO - Target table rows: 80,000
2024-01-15 10:03:42 - INFO - Will process in 8 chunks of 10,000 rows each
```

```
2024-01-15 10:04:20 - INFO - Processed chunk 1/8
2024-01-15 10:04:55 - INFO - Processed chunk 2/8
2024-01-15 10:05:30 - INFO - Processed chunk 3/8
```

**Checkpoint Files Created:**
```
checkpoints/
├── abc123_CUSTOMERS_chunk_0.pkl  # Chunk 1 result
├── abc123_CUSTOMERS_chunk_1.pkl  # Chunk 2 result  
└── abc123_CUSTOMERS_chunk_2.pkl  # Chunk 3 result
```

### Time: 10:05:45 - 💥 CRASH! (Network timeout)
```
2024-01-15 10:05:45 - ERROR - Database connection lost
Traceback (most recent call last):
  ...
oracledb.DatabaseError: Network timeout
```

**State at Crash:**
```json
{
  "session_id": "abc123-def456-789ghi", 
  "completed_tables": 1,
  "table_progress": {
    "EMPLOYEES": {"status": "completed"},
    "CUSTOMERS": {
      "status": "in_progress",
      "total_chunks": 8,
      "completed_chunks": 3,
      "processed_rows": 30000,
      "last_update": "2024-01-15T10:05:30"
    },
    "ORDERS": {"status": "pending"}
  }
}
```

**Files Preserved:**
```
comparison_state/
└── session_abc123-def456-789ghi.json  ✅ State preserved

checkpoints/
├── abc123_CUSTOMERS_chunk_0.pkl       ✅ Chunk results preserved
├── abc123_CUSTOMERS_chunk_1.pkl       ✅
└── abc123_CUSTOMERS_chunk_2.pkl       ✅

sync_target_EMPLOYEES_*.sql             ✅ Previous work preserved
comparison_summary_EMPLOYEES_*.txt      ✅
```

---

## 🔄 Resume - Second Run

### Time: 14:30:00 - Resume Session
```bash
$ python oracle_comparator.py config.yaml --resume abc123-def456-789ghi
```

```
2024-01-15 14:30:00 - INFO - Resumed session: abc123-def456-789ghi

SESSION SUMMARY: abc123...
====================================
Total Tables: 3
Completed: 1
Failed: 0  
In Progress: 1
Pending: 1
Started: 2024-01-15 10:00:00
Last Update: 2024-01-15 10:05:30
====================================

2024-01-15 14:30:01 - INFO - Processing 2 pending tables
2024-01-15 14:30:01 - INFO - Skipping completed table: EMPLOYEES  ⭐
2024-01-15 14:30:01 - INFO - Starting comparison for table: CUSTOMERS
```

### Time: 14:30:05 - CUSTOMERS Resume Logic
```
2024-01-15 14:30:05 - INFO - Found 3 existing chunk checkpoints for CUSTOMERS  ⭐
2024-01-15 14:30:05 - INFO - Loading checkpoints: chunk_0.pkl, chunk_1.pkl, chunk_2.pkl
```

**Checkpoint Loading:**
```python
existing_checkpoints = {
    0: {
        'missing_in_target': {'cust_001', 'cust_047'},
        'missing_in_source': set(),
        'different_rows': {'cust_023'},
        'matching_rows': {'cust_002', 'cust_003', ..., 'cust_9999'},
        'source_data': {...},  # Complete row data for SQL generation
        'target_data': {...}
    },
    1: { ... },  # Chunk 1 results
    2: { ... }   # Chunk 2 results
}
```

### Time: 14:30:10 - Skip Completed Chunks
```
2024-01-15 14:30:10 - INFO - Skipping chunk 1/8 (already processed)  ⭐
2024-01-15 14:30:10 - INFO - Skipping chunk 2/8 (already processed)  ⭐  
2024-01-15 14:30:10 - INFO - Skipping chunk 3/8 (already processed)  ⭐
2024-01-15 14:30:15 - INFO - Processing chunk 4/8  ⭐ <- Resumes from failure point
2024-01-15 14:30:50 - INFO - Processing chunk 5/8
2