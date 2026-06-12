import re
import requests
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-MX,es;q=0.9",
}

links = [
    "https://maps.app.goo.gl/WiKAcTKoysJLAtuS8?g_st=ipc",   # esperado: México, Huehuetoca
    "https://maps.app.goo.gl/k16FFEkouPRrpTZK6?g_st=iw",    # esperado: México, Polotitlán
    "https://maps.app.goo.gl/q3P9r9FPwvYLwyio6?g_st=ipc",   # esperado: México, Teoloyucan
]

for url in links:
    print("=" * 70)
    print("LINK:", url)
    try:
        resp = requests.get(url, allow_redirects=True, timeout=15, headers=HEADERS)
        print("  URL FINAL:", resp.url[:120])
        print("  ES SORRY:", "/sorry/" in resp.url)
        html = resp.text

        # Patrón 1: og:image con center=lat%2Clng
        m = re.search(r'center=(-?\d+\.\d+)%2C(-?\d+\.\d+)', html)
        print("  og:image center:", m.groups() if m else None)

        # Patrón 2: APP_INITIALIZATION_STATE = [[[zoom, lng, lat
        m = re.search(r'APP_INITIALIZATION_STATE=\[\[\[-?[\d.]+,(-?\d+\.\d+),(-?\d+\.\d+)', html)
        print("  APP_INIT (lng,lat):", m.groups() if m else None)

        # Patrón 3: cualquier @lat,lng en el HTML
        m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', html)
        print("  @ en HTML:", m.groups() if m else None)

        # Patrón 4: !3d!4d en el HTML
        m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', html)
        print("  !3d!4d en HTML:", m.groups() if m else None)
    except requests.RequestException as e:
        print("  ERROR:", e)
    time.sleep(3)  # espaciar para no provocar el bloqueo