import os
import re
from pathlib import Path

class ProjectManager:
    def __init__(self, base_dir="projects"):
        self.base_dir = base_dir
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

    def create_project_env(self, project_name):
        project_path = os.path.join(self.base_dir, project_name)
        folders = ['src', 'drivers', 'docs', 'tests', 'research']
        for folder in folders:
            os.makedirs(os.path.join(project_path, folder), exist_ok=True)
        return project_path

    def create_project(self, parent_dir, project_name, project_type="empty"):
        """Cria um projeto novo sem sobrescrever uma pasta existente."""
        name = (project_name or "").strip()
        if not name or name in {".", ".."}:
            raise ValueError("Informe um nome de projeto.")
        if re.search(r'[<>:"/\\|?*]', name) or name.endswith((".", " ")):
            raise ValueError("O nome do projeto contem caracteres invalidos.")

        parent = Path(parent_dir).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        project_path = parent / name
        if project_path.exists():
            raise FileExistsError(f"A pasta '{name}' ja existe.")

        kind = (project_type or "empty").strip().lower()
        aliases = {
            "vazio": "empty",
            "html": "web",
            "javascript": "web",
            "flutter/dart": "flutter",
            "flutter_dart": "flutter",
            "dart/flutter": "flutter",
            "flet-python": "flet",
            "python-flet": "flet",
        }
        kind = aliases.get(kind, kind)
        if kind not in {"empty", "python", "web", "flet", "dart", "flutter"}:
            raise ValueError("Tipo invalido. Use vazio, python, web, flet, dart ou flutter.")
        safe_pubspec_name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "merotec_app"
        dart_display_name = name.replace("\\", "\\\\").replace("'", "\\'")

        project_path.mkdir()
        (project_path / "README.md").write_text(
            f"# {name}\n\nProjeto criado pela Merotec IA IDE.\n", encoding="utf-8"
        )
        if kind == "python":
            (project_path / "tests").mkdir()
            (project_path / "main.py").write_text(
                'def main():\n    print("Ola, Merotec IA!")\n\n\nif __name__ == "__main__":\n    main()\n',
                encoding="utf-8",
            )
            (project_path / "requirements.txt").write_text("", encoding="utf-8")
        elif kind == "flet":
            (project_path / "main.py").write_text(
                "import flet as ft\n\n\n"
                "def main(page: ft.Page):\n"
                f"    page.title = \"{name}\"\n"
                "    page.add(ft.Text(\"Ola, Merotec IA + Flet!\"))\n\n\n"
                "if __name__ == \"__main__\":\n"
                "    ft.app(target=main)\n",
                encoding="utf-8",
            )
            (project_path / "requirements.txt").write_text("flet\n", encoding="utf-8")
        elif kind == "dart":
            (project_path / "bin").mkdir()
            (project_path / "bin" / "main.dart").write_text(
                "void main(List<String> arguments) {\n"
                "  print('Ola, Merotec IA + Dart!');\n"
                "}\n",
                encoding="utf-8",
            )
            (project_path / "pubspec.yaml").write_text(
                f"name: {safe_pubspec_name}\n"
                "description: Projeto Dart criado pela Merotec IA IDE.\n"
                "version: 1.0.0\n"
                "environment:\n"
                "  sdk: ^3.0.0\n",
                encoding="utf-8",
            )
        elif kind == "flutter":
            (project_path / "lib").mkdir()
            (project_path / "lib" / "main.dart").write_text(
                "import 'package:flutter/material.dart';\n\n"
                "void main() => runApp(const MerotecApp());\n\n"
                "class MerotecApp extends StatelessWidget {\n"
                "  const MerotecApp({super.key});\n\n"
                "  @override\n"
                "  Widget build(BuildContext context) {\n"
                "    return MaterialApp(\n"
                f"      title: '{dart_display_name}',\n"
                "      home: Scaffold(\n"
                f"        appBar: AppBar(title: const Text('{dart_display_name}')),\n"
                "        body: const Center(child: Text('Ola, Merotec IA + Flutter!')),\n"
                "      ),\n"
                "    );\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            (project_path / "pubspec.yaml").write_text(
                f"name: {safe_pubspec_name}\n"
                "description: Projeto Flutter criado pela Merotec IA IDE.\n"
                "version: 1.0.0+1\n"
                "environment:\n"
                "  sdk: ^3.0.0\n"
                "dependencies:\n"
                "  flutter:\n"
                "    sdk: flutter\n"
                "dev_dependencies:\n"
                "  flutter_test:\n"
                "    sdk: flutter\n"
                "flutter:\n"
                "  uses-material-design: true\n",
                encoding="utf-8",
            )
        elif kind == "web":
            (project_path / "index.html").write_text(
                "<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n"
                "  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                f"  <title>{name}</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n"
                f"<body>\n  <main><h1>{name}</h1></main>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n",
                encoding="utf-8",
            )
            (project_path / "style.css").write_text(
                "body { font-family: system-ui, sans-serif; margin: 2rem; }\n", encoding="utf-8"
            )
            (project_path / "app.js").write_text('console.log("Projeto pronto.");\n', encoding="utf-8")
        return project_path

    def save_file(self, project_name, subfolder, filename, content):
        path = os.path.join(self.base_dir, project_name, subfolder, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def save_updated_version(self, original_path, new_content):
        """Cria uma nova versão do ficheiro para não apagar o original."""
        base, ext = os.path.splitext(original_path)
        # Tenta criar nomes como arquivo_v1.py, arquivo_v2.py...
        counter = 1
        new_path = f"{base}_v{counter}{ext}"

        while os.path.exists(new_path):
            counter += 1
            new_path = f"{base}_v{counter}{ext}"

        with open(new_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return new_path
