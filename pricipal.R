# ================================================================
# FRAGMENTACIÓN DEL PAISAJE — Sector florícola, sur del Edo. de México
# Flujo completo en R:  GPKG (vector) -> raster -> métricas -> análisis
#
# Datos: 2 regiones (17, 18) x 5 series (III..VII) = 10 capas
#        capas nombradas  S_<serie>_R_<region>  (ej. S_III_R_17)
#        campos: codigo (USV INEGI), descripcio, ...
#
# Cómo usarlo:
#   1) Ajusta el bloque "CONFIGURACIÓN".
#   2) Corre TODO una vez: genera 'salidas/inventario_clases.csv'.
#   3) (Recomendado) Con ese inventario arma 'reclasificacion.csv'
#      (columnas: clave, clase) y vuelve a correr para homologar.
#      Si no creas ese archivo, usa 'descripcio' tal cual.
#
# Requiere: terra, sf, landscapemetrics(>=2.0), dplyr, tidyr,
#           ggplot2, readr, purrr
# ================================================================

library(terra)
library(sf)
library(landscapemetrics)
library(dplyr)
library(tidyr)
library(ggplot2)
library(readr)
library(purrr)

# ================================================================
# 1. CONFIGURACIÓN  (AJUSTA esto)
# ================================================================
ruta_proyecto <- "C:/ARTICULO"

# Si TODAS las capas están en un solo GPKG:
fuente_gpkg   <- file.path(ruta_proyecto, "usv_floricola.gpkg")
# Si están en archivos separados, ver nota en el bloque 3.

ruta_rasters  <- file.path(ruta_proyecto, "rasters")
ruta_salidas  <- file.path(ruta_proyecto, "salidas")
dir.create(ruta_rasters, showWarnings = FALSE, recursive = TRUE)
dir.create(ruta_salidas, showWarnings = FALSE, recursive = TRUE)

# Campo base que define la clase ANTES de homologar.
# "descripcio" = leyenda legible ; "codigo" = clave de 11 dígitos USV.
campo_clase_base <- "descripcio"

# Tabla de reclasificación (opcional). Columnas: clave, clase
# 'clave' = valor de campo_clase_base ; 'clase' = leyenda homologada.
ruta_reclas <- file.path(ruta_proyecto, "reclasificacion.csv")

# Resolución de la rejilla en metros (afecta NP, ED, MESH; úsala igual en todo)
resolucion_m <- 30

# Series, regiones y años (ajusta los años a tus metadatos INEGI)
series   <- c("III", "IV", "V", "VI", "VII")
regiones <- c("17", "18")
anios    <- c(III = 2002, IV = 2007, V = 2011, VI = 2014, VII = 2018)

# Métricas a nivel paisaje
metricas_objetivo <- c(
  "lsm_l_np",        # NP       - Número de parches
  "lsm_l_pd",        # PD       - Densidad de parches
  "lsm_l_lpi",       # LPI      - Índice del parche mayor (%)
  "lsm_l_ed",        # ED       - Densidad de borde (m/ha)
  "lsm_l_area_mn",   # AREA_MN  - Área media de parche (ha)
  "lsm_l_shdi",      # SHDI     - Diversidad de Shannon
  "lsm_l_ai",        # AI       - Índice de agregación (%)
  "lsm_l_mesh",      # MESH     - Tamaño de malla efectivo (ha)
  "lsm_l_cohesion"   # COHESION - Cohesión / conectividad estructural
)
etiquetas_metricas <- c(
  np       = "NP · Número de parches",
  pd       = "PD · Densidad de parches (n/100 ha)",
  lpi      = "LPI · Índice del parche mayor (%)",
  ed       = "ED · Densidad de borde (m/ha)",
  area_mn  = "AREA_MN · Área media de parche (ha)",
  shdi     = "SHDI · Diversidad de Shannon",
  ai       = "AI · Índice de agregación (%)",
  mesh     = "MESH · Malla efectiva (ha)",
  cohesion = "COHESION · Cohesión (0–100)"
)

# ================================================================
# 2. TABLA DE CAPAS  (region x serie)
# ================================================================
config <- expand.grid(serie = series, region = regiones,
                      stringsAsFactors = FALSE)
config$capa   <- paste0("S_", config$serie, "_R_", config$region)
config$fuente <- fuente_gpkg
config$anio   <- anios[config$serie]
config$tif    <- paste0("usv_S", config$serie, "_R", config$region, ".tif")

