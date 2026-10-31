# nightbot-points-commandimport json
import os
import random
import time
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ==========================================
# CONFIGURATION
# ==========================================

DB_FILE = "points_db.json"
PORT = int(os.environ.get("PORT", 8080))

STARTING_POINTS = 100

WATCH_POINTS = 100
WATCH_INTERVAL = 300  # 5 minutes

DAILY_REWARD = 500
DAILY_COOLDOWN = 24 * 60 * 60

BONUS_MIN = 10
BONUS_MAX = 100
BONUS_COOLDOWN = 60 * 60

CHANCE_REWARD = 50
CHANCE_COOLDOWN = 30 * 60

DB_LOCK = threading.Lock()


# ==========================================
# DATABASE FUNCTIONS
# ==========================================

def load_db():
    if not os.path.exists(DB_FILE):
        return {}

    try:
        with open(DB_FILE, "r", encoding="utf-8") as file:
            content = file.read().strip()

            if not content:
                return {}

            return json.loads(content)

    except (OSError, json.JSONDecodeError) as error:
        print(f"Database loading error: {error}")
        raise


def save_db(data):
    temporary_file = DB_FILE + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)

    os.replace(temporary_file, DB_FILE)


def get_user(db, username):
    current_time = time.time()

    if username not in db:
        db[username] = {
            "points": STARTING_POINTS,
            "last_check": current_time,
            "last_daily": 0,
            "last_bonus": 0,
            "last_chance": 0,
        }

    account = db[username]

    account.setdefault("points", STARTING_POINTS)
    account.setdefault("last_check", current_time)
    account.setdefault("last_daily", 0)
    account.setdefault("last_bonus", 0)
    account.setdefault("last_chance", 0)

    return account


# ==========================================
# PASSIVE WATCH-TIME POINTS
# ==========================================

