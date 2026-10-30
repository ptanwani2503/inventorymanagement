"""LLM + MCP orchestration. Run a terminal chat with:  python agent.py"""
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import litellm
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import BASE_DIR, DB_PATH, LLM_MODEL

litellm.drop_params = True  # ignore params a provider doesn't support instead of erroring
MAX_STEPS = 6            # max tool-call rounds per user message
KEEP_USER_TURNS = 6      # history window (keeps token usage under free-tier limits)
MAX_TOOL_CHARS = 6000

SYSTEM = """You are an inventory management assistant for a store.
- ALWAYS use the provided tools to get facts. Never guess prices, SKUs or quantities.
- Show lists as a compact markdown table: SKU | Product | Variant | Price (₹) | Qty | Status.
- Stock rules: OUT_OF_STOCK = quantity 0; LOW_STOCK = quantity <= reorder level; otherwise IN_STOCK.
- When calling tools pass prices as plain numbers (799, not ₹799) and quantities as integers.
- For updates/additions, confirm exactly what changed (old -> new quantity, new SKU, price, stock).
- If a SKU isn't found, say so and offer the similar SKUs the tool returned.
- If an add-product request lacks category, name, SKU, price or quantity, ask for the missing field."""


def _trim(history: list[dict]) -> list[dict]:
    users = [i for i, m in enumerate(history) if m["role"] == "user"]
    return history[users[-KEEP_USER_TURNS]:] if len(users) > KEEP_USER_TURNS else history


class InventoryAgent:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tools: list[dict] = []

    async def start(self) -> None:
        # cwd + inherited env => the server subprocess sees the same .env / DB path as this process
        params = StdioServerParameters(
            command=sys.executable, args=[str(BASE_DIR / "mcp_server.py")], cwd=str(BASE_DIR), env=None
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description or "",
                                              "parameters": t.inputSchema}}
            for t in listed.tools
        ]

    async def close(self) -> None:
        if self._stack:
            await self._stack.aclose()

    async def _complete(self, messages: list[dict]):
        for attempt in (1, 2):
            try:
                return await litellm.acompletion(
                    model=LLM_MODEL, messages=messages, tools=self.tools,
                    tool_choice="auto", temperature=0, max_tokens=4096)
            except litellm.BadRequestError as e:
                flaky = "tool_use_failed" in str(e) or "Failed to call a function" in str(e)
                if attempt == 1 and flaky:  # Llama occasionally emits a malformed tool call; retry once
                    continue
                raise

    @staticmethod
    def _friendly(e: Exception) -> str:
        text = str(e)
        if isinstance(e, litellm.AuthenticationError):
            return ("LLM authentication failed. Put your key in .env as GROQ_API_KEY=gsk_... "
                    "(and restart). Details: " + text[:200])
        if isinstance(e, litellm.RateLimitError):
            return "The LLM rate limit was hit. Wait ~30 seconds and try again."
        if "LLM Provider NOT provided" in text:
            return f"Model name '{LLM_MODEL}' has no provider prefix. Use GROQ_MODEL=openai/gpt-oss-120b."
        return f"LLM error ({type(e).__name__}): {text[:300]}"

    async def _run_tool(self, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments: {raw_args[:200]}"})
        try:
            result = await self.session.call_tool(name, args)
            text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        except Exception as e:  # tool/transport failure goes back to the model, not a crash
            text = json.dumps({"error": f"{type(e).__name__}: {e}"})
        return text[:MAX_TOOL_CHARS]

    async def chat(self, message: str, history: list[dict]) -> tuple[str, list[dict]]:
        """Returns (reply, tool_trace). `history` is mutated in place (per-session)."""
        history.append({"role": "user", "content": message})
        trace: list[dict] = []
        for _ in range(MAX_STEPS):
            try:
                resp = await self._complete([{"role": "system", "content": SYSTEM}] + _trim(history))
            except Exception as e:
                return self._friendly(e), trace
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            entry: dict = {"role": "assistant", "content": msg.content or ("" if not calls else None)}
            if calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                    for c in calls]
            history.append(entry)
            if not calls:
                return msg.content or "(no response)", trace
            for c in calls:
                out = await self._run_tool(c.function.name, c.function.arguments)
                trace.append({"tool": c.function.name, "arguments": c.function.arguments})
                history.append({"role": "tool", "tool_call_id": c.id, "content": out})
        return "I couldn't finish that request in a reasonable number of steps. Please rephrase.", trace


async def _cli() -> None:
    agent = InventoryAgent()
    await agent.start()
    print(f"Model: {LLM_MODEL} | DB: {DB_PATH}\nTools: {', '.join(t['function']['name'] for t in agent.tools)}\n(type 'exit')\n")
    history: list[dict] = []
    try:
        while True:
            q = (await asyncio.to_thread(input, "You> ")).strip()
            if q.lower() in {"exit", "quit"}:
                break
            if q:
                reply, trace = await agent.chat(q, history)
                for t in trace:
                    print(f"  [tool] {t['tool']}({t['arguments']})")
                print(f"\nAgent> {reply}\n")
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(_cli())