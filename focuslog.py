# --- START OF FILE focuslog.py ---

#
# FocusLog: Desktop Activity MCP Server
#
# Description:
# This MCP (Machine-to-Machine Communication Protocol) server runs as a background
# daemon to log focused window activity. It tracks user activity by calculating
# Actions Per Minute (APM), aggregates activity into a timeline, and provides
# on-demand, two-stage anonymization for window titles.
#
# Stage 1 Anonymization: Hard-coded removal of user-defined forbidden keywords.
# Stage 2 Anonymization: LLM-based (Ollama) removal of other PII.
#

import sys
import subprocess
import threading
import time
import logging
import sqlite3
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import statistics
from logging.handlers import RotatingFileHandler

# --- Local Imports ---
import config  # Import settings from config.py

# --- Dependency Check & Graceful Exit ---
try:
    from pydbus import SessionBus
    from fastmcp import FastMCP
except ImportError as e:
    missing_module = str(e).split("'")[1]
    print(f"Error: Required library '{missing_module}' is not installed.")
    print("Please install dependencies by running: pip install -r requirements.txt")
    sys.exit(1)

# --- Global State with Thread Safety ---
activity_ticks = deque()
activity_lock = threading.Lock()
anonymization_cache: Dict[str, str] = {}
anonymization_cache_lock = threading.Lock() # Lock for cache access

# --- Pre-compile regex for performance ---
# This pattern is built from user-defined keywords in config.py.
# The `\b` ensures whole-word matching only (e.g., 'user' won't match 'superuser').
FORBIDDEN_PATTERN = None
if config.FORBIDDEN_KEYWORDS:
    pattern_str = r'\b(' + '|'.join(re.escape(word) for word in config.FORBIDDEN_KEYWORDS) + r')\b'
    FORBIDDEN_PATTERN = re.compile(pattern_str, re.IGNORECASE)

# --- 1. Logging Setup ---
logger = logging.getLogger("FocusLogServerLogger")
logger.setLevel(logging.DEBUG)
if logger.hasHandlers():
    logger.handlers.clear()

