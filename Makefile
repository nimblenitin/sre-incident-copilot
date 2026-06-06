.PHONY: all build deploy test clean runbook

all: runbook build deploy test

runbook:
	python create_runbook_index.py

build:
	docker build -t inference-api:latest inference-api/
	kind load docker-image inference-api:latest --name alert-chatbot || true

deploy:
	kubectl --context kind-alert-chatbot apply -f k8s/

test:
	./test_e2e.sh

chatbot:
	streamlit run alert_app.py --server.port 8501

slack-mock:
	python -c "
	from http.server import HTTPServer, BaseHTTPRequestHandler
	import json
	class H(BaseHTTPRequestHandler):
		def do_POST(self):
			n = int(self.headers.get('Content-Length', 0))
			print('=== SLACK WEBHOOK ===')
			print(json.dumps(json.loads(self.rfile.read(n)), indent=2))
			print('======================')
			self.send_response(200)
			self.end_headers()
			self.wfile.write(b'ok')
	HTTPServer(('0.0.0.0', 5000), H).serve_forever()
	"

simulate:
	python simulate_alert.py

clean:
	-kind delete cluster --name alert-chatbot
	-rm -rf runbook_index/
