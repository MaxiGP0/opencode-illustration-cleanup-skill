---
name: illustration-cleanup
description: Segment and clean flat illustrations locally. Detects and erases overlaid text or logos with mask-based LaMa inpainting, removes the background with BiRefNet, refines alpha edges, and exports a transparent PNG cropped to the subject bounding box. Use for subject cutouts, sticker or asset extraction, background removal, text or logo removal, alpha matting, or repairing notches left by previous cutouts. Triggers: remove background, transparent PNG, cutout, segmentation, alpha matting, remove text, remove logo, object removal, inpainting, crop to subject, flat illustration, sticker.
license: MIT
---

# Illustration Cleanup

Pipeline local para ilustraciones con fondo plano. Hace, en este orden:

1. Detecta texto superpuesto con RapidOCR y arma una máscara **a nivel trazo**.
2. Borra texto/logos con **LaMa** (inpainting consciente del contenido).
3. Separa el sujeto del fondo con **BiRefNet** vía rembg.
4. Refina el borde del alpha (feather o matting closed-form).
5. Recorta al **bounding box** del sujeto y exporta un PNG transparente.

El orden importa: se limpia el texto **antes** de segmentar. Así el inpainting puede reconstruir el borde del ala o del contorno, y la segmentación final ve una silueta continua.

## Cuándo usarla

- “Quitá el fondo de esta ilustración y dejá PNG transparente”.
- “Borrá el texto/logo que está encima del dibujo”.
- “Recortá el sujeto al mínimo”.
- “Necesito alpha limpio, sin halos ni muescas”.
- “La herramienta anterior me comió un pedazo del ala”.

No es para fotos con pelo/pelusa extrema ni para video. Para fotos, BiRefNet puede servir, pero el matting CF es el que mejor maneja bordes suaves; para ilustración plana, `feather` suele alcanzar.

## Requisitos

- Windows 10/11 con PowerShell 5+.
- Internet en la primera ejecución (descarga Python, dependencias y modelos).
- ~3 GB libres. Sin permisos de administrador: todo queda dentro de la skill.
- No requiere GPU. CPU es el default.

La primera ejecución descarga:

| Componente | Tamaño aproximado |
|---|---|
| `uv` + Python 3.12 + dependencias | ~1 GB |
| Modelo BiRefNet General (`birefnet-general`) | ~900 MB |
| Modelo LaMa | ~200 MB |
| RapidOCR | ~15 MB |

Si querés empezar con menos peso, usá `--model isnet-anime` o `--model isnet-general-use` (~170 MB). Para la versión final, `birefnet-general` suele dar mejores bordes. `birefnet-general-lite` es un punto medio.

## Flujo recomendado

1. **Inspeccioná la imagen** a tamaño completo y a thumbnail. Identificá:
   - sujeto y fondo;
   - texto/logo y si cruza el borde del sujeto;
   - zonas donde una máscara rectangular dejaría una muesca.
2. **Ejecutá el pipeline** con los valores por defecto.
3. **Leé el reporte JSON**. Mirá `text_detections`, `mask_methods`, `inpaint_backend`, `model_used`, `warnings` y `bbox_source`.
4. **Revisá el preview** en checkerboard, fondo oscuro y fondo claro.
5. Si el texto no se borró o la máscara se comió parte del dibujo, ajustá:
   - `--mask-mode kmeans` o `--text-color "#RRGGBB"` para texto de color sólido;
   - `--mask-threshold` más bajo si falta texto, más alto si come de más;
   - `--mask-expand 1` si la máscara se está comiendo el borde;
   - `--erase-box x1,y1,x2,y2` para logos que OCR no detecta;
   - `--mask ruta.png` para control total.
6. **Verificá** el PNG final y el reporte antes de entregar.

## Comandos

Rutas típicas (usá la que corresponda a tu instalación):

```powershell
# Instalación con npx skills
$skill = "$HOME\.agents\skills\illustration-cleanup"

# Instalación con install.ps1
$skill = "$HOME\.config\opencode\skills\illustration-cleanup"
```

### Imagen individual

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\run.ps1" `
  "C:\ruta\ilustracion.png" `
  --output "C:\ruta\salida"
```

### Borrar solo un texto específico

```powershell
... --erase-text "SALE"
```

### Conservar un texto y borrar el resto

