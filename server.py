import json
import os
import sqlite3
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "success_institute.db"
LOCK = threading.Lock()

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


def init_db():
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


def read_state():
	with LOCK, sqlite3.connect(DB_PATH) as conn:
		row = conn.execute("SELECT data FROM app_state WHERE id = 1").fetchone()
		if row is None:
			return DEFAULT_STATE
		try:
			return json.loads(row[0])
		except (TypeError, ValueError):
			return DEFAULT_STATE


def merge_state(payload):
	"""Read the freshest state and upsert only the keys present in payload.

	This is critical for correctness with many concurrent students: a client's
	in-memory copy of ALL courses/users can be stale by the time it saves, so we
	never blindly replace the whole 'courses' or 'users' dict. Instead we only
	touch the specific user(s)/course(s) included in this request, leaving
	everyone else's data exactly as-is. Deletions are explicit via
	delete_courses / delete_users so "not present" never means "delete".
	"""
	with LOCK, sqlite3.connect(DB_PATH) as conn:
		row = conn.execute("SELECT data FROM app_state WHERE id = 1").fetchone()
		try:
			state = json.loads(row[0]) if row else json.loads(json.dumps(DEFAULT_STATE))
		except (TypeError, ValueError):
			state = json.loads(json.dumps(DEFAULT_STATE))
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

		conn.execute(
			"INSERT INTO app_state (id, data) VALUES (1, ?) "
			"ON CONFLICT(id) DO UPDATE SET data = excluded.data",
			(json.dumps(state),),
		)
		conn.commit()
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
			self._send_json(200, {"ok": True, "service": "success-institute"})
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
	init_db()
	port = int(os.environ.get("PORT", "8000"))
	server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
	print(f"Success Institute server running on port {port}", flush=True)
	server.serve_forever()