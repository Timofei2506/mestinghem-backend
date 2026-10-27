# Mestinghem AI — backend

FastAPI backend: AI chat, documents (PPTX/XLSX/DOCX), image generation, file
workspace (Postgres bytea storage), sandboxed PTY terminal, Stockfish chess and
the device agent API. Built and run via the included Dockerfile.

- Runtime: Python 3.13 + Stockfish (apt)
- Entry: `uvicorn app.main:app` on `$PORT`
- Config via environment variables (DATABASE_URL, GROQ_API_KEY, APP_SECRET_KEY, ...)
