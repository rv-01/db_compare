# Oracle Table Comparison Tool - Setup and Usage Guide

## Features

✅ **Scalable Architecture**: Uses multiprocessing and chunking for large tables  
✅ **Hash-based Comparison**: Efficient row-level comparison using MD5 hashing  
✅ **Configurable**: YAML-based configuration for databases and tables  
✅ **SQL Generation**: Automatically generates sync SQL scripts for differences  
✅ **Comprehensive Auditing**: File and database-based audit logging  
✅ **Memory Efficient**: Processes data in configurable chunks  
✅ **Generic Design**: Works with any Oracle table structure  

## Prerequisites

1. **Python 3.7+**
2. **Oracle Client Libraries** (for oracledb - much easier setup than cx_Oracle!)
3. **Network Access** to Oracle databases

## Installation

### 1. Install Oracle Client Libraries (Much Simpler with oracledb!)

**The new `oracledb` library supports two modes:**

**Thin Mode (Recommended - No Oracle Client needed!):**
```bash
# No additional setup required! 
# oracledb thin mode works without Oracle Instant Client
pip install oracledb
```

**Thick Mode (Optional - for advanced features):**
```bash
# Only needed for advanced Oracle features
# Download Oracle Instant Client if you need thick mode
pip install oracledb
```

**Benefits of oracledb over cx_Oracle:**
- ✅ **No Oracle Client installation required** (thin mode)
- ✅ **Easier deployment** - just pip install
- ✅ **Better performance** and memory usage
- ✅ **Modern Python API** with async support
- ✅ **Active development** by Oracle

### 2. Install Python Dependencies

```bash
# For Oracle only
pip install -r requirements.txt

# Or install directly
pip install oracledb PyYAML
```

### 3. Setup Configuration

```bash
# Copy the Oracle configuration template
cp multi_db_config.yaml my_oracle_config.yaml

# Edit with your Oracle database details
vi my_oracle_config.yaml
```

## Configuration

### Oracle Database Configuration
```yaml
source_database:
  db_type: "oracle"  # Specify database type
  host: "your-oracle-db.example.com"
  port: 1521
  database: "ORCL"  # Service name
  username: "your_username"
  password: "your_password"
```

### Table Configuration
```yaml
tables:
  - table_name: "YOUR_TABLE"
    primary_key: ["ID"]  # Can be composite: ["ID1", "ID2"]
    chunk_size: 10000    # Rows per chunk
    columns: ["COL1", "COL2"]  # Optional: specific columns
    where_clause: "STATUS = 'ACTIVE'"  # Optional: filter
```

### Performance Tuning
- **chunk_size**: Balance between memory usage and performance
  - Small tables: 5,000-10,000
  - Large tables: 2,000-5,000
  - Very large tables: 1,000-2,000
- **max_workers**: Number of parallel processes
  - Start with CPU count
  - Increase if database can handle more connections
  - Monitor database load and adjust

## Usage

### Compare All Tables
```bash
python multi_db_comparator.py my_oracle_config.yaml
```

### Compare Specific Table
```bash
python multi_db_comparator.py my_oracle_config.yaml --table EMPLOYEES
```

## Output Files

### 1. Audit Log
- **File**: `table_comparison_audit.log`
- **Content**: Detailed execution logs with timestamps
- **Database**: Optional audit table for centralized logging

### 2. Summary Reports
- **File**: `comparison_summary_{table}_{timestamp}.txt`
- **Content**: High-level comparison statistics

### 3. Sync SQL Scripts
Generated when differences are found:
- **Target Sync**: `sync_target_{table}_{timestamp}_{runid}.sql`
- **Source Sync**: `sync_source_{table}_{timestamp}_{runid}.sql`

**Example SQL Output:**
```sql
-- Insert missing rows
INSERT INTO EMPLOYEES (ID, NAME, DEPT) VALUES (101, 'John Doe', 'IT');

-- Update different rows
UPDATE EMPLOYEES SET NAME = 'Jane Smith', DEPT = 'HR' WHERE ID = 102;

COMMIT;
```

## Advanced Usage

### Custom Chunking Strategy
For very large tables, you might want to implement date-based chunking:

```yaml
tables:
  - table_name: "LARGE_AUDIT_TABLE"
    primary_key: ["ID"]
    chunk_size: 5000
    where_clause: "CREATED_DATE BETWEEN DATE '2024-01-01' AND DATE '2024-01-31'"
```

### Parallel Table Processing
The tool processes tables sequentially but chunks within each table in parallel. For multiple tables, consider running separate instances:

```bash
# Terminal 1
python oracle_table_comparator.py config1.yaml --table TABLE1

# Terminal 2
python oracle_table_comparator.py config2.yaml --table TABLE2
```

### Monitoring Large Operations
```bash
# Monitor progress
tail -f table_comparison_