#!/usr/bin/env python3
"""
Oracle Table Comparison Tool with Restart Capability
A scalable, configurable tool to compare Oracle tables across different databases
with comprehensive restart/resume functionality and schema support.
"""

import oracledb
import hashlib
import json
import logging
import multiprocessing as mp
import os
import sys
import pickle
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import yaml
import argparse
from dataclasses import dataclass, asdict
import uuid
from pathlib import Path


@dataclass
class DatabaseConfig:
    """Oracle database configuration class"""
    host: str
    port: int
    service_name: str
    username: str
    password: str
    
    def get_dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"
    
    def get_connection(self):
        """Get Oracle database connection"""
        return oracledb.connect(
            user=self.username,
            password=self.password,
            dsn=self.get_dsn()
        )


@dataclass
class TableConfig:
    """Table configuration class with schema support"""
    schema_name: str
    table_name: str
    primary_key: List[str]
    columns: Optional[List[str]] = None
    where_clause: Optional[str] = None
    chunk_size: int = 10000
    
    def get_full_table_name(self) -> str:
        """Get schema.table_name format"""
        return f"{self.schema_name}.{self.table_name}"
    
    def get_unique_identifier(self) -> str:
        """Get unique identifier for restart logic"""
        return f"{self.schema_name}_{self.table_name}"


@dataclass
class ComparisonResult:
    """Comparison result class"""
    schema_name: str
    table_name: str
    total_source_rows: int
    total_target_rows: int
    matching_rows: int
    missing_in_target: int
    missing_in_source: int
    different_rows: int
    execution_time: float
    chunks_processed: int
    
    def get_full_table_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass
class TableProgress:
    """Track progress for each table with schema support"""
    schema_name: str
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
    
    def get_unique_identifier(self) -> str:
        return f"{self.schema_name}_{self.table_name}"
    
    def get_full_table_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass
class ComparisonState:
    """Overall comparison state for restart capability"""
    session_id: str
    config_file: str
    start_time: datetime
    last_update: datetime
    total_tables: int
    completed_tables: int
    table_progress: Dict[str, TableProgress]  # Key: schema_table unique identifier
    current_table: str = None
    status: str = 'started'  # 'started', 'in_progress', 'completed', 'failed'


