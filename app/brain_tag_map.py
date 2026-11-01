"""Hashtag-Map: wie die Hashtags der sichtbaren Inhalte zusammenhaengen.

Reine Berechnung ohne Datei- oder HTTP-Zugriff. ``brain_routes`` reicht die
sichtbaren Dokumente und eine Funktion herein, die je Block die filterbaren
Hashtags liefert (dieselbe Aufloesung wie die Tag-Filter: Aliase zusammen-
gefuehrt, Vorschlaege dabei). Daraus entstehen:

* **tags** – je Hashtag Anzahl der Bloecke, erstes und letztes Datum und der
  Verlauf je Monat (``YYYY-MM``), fuer die Zeit-Ansicht; bei kurzen Zeitraeumen
  (``daily``) zusaetzlich je Tag, sonst stuende dort nur eine einzige Spalte;
* **edges** – je Hashtag-Paar die Zahl gemeinsamer Bloecke (``weight``) und
  eine Staerke 0..1 (``weight / sqrt(count_a * count_b)``), damit zwei kleine,
  immer gemeinsam auftretende Hashtags nicht neben zwei grossen untergehen;
* **categories** und je Hashtag ``category``/``abstraction`` – fuer den
  Hirn-Modus: Themenfelder per Label-Propagation (Sammel-Hashtags wie
  ``#info``, die mit fast allem auftreten, zaehlen dabei nicht als Bruecke)
  und ein Abstraktionswert 0..1 (innen = abstrakt, aussen = konkret);
* **suggestions** (nur mit ``approved``) – noch nicht freigegebene Hashtags,
  die eine Freigabe nahelegen: oft neben freigegebenen, wachsend im Zeitraum
  oder ohne jede Gruppe. Rein aus den Zahlen, ohne KI.

Ein Block mit sehr vielen Hashtags wuerde quadratisch viele Paare erzeugen;
fuer die Paare zaehlen deshalb hoechstens ``MAX_TAGS_PER_BLOCK`` davon.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations
from math import log1p, sqrt

MAX_TAGS_PER_BLOCK = 15
DEFAULT_MAX_TAGS = 80
DEFAULT_MAX_EDGES = 400
# Die Uebersicht zeigt bis zu so viele Hashtags auf einmal; damit auch die
# kleinen noch Nachbarn haben, waechst die Kantenzahl mit (edges_for).
MAX_TAGS_LIMIT = 500
MAX_EDGES_LIMIT = 3000
# Vorschlaege: ab dieser Staerke gilt ein Paar als "oft gemeinsam", darunter
# hat ein Hashtag keine Gruppe; "wachsend" heisst in der zweiten Haelfte des
# Zeitraums mindestens doppelt so oft wie in der ersten.
PARTNER_MIN_STRENGTH = 0.15
PARTNER_MIN_WEIGHT = 2
GROWTH_MIN_RECENT = 2
ISOLATED_MIN_COUNT = 3
# Hirn-Modus. Sammel-Hashtag: verbunden mit mindestens diesem Anteil aller
# anderen (und mindestens HUB_MIN_DEGREE). Themenfelder: hoechstens
# MAX_CATEGORIES, kleinere als CATEGORY_MIN_SIZE landen in "Sonstige".
HUB_SHARE = 0.5
HUB_MIN_DEGREE = 3
CLUSTER_MIN_STRENGTH = 0.05
CATEGORY_MIN_SIZE = 2
MAX_CATEGORIES = 10
# Abstraktion: gewichtete Summe, danach auf 0..1 gestreckt.
ABSTRACTION_WEIGHTS = {"frequency": 0.4, "degree": 0.35, "parent": 0.15, "child": 0.2, "name": 0.2}
# Bis zu dieser Spanne zeigt die Zeit-Ansicht Tage statt Monate.
DAILY_MAX_DAYS = 62


def _day(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def edges_for(max_tags):
    """Wie viele Kanten zu ``max_tags`` Hashtags passen: rund sechs je Hashtag."""
    return max(DEFAULT_MAX_EDGES, min(MAX_EDGES_LIMIT, int(max_tags) * 6))


def is_daily(start, end, today=None):
    """Ob ein Zeitraum kurz genug fuer Tagesspalten ist (offenes Ende = heute)."""
    if start is None:
        return False
    return ((end or today or date.today()) - start).days < DAILY_MAX_DAYS


def build_tag_map(documents, tags_of, *, start=None, end=None,
                  max_tags=DEFAULT_MAX_TAGS, max_edges=DEFAULT_MAX_EDGES, daily=False,
                  match="", include=(), approved=None, skip=()):
    """Hashtags, ihre Verbindungen und ihr Verlauf ueber die Zeit.

    ``tags_of(document, block)`` liefert die filterbaren Hashtags eines Blocks.
    Mit ``start``/``end`` zaehlen nur datierte Inhalte im Zeitraum. ``daily``
    (nur mit ``start``) liefert zusaetzlich den Verlauf je Tag und in ``days``
    jeden Tag des Zeitraums, auch die leeren.

    ``match`` holt alle Hashtags, deren Name den Text enthaelt, zusaetzlich zu
    den haeufigsten herein - samt ihrer Kanten -, damit der freie Filter der
    Map nicht an der Kappung endet; ``include`` ebenso fuer genau benannte
    (die Filter-Chips). ``approved`` (Menge der freigegebenen)
    markiert jeden Hashtag und schaltet die Vorschlaege ein; ``skip`` sind
    Hashtags, die nie vorgeschlagen werden (ausgeblendet, KI-Workflows).
    """
    daily = bool(daily and start)
    match = (match or "").strip().lstrip("#").lower()
    counts = Counter()
    months = defaultdict(Counter)
    days = defaultdict(Counter)
    first = {}
    last = {}
    pairs = Counter()
    blocks_seen = 0
    for document in documents:
        day = _day(document.get("date"))
        if (start or end) and (day is None or (start and day < start) or (end and day > end)):
            continue
        for block in document.get("blocks", []):
            tags = sorted(tags_of(document, block))
            if not tags:
                continue
            blocks_seen += 1
            for tag in tags:
                counts[tag] += 1
                if day:
                    months[tag][day.strftime("%Y-%m")] += 1
                    days[tag][day.isoformat()] += 1
                    first[tag] = min(first.get(tag, day), day)
                    last[tag] = max(last.get(tag, day), day)
            for a, b in combinations(tags[:MAX_TAGS_PER_BLOCK], 2):
                pairs[(a, b)] += 1

    top = [tag for tag, _ in counts.most_common(max(1, int(max_tags)))]
    chosen = set(top)
    matched = {tag for tag in include if tag in counts}
    if match:
        matched |= {tag for tag in counts if match in tag}
    if matched:
        top += sorted(matched - chosen, key=lambda tag: (-counts[tag], tag))
        chosen |= matched
    edges = []
    for (a, b), weight in pairs.items():
        if a in chosen and b in chosen:
            edges.append({
                "a": a, "b": b, "weight": weight,
                "strength": round(weight / sqrt(counts[a] * counts[b]), 4),
            })
    # Kanten der Filtertreffer zuerst, sonst schnitte die Kappung sie ab.
    edges.sort(key=lambda edge: (not (edge["a"] in matched or edge["b"] in matched),
                                 -edge["weight"], -edge["strength"], edge["a"], edge["b"]))
    edges = edges[:max(0, int(max_edges))]

    all_months = sorted({month for tag in top for month in months[tag]})
    categories, category_of, abstraction, hubs = categorise(top, counts, pairs)
    result = {
        "tags": [{
            "name": tag,
            "count": counts[tag],
            "first": first[tag].isoformat() if tag in first else "",
            "last": last[tag].isoformat() if tag in last else "",
            "months": dict(sorted(months[tag].items())),
            **({"days": dict(sorted(days[tag].items()))} if daily else {}),
            **({"approved": tag in approved} if approved is not None else {}),
            "category": category_of[tag],
            "abstraction": abstraction[tag],
        } for tag in top],
        "categories": categories,
        "hubs": sorted(hubs),
        "edges": edges,
        "months": all_months,
        "granularity": "day" if daily else "month",
        "blocks": blocks_seen,
        "total_tags": len(counts),
    }
    if daily:
        last_day = end or date.today()
        result["days"] = [(start + timedelta(days=offset)).isoformat()
                          for offset in range((last_day - start).days + 1)]
    if approved is not None:
        # Alle Hashtags des Zeitraums, schlank - fuer die Hashtag-Liste der Map,
        # auch die jenseits der Kappung.
        result["names"] = [{"name": tag, "count": count, "approved": tag in approved}
                           for tag, count in counts.most_common()]
        dated = [day for tag_days in days.values() for day in tag_days]
        middle = _middle(start or (date.fromisoformat(min(dated)) if dated else None),
                         end or (date.fromisoformat(max(dated)) if dated else None))
        result["suggestions"] = suggest(counts, pairs, days, set(approved), set(skip), middle)
    return result


def _strength(weight, count_a, count_b):
    return weight / sqrt(count_a * count_b)


def categorise(tags, counts, pairs):
    """Themenfelder und Abstraktion der gezeigten Hashtags (Hirn-Modus).

    Liefert ``(categories, category_of, abstraction, hubs)``. ``categories`` ist in
    Kreis-Reihenfolge: verwandte Felder stehen nebeneinander, "Sonstige"
    zuletzt. Alles deterministisch - gleiche Daten, gleiches Bild.
    """
    tags = list(tags)
    chosen = set(tags)
    links = defaultdict(dict)
    for (a, b), weight in pairs.items():
        if a in chosen and b in chosen:
            strength = _strength(weight, counts[a], counts[b])
            links[a][b] = strength
            links[b][a] = strength
    degree = {tag: len(links[tag]) for tag in tags}
    hubs = {tag for tag in tags
            if degree[tag] >= max(HUB_MIN_DEGREE, HUB_SHARE * (len(tags) - 1))}

    # Label-Propagation ohne Sammel-Hashtags; feste Reihenfolge, bei
    # Gleichstand gewinnt das kleinere Etikett.
    order = sorted((tag for tag in tags if tag not in hubs), key=lambda tag: (-counts[tag], tag))
    label = {tag: index for index, tag in enumerate(order)}
    for _ in range(20):
        changed = False
        for tag in order:
            votes = defaultdict(float)
            for other, strength in links[tag].items():
                if other in label and strength >= CLUSTER_MIN_STRENGTH:
                    votes[label[other]] += strength
            if not votes:
                continue
            best = min(votes, key=lambda key: (-votes[key], key))
            if best != label[tag]:
                label[tag] = best
                changed = True
        if not changed:
            break
    groups = defaultdict(list)
    for tag in order:
        groups[label[tag]].append(tag)
    ranked = sorted((members for members in groups.values() if len(members) >= CATEGORY_MIN_SIZE),
                    key=lambda members: (-sum(counts[tag] for tag in members), members[0]))[:MAX_CATEGORIES]
    member_of = {tag: index for index, members in enumerate(ranked) for tag in members}
    # Sammel-Hashtags gehoeren zum Feld, mit dem sie am staerksten verbunden sind.
    for hub in sorted(hubs, key=lambda tag: (-counts[tag], tag)):
        affinity = defaultdict(float)
        for other, strength in links[hub].items():
            if other in member_of:
                affinity[member_of[other]] += strength
        if affinity:
            member_of[hub] = min(affinity, key=lambda key: (-affinity[key], key))

    # Reihenfolge im Kreis: vom groessten Feld aus immer das am staerksten
    # mit dem letzten verbundene anschliessen.
    between = defaultdict(float)
    for tag in tags:
        for other, strength in links[tag].items():
            if tag in member_of and other in member_of and member_of[tag] != member_of[other]:
                between[(member_of[tag], member_of[other])] += strength
    sequence = [0] if ranked else []
    remaining = set(range(1, len(ranked)))
    while remaining:
        last = sequence[-1]
        following = min(remaining, key=lambda index: (-between[(last, index)], index))
        sequence.append(following)
        remaining.discard(following)
    position = {old: new for new, old in enumerate(sequence)}
    category_of = {}
    categories = []
    for new, old in enumerate(sequence):
        members = [tag for tag in tags if member_of.get(tag) == old]
        named = [tag for tag in members if tag not in hubs] or members
        categories.append({"label": min(named, key=lambda tag: (-counts[tag], tag)), "size": len(members),
                           "count": sum(counts[tag] for tag in members)})
    other = [tag for tag in tags if tag not in member_of]
    if other:
        categories.append({"label": "Sonstige", "size": len(other), "count": sum(counts[tag] for tag in other),
                           "other": True})
    for tag in tags:
        category_of[tag] = position[member_of[tag]] if tag in member_of else len(categories) - 1
    return categories, category_of, _abstraction(tags, counts, degree), hubs


def _abstraction(tags, counts, degree):
    """Abstraktionswert 0..1: haeufig, breit verbunden und Oberbegriff anderer
    (``docker`` zu ``docker-compose``) heisst abstrakt; lange, zusammengesetzte
    Namen und Unterbegriffe heissen konkret."""
    if not tags:
        return {}
    weights = ABSTRACTION_WEIGHTS
    top_count = max(counts[tag] for tag in tags)
    top_degree = max(1, max(degree.values()))

    def child_of(tag, parent):
        if tag == parent or not tag.startswith(parent):
            return False
        return tag[len(parent)] in "-_" or (len(parent) >= 4 and len(tag) > len(parent) + 2)

    raw = {}
    for tag in tags:
        is_child = any(child_of(tag, other) for other in tags)
        is_parent = any(child_of(other, tag) for other in tags)
        parts = tag.count("-") + tag.count("_")
        name = min(1.0, parts * 0.5 + max(0, len(tag) - 8) / 16)
        raw[tag] = (weights["frequency"] * log1p(counts[tag]) / log1p(top_count)
                    + weights["degree"] * degree[tag] / top_degree
                    + weights["parent"] * is_parent
                    - weights["child"] * is_child
                    - weights["name"] * name)
    low, high = min(raw.values()), max(raw.values())
    span = high - low or 1
    return {tag: round((value - low) / span, 3) for tag, value in raw.items()}


def _middle(start, end):
    if not start or not end:
        return None
    return start + (end - start) / 2


def suggest(counts, pairs, days, approved, skip, middle):
    """Noch nicht freigegebene Hashtags mit einem Grund fuer die Freigabe.

    Gruende: ``partner`` (tritt oft neben einem freigegebenen auf - der
    staerkste wird genannt), ``growing`` (zweite Zeitraumhaelfte mindestens
    doppelt so oft wie die erste) und ``isolated`` (oft genutzt, aber mit
    keinem anderen Hashtag eng verbunden - Kandidat fuer eine eigene Kategorie).
    Sortiert nach einem Punktwert aus Haeufigkeit und Gruenden.
    """
    best_any = defaultdict(float)
    partner = {}
    for (a, b), weight in pairs.items():
        strength = weight / sqrt(counts[a] * counts[b])
        for tag, other in ((a, b), (b, a)):
            best_any[tag] = max(best_any[tag], strength)
            if other in approved and weight >= PARTNER_MIN_WEIGHT and strength >= PARTNER_MIN_STRENGTH:
                if strength > partner.get(tag, ("", 0, 0))[1]:
                    partner[tag] = (other, strength, weight)
    found = []
    for tag, count in counts.items():
        if tag in approved or tag in skip:
            continue
        reasons = []
        score = 0.0
        if tag in partner:
            other, strength, weight = partner[tag]
            reasons.append({"kind": "partner", "tag": other, "weight": weight})
            score += 2 * strength
        if middle:
            recent = sum(value for day, value in days[tag].items() if date.fromisoformat(day) > middle)
            older = count - recent
            if recent >= GROWTH_MIN_RECENT and recent >= 2 * older:
                reasons.append({"kind": "growing", "recent": recent, "older": older})
                score += 1
        if count >= ISOLATED_MIN_COUNT and best_any[tag] < PARTNER_MIN_STRENGTH:
            reasons.append({"kind": "isolated"})
            score += 0.5
        if reasons:
            found.append({"name": tag, "count": count, "reasons": reasons,
                          "score": round(score + count ** 0.5 / 10, 4)})
    found.sort(key=lambda item: (-item["score"], -item["count"], item["name"]))
    return found


__all__ = ["build_tag_map", "suggest", "categorise", "edges_for", "is_daily", "MAX_TAGS_PER_BLOCK", "MAX_TAGS_LIMIT", "DAILY_MAX_DAYS"]
