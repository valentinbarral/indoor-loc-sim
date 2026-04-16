# Indoor Localization Simulator — Tutorial paso a paso

Este tutorial explica un flujo de trabajo completo de principio a fin:

1. Crear una planimetría
2. Añadir balizas
3. Definir una trayectoria
4. Generar señales RSS o ToF
5. Ejecutar algoritmos de localización
6. Comparar errores
7. Guardar y volver a cargar el proyecto

---

## 0. Antes de empezar

Instala las dependencias:

```bash
uv sync
```

Ejecuta la aplicación:

```bash
uv run indoor-loc-sim
```

Cuando se abra la aplicación, verás cinco pestañas:

1. **Planimetry**
2. **Trajectories**
3. **Signals**
4. **Estimation**
5. **Error Analysis**

![Visión general de la ventana principal](images/tutorial/01-main-window-overview.png)

---

## 1. Crear la planimetría

Ve a la pestaña **Planimetry**.

En esta pestaña puedes:

- crear uno o más niveles
- definir las dimensiones del nivel en metros
- cargar una imagen de plano
- calibrar la escala de la imagen
- dibujar muros y puertas
- añadir balizas

El título de la ventana muestra el nombre del fichero abierto actualmente. Si el edificio tiene cambios sin guardar, aparece un `*` antes del nombre del fichero.

### 1.1 Crear o seleccionar un nivel

Usa el cuadro **Levels** de la izquierda:

- haz clic en **Add Level** si lo necesitas
- selecciona el nivel activo en la lista

Después define:

- **Width (m)**
- **Height (m)**
- **Floor height (m)**

Si no tienes una imagen de fondo, la aplicación usa una rejilla métrica.

![Nivel de planimetría vacío](images/tutorial/02-planimetry-empty-level.png)

### 1.2 Cargar una imagen de plano (opcional)

Haz clic en **Load Floor Plan...** y elige una imagen PNG.

Después ajusta:

- **Scale (px/m)**
- **Width / Height (m)**

Usa el checkbox **Show floor plan** para mostrar u ocultar la imagen sin perder la geometría dibujada. Cuando la imagen se oculta, el canvas muestra un fondo blanco con rejilla métrica en lugar de una escena vacía.

![Plano cargado](images/tutorial/03-planimetry-floorplan-loaded.png)

### 1.3 Dibujar muros y puertas

Usa la barra de herramientas situada sobre el canvas:

- **Select**
- **Rectangle select**
- **Pan**
- **Beacon**
- **Wall**
- **Door**
- **Room**
- **Fit view**
- **Snap**

Notas de navegación:

- la herramienta **Pan** permite mover la vista de forma persistente
- también puedes mover la vista temporalmente arrastrando con el **botón central del ratón**

Para dibujar muros y puertas:

1. selecciona la herramienta **Wall** o **Door**
2. haz clic en el punto inicial
3. haz clic en el punto final

Notas sobre selección y borrado:

- en modo **Select** puedes seleccionar directamente muros, puertas y balizas
- las puertas colocadas sobre muros se pueden seleccionar de forma independiente del muro inferior
- los elementos seleccionados se pueden borrar con **Delete** o **Backspace**

Para dibujar una habitación rectangular simple:

1. selecciona la herramienta **Room**
2. arrastra un rectángulo sobre el canvas

Opciones útiles:

- **Snap** para alinear la geometría a la rejilla
- **Snap spacing** para controlar el paso
- **Wall color** para mejorar la visibilidad

![Muros, puertas y habitación](images/tutorial/04-planimetry-walls-doors-room.png)

---

## 2. Añadir y editar balizas

### 2.1 Colocar balizas

Selecciona la herramienta **Beacon** en la barra de planimetría y haz clic en el canvas para colocar balizas.

Cada baliza aparece:

- sobre el canvas
- en la lista de balizas de la izquierda

### 2.2 Editar propiedades de las balizas

Para cada baliza puedes editar:

- **Label**
- **Tx Power (dBm)**

También puedes:

- mover balizas directamente arrastrándolas en el mapa
- eliminar la baliza seleccionada
- borrar todas las balizas

Cualquier movimiento de balizas en planimetría se propaga al resto de pestañas.

### Recomendación práctica

Para una primera prueba, coloca 4 balizas cerca de las esquinas de la planta.

![Colocación de balizas](images/tutorial/05-planimetry-beacons.png)

---

## 3. Crear una trayectoria

Ve a la pestaña **Trajectories**.

