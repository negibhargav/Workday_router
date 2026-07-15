"""
Workday OAuth2 Token Manager — Authorization Code flow.

USAGE
-----
1. First-time setup (opens browser, captures code, saves tokens):
       python workday_auth.py login

2. Get a valid token in your code (auto-refreshes if expired):
       from workday_auth import get_valid_token
       token = get_valid_token()

3. Force a refresh manually:
       python workday_auth.py refresh

ENV VARS (put these in your .env)
-----------------------------------
    WORKDAY_CLIENT_ID      — your OAuth2 client ID
    WORKDAY_CLIENT_SECRET  — your OAuth2 client secret  ← ROTATE THIS NOW
    WORKDAY_AUTH_URL       — authorize endpoint
    WORKDAY_TOKEN_URL      — token endpoint
    WORKDAY_REDIRECT_URI   — must match what's registered (default: http://localhost:8742/callback)
    WORKDAY_TOKEN_FILE     — where to store tokens (default: .workday_tokens.json)
"""

import json
import os
import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from datetime import datetime

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# PROJECT ROOT — anchor all file paths absolutely so they resolve correctly
# regardless of which directory the process (e.g. MCP server) is launched from.
# ---------------------------------------------------------------------------
_HERE         = os.path.dirname(os.path.abspath(__file__))        # src/tools/
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # project root

# Load .env from project root explicitly
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
CLIENT_ID     = os.getenv("WORKDAY_CLIENT_ID",     "MDgwNWJmMDMtNzFmMC00OGU0LTk2ZmEtY2UxZWY1NGVkZmUz")
CLIENT_SECRET = os.getenv("WORKDAY_CLIENT_SECRET",  "REPLACE_ME_AFTER_ROTATING")
AUTH_URL      = os.getenv("WORKDAY_AUTH_URL",       "https://wcpdev.wd101.myworkday.com/wday/authgwy/jll_wcpdev1/authorize")
TOKEN_URL     = os.getenv("WORKDAY_TOKEN_URL",      "https://wcpdev-services1.wd101.myworkday.com/ccx/oauth2/jll_wcpdev1/token")
REDIRECT_URI  = os.getenv("WORKDAY_REDIRECT_URI",   "http://localhost:8742/callback")

# Default token file anchored to project root — works from any CWD
_default_token_file = os.path.join(_PROJECT_ROOT, ".workday_tokens.json")
TOKEN_FILE    = os.getenv("WORKDAY_TOKEN_FILE", _default_token_file)

# How many seconds before expiry to proactively refresh (default: 5 min)
REFRESH_BUFFER_SECS = int(os.getenv("WORKDAY_REFRESH_BUFFER", "300"))

# ---------------------------------------------------------------------------
# TOKEN STORAGE
# ---------------------------------------------------------------------------
def update_env_token(access_token: str, env_file: str = None) -> None:
    """
    Write/update the `WORKDAY_API_TOKEN` variable in the .env file.
    - If `WORKDAY_API_TOKEN=...` already exists → replaces that line in-place.
    - If not found → appends it at the end.
    """
    if env_file is None:
        env_file = os.path.join(_PROJECT_ROOT, ".env")
    new_line = f'WORKDAY_API_TOKEN="{access_token}"\n'

    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            # Match `WORKDAY_API_TOKEN=...` exactly
            if line.strip().startswith("WORKDAY_API_TOKEN=") and line.split("=")[0].strip() == "WORKDAY_API_TOKEN":
                lines[i] = new_line
                updated = True
                break

        with open(env_file, "w") as f:
            f.writelines(lines)
            if not updated:
                # Ensure file ends with newline before appending
                if lines and not lines[-1].endswith("\n"):
                    f.write("\n")
                f.write(new_line)
    else:
        with open(env_file, "w") as f:
            f.write(new_line)

    print(f"[workday_auth] .env updated: WORKDAY_API_TOKEN=...{access_token[-12:]}", file=sys.stderr)


