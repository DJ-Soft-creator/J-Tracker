# Design Guide – Visuelle Spezifikation

Diese Datei beschreibt das aktuell gültige Journl-Designsystem.

Die folgenden Spezifikationen definieren das Erscheinungsbild dieser App-Familie. Sie können als Referenz dienen, um neue Applikationen mit demselben visuellen Stil zu erstellen.

---

## Farbschema

### Grundfarben

| Rolle | Farbe | Hex | Tailwind |
|-------|-------|-----|----------|
| **Page Background** | Fast Schwarz | `#030711` | `gray-950` |
| **Surface / Panel** | Dunkelgrau | `#111827` | `gray-900` |
| **Input Field BG** | Noch dunkler | `#1f2937` | `gray-800` |
| **Field Border** | Mittlere Darkborder | `#374151` | `gray-700` |
| **Secondary Border** | Dehne Border | `#4b5563` | `gray-600` |
| **Primary Accent** | Grün (aktiv) | `#4ade80` | `green-400` |
| **Primary Button BG** | Sattires aktiv | `#16a34a` | `green-600` |
| **Primary Button HOVER** | Helleres Grun | `#22c55e` | `green-500` |
| **Secondary Button** | Grau muted | `#374151` | `gray-700` |
| **Secondary Button Hover** | heller gris | `#6b7280` | `gray-600` |
| **Error / Danger** | Rot | `#ef4444` | `red-600` |
| **Error Light** | Hellrot Text | `#f87171` | `red-400` |
| **Info / Blue Accent** | Blau (KI) | `#2563eb` | `blue-600` |
| **Success Light** | helle Grun text | `#22c55e` | `green-500` |

### Textfarben

| Rolle | Farbe | Tailwind |
|-------|-------|----------|
| **Haupttext / Headings** | Fast Weiß | `gray-100` `#f9fafb` |
| **Sekundärtext / Body** | Hellgrau | `gray-200` `#e5e7eb` |
| **Inaktiver Tab / Label** | Mittelgrau | `gray-300` `#d1d5db` |
| **Inaktiv Disabled / Caption** | Grau muted | `gray-400` `#9ca3af` |
| **Placeholder / Muted Text** | Dunkelgrau | `gray-500` `#6b7280` |
| **Disabled / Border Label** | Sehr dunkelgrau | `gray-600` `#4b5563` |

### Akzent-Grün als Markenfarbe

Das Grün (`green-400` / `#4ade80`) ist die dominante visuelle Identifikationsfarbe der App:
- Aktiver Tab-Inaktivator (Unterstreichung + Textfarbe)
- Submit-Button-Hintergrund (hover: helleres Grün)
- Header-Logo-Schrift
- Edit/Save-Buttons, Erfolgsmeldungen
- Fokus-Ring um Inputs

---

## Typografie

- **UI-Klaviatur:** System-Family (Tailwind default sans-serif: Inter, -apple-system, Segoe UI)
- **Monospace-Komponenten (Uhr, Datei-Namen):** `font-mono` = System Monospace (SF Mono / Menlo)
- **Fett:** Für Headings und Labels (`font-bold` / `font-semibold`)
- **Schriftgrößen-Hierarchie:**

| Ebene | Größe | Tailwind-Klasse | Verwendungsbeispiel |
|-------|-------|-----------------|-------------------|
| App Title (Logo) | 1.875rem | `text-3xl` / `text-xl` | Login-Overlay, Header |
| Section Headings | 1.125rem | `text-xl` / `lg:text-lg` | Tab-Titel |
| Body text in Cards | 0.875rem | `text-sm` | Formular-Inhalt, Einträge |
| Labels / Captions | 0.875rem | `text-sm` | Field labels |
| Clock / Metadata | 0.75rem | `text-xs` | Datum/Uhrzeit-Anzeige |

---

## Komponenten-Spezifikation

### Login Overlay