Esta pestaña se usa para definir el movimiento del usuario simulado.

### Parámetros

- **Walking speed (m/s)**
- **Sampling frequency (Hz)**

Estos dos valores definen la trayectoria final discretizada en el tiempo.

### 3.1 Añadir waypoints

Haz clic en **Draw Trajectory Mode** y después haz clic en el mapa para añadir waypoints.

La lista de waypoints de la izquierda se actualiza automáticamente.

### 3.2 Generar la trayectoria

Haz clic en **Generate!**

La aplicación crea una trayectoria ground-truth con:

- camino espacial interpolado
- tiempos asignados
- velocidades estimadas
- remuestreo final a la frecuencia seleccionada

La trayectoria generada se dibuja en:

- el canvas de la pestaña de trayectorias
- el overlay compartido del canvas de planimetría

![Waypoints de la trayectoria](images/tutorial/06-trajectory-waypoints.png)

![Trayectoria generada](images/tutorial/07-trajectory-generated.png)

---

## 4. Generar señales

Ve a la pestaña **Signals**.

Esta pestaña genera medidas para todas las balizas a lo largo de la trayectoria ground-truth.

### Tipos de señal disponibles

- **RSS**
- **ToF**

### Parámetros

#### Configuración de señal

- **Signal type**
- **Samples per point**

#### Parámetros de ruido

- **RSS σ**
- **ToF σ (ns)**

#### Modelo de propagación

- **A (RSSI at d₀)**
- **d₀ (ref. distance)**
- **Wall attenuation (dB)**
- **NLoS mode (ToF)**
- **NLoS error multiplier**
- **Path loss exponent**

### 4.1 Generar medidas RSS

Para un primer ejemplo:

- elige **RSS**
- fija **Samples per point = 1** o más
- elige un valor razonable para **RSS σ**
- haz clic en **Generate Signals**

La gráfica de la derecha muestra la evolución temporal de la señal para cada baliza.

![Configuración de señal RSS](images/tutorial/08-signals-rss-config.png)

![Gráfica de señal RSS](images/tutorial/09-signals-rss-plot.png)

### 4.2 Generar medidas ToF

Para simular ToF en su lugar:

- elige **ToF**
- fija **ToF σ (ns)**
- opcionalmente configura el tratamiento de NLoS
- haz clic en **Generate Signals**

Esto crea internamente medidas de tiempo de vuelo en segundos.

### 4.3 Mostrar un heatmap RSS

Todavía dentro de la pestaña Signals, puedes visualizar la cobertura RSS:

1. elige una baliza concreta o **All (average)**
2. elige **Resolution (m)**
3. haz clic en **Show Heatmap**

El panel derecho cambia de la gráfica temporal a una vista de heatmap.

![Heatmap RSS](images/tutorial/10-signals-heatmap.png)

---

## 5. Ejecutar algoritmos de localización

Ve a la pestaña **Estimation**.

Esta pestaña ejecuta algoritmos de localización sobre las señales generadas.

### Algoritmos disponibles

- **EKF + RSS**
- **EKF + ToF**
- **EKF + RSS + Accel**
- **UKF + RSS**
- **Trilateration + ToF**
- **Trilateration + RSS**
- **Fingerprint + RSS**

El panel de parámetros es dinámico: los controles que no afectan al algoritmo seleccionado se deshabilitan automáticamente.

### 5.1 EKF + RSS

Selecciona **EKF + RSS**.

Parámetros relevantes:

- **Process noise σ**
- **Measurement noise σ (dB)**

Después haz clic en **Run Estimation**.

El resultado:

- se añade al historial de simulaciones
- se dibuja en el mapa
- se guarda para el análisis posterior de errores

![Configuración de EKF RSS](images/tutorial/11-estimation-ekf-rss.png)

![Resultado de EKF RSS](images/tutorial/12-estimation-ekf-rss-result.png)

### 5.2 EKF + ToF

Selecciona **EKF + ToF**.

Parámetros relevantes:

- **Process noise σ**
- **Measurement noise σ (ns)**

Este algoritmo usa internamente rangos derivados de ToF y estima la posición en 2D con `z` conocida.

### 5.3 EKF + RSS + Accel

Selecciona **EKF + RSS + Accel**.

Parámetros relevantes:

- **Process noise σ**
- **Measurement noise σ (dB)**
- **Accelerometer noise variance**

Este algoritmo simula medidas de acelerómetro a partir de la trayectoria ground-truth y las fusiona con RSS.

