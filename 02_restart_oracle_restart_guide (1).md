# Oracle Table Comparison Tool - Restart Functionality Guide

## 🚀 Overview

The Oracle Table Comparison Tool now includes robust restart/resume capabilities that allow you to:
- **Recover from failures** at the table or chunk level
- **Resume interrupted comparisons** exactly where they left off
- **Handle long-running operations** across multiple sessions
- **Retry failed tables** without re-processing successful ones
- **Track progress** with detailed session management

## 🏗️ Restart Architecture

### Session Management
```
oracle_comparison_state/
├── oracle_session_abc123.json     # Session state file
├── oracle_session_def456.json     # Another session
└── ...

oracle_checkpoints/
├── abc123_HR_EMPLOYEES_chunk_0.pkl    # Chunk-level checkpoints
├── abc123_HR_EMPLOYEES_chunk_1.pkl
├── abc123_SALES_ORDERS_chunk_0.pkl
└── ...
```

### Key Components
1. **Session State**: Tracks overall progress and table status
2. **Chunk Checkpoints**: Saves results of each processed chunk
3. **Schema Awareness**: Uses `SCHEMA_TABLE` format for unique identification
4. **Progress Tracking**: Real-time updates of completion status

---

## 📋 Usage Examples

### 1. Start New Comparison
```bash
# Start fresh comparison
python oracle_comparator_restart.py config.yaml

# Output:
# Created new Oracle session: abc12345-6789-...
# Processing 8 pending tables
```

### 2. Resume After Interruption
```bash
# If interrupted (Ctrl+C), resume with:
python oracle_comparator_restart.py config.yaml --resume abc12345-6789-...

# Output:
# ================================================================================
# ORACLE COMPARISON SESSION: abc12345...
# ================================================================================
# Total Tables: 8
# Completed: 3
# Failed: 1  
# In Progress: 0
# Pending: 4
# Started: 2024-01-15 10:30:00
# Last Update: 2024-01-15 11:45:22
# 
# Table Details:
# --------------------------------------------------------------------------------
# ✅ HR.EMPLOYEES                    completed    25/25
# ✅ HR.DEPARTMENTS                  completed    5/5  
# ✅ SALES.ORDER_ITEMS               completed    42/42
# ❌ FINANCE.TRANSACTIONS            failed       15/20
#    Error: ORA-01017: invalid username/password
# ⏳ INVENTORY.PRODUCTS              pending      0/0
# ⏳ AUDIT.USER_ACTIONS              pending      0/0
# ⏳ REFERENCE.COUNTRY_CODES         pending      0/0
# ⏳ PROD_DATA.CUSTOMERS             pending      0/0
# ================================================================================
```

### 3. Compare Specific Table
```bash
# Compare only one table (with resume capability)
python oracle_comparator_restart.py config.yaml --table HR.EMPLOYEES --resume abc12345

# Can also start fresh for specific table
python oracle_comparator_restart.py config.yaml --table SALES.ORDER_ITEMS
```

### 4. Retry Failed Tables Only
```bash
# Fix the connection issue, then retry only failed tables
python oracle_comparator_restart.py config.yaml --resume abc12345 --retry-failed

# Output:
# Retrying 1 failed tables: FINANCE.TRANSACTIONS
# Starting comparison for table: FINANCE.TRANSACTIONS
```

### 5. Session Management
```bash
# List all sessions
python oracle_comparator_restart.py config.yaml --list-sessions

# Output:
# Available Oracle Comparison Sessions:
# ================================================================================
# Session ID: abc12345-6789-1011-1213-141516171819
#   Started: 2024-01-15T10:30:00
#   Status: in_progress
#   Progress: 3/8 tables
#   Config: prod_vs_dr_config.yaml
# --------------------------------------------------------------------------------
# Session ID: def67890-1234-5678-9012-345678901234  
#   Started: 2024-01-14T14:15:00
#   Status: completed
#   Progress: 5/5 tables
#   Config: weekly_audit_config.yaml
# --------------------------------------------------------------------------------

# Check specific session status
python oracle_comparator_restart.py config.yaml --status abc12345

# Clean up old sessions (older than 7 days)
python oracle_comparator_restart.py config.yaml --cleanup 7
```

---

## 🔧 How Restart Logic Works

### 1. Session Initialization
```python
# New session
session_id = "abc12345-6789-..."
state_file = "oracle_comparison_state/oracle_session_abc12345.json"

# Track each table with schema awareness
table_progress = {
    "HR_EMPLOYEES": TableProgress(
        schema_name="HR",
        table_name="EMPLOYEES", 
        status="pending",
        run_id="unique-run-id",
        total_chunks=0,
        completed_chunks=0
    ),
    "SALES_ORDER_ITEMS": TableProgress(...),
    # ... more tables
}
```

