import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent


class AppHandler(SimpleHTTPRequestHandler):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, directory=str(ROOT), **kwargs)

	def do_GET(self):
		if self.path.split("?", 1)[0] == "/api/health":
			body = b'{"ok":true,"service":"success-institute"}'
			self.send_response(200)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			self.wfile.write(body)
			return
		if self.path == "/":
			self.path = "/index.html"
		super().do_GET()


if __name__ == "__main__":
	port = int(os.environ.get("PORT", "8000"))
	server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
	print(f"Success Institute server running on port {port}", flush=True)
	server.serve_forever()
