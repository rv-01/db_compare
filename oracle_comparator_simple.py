def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Oracle Table Comparator - Simple Version')
    parser.add_argument('config', help='Configuration file path (YAML)')
    parser.add_argument('--table', help='Compare specific table only (format: SCHEMA.TABLE)')
    parser.add_argument('--no-progress', action='store_true', help='Disable progress bars')
    parser.add_argument('--json-output', action='store_true', help='Output results in JSON format')
    parser.add_argument('--debug', action='store_true', help='Enable debug output for troubleshooting')
    
    args = parser.parse_args()
    
    try:
        show_progress = not args.no_progress
        comparator = OracleTableComparatorSimple(args.config, show_progress)
        
        if args.table:
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
            
            result = comparator.compare_table(table_config, debug=args.debug)
            results = [result]
        else:
            results = comparator.compare_all_tables()
        
        # Output results
        if args.json_output:
            output = {
                'comparison_summary': {
                    'total_tables': len(results),
                    'completion_time': datetime.now().isoformat(),
                    'source_db': f"{comparator.source_db.host}:{comparator.source_db.port}",
                    'target_db': f"{comparator.target_db.host}:{comparator.target_db.port}"
                },
                'results': [
                    {
                        'table': result.get_full_table_name(),
                        'total_source_rows': result.total_source_rows,
                        'total_target_rows': result.total_target_rows,
                        'matching_rows': result.matching_rows,
                        'missing_in_target': result.missing_in_target,
                        'missing_in_source': result.missing_in_source,
                        'different_rows': result.different_rows,
                        'execution_time': result.execution_time,
                        'in_sync': (result.missing_in_target + result#!/usr/bin/env python3
"""
Oracle Table Comparator - Simple Version
Fast, lightweight Oracle table comparison without restart capability
Optimized for quick comparisons and CI/CD pipelines
"""

import oracledb
import hashlib
import json
import logging
import os
import sys
import time
import getpass
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import yaml
import argparse
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import threading


@dataclass
class DatabaseConfig:
    """Oracle database configuration with secure credential handling"""
    host: str
    port: int
    service_name: str
    username: str = None
    password: str = None
    credential_file: str = None
    prompt_credentials: bool = False
    
    def __post_init__(self):
        self._resolve_credentials()
    
    def _resolve_credentials(self):
        """Resolve credentials from various secure sources"""
        if self.prompt_credentials:
            self._prompt_for_credentials()
        elif self.credential_file:
            self._load_from_credential_file()
        else:
            self._resolve_environment_variables()
    
    def _prompt_for_credentials(self):
        """Interactively prompt for credentials"""
        if not self.username:
            self.username = input(f"Username for {self.host}: ")
        if not self.password:
            self.password = getpass.getpass(f"Password for {self.username}@{self.host}: ")
    
    def _load_from_credential_file(self):
        """Load credentials from external JSON file"""
        try:
            with open(self.credential_file, 'r') as f:
                creds = json.load(f)
                self.username = creds.get('username')
                self.password = creds.get('password')
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise ValueError(f"Error loading credential file {self.credential_file}: {e}")
    
    def _resolve_environment_variables(self):
        """Resolve username and password from environment variables"""
        if self.username and self.username.startswith('${'):
            env_var = self.username[2:-1]
            self.username = os.getenv(env_var)
            if not self.username:
                raise ValueError(f"Environment variable {env_var} not set")
        
        if self.password and self.password.startswith('${'):
            env_var = self.password[2:-1]
            self.password = os.getenv(env_var)
            if not self.password:
                raise ValueError(f"Environment variable {env_var} not set")
    
    def get_dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"
    
    def get_connection(self):
        """Get Oracle database connection"""
        if not self.username or not self.password:
            raise ValueError("Username and password required for database connection")
        
        return oracledb.connect(
            user=self.username,
            password=self.password,
            dsn=self.get_dsn()
        )


@dataclass
class TableConfig:
    """Table configuration"""
    schema_name: str
    table_name: str
    primary_key: List[str]
    columns: Optional[List[str]] = None
    where_clause: Optional[str] = None
    chunk_size: int = 10000
    
    def get_full_table_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass
class ComparisonResult:
    """Comparison result"""
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


class SimpleAuditLogger:
    """Simple audit logging"""
    
    def __init__(self, audit_file: str = "oracle_comparison_simple.log"):
        self.audit_file = audit_file
        self.setup_logging()
    
    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.audit_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)


class OracleTableComparatorSimple:
    """Simple Oracle table comparator without restart capability"""
    
    def __init__(self, config_file: str, show_progress: bool = True):
        self.config = self._load_config(config_file)
        self.source_db = DatabaseConfig(**self.config['source_database'])
        self.target_db = DatabaseConfig(**self.config['target_database'])
        self.tables = [TableConfig(**table) for table in self.config['tables']]
        self.show_progress = show_progress
        
        # Setup logging
        audit_config = self.config.get('audit', {})
        audit_file = audit_config.get('audit_file', 'oracle_comparison_simple.log')
        self.audit_logger = SimpleAuditLogger(audit_file)
        
        # Column cache for performance
        self.column_cache = {}
        self.column_cache_lock = threading.Lock()
    
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(config_file, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise Exception(f"Failed to load config file {config_file}: {str(e)}")
    
    def _get_table_columns_optimized(self, db_config: DatabaseConfig, schema_name: str, table_name: str) -> List[str]:
        """Get table columns with caching"""
        cache_key = f"{db_config.host}_{schema_name}_{table_name}"
        
        with self.column_cache_lock:
            if cache_key in self.column_cache:
                return self.column_cache[cache_key]
        
        connection = db_config.get_connection()
        cursor = connection.cursor()
        
        try:
            cursor.execute("""
                SELECT /*+ FIRST_ROWS */ column_name 
                FROM all_tab_columns 
                WHERE owner = UPPER(:1) AND table_name = UPPER(:2)
                ORDER BY column_id
            """, (schema_name, table_name))
            
            columns = [row[0] for row in cursor.fetchall()]
            
            if not columns:
                raise ValueError(f"Table {schema_name}.{table_name} not found")
            
            with self.column_cache_lock:
                self.column_cache[cache_key] = columns
            
            return columns
            
        finally:
            cursor.close()
            connection.close()
    
    def _bulk_fetch_metadata(self) -> Dict[str, Dict[str, List[str]]]:
        """Fetch all table metadata in parallel"""
        metadata = {'source': {}, 'target': {}}
        
        def fetch_for_db(db_config: DatabaseConfig, db_type: str):
            connection = db_config.get_connection()
            cursor = connection.cursor()
            
            try:
                # Get all schemas
                schemas = set(table.schema_name for table in self.tables)
                
                # Bulk query for all tables
                table_list = [(table.schema_name, table.table_name) for table in self.tables]
                placeholders = ", ".join([f"(UPPER('{schema}'), UPPER('{table}'))" 
                                        for schema, table in table_list])
                
                cursor.execute(f"""
                    SELECT /*+ FIRST_ROWS */ 
                        owner, table_name, column_name, column_id
                    FROM all_tab_columns 
                    WHERE (owner, table_name) IN ({placeholders})
                    ORDER BY owner, table_name, column_id
                """)
                
                current_table = None
                current_columns = []
                
                for owner, table_name, column_name, column_id in cursor.fetchall():
                    table_key = f"{owner}_{table_name}"
                    
                    if current_table != table_key:
                        if current_table and current_columns:
                            metadata[db_type][current_table] = current_columns
                        current_table = table_key
                        current_columns = []
                    
                    current_columns.append(column_name)
                
                if current_table and current_columns:
                    metadata[db_type][current_table] = current_columns
                    
            finally:
                cursor.close()
                connection.close()
        
        # Fetch in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            source_future = executor.submit(fetch_for_db, self.source_db, 'source')
            target_future = executor.submit(fetch_for_db, self.target_db, 'target')
            
            source_future.result()
            target_future.result()
        
        return metadata
    
    def _get_table_hash_chunks(self, db_config: DatabaseConfig, table_config: TableConfig, 
                              metadata: Dict = None) -> List[Dict]:
        """Extract table data in chunks with hashing"""
        connection = db_config.get_connection()
        cursor = connection.cursor()
        
        try:
            # Get columns
            if metadata:
                db_type = 'source' if db_config == self.source_db else 'target'
                table_key = f"{table_config.schema_name}_{table_config.table_name}"
                columns = metadata[db_type].get(table_key)
                if not columns:
                    columns = self._get_table_columns_optimized(db_config, table_config.schema_name, table_config.table_name)
            else:
                columns = table_config.columns or self._get_table_columns_optimized(
                    db_config, table_config.schema_name, table_config.table_name
                )
            
            # Get total count
            full_table_name = table_config.get_full_table_name()
            where_clause = f"WHERE {table_config.where_clause}" if table_config.where_clause else ""
            count_sql = f"SELECT /*+ FIRST_ROWS */ COUNT(*) FROM {full_table_name} {where_clause}"
            cursor.execute(count_sql)
            total_rows = cursor.fetchone()[0]
            
            chunks = []
            offset = 0
            
            # Progress bar for extraction
            extraction_pbar = None
            if self.show_progress:
                db_name = db_config.host.split('.')[0]
                extraction_pbar = tqdm(
                    total=total_rows,
                    desc=f"📥 {db_name}",
                    unit="rows",
                    leave=False,
                    colour="cyan"
                )
            
            while offset < total_rows:
                # Optimized pagination
                column_str = ', '.join(columns)
                pk_str = ', '.join(table_config.primary_key)
                
                query = f"""
                SELECT /*+ FIRST_ROWS({table_config.chunk_size}) */ {column_str}
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
                
                # Create hash data
                chunk_data = {}
                pk_indices = [columns.index(pk) for pk in table_config.primary_key]
                
                for row in rows:
                    pk_values = [str(row[idx]) for idx in pk_indices]
                    pk_key = '|'.join(pk_values)
                    
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
                
                if extraction_pbar:
                    extraction_pbar.update(len(rows))
                
                offset += table_config.chunk_size
            
            if extraction_pbar:
                extraction_pbar.close()
            
            return chunks
            
        finally:
            cursor.close()
            connection.close()
    
    def _compare_chunks(self, source_chunk: Dict, target_chunk: Dict) -> Dict:
        """Compare two chunks"""
        source_data = source_chunk.get('data', {})
        target_data = target_chunk.get('data', {})
        
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
    
    def compare_table(self, table_config: TableConfig, metadata: Dict = None, debug: bool = False) -> ComparisonResult:
        """Compare a single table"""
        full_table_name = table_config.get_full_table_name()
        start_time = datetime.now()
        
        self.audit_logger.logger.info(f"Starting comparison: {full_table_name}")
        
        if self.show_progress:
            tqdm.write(f"🔄 Comparing: {full_table_name}")
        
        try:
            # Extract data from both databases
            source_chunks = self._get_table_hash_chunks(self.source_db, table_config, metadata)
            target_chunks = self._get_table_hash_chunks(self.target_db, table_config, metadata)
            
            total_source_rows = sum(chunk['size'] for chunk in source_chunks)
            total_target_rows = sum(chunk['size'] for chunk in target_chunks)
            
            if debug:
                print(f"\nDEBUG: {full_table_name}")
                print(f"Source chunks: {len(source_chunks)}, Total source rows: {total_source_rows}")
                print(f"Target chunks: {len(target_chunks)}, Total target rows: {total_target_rows}")
            
            # Compare chunks
            all_missing_in_target = set()
            all_missing_in_source = set()
            all_different_rows = set()
            all_matching_rows = set()
            differences_for_sql = []
            
            max_chunks = max(len(source_chunks), len(target_chunks))
            
            # Progress bar for comparison
            if self.show_progress:
                chunk_pbar = tqdm(
                    total=max_chunks,
                    desc=f"🔍 {table_config.table_name}",
                    unit="chunk",
                    leave=False,
                    colour="green"
                )
            
            for i in range(max_chunks):
                source_chunk = source_chunks[i] if i < len(source_chunks) else {'data': {}}
                target_chunk = target_chunks[i] if i < len(target_chunks) else {'data': {}}
                
                comparison = self._compare_chunks(source_chunk, target_chunk)
                
                if debug:
                    print(f"Chunk {i}: Source keys: {len(source_chunk.get('data', {}))}, "
                          f"Target keys: {len(target_chunk.get('data', {}))}")
                    print(f"  Missing in target: {len(comparison['missing_in_target'])}")
                    print(f"  Missing in source: {len(comparison['missing_in_source'])}")
                    print(f"  Different: {len(comparison['different_rows'])}")
                    print(f"  Matching: {len(comparison['matching_rows'])}")
                
                all_missing_in_target.update(comparison['missing_in_target'])
                all_missing_in_source.update(comparison['missing_in_source'])
                all_different_rows.update(comparison['different_rows'])
                all_matching_rows.update(comparison['matching_rows'])
                differences_for_sql.append(comparison)
                
                if self.show_progress:
                    stats = f"M:{len(comparison['matching_rows'])} D:{len(comparison['different_rows'])}"
                    chunk_pbar.set_postfix_str(stats)
                    chunk_pbar.update(1)
            
            if self.show_progress:
                chunk_pbar.close()
            
            if debug:
                print(f"\nFINAL TOTALS for {full_table_name}:")
                print(f"  Total missing in target: {len(all_missing_in_target)}")
                print(f"  Total missing in source: {len(all_missing_in_source)}")
                print(f"  Total different: {len(all_different_rows)}")
                print(f"  Total matching: {len(all_matching_rows)}")
                print(f"  Sum: {len(all_missing_in_target) + len(all_missing_in_source) + len(all_different_rows) + len(all_matching_rows)}")
                print(f"  Expected (source + target): {total_source_rows + total_target_rows}")
                
                # Verify no overlaps
                all_keys = (all_missing_in_target | all_missing_in_source | 
                           all_different_rows | all_matching_rows)
                print(f"  Unique keys processed: {len(all_keys)}")
            
            # Generate SQL if differences found
            if all_missing_in_target or all_missing_in_source or all_different_rows:
                if debug:
                    print(f"\nGenerating SQL files...")
                    self._debug_comparison_results(table_config, differences_for_sql)
                
                self._generate_sql_files(table_config, differences_for_sql)
            
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
            
            # Log results
            self.audit_logger.logger.info(f"Completed {full_table_name}: "
                                        f"{result.matching_rows:,} matching, "
                                        f"{result.different_rows:,} different, "
                                        f"{result.missing_in_target:,} missing in target")
            
            if self.show_progress:
                status_icon = "✅" if (result.missing_in_target + result.missing_in_source + result.different_rows == 0) else "⚠️"
                tqdm.write(f"{status_icon} {full_table_name}: {result.matching_rows:,} matching, "
                          f"{result.different_rows:,} different, {result.missing_in_target:,} missing")
            
            return result
            
        except Exception as e:
            self.audit_logger.logger.error(f"Error comparing {full_table_name}: {str(e)}")
            raise
    
    def _generate_sql_files(self, table_config: TableConfig, differences: List[Dict]):
        """Generate SQL synchronization files"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        table_identifier = f"{table_config.schema_name}_{table_config.table_name}"
        
        target_sql_file = f"sync_target_{table_identifier}_{timestamp}.sql"
        source_sql_file = f"sync_source_{table_identifier}_{timestamp}.sql"
        
        full_table_name = table_config.get_full_table_name()
        
        with open(target_sql_file, 'w') as target_file, open(source_sql_file, 'w') as source_file:
            # Write headers
            for file, db_type in [(target_file, "TARGET"), (source_file, "SOURCE")]:
                file.write(f"-- SQL to sync {db_type} Oracle database for table {full_table_name}\n")
                file.write(f"-- Generated on {datetime.now()}\n\n")
            
            # Generate SQL statements
            for diff in differences:
                # INSERT statements for missing records
                for pk_key in diff['missing_in_target']:
                    if pk_key in diff['source_data']:
                        row_data = diff['source_data'][pk_key]['data']
                        self._write_insert_sql(target_file, full_table_name, row_data)
                
                for pk_key in diff['missing_in_source']:
                    if pk_key in diff['target_data']:
                        row_data = diff['target_data'][pk_key]['data']
                        self._write_insert_sql(source_file, full_table_name, row_data)
                
                # UPDATE statements for different records
                for pk_key in diff['different_rows']:
                    if pk_key in diff['source_data']:
                        source_row = diff['source_data'][pk_key]['data']
                        self._write_update_sql(target_file, table_config, source_row, pk_key)
            
            target_file.write("\nCOMMIT;\n")
            source_file.write("\nCOMMIT;\n")
        
        self.audit_logger.logger.info(f"Generated SQL files: {target_sql_file}, {source_sql_file}")
        
        if self.show_progress:
            tqdm.write(f"📝 Generated: {target_sql_file}, {source_sql_file}")
    
    def _write_insert_sql(self, file, table_name: str, row_data: Dict):
        """Write INSERT SQL statement"""
        columns = list(row_data.keys())
        values = []
        
        for val in row_data.values():
            if val is None:
                values.append('NULL')
            elif isinstance(val, str):
                escaped_val = val.replace("'", "''")
                values.append(f"'{escaped_val}'")
            elif isinstance(val, datetime):
                values.append(f"TO_DATE('{val.strftime('%Y-%m-%d %H:%M:%S')}', 'YYYY-MM-DD HH24:MI:SS')")
            else:
                values.append(str(val))
        
        file.write(f"INSERT INTO {table_name} ({', '.join(columns)}) ")
        file.write(f"VALUES ({', '.join(values)});\n")
    
    def _write_update_sql(self, file, table_config: TableConfig, row_data: Dict, pk_key: str):
        """Write UPDATE SQL statement"""
        pk_values = pk_key.split('|')
        full_table_name = table_config.get_full_table_name()
        
        set_clause = []
        where_clause = []
        
        # Build WHERE clause
        for i, pk_col in enumerate(table_config.primary_key):
            where_clause.append(f"{pk_col} = '{pk_values[i]}'")
        
        # Build SET clause
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
        """Compare all configured tables"""
        results = []
        
        if self.show_progress:
            tqdm.write(f"🚀 Starting Oracle comparison: {len(self.tables)} tables")
        
        # Pre-fetch metadata for all tables
        if self.show_progress:
            tqdm.write("📋 Fetching table metadata...")
        
        metadata = self._bulk_fetch_metadata()
        
        # Table progress bar
        table_pbar = None
        if self.show_progress:
            table_pbar = tqdm(
                total=len(self.tables),
                desc="📊 Tables",
                unit="table",
                colour="blue"
            )
        
        try:
            for table_config in self.tables:
                try:
                    result = self.compare_table(table_config, metadata)
                    results.append(result)
                    
                    # Generate summary report
                    self._generate_summary_report(result)
                    
                    if table_pbar:
                        status = "✅" if (result.missing_in_target + result.missing_in_source + result.different_rows == 0) else "⚠️"
                        table_pbar.set_postfix_str(status)
                        table_pbar.update(1)
                        
                except Exception as e:
                    self.audit_logger.logger.error(f"Failed to compare {table_config.get_full_table_name()}: {str(e)}")
                    if table_pbar:
                        table_pbar.set_postfix_str("❌")
                        table_pbar.update(1)
                    continue
            
            if self.show_progress:
                tqdm.write(f"\n🎉 Comparison completed: {len(results)} tables processed")
                synced_count = sum(1 for r in results if r.missing_in_target + r.missing_in_source + r.different_rows == 0)
                tqdm.write(f"   ✅ In sync: {synced_count}")
                tqdm.write(f"   ⚠️ With differences: {len(results) - synced_count}")
        
        finally:
            if table_pbar:
                table_pbar.close()
        
        return results
    
    def _generate_summary_report(self, result: ComparisonResult):
        """Generate summary report"""
        schema_table = f"{result.schema_name}_{result.table_name}"
        report_file = f"summary_{schema_table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        with open(report_file, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"ORACLE TABLE COMPARISON SUMMARY: {result.get_full_table_name()}\n")
            f.write("="*80 + "\n")
            f.write(f"Generated: {datetime.now()}\n")
            f.write(f"Source DB: {self.source_db.host}:{self.source_db.port}/{self.source_db.service_name}\n")
            f.write(f"Target DB: {self.target_db.host}:{self.target_db.port}/{self.target_db.service_name}\n\n")
            
            f.write(f"Total rows in source: {result.total_source_rows:,}\n")
            f.write(f"Total rows in target: {result.total_target_rows:,}\n")
            f.write(f"Matching rows: {result.matching_rows:,}\n")
            f.write(f"Rows missing in target: {result.missing_in_target:,}\n")
            f.write(f"Rows missing in source: {result.missing_in_source:,}\n")
            f.write(f"Different rows: {result.different_rows:,}\n")
            f.write(f"Execution time: {result.execution_time:.2f} seconds\n")
            f.write(f"Chunks processed: {result.chunks_processed}\n\n")
            
            if result.missing_in_target + result.missing_in_source + result.different_rows == 0:
                f.write("✅ TABLES ARE IN SYNC\n")
            else:
                f.write("⚠️ TABLES HAVE DIFFERENCES\n")
                f.write("SQL files generated for synchronization.\n")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Oracle Table Comparator - Simple Version')
    parser.add_argument('config', help='Configuration file path (YAML)')
    parser.add_argument('--table', help='Compare specific table only (format: SCHEMA.TABLE)')
    parser.add_argument('--no-progress', action='store_true', help='Disable progress bars')
    parser.add_argument('--json-output', action='store_true', help='Output results in JSON format')
    
    args = parser.parse_args()
    
    try:
        show_progress = not args.no_progress
        comparator = OracleTableComparatorSimple(args.config, show_progress)
        
        if args.table:
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
            results = [result]
        else:
            results = comparator.compare_all_tables()
        
        # Output results
        if args.json_output:
            output = {
                'comparison_summary': {
                    'total_tables': len(results),
                    'completion_time': datetime.now().isoformat(),
                    'source_db': f"{comparator.source_db.host}:{comparator.source_db.port}",
                    'target_db': f"{comparator.target_db.host}:{comparator.target_db.port}"
                },
                'results': [
                    {
                        'table': result.get_full_table_name(),
                        'total_source_rows': result.total_source_rows,
                        'total_target_rows': result.total_target_rows,
                        'matching_rows': result.matching_rows,
                        'missing_in_target': result.missing_in_target,
                        'missing_in_source': result.missing_in_source,
                        'different_rows': result.different_rows,
                        'execution_time': result.execution_time,
                        'in_sync': (result.missing_in_target + result.missing_in_source + result.different_rows) == 0
                    }
                    for result in results
                ]
            }
            print(json.dumps(output, indent=2))
        else:
            print(f"\n📊 Comparison Summary:")
            print(f"   Tables processed: {len(results)}")
            