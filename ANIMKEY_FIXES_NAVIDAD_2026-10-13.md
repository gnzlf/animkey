# AnimKey: correcciones y verificacion

Fecha: 2026-10-13. Entorno probado: mayapy de Maya 2024, Windows.

## Cambios

- Offset: los keys nuevos entre keys existentes conservan la referencia previa
  al cambio y propagan el delta al rango. Resolucion del destino por canal cuando
  la capa preferida no contiene ese control. Cambio de capa activa entre gestos.
  Se conserva una operacion Undo por arrastre, incluyendo boton central y lapiz;
  el cierre se hace despues de procesar el evento final de Maya.
- Draw: Windows Ink entrega posicion y presion juntas, sin depender de eventos
  de mouse sinteticos. Se evitan eventos duplicados de Qt/mouse. Normalizacion
  de presion nativa, punto visible desde pen-down y precalentamiento del canvas.
  Las onion skins no se reconstruyen durante el trazo.
- Sel Sets: carga de botones en bloque, ocultacion inmediata de widgets antiguos,
  seleccion explicita de la pagina importada y bloqueo de guardados parciales.
  Importar agrega pestanas; un fallo restaura el estado anterior. Compatibilidad
  con JSON UTF-8 con BOM y posiciones de ventanas de otros monitores.
- Barra: workspace con comportamiento de barra nativa, sin pestana propia.
  Despliegue de tangentes reducido a 120 ms y un refresco ligero compartido por
  ciclo, en lugar de reconstruir la barra por cada boton en cada ciclo.
- Temp Pivot: merge con Smart Bake e Increase Fidelity (tolerancia 0.1) hacia
  la capa de origen. Solo canales compensados y tramo afectado, mas un frame de
  guarda por extremo. Restauracion explicita de keys externos: el bake nativo
  sobre layers no siempre conserva esos keys por si solo.
- Copy/Save/Paste: mapa de propiedad de curvas por capa calculado una sola vez,
  rutas de controles normalizadas, guardado de todas las capas en la biblioteca,
  padres de layers, modos de acumulacion y curvas de peso animadas. Escritura
  asincrona con snapshot independiente y prioridad para el ultimo guardado.
  Save to Library no reemplaza los metadatos del clipboard actual.

## Pruebas automatizadas

57 pruebas aprobadas en conjunto:

| Archivo en `.github/tests/maya` | Pruebas |
| --- | ---: |
| test_offset_copy_animo_regressions.py | 12 |
| test_brush_rendering.py | 18 |
| test_sets_import.py | 2 |
| test_copy_storage.py | 3 |
| test_slider_copy_layers_regressions.py | 3 |
| test_temp_pivot_animation_preservation.py | 5 |
| test_temp_pivot_controls_audit.py | 12 |
| test_toolbar_tangent_transition.py | 1 |
| test_animation_curve_batch.py | 1 |

Ademas, `test_navidad_integration.py` abre el archivo indicado por
`ANIMKEY_TEST_SCENE` en un proceso aislado. Nunca guarda el archivo fuente.

Escena: `Navidad-SH01_3D Animation_v0022.ma`, con 8110 curvas y las capas
`BaseAnimation`, `AnimLayer1`, `OverlapHelper1` y `OverlapHelper2`.

- Offset y Undo en BaseAnimation y las tres capas, verificando los keys dentro
  del rango 4-30 y que los keys externos no cambien.
- Captura y transferencia a una escena nueva de 15 controles y 107 curvas.
  Comparacion de tiempos, valores, tangentes, locks, breakdowns e infinitos;
  tolerancia numerica de 1e-6 relativa / 1e-7 absoluta.
- Temp Pivot en `Mama_Lucha_Rigging:cloak_neck_FK_01_ctrl`, merge hacia
  `OverlapHelper2` y comparacion de matrices mundo en frames 8, 9 y 10.
- Medicion final de la muestra: 0.132 s para capturar y 0.105 s para pegar.
  El escenario Offset de Andresito (activacion, dos cambios y dos Undo) bajo
  de 7.618 s a 0.012 s al evitar buscar curvas de capas ajenas. Los otros
  escenarios finales de Offset midieron entre 0.011 s y 0.021 s.
  No es un benchmark de todos los rigs completos ni de la UI interactiva.

## Limites y comprobaciones pendientes

- La Huion fisica necesita una comprobacion real del usuario. Las pruebas
  automatizadas validan mensajes Windows Ink, presion variable y pixeles del
  trazo; no simulan el driver fisico. El driver debe entregar eventos de lapiz
  con presion, por Qt/Wintab o Windows Ink, no solo mouse.
- Las pruebas se hicieron por codigo, sin mover mouse ni teclado, en mayapy.
  El comportamiento del dock dentro de la ventana interactiva de Maya y la
  sensacion del lapiz requieren revision visual en esa sesion.
- La escena se abre con advertencias de plugins externos ausentes. Se validaron
  las curvas y los controles enumerados, no el render ni cada nodo de plugin.
- No se han certificado Maya 2022, 2023, 2025, 2026 ni 2027 en esta revision.
- No se promete exactitud universal para rigs/capas sin curvas editables,
  canales bloqueados, capas sin influencia o canales gobernados por expresiones.
- Los archivos instalados se cargaran completamente al reiniciar Maya. No se
  ha cerrado, reiniciado ni guardado la sesion de trabajo del usuario.
