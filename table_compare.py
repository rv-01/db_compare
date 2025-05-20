#!/usr/bin/env python3
import json
import logging
import hashlib
import oracledb
import concurrent.futures
from typing import List, Tuple, Dict
from datetime import datetime
import sys
import os
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('table_compare.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class DatabaseConfig:
    """Load and validate configuration from JSON file."""
    def __init__(self, config_path: str):
        try:
            with open(config_path, 'r') as f:
                self.config = json.load(f)
            self.validate_config()
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise

    def validate_config(self):
        """Validate required configuration parameters."""
        required_keys = ['source_db', 'target_db', 'table_name', 'batch_size', 'max_threads']
        for key in required_keys:
            if key not in self.config:
                raise ValueError(f"Missing required config key: {key}")

class OracleDatabase:
    """Handle Oracle database connections and queries using oracledb."""
    def __init__(self, db_config: Dict):
        self.conn_str = f"{db_config['username']}/{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['service']}"
        self.connection = None
        self.cursor = None

    @contextmanager
    def connect(self):
        """Context manager for database connection."""
        try:
            self.connection = oracledb.connect(dsn=self.conn_str)
            self.cursor = self.connection.cursor()
            yield self.cursor
        except oracledb.Error as e:
            logger.error(f"Database connection error: {e}")
            raise
        finally:
            if self.cursor:
                self.cursor.close()
            if self.connection:
                self.connection.close()

    def get_table_metadata(self, table_name: str) -> Tuple[str, List[str]]:
        """Fetch primary key and all columns for the table."""
        with self.connect() as cursor:
            # Get primary key
            cursor.execute("""
                SELECT cols.column_name
                FROM user_constraints cons
                JOIN user_cons_columns cols
                ON cons.constraint_name = cols.constraint_name
                WHERE cons.constraint_type = 'P'
                AND cons.table_name = :table_name
            """, {'table_name': table_name.upper()})
            primary_key = cursor.fetchone()
            if not primary_key:
                raise ValueError(f"No primary key found for table {table_name}")
            primary_key = primary_key[0]

            # Get all columns
            cursor.execute("""
                SELECT column_name
                FROM user_tab_columns
                WHERE table_name = :table_name
                ORDER BY column_id
            """, {'table_name': table_name.upper()})
            columns = [row[0] for row in cursor.fetchall()]

            # Exclude primary key from columns to compare
            columns = [col for col in columns if col != primary_key]

            return primary_key, columns

def compute_checksum(row: Tuple, columns: List[str]) -> str:
    """Compute MD5 checksum for a row."""
    row_str = ''.join(str(col) for col in row)
    return hashlib.md5(row_str.encode('utf-8')).hexdigest()

def fetch_data(cursor, table_name: str, primary_key: str, columns: List[str], 
               batch_size: int, offset: int) -> List[Tuple]:
    """Fetch a batch of data from the database."""
    query = f"""
        SELECT {primary_key}, {', '.join(columns)}
        FROM {table_name}
        ORDER BY {primary_key}
        OFFSET :offset ROWS FETCH NEXT :batch_size ROWS ONLY
    """
    try:
        cursor.execute(query, {'offset': offset, 'batch_size': batch_size})
        return cursor.fetchall()
    except oracledb.Error as e:
        logger.error(f"Error fetching data: {e}")
        raise

def compare_batch(args: Tuple) -> List[Dict]:
    """Compare a batch of rows between source and target databases."""
    (source_cursor, target_cursor, table_name, primary_key, columns, 
     batch_size, offset) = args
    differences = []

    try:
        source_rows = fetch_data(source_cursor, table_name, primary_key, columns, batch_size, offset)
        target_rows = fetch_data(target_cursor, table_name, primary_key, columns, batch_size, offset)

        source_dict = {row[0]: compute_checksum(row[1:], columns) for row in source_rows}
        target_dict = {row[0]: compute_checksum(row[1:], columns) for row in target_rows}

        # Find differences
        all_keys = set(source_dict.keys()) | set(target_dict.keys())
        for key in all_keys:
            source_checksum = source_dict.get(key)
            target_checksum = target_dict.get(key)

            if source_checksum != target_checksum:
                diff = {
                    'primary_key': key,
                    'source_checksum': source_checksum,
                    'target_checksum': target_checksum,
                    'status': 'mismatch' if key in source_dict and key in target_dict
                            else 'missing_in_target' if key in source_dict
                            else 'missing_in_source'
                }
                differences.append(diff)

        return differences
    except Exception as e:
        logger.error(f"Error in batch comparison at offset {offset}: {e}")
        return differences

def compare_tables(config: DatabaseConfig):
    """Main function to compare tables across databases."""
    source_db = OracleDatabase(config.config['source_db'])
    target_db = OracleDatabase(config.config['target_db'])
    table_name = config.config['table_name']
    batch_size = config.config['batch_size']
    max_threads = config.config['max_threads']

    # Fetch metadata
    try:
        primary_key, columns = source_db.get_table_metadata(table_name)
        # Verify target table has same structure
        target_pk, target_columns = target_db.get_table_metadata(table_name)
        if primary_key != target_pk or set(columns) != set(target_columns):
            raise ValueError("Table structures differ between source and target databases")
    except Exception as e:
        logger.error(f"Failed to fetch table metadata: {e}")
        raise

    differences = []
    total_rows = 0

    try:
        with source_db.connect() as source_cursor, target_db.connect() as target_cursor:
            # Get total row count
            source_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            total_rows = source_cursor.fetchone()[0]
            logger.info(f"Total rows to process: {total_rows}")

            # Prepare batches
            offsets = list(range(0, total_rows, batch_size))
            tasks = [(source_cursor, target_cursor, table_name, primary_key, columns, 
                     batch_size, offset) for offset in offsets]

            # Process batches in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
                future_to_offset = {executor.submit(compare_batch, task): task[-1] for task in tasks}
                for future in concurrent.futures.as_completed(future_to_offset):
                    offset = future_to_offset[future]
                    try:
                        batch_differences = future.result()
                        differences.extend(batch_differences)
                        logger.info(f"Processed batch at offset {offset}")
                    except Exception as e:
                        logger.error(f"Batch at offset {offset} failed: {e}")

    except Exception as e:
        logger.error(f"Table comparison failed: {e}")
        raise

    return differences, total_rows, primary_key, columns

def generate_report(differences: List[Dict], total_rows: int, primary_key: str, 
                   columns: List[str], output_dir: str = 'reports'):
    """Generate a summary report of differences."""
    os.makedirs(output_dir, exist_ok=True)
    report_file = os.path.join(output_dir, f"table_compare_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

    with open(report_file, 'w') as f:
        f.write(f"Table Comparison Report\n")
        f.write(f"Generated at: {datetime.now()}\n")
        f.write(f"Total rows processed: {total_rows}\n")
        f.write(f"Primary Key: {primary_key}\n")
        f.write(f"Columns Compared: {', '.join(columns)}\n")
        f.write(f"Total differences found: {len(differences)}\n\n")

        for diff in differences:
            f.write(f"Primary Key: {diff['primary_key']}\n")
            f.write(f"Status: {diff['status']}\n")
            f.write(f"Source Checksum: {diff['source_checksum']}\n")
            f.write(f"Target Checksum: {diff['target_checksum']}\n")
            f.write("-" * 50 + "\n")

    logger.info(f"Report generated at {report_file}")

def main(config_path: str = 'config.json'):
    """Main entry point for the script."""
    try:
        config = DatabaseConfig(config_path)
        differences, total_rows, primary_key, columns = compare_tables(config)
        generate_report(differences, total_rows, primary_key, columns)
        logger.info("Table comparison completed successfully")
    except Exception as e:
        logger.error(f"Script execution failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()