Es especialmente útil para probar el comportamiento en trayectorias con giros.

### 5.4 Trilateration + ToF

Selecciona **Trilateration + ToF**.

No requiere parámetros de filtro.

Requisitos:

- la señal ToF debe estar activa
- al menos 3 balizas válidas en cada paso de estimación

Si en un paso dado hay menos de 3 rangos válidos, el algoritmo mantiene la estimación anterior.

### 5.5 Trilateration + RSS

Selecciona **Trilateration + RSS**.

Este algoritmo ahora:

- elige siempre las **3 balizas con RSS más alta** en cada instante
- convierte RSS a distancia usando los parámetros de propagación usados en la generación de señales
- reutiliza la estimación anterior si hay menos de 3 medidas válidas

Este método suele ser bastante menos estable que los métodos basados en filtros de Kalman.

### 5.6 Fingerprint + RSS

Selecciona **Fingerprint + RSS**.

Parámetros relevantes:

- **Grid spacing (m)**
- **k (neighbors)**
- **Auto-scale k with grid density**
- **Samples per point**
- **Distance metric**

Cuando lo ejecutas, la aplicación primero construye un radio map y después estima cada punto de la trayectoria usando k-NN ponderado.

![Configuración de fingerprint](images/tutorial/13-estimation-fingerprint-config.png)

![Overlay de fingerprint](images/tutorial/14-estimation-fingerprint-overlay.png)

---

## 6. Comparar errores de localización

Ve a la pestaña **Error Analysis**.

Esta pestaña compara todas las simulaciones almacenadas.

### Gráficas disponibles

- **CDF of Errors**
- **Error over Time**
- **X Error over Time**
- **Y Error over Time**

### Flujo de uso

1. elige el tipo de gráfica
2. activa o desactiva las simulaciones que quieres comparar
3. inspecciona la tabla **Summary**
4. opcionalmente exporta a CSV

El resumen incluye:

- Mean
- P50
- P90
- Max

Todos los valores están en metros.

![Comparación CDF](images/tutorial/15-analysis-cdf.png)

![Serie temporal del error](images/tutorial/16-analysis-time-series.png)

---

## 7. Guardar y volver a cargar un proyecto

Usa el menú **File**:

- **Save Building**
- **Save Building As...**
- **Save Project**
- **Save Project As...**
- **Open Project...**

Comportamiento estándar de guardado:

- si has abierto un JSON de edificio, **Save Building** sobrescribe ese mismo fichero
- si has abierto un fichero de proyecto, **Save Project** sobrescribe ese mismo proyecto
- **Save ... As** crea un nuevo fichero en otra ruta
- si el edificio tiene cambios sin guardar y cierras la ventana, la aplicación ofrece **Save**, **Cancel** o **Exit without saving**

El formato actual de proyecto `.ilsim` almacena:

- edificio
- waypoints
- trayectoria ground-truth
- señales generadas
- simulaciones anteriores
- imágenes de planos

Esto significa que puedes cerrar la aplicación y continuar más tarde con el mismo estado del proyecto.

---

## 8. Primer experimento recomendado

Si quieres una demo simple y reproducible:

1. Crea un nivel de **30 × 20 m**
2. Coloca **4 balizas** cerca de las esquinas
3. Dibuja una trayectoria curva con varios waypoints
4. Genera señales **RSS** con ruido bajo
5. Ejecuta:
   - EKF + RSS
   - UKF + RSS
   - Trilateration + RSS
   - Fingerprint + RSS
6. Compara todas las simulaciones en **Error Analysis**

Después repite variando:

- un ruido RSS más alto
- un exponente de pérdidas diferente
- más muros

Esto ayuda a entender bien la sensibilidad de cada método.

---

## 9. Problemas habituales

### La señal no coincide con el algoritmo seleccionado

Ejemplo:

- has generado señales RSS
- pero has seleccionado **Trilateration + ToF**

En ese caso, la aplicación rechazará la simulación y mostrará una advertencia en el panel de resultados.

### La trilateración con RSS es inestable

Esto es esperable.

Motivos:

- RSS se convierte en distancia de forma indirecta
- el modelo es sensible al ruido y a desajustes de calibración
- los muros y un exponente de pérdidas incorrecto pueden producir errores grandes

### Se modifica la planimetría después de simular

Si mueves balizas o cambias muros después de generar señales o ejecutar estimación, las señales y resultados anteriores pueden dejar de ser físicamente coherentes con el nuevo layout.

---
