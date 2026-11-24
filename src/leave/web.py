"""最小のWeb画面（標準ライブラリのみ）。python -m leave.web で起動。"""
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

from leave.store import demo_store

STORE = demo_store()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        rows = "".join(
            f"<tr><td>{escape(e.id)}</td><td>{escape(e.name)}</td><td>{e.balance}</td></tr>"
            for e in STORE.employees.values())
        body = (f"<!doctype html><meta charset=utf-8><title>有給休暇</title>"
                f"<h1>従業員一覧</h1><table border=1><tr><th>ID</th><th>氏名</th>"
                f"<th>有給残日数</th></tr>{rows}</table>")
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main(port: int = 8000) -> None:
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
