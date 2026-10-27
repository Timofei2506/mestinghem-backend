"""HTTP routers."""
from . import admin, agent, auth, chat, chess, documents, files, images, legal, terminal

ROUTERS = [
    auth.router,
    chat.router,
    files.router,
    images.router,
    documents.router,
    chess.router,
    terminal.router,
    agent.router,
    admin.router,
    admin.admin,
    legal.router,
]

__all__ = ["ROUTERS"]
