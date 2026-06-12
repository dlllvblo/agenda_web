import re
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

links = [
    "https://maps.app.goo.gl/vF9ytiKztjnzTXSf7",
    "https://maps.google.com/?q=20.080444,-99.431404",
    "https://maps.app.goo.gl/WiKAcTKoysJLAtuS8?g_st=ipc",
    "https://maps.app.goo.gl/gscHmmtaLujR4oCs7",
    "https://maps.app.goo.gl/vUwgzR1EabEMHCxY6?g_st=ic",
    "https://maps.app.goo.gl/g9M47vb5ZkJtZFPy7?g_st=aw",
    "https://maps.app.goo.gl/k16FFEkouPRrpTZK6?g_st=iw",
    "https://maps.app.goo.gl/kP1F41nw8XbnpSat9?g_st=ic",
    "https://maps.google.com/?q=20.097332,-99.470154",
    "https://maps.app.goo.gl/q3P9r9FPwvYLwyio6?g_st=ipc",
    "https://maps.google.com/?q=20.220749,-99.611165&entry=gps&g_st=aw",
]

for url in links:
    print("=" * 70)
    print("ORIGINAL:", url)
    match_q = re.search(r'[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)', url)
    if match_q:
        print("  -> q= directo:", match_q.group(1), match_q.group(2))
        continue
    try:
        resp = requests.get(url, allow_redirects=True, timeout=10, headers=HEADERS)
        print("  EXPANDIDA:", resp.url[:200])
        m_at = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', resp.url)
        m_3d = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', resp.url)
        m_1d = re.search(r'!1d(-?\d+\.\d+)!2d(-?\d+\.\d+)', resp.url)
        print("  @:", m_at.groups() if m_at else None)
        print("  !3d!4d:", m_3d.groups() if m_3d else None)
        print("  !1d!2d (dir):", m_1d.groups() if m_1d else None)
    except requests.RequestException as e:
        print("  ERROR:", e)