import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

class SlackWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        sys.stdout.write("\n=== SLACK WEBHOOK RECEIVED ===\n")
        try:
            payload = json.loads(body)
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
            if "attachments" in payload:
                for att in payload["attachments"]:
                    if "actions" in att:
                        for action in att["actions"]:
                            if action.get("type") == "button":
                                sys.stdout.write(f"\n🔗 Chatbot URL: {action['url']}\n")
        except Exception as e:
            sys.stdout.write(f"Parse error: {e}\n")
            sys.stdout.write(body.decode() + "\n")
        sys.stdout.write("==============================\n")
        sys.stdout.flush()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    port = 5000
    sys.stdout.write(f"Slack mock webhook server running on http://localhost:{port}\n")
    sys.stdout.write("Send alerts here to see the chatbot links in console.\n")
    sys.stdout.flush()
    HTTPServer(("0.0.0.0", port), SlackWebhookHandler).serve_forever()
