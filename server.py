import json
import os
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Persistent storage: Supabase (Postgres via PostgREST).
#
# Render's FREE plan has an ephemeral filesystem — any local file (including a
# SQLite .db) is wiped every time the service redeploys, restarts, or spins
# down after 15 minutes idle. That was the cause of admin/student data
# "disappearing on its own". Supabase's free Postgres project lives outside
# Render entirely, so data survives restarts, redeploys, and spin-downs.
#
# Set these two environment variables on Render (Dashboard -> your service ->
# Environment):
#   SUPABASE_URL  -> https://xxxxxxxx.supabase.co   (Project Settings -> API)
#   SUPABASE_KEY  -> the "service_role" secret key   (Project Settings -> API)
#
# If they are not set (e.g. while developing on your own laptop), the server
# automatically falls back to a local SQLite file so you can still test
# offline — but remember that fallback does NOT persist on Render's free plan.
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

DEFAULT_STATE = {
	"courses": {
		"Computer Fundamentals": {
			"active": True,
			"questions": [
				{"q": "कंप्यूटर का brain किसे कहा जाता है?", "o": ["Monitor", "CPU", "Keyboard", "Mouse"], "a": 1},
				{"q": "1 Byte में कितने Bits होते हैं?", "o": ["4", "8", "16", "32"], "a": 1},
			],
		},
		"MS Office Basics": {"active": False, "questions": []},
		"Typing Mastery": {"active": False, "questions": []},
	},
	"users": {},
}


def _default_state():
	return json.loads(json.dumps(DEFAULT_STATE))


# ---------------------------------------------------------------------------
# Supabase (PostgREST) backend
# ---------------------------------------------------------------------------
def _supabase_request(method, path, body=None, extra_headers=None, timeout=10):
	url = f"{SUPABASE_URL}{path}"
	data = json.dumps(body).encode("utf-8") if body is not None else None
	req = urllib.request.Request(url, data=data, method=method)
	req.add_header("apikey", SUPABASE_KEY)
	req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
	req.add_header("Content-Type", "application/json")
	if extra_headers:
		for key, value in extra_headers.items():
			req.add_header(key, value)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			raw = resp.read()
			return json.loads(raw) if raw else None
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", "ignore")
		raise RuntimeError(f"Supabase {method} {path} failed: {exc.code} {detail}") from exc


def _supabase_read_state():
	rows = _supabase_request("GET", "/rest/v1/app_state?id=eq.1&select=data")
	if rows:
		return rows[0]["data"]
	return _default_state()


def _supabase_write_state(state):
	_supabase_request(
		"POST",
		"/rest/v1/app_state?on_conflict=id",
		body=[{"id": 1, "data": state}],
		extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
	)


# ---------------------------------------------------------------------------
# Local SQLite fallback (dev/offline only — NOT persistent on Render free plan)
# ---------------------------------------------------------------------------
if not USE_SUPABASE:
	import sqlite3

	DB_PATH = ROOT / "success_institute.db"

	def _init_local_db():
		with sqlite3.connect(DB_PATH) as conn:
			conn.execute(
				"CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)"
			)
			row = conn.execute("SELECT data FROM app_state WHERE id = 1").fetchone()
			if row is None:
				conn.execute(
					"INSERT INTO app_state (id, data) VALUES (1, ?)",
					(json.dumps(DEFAULT_STATE),),
				)
				conn.commit()

	def _local_read_state():
		with sqlite3.connect(DB_PATH) as conn:
			row = conn.execute("SELECT data FROM app_state WHERE id = 1").fetchone()
			if row is None:
				return _default_state()
			try:
				return json.loads(row[0])
			except (TypeError, ValueError):
				return _default_state()

	def _local_write_state(state):
		with sqlite3.connect(DB_PATH) as conn:
			conn.execute(
				"INSERT INTO app_state (id, data) VALUES (1, ?) "
				"ON CONFLICT(id) DO UPDATE SET data = excluded.data",
				(json.dumps(state),),
			)
			conn.commit()


