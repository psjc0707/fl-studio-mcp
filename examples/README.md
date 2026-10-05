# FL Studio Agent — Opus 5.5 API + Bridge

Cliente Python que orquestra Opus 5.5 (Anthropic API) com a Bridge local
do FL Studio. Loop agentic completo: você fala em português, Opus 5.5
decide quais das 17 tools chamar, executa, e responde.

## Setup

```bash
pip install anthropic requests
export ANTHROPIC_API_KEY=sk-ant-...        # Linux/Mac
$env:ANTHROPIC_API_KEY = "sk-ant-..."      # PowerShell
```

## Subir a Bridge (em outro terminal)

```bash
cd C:\Users\4l13n\Desktop\fl-studio-mcp\bridge
python fl_bridge.py     # porta 8767
python fl_mcp.py        # porta 8779
```

## Rodar o agente

```bash
cd C:\Users\4l13n\Desktop\fl-studio-mcp\examples
python fl_studio_agent.py
```

## Comandos exemplo

```
> Toca o FL Studio
> Cria uma progressao 2-5-1 em Do maior no canal 0
> Mix: baixa 3dB no canal 5
> Bota o BPM em 140
> Inspect: C:\Users\4l13n\Documents\Image-Line\FL Studio\Projects\Project_1\Project_1.flp
> Sair
```

## Variáveis de ambiente

| Var | Default | Função |
|---|---|---|
| `ANTHROPIC_API_KEY` | (obrigatória) | Sua chave Anthropic |
| `FL_GATEWAY` | `http://localhost:8779` | URL do gateway de tools |
| `FL_MODEL` | `claude-opus-4-5` | Modelo Opus 5.5 a usar |

## Como funciona

```
┌──────────────┐   tool_use    ┌──────────────┐
│   Usuário    │ ────────────> │  Opus 5.5    │
│  (você)      │               │  (decide)    │
└──────────────┘               └──────┬───────┘
       ▲                              │ JSON-RPC
       │ final response               ▼
       │                       ┌──────────────┐
       │                       │   Gateway    │ localhost:8779
       │                       │  fl_mcp.py   │
       │                       └──────┬───────┘
       │                              │ HTTP
       │                              ▼
       │                       ┌──────────────┐
       └───────────────────────│   Bridge     │ localhost:8767
            final text          │ fl_bridge.py │
                               └──────┬───────┘
                                      │ MIDI + offline
                                      ▼
                               ┌──────────────┐
                               │  FL Studio   │
                               │  (viva MIDI) │
                               └──────────────┘
```

## Versão em 1 linha (Node)

```javascript
// Use o mesmo padrão em Node com @anthropic-ai/sdk
```
