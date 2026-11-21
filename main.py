import os

from flask import Flask

app = Flask(__name__)

@app.get("/")
@app.get("/health")
@app.get("/healthz")
def health():
    return "Aura web-only cutover bridge", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
