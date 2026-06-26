# Doc Management React UI

This app replaces the Streamlit UI with React and talks to a Python API server.

## 1) Start backend API

From repo root:

```bash
pip install -r doc-management/frontend/requirements.txt
python -m uvicorn doc-management.frontend.api_server:app --host 127.0.0.1 --port 8001 --reload
```

## 2) Start React app

In a second terminal:

```bash
cd doc-management/frontend/react-app
npm install
npm run dev
```

The UI runs at `http://127.0.0.1:5173` and calls `http://127.0.0.1:8001` by default.

## Optional API base override

Set `VITE_DM_API_BASE` if API host/port differs:

```bash
VITE_DM_API_BASE=http://127.0.0.1:9000 npm run dev
```