def log_refresh(action_type: str, token_data: dict) -> None:
    """Logs the token refresh event to the log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved_at_str = datetime.fromtimestamp(token_data.get("saved_at", time.time())).strftime("%Y-%m-%d %H:%M:%S")
    expires_at = token_data.get("expires_at")
    expires_at_str = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S") if expires_at else "N/A"
    
    # Extract access token suffix securely
    access_token = token_data.get("access_token", "")
    token_suffix = f"...{access_token[-12:]}" if len(access_token) >= 12 else "N/A"
    
    log_entry = (
        f"[{timestamp}] ACTION: {action_type}\n"
        f"  - Saved At: {saved_at_str}\n"
        f"  - Expires At: {expires_at_str}\n"
        f"  - Expires In: {token_data.get('expires_in', 'unknown')} seconds\n"
        f"  - Access Token (suffix): {token_suffix}\n"
        f"  - Scope: {token_data.get('scope', 'unknown')}\n"
        f"  - Token Type: {token_data.get('token_type', 'unknown')}\n"
        f"{'-'*60}\n"
    )
    
    # Append to log file
    _default_log_file = os.path.join(_PROJECT_ROOT, "src", "log", "workday_refresh.log")
    log_file = os.getenv("WORKDAY_REFRESH_LOG_FILE", _default_log_file)
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a") as f:
            f.write(log_entry)
        print(f"[workday_auth] Refresh event logged to {log_file}", file=sys.stderr)
    except Exception as e:
        print(f"[workday_auth] Failed to write refresh log: {e}", file=sys.stderr)


def get_jwt_exp(token: str) -> int | None:
    """Safely decodes JWT payload to extract 'exp' claim without external libraries."""
    import base64
    import json
    try:
        parts = token.split(".")
        if len(parts) == 3:
            payload = parts[1]
            # Add padding to base64 string if necessary
            padded = payload + "=" * (4 - len(payload) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
            data = json.loads(decoded)
            return data.get("exp")
    except Exception:
        pass
    return None


def save_tokens(token_data: dict, action_type: str = "Token Update") -> None:
    """Persist tokens to disk with an absolute expiry timestamp and log the event."""
    token_data["saved_at"] = time.time()
    
    expires_in = token_data.get("expires_in")
    if expires_in is not None:
        token_data["expires_at"] = token_data["saved_at"] + int(expires_in)
    else:
        # Fallback 1: Extract exp from access_token JWT
        exp = get_jwt_exp(token_data.get("access_token", ""))
        if exp:
            token_data["expires_at"] = float(exp)
            token_data["expires_in"] = int(exp - token_data["saved_at"])
        else:
            # Fallback 2: Default to 3600 (1 hour)
            token_data["expires_in"] = 3600
            token_data["expires_at"] = token_data["saved_at"] + 3600

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)  # owner read/write only
    print(f"[workday_auth] Tokens saved to {TOKEN_FILE}", file=sys.stderr)
    update_env_token(token_data["access_token"])
    log_refresh(action_type, token_data)


def load_tokens() -> dict | None:
    """Load tokens from disk. Returns None if file missing or corrupt."""
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def is_token_expired(tokens: dict) -> bool:
    """True if access_token is expired or will expire within REFRESH_BUFFER_SECS."""
    expires_at = tokens.get("expires_at")
    if not expires_at:
        return True  # no expiry info → assume expired, be safe
    return time.time() >= (expires_at - REFRESH_BUFFER_SECS)


# ---------------------------------------------------------------------------
# STEP 1 — Local callback server to capture the auth code automatically
# ---------------------------------------------------------------------------
_captured_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _captured_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "error" in params:
            error = params["error"][0]
            desc  = params.get("error_description", [""])[0]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Auth error: {error} — {desc}".encode())
            print(f"[workday_auth] Auth error: {error} — {desc}", file=sys.stderr)
            return

        if "code" in params:
            _captured_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Authorised. You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code param.")

    def log_message(self, *args):
        pass  # silence HTTP server logs


def _start_callback_server(port: int) -> HTTPServer:
    server = HTTPServer(("localhost", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# STEP 2 — Exchange auth code for tokens
# ---------------------------------------------------------------------------
def exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# STEP 3 — Refresh access token using refresh_token
# ---------------------------------------------------------------------------
def refresh_access_token(refresh_token: str) -> dict:
    print("[workday_auth] Refreshing access token...", file=sys.stderr)
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()
    print("[workday_auth] Token refreshed successfully.", file=sys.stderr)
    return tokens


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
def login() -> str:
    """
    Full browser-based login flow.
    Opens the Workday auth URL, waits for the callback, exchanges the code,
    saves tokens, and returns the access_token.
    """
    global _captured_code

    parsed = urlparse(REDIRECT_URI)
    port = parsed.port or 8742

    server = _start_callback_server(port)

    auth_params = urlencode({
        "response_type": "code",
        "client_id":     CLIENT_ID,
    })
    full_auth_url = f"{AUTH_URL}?{auth_params}"

    print(f"[workday_auth] Opening browser for login...", file=sys.stderr)
    print(f"[workday_auth] If browser doesn't open, visit:\n  {full_auth_url}", file=sys.stderr)
    webbrowser.open(full_auth_url)

    # Wait up to 120s for the callback
    deadline = time.time() + 120
    while _captured_code is None and time.time() < deadline:
        time.sleep(0.2)

    # Shut down the background server loop and close socket
    server.shutdown()
    server.server_close()

    if _captured_code is None:
        raise TimeoutError("No auth code received within 120 seconds.")

    code = _captured_code
    _captured_code = None  # reset for next call

    print("[workday_auth] Auth code captured. Exchanging for tokens...", file=sys.stderr)
    tokens = exchange_code(code)
    save_tokens(tokens, "Interactive Login")
    return tokens["access_token"]


def get_valid_token() -> str:
    """
    Returns a valid access_token.
    - If stored token is still valid → return it directly.
    - If expired but refresh_token exists → refresh and return new token.
    - If no tokens on disk or refresh fails → automatically triggers login() (browser interactive window).
    """
    tokens = load_tokens()

    # 1. No tokens found on disk -> Trigger interactive browser login
    if tokens is None:
        if os.getenv("WORKDAY_NON_INTERACTIVE") == "true":
            raise RuntimeError("No Workday tokens found on disk and running in non-interactive mode. Please run: python src/tools/Refresh_token.py login")
        print("[workday_auth] No tokens found on disk. Launching login window...", file=sys.stderr)
        return login()

    # 2. Token still valid -> Return it
    if not is_token_expired(tokens):
        return tokens["access_token"]

    # 3. Attempt silent refresh using refresh_token
    refresh_token = tokens.get("refresh_token")
    if refresh_token:
        try:
            new_tokens = refresh_access_token(refresh_token)
            # Workday may or may not return a new refresh_token — preserve old one if not
            if "refresh_token" not in new_tokens:
                new_tokens["refresh_token"] = refresh_token
            save_tokens(new_tokens, "Silent Auto-Refresh")
            return new_tokens["access_token"]
        except Exception as e:
            print(f"[workday_auth] Silent refresh failed: {e}.", file=sys.stderr)
            if os.getenv("WORKDAY_NON_INTERACTIVE") == "true":
                raise RuntimeError(f"Workday silent refresh failed: {e}. Please run: python src/tools/Refresh_token.py login") from e
            print("[workday_auth] Launching login window...", file=sys.stderr)

    # 4. Refresh token missing or refresh failed -> Trigger interactive browser login
    if os.getenv("WORKDAY_NON_INTERACTIVE") == "true":
        raise RuntimeError("Workday refresh token is missing/expired and running in non-interactive mode. Please run: python src/tools/Refresh_token.py login")
    return login()


def force_refresh() -> str:
    """
    Force a refresh of the access token using the stored refresh token.
    Updates the stored tokens and the .env file.
    """
    tokens = load_tokens()
    if not tokens:
        if os.getenv("WORKDAY_NON_INTERACTIVE") == "true":
            raise RuntimeError("No Workday tokens found on disk for forced refresh. Please run: python src/tools/Refresh_token.py login")
        print("[workday_auth] No tokens found on disk for forced refresh. Triggering login...", file=sys.stderr)
        return login()

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        if os.getenv("WORKDAY_NON_INTERACTIVE") == "true":
            raise RuntimeError("No Workday refresh token found on disk for forced refresh. Please run: python src/tools/Refresh_token.py login")
        print("[workday_auth] No refresh token found on disk for forced refresh. Triggering login...", file=sys.stderr)
        return login()

    try:
        new_tokens = refresh_access_token(refresh_token)
        if "refresh_token" not in new_tokens:
            new_tokens["refresh_token"] = refresh_token
        save_tokens(new_tokens, "Forced Refresh")
        return new_tokens["access_token"]
    except Exception as e:
        if os.getenv("WORKDAY_NON_INTERACTIVE") == "true":
            raise RuntimeError(f"Forced refresh failed: {e}. Please run: python src/tools/Refresh_token.py login") from e
        print(f"[workday_auth] Forced refresh failed: {e}. Launching login window...", file=sys.stderr)
        return login()


def token_info() -> None:
    """Print current token status."""
    tokens = load_tokens()
    if not tokens:
        print("No tokens on disk.")
        return

    expires_at = tokens.get("expires_at")
    if expires_at:
        remaining = expires_at - time.time()
        status = (
            f"expires in {int(remaining)}s"
            if remaining > 0
            else f"EXPIRED {int(-remaining)}s ago"
        )
        saved = datetime.fromtimestamp(tokens["saved_at"]).strftime("%Y-%m-%d %H:%M:%S")
        print(f"access_token : ...{tokens['access_token'][-12:]}")
        print(f"refresh_token: {'yes' if tokens.get('refresh_token') else 'no'}")
        print(f"status       : {status}")
        print(f"saved at     : {saved}")
    else:
        print("Token present but no expiry info.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"

    if cmd == "login":
        token = login()
        print(f"\nAccess token: ...{token[-12:]}")

    elif cmd == "refresh":
        tokens = load_tokens()
        if not tokens or not tokens.get("refresh_token"):
            print("No refresh_token on disk. Run: python workday_auth.py login")
            sys.exit(1)
        new_tokens = refresh_access_token(tokens["refresh_token"])
        if "refresh_token" not in new_tokens:
            new_tokens["refresh_token"] = tokens["refresh_token"]
        save_tokens(new_tokens, "CLI Manual Refresh")
        print(f"Refreshed. New token: ...{new_tokens['access_token'][-12:]}")

    elif cmd == "info":
        token_info()

    else:
        print("Usage: python workday_auth.py [login | refresh | info]")
        sys.exit(1)
