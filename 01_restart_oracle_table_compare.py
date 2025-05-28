#!/usr/bin/env python3
"""
Oracle Table Comparison Tool
A scalable, configurable tool to compare Oracle tables across different databases
using hashing and multiprocessing with comprehensive audit capabilities.
"""

import oracledb
import hashlib
import json
import logging
import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import yaml
import argparse
from dataclasses import dataclass
import uuid
import csv


@dataclass
class DatabaseConfig:
    """Database configuration class"""
    host: str
    port: int
    service_name: str
    username: str
    password: str
    
    def get_dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"


@dataclass
class TableConfig:
    """Table configuration class"""
    table_name: str
    primary_key: List[str]
    columns: Optional[List[str]] = None
    where_clause: Optional[str] = None
    chunk_size: int = 10000


@dataclass
class ComparisonResult:
    """Comparison result class"""
    table_name: str
    total_source_rows: int
    total_target_rows: int
    matching_rows: int
    missing_in_target: int
    missing_in_source: int
    different_rows: int
    execution_time: float
    chunks_processed: int


@dataclass
class TableProgress:
    """Track progress for each table"""
    table_name: str
    run_id: str
    status: str  # 'pending', 'in_progress', 'completed', 'failed'
    total_chunks: int
    completed_chunks: int
    processed_rows: int
    start_time: datetime
    last_update: datetime
    chunk_results: List[Dict] = None
    error_message: str = None
    
    def __post_init__(self):
        if self.chunk_results is None:
            self.chunk_results = []


@dataclass
class ComparisonState:
    """Overall comparison state for restart capability"""
    session_id: str
    config_file: str
    start_time: datetime
    last_update: datetime
    total_tables: int
    completed_tables: int
    table_progress: Dict[str, TableProgress]
    current_table: str = None
    status: str = 'started'  # 'started', 'in_progress', 'completed', 'failed'


