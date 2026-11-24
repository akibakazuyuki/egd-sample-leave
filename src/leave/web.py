"""最小のWeb画面（標準ライブラリのみ）。PYTHONPATH=src python3 -m leave.web で起動。"""
import os
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from leave.service import LeaveError, LeaveService
from leave.store import demo_store

STORE = demo_store()
JST = timezone(timedelta(hours=9))


def today() -> date:
    """申請のたびに日本時間の今日を返す。LEAVE_TODAY はデモ・証拠取得用の固定値。"""
    fixed = os.environ.get("LEAVE_TODAY")
    return date.fromisoformat(fixed) if fixed else datetime.now(JST).date()


SERVICE = LeaveService(STORE, today=today)


def page(message: str = "", ok: bool = True) -> bytes:
    rows = "".join(
        f"<tr><td>{escape(e.id)}</td><td>{escape(e.name)}</td><td id='bal-{escape(e.id)}'>{e.balance:g}</td>"
        f"<td>{', '.join(d.isoformat() for d in sorted(SERVICE.applied_dates(e.id))) or '-'}</td></tr>"
        for e in STORE.employees.values())
    options = "".join(f"<option value='{escape(e.id)}'>{escape(e.id)} {escape(e.name)}</option>"
                      for e in STORE.employees.values())
    note = (f"<p id='message' style='color:{'green' if ok else 'crimson'}'>{escape(message)}</p>"
            if message else "")
    html = f"""<!doctype html><meta charset=utf-8><title>有給休暇</title>
<style>body{{font-family:sans-serif;margin:24px}}td,th{{padding:4px 10px}}</style>
<h1>有給休暇の申請</h1><p>今日：{SERVICE.today.isoformat()}</p>{note}
<form method=post>
従業員 <select name=emp>{options}</select>
開始日 <input type=date name=start required>
終了日 <input type=date name=end>
<select name=amount><option value=1>1日</option><option value=0.5>半日</option></select>
<button type=submit>申請</button></form>
<h2>従業員一覧</h2><table border=1><tr><th>ID</th><th>氏名</th><th>有給残日数</th><th>申請済みの日</th></tr>{rows}</table>"""
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(page())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8")).items()}
        try:
            start = date.fromisoformat(form["start"])
            end = date.fromisoformat(form["end"]) if form.get("end") else None
            days = SERVICE.apply(form["emp"], start, end, amount=float(form.get("amount", 1)))
            self._send(page(f"{days:g}日の申請を受け付けました", ok=True))
        except LeaveError as e:
            self._send(page(str(e), ok=False))
        except (KeyError, ValueError):
            self._send(page("入力を確認してください", ok=False))

    def log_message(self, *args):
        pass


def main(port: int = int(os.environ.get("PORT", 8000))) -> None:
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
