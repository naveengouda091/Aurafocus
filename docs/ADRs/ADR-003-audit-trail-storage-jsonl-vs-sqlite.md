# ADR-003: Decision Audit Trail Storage Format (JSONL vs. SQLite)

**Status:** Accepted  
**Date:** 2026-09-23  
**Deciders:** Staff Technical Product Manager (TPM), Lead Systems Architect  
**Technical Area:** Persistence Layer, Telemetry, Auditability, Data Engine  

---

## 1. Context and Problem Statement

AuraFocus's core differentiator and compliance requirement is the **"Decision Taken" Audit Engine**. The daemon must log every evaluation cycle (1 Hz) to an immutable audit trail capturing 10 distinct telemetry attributes:
1. `timestamp`
2. `process_name`
3. `window_title`
4. `is_fullscreen`
5. `laya_mode`
6. `confidence`
7. `urgency_score`
8. `should_silence`
9. `debounce_action_applied`
10. `os_dnd_state`

The storage mechanism must support continuous low-overhead appending from the background daemon while simultaneously allowing the CLI reporting tool (`report.py`) or external tooling to read and analyze logs without causing file-lock conflicts, thread stalls, or corruption during sudden OS reboots.

---

## 2. Decision Drivers

- **Zero-Latency Non-Blocking Writes:** Disk writes must never stall or add jitter to the daemon's 1Hz cycle.
- **Resilience to Crash Corruption:** If the machine loses power or the process is terminated abruptly (`SIGKILL` / Task Manager end-process), already persisted records must remain 100% intact.
- **Concurrent Read/Write Safety:** The user must be able to run `report.py` or inspect logs without stopping the background daemon or hitting database lock errors.
- **Inspectability & Transparency:** Users and compliance auditors must be able to verify decisions using standard utilities (`tail`, `grep`, `jq`, VS Code) without requiring proprietary database clients.
- **Minimal Complexity & Dependencies:** Zero native C-extension database drivers or external database server processes.

---

## 3. Considered Options

1. **Option 1: Embedded Relational Database (SQLite)**  
   Store decisions in a relational table with indexed columns (`timestamp`, `process_name`, `laya_mode`).
2. **Option 2: Append-Only JSON Lines (`decisions.jsonl`) (Selected)**  
   Store each decision record as a single JSON object terminated by a newline character in an append-only flat file.
3. **Option 3: Embedded Columnar Database (DuckDB / Parquet)**  
   Write records to columnar parquet batches for high-speed analytical aggregation.

---

## 4. Evaluation & Trade-off Matrix

| Criterion | Option 1: SQLite | Option 2: JSON Lines (`decisions.jsonl`) | Option 3: Columnar (Parquet/DuckDB) |
| :--- | :--- | :--- | :--- |
| **Write Mechanism** | Transactional B-Tree / WAL file writes | **Atomic `write()` + flush with `O_APPEND`** | Batch buffer commit |
| **Concurrent Access** | Risk of `sqlite3.OperationalError: database is locked` during reads | **Lockless: Reader streams up to current EOF; Writer appends** | File locks or reader/writer contention |
| **Crash Durability** | Requires WAL recovery; risk of malformed DB header on sudden crash | **Zero corruption risk; at worst only the final trailing partial line is truncated** | Potential parquet footer corruption |
| **Human Inspectability** | Binary format (requires SQLite viewer) | **100% Human-readable plain text (`jq`, `grep`, `tail -f`)** | Binary format |
| **Streaming Memory Footprint** | Low ($O(1)$ cursor) | **Ultra-Low ($O(1)$ via Python generator: `for line in f:`)** | Moderate |
| **Daily Storage Footprint** | $\approx 4\text{ MB} - 7\text{ MB}$ / 8-hour workday | **$\approx 5.5\text{ MB}$ / 8-hour workday ($\sim 190\text{ bytes/line}$)** | $\approx 1.5\text{ MB}$ (Compressed) |
| **External Dependencies** | Standard library `sqlite3` (C dependency) | **Zero (Native Python `json` and standard file I/O)** | Requires `pyarrow` / `duckdb` (>100MB wheels) |

---

## 5. Decision Outcome

**Chosen Option:** **Option 2: Append-Only JSON Lines (`decisions.jsonl`)**.

### Rationale:
1. **OS-Level Atomic Append Guarantees:** On both Windows (NTFS) and POSIX filesystems, opening a file in append mode (`a` / `FILE_APPEND_DATA`) ensures writes are serialized at the OS kernel level. Appends do not require explicit reader-writer mutexes across processes.
2. **Crash Resilience:** Because each record is self-contained and delimited by `\n`, an abrupt crash or system reboot can never corrupt historical records. If an abrupt shutdown occurs mid-write, only the final incomplete line is discarded, leaving all prior entries intact.
3. **Seamless CLI Telemetry Processing:** In `report.py`, analyzing 100,000 records takes $< 150\text{ ms}$ using a streaming line generator, using $< 15\text{ MB}$ of Python heap memory. Complex database indexes are completely unnecessary for the scale of desktop telemetry (86,400 records per 24 hours).
4. **Audit Transparency:** Users can directly view, search, and audit their decisions using familiar tools like PowerShell `Get-Content -Tail 10 -Wait`, bash `tail -f decisions.jsonl | jq`, or any plain text editor.

---

## 6. Implementation Consequences

- An asynchronous write queue (`queue.Queue`) inside `logger.py` buffers records in memory so that OS file I/O never blocks the sensing/inference loop.
- A log rotation policy can be added in future versions if log size exceeds a configurable threshold (e.g., $>50\text{ MB}$).
