import logging
import os
from datetime import datetime
from rich.console import Console
from rich.theme import Theme

# --- Professional Logging System ---
custom_theme = Theme({
    "info": "cyan",
    "warning": "bold yellow",
    "error": "bold red",
    "success": "bold green",
})

console = Console(theme=custom_theme)
LEVEL_MAP = {"DEBUG": 10, "INFO": 20, "SUCCESS": 25, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

LOG_DIR = os.path.join(os.getcwd(), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

# File Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )] if hasattr(logging, "handlers") else [logging.FileHandler(LOG_FILE, encoding='utf-8')]
)

class FatwaLogger:
    def __init__(self, name="FatwaBot"):
        self.logger = logging.getLogger(name)
        self.log_level = os.getenv("TITAN_LOG_LEVEL", "INFO").upper()
        self.current_level = LEVEL_MAP.get(self.log_level, 20)

    def _should_print(self, level_name):
        return LEVEL_MAP.get(level_name, 20) >= self.current_level

    def info(self, msg):
        self.logger.info(msg)
        if self._should_print("INFO"):
            console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] ℹ️ {msg}", style="info")

    def success(self, msg):
        self.logger.info(f"SUCCESS: {msg}")
        if self._should_print("SUCCESS"):
            console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] ✅ {msg}", style="success")

    def warning(self, msg):
        self.logger.warning(msg)
        if self._should_print("WARNING"):
            console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] ⚠️ {msg}", style="warning")

    def error(self, msg):
        self.logger.error(msg)
        if self._should_print("ERROR"):
            console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] ❌ {msg}", style="error")

    def critical(self, msg):
        self.logger.critical(msg)
        console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] 💀 [bold red]{msg}[/bold red]")

logger = FatwaLogger()
