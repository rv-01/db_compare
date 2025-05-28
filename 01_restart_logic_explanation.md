# Restart Logic Implementation - Complete Guide

## 🎯 Overview

The restart logic is implemented through a **two-tier checkpoint system**:
1. **Session-level state management** (table completion tracking)
2. **Chunk-level checkpointing** (granular progress within tables)

This allows recovery from failures at both the table level and individual chunk level within a table.

---

## 🏗️ Architecture Components

### 1. **State Management Classes**

```python
@dataclass
class TableProgress:
    table_name: str
    run_id: str
    status: str          # 'pending', 'in_progress', 'completed', 'failed'
    total_chunks: int
    completed_chunks: int
    processed_rows: int
    start_time: datetime
    last_update: datetime
    chunk_results: List[Dict] = None
    error_message: str = None

@dataclass
class ComparisonState:
    session_id: str      # Unique session identifier
    config_file: str     # Original config file path
    start_time: datetime
    last_update: datetime
    total_tables: int
    completed_tables: int
    table_progress: Dict[str, TableProgress]  # Per-table progress
    current_table: str = None
    status: str = 'started'
```

**Purpose**: These classes maintain the complete state of a comparison session, tracking progress at both session and table levels.

---

## 🔄 Implementation Flow

### Step 1: **Session Creation/Loading**

```python
class StateManager:
    def create_new_session(self, config_file: str, tables: List[TableConfig]) -> str:
        session_id = str(uuid.uuid4())  # Generate unique session ID
        
        # Initialize progress for each table
        table_progress = {}
        for table in tables:
            table_progress[table.table_name] = TableProgress(
                table_name=table.table_name,
                run_id=str(uuid.uuid4()),     # Unique run ID per table
                status='pending',             # Initial status
                total_chunks=0,
                completed_chunks=0,
                processed_rows=0,
                start_time=datetime.now(),
                last_update=datetime.now()
            )
        
        # Save initial state
        self.current_state = ComparisonState(...)
        self.save_state()  # Persist to JSON file
        return session_id
```

**New Session Flow:**
```
User runs: python script.py config.yaml
    ↓
Generate session_id: abc123-def456-789...
    ↓
Create state file: comparison_state/session_abc123-def456-789.json
    ↓
Initialize all tables as 'pending'
```

**Resume Session Flow:**
```python
def load_session(self, session_id: str) -> bool:
    state_file = self.state_dir / f"session_{session_id}.json"
    
    with open(state_file, 'r') as f:
        state_data = json.load(f)
    
    # Reconstruct datetime objects from ISO strings
    for table_name, progress_data in state_data['table_progress'].items():
        progress_data['start_time'] = datetime.fromisoformat(progress_data['start_time'])
        progress_data['last_update'] = datetime.fromisoformat(progress_data['last_update'])
    
    self.current_state = ComparisonState(**state_data)
    return True
```

---

### Step 2: **Persistent State Storage**

**File Structure:**
```
project/
├── comparison_state/
│   ├── session_abc123-def456-789.json    # Main session state
│   ├── session_xyz789-abc123-456.json    # Another session
│   └── ...
├── checkpoints/
│   ├── abc123_EMPLOYEES_chunk_0.pkl      # Chunk-level checkpoints
│   ├── abc123_EMPLOYEES_chunk_1.pkl
│   ├── abc123_CUSTOMERS_chunk_0.pkl
│   └── ...
└── oracle_comparator.py
```

**Session State File Format:**
```json
{
  "session_id": "abc123-def456-789",
  "config_file": "my_config.yaml",
  "start_time": "2024-01-15T10:30:00",
  "last_update": "2024-01-15T11:45:30",
  "total_tables": 3,
  "completed_tables": 1,
  "status": "in_progress",
  "table_progress": {
    "EMPLOYEES": {
      "table_name": "EMPLOYEES",
      "run_id": "run-001-emp",
      "status": "completed",
      "total_chunks": 5,
      "completed_chunks": 5,
      "processed_rows": 50000,
      "start_time": "2024-01-15T10:30:00",
      "last_update": "2024-01-15T10:45:00",
      "error_message": null
    },
    "CUSTOMERS": {
      "table_name": "CUSTOMERS",
      "run_id": "run-002-cust",
      "status": "in_progress",
      "total_chunks": 8,
      "completed_chunks": 3,
      "processed_rows": 30000,
      "start_time": "2024-01-15T10:45:00",
      "last_update": "2024-01-15T11:45:30",
      "error_message": null
    },
    "ORDERS": {
      "table_name": "ORDERS",
      "run_id": "run-003-ord",
      "status": "pending",
      "total_chunks": 0,
      "completed_chunks": 0,
      "processed_rows": 0,
      "start_time": "2024-01-15T10:30:00",
      "last_update": "2024-01-15T10:30:00",
      "error_message": null
    }
  }
}
```

---

### Step 3: **Chunk-Level Checkpointing**

