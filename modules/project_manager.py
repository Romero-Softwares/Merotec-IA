import os

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