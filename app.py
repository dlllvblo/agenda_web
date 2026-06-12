from flask import Flask, request, jsonify, render_template
import re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import requests
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

app = Flask(__name__)

# =========================
# MAPA PROYECTOS
# =========================
mapa_proyectos = {
        'TAP':   '1. AIFA - PACHUCA',
        'TIGDL': '5. IRAPUATO - GUADALAJARA',
        'TMLM':  '6. MAZATLÁN - LOS MOCHIS',
        'TMQ':   '2. MÉXICO - QUERÉTARO',
        'TQI':   '3. QUERÉTARO - IRAPUATO',
        'TQSLP': '7. QUERÉTARO - SAN LUIS POTOSÍ',
        'TSNL':  '4. SALTILLO - NUEVO LAREDO',
        'TSLPS': 'SAN LUIS POTOSÍ - SALTILLO',
    }

meses = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
}

# CAPA MUNICIPAL INEGI
def cargar_capas():
    base = os.path.join(os.path.dirname(__file__), "data")
    with open(os.path.join(base, "municipios_mx.geojson"), encoding="utf-8") as f:
        mun_gj = json.load(f)

    geoms = [shape(feat["geometry"]) for feat in mun_gj["features"]]
    props = [feat["properties"] for feat in mun_gj["features"]]
    return STRtree(geoms), geoms, props

ARBOL_MUN, MUN_GEOMS, MUN_PROPS = cargar_capas()


from urllib.parse import unquote

def extraer_coordenadas(url_maps):
    """Extrae lat, lng de un link de Google Maps (corto o expandido)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }

    def buscar_coords(url):
        # Decodificar URL-encoding (doble, por el parámetro continue= de Google)
        url = unquote(unquote(url))
        # 1. Pin real del lugar: !3d{lat}!4d{lng}
        match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        # 2. Formato ?q=lat,lng
        match = re.search(r'[?&]q=(-?\d+\.\d+),\s*(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        # 3. Búsqueda por coordenadas: /maps/search/lat,+lng
        match = re.search(r'/maps/search/(-?\d+\.\d+),\s*\+?(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        # 4. Links de ruta: destino como !1d{lng}!2d{lat}
        if "/dir/" in url:
            match = re.search(r'!1d(-?\d+\.\d+)!2d(-?\d+\.\d+)', url)
            if match:
                return float(match.group(2)), float(match.group(1))
        # 5. Último recurso: @lat,lng (centro del encuadre, puede diferir del punto)
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None

    # Intentar sobre la URL original
    coords = buscar_coords(url_maps)
    if coords:
        return coords

    # Expandir el link corto y buscar en la URL final 
    try:
        resp = requests.get(url_maps, allow_redirects=True, timeout=10, headers=headers)
        coords = buscar_coords(resp.url)
        if coords:
            return coords
    except requests.RequestException:
        pass
    return None, None


def obtener_estado_municipio(lat, lng):
    """Point-in-polygon contra capa municipal INEGI local"""
    punto = Point(lng, lat)
    for idx in ARBOL_MUN.query(punto):
        if MUN_GEOMS[idx].contains(punto):
            p = MUN_PROPS[idx]
            return p["NOM_ENT"], p["NOM_MUN"]
    # Fallback: punto en hueco de la simplificación → municipio más cercano (<~1 km)
    idx = ARBOL_MUN.nearest(punto)
    if idx is not None and MUN_GEOMS[idx].distance(punto) < 0.01:
        p = MUN_PROPS[idx]
        return p["NOM_ENT"], p["NOM_MUN"]
    return "", ""

def resumir_actividad(texto_actividad):
    """Reformula la actividad a estilo de reporte institucional. Fallback: texto original."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not texto_actividad.strip():
        return texto_actividad
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 150,
                "messages": [{
                    "role": "user",
                    "content": (
                        "Reformula esta actividad de campo como una línea de reporte "
                        "institucional en pasado, breve y formal (estilo: 'Se realizó reunión con...'). "
                        "Conserva números de parcela, nombres de personas, ejidos y PKs. "
                        "Responde SOLO con la frase, sin comillas ni explicación.\n\n"
                        f"Actividad: {texto_actividad}"
                    )
                }],
            },
            timeout=20,
        )
        resp.raise_for_status()
        resumen = resp.json()["content"][0]["text"].strip()
        return resumen if resumen else texto_actividad
    except Exception:
        return texto_actividad