# ================================================================
# 3. LECTURA Y VALIDACIÓN
# ================================================================
leer_capa <- function(fuente, capa) {
  if (!file.exists(fuente)) stop("No existe el archivo: ", fuente)
  if (is.null(capa) || capa == "") terra::vect(fuente)
  else terra::vect(fuente, layer = capa)
}
# Archivos separados: descomenta y ajusta —
   config$fuente <- file.path(ruta_proyecto, "gpkg", paste0(config$capa, ".gpkg"))
   config$capa   <- "C:/ARTICULO/USyV/III/S_III_R_17.gpkg"

vects <- Map(leer_capa, config$fuente, config$capa)

for (i in seq_along(vects)) {
  if (!campo_clase_base %in% names(vects[[i]]))
    stop("La capa '", config$capa[i], "' no tiene el campo '", campo_clase_base,
         "'. Campos: ", paste(names(vects[[i]]), collapse = ", "))
}
crs_ref <- terra::crs(vects[[1]])
for (i in seq_along(vects))
  if (terra::crs(vects[[i]]) != crs_ref)
    warning("La capa '", config$capa[i], "' tiene un CRS distinto.")

# ================================================================
# 4. INVENTARIO DE CLASES  (universo de clases + superficie)
# ================================================================
inv <- do.call(rbind, lapply(seq_along(vects), function(i) {
  v <- vects[[i]]
  data.frame(
    serie   = config$serie[i],
    region  = config$region[i],
    clave   = as.character(v[[campo_clase_base]][[1]]),
    area_ha = terra::expanse(v, unit = "ha", transform = FALSE),
    stringsAsFactors = FALSE
  )
}))
inventario <- inv %>%
  group_by(clave) %>%
  summarise(area_ha = sum(area_ha), n_poligonos = n(), .groups = "drop") %>%
  arrange(desc(area_ha))
readr::write_csv(inventario, file.path(ruta_salidas, "inventario_clases.csv"))
message("Inventario de clases (", nrow(inventario), " clases) -> salidas/inventario_clases.csv")
print(inventario, n = 50)

# ================================================================
# 5. RECLASIFICACIÓN / HOMOLOGACIÓN  (opcional)
# ================================================================
usar_reclas <- file.exists(ruta_reclas)
if (usar_reclas) {
  reclas <- read.csv(ruta_reclas, colClasses = "character", fileEncoding = "UTF-8")
  if (!all(c("clave", "clase") %in% names(reclas)))
    stop("reclasificacion.csv debe tener columnas 'clave' y 'clase'.")
  message("Reclasificación activa: ", nrow(reclas), " equivalencias.")
} else {
  message("Sin reclasificacion.csv: se usa '", campo_clase_base, "' tal cual.")
}

clase_final <- function(v) {
  base <- as.character(v[[campo_clase_base]][[1]])
  if (usar_reclas) reclas$clase[match(base, reclas$clave)] else base
}
for (i in seq_along(vects)) {
  vects[[i]]$CLASE_FINAL <- clase_final(vects[[i]])
  if (anyNA(vects[[i]]$CLASE_FINAL))
    warning("Capa '", config$capa[i],
            "': hay valores sin equivalencia (quedarán como NA / fondo).")
}

# ================================================================
# 6. DICCIONARIO ENTERO HOMOLOGADO (mismo código en las 10 capas)
# ================================================================
clases_all <- sort(unique(unlist(lapply(vects, function(v)
  as.character(v$CLASE_FINAL)))))
clases_all <- clases_all[!is.na(clases_all)]
lookup <- data.frame(codigo = seq_along(clases_all),
                     clase  = clases_all, stringsAsFactors = FALSE)
readr::write_csv(lookup, file.path(ruta_salidas, "equivalencia_clases.csv"))
message("Clases homologadas: ", nrow(lookup)); print(lookup)

# ================================================================
# 7. REJILLA COMÚN POR REGIÓN + RASTERIZACIÓN
# ================================================================
plantillas <- list()
for (reg in regiones) {
  idx  <- which(config$region == reg)
  exts <- lapply(vects[idx], terra::ext)
  plantillas[[reg]] <- terra::rast(
    terra::ext(min(sapply(exts, function(e) e[1])),
               max(sapply(exts, function(e) e[2])),
               min(sapply(exts, function(e) e[3])),
               max(sapply(exts, function(e) e[4]))),
    resolution = resolucion_m, crs = crs_ref)
}
for (i in seq_len(nrow(config))) {
  v <- vects[[i]]
  v$cod_clase <- lookup$codigo[match(as.character(v$CLASE_FINAL), lookup$clase)]
  r <- terra::rasterize(v, plantillas[[config$region[i]]],
                        field = "cod_clase", background = NA)
  terra::writeRaster(r, file.path(ruta_rasters, config$tif[i]),
                     overwrite = TRUE, datatype = "INT2U")
  message("Rasterizada ", config$capa[i], " -> ", config$tif[i])
}