### 2. Chunk-Level Checkpointing
```python
# For each chunk processed:
chunk_result = {
    'missing_in_target': {'pk1', 'pk2'},
    'missing_in_source': {'pk3'},
    'different_rows': {'pk4', 'pk5'},
    'matching_rows': {...},
    'source_data': {...},
    'target_data': {...}
}

# Save checkpoint
checkpoint_manager.save_chunk_checkpoint(
    session_id="abc12345",
    table_unique_id="HR_EMPLOYEES", 
    chunk_index=5,
    chunk_result=chunk_result
)
```

### 3. Resume Logic
```python
# On resume, load existing checkpoints
existing_checkpoints = checkpoint_manager.load_chunk_checkpoints(
    session_id, "HR_EMPLOYEES"
)

# Skip already processed chunks
for i in range(total_chunks):
    if i in existing_checkpoints:
        # Use saved result
        comparison = existing_checkpoints[i]
        logger.info(f"Skipping chunk {i+1}/{total_chunks} (already processed)")
    else:
        # Process new chunk
        comparison = compare_chunks(source_chunk, target_chunk)
        save_chunk_checkpoint(session_id, table_id, i, comparison)
        logger.info(f"Processed chunk {i+1}/{total_chunks}")
```

### 4. Schema-Aware Table Identification
```python
# Unique table identification prevents confusion
def get_unique_identifier(schema_name, table_name):
    return f"{schema_name}_{table_name}"

# Examples:
"HR_EMPLOYEES"           # HR schema, EMPLOYEES table
"SALES_ORDER_ITEMS"      # SALES schema, ORDER_ITEMS table  
"PROD_DATA_CUSTOMERS"    # PROD_DATA schema, CUSTOMERS table
"TEST_DATA_CUSTOMERS"    # TEST_DATA schema, CUSTOMERS table (different!)

# This prevents restart issues when multiple schemas have same table names
```

---

## 💡 Restart Scenarios

### Scenario 1: Network Interruption
```bash
# Comparison running...
Processing table: SALES.ORDER_ITEMS (chunk 15/50)
Network error: Connection lost

# Resume after network is restored
python oracle_comparator_restart.py config.yaml --resume abc12345

# Output:
Found 14 existing chunk checkpoints for SALES.ORDER_ITEMS
Skipping chunks 1-14 (already processed)
Processing chunk 15/50...
```

### Scenario 2: Database Maintenance
```bash
# Comparison running during maintenance window
Processing table: FINANCE.TRANSACTIONS (chunk 8/25)
Database going down for maintenance...

# After maintenance, resume exactly where left off
python oracle_comparator_restart.py config.yaml --resume abc12345

# Continues from chunk 8, doesn't repeat work
```

### Scenario 3: Application Crash
```bash
# System crash or application error
Processing table: AUDIT.USER_ACTIONS (chunk 120/200)
Segmentation fault (core dumped)

# State is automatically saved, resume seamlessly
python oracle_comparator_restart.py config.yaml --resume abc12345

# Output:
Table AUDIT.USER_ACTIONS status: in_progress (120/200 chunks completed)
Resuming from chunk 121...
```

### Scenario 4: Selective Retry
```bash
# Some tables failed due to permission issues
❌ FINANCE.TRANSACTIONS    failed    Error: ORA-00942: table or view does not exist
❌ AUDIT.SENSITIVE_DATA    failed    Error: ORA-01031: insufficient privileges

# Fix permissions, then retry only failed tables
python oracle_comparator_restart.py config.yaml --resume abc12345 --retry-failed

# Only processes the 2 failed tables, skips completed ones
```

---

## 🎯 Best Practices

### 1. **Monitor Long-Running Operations**
```bash
# Start comparison in background with logging
nohup python oracle_comparator_restart.py config.yaml > oracle_comparison.log 2>&1 &

# Monitor progress
tail -f oracle_comparison.log

# Check session status anytime
python oracle_comparator_restart.py config.yaml --status abc12345
```

### 2. **Handle Large Tables Strategically**
```yaml
# Break very large tables into smaller chunks
- schema_name: "ARCHIVE"
  table_name: "TRANSACTION_HISTORY"
  primary_key: ["TRANSACTION_ID"]
  chunk_size: 1000  # Small chunks for huge tables
  where_clause: "TRANSACTION_DATE >= DATE '2024-01-01'"
```

