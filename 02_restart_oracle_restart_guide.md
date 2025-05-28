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
        comparison = existing