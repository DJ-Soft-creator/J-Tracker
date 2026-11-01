/* Hashtag-Map: die fuenf Sichten (Uebersicht, Hirn, Orbit, Cluster, Zeit).
 *
 * Rahmen, Daten, Zoom und Interaktion stehen in brain-tag-map.js; diese Datei
 * laedt danach und traegt ihre Zeichenfunktionen in kit.views ein. Jede Sicht
 * zeichnet in die Zoom-Gruppe, die stage() liefert. Hashtag-Namen nur als Text.
 */
(function () {
  const kit = window.journlBrainTagMapKit;
  if (!kit) return;
  const {
    state, el, stage, nodeGroup, edgeHit, curve, bindNode, placeTip, draggable,
    recencyColor, clusterColor, radiusFor, matches, CLUSTER_LIMIT, DAY,
  } = kit;

  // ─── Uebersicht ──────────────────────────────────────────────────
  // Sonnenblumen-Spirale nach Haeufigkeit (die groessten innen), danach
  // auseinandergeschoben, bis sich keine Kreise mehr ueberdecken. Passt das
  // Ergebnis nicht auf die Buehne, startet der Zoom eingepasst.
  function overviewLayout(width, height) {
    const tags = state.data.tags;
    const max = Math.max(1, ...tags.map((tag) => tag.count));
    const nodes = tags.map((tag, index) => {
      const angle = index * 2.39996;
      const distance = Math.sqrt(index + 0.5);
      return { tag, r: 3 + 11 * Math.sqrt(tag.count / max), ux: distance * Math.cos(angle), uy: distance * Math.sin(angle) };
    });
    const area = nodes.reduce((sum, node) => sum + (node.r + 3) ** 2, 0);
    const spacing = Math.sqrt(area / nodes.length) * 1.1;
    const stretch = Math.sqrt(Math.max(1, Math.min(2.2, width / height)));
    nodes.forEach((node) => {
      node.x = width / 2 + node.ux * spacing * stretch;
      node.y = height / 2 + node.uy * spacing / stretch;
    });
    for (let round = 0; round < 80; round += 1) {
      let moved = false;
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const need = a.r + b.r + 3;
          if (Math.abs(dx) >= need || Math.abs(dy) >= need) continue;
          const distance = Math.hypot(dx, dy) || 0.01;
          if (distance >= need) continue;
          const push = (need - distance) / 2;
          a.x -= (dx / distance) * push; a.y -= (dy / distance) * push;
          b.x += (dx / distance) * push; b.y += (dy / distance) * push;
          moved = true;
        }
      }
      if (!moved) break;
    }
    applySaved(nodes, 'overview', width, height);
    const left = Math.min(...nodes.map((node) => node.x - node.r));
    const right = Math.max(...nodes.map((node) => node.x + node.r + 60));
    const top = Math.min(...nodes.map((node) => node.y - node.r));
    const bottom = Math.max(...nodes.map((node) => node.y + node.r));
    const k = Math.min(2, (width - 24) / (right - left), (height - 40) / (bottom - top));
    const fit = { k, x: width / 2 - ((left + right) / 2) * k, y: (height - 16) / 2 - ((top + bottom) / 2) * k };
    return { nodes, width, height, fit, index: new Map(nodes.map((node) => [node.tag.name, node])) };
  }

  function drawOverview() {
    const { svg, width, height } = stage();
    if (!state.overview || state.overview.width !== width || state.overview.height !== height) {
      state.overview = overviewLayout(width, height);
      state.zoom = null;
    }
    const { nodes, index } = state.overview;
    const lines = el('g', {}, svg);
    const layer = el('g', {}, svg);
    // Namen auf eigener Ebene ueber allen Knoten, sonst verdeckt sie der Nachbar.
    const names = el('g', { 'pointer-events': 'none' }, svg);
    nodes.forEach((node) => {
      const group = nodeGroup(layer, node.tag, node.x, node.y, node.r, recencyColor(node.tag.step), false);
      const label = el('text', { class: 'map-label' }, names);
      label.textContent = `#${node.tag.name}`;
      label.dataset.labelR = node.r.toFixed(1);
      label.dataset.name = node.tag.name;
      label.dataset.x = node.x.toFixed(1);
      label.dataset.y = node.y.toFixed(1);
      // Zeigen blendet die Verbindungen dieses Hashtags ein.
      const showLines = () => {
        lines.textContent = '';
        const k = state.zoom?.k || 1;
        node.tag.links.forEach((link) => {
          const other = index.get(link.name);
          if (!other) return;
          el('path', {
            d: curve(node.x, node.y, other.x, other.y, 0.12), fill: 'none', stroke: '#cbd5e1',
            'stroke-width': ((0.8 + link.strength * 2) / Math.sqrt(k)).toFixed(2), 'stroke-opacity': (0.3 + link.strength * 0.6).toFixed(2),
          }, lines);
        });
      };
      group.addEventListener('pointerenter', showLines);
      group.addEventListener('pointerleave', () => { lines.textContent = ''; });
      // Verschieben: Linien und Name ziehen mit, am Ende ordnet sich alles neu.
      draggable(group, node, () => {
        label.dataset.x = node.x.toFixed(1);
        label.dataset.y = node.y.toFixed(1);
        showLines();
        kit.applyZoom();
      }, () => kit.redraw());
    });
    // Bei einem engen Filter bleiben die Verbindungen der Treffer stehen.
    const hits = kit.filterActive() ? nodes.filter((node) => matches(node.tag.name)) : [];
    if (hits.length && hits.length <= 40) {
      const fixed = el('g', {}, svg);
      svg.insertBefore(fixed, layer);
      hits.forEach((node) => node.tag.links.forEach((link) => {
        const other = index.get(link.name);
        if (!other) return;
        el('path', {
          d: curve(node.x, node.y, other.x, other.y, 0.12), fill: 'none', stroke: recencyColor(node.tag.step),
          'stroke-width': (0.8 + link.strength * 2).toFixed(2), 'stroke-opacity': (0.25 + link.strength * 0.5).toFixed(2),
        }, fixed);
      }));
    }
  }

  // ─── Orbit ───────────────────────────────────────────────────────
  function drawOrbit() {
    const { svg, width, height } = stage();
    const data = state.data;
    const center = data.byName.get(state.focus) || data.tags[0];
    const cx = width / 2;
    const cy = height / 2;
    const narrow = width < 520;
    // Ellipse statt Kreis: nutzt die Breite eines Querformats aus.
    const reachX = Math.max(60, width / 2 - (narrow ? 58 : 90));
    const reachY = Math.max(80, height / 2 - 34);
    const neighbours = center.links.slice(0, narrow ? 12 : 18).map((link) => ({ link, tag: data.byName.get(link.name) })).filter((item) => item.tag);
    const rings = [0.38, 0.65, 0.92];
    const maxCount = Math.max(...data.tags.map((tag) => tag.count));
    rings.forEach((factor) => el('ellipse', { cx, cy, rx: reachX * factor, ry: reachY * factor, fill: 'none', stroke: '#1f2937', 'stroke-dasharray': '2 5' }, svg));
    const lines = el('g', {}, svg);
    const hits = el('g', {}, svg);
    const nodes = el('g', {}, svg);
    const groups = new Map();
    // Staerkste Verbindungen auf die innere Bahn.
    const placed = neighbours.map((item, index) => {
      const ringOf = (position) => (narrow ? (position < 4 ? 0 : position < 8 ? 1 : 2) : (position < 5 ? 0 : position < 11 ? 1 : 2));
      const ring = ringOf(index);
      const onRing = neighbours.filter((_, other) => ringOf(other) === ring);
      const slot = onRing.indexOf(item);
      return { ...item, ring, base: (slot / onRing.length) * Math.PI * 2 + ring * 0.6 };
    });
    // Verbindungen der Nachbarn untereinander: neutral grau, damit die Linien
    // zur Mitte fuehren, aber deutlich genug zum Lesen und Anklicken.
    const inner = new Set(placed.map((item) => item.tag.name));
    let crossLinks = [];
    placed.forEach((item) => item.tag.links.forEach((link) => {
      if (inner.has(link.name) && item.tag.name < link.name) crossLinks.push([item.tag.name, link.name, link.strength, link.weight]);
    }));
    // Nur die staerksten, sonst wird der Orbit bei vielen Hashtags ein Knaeuel.
    crossLinks = crossLinks.sort((x, y) => y[2] - x[2]).slice(0, 30);
    const cross = crossLinks.map(([a, b, strength, weight]) => {
      const path = el('path', {
        fill: 'none', stroke: '#94a3b8', 'stroke-width': (1.0 + strength * 1.5).toFixed(2),
        'stroke-opacity': (0.25 + strength * 0.5).toFixed(2),
      }, lines);
      return { a, b, path, hit: edgeHit(hits, path, a, b, weight, groups) };
    });
    const elements = placed.map((item) => {
      const path = el('path', {
        fill: 'none', stroke: recencyColor(item.tag.step), 'stroke-width': (1.0 + item.link.strength * 2.6).toFixed(2),
        'stroke-opacity': (0.25 + item.link.strength * 0.6).toFixed(2), class: 'map-flow',
      }, lines);
      const hit = edgeHit(hits, path, center.name, item.tag.name, item.link.weight, groups);
      const node = nodeGroup(nodes, item.tag, 0, 0, 3 + 0.6 * radiusFor(item.tag.count, maxCount), recencyColor(item.tag.step), true);
      groups.set(item.tag.name, node);
      return { ...item, path, hit, node };
    });
    const middle = nodeGroup(nodes, center, cx, cy, radiusFor(center.count, maxCount) + 6, recencyColor(center.step), true);
    middle.querySelector('text')?.setAttribute('class', 'map-label font-semibold');
    groups.set(center.name, middle);
    const positions = new Map();
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    let last = performance.now();
    const speeds = [0.05, 0.032, 0.02];
    function frame(now) {
      const delta = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (!reduced && !state.paused) state.orbitTime += delta;
      elements.forEach((item) => {
        const angle = item.base + speeds[item.ring] * state.orbitTime + state.spin;
        const x = cx + Math.cos(angle) * reachX * rings[item.ring];
        const y = cy + Math.sin(angle) * reachY * rings[item.ring];
        positions.set(item.tag.name, [x, y]);
        item.node.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
        const d = curve(cx, cy, x, y, 0.12);
        item.path.setAttribute('d', d);
        item.hit.setAttribute('d', d);
      });
      cross.forEach((link) => {
        const [x1, y1] = positions.get(link.a);
        const [x2, y2] = positions.get(link.b);
        const d = curve(x1, y1, x2, y2, 0.25);
        link.path.setAttribute('d', d);
        link.hit.setAttribute('d', d);
      });
      state.frame = state.open && state.view === 'orbit' && !reduced && !state.paused ? requestAnimationFrame(frame) : 0;
    }
    // Die Drehung lebt in state.orbitTime/spin - so uebersteht sie ein Neuzeichnen,
    // und Anhalten oder Drehen mit der Maus setzt genau dort an.
    state.orbitCenter = { cx, cy };
    state.orbitRender = () => frame(performance.now());
    frame(performance.now());
  }

  // ─── Cluster ─────────────────────────────────────────────────────
  // Das Kraefte-Layout ist quadratisch in der Knotenzahl - es nimmt die
  // haeufigsten CLUSTER_LIMIT, alle zeigt die Uebersicht.
  function clusterLayout(width, height) {
    const data = state.data;
    const tags = data.tags.slice(0, CLUSTER_LIMIT);
    const nodes = tags.map((tag, index) => {
      const angle = (index / tags.length) * Math.PI * 2 + tag.clusterIndex;
      return { tag, x: width / 2 + Math.cos(angle) * width * 0.3, y: height / 2 + Math.sin(angle) * height * 0.3, vx: 0, vy: 0 };
    });
    const index = new Map(nodes.map((node) => [node.tag.name, node]));
    const springs = data.edges.map((edge) => [index.get(edge.a), index.get(edge.b), edge.strength]).filter(([a, b]) => a && b);
    const area = Math.sqrt((width * height) / Math.max(1, nodes.length));
    for (let step = 0; step < 260; step += 1) {
      const cool = 1 - step / 260;
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          const distance = Math.max(1, Math.hypot(dx, dy));
          const push = (area * area) / distance / 40;
          dx /= distance; dy /= distance;
          a.vx += dx * push; a.vy += dy * push; b.vx -= dx * push; b.vy -= dy * push;
        }
      }
      springs.forEach(([a, b, strength]) => {
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const pull = 0.02 + strength * 0.08;
        a.vx += dx * pull; a.vy += dy * pull; b.vx -= dx * pull; b.vy -= dy * pull;
      });
      nodes.forEach((node) => {
        node.vx += (width / 2 - node.x) * 0.01;
        node.vy += (height / 2 - node.y) * 0.01;
        node.x += Math.max(-20, Math.min(20, node.vx * 0.1 * cool));
        node.y += Math.max(-20, Math.min(20, node.vy * 0.1 * cool));
        node.x = Math.max(24, Math.min(width - 24, node.x));
        node.y = Math.max(24, Math.min(height - 24, node.y));
        node.vx *= 0.5; node.vy *= 0.5;
      });
    }
    applySaved(nodes, 'cluster', width, height);
    return { nodes, index, width, height };
  }

  // Gespeicherte Anordnung (relativ zur Flaeche) ueber das berechnete Layout legen.
  function applySaved(nodes, view, width, height) {
    const saved = state.layouts?.[view];
    if (!saved) return;
    nodes.forEach((node) => {
      const point = saved[node.tag.name];
      if (point) { node.x = point[0] * width; node.y = point[1] * height; }
    });
  }

  function drawCluster() {
    const { svg, width, height } = stage();
    if (!state.layout || state.layout.width !== width || state.layout.height !== height) state.layout = clusterLayout(width, height);
    const { nodes, index } = state.layout;
    const maxCount = Math.max(...nodes.map((node) => node.tag.count));
    const lines = el('g', {}, svg);
    const hits = el('g', {}, svg);
    const groups = new Map();
    const paths = state.data.edges.map((edge) => {
      const a = index.get(edge.a);
      const b = index.get(edge.b);
      if (!a || !b) return null;
      const same = a.tag.clusterIndex === b.tag.clusterIndex;
      const path = el('path', {
        d: curve(a.x, a.y, b.x, b.y, 0.18), fill: 'none',
        stroke: same ? clusterColor(a.tag) : '#64748b',
        'stroke-width': (0.8 + edge.strength * 2.0).toFixed(2), 'stroke-opacity': (0.16 + edge.strength * 0.5).toFixed(2),
        class: edge.strength > 0.35 ? 'map-flow' : '',
      }, lines);
      const hit = edgeHit(hits, path, edge.a, edge.b, edge.weight, groups);
      return { path, hit, a: edge.a, b: edge.b, base: path.getAttribute('stroke-opacity') };
    }).filter(Boolean);
    const labelled = new Set(nodes.slice().sort((x, y) => y.tag.count - x.tag.count).slice(0, 30).map((node) => node.tag.name));
    nodes.forEach((node) => {
      const group = nodeGroup(svg, node.tag, node.x, node.y, radiusFor(node.tag.count, maxCount),
        clusterColor(node.tag), labelled.has(node.tag.name), recencyColor(node.tag.step));
      groups.set(node.tag.name, group);
      // Hover hebt die eigenen Verbindungen hervor.
      group.addEventListener('pointerenter', () => paths.forEach((item) => {
        const own = item.a === node.tag.name || item.b === node.tag.name;
        item.path.setAttribute('stroke-opacity', own ? '0.9' : '0.04');
      }));
      group.addEventListener('pointerleave', () => paths.forEach((item) => item.path.setAttribute('stroke-opacity', item.base)));
      // Verschieben: die eigenen Verbindungen ziehen mit, die Position bleibt im Layout.
      const own = paths.filter((item) => item.a === node.tag.name || item.b === node.tag.name);
      draggable(group, node, () => own.forEach((item) => {
        const a = index.get(item.a);
        const b = index.get(item.b);
        const d = curve(a.x, a.y, b.x, b.y, 0.18);
        item.path.setAttribute('d', d);
        item.hit.setAttribute('d', d);
      }));
    });
  }

  // ─── Hirn ────────────────────────────────────────────────────────
  // Ein Kreis aus Themenfeldern (Sektoren, Reihenfolge und Gruppen vom Server).
  // Der Abstand zur Mitte ist die Abstraktion: innen die breiten Begriffe
  // (Kern), aussen die konkreten (Rinde). Ringe fest (1-4) oder stufenlos.
  // Linien im Feld in dessen Farbe, zwischen Feldern nur die 20 staerksten -
  // gestrichelt und anklickbar, das sind die neuen Zusammenhaenge.
  const INNER = 0.16;
  const BRIDGES = 20;

  function wedge(cx, cy, rx, ry, from, to, inner) {
    const steps = Math.max(2, Math.ceil((to - from) / 0.12));
    const point = (angle, factor) => `${(cx + Math.cos(angle) * rx * factor).toFixed(1)},${(cy + Math.sin(angle) * ry * factor).toFixed(1)}`;
    const outer = [];
    const back = [];
    for (let step = 0; step <= steps; step += 1) {
      const angle = from + ((to - from) * step) / steps;
      outer.push(point(angle, 1));
      back.unshift(point(angle, inner));
    }
    return `M${outer.join(' L')} L${back.join(' L')} Z`;
  }

  // Reindrillen (state.drill, Brotkrumen in brain-tag-map-actions.js): das
  // Teil-Hirn zeigt nur den fokussierten Hashtag in der Mitte und seine
  // direkten Nachbarn, in deren Feldern und Ringen.
  function drawBrain() {
    const { svg, width, height } = stage();
    const data = state.data;
    const drilled = state.drill[state.drill.length - 1];
    const focus = drilled ? data.byName.get(drilled) : null;
    if (drilled && !focus) state.drill = [];
    const pool = focus
      ? focus.links.map((link) => data.byName.get(link.name)).filter(Boolean)
      : data.tags;
    const categories = (data.categories || []).map((category, index) => ({
      ...category, index, size: pool.filter((tag) => tag.category === index).length,
    })).filter((category) => category.size);
    const cx = width / 2;
    const cy = height / 2;
    const rx = Math.max(80, width / 2 - (width < 520 ? 50 : 110));
    const ry = Math.max(80, height / 2 - 30);
    const rings = state.rings === 'auto' ? 0 : Number(state.rings);
    const radial = (score) => {
      if (!rings) return INNER + (1 - score) * (1 - INNER) * 0.94 + 0.03;
      const ring = Math.min(rings - 1, Math.floor((1 - score) * rings));
      return INNER + ((ring + 0.5) / rings) * (1 - INNER);
    };
    // Sektorbreite nach Mitgliederzahl, gedaempft - kleine Felder bleiben lesbar.
    const weights = categories.map((category) => category.size ** 0.75);
    const total = weights.reduce((sum, value) => sum + value, 0) || 1;
    const gap = categories.length > 1 ? 0.035 : 0;
    const usable = Math.PI * 2 - gap * categories.length;
    let cursor = -Math.PI / 2;
    const sectors = categories.map((category, position) => {
      const span = (weights[position] / total) * usable;
      const sector = { ...category, from: cursor, to: cursor + span };
      cursor += span + gap;
      return sector;
    });
    const colorOf = (index) => clusterColor({ clusterIndex: index });
    const selected = data.byName.get(state.selected);
    const back = el('g', {}, svg);
    if (focus && !pool.length) {
      const text = el('text', { x: cx, y: cy + 34, 'text-anchor': 'middle', class: 'map-label' }, back);
      text.textContent = 'Keine Verbindungen im Zeitraum.';
    }
    sectors.forEach((sector) => {
      el('path', { d: wedge(cx, cy, rx, ry, sector.from, sector.to, INNER), fill: colorOf(sector.index),
        'fill-opacity': selected && selected.category !== sector.index ? 0.02 : 0.06 }, back);
      const middle = (sector.from + sector.to) / 2;
      const cos = Math.cos(middle);
      const text = sector.other ? 'Sonstige' : `#${sector.label}`;
      // Am Rand nach innen schieben, damit kein Feldname abgeschnitten wird.
      const anchor = cos > 0.25 ? 'start' : cos < -0.25 ? 'end' : 'middle';
      const wide = text.length * 7.2;
      const lowest = { start: 4, end: wide + 4, middle: wide / 2 + 4 }[anchor];
      const highest = width - { start: wide + 4, end: 4, middle: wide / 2 + 4 }[anchor];
      const label = el('text', {
        x: Math.max(lowest, Math.min(highest, cx + cos * (rx + 10))).toFixed(1),
        y: (cy + Math.sin(middle) * (ry + 12) + 4).toFixed(1), 'text-anchor': anchor,
        class: 'map-label', style: `fill:${colorOf(sector.index)};font-weight:600`,
      }, back);
      label.textContent = text;
    });
    if (rings > 1) {
      for (let ring = 1; ring < rings; ring += 1) {
        const factor = INNER + (ring / rings) * (1 - INNER);
        el('ellipse', { cx, cy, rx: rx * factor, ry: ry * factor, fill: 'none', stroke: '#1f2937', 'stroke-dasharray': '2 5' }, back);
      }
    }
    el('ellipse', { cx, cy, rx: rx * INNER, ry: ry * INNER, fill: 'none', stroke: '#1f2937' }, back);

    // Positionen: je Feld (und Ring) gleichmaessig ueber den Bogen verteilt.
    const placed = new Map();
    const maxCount = Math.max(1, ...pool.map((tag) => tag.count));
    sectors.forEach((sector) => {
      const members = pool.filter((tag) => tag.category === sector.index);
      const bands = new Map();
      members.forEach((tag) => {
        const key = rings ? Math.min(rings - 1, Math.floor((1 - tag.abstraction) * rings)) : 0;
        if (!bands.has(key)) bands.set(key, []);
        bands.get(key).push(tag);
      });
      const pad = Math.min(0.02, (sector.to - sector.from) / 6);
      bands.forEach((band) => {
        band.sort((a, b) => (rings ? b.count - a.count : 0) || a.name.localeCompare(b.name));
        band.forEach((tag, slot) => {
          const angle = sector.from + pad + ((slot + 0.5) / band.length) * (sector.to - sector.from - 2 * pad);
          const factor = radial(tag.abstraction);
          placed.set(tag.name, { tag, x: cx + Math.cos(angle) * rx * factor, y: cy + Math.sin(angle) * ry * factor,
            r: 2.5 + 8 * Math.sqrt(tag.count / Math.max(1, maxCount)) });
        });
      });
    });

    const lines = el('g', {}, svg);
    const hits = el('g', {}, svg);
    const groups = new Map();
    const inside = [];
    const across = [];
    if (focus) {
      // Die Mitte: der fokussierte Hashtag, seine Verbindungen als Speichen.
      placed.set(focus.name, { tag: focus, x: cx, y: cy, r: 12, center: true });
      focus.links.forEach((link) => {
        const other = placed.get(link.name);
        if (!other) return;
        const path = el('path', {
          d: curve(cx, cy, other.x, other.y, 0.06), fill: 'none', stroke: colorOf(other.tag.category),
          'stroke-width': (0.8 + link.strength * 2.2).toFixed(2), 'stroke-opacity': (0.25 + link.strength * 0.55).toFixed(2),
        }, lines);
        edgeHit(hits, path, focus.name, link.name, link.weight, groups);
      });
    }
    data.edges.forEach((edge) => {
      const a = placed.get(edge.a);
      const b = placed.get(edge.b);
      if (!a || !b || a.center || b.center) return;
      (a.tag.category === b.tag.category ? inside : across).push({ edge, a, b });
    });
    inside.slice(0, 400).forEach(({ edge, a, b }) => {
      const path = el('path', {
        d: curve(a.x, a.y, b.x, b.y, 0.15), fill: 'none', stroke: colorOf(a.tag.category),
        'stroke-width': (0.6 + edge.strength * 1.8).toFixed(2), 'stroke-opacity': (0.12 + edge.strength * 0.45).toFixed(2),
      }, lines);
      edgeHit(hits, path, edge.a, edge.b, edge.weight, groups);
    });
    // Bruecken: Sammel-Hashtags (#info) verbinden ohnehin alles - sie zaehlen
    // nicht, und je Hashtag hoechstens drei, damit die 20 sich verteilen.
    const hubs = new Set(data.hubs || []);
    const used = new Map();
    const bridges = across
      .filter(({ edge }) => edge.weight >= 2 && !hubs.has(edge.a) && !hubs.has(edge.b))
      .sort((x, y) => y.edge.strength - x.edge.strength)
      .filter(({ edge }) => {
        if ((used.get(edge.a) || 0) >= 3 || (used.get(edge.b) || 0) >= 3) return false;
        used.set(edge.a, (used.get(edge.a) || 0) + 1);
        used.set(edge.b, (used.get(edge.b) || 0) + 1);
        return true;
      })
      .slice(0, BRIDGES);
    bridges.forEach(({ edge, a, b }) => {
      const path = el('path', {
        d: curve(a.x, a.y, b.x, b.y, 0.08), fill: 'none', stroke: '#e2e8f0', 'stroke-dasharray': '4 3',
        'stroke-width': (1.2 + edge.strength * 2).toFixed(2), 'stroke-opacity': (0.45 + edge.strength * 0.4).toFixed(2),
      }, lines);
      edgeHit(hits, path, edge.a, edge.b, edge.weight, groups);
    });
    const layer = el('g', {}, svg);
    const names = el('g', { 'pointer-events': 'none' }, svg);
    // Namen: Mitte, dann die abstraktesten zuerst, damit der Kern beschriftet ist.
    [...placed.values()].sort((x, y) => (y.center ? 1 : 0) - (x.center ? 1 : 0)
      || y.tag.abstraction - x.tag.abstraction || y.tag.count - x.tag.count).forEach((node) => {
      const group = nodeGroup(layer, node.tag, node.x, node.y, node.r, colorOf(node.tag.category), false, recencyColor(node.tag.step));
      groups.set(node.tag.name, group);
      if (selected && selected !== focus && !node.center && node.tag.category !== selected.category) group.style.opacity = '0.35';
      const label = el('text', { class: 'map-label' }, names);
      label.textContent = `#${node.tag.name}`;
      Object.assign(label.dataset, { labelR: Math.max(node.r, 6).toFixed(1), name: node.tag.name, x: node.x.toFixed(1), y: node.y.toFixed(1) });
    });
  }

  // ─── Zeit ────────────────────────────────────────────────────────
  function drawTime() {
    const { svg, width, height } = stage();
    const data = state.data;
    const daily = data.granularity === 'day';
    const columns = daily ? data.days : data.months;
    const valuesOf = (tag) => (daily ? tag.days : tag.months) || {};
    if (!columns?.length) {
      const text = el('text', { x: width / 2, y: height / 2, 'text-anchor': 'middle', class: 'map-label' }, svg);
      text.textContent = 'Keine datierten Einträge im Zeitraum.';
      return;
    }
    // Ein Monat ist so alt wie sein letzter Tag im Zeitraum.
    const columnStep = (column) => {
      if (daily) return data.scale.step(column);
      const [year, month] = column.split('-').map(Number);
      const lastDay = new Date(Date.UTC(year, month, 0));
      const capped = Math.min(Math.floor(lastDay.getTime() / DAY), data.scale.end);
      return data.scale.step(new Date(capped * DAY).toISOString().slice(0, 10));
    };
    // Die Mitte und ihre engsten Verwandten zuerst, dann die haeufigsten.
    const center = data.byName.get(state.focus);
    const chosen = [];
    const hits = kit.filterActive() ? data.tags.filter((tag) => matches(tag.name)) : [];
    [...hits, center, ...(center?.links || []).map((link) => data.byName.get(link.name)), ...data.tags].forEach((tag) => {
      if (tag && !chosen.includes(tag) && chosen.length < 18) chosen.push(tag);
    });
    const left = Math.min(150, width * 0.32);
    const top = 28;
    const rowHeight = Math.max(16, Math.min(30, (height - top - 30) / chosen.length));
    const step = (width - left - 24) / Math.max(1, columns.length - 1 || 1);
    const maxValue = Math.max(1, ...chosen.flatMap((tag) => Object.values(valuesOf(tag))));
    const labelEvery = Math.max(1, Math.ceil(columns.length / Math.max(2, (width - left) / 56)));
    const columnLabel = (column) => (daily ? `${column.slice(8, 10)}.${column.slice(5, 7)}.` : `${column.slice(5, 7)}/${column.slice(2, 4)}`);
    columns.forEach((column, index) => {
      const x = left + index * step;
      el('line', { x1: x, x2: x, y1: top - 6, y2: top + chosen.length * rowHeight, stroke: '#111827' }, svg);
      if (index % labelEvery === 0) {
        const text = el('text', { x, y: top - 12, 'text-anchor': 'middle', class: 'map-label', 'font-size': 10 }, svg);
        text.textContent = columnLabel(column);
      }
    });
    chosen.forEach((tag, row) => {
      const y = top + row * rowHeight + rowHeight / 2;
      const label = el('text', { x: left - 10, y: y + 4, 'text-anchor': 'end', class: 'map-label map-node' }, svg);
      label.textContent = `#${tag.name}`;
      if (tag === center) label.setAttribute('style', 'fill:#fff;font-weight:600');
      bindNode(label, tag);
      const values = valuesOf(tag);
      const points = columns.map((column, index) => [left + index * step, values[column] || 0, column]).filter(([, value]) => value);
      if (points.length > 1) {
        el('path', {
          d: points.map(([x], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' '),
          fill: 'none', stroke: clusterColor(tag), 'stroke-opacity': 0.4,
          'stroke-width': 1.4, class: 'map-flow',
        }, svg);
      }
      points.forEach(([x, value, column]) => {
        const dot = el('circle', {
          cx: x, cy: y, r: (2 + 7 * Math.sqrt(value / maxValue)).toFixed(1),
          fill: recencyColor(columnStep(column)), 'fill-opacity': (0.55 + 0.45 * value / maxValue).toFixed(2),
          stroke: '#030712', 'stroke-width': 1,
        }, svg);
        dot.addEventListener('pointerenter', (event) => placeTip(event, `#${tag.name} · ${column} · ${value} Einträge`));
        dot.addEventListener('pointerleave', () => placeTip(null, ''));
      });
    });
  }

  Object.assign(kit.views, {
    overview: drawOverview, brain: drawBrain, orbit: drawOrbit, cluster: drawCluster, time: drawTime,
  });
})();
