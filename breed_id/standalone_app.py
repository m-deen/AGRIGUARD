"""
Standalone Breed Identification API (NOT part of AgriGuard app.py).

Run (from this folder, using breed_id/.venv):
    .\\.venv\\Scripts\\python.exe standalone_app.py

Then open:
    http://localhost:5001/
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from breed_identification import identify_breed_from_photo, BREED_CARE_LIBRARY
from model_loader import load_model, load_class_names, model_predict_fn

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT))
CORS(app, origins=["*"])

# Load model at startup so first request is faster
try:
    load_model()
except Exception as e:
    print(f"[WARN] Model not loaded at startup: {e}")


@app.get("/")
def index():
    return send_from_directory(ROOT, "demo.html")


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "module": "breed_id_standalone",
        "classes": load_class_names(),
    })


@app.get("/api/breed/supported")
def supported():
    classes = load_class_names()
    data = []
    for name in classes:
        care = BREED_CARE_LIBRARY.get(name, {})
        data.append({
            "breed_name": name,
            "species": care.get("species"),
            "in_model": True,
        })
    return jsonify({"status": "success", "data": data})


@app.post("/api/breed/identify")
def identify():
    """
    multipart/form-data field: image (JPEG/PNG, max 5MB)
    Matches the format AgriGuard will use later when integrating.
    """
    f = request.files.get("image") or request.files.get("file")
    if not f:
        return jsonify({
            "success": False,
            "error": "No image uploaded. Use form field 'image'."
        }), 400

    file_bytes = f.read()
    result = identify_breed_from_photo(file_bytes, model_predict_fn)
    status = 200 if result.get("success") else 400
    return jsonify(result), status


if __name__ == "__main__":
    port = int(os.getenv("BREED_PORT", "5001"))
    print(f"Breed ID standalone → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