def procesar_agenda(texto):

    # --- PROYECTO ---
    proyecto_match = re.search(r"Proyecto\s*(.*)", texto, re.IGNORECASE)
    proyecto_raw = proyecto_match.group(1).strip() if proyecto_match else ""
    if proyecto_raw:
        clave = proyecto_raw.split()[-1].upper()
        proyecto_final = mapa_proyectos.get(clave, proyecto_raw)
    else:
        proyecto_final = "SIN PROYECTO"

    # --- FECHA ---
    fecha_match = re.search(
        r"(?:\w+\s+)?(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",
        texto, re.IGNORECASE
    )
    if fecha_match:
        dia = fecha_match.group(1).zfill(2)
        mes_texto = fecha_match.group(2).lower()
        anio = fecha_match.group(3)
        mes = meses.get(mes_texto, "00")
        fecha = f"{dia}/{mes}/{anio}"
        fecha_dt = datetime.strptime(fecha, "%d/%m/%Y")
        fecha_solicitud = (fecha_dt - timedelta(days=1)).strftime("%d/%m/%Y")
    else:
        fecha = ""
        fecha_solicitud = ""
        for m in re.finditer(r"(\d{1,2})\s+de\s+(\w+)(?:\s+de\s+(\d{4}))?", texto, re.IGNORECASE):
            mes_texto = m.group(2).lower()
            if mes_texto in meses:
                dia = m.group(1).zfill(2)
                anio = m.group(3) if m.group(3) else str(datetime.now().year)
                fecha = f"{dia}/{meses[mes_texto]}/{anio}"
                fecha_dt = datetime.strptime(fecha, "%d/%m/%Y")
                fecha_solicitud = (fecha_dt - timedelta(days=1)).strftime("%d/%m/%Y")
                break

    # --- BLOQUES ---
    bloques = re.split(r"\n\d+[\.-]\s*", texto)[1:]
    if not bloques:
        cuerpo = "\n".join(
            l for l in texto.splitlines()
            if not re.match(r"\s*Proyecto\b", l, re.IGNORECASE)
            and not re.search(r"\b\d{1,2}\s+de\s+(?:%s)\b" % "|".join(meses), l, re.IGNORECASE)
        ).strip()
        if cuerpo:
            bloques = [cuerpo]
    filas = []

    for bloque in bloques:
        bloque = bloque.replace('\xa0', ' ').strip()

        # HORA
        match_hora = re.search(r"(\d{1,2}:\d{2})\s*hrs?\s*[-–—]", bloque, re.IGNORECASE)
        hora_label_match = re.search(r"Hora:\s*(\d{1,2}:\d{2})", bloque, re.IGNORECASE)
        hora_sola_match = re.search(r"(\d{1,2}:\d{2})\s*hrs?\b", bloque, re.IGNORECASE)
        if match_hora:
            hora_txt = match_hora.group(1).strip()
        elif hora_label_match:
            hora_txt = hora_label_match.group(1).strip()
        elif hora_sola_match:
            hora_txt = hora_sola_match.group(1).strip()
        else:
            hora_txt = ""

        # LÍNEA PRINCIPAL
        match_inline = re.search(r"\d{1,2}:\d{2}\s*hrs?\s*[-–—]\s*(.*)", bloque, re.IGNORECASE)
        if match_inline:
            linea_principal = match_inline.group(1).strip()
        else:
            linea_principal = bloque.splitlines()[0].strip()
        if re.fullmatch(r'\d{1,2}:\d{2}\s*hrs?\.?', linea_principal, re.IGNORECASE):
            lineas_bloque = [l.strip() for l in bloque.splitlines() if l.strip()]
            if len(lineas_bloque) > 1:
                linea_principal = lineas_bloque[1]

        # CAMBIO 1: cortar linea_principal antes del primer campo conocido
        linea_principal = re.split(
            r'\s+(?=(?:F:|BDTs?:|Pol[ií]gonos?:|Asistentes?:|Ubicaci[oó]n:|Ejido:|Municipio:|Hora:|Parcelas:))',
            linea_principal, maxsplit=1, flags=re.IGNORECASE
        )[0].strip()

        linea_principal = re.sub(r'^\d{1,2}:\d{2}\s*hrs?\.?\s*[-–—]\s*', '', linea_principal, flags=re.IGNORECASE).strip()
        linea_principal = re.sub(r'^[-–—\s]+', '', linea_principal).strip()
        linea_principal = re.sub(r'\.$', '', linea_principal).strip()

        actividad_txt = linea_principal

        # CAMPO Actividad:
        actividad_field = re.search(r"Actividad:\s*(.*?)(?=\n\s*\w+:|$)", bloque, re.IGNORECASE | re.DOTALL)
        actividad_detalle = ""
        if actividad_field and actividad_field.group(1).strip():
            actividad_raw = actividad_field.group(1).strip()
            actividad_detalle = re.sub(r'\s*\n\s*[•\-]\s*', ' | ', actividad_raw)
            actividad_detalle = re.sub(r'^[•\-]\s*', '', actividad_detalle).strip()

        # FRENTE
        frente = re.search(
            r"(?<!\w)(?:Frente|F):\s*([^\n]+?)(?=\s+(?:BDTs?|Pol[ií]gonos?|Asistentes?|Asiste|Ubicaci[oó]n|Ejido|Municipio|Parcelas):|[\r\n]|$)",
            bloque, re.IGNORECASE
        )

        # POLÍGONO
        poligono = re.search(r"Pol[ií]gono:\s*(.*)", bloque, re.IGNORECASE)

        # MUNICIPIO
        municipio_match = re.search(r"Municipio:\s*(.*)", bloque, re.IGNORECASE)
        municipio = municipio_match.group(1).strip() if municipio_match else ""

        # ASISTENTES
        asistentes = re.search(
            r"(?:Asistentes?|Asisten?|Participa(?:n|ntes)?):\s*([^\n]+?)(?=\s+(?:Ubicaci[oó]n|Punto de reuni[oó]n|Punto de encuentro|BDTs?|Pol[ií]gonos?|Ejido|Municipio|Parcelas):|$)",
            bloque, re.IGNORECASE
        )

        # UBICACIÓN
        ubicacion = re.search(
            r"(?:Ubicaci[oó]n|Punto de reuni[oó]n|Punto de encuentro):\s*[\n\r]*\s*(?:\[.*?\]\((https?://[^\)\s]+)\)|(https?://[^\s\]]+)|(.+))",
            bloque,
            re.IGNORECASE
        )
        url = ""
        if ubicacion:
            url = (ubicacion.group(1) or ubicacion.group(2) or ubicacion.group(3) or "").strip()

        estado_geo = ""
        municipio_geo = ""
        if url and ("goo.gl" in url or "google.com" in url):
            lat, lng = extraer_coordenadas(url)
            if lat is not None:
                estado_geo, municipio_geo = obtener_estado_municipio(lat, lng)

        # BDTs
        bdts = re.search(
            r"BDTs?:\s*([^\n]+?)(?=\s+(?:F:|Pol[ií]gonos?:|Asistentes?:|Ubicaci[oó]n:|Ejido:|Municipio:|Parcelas:)|$)",
            bloque, re.IGNORECASE
        )

       # ACTIVIDADES DESARROLLADAS
        bdts_val = bdts.group(1).strip() if bdts else ""
        if bdts_val.upper() == "N/A":
            bdts_val = ""

        partes = []
        if bdts_val:
            partes.append(bdts_val)
        partes.append(resumir_actividad(linea_principal))
        if actividad_detalle and actividad_detalle != linea_principal:
            partes.append(actividad_detalle)
        actividades_desarrolladas = " | ".join(partes) if partes else ""

        # EJIDO
        ejido_match = re.search(r"Ejido:?\s*(.*)", bloque, re.IGNORECASE)
        ejido = ejido_match.group(1).strip() if ejido_match else ""

        # NÚCLEO AGRARIO
        nucleo = re.search(r"Núcleo Agrario:\s*(.*)", bloque, re.IGNORECASE)
        if not nucleo:
            ejido_inline = re.search(
                r'\bEjido\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s]+?)(?:\)|,|\.|\n|$)',
                linea_principal
            )
            if ejido_inline:
                nucleo_txt = ejido_inline.group(1).strip()
                nucleo = type('_', (), {'group': lambda self, n: nucleo_txt})()
        if not nucleo and ejido_match:
            nucleo_txt = ejido_match.group(1).strip()
            if nucleo_txt:
                nucleo = type('_', (), {'group': lambda self, n: nucleo_txt})()

        # PROPIETARIOS
        particular = re.search(r"Propietarios?:\s*(.*)|Particular:\s*(.*)", bloque, re.IGNORECASE)
        if not particular:
            propietario_inline = re.search(
                r'\b(?:se[ñn]or[a]?|propietari[ao])\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+)',
                linea_principal,
                re.IGNORECASE
            )
            if propietario_inline:
                prop_txt = propietario_inline.group(1).strip()
                particular = type('_', (), {'group': lambda self, n: prop_txt if n in (1, 2) else ''})()


        fila = {
            "FECHA DE SOLICITUD": fecha_solicitud,
            "SOLICITANTE": "SEDATU",
            "MEDIO DE SOLICITUD": "WhatsApp",
            "TIPO DE SOLICITUD": actividad_txt,
            "PROYECTO FERROVIARIO": proyecto_final,
            "UBICACIÓN": url,
            "TIPO DE PROPIEDAD": "",
            "FRENTE": (
                re.sub(r'\b(\d+)\b', r'F\1', frente.group(1).strip())
                if frente and frente.group(1).strip().upper() != "N/A" else ""
            ),
            "POLÍGONO": (
                poligono.group(1).strip()
                if poligono and poligono.group(1).strip().upper() != "N/A" else ""
            ),
            "ESTADO": estado_geo.upper(),
            "MUNICIPIO": municipio if municipio else municipio_geo,
            "EJIDO": ejido,
            "NÚCLEO AGRARIO": (nucleo.group(1).strip() if nucleo else ""),
            "PROPIETARIOS PROPIEDAD PRIVADA": (
                (particular.group(1) or particular.group(2) or "").strip()
                if particular else ""
            ),
            "FECHA Y HORA": f"{fecha} {hora_txt}",
            "DEPENDENCIAS PARTICIPANTES": (
                asistentes.group(1).strip() if asistentes else ""
            ),
            "ACTIVIDADES  DESARROLLADAS": actividades_desarrolladas,
        }

        filas.append(fila)

    return filas