# File handler with rotation (max 5MB per file, keeping 5 old files)
file_handler = RotatingFileHandler(
    config.LOG_FILE, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console handler for high-level info
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(levelname)s: %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# --- 2. System Dependency Checks ---
def check_system_dependencies():
    """Verify that required command-line tools are installed."""
    tools = ["xdotool", "ollama", "xprintidle"]
    for tool in tools:
        try:
            subprocess.run(["which", tool], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.critical(f"Error: Required system tool '{tool}' not found. Please install it.")
            sys.exit(1)
    logger.info("All system dependencies are met.")

# --- 3. Database Setup ---
def setup_database():
    """Initializes the SQLite database and activity table if they don't exist."""
    try:
        with sqlite3.connect(config.DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity (
                    timestamp TEXT PRIMARY KEY,
                    active_title TEXT,
                    apm INTEGER
                )
            """)
            conn.commit()
        logger.info(f"Database '{config.DB_FILE}' is ready.")
    except sqlite3.Error as e:
        logger.critical(f"Failed to set up database: {e}")
        sys.exit(1)

# --- 4. MCP Server & Tool Definition ---
mcp = FastMCP(
    name="FocusLogServer",
    instructions="This server logs focused window activity and user's AFK status. "
                 "It provides an APM (Actions Per Minute) metric for active usage."
)

# --- 5. Helper Functions ---
def _run_command(command: list[str], input_text: Optional[str] = None, timeout_s: int = 5) -> Optional[str]:
    """Executes a shell command and returns its stdout, with error handling and a timeout."""
    try:
        result = subprocess.run(
            command, input=input_text, check=True, capture_output=True,
            text=True, encoding='utf-8', timeout=timeout_s
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout_s}s: '{' '.join(command)}'")
        return None
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: '{' '.join(command)}'. Error: {e.stderr.strip()}")
        return None

def _get_active_window_title() -> Optional[str]:
    """Retrieves the title of the currently focused window."""
    return _run_command(["xdotool", "getactivewindow", "getwindowname"])

def _is_screen_locked() -> bool:
    """Checks if the screen is locked via D-Bus for common desktop environments."""
    try:
        bus = SessionBus()
        services = ['org.freedesktop.ScreenSaver', 'org.gnome.ScreenSaver', 'org.cinnamon.ScreenSaver', 'org.mate.ScreenSaver']
        for service in services:
            try:
                # Use a short timeout for D-Bus calls to prevent blocking
                return bool(bus.get(service, timeout=2).GetActive())
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"D-Bus screen lock check failed: {e}")
    return False

def _format_duration_compact(duration: timedelta) -> str:
    """Formats a timedelta into a compact string like '5m' or '2h30m'."""
    seconds = int(duration.total_seconds())
    if seconds < 60: return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    if minutes < 60: return f"{minutes}m"
    hours, rem_minutes = divmod(minutes, 60)
    if rem_minutes == 0: return f"{hours}h"
    return f"{hours}h{rem_minutes}m"

def _get_idle_time_ms() -> Optional[int]:
    """Returns user idle time in milliseconds using xprintidle."""
    result = _run_command(["xprintidle"])
    return int(result) if result and result.isdigit() else None

def _sanitize_window_title(title: str) -> str:
    """Cleans up and shortens window titles based on predefined rules."""
    browser_name = next((browser for browser in config.KNOWN_BROWSERS if title.endswith(browser)), None)
    if browser_name:
        for keyword, clean_name in config.TITLE_CLEANUP_RULES.items():
            if keyword in title:
                return f"{clean_name} - {browser_name}"

    if len(title) > config.MAX_TITLE_LENGTH:
        return "..." + title[-(config.MAX_TITLE_LENGTH - 3):]
    return title

def _anonymize_title(title: str) -> str:
    """
    Anonymizes a title in two stages:
    1. Hard-coded removal of forbidden keywords.
    2. LLM-based removal of other PII.
    """
    pre_sanitized_title = title
    if FORBIDDEN_PATTERN:
        pre_sanitized_title = FORBIDDEN_PATTERN.sub("", title).strip()
        pre_sanitized_title = re.sub(r'\s*-\s*-\s*', ' - ', pre_sanitized_title)
        pre_sanitized_title = pre_sanitized_title.strip(' -')

    prompt = f"""
You are a text sanitization filter. Your only task is to analyze the following window title and remove any Personally Identifiable Information (PII) like email addresses, real names, or usernames/nicknames. Keep all other information intact. You must only output the sanitized title.
Examples:
- Input: "@some_user - General - My Discord Server"
- Output: "General - My Discord Server"
- Input: "main.py - MySecretProject - Visual Studio Code"
- Output: "main.py - MySecretProject - Visual Studio Code"
Now, sanitize this window title:
Input: "{pre_sanitized_title}"
Output:
"""
    sanitized_title = _run_command(['ollama', 'run', config.OLLAMA_MODEL], input_text=prompt, timeout_s=config.OLLAMA_TIMEOUT_S)

    if sanitized_title is None:
        logger.warning(f"Ollama call failed for title: '{title}'. Returning pre-sanitized version.")
        return pre_sanitized_title

    return sanitized_title

@mcp.tool
def get_activity_log(hours_ago: int = 1) -> str:
    """
    Retrieves activity, anonymizes titles, and summarizes it into a compressed timeline
    with average APM for each period.

    Args:
        hours_ago: How many hours back to retrieve the log for. Defaults to 1.
    """
    logger.info(f"Tool 'get_activity_log' called for the last {hours_ago} hour(s).")
    hours_ago = max(1, min(hours_ago, config.LOG_RETENTION_HOURS))
    start_time_iso = (datetime.now() - timedelta(hours=hours_ago)).isoformat()

    try:
        with sqlite3.connect(config.DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, active_title, apm FROM activity WHERE timestamp >= ? ORDER BY timestamp", (start_time_iso,))
            raw_rows: List[Tuple[str, str, int]] = cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Database query failed: {e}")
        return "Error: Could not retrieve data from the activity log."

    if not raw_rows:
        return f"No activity recorded in the last {hours_ago} hour(s)."

    unique_titles = {title for _, title, _ in raw_rows if title and "USER_AFK" not in title}
    
    with anonymization_cache_lock:
        titles_to_anonymize = [title for title in unique_titles if title not in anonymization_cache]

    if titles_to_anonymize:
        logger.info(f"Anonymizing {len(titles_to_anonymize)} new unique titles in parallel...")
        with ThreadPoolExecutor() as executor:
            future_to_title = {executor.submit(_anonymize_title, title): title for title in titles_to_anonymize}
            for future in future_to_title:
                original_title = future_to_title[future]
                try:
                    sanitized_result = future.result()
                    with anonymization_cache_lock:
                        anonymization_cache[original_title] = sanitized_result
                except Exception as exc:
                    logger.error(f"'{original_title}' generated an exception during anonymization: {exc}")
                    with anonymization_cache_lock:
                        anonymization_cache[original_title] = original_title
        logger.info("Anonymization complete.")

    anonymization_map = {title: anonymization_cache.get(title, title) for title in unique_titles}

    timeline = []
    current_block_title, current_block_start_time, current_block_apm_values = None, None, []

    for timestamp_iso, title, apm in raw_rows:
        timestamp = datetime.fromisoformat(timestamp_iso)
        anonymized_title = anonymization_map.get(title, title)

        if anonymized_title != current_block_title:
            if current_block_title is not None:
                duration = timestamp - current_block_start_time
                avg_apm = int(statistics.mean(current_block_apm_values)) if current_block_apm_values else 0
                timeline.append((current_block_start_time, duration, current_block_title, avg_apm))

            current_block_title = anonymized_title
            current_block_start_time = timestamp
            current_block_apm_values = [apm] if apm is not None else []
        elif apm is not None:
            current_block_apm_values.append(apm)

    if current_block_title is not None:
        duration = datetime.now() - current_block_start_time
        avg_apm = int(statistics.mean(current_block_apm_values)) if current_block_apm_values else 0
        timeline.append((current_block_start_time, duration, current_block_title, avg_apm))

    timeline_str = f"Timeline of focused windows & AFK status (last {hours_ago}h):\n"
    for start, duration, title, avg_apm in timeline:
        if duration.total_seconds() < config.LOG_INTERVAL_SECONDS / 2: continue
        start_f = start.strftime('%H:%M')
        duration_f = _format_duration_compact(duration)

        if "USER_AFK" in title:
            timeline_str += f"{start_f} ({duration_f}): {title}\n"
        else:
            timeline_str += f"{start_f} ({duration_f}): (Avg APM: {avg_apm}) {title}\n"

    logger.debug(f"--- Sending to LLM ---\n{timeline_str.strip()}")
    return timeline_str.strip()


# --- 6. Background Threads ---
def _apm_counter_thread():
    """Polls for user activity (mouse/keyboard) and adds ticks to a thread-safe deque."""
    logger.info("APM sensor thread started.")
    last_idle_time = _get_idle_time_ms() or 0
    poll_interval_ms = int(config.APM_POLL_INTERVAL_S * 1000)

    while True:
        time.sleep(config.APM_POLL_INTERVAL_S)
        current_idle_time = _get_idle_time_ms()
        if current_idle_time is None: continue

        if current_idle_time < last_idle_time + poll_interval_ms:
            with activity_lock:
                activity_ticks.append(datetime.now())
        last_idle_time = current_idle_time

def log_activity_periodically():
    """Periodically logs the sanitized window title and current APM to the database."""
    logger.info("Main logger thread started.")
    while True:
        time.sleep(config.LOG_INTERVAL_SECONDS)
        try:
            final_title, apm_count = None, 0

            if _is_screen_locked():
                final_title = "USER_AFK_LOCKED"
            else:
                now = datetime.now()
                one_minute_ago = now - timedelta(seconds=config.APM_WINDOW_SECONDS)
                with activity_lock:
                    while activity_ticks and activity_ticks[0] < one_minute_ago:
                        activity_ticks.popleft()
                    apm_count = len(activity_ticks)

                raw_title = _get_active_window_title() or "Desktop"
                final_title = _sanitize_window_title(raw_title)

            timestamp_iso = datetime.now().isoformat()

            with sqlite3.connect(config.DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO activity (timestamp, active_title, apm) VALUES (?, ?, ?)",
                               (timestamp_iso, final_title, apm_count))
                cutoff_iso = (datetime.now() - timedelta(hours=config.LOG_RETENTION_HOURS)).isoformat()
                cursor.execute("DELETE FROM activity WHERE timestamp < ?", (cutoff_iso,))
                conn.commit()

            logger.debug(f"Logged activity: '{final_title}' with APM: {apm_count}")
        except Exception as e:
            logger.error(f"Error in main logger thread: {e}", exc_info=True)


# --- 7. Server Execution ---
if __name__ == "__main__":
    check_system_dependencies()
    setup_database()

    apm_sensor = threading.Thread(target=_apm_counter_thread, daemon=True)
    main_logger = threading.Thread(target=log_activity_periodically, daemon=True)

    apm_sensor.start()
    main_logger.start()

    logger.info(f"Starting FocusLog MCP Server on http://127.0.0.1:{config.MCP_PORT}/mcp/")
    logger.info("Press Ctrl+C to stop the server.")

    try:
        mcp.run(transport="http", port=config.MCP_PORT)
    except KeyboardInterrupt:
        logger.info("Shutdown signal received. Stopping server.")
        sys.exit(0)