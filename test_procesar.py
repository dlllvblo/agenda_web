import requests

with open("agenda.txt", encoding="utf-8") as f:
    texto = f.read()

r = requests.post(
    "http://127.0.0.1:5000/procesar",
    json={"texto": texto},
    timeout=120
)
data = r.json()

if "error" in data:
    print("ERROR:", data["error"])
else:
    for fila in data.get("filas", []):
        print(f"ESTADO: {fila['ESTADO']!r} | MUNICIPIO: {fila['MUNICIPIO']!r} | UBICACIÓN: {fila['UBICACIÓN']!r}")