class StateManager:
    """Manages comparison state for restart capability with schema support"""
    
    def __init__(self, state_dir: str = "oracle_comparison_state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(exist_ok=True)
        self.state_file = None
        self.current_state = None
        
    def create_new_session(self, config_file: str, tables: List[TableConfig]) -> str:
        """Create a new comparison session"""
        session_id = str(uuid.uuid4())
        self.state_file = self.state_dir / f"oracle_session_{session_id}.json"
        
        table_progress = {}
        for table in tables:
            unique_id = table.get_unique_identifier()
            table_progress[unique_id] = TableProgress(
                schema_name=table.schema_name,
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
        self.state_file = self.state_dir / f"oracle_session_{session_id}.json"
        
        if not self.state_file.exists():
            return False
            
        try:
            with open(self.state_file, 'r') as f:
                state_data = json.load(f)
            
            # Reconstruct objects from JSON
            table_progress = {}
            for table_id, progress_data in state_data['table_progress'].items():
                # Convert datetime strings back to datetime objects
                progress_data['start_time'] = datetime.fromisoformat(progress_data['start_time'])
                progress_data['last_update'] = datetime.fromisoformat(progress_data['last_update'])
                table_progress[table_id] = TableProgress(**progress_data)
            
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
            
            for table_id, progress in state_dict['table_progress'].items():
                progress['start_time'] = progress['start_time'].isoformat()
                progress['last_update'] = datetime.now().isoformat()
            
            with open(self.state_file, 'w') as f:
                json.dump(state_dict, f, indent=2, default=str)
                
        except Exception as e:
            print(f"Error saving state: {e}")
    
    def update_table_progress(self, table_unique_id: str, **kwargs):
        """Update progress for a specific table using unique identifier"""
        if self.current_state and table_unique_id in self.current_state.table_progress:
            progress = self.current_state.table_progress[table_unique_id]
            for key, value in kwargs.items():
                if hasattr(progress, key):
                    setattr(progress, key, value)
            progress.last_update = datetime.now()
            self.current_state.last_update = datetime.now()
            self.save_state()
    
    def mark_table_completed(self, table_unique_id: str, result: ComparisonResult):
        """Mark a table as completed"""
        if self.current_state:
            self.update_table_progress(
                table_unique_id,
                status='completed',
                processed_rows=result.total_source_rows
            )
            self.current_state.completed_tables += 1
            self.save_state()
    
    def mark_table_failed(self, table_unique_id: str, error: str):
        """Mark a table as failed"""
        if self.current_state:
            self.update_table_progress(
                table_unique_id,
                status='failed',
                error_message=error
            )
            self.save_state()
    
    def get_pending_tables(self) -> List[str]:
        """Get list of tables that haven't been completed"""
        if not self.current_state:
            return []
            
        pending = []
        for table_id, progress in self.current_state.table_progress.items():
            if progress.status in ['pending', 'in_progress', 'failed']:
                pending.append(table_id)
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
            'last_update': self.current_state.last_update,
            'table_details': [
                {
                    'schema_table': f"{p.schema_name}.{p.table_name}",
                    'status': p.status,
                    'chunks': f"{p.completed_chunks}/{p.total_chunks}",
                    'error': p.error_message
                }
                for p in self.current_state.table_progress.values()
            ]
        }
    
    def cleanup_old_sessions(self, days_old: int = 7):
        """Clean up old session files"""
        cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
        
        for state_file in self.state_dir.glob("oracle_session_*.json"):
            if state_file.stat().st_mtime < cutoff_time:
                try:
                    state_file.unlink()
                    print(f"Cleaned up old session file: {state_file.name}")
                except Exception as e:
                    print(f"Error cleaning up {state_file.name}: {e}")


class CheckpointManager:
    """Manages checkpoints for chunk-level recovery with schema support"""
    
    def __init__(self, checkpoint_dir: str = "oracle_checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
    
    def save_chunk_checkpoint(self, session_id: str, table_unique_id: str, 
                            chunk_index: int, chunk_result: Dict):
        """Save checkpoint for a completed chunk"""
        checkpoint_file = (self.checkpoint_dir / 
                          f"{session_id}_{table_unique_id}_chunk_{chunk_index}.pkl")
        
        try:
            with open(checkpoint_file, 'wb') as f:
                pickle.dump({
                    'chunk_index': chunk_index,
                    'timestamp': datetime.now(),
                    'result': chunk_result
                }, f)
        except Exception as e:
            print(f"Error saving chunk checkpoint: {e}")
    
    def load_chunk_checkpoints(self, session_id: str, table_unique_id: str) -> Dict[int, Dict]:
        """Load all chunk checkpoints for a table"""
        checkpoints = {}
        pattern = f"{session_id}_{table_unique_id}_chunk_*.pkl"
        
        for checkpoint_file in self.checkpoint_dir.glob(pattern):
            try:
                with open(checkpoint_file, 'rb') as f:
                    data = pickle.load(f)
                    checkpoints[data['chunk_index']] = data['result']
            except Exception as e:
                print(f"Error loading checkpoint {checkpoint_file}: {e}")
        
        return checkpoints
    
    def cleanup_table_checkpoints(self, session_id: str, table_unique_id: str):
        """Clean up checkpoints for a completed table"""
        pattern = f"{session_id}_{table_unique_id}_chunk_*.pkl"
        
        for checkpoint_file in self.checkpoint_dir.glob(pattern):
            try:
                checkpoint_file.unlink()
            except Exception as e:
                print(f"Error cleaning up checkpoint {checkpoint_file}: {e}")


class AuditLogger:
    """Handles audit logging to file and database"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.audit_file = config.get('audit_file', 'oracle_table_comparison_audit.log')
        self.setup_logging()
        
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
    
    def log_comparison_start(self, run_id: str, schema_table: str):
        """Log comparison start"""
        message = f"COMPARISON_START - Run ID: {run_id}, Table: {schema_table}"
        self.logger.info(message)
    
    def log_comparison_end(self, run_id: str, schema_table: str, result: ComparisonResult):
        """Log comparison end with results"""
        message = (f"COMPARISON_END - Run ID: {run_id}, Table: {schema_table}, "
                  f"Results: {result.__dict__}")
        self.logger.info(message)
    
    def log_error(self, run_id: str, schema_table: str, error: str):
        """Log errors"""
        message = f"ERROR - Run ID: {run_id}, Table: {schema_table}, Error: {error}"
        self.logger.error(message)


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
                self.audit_logger.logger.info(f"Resumed Oracle session: {session_id}")
                self._print_session_summary()
            else:
                raise ValueError(f"Oracle session {session_id} not found or corrupted")
        else:
            self.session_id = self.state_manager.create_new_session(config_file, self.tables)
            self.audit_logger.logger.info(f"Created new Oracle session: {self.session_id}")
    
    def _print_session_summary(self):
        """Print current session summary"""
        summary = self.state_manager.get_session_summary()
        print("\n" + "="*80)
        print(f"ORACLE COMPARISON SESSION: {summary['session_id'][:8]}...")
        print("="*80)
        print(f"Total Tables: {summary['total_tables']}")
        print(f"Completed: {summary['completed']}")
        print(f"Failed: {summary['failed']}")
        print(f"In Progress: {summary['in_progress']}")
        print(f"Pending: {summary['pending']}")
        print(f"Started: {summary['start_time']}")
        print(f"Last Update: {summary['last_update']}")
        print("\nTable Details:")
        print("-" * 80)
        for detail in summary['table_details']:
            status_icon = {"completed": "✅", "failed": "❌", "in_progress": "🔄", "pending": "⏳"}
            icon = status_icon.get(detail['status'], "❓")
            print(f"{icon} {detail['schema_table']:<30} {detail['status']:<12} {detail['chunks']}")
            if detail['error']:
                print(f"   Error: {detail['error']}")
        print("="*80 + "\n")
    
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(config_file, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise Exception(f"Failed to load config file {config_file}: {str(e)}")
    
    def _get_table_columns(self, db_config: DatabaseConfig, schema_name: str, table_name: str) -> List[str]:
        """Get all columns for a table"""
        connection = db_config.get_connection()
        cursor = connection.cursor()
        
        try:
            cursor.execute("""
                SELECT column_name 
                FROM all_tab_columns 
                WHERE owner = UPPER(:1) AND table_name = UPPER(:2)
                ORDER BY column_id
            """, (schema_name, table_name))
            
            return [row[0] for row in cursor.fetchall()]
            
        finally:
            cursor.close()
            connection.close()
    
    def _get_table_hash_chunks(self, db_config: DatabaseConfig, table_config: TableConfig, 
                              existing_checkpoints: Dict = None) -> List[Dict]:
        """Get table data in chunks with hash values (skip if checkpoint exists)"""
        if existing_checkpoints is None:
            existing_checkpoints = {}
            
        connection = db_config.get_connection()
        cursor = connection.cursor()
        
        try:
            # Build column list
            columns = table_config.columns or self._get_table_columns(
                db_config, table_config.schema_name, table_config.table_name
            )
            
            # Get total count
            full_table_name = table_config.get_full_table_name()
            where_clause = f"WHERE {table_config.where_clause}" if table_config.where_clause else ""
            count_sql = f"SELECT COUNT(*) FROM {full_table_name} {where_clause}"
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
                
                # Query with Oracle-specific pagination
                column_str = ', '.join(columns)
                pk_str = ', '.join(table_config.primary_key)
                
                query = f"""
                SELECT {column_str}
                FROM (
                    SELECT {column_str}, ROWNUM as rn
                    FROM (
                        SELECT {column_str}
                        FROM {full_table_name}
                        {where_clause}
                        ORDER BY {pk_str}
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
    
    def compare_table(self, table_config: TableConfig) -> ComparisonResult:
        """Compare a single table between source and target databases with restart capability"""
        table_unique_id = table_config.get_unique_identifier()
        full_table_name = table_config.get_full_table_name()
        
        # Get or create run ID from state
        table_progress = self.state_manager.current_state.table_progress[table_unique_id]
        run_id = table_progress.run_id
        
        # Check if table is already completed
        if table_progress.status == 'completed':
            self.audit_logger.logger.info(f"Table {full_table_name} already completed, skipping")
            return self._create_result_from_progress(table_progress)
        
        start_time = datetime.now()
        self.state_manager.update_table_progress(table_unique_id, status='in_progress')
        self.audit_logger.log_comparison_start(run_id, full_table_name)
        
        try:
            # Load existing chunk checkpoints
            existing_checkpoints = self.checkpoint_manager.load_chunk_checkpoints(
                self.session_id, table_unique_id
            )
            
            if existing_checkpoints:
                self.audit_logger.logger.info(
                    f"Found {len(existing_checkpoints)} existing chunk checkpoints for {full_table_name}"
                )
            
            # Get table metadata
            source_chunks = self._get_table_hash_chunks(self.source_db, table_config, existing_checkpoints)
            target_chunks = self._get_table_hash_chunks(self.target_db, table_config, existing_checkpoints)
            
            total_source_rows = sum(chunk['size'] for chunk in source_chunks if not chunk.get('from_checkpoint', False))
            total_target_rows = sum(chunk['size'] for chunk in target_chunks if not chunk.get('from_checkpoint', False))
            
            # Add rows from checkpoints
            for chunk_idx in existing_checkpoints:
                checkpoint_data = existing_checkpoints[chunk_idx]
                total_source_rows += len(checkpoint_data.get('source_data', {}))
                total_target_rows += len(checkpoint_data.get('target_data', {}))
            
            # Update progress with total chunks
            max_chunks = max(len(source_chunks), len(target_chunks))
            self.state_manager.update_table_progress(
                table_unique_id,
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
                        self.session_id, table_unique_id, i, comparison
                    )
                    
                    completed_chunks += 1
                    self.audit_logger.logger.info(f"Processed chunk {i+1}/{max_chunks} for {full_table_name}")
                
                # Aggregate results
                all_missing_in_target.update(comparison['missing_in_target'])
                all_missing_in_source.update(comparison['missing_in_source'])
                all_different_rows.update(comparison['different_rows'])
                all_matching_rows.update(comparison['matching_rows'])
                differences_for_sql.append(comparison)
                
                # Update progress
                self.state_manager.update_table_progress(
                    table_unique_id,
                    completed_chunks=completed_chunks,
                    processed_rows=min(total_source_rows, (i + 1) * table_config.chunk_size)
                )
                
                # Add small delay to prevent overwhelming the Oracle database
                if i > 0 and i % 10 == 0:
                    time.sleep(0.1)
            
            # Generate SQL files if there are differences
            if all_missing_in_target or all_missing_in_source or all_different_rows:
                self._generate_sql_files(table_config, differences_for_sql, run_id)
            
            # Create result
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = ComparisonResult(
                schema_name=table_config.schema_name,
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
            self.state_manager.mark_table_completed(table_unique_id, result)
            self.audit_logger.log_comparison_end(run_id, full_table_name, result)
            
            # Clean up checkpoints for completed table
            self.checkpoint_manager.cleanup_table_checkpoints(self.session_id, table_unique_id)
            
            return result
            
        except Exception as e:
            error_msg = str(e)
            self.state_manager.mark_table_failed(table_unique_id, error_msg)
            self.audit_logger.log_error(run_id, full_table_name, error_msg)
            raise
    
    def _create_result_from_progress(self, progress: TableProgress) -> ComparisonResult:
        """Create a ComparisonResult from saved progress (for already completed tables)"""
        return ComparisonResult(
            schema_name=progress.schema_name,
            table_name=progress.table_name,
            total_source_rows=progress.processed_rows,
            total_target_rows=progress.processed_rows,  # Approximate
            matching_rows=0,  # Would need to be saved in progress for exact numbers
            missing_in_target=0,
            missing_in_source=0,
            different_rows=0,
            execution_time=0.0,
            chunks_processed=progress.completed_chunks
        )
    
    def _generate_sql_files(self, table_config: TableConfig, differences: List[Dict], run_id: str):
        """Generate SQL files for synchronization"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        schema_table = table_config.get_unique_identifier()
        
        # SQL for target database (insert missing, update different)
        target_sql_file = f"sync_target_{schema_table}_{timestamp}_{run_id[:8]}.sql"
        source_sql_file = f"sync_source_{schema_table}_{timestamp}_{run_id[:8]}.sql"
        
        full_table_name = table_config.get_full_table_name()
        
        with open(target_sql_file, 'w') as target_file, open(source_sql_file, 'w') as source_file:
            target_file.write(f"-- SQL to sync TARGET Oracle database for table {full_table_name}\n")
            target_file.write(f"-- Generated on {datetime.now()}\n")
            target_file.write(f"-- Run ID: {run_id}\n\n")
            
            source_file.write(f"-- SQL to sync SOURCE Oracle database for table {full_table_name}\n")
            source_file.write(f"-- Generated on {datetime.now()}\n")
            source_file.write(f"-- Run ID: {run_id}\n\n")
            
            for diff in differences:
                # Insert missing rows in target
                for pk_key in diff['missing_in_target']:
                    if pk_key in diff['source_data']:
                        row_data = diff['source_data'][pk_key]['data']
                        self._write_oracle_insert_sql(target_file, full_table_name, row_data)
                
                # Insert missing rows in source
                for pk_key in diff['missing_in_source']:
                    if pk_key in diff['target_data']:
                        row_data = diff['target_data'][pk_key]['data']
                        self._write_oracle_insert_sql(source_file, full_table_name, row_data)
                
                # Update different rows (using source as master)
                for pk_key in diff['different_rows']:
                    if pk_key in diff['source_data']:
                        source_row = diff['source_data'][pk_key]['data']
                        self._write_oracle_update_sql(target_file, table_config, source_row, pk_key)
            
            target_file.write("\nCOMMIT;\n")
            source_file.write("\nCOMMIT;\n")
        
        self.audit_logger.logger.info(f"Generated SQL files: {target_sql_file}, {source_sql_file}")
    
    def _write_oracle_insert_sql(self, file, table_name: str, row_data: Dict):
        """Write Oracle INSERT SQL statement"""
        columns = list(row_data.keys())
        values = []
        
        for val in row_data.values():
            if val is None:
                values.append('NULL')
            elif isinstance(val, str):
                # Escape single quotes for Oracle
                escaped_val = val.replace("'", "''")
                values.append(f"'{escaped_val}'")
            elif isinstance(val, datetime):
                # Oracle date format
                values.append(f"TO_DATE('{val.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')")
            else:
                values.append(str(val))
        
        file.write(f"INSERT INTO {table_name} ({', '.join(columns)}) ")
        file.write(f"VALUES ({', '.join(values)});\n")
    
    def _write_oracle_update_sql(self, file, table_config: TableConfig, row_data: Dict, pk_key: str):
        """Write Oracle UPDATE SQL statement"""
        pk_values = pk_key.split('|')
        full_table_name = table_config.get_full_table_name()
        
        set_clause = []
        where_clause = []
        
        # Build WHERE clause from primary key
        for i, pk_col in enumerate(table_config.primary_key):
            where_clause.append(f"{pk_col} = '{pk_values[i]}'")
        
        # Build SET clause for non-primary key columns
        for col, val in row_data.items():
            if col not in table_config.primary_key:
                if val is None:
                    val_str = 'NULL'
                elif isinstance(val, str):
                    escaped_val = val.replace("'", "''")
                    val_str = f"'{escaped_val}'"
                elif isinstance(val, datetime):
                    val_str = f"TO_DATE('{val.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')"
                else:
                    val_str = str(val)
                set_clause.append(f"{col} = {val_str}")
        
        if set_clause:
            file.write(f"UPDATE {full_table_name} SET {', '.join(set_clause)} ")
            file.write(f"WHERE {' AND '.join(where_clause)};\n")
    
    def compare_all_tables(self) -> List[ComparisonResult]:
        """Compare all configured tables with restart capability"""
        results = []
        
        # Get list of pending tables (not completed)
        pending_table_ids = self.state_manager.get_pending_tables()
        
        if not pending_table_ids:
            self.audit_logger.logger.info("All tables already completed")
            return results
        
        self.audit_logger.logger.info(f"Processing {len(pending_table_ids)} pending tables")
        
        for table_config in self.tables:
            table_unique_id = table_config.get_unique_identifier()
            
            if table_unique_id not in pending_table_ids:
                self.audit_logger.logger.info(f"Skipping completed table: {table_config.get_full_table_name()}")
                continue
            
            try:
                self.audit_logger.logger.info(f"Starting comparison for table: {table_config.get_full_table_name()}")
                result = self.compare_table(table_config)
                results.append(result)
                
                # Generate summary report
                self._generate_summary_report(result)
                
                self.audit_logger.logger.info(f"Completed table: {table_config.get_full_table_name()}")
                
            except KeyboardInterrupt:
                self.audit_logger.logger.warning(f"Interrupted during table {table_config.get_full_table_name()}")
                print(f"\n⚠️  Oracle comparison interrupted. Session saved: {self.session_id}")
                print(f"Resume with: python {sys.argv[0]} {self.state_manager.current_state.config_file} --resume {self.session_id}")
                raise
                
            except Exception as e:
                self.audit_logger.logger.error(f"Failed to compare table {table_config.get_full_table_name()}: {str(e)}")
                # Continue with next table rather than stopping completely
                continue
        
        # Mark session as completed if all tables are done
        remaining_pending = self.state_manager.get_pending_tables()
        if not remaining_pending:
            self.state_manager.current_state.status = 'completed'
            self.state_manager.save_state()
            self.audit_logger.logger.info("All Oracle tables completed successfully")
            print(f"\n🎉 All tables completed successfully! Session: {self.session_id}")
        
        return results
    
    def get_session_status(self) -> Dict:
        """Get current session status"""
        return self.state_manager.get_session_summary()
    
    def list_failed_tables(self) -> List[str]:
        """Get list of failed tables that can be retried"""
        if not self.state_manager.current_state:
            return []
        
        failed_tables = []
        for table_id, progress in self.state_manager.current_state.table_progress.items():
            if progress.status == 'failed':
                failed_tables.append(f"{progress.schema_name}.{progress.table_name}")
        
        return failed_tables
    
    def retry_failed_tables(self) -> List[ComparisonResult]:
        """Retry only the failed tables"""
        if not self.state_manager.current_state:
            return []
        
        # Reset failed tables to pending
        for table_id, progress in self.state_manager.current_state.table_progress.items():
            if progress.status == 'failed':
                progress.status = 'pending'
                progress.error_message = None
        
        self.state_manager.save_state()
        
        # Run comparison on failed tables
        return self.compare_all_tables()
    
    def _generate_summary_report(self, result: ComparisonResult):
        """Generate summary report"""
        schema_table = f"{result.schema_name}_{result.table_name}"
        report_file = f"oracle_comparison_summary_{schema_table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        with open(report_file, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"ORACLE TABLE COMPARISON SUMMARY: {result.get_full_table_name()}\n")
            f.write("="*80 + "\n")
            f.write(f"Generated on: {datetime.now()}\n")
            f.write(f"Source DB: {self.source_db.host}:{self.source_db.port}/{self.source_db.service_name}\n")
            f.write(f"Target DB: {self.target_db.host}:{self.target_db.port}/{self.target_db.service_name}\n")
            f.write(f"Session ID: {self.session_id}\n\n")
            
            f.write(f"Total rows in source: {result.total_source_rows:,}\n")
            f.write(f"Total rows in target: {result.total_target_rows:,}\n")
            f.write(f"Matching rows: {result.matching_rows:,}\n")
            f.write(f"Rows missing in target: {result.missing_in_target:,}\n")
            f.write(f"Rows missing in source: {result.missing_in_source:,}\n")
            f.write(f"Different rows: {result.different_rows:,}\n")
            f.write(f"Execution time: {result.execution_time:.2f} seconds\n")
            f.write(f"Chunks processed: {result.chunks_processed}\n\n")
            
            if result.missing_in_target + result.missing_in_source + result.different_rows == 0:
                f.write("✓ ORACLE TABLES ARE IN SYNC\n")
            else:
                f.write("✗ ORACLE TABLES ARE NOT IN SYNC\n")
                f.write("SQL files have been generated for synchronization.\n")
                
                # Sync recommendations
                f.write(f"\nSynchronization Recommendations:\n")
                f.write(f"- Review generated SQL files before execution\n")
                f.write(f"- Test SQL scripts in a non-production environment first\n")
                f.write(f"- Consider running during maintenance windows\n")
                f.write(f"- Take backups before applying changes\n")


def list_sessions():
    """List all available sessions"""
    state_manager = StateManager()
    session_files = list(state_manager.state_dir.glob("oracle_session_*.json"))
    
    if not session_files:
        print("No Oracle comparison sessions found.")
        return
    
    print("\nAvailable Oracle Comparison Sessions:")
    print("="*80)
    
    for session_file in session_files:
        try:
            with open(session_file, 'r') as f:
                session_data = json.load(f)
            
            session_id = session_data['session_id']
            start_time = session_data['start_time']
            status = session_data.get('status', 'unknown')
            total_tables = session_data.get('total_tables', 0)
            completed_tables = session_data.get('completed_tables', 0)
            
            print(f"Session ID: {session_id}")
            print(f"  Started: {start_time}")
            print(f"  Status: {status}")
            print(f"  Progress: {completed_tables}/{total_tables} tables")
            print(f"  Config: {session_data.get('config_file', 'unknown')}")
            print("-" * 80)
            
        except Exception as e:
            print(f"Error reading session {session_file.name}: {e}")


def cleanup_sessions(days_old: int = 7):
    """Clean up old sessions"""
    state_manager = StateManager()
    checkpoint_manager = CheckpointManager()
    
    print(f"Cleaning up Oracle sessions older than {days_old} days...")
    state_manager.cleanup_old_sessions(days_old)
    
    # Also cleanup orphaned checkpoints
    cutoff_time = datetime.now().timestamp() - (days_old * 24 * 3600)
    
    for checkpoint_file in checkpoint_manager.checkpoint_dir.glob("*.pkl"):
        if checkpoint_file.stat().st_mtime < cutoff_time:
            try:
                checkpoint_file.unlink()
                print(f"Cleaned up old checkpoint: {checkpoint_file.name}")
            except Exception as e:
                print(f"Error cleaning up {checkpoint_file.name}: {e}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Oracle Table Comparison Tool with Restart Capability')
    parser.add_argument('config', help='Configuration file path (YAML)')
    parser.add_argument('--table', help='Compare specific table only (format: SCHEMA.TABLE)')
    parser.add_argument('--resume', help='Resume from existing session ID')
    parser.add_argument('--list-sessions', action='store_true', help='List all available sessions')
    parser.add_argument('--cleanup', type=int, metavar='DAYS', help='Clean up sessions older than DAYS')
    parser.add_argument('--retry-failed', action='store_true', help='Retry only failed tables from resumed session')
    parser.add_argument('--status', help='Show status of specific session ID')
    
    args = parser.parse_args()
    
    # Handle utility commands
    if args.list_sessions:
        list_sessions()
        return
    
    if args.cleanup:
        cleanup_sessions(args.cleanup)
        return
    
    if args.status:
        state_manager = StateManager()
        if state_manager.load_session(args.status):
            summary = state_manager.get_session_summary()
            print(json.dumps(summary, indent=2, default=str))
        else:
            print(f"Session {args.status} not found")
        return
    
    # Main comparison logic
    try:
        comparator = OracleTableComparator(args.config, args.resume)
        
        if args.retry_failed:
            if not args.resume:
                print("--retry-failed requires --resume <session_id>")
                sys.exit(1)
            
            failed_tables = comparator.list_failed_tables()
            if failed_tables:
                print(f"Retrying {len(failed_tables)} failed tables: {', '.join(failed_tables)}")
                results = comparator.retry_failed_tables()
            else:
                print("No failed tables found to retry")
                return
        
        elif args.table:
            # Find specific table
            schema_table = args.table.upper()
            if '.' not in schema_table:
                print("Table must be specified as SCHEMA.TABLE")
                sys.exit(1)
            
            schema_name, table_name = schema_table.split('.', 1)
            
            table_config = None
            for table in comparator.tables:
                if (table.schema_name.upper() == schema_name and 
                    table.table_name.upper() == table_name):
                    table_config = table
                    break
            
            if not table_config:
                print(f"Table {schema_table} not found in configuration")
                sys.exit(1)
            
            result = comparator.compare_table(table_config)
            print(f"\nComparison completed for table {result.get_full_table_name()}")
        else:
            results = comparator.compare_all_tables()
            print(f"\nComparison completed for {len(results)} tables")
        
        print(f"Session ID: {comparator.session_id}")
        print("Check audit logs and generated files for detailed results.")
        
    except KeyboardInterrupt:
        print("\n⚠️  Operation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()