import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


class MemorySubnet:
    """Pequeno grafo local de memória para o contexto do agente da IDE.

    A sub-rede mantém sinais leves do projeto conectados por tipo de relação
    para que o agente receba contexto compacto e durável sem reler tudo.
    """

    def __init__(self, root_path, max_nodes=120):
        self.root_path = Path(root_path).resolve()
        self.max_nodes = max_nodes
        self.nodes = {}
        self.edges = defaultdict(Counter)
        self.created_at = datetime.now().isoformat(timespec="seconds")

    def reset_workspace(self, root_path):
        root_path = Path(root_path).resolve()
        if root_path == self.root_path:
            return
        self.root_path = root_path
        self.nodes.clear()
        self.edges.clear()
        self.created_at = datetime.now().isoformat(timespec="seconds")

    def add_node(self, key, kind, label=None, weight=1, metadata=None):
        key = str(key).strip()
        if not key:
            return None
        node = self.nodes.get(key)
        if node is None:
            node = {
                "key": key,
                "kind": kind,
                "label": label or key,
                "weight": 0,
                "metadata": {},
                "updated_at": "",
            }
            self.nodes[key] = node
        node["kind"] = kind or node["kind"]
        node["label"] = label or node["label"]
        node["weight"] = int(node.get("weight", 0)) + max(1, int(weight))
        node["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if metadata:
            node["metadata"].update(metadata)
        self._trim()
        return node

    def connect(self, source, relation, target, weight=1):
        source = str(source).strip()
        target = str(target).strip()
        relation = str(relation).strip() or "related_to"
        if not source or not target:
            return
        self.edges[(source, relation)][target] += max(1, int(weight))

    def ingest_project_signals(self, files, summary="", recent_changes=None):
        self.add_node("workspace", "workspace", self.root_path.name, weight=3)
        if summary:
            self.add_node("summary", "summary", summary.splitlines()[0][:120], metadata={"text": summary[:1000]})
            self.connect("workspace", "has_summary", "summary", weight=2)

        extension_counts = Counter()
        folder_counts = Counter()
        key_file_names = {
            "main.py",
            "app.py",
            "index.html",
            "package.json",
            "requirements.txt",
            "pubspec.yaml",
            "readme.md",
        }
        for path, rel in files:
            rel_text = rel.as_posix()
            folder = rel.parts[0] if len(rel.parts) > 1 else "."
            extension = path.suffix.lower() or "[sem-extensao]"
            extension_counts[extension] += 1
            folder_counts[folder] += 1
            if path.name.lower() in key_file_names:
                self.add_node(rel_text, "file", rel_text, weight=4, metadata={"suffix": extension})
                self.connect("workspace", "entry_file", rel_text, weight=3)

        for extension, count in extension_counts.most_common(8):
            key = f"ext:{extension}"
            self.add_node(key, "extension", extension, weight=count)
            self.connect("workspace", "uses_extension", key, weight=count)

        for folder, count in folder_counts.most_common(10):
            key = f"folder:{folder}"
            self.add_node(key, "folder", folder, weight=count)
            self.connect("workspace", "has_folder", key, weight=count)

        for change in recent_changes or []:
            rel = change.get("rel") or change.get("path") or ""
            if not rel:
                continue
            key = f"change:{rel}"
            self.add_node(
                key,
                "change",
                rel,
                weight=5,
                metadata={
                    "action": change.get("action", ""),
                    "objective": change.get("objective", "")[:240],
                    "timestamp": change.get("timestamp", ""),
                },
            )
            self.connect("workspace", "recent_change", key, weight=4)

    def format_for_agent(self, limit=12):
        if not self.nodes:
            return "- Sub-rede sem sinais registrados ainda."
        ranked = sorted(self.nodes.values(), key=lambda item: item.get("weight", 0), reverse=True)
        lines = [f"- Workspace: {self.root_path.name}"]
        for node in ranked[:limit]:
            if node["key"] == "workspace":
                continue
            meta = node.get("metadata") or {}
            detail = ""
            if node["kind"] == "change":
                detail = f" ({meta.get('action', 'alteracao')})"
            elif node["kind"] == "extension":
                detail = f" ({node.get('weight', 0)} sinais)"
            lines.append(f"- {node['kind']}: {node['label']}{detail}")
            if len(lines) >= limit:
                break
        return "\n".join(lines)

    def to_dict(self):
        return {
            "root_path": str(self.root_path),
            "created_at": self.created_at,
            "nodes": self.nodes,
            "edges": [
                {"source": source, "relation": relation, "target": target, "weight": weight}
                for (source, relation), targets in self.edges.items()
                for target, weight in targets.items()
            ],
        }

    def export_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def _trim(self):
        overflow = len(self.nodes) - self.max_nodes
        if overflow <= 0:
            return
        removable = sorted(
            (node for node in self.nodes.values() if node["key"] != "workspace"),
            key=lambda item: (item.get("weight", 0), item.get("updated_at", "")),
        )
        for node in removable[:overflow]:
            self.nodes.pop(node["key"], None)
        valid_keys = set(self.nodes)
        for edge_key in list(self.edges):
            source, _relation = edge_key
            if source not in valid_keys:
                self.edges.pop(edge_key, None)
                continue
            for target in list(self.edges[edge_key]):
                if target not in valid_keys:
                    del self.edges[edge_key][target]
