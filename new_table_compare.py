#!/usr/bin/env python3
"""
compare_tables.py

Scalable, configurable script to compare tables across two Oracle clusters
using SHA-256 checksums and multi-threading—no direct cx_Oracle dependency.
"""

import argparse
import json
import logging
import hashlib
import sys
import os

from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Tuple

import concurrent.futures

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import SQLAlchemyError

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("table_compare.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Config loader/validator
# ─────────────────────────────────────────────────────────────────────────────
class DatabaseConfig:
    """Load and validate configuration from JSON file."""
    def __init__(self, path: str):
        try:
            with open(path, "r") as f:
                self.cfg = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read config: {e}")
            raise

        self._validate()

    def _validate(self):
        keys = ["source_db", "target_db", "source_table_name", "target_table_name", "batch_size", "max_threads"]
        for k in keys:
            if k not in self.cfg:
                raise ValueError(f"Missing config key: {k}")

        for side in ("source_db", "target_db"):
            for sub in ("username", "password", "host", "port", "service"):
                if sub not in self.cfg[side]:
                    raise ValueError(f"Missing {side}.{sub}")


# ─────────────────────────────────────────────────────────────────────────────
# Oracle via SQLAlchemy
# ─────────────────────────────────────────────────────────────────────────────
class SQLDatabase:
    """
    Wraps a SQLAlchemy engine for Oracle.
    Default schema = connection user (uppercased).
    """
    def __init__(self, db_conf: Dict):
        user = db_conf["username"]
        pwd  = db_conf["password"]
        host = db_conf["host"]
        port = db_conf["port"]
        svc  = db_conf["service"]
        # optional override
        self.schema = db_conf.get("schema", user.upper())

        uri = f"oracle+oracledb://{user}:{pwd}@{host}:{port}/?service_name={svc}"
        self.engine = create_engine(uri, pool_pre_ping=True)

    @contextmanager
    def connect(self):
        """Yield a fresh Connection; ensure it's closed."""
        conn = self.engine.connect()
        try:
            yield conn
        except SQLAlchemyError as e:
            logger.error(f"DB error: {e}")
            raise
        finally:
            conn.close()

    def get_table_metadata(self, table_name: str) -> Tuple[str, List[str]]:
        """
        Discover the PK and non-PK columns for a table.
        """
        insp = inspect(self.engine)
        tbl = table_name.upper()

        pk_info = insp.get_pk_constraint(tbl, schema=self.schema)
        pk_cols = pk_info.get("constrained_columns", [])
        if not pk_cols:
            raise ValueError(f"No PK on {self.schema}.{tbl}")
        pk = pk_cols[0]

        cols = [
            col["name"]
            for col in insp.get_columns(tbl, schema=self.schema)
            if col["name"] != pk
        ]
        return pk, cols


# ─────────────────────────────────────────────────────────────────────────────
# Checksum & fetch routines
# ─────────────────────────────────────────────────────────────────────────────
def compute_checksum(values: Tuple) -> str:
    """SHA-256 over pipe-delimited string of values."""
    h = hashlib.sha256()
    for v in values:
        h.update((str(v) if v is not None else "").encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def fetch_batch(
    conn,
    full_table: str,
    pk_col: str,
    cols: List[str],
    batch_size: int,
    offset: int
) -> List[Tuple]:
    """
    SELECT PK and columns in one page.
    """
    qry = text(f"""
        SELECT {pk_col}, {', '.join(cols)}
        FROM {full_table}
        ORDER BY {pk_col}
        OFFSET :offset ROWS FETCH NEXT :batch_size ROWS ONLY
    """)
    result = conn.execute(qry, {"offset": offset, "batch_size": batch_size})
    return result.fetchall()


def compare_batch(
    engine_src,
    engine_tgt,
    src_table: str,
    tgt_table: str,
    pk_col: str,
    cols: List[str],
    batch_size: int,
    offset: int
) -> List[Dict]:
    """
    Open fresh connections per thread, fetch the same page from source/target,
    compute checksums, and record any mismatches or missing keys.
    """
    diffs = []
    try:
        with engine_src.connect() as cs, engine_tgt.connect() as ct:
            src_rows = fetch_batch(cs, src_table, pk_col, cols, batch_size, offset)
            tgt_rows = fetch_batch(ct, tgt_table, pk_col, cols, batch_size, offset)

        src_map = {r[0]: compute_checksum(r[1:]) for r in src_rows}
        tgt_map = {r[0]: compute_checksum(r[1:]) for r in tgt_rows}

        all_keys = set(src_map)|set(tgt_map)
        for key in all_keys:
            s = src_map.get(key)
            t = tgt_map.get(key)
            if s != t:
                status = (
                    "mismatch" if (key in src_map and key in tgt_map) else
                    "missing_in_target" if key in src_map else
                    "missing_in_source"
                )
                diffs.append({
                    "primary_key":    key,
                    "source_checksum": s or "",
                    "target_checksum": t or "",
                    "status":          status
                })
    except Exception as e:
        logger.error(f"Batch @offset {offset} failed: {e}")
    return diffs


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────
def compare_tables(cfg: DatabaseConfig):
    src_db = SQLDatabase(cfg.cfg["source_db"])
    tgt_db = SQLDatabase(cfg.cfg["target_db"])
    src_tbl = cfg.cfg["source_table_name"]
    tgt_tbl = cfg.cfg["target_table_name"]
    batch_size  = cfg.cfg["batch_size"]
    max_threads = cfg.cfg["max_threads"]

    # Discover PK & columns
    try:
        pk_src, cols_src = src_db.get_table_metadata(src_tbl)
        pk_tgt, cols_tgt = tgt_db.get_table_metadata(tgt_tbl)
        if pk_src != pk_tgt or set(cols_src) != set(cols_tgt):
            raise ValueError("Source/target schemas differ")
    except Exception as e:
        logger.error(f"Metadata discovery failed: {e}")
        raise

    # Fully qualified names
    full_src = f"{src_db.schema}.{src_tbl.upper()}"
    full_tgt = f"{tgt_db.schema}.{tgt_tbl.upper()}"

    # Count total rows (from source)
    with src_db.connect() as conn:
        cnt = conn.execute(text(f"SELECT COUNT(*) FROM {full_src}")).scalar()
    logger.info(f"Total rows to process: {cnt}")

    offsets = list(range(0, cnt, batch_size))
    tasks = [
        (src_db.engine, tgt_db.engine, full_src, full_tgt, pk_src, cols_src, batch_size, off)
        for off in offsets
    ]

    all_diffs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as exe:
        futures = {
            exe.submit(compare_batch, *args): args[-1]
            for args in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            off = futures[fut]
            res = fut.result()
            all_diffs.extend(res)
            logger.info(f"Batch @{off} → {len(res)} differences")

    return all_diffs, cnt, pk_src, cols_src


def generate_report(
    diffs: List[Dict],
    total: int,
    pk_col: str,
    cols: List[str],
    out_dir: str = "reports"
):
    os.makedirs(out_dir, exist_ok=True)
    fn = datetime.now().strftime("report_%Y%m%d_%H%M%S.txt")
    path = os.path.join(out_dir, fn)

    with open(path, "w") as f:
        f.write(f"Table Compare Report\nGenerated: {datetime.now()}\n")
        f.write(f"Rows processed: {total}\nPK: {pk_col}\nCols: {', '.join(cols)}\n")
        f.write(f"Differences: {len(diffs)}\n\n")
        for d in diffs:
            f.write(
                f"{d['primary_key']} | {d['status']} | "
                f"{d['source_checksum']} -> {d['target_checksum']}\n"
            )
    logger.info(f"Report written to {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("config", help="JSON config path")
    args = p.parse_args()

    try:
        cfg = DatabaseConfig(args.config)
        diffs, total, pk, cols = compare_tables(cfg)
        generate_report(diffs, total, pk, cols)
        logger.info("Comparison complete.")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