### 3. **Optimize for Your Environment**
```yaml
# Adjust based on your infrastructure
max_workers: 2  # Conservative for production systems
chunk_size: 5000  # Balance between memory and performance

# For development environments
max_workers: 8  # More aggressive
chunk_size: 15000  # Larger chunks
```

### 4. **Regular Cleanup**
```bash
# Clean up old sessions weekly
python oracle_comparator_restart.py config.yaml --cleanup 7

# Or set up cron job
0 2 * * 0 /path/to/python oracle_comparator_restart.py config.yaml --cleanup 7
```

---

## 🔍 Troubleshooting

### Common Issues and Solutions

**1. Session Not Found**
```bash
# Error: Session abc12345 not found or corrupted
# Solution: Check if session ID is correct
python oracle_comparator_restart.py config.yaml --list-sessions
```

**2. Checkpoint Corruption**
```bash
# Error loading checkpoint file
# Solution: Delete corrupted checkpoint and restart that table
rm oracle_checkpoints/abc12345_HR_EMPLOYEES_chunk_*.pkl
python oracle_comparator_restart.py config.yaml --resume abc12345 --table HR.EMPLOYEES
```

**3. Schema/Table Not Found**
```bash
# Error: ORA-00942: table or view does not exist
# Check schema and table names in config
sqlplus user/pass@db
SQL> SELECT owner, table_name FROM all_tables WHERE table_name = 'EMPLOYEES';
```

**4. Memory Issues with Large Chunks**
```yaml
# Reduce chunk size in config
- schema_name: "LARGE_SCHEMA"
  table_name: "HUGE_TABLE"
  chunk_size: 1000  # Reduce from 10000 to 1000
```

**5. Connection Timeouts**
```python
# The tool automatically retries connection errors
# But you can adjust Oracle connection settings:
# - Increase SQLNET.RECV_TIMEOUT
# - Check network stability
# - Use connection pooling if available
```

---

## 📊 Performance Monitoring

### Session Progress Tracking
```bash
# Real-time status monitoring
watch -n 30 "python oracle_comparator_restart.py config.yaml --status abc12345"

# Get detailed JSON output for automation
python oracle_comparator_restart.py config.yaml --status abc12345 | jq .
```

### Chunk-Level Progress
```python
# Progress updates in logs
2024-01-15 10:45:22 - INFO - Processed chunk 25/100 for HR.EMPLOYEES
2024-01-15 10:46:15 - INFO - Processed chunk 26/100 for HR.EMPLOYEES
2024-01-15 10:47:08 - INFO - Processed chunk 27/100 for HR.EMPLOYEES

# Estimated completion time calculation
chunks_remaining = total_chunks - completed_chunks
avg_time_per_chunk = total_time / completed_chunks
estimated_completion = chunks_remaining * avg_time_per_chunk
```

### Resource Usage
```bash
# Monitor system resources during comparison
top -p $(pgrep -f oracle_comparator_restart.py)

# Oracle database monitoring
SELECT sid, serial#, username, program, status, sql_id
FROM v$session 
WHERE program LIKE '%python%' OR username = 'YOUR_COMPARISON_USER';
```

---

## 🚀 Advanced Features

### 1. **Parallel Table Processing** (Future Enhancement)
```python
# Currently processes tables sequentially for stability
# Future: Process multiple tables in parallel
with ProcessPoolExecutor(max_workers=3) as executor:
    futures = {executor.submit(compare_table, table): table for table in pending_tables}
```

### 2. **Incremental Comparison**
```yaml
# Compare only recent changes
- schema_name: "SALES"
  table_name: "ORDERS"
  where_clause: "MODIFIED_DATE >= SYSDATE - 1"  # Last 24 hours only
```

### 3. **Custom Recovery Strategies**
```python
# Implement custom recovery logic for specific error types
def handle_oracle_error(error_code, table_config):
    if error_code == 'ORA-01017':  # Invalid credentials
        return 'retry_with_new_credentials'
    elif error_code == 'ORA-00942':  # Table not found
        return 'skip_table'
    else:
        return 'fail_and_continue'
```

### 4. **Integration with Monitoring Systems**
```bash
# Send alerts on failures
if [ $? -ne 0 ]; then
    curl -X POST "https://hooks.slack.com/services/..." \
         -d '{"text":"Oracle comparison failed for session abc12345"}'
fi
```

This comprehensive restart functionality ensures that your Oracle table comparisons are resilient, efficient, and can handle enterprise-scale operations with confidence. The schema-aware design prevents conflicts and provides precise recovery capabilities for complex multi-schema environments.