def update_passive_points(db, username, is_live):
    account = get_user(db, username)

    current_time = time.time()
    last_check = float(account["last_check"])

    elapsed = max(0, current_time - last_check)

    completed_blocks = int(elapsed // WATCH_INTERVAL)

    if completed_blocks > 0:

        if is_live:
            earned = completed_blocks * WATCH_POINTS

            account["points"] += earned

            account["last_check"] = (
                last_check + completed_blocks * WATCH_INTERVAL
            )

        else:
            account["last_check"] = current_time

    return account["points"]


# ==========================================
# NIGHTBOT HELPERS
# ==========================================

def get_username(handler):
    header = handler.headers.get("Nightbot-User", "")

    parameters = parse_qs(header)

    username = (
        parameters.get("name", [""])[0]
        .strip()
        .lower()
    )

    return username


def is_live_request(path):
    query = parse_qs(urlparse(path).query)

    value = query.get("live", [""])[0].lower()

    return value in ("1", "true", "yes", "live")


def format_time(seconds):
    seconds = max(0, int(seconds))

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"

    if minutes:
        return f"{minutes}m"

    return f"{seconds}s"


def respond(handler, message, status=200):
    content = str(message).encode("utf-8")

    handler.send_response(status)
    handler.send_header(
        "Content-Type",
        "text/plain; charset=utf-8"
    )
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()

    handler.wfile.write(content)


# ==========================================
# HTTP SERVER
# ==========================================

class PointsHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        username = get_username(self)

        # Health check
        if route == "/health":
            return respond(self, "ok")

        # Require Nightbot identity for user commands.
        if not username:
            return respond(
                self,
                "Error: Nightbot user identity was not provided.",
                400
            )

        # ======================================
        # BALANCE COMMAND
        # ======================================

        if route in ("/", "/balance"):

            target = (
                query.get("touser", [username])[0]
                .strip()
                .lower()
            )

            if not target:
                target = username

            with DB_LOCK:
                db = load_db()

                update_passive_points(
                    db,
                    username,
                    is_live_request(self.path)
                )

                points = update_passive_points(
                    db,
                    target,
                    is_live_request(self.path)
                )

                save_db(db)

            # Preserve the original root endpoint's integer response.
            if route == "/":
                return respond(self, str(points))

            return respond(
                self,
                f"@{username} you have {points:,} points."
            )

        # ======================================
        # DAILY REWARD
        # ======================================

        if route == "/daily":

            with DB_LOCK:
                db = load_db()
                account = get_user(db, username)

                current_time = time.time()

                elapsed = (
                    current_time - account["last_daily"]
                )

                if elapsed < DAILY_COOLDOWN:

                    remaining = DAILY_COOLDOWN - elapsed

                    message = (
                        f"@{username} you already claimed "
                        f"your daily reward. Try again in "
                        f"{format_time(remaining)}."
                    )

                else:

                    account["points"] += DAILY_REWARD

                    account["last_daily"] = current_time

                    message = (
                        f"@{username} received "
                        f"{DAILY_REWARD:,} daily points! "
                        f"Balance: {account['points']:,}."
                    )

                save_db(db)

            return respond(self, message)

        # ======================================
        # RANDOM BONUS
        # ======================================

        if route == "/bonus":

            with DB_LOCK:
                db = load_db()
                account = get_user(db, username)

                current_time = time.time()

                elapsed = (
                    current_time - account["last_bonus"]
                )

                if elapsed < BONUS_COOLDOWN:

                    remaining = BONUS_COOLDOWN - elapsed

                    message = (
                        f"@{username} your bonus is on "
                        f"cooldown. Try again in "
                        f"{format_time(remaining)}."
                    )

                else:

                    reward = random.randint(
                        BONUS_MIN,
                        BONUS_MAX
                    )

                    account["points"] += reward

                    account["last_bonus"] = current_time

                    message = (
                        f"@{username} earned a "
                        f"{reward}-point bonus! "
                        f"Balance: {account['points']:,}."
                    )

                save_db(db)

            return respond(self, message)

        # ======================================
        # CHANCE REWARD
        # ======================================

        if route == "/chance":

            with DB_LOCK:
                db = load_db()
                account = get_user(db, username)

                current_time = time.time()

                elapsed = (
                    current_time - account["last_chance"]
                )

                if elapsed < CHANCE_COOLDOWN:

                    remaining = CHANCE_COOLDOWN - elapsed

                    message = (
                        f"@{username} your chance reward "
                        f"is on cooldown. Try again in "
                        f"{format_time(remaining)}."
                    )

                else:

                    account["last_chance"] = current_time

                    if random.random() < 0.5:

                        account["points"] += CHANCE_REWARD

                        message = (
                            f"@{username} won "
                            f"{CHANCE_REWARD} bonus points! "
                            f"Balance: {account['points']:,}."
                        )

                    else:

                        message = (
                            f"@{username} didn't receive "
                            f"a bonus this time, but kept "
                            f"all their points. "
                            f"Balance: {account['points']:,}."
                        )

                save_db(db)

            return respond(self, message)

        # ======================================
        # LEADERBOARD
        # ======================================

        if route == "/leaderboard":

            with DB_LOCK:
                db = load_db()

                ranked = sorted(
                    [
                        (
                            name,
                            int(account.get("points", 0))
                        )
                        for name, account in db.items()
                        if isinstance(account, dict)
                    ],
                    key=lambda item: item[1],
                    reverse=True
                )[:5]

            if not ranked:

                message = "No points recorded yet."

            else:

                results = []

                for position, (name, points) in enumerate(
                    ranked,
                    start=1
                ):
                    results.append(
                        f"{position}. {name}: {points:,}"
                    )

                message = "Top points: " + " | ".join(results)

            return respond(self, message)

        # ======================================
        # UNKNOWN COMMAND
        # ======================================

        return respond(
            self,
            "Unknown command. Available commands: "
            "balance, daily, bonus, chance, leaderboard.",
            404
        )

    def log_message(self, format_string, *args):
        print(format_string % args)


# ==========================================
# START SERVER
# ==========================================

if __name__ == "__main__":

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        PointsHandler
    )

    print(f"Points API running on port {PORT}")

    server.serve_forever()