```powershell
... --keep-text "NEW"
```

### Logo que OCR no detecta (caja manual)

```powershell
... --erase-box 80,540,180,640 --mask-mode kmeans
```

Si el logo tiene un color dominante conocido:

```powershell
... --erase-box 80,540,180,640 --text-color "#D62828" --mask-threshold 22
```

### Varios colores de texto en una sola pasada

`--text-color` es repetible. Si la ilustración tiene texto negro, rojo y blanco:

```powershell
... --text-color "#111111" --text-color "#D62828" --text-color "#FFFFFF"
```

### Quedarse solo con el sujeto principal

Si hay un logo o un elemento suelto que no querés en el PNG:

```powershell
... --keep-largest
```

Para conservar elementos secundarios grandes pero descartar motas:

```powershell
... --min-component 0.02
```

### Reparar una muesca existente

Creá una máscara PNG en blanco y negro donde blanco = zona a reconstruir, y corré:

```powershell
... --no-ocr --mask "C:\ruta\muesca.png" --inpaint lama --mask-expand 1 --mask-feather 1.5
```

### Matting de mayor calidad

```powershell
... --matting cf --force-matting
```

### Lote

```powershell
... "C:\ruta\carpeta" --output "C:\ruta\salida" --recursive
```

### Ayuda completa

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\run.ps1" --help
```

## Opciones que importan

| Opción | Default | Para qué |
|---|---|---|
| `--model` | `birefnet-general` | Modelo de segmentación. `isnet-anime` para ilustración estilo anime; `isnet-general-use` o `birefnet-general-lite` como respaldo liviano. |
| `--matting` | `feather` | `cf` = closed-form matting (mejor borde suave, más lento); `none` = alpha crudo. |
| `--feather` | `0.7` | Suavizado del alpha en píxeles. |
| `--mask-mode` | `auto` | `auto`, `kmeans`, `border`, `box`. `auto` es conservador: si no puede aislar el trazo, **no borra** y avisa. `box` borra toda la caja y **puede dejar muescas**; usalo solo si la caja no toca al sujeto. |
| `--mask-threshold` | `28` | Diferencia de color para separar el trazo del fondo. |
| `--text-color` | ninguno | Color exacto del texto/logo; repetible. Es el modo más preciso en ilustración plana. |
| `--mask-expand` | `2` | Dilatación de la máscara. Subilo si queda un halo; bajalo si come borde. |
| `--mask-feather` | `1.0` | Suavizado de la máscara de inpainting. |
| `--inpaint` | `auto` | `lama` (recomendado), `opencv` (fallback), `none`. |
| `--erase-box` | ninguna | Caja manual `x1,y1,x2,y2`, repetible. |
| `--mask` | ninguna | Máscara manual; blanco = borrar. |
| `--padding` | `8` | Aire transparente alrededor del bbox. |
| `--square` | off | Completar a lienzo cuadrado. |
| `--keep-largest` | off | Conservar solo el componente alpha más grande (sujeto principal). |
| `--min-component` | `0.0` | Área mínima relativa al componente mayor; descarta islas chicas. |
| `--alpha-threshold` | `8` | Alpha mínimo para calcular el bbox. |
| `--despill` | off | Quita color del fondo en bordes semitransparentes. |
| `--use-existing-alpha` | off | Si la entrada ya tiene alpha, lo usa en vez de segmentar. |

## Cómo evitar la muesca del ala

La muesca casi nunca la causa el modelo de inpainting: la causa una máscara que incluye píxeles del sujeto que no había que borrar.

- **No uses una caja rectangular** sobre texto que cruza el ala. La caja incluye el borde del ala y el inpainting lo borra.
- La máscara tiene que seguir **los trazos**. `--text-color` con el color exacto del texto es el modo más preciso; `auto` intenta agrupar colores y quedarse con los componentes que parecen letras.
- El modo `auto` es conservador: si no encuentra letras claras, devuelve `auto-uncertain` / `applied: false` y **no borra**. Eso evita la muesca, pero deja el texto; en ese caso pasá `--text-color` o `--mask`.
- Dilatá apenas 1–2 px. Una dilatación grande vuelve a meter el borde del ala en la máscara. Si queda un halo o un fantasma, probá `--mask-expand 3` y revisá de nuevo.
- Si el texto es negro sobre un ala azul, `--text-color "#111111" --mask-threshold 30` aísla solo las letras y deja intacto el borde.
- Si el texto tiene anti-aliasing, `--mask-feather 1.0` ayuda a que el inpainting no deje un contorno duro.
- Si la muesca ya existe en la imagen original, no la “segmentes”: creá una máscara manual de la muesca y corré `--no-ocr --mask ...`. El inpainting tiene que reconstruir el borde, no recortarlo.
- Para ilustración plana, LaMa copia color plano y líneas del entorno mejor que un modelo generativo. Si LaMa no está disponible y cae a OpenCV, revisá el resultado con más cuidado: Telea suele dejar manchas en áreas grandes.

## Verificación obligatoria

Validación rápida del PNG:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\validate.ps1" "C:\ruta\salida_clean.png"
```

