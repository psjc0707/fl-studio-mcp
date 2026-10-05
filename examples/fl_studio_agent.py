"""
FL Studio Agent — Opus 5.5 API + Bridge HTTP

A full bidirectional loop where you say what you want in plain Portuguese,
Opus 5.5 decides which FL Studio tools to call, executes them through the
local Bridge, and reports back.

Setup:
    1) Bridge running:    python fl_bridge.py    (port 8767)
    2) Gateway running:   python fl_mcp.py       (port 8779)
    3) Opus 5.5 key in env:  set ANTHROPIC_API_KEY=sk-ant-...
    4) pip install anthropic requests
    5) python fl_studio_agent.py

Then just type natural commands at the prompt.
"""

import json
import os
import sys
import time
from typing import Any

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("pip install anthropic")
    sys.exit(1)


GATEWAY_URL = os.environ.get("FL_GATEWAY", "http://localhost:8779")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("FL_MODEL", "claude-opus-4-5")


def load_tools() -> list[dict]:
    """Fetch the tool catalog from the gateway."""
    r = requests.get(f"{GATEWAY_URL}/tools", timeout=5)
    r.raise_for_status()
    return r.json()["tools"]


def call_tool(name: str, arguments: dict) -> dict:
    """Invoke a tool via the gateway."""
    r = requests.post(
        f"{GATEWAY_URL}/call",
        json={"name": name, "arguments": arguments},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["result"]


SYSTEM_PROMPT = """Voce é um agente de produção musical que controla o FL Studio.

Voce tem acesso a 17 tools via REST em {gateway}.

REGRAS:
- Seja direto. Use as tools quando precisar agir.
- Para compor, use /piano/add_chord (mais sofisticado) ou /piano/arp.
- Para mixar, use /mixer/volume, /mixer/pan, /mixer/mute.
- Para transporte (tocar/parar/recordar), use /transport/play, /stop, /record.
- Para BPM, use /transport/bpm.
- Para ler arquivo .flp, use /project/inspect com path.
- Para carregar projeto, use /project/open com path.
- Para ver o estado atual, use /state (você pode chamá-la quando precisar).
- Se uma tool falhar, tente outra abordagem.
- Sempre responda em português brasileiro, curto e claro.

O usuario vai te pedir coisas como:
  "Cria uma progressão 2-5-1 em Dó maior"
  "Mix: baixa 3dB nas tracks 5 e 7"
  "Toca e para o projeto"
  "Inspeciona o projeto atual"
  "Sobe BPM pra 140"

Quando o usuario pedir, escolha as tools certas e invoque."""


def run_agent(user_input: str) -> str:
    """Run one Opus 5.5 turn with tool support."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    tools = load_tools()

    messages = [{"role": "user", "content": user_input}]

    # Initial Opus 5.5 call
    print("[opus 5.5] thinking...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT.format(gateway=GATEWAY_URL),
        tools=tools,
        messages=messages,
    )

    # Agentic loop: handle tool calls until Opus 5.5 is done
    while response.stop_reason == "tool_use":
        tool_uses = [c for c in response.content if c.type == "tool_use"]
        tool_results = []
        for tool_use in tool_uses:
            print(f"[opus 5.5] calling {tool_use.name}({json.dumps(tool_use.input)[:80]}...)")
            try:
                result = call_tool(tool_use.name, tool_use.input)
                ok_str = json.dumps(result, default=str)[:200]
                print(f"[bridge] -> {ok_str}")
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
                print(f"[bridge] -> ERROR: {exc}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result, default=str),
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT.format(gateway=GATEWAY_URL),
            tools=tools,
            messages=messages,
        )

    # Extract final text
    final = ""
    for block in response.content:
        if hasattr(block, "text"):
            final += block.text
    return final


def main():
    if not ANTHROPIC_KEY:
        print("set ANTHROPIC_API_KEY first")
        sys.exit(1)

    print(f"╔════════════════════════════════════════════════════════════╗")
    print(f"║  FL Studio Agent — Opus 5.5 + Bridge HTTP                 ║")
    print(f"║  Gateway: {GATEWAY_URL:48} ║")
    print(f"║  Model:   {MODEL:48} ║")
    print(f"╚════════════════════════════════════════════════════════════╝")
    print()
    print("Comandos exemplo:")
    print("  Toca o FL Studio")
    print("  Cria uma progressao 2-5-1 em Do maior no canal 0")
    print("  Mix: baixa 3dB no canal 5")
    print("  Bota o BPM em 140")
    print("  Inspect: C:\\Users\\4l13n\\Documents\\Image-Line\\FL Studio\\Projects\\Project_1\\Project_1.flp")
    print("  Sair")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("sair", "exit", "quit"):
            break

        try:
            answer = run_agent(user_input)
            print(f"\nopus 5.5> {answer}\n")
        except Exception as exc:
            print(f"\nERROR: {exc}\n")


if __name__ == "__main__":
    main()