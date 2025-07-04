# FocusLog: Desktop Activity MCP Server

A background server that logs your desktop activity, calculates your Actions Per Minute (APM), and provides a clean, anonymized timeline of your work on demand. It's designed to be a data source for personal analytics or AI assistants.

This server uses `FastMCP` for communication, `Ollama` for AI-powered anonymization, and standard Linux tools for activity tracking.

## Features

-   **Activity Logging**: Periodically records the title of the currently focused window.
-   **APM Tracking**: Calculates your Actions Per Minute (APM) based on keyboard and mouse activity.
-   **Timeline Aggregation**: Groups consecutive activities into a compressed, easy-to-read timeline.
-   **Two-Stage Anonymization**:
    1.  **Hard-coded Filter**: Guarantees removal of user-defined sensitive keywords.
    2.  **LLM Anonymization**: Uses a local Ollama model to intelligently remove any other PII.
-   **Title Sanitization**: Cleans up and shortens excessively long window titles.
-   **Reliable & Configurable**: Features log rotation, graceful shutdown, and all settings are managed in a separate `config.py` file.
-   **Systemd Service**: Can be run as a persistent background service for maximum reliability.

## Requirements

### System Dependencies

> [!IMPORTANT]
> **Compatibility Note:** This server is designed specifically for **Linux desktop environments running on the X11 display server**. It relies on tools that are part of the X11 ecosystem and will **not** work on Windows, macOS, or native Wayland sessions.

You must have the following command-line tools and libraries installed:
-   `xdotool`: For getting the active window title.
-   `xprintidle`: For checking user idle time.
-   `ollama`: For running the local LLM.
-   **PyGObject**: For D-Bus communication (used to detect screen lock).

On **Debian/Ubuntu/Linux Mint**, you can install them all with:
```bash
sudo apt update
sudo apt install xdotool xprintidle python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

For `Ollama`, follow the official installation instructions at [ollama.com](https://ollama.com/). After installing, make sure you have pulled a model:
```bash
ollama pull gemma3
```

### Python Dependencies

The project requires Python 3.8+ and uses a virtual environment to manage its packages.

## Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/focuslog.git
    cd focuslog
    ```

2.  **Create a virtual environment:**
    We need to create a virtual environment that has access to the system's `PyGObject` library.
    ```bash
    python3 -m venv venv --system-site-packages
    ```

3.  **Activate the environment and install packages:**
    ```bash
    source venv/bin/activate
    pip install -r requirements.txt
    deactivate
    ```
    *(You can leave the environment by typing `deactivate`)*.

4.  **Create your configuration file:**
    ```bash
    cp config.py.example config.py
    ```

5.  **Edit your configuration:**
    Open `config.py` in a text editor.
    -   **Crucially, fill in the `FORBIDDEN_KEYWORDS` list** with any personal data you want to ensure is always removed.
    -   Adjust other settings like `OLLAMA_MODEL` or `MCP_PORT` if you wish.

## Usage

### Manual Start (for testing)

To run the server manually for testing, first activate the virtual environment:
```bash
source venv/bin/activate
python focuslog.py
```
The server will start and show logs in the console. Press `Ctrl+C` to stop it.

### Running as a Service (Recommended)

For continuous, reliable background operation, it's best to run FocusLog as a `systemd` user service. This will automatically start the server when you log in and restart it if it ever crashes.

1.  **Prepare the service file:**
    ```bash
    cp focuslog.service.example focuslog.service
    ```

2.  **Edit `focuslog.service`:**
    Open the file and replace the placeholder paths with your actual ones.
    -   `WorkingDirectory=/home/your_username/path/to/focuslog` -> e.g., `WorkingDirectory=/home/dw/projects/focuslog`
    -   `ExecStart=...` -> Update the path to `venv/bin/python` and `focuslog.py` as well.
    -   **Important**: Do NOT add `User=` or `Group=` lines. `systemd --user` handles this automatically.

3.  **Install and enable the service:**
    ```bash
    mkdir -p ~/.config/systemd/user
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