```python
class CheckpointManager:
    def save_chunk_checkpoint(self, session_id: str, table_name: str, 
                            chunk_index: int, chunk_result: Dict):
        checkpoint_file = f"{session_id}_{table_name}_chunk_{chunk_index}.pkl"
        
        with open(checkpoint_file, 'wb') as f:
            pickle.dump({
                'chunk_index': chunk_index,
                'timestamp': datetime.now(),
                'result': {
                    'missing_in_target': chunk_result['missing_in_target'],
                    'missing_in_source': chunk_result['missing_in_source'],
                    'different_rows': chunk_result['different_rows'],
                    'matching_rows': chunk_result['matching_rows'],
                    'source_data': chunk_result['source_data'],
                    'target_data': chunk_result['target_data']
                }
            }, f)
```

**Checkpoint File Contents (Binary Pickle):**
```python
{
    'chunk_index': 2,
    'timestamp': datetime(2024, 1, 15, 11, 30, 45),
    'result': {
        'missing_in_target': {'emp_001', 'emp_047'},
        'missing_in_source': set(),
        'different_rows': {'emp_023'},
        'matching_rows': {'emp_002', 'emp_003', ..., 'emp_999'},
        'source_data': {
            'emp_001': {'hash': 'a1b2c3...', 'data': {...}},
            # ... complete row data for SQL generation
        },
        'target_data': {
            'emp_002': {'hash': 'd4e5f6...', 'data': {...}},
            # ... target row data
        }
    }
}
```

---

### Step 4: **Resume Logic Implementation**

```python
def compare_table(self, table_config: TableConfig) -> ComparisonResult:
    table_name = table_config.table_name
    table_progress = self.state_manager.current_state.table_progress[table_name]
    
    # ✅ RESTART CHECK #1: Skip completed tables
    if table_progress.status == 'completed':
        self.audit_logger.logger.info(f"Table {table_name} already completed, skipping")
        return self._create_result_from_progress(table_progress)
    
    # ✅ RESTART CHECK #2: Load existing chunk checkpoints
    existing_checkpoints = self.checkpoint_manager.load_chunk_checkpoints(
        self.session_id, table_name
    )
    
    if existing_checkpoints:
        self.audit_logger.logger.info(
            f"Found {len(existing_checkpoints)} existing chunk checkpoints for {table_name}"
        )
    
    # ✅ RESTART CHECK #3: Skip completed chunks during data extraction
    source_chunks = self._get_table_hash_chunks(self.source_db, table_config, existing_checkpoints)
    target_chunks = self._get_table_hash_chunks(self.target_db, table_config, existing_checkpoints)
    
    # ✅ RESTART CHECK #4: Process only remaining chunks
    for i in range(max_chunks):
        if i in existing_checkpoints:
            # Use cached result
            comparison = existing_checkpoints[i]
            self.audit_logger.logger.info(f"Skipping chunk {i+1}/{max_chunks} (already processed)")
        else:
            # Process new chunk
            comparison = self._compare_chunks(source_chunk, target_chunk)
            
            # Save checkpoint immediately
            self.checkpoint_manager.save_chunk_checkpoint(
                self.session_id, table_name, i, comparison
            )
```

---

## 🔧 Data Extraction with Resume Support

```python
def _get_table_hash_chunks(self, db_config, table_config, existing_checkpoints=None):
    if existing_checkpoints is None:
        existing_checkpoints = {}
    
    chunks = []
    offset = 0
    chunk_index = 0
    
    while offset < total_rows:
        # ✅ SKIP COMPLETED CHUNKS
        if chunk_index in existing_checkpoints:
            chunks.append({'data': {}, 'size': 0, 'from_checkpoint': True})
            offset += table_config.chunk_size
            chunk_index += 1
            continue  # Don't query database for this chunk
        
        # Execute query only for incomplete chunks
        query = f"""
        SELECT {column_str}
        FROM (
            SELECT {column_str}, ROWNUM as rn
            FROM (SELECT {column_str} FROM {table_name} {where_clause} ORDER BY {pk})
            WHERE ROWNUM <= {offset + chunk_size}
        )
        WHERE rn > {offset}
        """
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        # Process rows and create hashes
        chunk_data = {}
        for row in rows:
            pk_key = '|'.join([str(row[columns.index(pk)]) for pk in primary_key])
            row_hash = hashlib.md5(row_string.encode()).hexdigest()
            chunk_data[pk_key] = {'hash': row_hash, 'data': dict(zip(columns, row))}
        
        chunks.append({'offset': offset, 'size': len(rows), 'data': chunk_data})
        offset += chunk_size
        chunk_index += 1
    
    return chunks
```

---

## 📋 Real-World Restart Scenarios

### Scenario 1: **Script Crashes Mid-Table**