Antes de decir que está listo:

- [ ] El PNG tiene canal alpha y las cuatro esquinas son transparentes.
- [ ] El sujeto no tiene agujeros ni partes comidas.
- [ ] El texto/logo desapareció por completo; no quedan restos ni rectángulos smudge.
- [ ] En el JSON, las regiones que querías borrar tienen `"applied": true`; si alguna dice `false`, se avisó y hay que resolverla con `--text-color` o `--mask`.
- [ ] El borde del ala/contorno es continuo; no hay muesca.
- [ ] El bbox está ajustado, con el padding pedido y sin clipping.
- [ ] El preview se lee bien sobre checkerboard, fondo oscuro y fondo claro.
- [ ] El reporte JSON no tiene `warnings` sin revisar.
- [ ] No se sobrescribió la imagen original.

## Limitaciones

- RapidOCR puede no detectar texto muy estilizado, manuscrito o deformado. En ese caso usá `--erase-box` o `--mask`.
- Un logo multicolor puede necesitar máscara manual; `kmeans` asume dos colores dominantes.
- Imágenes mayores a 6000 px pueden ser lentas. El matting CF se omite automáticamente arriba de `--matting-max-dim` salvo `--force-matting`.
- El inpainting de áreas muy grandes puede inventar textura. Mantené la máscara pegada al texto/logo.
- Borrar watermarks o logos puede tener implicancias legales. Confirmá que tenés derecho a editar la imagen.

## Archivos que genera

Para `ilustracion.png`:

- `ilustracion_clean.png` — PNG transparente final, recortado.
- `ilustracion_preview.png` — composite sobre checkerboard para inspección.
- `ilustracion_alpha.png` — canal alpha en escala de grises.
- `ilustracion_erase-mask.png` — máscara de texto/logo usada para inpainting.
- `ilustracion_cleaned.png` — imagen intermedia después del inpainting.
- `batch_summary.json` — reporte y resumen.

## Troubleshooting

- **No hay Python/uv**: `run.ps1` llama a `bootstrap.ps1` y descarga `uv` + Python 3.12 dentro de `tools\`. No requiere admin.
- **Execution policy**: invocá con `powershell -NoProfile -ExecutionPolicy Bypass -File ...`.
- **Falla la descarga de un modelo**: probá `--model isnet-general-use`; el pipeline hace fallback automático y lo reporta en `warnings`.
- **LaMa no carga**: el pipeline cae a OpenCV si `--inpaint auto`; para forzar el error usá `--inpaint lama`.
- **El texto sigue apareciendo**: mirá `text_detections` y `mask_methods` en el JSON; si OCR no lo detectó, agregá `--erase-box`.
- **Se comió parte del dibujo**: bajá `--mask-expand`, subí `--mask-threshold`, o usá `--text-color`/`--mask-mode kmeans`.
- **Borde con halo**: probá `--despill` o `--matting cf`.

## Notas técnicas

- `pipeline.py` importa `rembg`, `rapidocr_onnxruntime` y `simple_lama_inpainting` de forma perezosa, así `--help` funciona aunque falte un modelo.
- Los modelos se guardan en `models\rembg`, `models\torch` y `models\hf` dentro de la skill.
- La entrada nunca se modifica. Si el nombre de salida existe, se agrega `-1`, `-2`, etc. salvo `--overwrite`.
- El reporte incluye `mask_methods` para saber si la máscara fue `kmeans`, `border`, `box` o un fallback. Si ves `box-fallback`, revisá esa región.