class StateManager:
    """Manages comparison state for restart capability"""
    
    def __init__(self, state_dir: str = "comparison_state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(exist_ok=True)
        self.state_file = None
        self.current_state = None
        
    def create_new_session(self, config_file: str, tables: List[TableConfig]) -> str:
        """Create a new comparison session"""
        session_id = str(uuid.uuid4())
        self.state_file = self.state_dir / f"session_{session_id}.json"
        
        table_progress = {}
        for table in tables:
            table_progress[table.table_name] = TableProgress(
                table_name=table.table_name,
                run_id=str(uuid.uuid4()),
                status='pending',
                total_chunks=0,
                completed_chunks=0,
                processed_rows=0,
                start_time=datetime.now(),
                last_update=datetime.now()
            )
        
        self.current_state = ComparisonState(
            session_id=session_id,
            config_file=config_file,
            start_time=datetime.now(),
            last_update=datetime.now(),
            total_tables=len(tables),
            completed_tables=0,
            table_progress=table_progress
        )
        
        self.save_state()
        return session_id
    
    def load_session(self, session_id: str) -> bool:
        """Load existing session state"""
        self.state_file = self.state_dir / f"session_{session_id}.json"
        
        if not self.state_file.exists():
            return False
            
        try:
            with open(self.state_file, 'r') as f:
                state_data = json.load(f)
            
            # Reconstruct objects from JSON
            table_progress = {}
            for table_name, progress_data in state_data['table_progress'].items():
                # Convert datetime strings back to datetime objects
                progress_data['start_time'] = datetime.fromisoformat(progress_data['start_time'])
                progress_data['last_update'] = datetime.fromisoformat(progress_data['last_update'])
                table_progress[table_name] = TableProgress(**progress_data)
            
            state_data['table_progress'] = table_progress
            state_data['start_time'] = datetime.fromisoformat(state_data['start_time'])
            state_data['last_update'] = datetime.fromisoformat(state_data['last_update'])
            
            self.current_state = ComparisonState(**state_data)
            return True
            
        except Exception as e:
            print(f"Error loading session state: {e}")
            return False
    
    def save_state(self):
        """Save current state to file"""
        if not self.current_state or not self.state_file:
            return
            
        try:
            # Convert to serializable format
            state_dict = asdict(self.current_state)
            
            # Convert datetime objects to strings
            state_dict['start_time'] = self.current_state.start_time.isoformat()
            state_dict['last_update'] = datetime.now().isoformat()
            
            for table_name, progress in state_dict['table_progress'].items():
                progress['start_time'] = progress['start_time'].isoformat()
                progress['last_update'] = datetime.now().isoformat()
            
            with open(self.state_file, 'w') as f:
                json.dump(state_dict, f, indent=2, default=str)
                
        except Exception as e:
            print(f"Error saving state: {e}")
    
    def update_table_progress(self, table_name: str, **kwargs):
        """Update progress for a specific table"""
        if self.current_state and table_name in self.current_state.table_progress:
            progress = self.current_state.table_progress[table_name]
            for key, value in kwargs.items():
                if hasattr(progress, key):
                    setattr(progress, key, value)
            progress.last_update = datetime.now()
            self.current_state.last_update = datetime.now()
            self.save_state()
    
    def mark_table_completed(self, table_name: str, result: ComparisonResult):
        """Mark a table as completed"""
        if self.current_state:
            self.update_table_progress(
                table_name,
                status='completed',
                processed_rows=result.total_source_rows
            )
            self.current_state.completed_tables += 1
            self.save_state()
    
    def mark_table_failed(self, table_name: str, error: str):
        """Mark a table as failed"""
        if self.current_state:
            self.update_table_progress(
                table_name,
                status='failed',
                error_message=error
            )
            self.save_state()
    
    def get_pending_tables(self) -> List[str]:
        """Get list of tables that haven't been completed"""
        if not self.current_state:
            return []
            
        pending = []
        for table_name, progress in self.current_state.table_progress.items():
            if progress.status in ['pending', 'in_progress', 'failed']:
                pending.append(table_name)
        return pending
    
    def get_session_summary(self) -> Dict:
        """Get summary of current session"""
        if not self.current_state:
            return {}
            
        completed = sum(1 for p in self.current_state.table_progress.values() if p.status == 'completed')
        failed = sum(1 for p in self.current_state.table_progress.values() if p.status == 'failed')
        in_progress = sum(1 for p in self.current_state.table_progress.values() if p.status == 'in_progress')
        pending = sum(1 for p in self.current_state.table_progress.values() if p.status == 'pending')
        
        return {
            'session_id': self.current_state.session_id,
            'total_tables': self.current_state.total_tables,
            'completed': completed,
            'failed': failed,
            'in_progress': in_progress,
            'pending': pending,
            'start_time': self.current_state.start_time,
            'last_update': self.current_state.last_update
        }
    
    def cleanup_old_sessions(self, days_old: int = 7):
        """Clean up old session files"""
        cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
        
        for state_file in self.state_dir.glob("session_*.json"):
            if state_file.stat().st_mtime < cutoff_time:
                try:
                    state_file.unlink()
                    print(f"Cleaned up old session file: {state_file.name}")
                except Exception as e:
                    print(f"Error cleaning up {state_file.name}: {e}")


class CheckpointManager:
    """Manages checkpoints for chunk-level recovery"""
    
    def __init__(self, checkpoint_dir: str = "checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
    
    def save_chunk_checkpoint(self, session_id: str, table_name: str, 
                            chunk_index: int, chunk_result: Dict):
        """Save checkpoint for a completed chunk"""
        checkpoint_file = (self.checkpoint_dir / 
                          f"{session_id}_{table_name}_chunk_{chunk_index}.pkl")
        
        try:
            with open(checkpoint_file, 'wb') as f:
                pickle.dump({
                    'chunk_index': chunk_index,
                    'timestamp': datetime.now(),
                    'result': chunk_result
                }, f)
        except Exception as e:
            print(f"Error saving chunk checkpoint: {e}")
    
    def load_chunk_checkpoints(self, session_id: str, table_name: str) -> Dict[int, Dict]:
        """Load all chunk checkpoints for a table"""
        checkpoints = {}
        pattern = f"{session_id}_{table_name}_chunk_*.pkl"
        
        for checkpoint_file in self.checkpoint_dir.glob(pattern):
            try:
                with open(checkpoint_file, 'rb') as f:
                    data = pickle.load(f)
                    checkpoints[data['chunk_index']] = data['result']
            except Exception as e:
                print(f"Error loading checkpoint {checkpoint_file}: {e}")
        
        return checkpoints
    
    def cleanup_table_checkpoints(self, session_id: str, table_name: str):
        """Clean up checkpoints for a completed table"""
        pattern = f"{session_id}_{table_name}_chunk_*.pkl"
        
        for checkpoint_file in self.checkpoint_dir.glob(pattern):
            try:
                checkpoint_file.unlink()
            except Exception as e:
                print(f"Error cleaning up checkpoint {checkpoint_file}: {e}")


class AuditLogger:
    """Handles audit logging to file and database"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.audit_file = config.get('audit_file', 'table_comparison_audit.log')
        self.setup_logging()
        self.audit_db_config = config.get('audit_database')
        
    def setup_logging(self):
        """Setup file logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.audit_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def log_comparison_start(self, run_id: str, table_name: str):
        """Log comparison start"""
        message = f"COMPARISON_START - Run ID: {run_id}, Table: {table_name}"
        self.logger.info(message)
        self._log_to_audit_table(run_id, table_name, 'START', message)
    
    def log_comparison_end(self, run_id: str, table_name: str, result: ComparisonResult):
        """Log comparison end with results"""
        message = (f"COMPARISON_END - Run ID: {run_id}, Table: {table_name}, "
                  f"Results: {result.__dict__}")
        self.logger.info(message)
        self._log_to_audit_table(run_id, table_name, 'END', message, result.__dict__)
    
    def log_error(self, run_id: str, table_name: str, error: str):
        """Log errors"""
        message = f"ERROR - Run ID: {run_id}, Table: {table_name}, Error: {error}"
        self.logger.error(message)
        self._log_to_audit_table(run_id, table_name, 'ERROR', message)
    
    def _log_to_audit_table(self, run_id: str, table_name: str, status: str, 
                           message: str, details: Dict = None):
        """Log to audit table if configured"""
        if not self.audit_db_config:
            return
            
        try:
            db_config = DatabaseConfig(**self.audit_db_config)
            connection = oracledb.connect(
                user=db_config.username,
                password=db_config.password,
                dsn=db_config.get_dsn()
            )
            
            cursor = connection.cursor()
            
            # Create audit table if not exists
            create_table_sql = """
            BEGIN
                EXECUTE IMMEDIATE 'CREATE TABLE table_comparison_audit (
                    audit_id VARCHAR2(50) PRIMARY KEY,
                    run_id VARCHAR2(50),
                    table_name VARCHAR2(100),
                    status VARCHAR2(20),
                    message CLOB,
                    details CLOB,
                    created_date DATE DEFAULT SYSDATE
                )';
            EXCEPTION
                WHEN OTHERS THEN
                    IF SQLCODE != -955 THEN  -- Table already exists
                        RAISE;
                    END IF;
            END;
            """
            
            cursor.execute(create_table_sql)
            
            # Insert audit record
            insert_sql = """
            INSERT INTO table_comparison_audit 
            (audit_id, run_id, table_name, status, message, details, created_date)
            VALUES (:1, :2, :3, :4, :5, :6, SYSDATE)
            """
            
            cursor.execute(insert_sql, (
                str(uuid.uuid4()),
                run_id,
                table_name,
                status,
                message,
                json.dumps(details) if details else None
            ))
            
            connection.commit()
            cursor.close()
            connection.close()
            
        except Exception as e:
            self.logger.error(f"Failed to log to audit table: {str(e)}")


class OracleTableComparator:
    """Main class for Oracle table comparison with restart capability"""
    
    def __init__(self, config_file: str, session_id: str = None):
        self.config = self._load_config(config_file)
        self.source_db = DatabaseConfig(**self.config['source_database'])
        self.target_db = DatabaseConfig(**self.config['target_database'])
        self.tables = [TableConfig(**table) for table in self.config['tables']]
        self.audit_logger = AuditLogger(self.config.get('audit', {}))
        self.max_workers = self.config.get('max_workers', mp.cpu_count())
        
        # Initialize state and checkpoint managers
        self.state_manager = StateManager()
        self.checkpoint_manager = CheckpointManager()
        
        # Handle session management
        if session_id:
            if self.state_manager.load_session(session_id):
                self.session_id = session_id
                self.audit_logger.logger.info(f"Resumed session: {session_id}")
                self._print_session_summary()
            else:
                raise ValueError(f"Session {session_id} not found or corrupted")
        else:
            self.session_id = self.state_manager.create_new_session(config_file, self.tables)
            self.audit_logger.logger.info(f"Created new session: {self.session_id}")
    
    def _print_session_summary(self):
        """Print current session summary"""
        summary = self.state_manager.get_session_summary()
        print("\n" + "="*60)
        print(f"SESSION SUMMARY: {summary['session_id'][:8]}...")
        print("="*60)
        print(f"Total Tables: {summary['total_tables']}")
        print(f"Completed: {summary['completed']}")
        print(f"Failed: {summary['failed']}")
        print(f"In Progress: {summary['in_progress']}")
        print(f"Pending: {summary['pending']}")
        print(f"Started: {summary['start_time']}")
        print(f"Last Update: {summary['last_update']}")
        print("="*60 + "\n")
        
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(config_file, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise Exception(f"Failed to load config file {config_file}: {str(e)}")
    
    def _get_connection(self, db_config: DatabaseConfig):
        """Get database connection"""
        return oracledb.connect(
            user=db_config.username,
            password=db_config.password,
            dsn=db_config.get_dsn()
        )
    
    def _get_table_hash_chunks(self, db_config: DatabaseConfig, table_config: TableConfig, 
                              existing_checkpoints: Dict[int, Dict] = None) -> List[Dict]:
        """Get table data in chunks with hash values (skip if checkpoint exists)"""
        if existing_checkpoints is None:
            existing_checkpoints = {}
            
        connection = self._get_connection(db_config)
        cursor = connection.cursor()
        
        try:
            # Build column list
            columns = table_config.columns or self._get_table_columns(cursor, table_config.table_name)
            column_str = ', '.join(columns)
            
            # Build query
            where_clause = f"WHERE {table_config.where_clause}" if table_config.where_clause else ""
            
            # Get total count
            count_sql = f"SELECT COUNT(*) FROM {table_config.table_name} {where_clause}"
            cursor.execute(count_sql)
            total_rows = cursor.fetchone()[0]
            
            chunks = []
            offset = 0
            chunk_index = 0
            
            while offset < total_rows:
                # Skip chunk if checkpoint exists
                if chunk_index in existing_checkpoints:
                    chunks.append({'data': {}, 'size': 0, 'from_checkpoint': True})
                    offset += table_config.chunk_size
                    chunk_index += 1
                    continue
                
                # Query with pagination
                query = f"""
                SELECT {column_str}
                FROM (
                    SELECT {column_str}, ROWNUM as rn
                    FROM (
                        SELECT {column_str}
                        FROM {table_config.table_name}
                        {where_clause}
                        ORDER BY {', '.join(table_config.primary_key)}
                    )
                    WHERE ROWNUM <= {offset + table_config.chunk_size}
                )
                WHERE rn > {offset}
                """
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                if not rows:
                    break
                
                # Create hash for each row
                chunk_data = {}
                for row in rows:
                    # Create primary key
                    pk_values = [str(row[columns.index(pk)]) for pk in table_config.primary_key]
                    pk_key = '|'.join(pk_values)
                    
                    # Create hash of all column values
                    row_str = '|'.join([str(val) if val is not None else 'NULL' for val in row])
                    row_hash = hashlib.md5(row_str.encode()).hexdigest()
                    
                    chunk_data[pk_key] = {
                        'hash': row_hash,
                        'data': dict(zip(columns, row))
                    }
                
                chunks.append({
                    'offset': offset,
                    'size': len(rows),
                    'data': chunk_data
                })
                
                offset += table_config.chunk_size
                chunk_index += 1
            
            return chunks
            
        finally:
            cursor.close()
            connection.close()
    
    def _get_table_columns(self, cursor, table_name: str) -> List[str]:
        """Get all columns for a table"""
        cursor.execute("""
            SELECT column_name 
            FROM user_tab_columns 
            WHERE table_name = UPPER(:1)
            ORDER BY column_id
        """, (table_name,))
        
        return [row[0] for row in cursor.fetchall()]
    
    def _compare_chunks(self, source_chunk: Dict, target_chunk: Dict) -> Dict:
        """Compare two chunks and return differences"""
        source_data = source_chunk['data']
        target_data = target_chunk['data']
        
        source_keys = set(source_data.keys())
        target_keys = set(target_data.keys())
        
        missing_in_target = source_keys - target_keys
        missing_in_source = target_keys - source_keys
        common_keys = source_keys & target_keys
        
        different_rows = set()
        matching_rows = set()
        
        for key in common_keys:
            if source_data[key]['hash'] != target_data[key]['hash']:
                different_rows.add(key)
            else:
                matching_rows.add(key)
        
        return {
            'missing_in_target': missing_in_target,
            'missing_in_source': missing_in_source,
            'different_rows': different_rows,
            'matching_rows': matching_rows,
            'source_data': source_data,
            'target_data': target_data
        }
    
    def _process_table_chunk(self, args: Tuple) -> Dict:
        """Process a single table chunk comparison"""
        source_db_config, target_db_config, table_config, chunk_index = args
        
        try:
            # Get source chunk
            source_chunks = self._get_table_hash_chunks(source_db_config, table_config)
            target_chunks = self._get_table_hash_chunks(target_db_config, table_config)
            
            if chunk_index >= len(source_chunks) and chunk_index >= len(target_chunks):
                return None
            
            source_chunk = source_chunks[chunk_index] if chunk_index < len(source_chunks) else {'data': {}}
            target_chunk = target_chunks[chunk_index] if chunk_index < len(target_chunks) else {'data': {}}
            
            return self._compare_chunks(source_chunk, target_chunk)
            
        except Exception as e:
            return {'error': str(e)}
    
    def compare_table(self, table_config: TableConfig) -> ComparisonResult:
        """Compare a single table between source and target databases with restart capability"""
        table_name = table_config.table_name
        
        # Get or create run ID from state
        table_progress = self.state_manager.current_state.table_progress[table_name]
        run_id = table_progress.run_id
        
        # Check if table is already completed
        if table_progress.status == 'completed':
            self.audit_logger.logger.info(f"Table {table_name} already completed, skipping")
            return self._create_result_from_progress(table_progress)
        
        start_time = datetime.now()
        self.state_manager.update_table_progress(table_name, status='in_progress')
        self.audit_logger.log_comparison_start(run_id, table_name)
        
        try:
            # Load existing chunk checkpoints
            existing_checkpoints = self.checkpoint_manager.load_chunk_checkpoints(
                self.session_id, table_name
            )
            
            if existing_checkpoints:
                self.audit_logger.logger.info(
                    f"Found {len(existing_checkpoints)} existing chunk checkpoints for {table_name}"
                )
            
            # Get table data in chunks
            source_chunks = self._get_table_hash_chunks(self.source_db, table_config, existing_checkpoints)
            target_chunks = self._get_table_hash_chunks(self.target_db, table_config, existing_checkpoints)
            
            total_source_rows = sum(chunk['size'] for chunk in source_chunks)
            total_target_rows = sum(chunk['size'] for chunk in target_chunks)
            
            # Update progress with total chunks
            max_chunks = max(len(source_chunks), len(target_chunks))
            self.state_manager.update_table_progress(
                table_name,
                total_chunks=max_chunks,
                processed_rows=0
            )
            
            # Compare chunks (with restart capability)
            all_missing_in_target = set()
            all_missing_in_source = set()
            all_different_rows = set()
            all_matching_rows = set()
            differences_for_sql = []
            
            completed_chunks = len(existing_checkpoints)
            
            # Process remaining chunks
            for i in range(max_chunks):
                # Skip if chunk already processed
                if i in existing_checkpoints:
                    comparison = existing_checkpoints[i]
                    self.audit_logger.logger.info(f"Skipping chunk {i+1}/{max_chunks} (already processed)")
                else:
                    # Process new chunk
                    source_chunk = source_chunks[i] if i < len(source_chunks) else {'data': {}}
                    target_chunk = target_chunks[i] if i < len(target_chunks) else {'data': {}}
                    
                    comparison = self._compare_chunks(source_chunk, target_chunk)
                    
                    # Save checkpoint
                    self.checkpoint_manager.save_chunk_checkpoint(
                        self.session_id, table_name, i, comparison
                    )
                    
                    completed_chunks += 1
                    self.audit_logger.logger.info(f"Processed chunk {i+1}/{max_chunks}")
                
                # Aggregate results
                all_missing_in_target.update(comparison['missing_in_target'])
                all_missing_in_source.update(comparison['missing_in_source'])
                all_different_rows.update(comparison['different_rows'])
                all_matching_rows.update(comparison['matching_rows'])
                differences_for_sql.append(comparison)
                
                # Update progress
                self.state_manager.update_table_progress(
                    table_name,
                    completed_chunks=completed_chunks,
                    processed_rows=min(total_source_rows, (i + 1) * table_config.chunk_size)
                )
                
                # Add small delay to prevent overwhelming the database
                if i > 0 and i % 10 == 0:
                    time.sleep(0.1)
            
            # Generate SQL files if there are differences
            if all_missing_in_target or all_missing_in_source or all_different_rows:
                self._generate_sql_files(table_config, differences_for_sql, run_id)
            
            # Create result
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = ComparisonResult(
                table_name=table_config.table_name,
                total_source_rows=total_source_rows,
                total_target_rows=total_target_rows,
                matching_rows=len(all_matching_rows),
                missing_in_target=len(all_missing_in_target),
                missing_in_source=len(all_missing_in_source),
                different_rows=len(all_different_rows),
                execution_time=execution_time,
                chunks_processed=max_chunks
            )
            
            # Mark table as completed
            self.state_manager.mark_table_completed(table_name, result)
            self.audit_logger.log_comparison_end(run_id, table_name, result)
            
            # Clean up checkpoints for completed table
            self.checkpoint_manager.cleanup_table_checkpoints(self.session_id, table_name)
            
            return result
            
        except Exception as e:
            error_msg = str(e)
            self.state_manager.mark_table_failed(table_name, error_msg)
            self.audit_logger.log_error(run_id, table_name, error_msg)
            raise
    
    def _create_result_from_progress(self, progress: TableProgress) -> ComparisonResult:
        """Create a ComparisonResult from saved progress (for already completed tables)"""
        return ComparisonResult(
            table_name=progress.table_name,
            total_source_rows=progress.processed_rows,
            total_target_rows=progress.processed_rows,  # Approximate
            matching_rows=0,  # Would need to be saved in progress
            missing_in_target=0,
            missing_in_source=0,
            different_rows=0,
            execution_time=0.0,
            chunks_processed=progress.completed_chunks
        )
    
    def _generate_sql_files(self, table_config: TableConfig, differences: List[Dict], run_id: str):
        """Generate SQL files for synchronization"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # SQL for target database (insert missing, update different)
        target_sql_file = f"sync_target_{table_config.table_name}_{timestamp}_{run_id[:8]}.sql"
        source_sql_file = f"sync_source_{table_config.table_name}_{timestamp}_{run_id[:8]}.sql"
        
        with open(target_sql_file, 'w') as target_file, open(source_sql_file, 'w') as source_file:
            target_file.write(f"-- SQL to sync TARGET database for table {table_config.table_name}\n")
            target_file.write(f"-- Generated on {datetime.now()}\n")
            target_file.write(f"-- Run ID: {run_id}\n\n")
            
            source_file.write(f"-- SQL to sync SOURCE database for table {table_config.table_name}\n")
            source_file.write(f"-- Generated on {datetime.now()}\n")
            source_file.write(f"-- Run ID: {run_id}\n\n")
            
            for diff in differences:
                # Insert missing rows in target
                for pk_key in diff['missing_in_target']:
                    if pk_key in diff['source_data']:
                        row_data = diff['source_data'][pk_key]['data']
                        columns = list(row_data.keys())
                        values = [f"'{v}'" if isinstance(v, str) else str(v) if v is not None else 'NULL' 
                                 for v in row_data.values()]
                        
                        target_file.write(f"INSERT INTO {table_config.table_name} ({', '.join(columns)}) ")
                        target_file.write(f"VALUES ({', '.join(values)});\n")
                
                # Insert missing rows in source
                for pk_key in diff['missing_in_source']:
                    if pk_key in diff['target_data']:
                        row_data = diff['target_data'][pk_key]['data']
                        columns = list(row_data.keys())
                        values = [f"'{v}'" if isinstance(v, str) else str(v) if v is not None else 'NULL' 
                                 for v in row_data.values()]
                        
                        source_file.write(f"INSERT INTO {table_config.table_name} ({', '.join(columns)}) ")
                        source_file.write(f"VALUES ({', '.join(values)});\n")
                
                # Update different rows (using source as master)
                for pk_key in diff['different_rows']:
                    if pk_key in diff['source_data']:
                        source_row = diff['source_data'][pk_key]['data']
                        pk_values = pk_key.split('|')
                        
                        set_clause = []
                        where_clause = []
                        
                        for i, pk_col in enumerate(table_config.primary_key):
                            where_clause.append(f"{pk_col} = '{pk_values[i]}'")
                        
                        for col, val in source_row.items():
                            if col not in table_config.primary_key:
                                val_str = f"'{val}'" if isinstance(val, str) else str(val) if val is not None else 'NULL'
                                set_clause.append(f"{col} = {val_str}")
                        
                        if set_clause:
                            target_file.write(f"UPDATE {table_config.table_name} SET {', '.join(set_clause)} ")
                            target_file.write(f"WHERE {' AND '.join(where_clause)};\n")
            
            target_file.write("\nCOMMIT;\n")
            source_file.write("\nCOMMIT;\n")
        
        self.audit_logger.logger.info(f"Generated SQL files: {target_sql_file}, {source_sql_file}")
    
    def compare_all_tables(self) -> List[ComparisonResult]:
        """Compare all configured tables"""
        results = []
        
        for table_config in self.tables:
            try:
                result = self.compare_table(table_config)
                results.append(result)
                
                # Generate summary report
                self._generate_summary_report(result)
                
            except Exception as e:
                self.audit_logger.logger.error(f"Failed to compare table {table_config.table_name}: {str(e)}")
        
        return results
    
    def _generate_summary_report(self, result: ComparisonResult):
        """Generate summary report"""
        report_file = f"comparison_summary_{result.table_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        with open(report_file, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"TABLE COMPARISON SUMMARY: {result.table_name}\n")
            f.write("="*80 + "\n")
            f.write(f"Generated on: {datetime.now()}\n\n")
            
            f.write(f"Total rows in source: {result.total_source_rows:,}\n")
            f.write(f"Total rows in target: {result.total_target_rows:,}\n")
            f.write(f"Matching rows: {result.matching_rows:,}\n")
            f.write(f"Rows missing in target: {result.missing_in_target:,}\n")
            f.write(f"Rows missing in source: {result.missing_in_source:,}\n")
            f.write(f"Different rows: {result.different_rows:,}\n")
            f.write(f"Execution time: {result.execution_time:.2f} seconds\n")
            f.write(f"Chunks processed: {result.chunks_processed}\n\n")
            
            if result.missing_in_target + result.missing_in_source + result.different_rows == 0:
                f.write("✓ TABLES ARE IN SYNC\n")
            else:
                f.write("✗ TABLES ARE NOT IN SYNC\n")
                f.write("SQL files have been generated for synchronization.\n")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Oracle Table Comparison Tool')
    parser.add_argument('config', help='Configuration file path (YAML)')
    parser.add_argument('--table', help='Compare specific table only')
    
    args = parser.parse_args()
    
    try:
        comparator = OracleTableComparator(args.config)
        
        if args.table:
            # Find specific table
            table_config = None
            for table in comparator.tables:
                if table.table_name.upper() == args.table.upper():
                    table_config = table
                    break
            
            if not table_config:
                print(f"Table {args.table} not found in configuration")
                sys.exit(1)
            
            result = comparator.compare_table(table_config)
            print(f"\nComparison completed for table {result.table_name}")
        else:
            results = comparator.compare_all_tables()
            print(f"\nComparison completed for {len(results)} tables")
        
        print("Check audit logs and generated files for detailed results.")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
