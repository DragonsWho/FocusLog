# FocusLog: Desktop Activity MCP Server

A background server that logs your desktop activity, calculates your Actions Per Minute (APM), and provides a clean, anonymized timeline of your work on demand. It's designed to be a data source for personal analytics or AI assistants.

This server uses `FastMCP` for communication, `Ollama` for AI-powered anonymization, and standard Linux tools for activity tracking.

## Features

-   **Activity Logging**: Periodically records the title of the currently focused window.
-   **APM Tracking**: Calculates your Actions Per Minute (APM) based on keyboard and mouse activity.
-   **Timeline Aggregation**: Groups consecutive activities into a compressed, easy-to-read timeline.
-   **Two-Stage Anonymization**:
    1.  **Hard-coded Filter**: Guarantees removal of user-defined sensitive keywords (e.g., nicknames, emails).
    2.  **LLM Anonymization**: Uses a local Ollama model to intelligently remove any other Personally Identifiable Information (PII).
-   **Title Sanitization**: Cleans up and shortens excessively long window titles.
-   **Reliable & Configurable**: Features log rotation, graceful shutdown, and all settings are managed in a separate `config.py` file.
-   **Systemd Service**: Can be run as a persistent background service.

## Requirements

### System Dependencies

You must have the following command-line tools installed:
-   `xdotool`: For getting the active window title.
-   `xprintidle`: For checking user idle time.
-   `ollama`: For running the local LLM.

On Debian/Ubuntu, you can install them with:
```bash
sudo apt update
sudo apt install xdotool xprintidle
```

> [!IMPORTANT]
> **Compatibility Note:** This server is designed specifically for **Linux desktop environments running on the X11 display server**. It relies on tools that are part of the X11 ecosystem and will **not** work on Windows, macOS, or native Wayland sessions.

For `Ollama`, follow the official installation instructions at [ollama.com](https://ollama.com/). After installing, make sure you have pulled a model:
```bash
ollama pull gemma3
```

### Python Dependencies

The project requires Python 3.8+ and a few libraries.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/focuslog.git
    cd focuslog
    ```

2.  **Install Python packages:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Create your configuration file:**
    Copy the example configuration file.
    ```bash
    cp config.py.example config.py
    ```

4.  **Edit your configuration:**
    Open `config.py` in a text editor.
    -   **Crucially, fill in the `FORBIDDEN_KEYWORDS` list** with any personal data (nicknames, email, etc.) you want to ensure is always removed. **This file is ignored by git, so your secrets are safe.**
    -   Adjust other settings like the `OLLAMA_MODEL` or `MCP_PORT` if you wish.

## Usage

### Manual Start (for testing)

You can run the server directly from your terminal:
```bash
python focuslog.py
```
The server will start and show logs in the console. Press `Ctrl+C` to stop it.

### Running as a Service (Recommended)

For continuous, reliable background operation, it's best to run FocusLog as a `systemd` user service. This will automatically start the server when you log in and restart it if it ever crashes.

1.  **Prepare the service file:**
    Copy the example service file.
    ```bash
    cp focuslog.service.example focuslog.service
    ```

2.  **Edit `focuslog.service`:**
    Open the file and replace the placeholder paths and username with your actual ones.
    - `User=your_username` -> `User=myuser`
    - `Group=your_username` -> `Group=myuser`
    - `WorkingDirectory=/home/your_username/path/to/focuslog` -> `WorkingDirectory=/home/myuser/projects/focuslog`
    - `ExecStart=...` -> Update the path here as well.

3.  **Install and enable the service:**
    First, create the directory for user services if it doesn't exist:
    ```bash
    mkdir -p ~/.config/systemd/user
    ```
    Now, copy the file and start the service:
    ```bash
    cp focuslog.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now focuslog.service
    ```
    The `--now` flag both enables it for future logins and starts it immediately.

4.  **Manage the service:**
    -   **Check status:** `systemctl --user status focuslog.service`
    -   **View logs:** `journalctl --user -u focuslog.service -f` (`-f` to follow live)
    -   **Stop:** `systemctl --user stop focuslog.service`
    -   **Start:** `systemctl --user start focuslog.service`
 

## How It Works

The server runs two main background threads:
1.  **APM Sensor**: Checks for user input every half-second to update an activity "tick" counter.
2.  **Logger**: Every 10 seconds, it:
    -   Gets the current active window title.
    -   Sanitizes the title (shortens it and applies custom rules).
    -   Calculates the current APM based on ticks in the last minute.
    -   Saves the sanitized title and APM to an SQLite database.

When the `get_activity_log` tool is called via an MCP client, it:
1.  Fetches the raw data from the database.
2.  Performs two-stage anonymization on all unique window titles, caching the results for performance.
3.  Aggregates the data into a clean timeline.
4.  Returns the timeline as a string.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details. 