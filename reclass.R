# ================================================================
# 01 · Generador de reclasificacion.csv  (homologación de leyenda USV)
#
# Lee 'salidas/inventario_clases.csv' (creado por el script principal),
# homologa cada 'descripcio' a tu leyenda de 7-8 clases mediante
# reglas por palabra clave, y escribe 'reclasificacion.csv'.
#
# Flujo:
#   1) Corre fragmentacion_paisaje_completo.R una vez  -> inventario_clases.csv
#   2) Corre ESTE script                               -> reclasificacion.csv
#   3) Revisa 'sin_clasificar.csv' y completa a mano lo que falte
#   4) Corre de nuevo el script principal (homologa automáticamente)
# ================================================================

ruta_proyecto <- "C:/ARTICULO"
ruta_inv      <- file.path(ruta_proyecto, "salidas", "inventario_clases.csv")
ruta_out      <- file.path(ruta_proyecto, "reclasificacion.csv")
ruta_faltan   <- file.path(ruta_proyecto, "salidas", "sin_clasificar.csv")

if (!file.exists(ruta_inv))
  stop("Falta inventario_clases.csv. Corre primero fragmentacion_paisaje_completo.R")

inv <- read.csv(ruta_inv, stringsAsFactors = FALSE, fileEncoding = "UTF-8")

# ---------------------------------------------------------------
# Reglas de homologación (primera coincidencia gana). Sin acentos.
# ---------------------------------------------------------------
homologar <- function(x) {
  s   <- toupper(iconv(x, to = "ASCII//TRANSLIT"))
  out <- rep(NA_character_, length(s))
  regla <- function(pat) grepl(pat, s) & is.na(out)
  
  out[regla("PROTEGIDA|INVERNADERO|FLORICOLA|VIVERO")] <-
    "Agricultura intensiva / florícola"
  out[regla("AGRICULTURA")] <-
    "Agricultura"
  out[regla("\\bAGUA\\b|PRESA|BORDO|EMBALSE|LAGUNA|CUERPO")] <-
    "Agua"
  out[regla("SECUNDARIA")] <-
    "Vegetación secundaria"
  out[regla("PASTIZAL|PRADERA")] <-
    "Pastizal"
  out[regla("BOSQUE|SELVA|MATORRAL|MEZQUITAL|CHAPARRAL|PALMAR|GALERIA")] <-
    "Bosque / vegetación arbórea"
  out[regla("URBAN|ASENTAMIENTO|ZONA URBANA|INFRAESTRUCTURA|POBLADO")] <-
    "Zona urbana / infraestructura"
  out[regla("DESNUDO|SIN VEGETACION|EROSION|BANCO DE MATERIAL|ALTERAD")] <-
    "Suelo desnudo / área alterada"
  out
}

inv$clase <- homologar(inv$clave)

# ---------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------
clasificadas <- inv[!is.na(inv$clase), c("clave", "clase")]
write.csv(clasificadas, ruta_out, row.names = FALSE, fileEncoding = "UTF-8")

faltan <- inv[is.na(inv$clase), c("clave", "area_ha")]
if (nrow(faltan) > 0) {
  faltan <- faltan[order(-faltan$area_ha), ]
  write.csv(faltan, ruta_faltan, row.names = FALSE, fileEncoding = "UTF-8")
  message("⚠ ", nrow(faltan), " clase(s) SIN clasificar (revisa sin_clasificar.csv):")
  print(faltan)
} else {
  message("Todas las clases quedaron homologadas.")
}

# Resumen de superficie por clase homologada
resumen <- aggregate(area_ha ~ clase, data = inv[!is.na(inv$clase), ], FUN = sum)
resumen <- resumen[order(-resumen$area_ha), ]
message("\nSuperficie por clase homologada:")
print(resumen)

message("\nListo -> ", ruta_out)