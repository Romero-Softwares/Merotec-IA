"""Merotec native single-layer CAD model and geometric checks, without external CAD."""
from __future__ import annotations

import copy
import csv
import html
import io
import json
import math
from pathlib import Path


# Pad coordinates and sizes in mm. Library entries are starting points, not part approvals.
LIBRARY = {
    "R_0603": {"prefix": "R", "pads": {"1": [-0.8, 0], "2": [0.8, 0]}, "size": [0.9, 0.95]},
    "C_0603": {"prefix": "C", "pads": {"1": [-0.8, 0], "2": [0.8, 0]}, "size": [0.9, 0.95]},
    "LED_0805": {"prefix": "D", "pads": {"A": [-1.1, 0], "K": [1.1, 0]}, "size": [1.2, 1.3]},
    "CONN_SMD_2": {"prefix": "J", "pads": {"1": [0, -1.27], "2": [0, 1.27]}, "size": [1.5, 1.5]},
    "SOIC_8": {"prefix": "U", "pads": {str(i + 1): [-2.7, -1.905 + i * 1.27] for i in range(4)} |
               {str(i + 5): [2.7, 1.905 - i * 1.27] for i in range(4)}, "size": [1.5, 0.6]},
}


def finite(value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Coordenada/medida inválida.")
    value = float(value)
    if not math.isfinite(value) or abs(value) > 100000 or (minimum is not None and value < minimum):
        raise ValueError("Medida inválida ou fora do limite.")
    return value


def new_design():
    return {"format": "merotec-pcb", "version": 1, "board": {"width": 80.0, "height": 60.0, "clearance": 0.2, "min_track": 0.25},
            "components": [], "tracks": []}


def demo_design():
    """Geometry/netlist demonstration, not a complete powered device."""
    design = new_design()
    add_component(design, "R_0603", "1k", 20, 20)
    add_component(design, "R_0603", "2k", 40, 20)
    for part in design["components"]:
        part["nets"] = {"1": "BUS_A", "2": "BUS_B"}
    design["tracks"] = [
        {"from": "R1.1", "to": "R2.1", "width": .3, "points": [[19.2, 15], [39.2, 15]]},
        {"from": "R1.2", "to": "R2.2", "width": .3, "points": [[20.8, 25], [40.8, 25]]}]
    return design


def component(design, ref):
    return next(c for c in design["components"] if c["ref"] == ref)


def pad(design, endpoint):
    ref, pin = endpoint.split(".")
    part = component(design, ref)
    definition = LIBRARY[part["kind"]]
    x, y = definition["pads"][pin]
    angle = math.radians(part["rotation"])
    return (part["x"] + x * math.cos(angle) - y * math.sin(angle),
            part["y"] + x * math.sin(angle) + y * math.cos(angle))


def net(design, endpoint):
    ref, pin = endpoint.split(".")
    return component(design, ref)["nets"][pin]


def add_component(design, kind, value="", x=10, y=10):
    definition = LIBRARY[kind]
    number = 1
    while any(c["ref"] == f"{definition['prefix']}{number}" for c in design["components"]):
        number += 1
    ref = f"{definition['prefix']}{number}"
    index = len(design["components"])
    part = {"ref": ref, "kind": kind, "value": value, "x": x, "y": y, "rotation": 0,
            "sx": 15 + (index % 4) * 28, "sy": 15 + (index // 4) * 24,
            "nets": {p: "" for p in definition["pads"]}, "roles": {p: "passive" for p in definition["pads"]}}
    design["components"].append(part)
    return ref


def remove_component(design, ref):
    design["components"] = [c for c in design["components"] if c["ref"] != ref]
    design["tracks"] = [t for t in design["tracks"] if not any(e.startswith(ref + ".") for e in (t["from"], t["to"]))]


def validate(design):
    """Reject malformed documents before replacing the open design."""
    try:
        if not isinstance(design, dict) or design["format"] != "merotec-pcb" or design["version"] != 1:
            raise ValueError("Formato nativo ou versão não suportados.")
        for key in ("width", "height", "min_track"):
            finite(design["board"][key], 0.01)
        finite(design["board"]["clearance"], 0)
        if not isinstance(design["components"], list) or not isinstance(design["tracks"], list):
            raise ValueError("Componentes e trilhas devem ser listas.")
        if len(design["components"]) > 500 or len(design["tracks"]) > 2000:
            raise ValueError("Limite nativo: 500 componentes e 2000 trilhas.")
        refs, endpoints = set(), set()
        for c in design["components"]:
            ref = c["ref"]
            if not isinstance(ref, str) or not ref or not ref.isascii() or not ref.replace("_", "").isalnum() or ref in refs:
                raise ValueError("Referência inválida ou duplicada.")
            refs.add(ref)
            definition = LIBRARY[c["kind"]]
            if not isinstance(c["value"], str):
                raise ValueError("Valor deve ser texto.")
            for key in ("x", "y", "sx", "sy", "rotation"):
                finite(c[key])
            if set(c["nets"]) != set(definition["pads"]) or set(c["roles"]) != set(definition["pads"]):
                raise ValueError("Pinagem não corresponde ao footprint.")
            for pin, value in c["nets"].items():
                if not isinstance(value, str) or len(value) > 100:
                    raise ValueError("Nome de rede inválido.")
                if c["roles"][pin] not in {"passive", "input", "output", "power_in", "power_out", "nc"}:
                    raise ValueError("Tipo elétrico inválido.")
                endpoints.add(f"{ref}.{pin}")
        for t in design["tracks"]:
            if t["from"] not in endpoints or t["to"] not in endpoints or t["from"] == t["to"]:
                raise ValueError("Trilha com terminal inválido.")
            finite(t["width"], 0.01)
            if not isinstance(t["points"], list) or len(t["points"]) > 100:
                raise ValueError("Pontos de trilha inválidos.")
            for point in t["points"]:
                if len(point) != 2:
                    raise ValueError("Ponto exige x e y.")
                for coordinate in point:
                    finite(coordinate)
        if sum(len(t["points"]) + 1 for t in design["tracks"]) > 2000:
            raise ValueError("Limite nativo: 2000 segmentos de cobre por projeto.")
    except (KeyError, TypeError, StopIteration, AttributeError, OverflowError) as exc:
        raise ValueError(f"Documento nativo inválido: {exc}") from exc
    return design


def load(path):
    if Path(path).stat().st_size > 5_000_000:
        raise ValueError("Documento excede 5 MB.")
    return validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def save(path, design):
    validate(design)
    # Replace atomically; an interrupted write does not truncate the previous design.
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mpcb-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(design, handle, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def track_points(design, track):
    return [pad(design, track["from"]), *track["points"], pad(design, track["to"])]


def point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d)) if d else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def segment_distance(a, b, c, d):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if (cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0):
        return 0.0
    return min(point_segment(a, c, d), point_segment(b, c, d), point_segment(c, a, b), point_segment(d, a, b))


def pad_polygon(design, endpoint):
    ref, _ = endpoint.split(".")
    part = component(design, ref)
    x, y = pad(design, endpoint)
    width, height = LIBRARY[part["kind"]]["size"]
    angle = math.radians(part["rotation"])
    return [(x + dx * math.cos(angle) - dy * math.sin(angle),
             y + dx * math.sin(angle) + dy * math.cos(angle))
            for dx, dy in [(-width/2, -height/2), (width/2, -height/2), (width/2, height/2), (-width/2, height/2)]]


def polygon_edges(poly):
    return list(zip(poly, poly[1:] + poly[:1]))


def inside_polygon(point, poly):
    crosses = [(b[0]-a[0])*(point[1]-a[1]) - (b[1]-a[1])*(point[0]-a[0]) for a, b in polygon_edges(poly)]
    return all(v >= -1e-9 for v in crosses) or all(v <= 1e-9 for v in crosses)


def segment_polygon(a, b, poly):
    if inside_polygon(a, poly) or inside_polygon(b, poly):
        return 0.0
    return min(segment_distance(a, b, c, d) for c, d in polygon_edges(poly))


def polygon_distance(a, b):
    if inside_polygon(a[0], b) or inside_polygon(b[0], a):
        return 0.0
    return min(segment_distance(p, q, r, s) for p, q in polygon_edges(a) for r, s in polygon_edges(b))


def checks(design):
    """Check oriented rectangular pads and round-capped, single-layer copper traces."""
    validate(design)
    findings, pads, segments, nets = [], [], [], {}

    def issue(code, detail):
        findings.append({"code": code, "detail": detail})

    board = design["board"]
    clearance = board["clearance"]
    def inside(p, radius):
        return radius + clearance <= p[0] <= board["width"] - radius - clearance and radius + clearance <= p[1] <= board["height"] - radius - clearance

    for c in design["components"]:
        for pin, name in c["nets"].items():
            endpoint = f"{c['ref']}.{pin}"
            position = pad(design, endpoint)
            polygon = pad_polygon(design, endpoint)
            pads.append((endpoint, name, polygon))
            role = c["roles"][pin]
            if role == "nc":
                if name:
                    issue("ERC_NC_CONNECTED", f"{endpoint}: pino NC ligado a {name}.")
            elif not name:
                issue("ERC_UNASSIGNED", f"{endpoint}: sem rede ou marcação NC.")
            if name:
                nets.setdefault(name, []).append((endpoint, role))
            if not all(inside(p, 0) for p in polygon):
                issue("DRC_EDGE", f"{endpoint}: pad fora da margem da placa.")
    links = {}
    for index, t in enumerate(design["tracks"]):
        a, b = t["from"], t["to"]
        name = net(design, a)
        if not name or name != net(design, b):
            issue("DRC_SHORT", f"Trilha {index + 1}: terminais sem a mesma rede atribuída.")
            name = f"!invalid-{index}"
        else:
            links.setdefault(a, set()).add(b)
            links.setdefault(b, set()).add(a)
        if t["width"] < board["min_track"]:
            issue("DRC_WIDTH", f"Trilha {index + 1}: largura abaixo de {board['min_track']} mm.")
        points = track_points(design, t)
        if not all(inside(p, t["width"] / 2) for p in points):
            issue("DRC_EDGE", f"Trilha {index + 1}: fora da margem da placa.")
        for start, end in zip(points, points[1:]):
            segments.append((index, name, start, end, t["width"] / 2))
    for name, nodes in nets.items():
        if len(nodes) < 2:
            issue("ERC_SINGLE_PIN", f"{name}: rede com um único terminal.")
        if sum(role == "output" for _, role in nodes) > 1 or sum(role == "power_out" for _, role in nodes) > 1:
            issue("ERC_DRIVER_CONFLICT", f"{name}: múltiplas saídas declaradas.")
        if any(role == "input" for _, role in nodes) and not any(role == "output" for _, role in nodes):
            issue("ERC_NO_DRIVER", f"{name}: entrada sem saída declarada (modelo simplificado).")
        if any(role == "power_in" for _, role in nodes) and not any(role == "power_out" for _, role in nodes):
            issue("ERC_NO_POWER", f"{name}: alimentação sem fonte declarada.")
        reached, pending = set(), [nodes[0][0]]
        while pending:
            node = pending.pop()
            if node not in reached:
                reached.add(node)
                pending.extend(links.get(node, ()))
        if any(endpoint not in reached for endpoint, _ in nodes):
            issue("DRC_UNROUTED", f"{name}: terminais ainda não conectados por trilhas explícitas.")
    for i, (ep, name, polygon) in enumerate(pads):
        for ep2, other, poly2 in pads[i + 1:]:
            if name and name == other:
                continue
            if polygon_distance(polygon, poly2) < clearance - 1e-9 or polygon_distance(polygon, poly2) <= 1e-9:
                issue("DRC_PAD_CLEARANCE", f"{ep} / {ep2}: pads sem folga.")
        for index, other, a, b, r2 in segments:
            if name and name == other:
                continue
            if segment_polygon(a, b, polygon) < r2 + clearance - 1e-9:
                issue("DRC_TRACK_PAD", f"Trilha {index + 1} / {ep}: possível curto ou folga insuficiente.")
    for i, (index, name, a, b, radius) in enumerate(segments):
        for j, other, c, d, r2 in segments[i + 1:]:
            if index == j or name == other:
                continue
            if segment_distance(a, b, c, d) < radius + r2 + clearance - 1e-9:
                issue("DRC_TRACK_CLEARANCE", f"Trilhas {index + 1}/{j + 1}: cruzamento ou folga insuficiente.")
    if not design["components"]:
        issue("EMPTY", "Projeto vazio: não há circuito para validar.")
    return findings


def export_bom(design):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Reference", "Quantity", "Value", "Footprint"])
    for c in design["components"]:
        writer.writerow([c["ref"], 1, c["value"], c["kind"]])
    return output.getvalue()


def export_svg(design):
    validate(design)
    w, h = design["board"]["width"], design["board"]["height"]
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',
             f'<rect width="{w}" height="{h}" fill="#102a24" stroke="#8ecdb4" stroke-width="0.15"/>']
    for t in design["tracks"]:
        points = " ".join(f"{x},{y}" for x, y in track_points(design, t))
        lines.append(f'<polyline points="{points}" stroke="#f4ac57" stroke-width="{t["width"]}" fill="none" stroke-linecap="round"/>')
    for c in design["components"]:
        width, height = LIBRARY[c["kind"]]["size"]
        for pin in c["nets"]:
            x, y = pad(design, f"{c['ref']}.{pin}")
            lines.append(f'<rect x="{x-width/2}" y="{y-height/2}" width="{width}" height="{height}" transform="rotate({c["rotation"]},{x},{y})" fill="#f2d084"/>')
        lines.append(f'<text x="{c["x"]}" y="{c["y"]-2}" font-size="1.5" fill="white">{html.escape(c["ref"] + " " + c["value"])}</text>')
    return "\n".join(lines + ["</svg>"])


class History:
    def __init__(self):
        self.undo_stack, self.redo_stack = [], []

    def record(self, design):
        self.undo_stack.append(copy.deepcopy(design))
        self.undo_stack = self.undo_stack[-50:]
        self.redo_stack.clear()

    def undo(self, design):
        if not self.undo_stack:
            return design
        self.redo_stack.append(copy.deepcopy(design))
        return self.undo_stack.pop()

    def redo(self, design):
        if not self.redo_stack:
            return design
        self.undo_stack.append(copy.deepcopy(design))
        return self.redo_stack.pop()