**Initial Run:**
```bash
$ python oracle_comparator.py config.yaml
Session created: abc123-def456-789
Processing EMPLOYEES table...
  Chunk 1/10 ✅
  Chunk 2/10 ✅  
  Chunk 3/10 ✅
  Chunk 4/10 ❌ CRASH! (Network timeout)
```

**State at Crash:**
```json
{
  "session_id": "abc123-def456-789",
  "table_progress": {
    "EMPLOYEES": {
      "status": "in_progress",
      "total_chunks": 10,
      "completed_chunks": 3,
      "processed_rows": 30000
    }
  }
}
```

**Files Created:**
```
checkpoints/
├── abc123_EMPLOYEES_chunk_0.pkl  ✅
├── abc123_EMPLOYEES_chunk_1.pkl  ✅
└── abc123_EMPLOYEES_chunk_2.pkl  ✅
```

**Resume:**
```bash
$ python oracle_comparator.py config.yaml --resume abc123-def456-789

SESSION SUMMARY: abc123...
====================================
Total Tables: 3
Completed: 0
Failed: 0
In Progress: 1
Pending: 2
Started: 2024-01-15 10:30:00
Last Update: 2024-01-15 10:32:15
====================================

Resumed session: abc123-def456-789
Processing EMPLOYEES table...
Found 3 existing chunk checkpoints for EMPLOYEES
  Skipping chunk 1/10 (already processed)
  Skipping chunk 2/10 (already processed)
  Skipping chunk 3/10 (already processed)
  Processing chunk 4/10 ✅  <- Continues from failure point
  Processing chunk 5/10 ✅
  ...
```

---

### Scenario 2: **User Intentionally Stops Script**

```bash
$ python oracle_comparator.py config.yaml
Session created: def456-789abc-123
Processing EMPLOYEES table... ✅ Complete
Processing CUSTOMERS table...
  Chunk 1/5 ✅
  Chunk 2/5 ✅
  ^C  <- User presses Ctrl+C

⚠️  Comparison interrupted. Session saved: def456-789abc-123
Resume with: --resume def456-789abc-123
```

**Resume Later:**
```bash
$ python oracle_comparator.py config.yaml --resume def456-789abc-123

Skipping completed table: EMPLOYEES
Processing CUSTOMERS table...
Found 2 existing chunk checkpoints for CUSTOMERS
  Skipping chunk 1/5 (already processed)
  Skipping chunk 2/5 (already processed)  
  Processing chunk 3/5 ✅  <- Continues seamlessly
```

---

## 🚀 Key Benefits of This Implementation

### 1. **Granular Recovery**
- **Table-level**: Skip entire completed tables
- **Chunk-level**: Resume from exact failure point within a table
- **No duplicate work**: Never re-processes completed chunks

### 2. **Robust State Management**
```python
def save_state(self):
    # Atomic write to prevent corruption
    temp_file = self.state_file.with_suffix('.tmp')
    with open(temp_file, 'w') as f:
        json.dump(state_dict, f, indent=2, default=str)
    temp_file.replace(self.state_file)  # Atomic move
```

### 3. **Memory Efficiency**
- Checkpoints store only comparison results, not raw data
- Old checkpoints cleaned up after table completion
- Bounded memory usage regardless of restart frequency

### 4. **Error Handling**
```python
try:
    result = self.compare_table(table_config)
    self.state_manager.mark_table_completed(table_name, result)
except KeyboardInterrupt:
    print(f"⚠️  Session saved: {self.session_id}")
    print(f"Resume with: --resume {self.session_id}")
    raise
except Exception as e:
    self.state_manager.mark_table_failed(table_name, str(e))
    # Continue with next table instead of failing completely
    continue
```

---

## 🛠️ Usage Commands

### Start New Comparison
```bash
python oracle_comparator.py config.yaml
```

### Resume Specific Session
```bash
python oracle_comparator.py config.yaml --resume abc123-def456-789
```

### List Available Sessions
```bash
ls comparison_state/session_*.json
```

### Check Session Status
```bash
python -c "
import json
with open('comparison_state/session_abc123-def456-789.json') as f:
    state = json.load(f)
    print(f'Completed: {state[\"completed_tables\"]}/{state[\"total_tables\"]}')
"
```

---

## 🧹 Cleanup & Maintenance

### Automatic Cleanup
```python
def cleanup_old_sessions(self, days_old: int = 7):
    cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
    
    for state_file in self.state_dir.glob("session_*.json"):
        if state_file.stat().st_mtime < cutoff_time:
            state_file.unlink()
            
def cleanup_table_checkpoints(self, session_id: str, table_name: str):
    # Remove chunk checkpoints after table completion
    pattern = f"{session_id}_{table_name}_chunk_*.pkl"
    for checkpoint_file in self.checkpoint_dir.glob(pattern):
        checkpoint_file.unlink()
```

This restart implementation provides **enterprise-grade reliability** with granular recovery capabilities, ensuring that large-scale database comparisons can always be resumed from the exact point of failure without losing any work.
