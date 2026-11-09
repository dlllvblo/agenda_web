#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
descargador_ran.py — Descarga masiva de parcelas y núcleos agrarios
Recorre la república por entidad y municipio consultando el padrón del
Registro Agrario Nacional (sig.ran.gob.mx) y consolida en GeoPackage local.

*** SIMULACIÓN VISUAL: no realiza peticiones de red ni escribe archivos. ***
Detener con Ctrl + C.
"""
import sys
import time
import random
from datetime import datetime


# ───────────────────────── colores ─────────────────────────
class C:
    RESET = "\033[0m"; DIM = "\033[2m"; BOLD = "\033[1m"
    VERDE = "\033[32m"; CYAN = "\033[36m"; AMBAR = "\033[33m"
    ROJO = "\033[31m"; GRIS = "\033[90m"; AZUL = "\033[34m"; MAG = "\033[35m"


def w(s):
    sys.stdout.write(s)
    sys.stdout.flush()


# ───────────────────────── datos ─────────────────────────
ESTADOS = {
    "01": ("Aguascalientes", ["Aguascalientes", "Calvillo", "Jesús María", "Rincón de Romos", "Pabellón de Arteaga", "Asientos"]),
    "02": ("Baja California", ["Mexicali", "Tijuana", "Ensenada", "Tecate", "Playas de Rosarito"]),
    "03": ("Baja California Sur", ["La Paz", "Los Cabos", "Comondú", "Loreto", "Mulegé"]),
    "04": ("Campeche", ["Campeche", "Carmen", "Champotón", "Escárcega", "Calkiní", "Hopelchén"]),
    "05": ("Coahuila", ["Saltillo", "Torreón", "Monclova", "Piedras Negras", "Acuña", "Ramos Arizpe", "San Pedro"]),
    "06": ("Colima", ["Colima", "Manzanillo", "Tecomán", "Villa de Álvarez", "Coquimatlán"]),
    "07": ("Chiapas", ["Tuxtla Gutiérrez", "Tapachula", "San Cristóbal de las Casas", "Comitán", "Palenque", "Ocosingo"]),
    "08": ("Chihuahua", ["Chihuahua", "Juárez", "Cuauhtémoc", "Delicias", "Hidalgo del Parral", "Camargo"]),
    "09": ("Ciudad de México", ["Milpa Alta", "Tláhuac", "Xochimilco", "Tlalpan", "Cuajimalpa"]),
    "10": ("Durango", ["Durango", "Gómez Palacio", "Lerdo", "Nuevo Ideal", "Canatlán", "El Salto"]),
    "11": ("Guanajuato", ["León", "Irapuato", "Celaya", "Salamanca", "Silao", "Guanajuato", "Pénjamo"]),
    "12": ("Guerrero", ["Acapulco", "Chilpancingo", "Iguala", "Taxco", "Zihuatanejo", "Tlapa"]),
    "13": ("Hidalgo", ["Pachuca de Soto", "Tulancingo", "Tula de Allende", "Huejutla", "Tepeji del Río", "Actopan", "Ixmiquilpan", "Tizayuca"]),
    "14": ("Jalisco", ["Guadalajara", "Zapopan", "Tlaquepaque", "Tonalá", "Puerto Vallarta", "Lagos de Moreno", "Tepatitlán"]),
    "15": ("México", ["Toluca", "Ecatepec", "Naucalpan", "Huehuetoca", "Teoloyucan", "Coyotepec", "Tepotzotlán", "Lerma", "Zumpango", "Otumba"]),
    "16": ("Michoacán", ["Morelia", "Uruapan", "Zamora", "Lázaro Cárdenas", "Apatzingán", "Pátzcuaro", "Zitácuaro"]),
    "17": ("Morelos", ["Cuernavaca", "Cuautla", "Jiutepec", "Temixco", "Yautepec", "Ayala"]),
    "18": ("Nayarit", ["Tepic", "Bahía de Banderas", "Santiago Ixcuintla", "Compostela", "Xalisco"]),
    "19": ("Nuevo León", ["Monterrey", "Guadalupe", "Apodaca", "General Escobedo", "San Nicolás", "García", "Santa Catarina", "Cadereyta Jiménez"]),
    "20": ("Oaxaca", ["Oaxaca de Juárez", "Juchitán", "Salina Cruz", "Tuxtepec", "Huajuapan", "Tlaxiaco"]),
    "21": ("Puebla", ["Puebla", "Tehuacán", "Atlixco", "San Martín Texmelucan", "San Pedro Cholula", "Huauchinango", "Izúcar de Matamoros"]),
    "22": ("Querétaro", ["Querétaro", "San Juan del Río", "El Marqués", "Corregidora", "Pedro Escobedo", "Tequisquiapan", "Colón"]),
    "23": ("Quintana Roo", ["Benito Juárez", "Othón P. Blanco", "Solidaridad", "Cozumel", "Felipe Carrillo Puerto"]),
    "24": ("San Luis Potosí", ["San Luis Potosí", "Soledad de G. S.", "Ciudad Valles", "Matehuala", "Rioverde", "Tamazunchale"]),
    "25": ("Sinaloa", ["Culiacán", "Mazatlán", "Ahome", "Guasave", "Navolato", "El Fuerte", "Mocorito"]),
    "26": ("Sonora", ["Hermosillo", "Cajeme", "Nogales", "San Luis Río Colorado", "Navojoa", "Guaymas", "Caborca"]),
    "27": ("Tabasco", ["Centro", "Cárdenas", "Comalcalco", "Huimanguillo", "Macuspana", "Cunduacán"]),
    "28": ("Tamaulipas", ["Reynosa", "Matamoros", "Nuevo Laredo", "Tampico", "Victoria", "Altamira", "Río Bravo", "El Mante"]),
    "29": ("Tlaxcala", ["Tlaxcala", "Apizaco", "Huamantla", "Chiautempan", "Calpulalpan", "Zacatelco"]),
    "30": ("Veracruz", ["Veracruz", "Xalapa", "Coatzacoalcos", "Córdoba", "Poza Rica", "Orizaba", "Minatitlán", "Tuxpan", "Papantla"]),
    "31": ("Yucatán", ["Mérida", "Kanasín", "Valladolid", "Tizimín", "Progreso", "Umán", "Ticul"]),
    "32": ("Zacatecas", ["Zacatecas", "Fresnillo", "Guadalupe", "Jerez", "Río Grande", "Sombrerete", "Pinos"]),
}

EJIDOS = [
    "Emiliano Zapata", "Lázaro Cárdenas", "Benito Juárez", "San Miguel", "La Esperanza",
    "El Progreso", "Cinco de Mayo", "Nuevo Ideal", "San José", "Vicente Guerrero",
    "Francisco I. Madero", "El Refugio", "La Concepción", "San Isidro", "Guadalupe Victoria",
    "Adolfo López Mateos", "Plan de Ayala", "Tierra y Libertad", "El Carmen", "San Antonio",
    "Los Reyes", "Santa María", "18 de Marzo", "La Purísima", "El Salitre", "Ojo de Agua",
    "Las Palmas", "San Francisco", "El Fresno", "Loma Bonita", "Agua Zarca", "El Zapote",
]

CAPAS = ["parcelas", "solares urbanos", "uso común", "perímetro del núcleo", "asentamiento humano"]


# ───────────────────────── helpers visuales ─────────────────────────
def barra(pct, ancho=26):
    lleno = int(ancho * pct)
    return "█" * lleno + "░" * (ancho - lleno)


def ts():
    return datetime.now().strftime("%H:%M:%S")


def descargar_archivo(nombre, mb, tally):
    """Anima una barra de progreso para un 'archivo'."""
    bajado = 0.0
    while bajado < mb:
        bajado = min(mb, bajado + random.uniform(0.12, 0.55))
        pct = bajado / mb
        vel = random.uniform(1.8, 9.4)
        w(f"\r    {C.CYAN}↓{C.RESET} {nombre:<44}{C.GRIS}{barra(pct)}{C.RESET} "
          f"{int(pct * 100):3d}%  {C.DIM}{vel:4.1f} MB/s{C.RESET}")
        time.sleep(random.uniform(0.015, 0.06))
    w(f"\r    {C.VERDE}✓{C.RESET} {nombre:<44}{C.VERDE}{barra(1.0)}{C.RESET} "
      f"100%  {C.DIM}{mb:4.1f} MB{C.RESET}\n")
    tally["archivos"] += 1
    tally["mb"] += mb


def banner():
    w(f"{C.BOLD}{C.MAG}")
    w("╔══════════════════════════════════════════════════════════════════╗\n")
    w("║   RAN · Descarga masiva de padrón de núcleos agrarios y parcelas  ║\n")
    w("║   Registro Agrario Nacional — SEDATU · sig.ran.gob.mx            ║\n")
    w("╚══════════════════════════════════════════════════════════════════╝\n")
    w(C.RESET)


def conectar():
    pasos = [
        "Resolviendo sig.ran.gob.mx",
        "Estableciendo túnel TLS 1.3",
        "Autenticando con certificado institucional",
        "Solicitando token de sesión (OAuth2)",
        "Cargando catálogo de entidades federativas",
    ]
    for p in pasos:
        w(f"{C.GRIS}[{ts()}]{C.RESET} {p} ")
        for _ in range(random.randint(3, 6)):
            w(f"{C.DIM}.{C.RESET}")
            time.sleep(random.uniform(0.08, 0.22))
        w(f" {C.VERDE}OK{C.RESET}\n")
        time.sleep(0.1)
    w(f"{C.GRIS}[{ts()}]{C.RESET} Sesión establecida. "
      f"{C.VERDE}Token válido 3600 s.{C.RESET}\n\n")


# ───────────────────────── main ─────────────────────────
def main():
    tally = {"archivos": 0, "mb": 0.0, "parcelas": 0, "nucleos": 0, "municipios": 0}
    banner()
    conectar()

    claves = list(ESTADOS.keys())
    idx = 0
    try:
        while True:
            cve_ent = claves[idx % len(claves)]
            idx += 1
            nom_ent, municipios = ESTADOS[cve_ent]

            for muni in random.sample(municipios, k=min(len(municipios), random.randint(2, 4))):
                cve_mun = f"{random.randint(1, 120):03d}"
                tally["municipios"] += 1
                w(f"\n{C.BOLD}{C.AZUL}[{cve_ent} {nom_ent} · {cve_mun} {muni}]{C.RESET} "
                  f"{C.DIM}consultando padrón de núcleos agrarios…{C.RESET}\n")
                time.sleep(random.uniform(0.2, 0.5))

                n_nucleos = random.randint(2, 6)
                w(f"    {C.GRIS}› {n_nucleos} núcleos agrarios en el municipio{C.RESET}\n")

                for _ in range(n_nucleos):
                    ejido = random.choice(EJIDOS)
                    tipo = random.choice(["Ejido", "Ejido", "Ejido", "Comunidad"])
                    n_parc = random.randint(48, 640)
                    tally["nucleos"] += 1
                    tally["parcelas"] += n_parc

                    clave_na = f"{cve_ent}{cve_mun}{random.randint(1,999):03d}"
                    w(f"  {C.CYAN}▸{C.RESET} {tipo} \"{ejido}\"  "
                      f"{C.GRIS}NA {clave_na} · {n_parc} parcelas{C.RESET}\n")

                    capa = random.choice(CAPAS)
                    slug = ejido.replace(" ", "").replace('"', "")[:14]
                    fmt = random.choice(["kml", "shp", "gpkg"])
                    fn = f"RAN_{cve_ent}_{cve_mun}_{slug}_{capa.split()[0]}.{fmt}"
                    mb = round(random.uniform(0.4, 7.5), 1)
                    descargar_archivo(fn, mb, tally)

                # consolidación por municipio
                w(f"    {C.AMBAR}⇩ consolidando {random.randint(80, 900)} rasgos "
                  f"en GeoPackage municipal…{C.RESET}")
                time.sleep(random.uniform(0.3, 0.8))
                w(f" {C.VERDE}listo{C.RESET}  "
                  f"{C.DIM}RAN_{cve_ent}_{cve_mun}_consolidado.gpkg{C.RESET}\n")

            time.sleep(random.uniform(0.15, 0.4))

    except KeyboardInterrupt:
        w("\n\n")
        w(f"{C.AMBAR}⏹  Descarga interrumpida por el usuario (Ctrl+C).{C.RESET}\n")
        w(f"{C.GRIS}[{ts()}] Cerrando sesión en sig.ran.gob.mx…{C.RESET}\n")
        time.sleep(0.4)
        w(f"{C.BOLD}Resumen de la sesión:{C.RESET}\n")
        w(f"   Municipios procesados : {C.CYAN}{tally['municipios']}{C.RESET}\n")
        w(f"   Núcleos agrarios      : {C.CYAN}{tally['nucleos']}{C.RESET}\n")
        w(f"   Parcelas indexadas    : {C.CYAN}{tally['parcelas']:,}{C.RESET}\n")
        w(f"   Archivos descargados  : {C.CYAN}{tally['archivos']}{C.RESET}\n")
        w(f"   Volumen total         : {C.CYAN}{tally['mb']:,.1f} MB{C.RESET}\n")
        w(f"\n{C.VERDE}✓ Sesión cerrada correctamente.{C.RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()