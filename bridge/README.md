# FL Studio Bridge — HTTP local

Microsserviço Python que expõe o FL Studio em REST na porta `8765`. Bate no
MIDI controller script via loopMIDI (control ao vivo) e usa `pyflp` para
operações offline em arquivos `.flp`.

## Subir
```bat
cd C:\Users\4l13n\Desktop\fl-studio-mcp\bridge
start_bridge.bat
```

Fica ouvindo em `http://localhost:8765`.

## Endpoints

| Método | Rota | Body |
|---|---|---|
| GET | `/health` | — |
| GET | `/state` | — |
| POST | `/transport/play` | — |
| POST | `/transport/stop` | — |
| POST | `/transport/record` | — |
| POST | `/transport/bpm` | `{"bpm":128}` |
| POST | `/mixer/volume` | `{"channel":1,"value":0.8}` |
| POST | `/mixer/pan` | `{"channel":1,"value":-0.2}` |
| POST | `/mixer/mute` | `{"channel":1,"on":true}` |
| POST | `/mixer/solo` | `{"channel":1,"on":true}` |
| POST | `/piano/add_chord` | `{"root":"C","quality":"m7","octave":4,"length":1,"channel":0}` |
| POST | `/piano/arp` | `{"root":"A","quality":"m7","octaves":2,"steps":[0,2,1,3]}` |
| POST | `/piano/clear` | `{"channel":0}` |

## Como o Opus 5.5 API usa

No system prompt do seu app que chama a API do Opus 5.5, inclua as tools como
`http://localhost:8765/...` e peça ao modelo para usar quando o usuário
pedir ações no FL Studio. O Opus 5.5 pode chamar `requests.post` nativamente se
estiver rodando código, ou você pode expor as tools explicitamente para ele.

## Exemplo rápido (Python)
```python
import requests
requests.post("http://localhost:8765/transport/play")
requests.post("http://localhost:8765/piano/add_chord",
              json={"root":"D","quality":"m7","octave":4,"length":2,"channel":0})
```

## Exemplo rápido (curl)
```bash
curl -X POST http://localhost:8765/transport/play
curl -X POST http://localhost:8765/piano/add_chord -H "Content-Type: application/json" -d "{\"root\":\"C\",\"quality\":\"maj7\",\"octave\":4,\"length\":1}"
```