# ---------------------------------------------------------------------------
# Public read/merge API (used by the HTTP handler below)
# ---------------------------------------------------------------------------
def read_state():
	try:
		if USE_SUPABASE:
			return _supabase_read_state()
		return _local_read_state()
	except Exception as exc:
		print("read_state failed, using in-memory defaults:", exc, flush=True)
		return _default_state()


def merge_state(payload):
	"""Read the freshest state and upsert only the keys present in payload.

	This is critical for correctness with many concurrent students: a client's
	in-memory copy of ALL courses/users can be stale by the time it saves, so we
	never blindly replace the whole 'courses' or 'users' dict. Instead we only
	touch the specific user(s)/course(s) included in this request, leaving
	everyone else's data exactly as-is. Deletions are explicit via
	delete_courses / delete_users so "not present" never means "delete".
	"""
	with LOCK:
		state = read_state()
		state.setdefault("courses", {})
		state.setdefault("users", {})

		incoming_users = payload.get("users")
		if isinstance(incoming_users, dict):
			for mobile, user in incoming_users.items():
				if isinstance(user, dict):
					state["users"][mobile] = user

		incoming_courses = payload.get("courses")
		if isinstance(incoming_courses, dict):
			for name, course in incoming_courses.items():
				if isinstance(course, dict):
					state["courses"][name] = course

		delete_courses = payload.get("delete_courses")
		if isinstance(delete_courses, list):
			for name in delete_courses:
				state["courses"].pop(name, None)

		delete_users = payload.get("delete_users")
		if isinstance(delete_users, list):
			for mobile in delete_users:
				state["users"].pop(mobile, None)

		if USE_SUPABASE:
			_supabase_write_state(state)
		else:
			_local_write_state(state)
		return state


class AppHandler(SimpleHTTPRequestHandler):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, directory=str(ROOT), **kwargs)

	def _send_json(self, status, payload):
		body = json.dumps(payload).encode("utf-8")
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		path = self.path.split("?", 1)[0]
		if path == "/api/health":
			self._send_json(200, {"ok": True, "service": "success-institute", "storage": "supabase" if USE_SUPABASE else "local-sqlite"})
			return
		if path == "/api/data":
			self._send_json(200, read_state())
			return
		if self.path == "/":
			self.path = "/index.html"
		super().do_GET()

	def do_POST(self):
		path = self.path.split("?", 1)[0]
		if path == "/api/data":
			try:
				length = int(self.headers.get("Content-Length", 0))
				raw = self.rfile.read(length) if length else b"{}"
				incoming = json.loads(raw or b"{}")
				if not isinstance(incoming, dict):
					raise ValueError("Payload must be a JSON object.")
				allowed_keys = {"courses", "users", "delete_courses", "delete_users"}
				if not any(k in incoming for k in allowed_keys):
					raise ValueError(
						"Payload must include at least one of: courses, users, delete_courses, delete_users."
					)
				new_state = merge_state(incoming)
				self._send_json(200, {"ok": True, "data": new_state})
			except Exception as exc:
				self._send_json(400, {"ok": False, "error": str(exc)})
			return
		self.send_error(404, "Not found")

	def log_message(self, fmt, *args):
		# keep Render logs readable
		print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
	if USE_SUPABASE:
		print("Persistent storage: Supabase (data will survive restarts/redeploys).", flush=True)
	else:
		print(
			"WARNING: SUPABASE_URL/SUPABASE_KEY not set — falling back to local SQLite. "
			"On Render's free plan this file is WIPED on every restart/redeploy/spin-down. "
			"Set the two env vars to make data permanent.",
			flush=True,
		)
		_init_local_db()
	port = int(os.environ.get("PORT", "8000"))
	server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
	print(f"Success Institute server running on port {port}", flush=True)
	server.serve_forever()