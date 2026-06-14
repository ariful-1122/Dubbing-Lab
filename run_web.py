#!/usr/bin/env python3
"""Runner script to build the frontend and launch the Dubbing Lab Web UI."""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Add project root to python path to ensure app/ modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings


def verify_node_installed() -> bool:
    """Check if Node.js and npm are available in the system path."""
    return bool(shutil.which("npm")) or bool(shutil.which("npm.cmd"))


def build_frontend(force: bool = False) -> None:
    """Build the React frontend using Vite if dist/ doesn't exist or force=True."""
    dist_dir = PROJECT_ROOT / "frontend" / "dist"
    
    if dist_dir.exists() and not force:
        print("Compiled frontend found. Skipping build step (use --force-build to rebuild).")
        return
        
    print("Building frontend React application...")
    if not verify_node_installed():
        print(
            "ERROR: Node.js and npm are required to build the frontend, but were not found in your PATH.\n"
            "Please install Node.js from https://nodejs.org/ and restart your terminal.",
            file=sys.stderr
        )
        sys.exit(1)
        
    frontend_dir = PROJECT_ROOT / "frontend"
    
    # Check if node_modules exists, if not run npm install
    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        print("Installing frontend npm dependencies...")
        subprocess.run("npm install", shell=True, cwd=str(frontend_dir), check=True)
        
    print("Compiling production assets with Vite...")
    subprocess.run("npm run build", shell=True, cwd=str(frontend_dir), check=True)
    print("Frontend build completed successfully!")


def open_browser_delayed(url: str, delay: float = 1.5) -> None:
    """Open the web browser after a short delay to let the server startup first."""
    time.sleep(delay)
    print(f"Opening browser at {url}...")
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Dubbing Lab Web UI.")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind uvicorn server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run uvicorn server on (default: 8000)",
    )
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="Force rebuilding of the React frontend before starting the server.",
    )
    args = parser.parse_args()
    
    # Initialize settings and inject FFmpeg path
    settings = get_settings()
    settings.apply_ffmpeg_path()
    settings.ensure_directories()
    
    # Build frontend
    try:
        build_frontend(force=args.force_build)
    except subprocess.CalledProcessError as err:
        print(f"ERROR: Frontend build failed: {err}", file=sys.stderr)
        sys.exit(1)
        
    url = f"http://{args.host}:{args.port}"
    print(f"Starting server at {url}...")
    
    # Start thread to open browser
    threading.Thread(
        target=open_browser_delayed,
        args=(url,),
        daemon=True
    ).start()
    
    # Launch uvicorn server
    import uvicorn
    uvicorn.run("app.api:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