- **Overlay:** `fixed inset-0 z-50 bg-gray-950` (vollständig abgedeckt, voller Viewport)
- **Panel:** `bg-gray-900 border border-gray-800 rounded-xl p-6 max-w-sm w-full shadow-xl`
- **Titel („Journl"):** `text-3xl font-bold text-center text-green-400 mb-8`
- **Sub-Titel (Label):** `text-xl font-semibold text-center mb-6`
- **Input Felder:** `rounded-lg bg-gray-900 border border-gray-700 px-4 py-2.5 text-gray-100 focus:ring-2 focus:ring-green-500 w-full block mb-4/mb-6`
- **Submit Button:** `bg-green-600 hover:bg-green-500 text-white font-semibold py-2.5 px-4 rounded-lg w-full transition`
- **Fehlanzeige:** `text-red-400 text-sm text-center mt-3 hidden` (wird via JS show/hidetog)

### Header

- **Layout:** `bg-gray-900 border-b border-gray-800 flex items-center justify-between h-[48px] shrink-0` (feststehend, kein Scrollen)
- **Logo-Text:** `text-lg font-bold text-green-400`
- **Rechts:** Clock `font-mono text-xs text-gray-500` + Logout-Link `text-sm text-gray-500 hover:text-white transition`

### Tab-Navigation

- **Nav-Bar:** `bg-gray-900 border-b border-gray-800 flex shrink-0`
- **Tab Button (aktiv):**
  - `flex-1 px-5 py-3 text-sm font-medium border-b-2 border-green-400 text-green-400 transition`
  - Grüner Text, grüne Unterstreichung (`border-green-400`)
- **Tab Button (inaktiv):**
  - `flex-1 px-5 py-3 text-sm font-medium border-b-2 border-transparent text-gray-500 hover:text-gray-300 transition`
  - Grauer Text, kein Border-Rand

### Input Area („Schreiben" Tab)

#### Einfaches Template (Single Textarea)
- **Textarea:** `bg-gray-900/50 border border-gray-800 rounded-lg text-gray-100 focus:outline-none focus:border-gray-600 resize-none px-[50px] py-[12px] w-full`
- Desktop-Hintergrund: leicht transparenter Overlay (`bg-gray-900/50`) für Tiefe-Dreierlei
- Mobile-Variante: transparent ohne Hintergund (`bg-transparent`)

#### Formular-Templates – Layout
- **Input-Felder (text/number):**
  - `rounded-lg bg-gray-800 border border-gray-700 px-[14px] py-[12px] text-gray-100 focus:ring-2 focus:ring-green-500 w-full`
  - Fokus: grüner Ring (`ring-2 green-500`), Border hellt sich auf (`border-green-400`)
- **Label oben:**
  - `block text-sm font-medium mb-1 text-gray-300`
- **Textarea-Felder:** identisch zu Input + `rows="3"`

#### Template-Selector + Submit Bar (Desktop)
- **Position:** statisch unterhalb der Input-Area, über der App-Info-Box
- **Dropdown-Select:**
  - `rounded-lg bg-gray-800/10 border border-gray-700/10 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500 appearance-none`
- **AI Select:**
  - Selbs wie oben, aber `focus:ring-blue-500` (blauer Fokus-Ring als visueller Unterschied für KI)
- **Submit Button:**
  - `bg-green-600/70 hover:bg-green-500 text-white font-semibold py-2 px-5 rounded-lg transition text-sm shrink-0`

#### App-generated Information-Box (Info-Bereich)
- **"App Info Box":**
  - `w-full max-w-lg mx-auto bg-gray-800/10 border border-gray-700/10 rounded-xl px-4 py-3 text-sm leading-relaxed pointer-events-none select-none text-gray-400 whitespace-pre-wrap`
- **KI-Antwort Box:**
  - `w-full max-w-lg mx-auto bg-gray-800/20 border border-blue-700/30 rounded-xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap text-gray-300`

### Dashboard (Einträge-Tabelle)

#### Container
- **Scroll-Bereich:** `flex-1 overflow-y-auto px-[50px] py-3 space-y-3`
- **Leerer State:** center aligned gray text - `text-gray-500 text-center mt-8`

#### Einzelner Eintrag (Journal-Entry Card)
- **Container-Karte:** `bg-gray-900 rounded-xl p-4 border border-gray-800 w-full`
  - Abgerundete Ecken (`rounded-xl`) für weiches Aussehen
  - Dezente Border (`border-gray-800`) als subtiler Abgrenzer zum Hindergrund
  
- **Metadaten-Zeile:**
  - `text-xs text-gray-600 mb-2 font-mono flex items-center justify-between`
  - Zeigt Dateiname (Monospace, sehr dunkles Grau)
  
- **Edit/Löschen-Buttons:**
  - `text-green-500 hover:text-green-400 transition` – Bearbeiten (Bleistift Emoji)
  - `text-red-500 hover:text-red-400 transition` – Löschen (Mülleimer Emoji)
  - Beide: `flex items-center gap-1` nebeneinander rechts
  
- **Entry-Inhalt:**
  - `text-gray-200 text-sm whitespace-pre-wrap line-height-relaxed`
  - Monospace nicht, sondern normaler Zeilenabstand für Lesbarkeit
  
- **Inline Edit-Textarea:**
  - `rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500`
  
- **Edit-Aktionen (Speichern / Abbrechen):**
  - Speichern: `bg-green-600 hover:bg-green-500 text-white text-xs py-1.5 px-3 rounded transition`
  - Abbrechen: `bg-gray-700 hover:bg-gray-600 text-white text-xs py-1.5 px-3 rounded transition`

### Toast-Benachrichtigung (Bestätigung/ Fehler)

- **Position:** `fixed bottom-6 left-1/2 -translate-x-1/2 z-50`
- **Grüner Status:** `bg-green-600 text-white px-5 py-2.5 rounded-lg shadow-xl font-medium text-sm transition-opacity`
- **Roter Fehler:** same structure mit `bg-red-600`
- **Opacity:** fade-in / fade-out via `opacity-0 pointer-events-none transition-opacity duration-300`

### Desktop-spezifisch: Resize-Handle

- Ein schmaler Griff zwischen Input-Area und Controls-Bar:
  - `position:absolute bottom-0 left/right 0 h-[16px] cursor-ns-resize z-index-10`
  - Innerhalb: eine kleine horizontal line (mit `::after`)
    - `content:'' display block w-[64px] h-[5px] bg-gray-600 / rounded-full`
    - Hover: `bg-gray-400`
  - Ermöglicht Drag-Ziehen um die Height der Input Area anzupassen

---

## Layout & Struktur

### Mobile (index.html)

```
┌───────────────────────┐
│       Header          │  ← App Title + Clock + Logout
├───────────────────────┤
│   Write │ Today Tabs  │  ← Tab Navigation
├───────────────────────┤
│                       │
│     Input Area        │  ← Scrollable, fixed height (35%)
│     [Textarea] /      │
│     [Form Fields]     │
│                       │
│  ┌─────────────────┐  │  ← Template Selector + Submit
│  │ Select  Send    │  │     Absolutely positioned (top: 37%)
│  └─────────────────┘  │
│                       │
│   App Info Box        │  ← Non-editable hint text
│                       │
├───────────────────────┤
│                       │
└───────────────────────┘
```

- **Input Area:** `h-[35%]` fest, scrollable bei zu viel Inhalt
- **Selector/Submit Bar:** absolut positioniert (`top: 37%`) → schwebt über der Input-Area
- **UI:** Mobile-first mit touch-größeren Feldern und Padding

### Desktop (desktop.html)

```
┌───────────────────────┐
│       Header          │  ← App Title + Clock + Logout
├───────────────────────┤
│   Write │ Today Tabs  │  ← Tab Navigation
├───────────────────────┤
│                       │
│     Input Area        │  ← User-drag resize handle (h:35%-75%)
│    [Textarea/Form]    │
│                        │
│  ━━━━━━━━━━━━━━━━   │  ← Resize-Handle (drag)
│  ┌─────────────────┐  │  ← Template Selector + Submit
│  │ Select  Send    │  │     Statisch positioniert unter Input Area
│  └─────────────────┘  │
│                       │
│   App Info Box        │  
│                       │
├───────────────────────┤
│                       │
└───────────────────────┘
```

- **Input Area:** `h-[35%]` als Start-Höhe, per Resize-Handle auf bis zu 75% der Viewport-Höhe erweiterbar
- **Selector/Submit Bar:** statisch positioniert (nicht absolut) unterhalb des Eingabebereichs
- **UI Desk**Top-first mit Maus-Gesten

---

## Animationen & Übergänge

### Input-Felder beim Löschen (`wipe-out` animation)

```css
@keyframes wipeOut {
  0%   { opacity: 1; transform: translateY(0); filter: blur(0); }
  60%  { opacity: 0; transform: translateY(-8px); filter: blur(4px); }
  100% { opacity: 0; transform: translateY(-8px); filter: blur(4px); }
}
/* Anwendung bei submit */
.wipe-out { animation: wipeOut 0.35s ease-out forwards; }
```

### Toast-Fade

- fade-in: `opacity-0 → opacity-100` über `duration-300`
- Fade-out: automatisch nach 2 Sekunden via JS timeout

### Tab-Wechsel

- Subtile Border-Farbe Übergang `border-b-2 transition` auf tab-buttons
- Kein content-transition (inhalt nur sichtbar/verborgen durch display)

### Submit-Button Loading State

- `opacity-60 cursor-not-allowed`
- Text wechselt zu „..."

---

## Dark-Only – Durchgängiges Design-Prinzip

Die gesamte App verwendet ausschließlich dunkle Farbwerte. Es gibt keine hellen (Light Mode) Varianten:
- Kein Toggle Light/Dark
- Kein `light:` oder `dark:` variant-prefix in Tailwind-Klassen
- Alle Farben sind fest auf die `gray-9xx` bzw. `gray-xxx` Skalen gemapped

Dies erzeugt eine durchgehende "Midnight"-Ästhetik: konsequent dunkle Oberfläche mit grünem Akzent als „Leitsignal" des Designs.

---

## Quick-Copy-Spezifikation für neue Projekte

Um neue Web-Apps mit diesem Design zu beginnen, brauchten nur die folgenden Tailwind-Klassen-Packs:

```
Page bg:         bg-gray-950 text-gray-100
Panel/Surface:   bg-gray-900 border border-gray-800 rounded-xl
Input BG:        bg-gray-800 border border-gray-700 rounded-lg
Text primary:    text-gray-100
Text body:       text-gray-200
Text muted:      text-gray-400
Text disabled:   text-gray-500 / text-gray-600
Button Primary:  bg-green-600 hover:bg-green-500 text-white font-semibold rounded-lg transition
Tab active:      border-b-2 border-green-400 text-green-400
Tab inactive:    border-b-2 border-transparent text-gray-500 hover:text-gray-300
Focus ring:       focus:ring-2 focus:ring-green-500
Divider:         border-b border-gray-800
Select:          bg-gray-800/10 border border-gray-700/10 rounded-lg px-3 py-2 appearance-none
Info Box:        bg-gray-800/10 border border-gray-700/10 rounded-xl text-gray-400
AI Response:     bg-gray-800/20 border border-blue-700/30 rounded-xl text-gray-300
Entry card:      bg-gray-900 border border-gray-800 rounded-xl p-4
Toast:           fixed bottom-6 z-50 rounded-lg shadow-xl px-5 py-2.5 font-medium
Font mono:       font-mono
```
