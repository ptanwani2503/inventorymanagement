"""Web chat app.  Start it with:  python app.py   then open http://127.0.0.1:8000"""
import asyncio
import sys

if sys.platform == "win32":  # Windows needs this loop type to launch the MCP server subprocess
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent import InventoryAgent
from config import DB_PATH, LLM_MODEL

HERE = Path(__file__).resolve().parent
sessions: dict[str, list[dict]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = InventoryAgent()
    await agent.start()  # launches mcp_server.py once and connects to it
    app.state.agent = agent
    yield
    await agent.close()


app = FastAPI(title="Inventory Agent", lifespan=lifespan)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = "default"


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "model": LLM_MODEL, "db": str(DB_PATH), "tools": len(app.state.agent.tools)}


@app.post("/api/chat")
async def chat(body: ChatIn):
    history = sessions.setdefault(body.session_id, [])
    reply, trace = await app.state.agent.chat(body.message, history)
    return {"reply": reply, "tools_used": trace}


@app.post("/api/reset")
async def reset(session_id: str = "default"):
    sessions.pop(session_id, None)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, loop="asyncio")