# ================================================================
# 8. MÉTRICAS DE PAISAJE POR (REGIÓN, SERIE)
# ================================================================
cargar_raster <- function(tif) {
  r <- terra::rast(file.path(ruta_rasters, tif))
  message("— ", tif, " —"); print(landscapemetrics::check_landscape(r))
  r
}
metricas <- config %>%
  purrr::pmap(function(serie, region, anio, tif, ...) {
    r <- cargar_raster(tif)
    landscapemetrics::calculate_lsm(r, what = metricas_objetivo) %>%
      mutate(serie = serie, region = factor(region), anio = anio)
  }) %>%
  bind_rows()

tabla <- metricas %>%
  select(region, serie, anio, metric, value) %>%
  pivot_wider(names_from = metric, values_from = value) %>%
  arrange(region, anio)
readr::write_csv(tabla, file.path(ruta_salidas, "metricas_region_serie.csv"))
print(tabla)

# ================================================================
# 9. TENDENCIAS (inicial vs final + monotonía de Spearman)
# ================================================================
tendencias <- metricas %>%
  arrange(anio) %>%
  group_by(region, metric) %>%
  summarise(
    valor_inicial = dplyr::first(value),
    valor_final   = dplyr::last(value),
    cambio_abs    = valor_final - valor_inicial,
    cambio_pct    = 100 * (valor_final - valor_inicial) / valor_inicial,
    rho_spearman  = suppressWarnings(stats::cor(anio, value, method = "spearman")),
    .groups = "drop"
  )
readr::write_csv(tendencias, file.path(ruta_salidas, "tendencias_region.csv"))
print(tendencias)

# ================================================================
# 10. GRÁFICO DE EVOLUCIÓN TEMPORAL (regiones superpuestas)
# ================================================================
g <- metricas %>%
  mutate(metric_lab = factor(etiquetas_metricas[metric],
                             levels = etiquetas_metricas)) %>%
  ggplot(aes(x = anio, y = value, color = region, group = region)) +
  geom_line(linewidth = 0.6) +
  geom_point(size = 2.2) +
  facet_wrap(~ metric_lab, scales = "free_y", ncol = 3) +
  scale_x_continuous(breaks = sort(unique(config$anio))) +
  scale_color_manual(values = c("17" = "#1b7837", "18" = "#762a83")) +
  labs(title = "Evolución de la fragmentación del paisaje por región",
       subtitle = "Sector florícola, sur del Estado de México",
       x = "Año (serie INEGI USV)", y = "Valor de la métrica",
       color = "Región") +
  theme_minimal(base_size = 11) +
  theme(panel.grid.minor = element_blank(),
        strip.text = element_text(face = "bold"))
ggsave(file.path(ruta_salidas, "evolucion_fragmentacion.png"),
       g, width = 12, height = 8, dpi = 300)

# ================================================================
# 11. MATRIZ DE TRANSICIÓN III -> VII, POR REGIÓN (Objetivo 2)
# ================================================================
matriz_transicion <- function(tif_ini, tif_fin, etiqueta) {
  r1 <- terra::rast(file.path(ruta_rasters, tif_ini))
  r2 <- terra::rast(file.path(ruta_rasters, tif_fin))
  if (!terra::compareGeom(r1, r2, stopOnError = FALSE))
    r2 <- terra::resample(r2, r1, method = "near")
  s <- c(r1, r2); names(s) <- c("inicial", "final")
  ct <- terra::crosstab(s, long = TRUE)
  names(ct) <- c("cod_inicial", "cod_final", "n_celdas")
  ct$area_ha       <- ct$n_celdas * prod(terra::res(r1)) / 10000
  ct$clase_inicial <- lookup$clase[match(ct$cod_inicial, lookup$codigo)]
  ct$clase_final   <- lookup$clase[match(ct$cod_final,   lookup$codigo)]
  ct$region        <- etiqueta
  ct[order(-ct$area_ha),
     c("region", "clase_inicial", "clase_final", "n_celdas", "area_ha")]
}
transiciones <- lapply(regiones, function(reg) {
  ini <- config$tif[config$region == reg & config$serie == "III"]
  fin <- config$tif[config$region == reg & config$serie == "VII"]
  matriz_transicion(ini, fin, reg)
})
trans_df <- bind_rows(transiciones)
readr::write_csv(trans_df, file.path(ruta_salidas, "transicion_III_VII.csv"))
print(head(trans_df, 20))

message("\nListo. Resultados en: ", ruta_salidas)