def subir_a_sheets(filas):
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    # Soporte para credenciales desde variable de entorno (Render) o archivo local
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credenciales.json", scopes=SCOPES)

    client = gspread.Client(auth=creds)
    SPREADSHEET_ID = "1xwnS8DiEB4rzs7I8BRGgUbtzDF8M_zn58qYUJS-Mvmk"
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.get_worksheet(0)
    headers = worksheet.row_values(1)
    columna_a = worksheet.col_values(1)

    ultimo = 0
    if len(columna_a) > 1:
        try:
            ultimo = int(columna_a[-1])
        except:
            ultimo = 0

    inicio = ultimo + 1
    datos = []
    for i, fila in enumerate(filas):
        nueva_fila = [inicio + i]
        for header in headers[1:]:
            nueva_fila.append(fila.get(header, ""))
        datos.append(nueva_fila)

    worksheet.append_rows(datos, value_input_option="USER_ENTERED")
    return len(filas)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/procesar", methods=["POST"])
def procesar():
    try:
        texto = request.json.get("texto", "")
        if not texto.strip():
            return jsonify({"error": "El texto está vacío"}), 400
        filas = procesar_agenda(texto)
        if not filas:
            return jsonify({"error": "No se encontraron actividades en el texto"}), 400
        total = subir_a_sheets(filas)
        return jsonify({"success": True, "filas": filas, "total": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/sw.js")
def service_worker():
    # Servido desde raíz para que el scope del SW cubra toda la app
    return app.send_static_file("sw.js")


@app.route("/share", methods=["GET", "POST"])
def share():
    if request.method == "POST":
        texto = (request.form.get("text") or request.form.get("title") or "").strip()
    else:
        texto = (request.args.get("text") or "").strip()

    if not texto:
        return "No llegó texto para procesar", 400

    try:
        filas = procesar_agenda(texto)
        if not filas:
            return "<h2>⚠️ No se encontraron actividades en el texto compartido</h2>", 400
        total = subir_a_sheets(filas)
        resumen_html = "".join(
            f"<li>{f['FECHA Y HORA']} — {f['TIPO DE SOLICITUD'][:80]}</li>" for f in filas
        )
        return f"""
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <style>body{{font-family:sans-serif;padding:24px;background:#691B4F;color:white}}
        .card{{background:white;color:#333;border-radius:16px;padding:20px}}</style></head>
        <body><div class="card">
        <h2>✅ {total} actividad(es) subida(s) a Sheets</h2>
        <ul>{resumen_html}</ul>
        </div></body></html>
        """
    except Exception as e:
        return f"<h2>❌ Error: {e}</h2>", 500


if __name__ == "__main__":
    app.run(debug=False)