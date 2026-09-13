import os
import json
import base64
import subprocess
import shutil
import re
from datetime import datetime
import requests
from nacl.public import PublicKey, SealedBox
from nacl.encoding import Base64Encoder


class GitHubRepoManager:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        self.base_url = "https://api.github.com"

    def repo_exists(self, owner: str, repo: str) -> bool:
        """检查仓库是否存在"""
        url = f"{self.base_url}/repos/{owner}/{repo}"
        resp = requests.get(url, headers=self.headers)
        return resp.status_code == 200

    def create_repo(self, name: str, description: str = "", private: bool = False, auto_init: bool = True, default_branch: str = "master") -> dict:
        """创建仓库"""
        url = f"{self.base_url}/user/repos"
        data = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": auto_init,
            "default_branch": default_branch,
        }
        resp = requests.post(url, headers=self.headers, json=data)
        resp.raise_for_status()
        print(f"[+] 仓库 '{name}' 创建成功，默认分支: {default_branch}")
        return resp.json()

    def set_workflow_permissions(self, owner: str, repo: str, permission: str = "write") -> dict:
        """设置仓库 Workflow permissions 权限
        permission: "read" 或 "write"
            "read"  = Read repository contents and packages permissions
            "write" = Read and write permissions
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/permissions/workflow"
        data = {
            "enabled": True,
            "default_workflow_permissions": permission,
        }
        resp = requests.put(url, headers=self.headers, json=data)
        resp.raise_for_status()
        print(f"[+] Workflow permissions 已设置为: {permission}")
        return {"status": resp.status_code}

    def encrypt_secret(self, public_key: str, secret_value: str) -> str:
        """使用仓库公钥加密 secret"""
        pk = PublicKey(public_key, encoder=Base64Encoder())
        sealed_box = SealedBox(pk)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    def get_repo_public_key(self, owner: str, repo: str) -> dict:
        """获取仓库的公钥，用于加密 secrets"""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/secrets/public-key"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def create_or_update_secret(self, owner: str, repo: str, secret_name: str, secret_value: str) -> dict:
        """创建或更新仓库 secret"""
        public_key_data = self.get_repo_public_key(owner, repo)
        encrypted_value = self.encrypt_secret(public_key_data["key"], secret_value)

        url = f"{self.base_url}/repos/{owner}/{repo}/actions/secrets/{secret_name}"
        data = {
            "encrypted_value": encrypted_value,
            "key_id": public_key_data["key_id"],
        }
        resp = requests.put(url, headers=self.headers, json=data)
        resp.raise_for_status()
        print(f"[+] Secret '{secret_name}' 已设置")
        return {"status": resp.status_code}

    def list_secrets(self, owner: str, repo: str) -> list:
        """列出仓库所有 secrets"""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/secrets"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("secrets", [])

    def get_secret(self, owner: str, repo: str, secret_name: str) -> str:
        """获取单个secret的值（需要解密）"""
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/secrets/{secret_name}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("value", "")

    def clone_repo(self, repo_url: str, target_dir: str, branch: str = None):
        """克隆仓库"""
        cmd = ["git", "clone", repo_url, target_dir]
        if branch:
            cmd.extend(["-b", branch])
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"[+] 仓库已克隆到: {target_dir}")

    def run_shell_script(self, script_content: str, env_vars: dict):
        """执行shell脚本"""
        env = os.environ.copy()
        env.update(env_vars)
        subprocess.run(script_content, shell=True, env=env, check=True, cwd=os.getcwd())


class RustDeskManager:
    def __init__(self, github_manager: GitHubRepoManager, source_owner: str, source_repo: str):
        self.github_manager = github_manager
        self.source_owner = source_owner
        self.source_repo = source_repo
        self.work_dir = os.path.join(os.getcwd(), "src")
        self.rustdesk_dir = os.path.join(self.work_dir, "rustdesk")
        self.hbb_common_dir = os.path.join(self.work_dir, "hbb_common")
        self.merge_dir = os.path.join(self.work_dir, "rustdesktest")

    def clean_work_dir(self):
        """清理工作目录"""
        if os.path.exists(self.work_dir):
            shutil.rmtree(self.work_dir)
        os.makedirs(self.work_dir)

    def clone_repos(self):
        """克隆rustdesk和hbb_common仓库"""
        print("[+] 克隆rustdesk仓库...")
        self.github_manager.clone_repo(
            "https://github.com/rustdesk/rustdesk.git",
            self.rustdesk_dir
        )
        print("[+] 克隆hbb_common仓库...")
        self.github_manager.clone_repo(
            "https://github.com/rustdesk/hbb_common.git",
            self.hbb_common_dir
        )

    def get_commit_ids(self, tag_id: str = "1.4.9"):
        """获取tag对应的commit id"""
        main_commit_id = ""
        sub_commit_id = ""

        if tag_id:
            # 获取主仓库commit id
            result = subprocess.run(
                ["git", "rev-parse", tag_id],
                cwd=self.rustdesk_dir,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                main_commit_id = result.stdout.strip()
                print(f"[+] Main Commit ID: {main_commit_id}")

            # 获取子模块commit id
            result = subprocess.run(
                ["git", "ls-tree", tag_id, "libs/hbb_common"],
                cwd=self.rustdesk_dir,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 3:
                    sub_commit_id = parts[2]
                    print(f"[+] SUB Commit ID: {sub_commit_id}")

        return main_commit_id, sub_commit_id

    def modify_hbb_common(self, sub_commit_id: str, secrets: dict):
        """修改hbb_common仓库"""
        print("[+] 修改hbb_common仓库...")

        # 切换到子模块commit
        if sub_commit_id:
            subprocess.run(
                ["git", "checkout", "-b", datetime.now().strftime("%Y%m%d%H%M%S")],
                cwd=self.hbb_common_dir,
                check=True,
                capture_output=True
            )
            subprocess.run(
                ["git", "reset", "--hard", sub_commit_id],
                cwd=self.hbb_common_dir,
                check=True,
                capture_output=True
            )

        # 读取端口配置
        port_21114 = secrets.get("PORT21114", "21114")
        port_21115 = secrets.get("PORT21115", "21115")
        port_21116 = secrets.get("PORT21116", "21116")
        port_21117 = secrets.get("PORT21117", "21117")
        port_21118 = secrets.get("PORT21118", "21118")
        port_21119 = secrets.get("PORT21119", "21119")
        rendezvous_servers = secrets.get("RENDEZVOUS_SERVERS", "rs-ny.rustdesk.com")
        public_rs_pub_key = secrets.get("PUBLIC_RS_PUB_KEY", "OeVuKk5nlHiXp+APNn0Y3pC1Iwpwn44JGqrQCsWqmBw=")
        hard_settings = secrets.get("HARD_SETTINGS", "123456")
        api_server = secrets.get("API_SERVER", "api.rustdesk.com")

        # 使用sed修改文件
        replacements = {
            "21114": port_21114,
            "21115": port_21115,
            "21116": port_21116,
            "21117": port_21117,
            "21118": port_21118,
            "21119": port_21119,
        }

        for old_port, new_port in replacements.items():
            self._sed_replace(self.hbb_common_dir, old_port, new_port)

        # 修改RENDEZVOUS_SERVERS
        self._sed_replace_config(
            self.hbb_common_dir,
            r'pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[".*?"\];',
            f'pub const RENDEZVOUS_SERVERS: &[&str] = &["{rendezvous_servers}"];'
        )

        # 修改PUBLIC_RS_PUB_KEY或RS_PUB_KEY
        config_rs_path = os.path.join(self.hbb_common_dir, "src", "config.rs")
        if os.path.exists(config_rs_path):
            with open(config_rs_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if "pub const PUBLIC_RS_PUB_KEY" in content:
                self._sed_replace_config(
                    self.hbb_common_dir,
                    r'pub const PUBLIC_RS_PUB_KEY: &str = ".*?";',
                    f'pub const PUBLIC_RS_PUB_KEY: &str = "{public_rs_pub_key}";'
                )
            else:
                self._sed_replace_config(
                    self.hbb_common_dir,
                    r'pub const RS_PUB_KEY: &str = ".*?";',
                    f'pub const RS_PUB_KEY: &str = "{public_rs_pub_key}";'
                )

        # 修改HARD_SETTINGS
        self._sed_replace_config(
            self.hbb_common_dir,
            r'pub static ref HARD_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new\(\{let mut m = HashMap::new\(\);m\.insert\("password"\.to_owned\(\), ".*?"\.to_owned\(\)\);m\}\);',
            f'pub static ref HARD_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new({{let mut m = HashMap::new();m.insert("password".to_owned(), "{hard_settings}".to_owned());m}});'
        )

        # 修改API_SERVER
        self._sed_replace(
            self.hbb_common_dir,
            "https://api.rustdesk.com/version/latest",
            f"http://{api_server}/version/latest"
        )

        # 修改config.rs中的其他配置
        config_rs_path = os.path.join(self.hbb_common_dir, "src", "config.rs")
        if os.path.exists(config_rs_path):
            with open(config_rs_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = content.replace("1_000_000_000..2_000_000_000", "100_000..1_000_000")
            content = content.replace("id &= 0x1FFFFFFF;", "id = (id % 900_000) + 100_000;")
            with open(config_rs_path, 'w', encoding='utf-8') as f:
                f.write(content)

        # 修改lib.rs中的正则表达式
        lib_rs_path = os.path.join(self.hbb_common_dir, "src", "lib.rs")
        if os.path.exists(lib_rs_path):
            with open(lib_rs_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = content.replace(
                r'regex::Regex::new(r"^[a-zA-Z][\w-]{5,15}$")',
                r'regex::Regex::new(r"^[\w-]{6,16}$")'
            )
            with open(lib_rs_path, 'w', encoding='utf-8') as f:
                f.write(content)

        # 删除.gitignore和.git
        gitignore_path = os.path.join(self.hbb_common_dir, ".gitignore")
        if os.path.exists(gitignore_path):
            os.remove(gitignore_path)
        git_dir = os.path.join(self.hbb_common_dir, ".git")
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir)

    def modify_rustdesk(self, main_commit_id: str, secrets: dict):
        """修改rustdesk仓库"""
        print("[+] 修改rustdesk仓库...")

        # 切换到主仓库commit
        if main_commit_id:
            subprocess.run(
                ["git", "checkout", "-b", datetime.now().strftime("%Y%m%d%H%M%S")],
                cwd=self.rustdesk_dir,
                check=True,
                capture_output=True
            )
            subprocess.run(
                ["git", "reset", "--hard", main_commit_id],
                cwd=self.rustdesk_dir,
                check=True,
                capture_output=True
            )

        # 删除子模块
        sub_prefix = "libs/hbb_common"
        subprocess.run(
            ["git", "submodule", "deinit", "-f", sub_prefix],
            cwd=self.rustdesk_dir,
            check=False,
            capture_output=True
        )
        subprocess.run(
            ["git", "rm", "--cached", sub_prefix],
            cwd=self.rustdesk_dir,
            check=False,
            capture_output=True
        )

        # 清理子模块
        modules_dir = os.path.join(self.rustdesk_dir, ".git", "modules", sub_prefix)
        if os.path.exists(modules_dir):
            shutil.rmtree(modules_dir)

        submodule_dir = os.path.join(self.rustdesk_dir, sub_prefix)
        if os.path.exists(submodule_dir):
            shutil.rmtree(submodule_dir)

        # 删除.gitmodules和dependabot.yml
        gitmodules_path = os.path.join(self.rustdesk_dir, ".gitmodules")
        if os.path.exists(gitmodules_path):
            os.remove(gitmodules_path)

        dependabot_path = os.path.join(self.rustdesk_dir, ".github", "dependabot.yml")
        if os.path.exists(dependabot_path):
            os.remove(dependabot_path)

        # 创建子模块目录并复制文件
        os.makedirs(submodule_dir, exist_ok=True)
        self._copy_files(self.hbb_common_dir, submodule_dir)

        # 读取secrets
        port_21114 = secrets.get("PORT21114", "21114")
        port_21115 = secrets.get("PORT21115", "21115")
        port_21116 = secrets.get("PORT21116", "21116")
        port_21117 = secrets.get("PORT21117", "21117")
        port_21118 = secrets.get("PORT21118", "21118")
        port_21119 = secrets.get("PORT21119", "21119")
        admin_server = secrets.get("ADMIN_SERVER", "admin.rustdesk.com")
        repo_name = secrets.get("REPO_NAME", "")

        # 修改端口（使用不同的端口避免冲突）
        port_mapping = {
            "21114": "26114",
            "21115": "26115",
            "21116": "26116",
            "21117": "26117",
            "21118": "26118",
            "21119": "26119",
        }

        for old_port, new_port in port_mapping.items():
            self._sed_replace(self.rustdesk_dir, old_port, new_port)

        # 修改其他配置
        self._sed_replace(
            self.rustdesk_dir,
            "572f695136261188308f16ad2ca5c851a712c464060ae6974944458eb83880ba",
            "572f695136211188308f16ad2ca5c851a712c464060ae6974944458eb83880ba"
        )

        # 修改admin server
        self._sed_replace_config(
            self.rustdesk_dir,
            r'"https://admin.rustdesk.com".to_owned()',
            f'"http://{admin_server}".to_owned()'
        )

        # 修改flutter-ci.yml
        flutter_ci_path = os.path.join(self.rustdesk_dir, ".github", "workflows", "flutter-ci.yml")
        if os.path.exists(flutter_ci_path):
            with open(flutter_ci_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = content.replace("upload-artifact: false", "upload-artifact: true")
            with open(flutter_ci_path, 'w', encoding='utf-8') as f:
                f.write(content)

        # 修改下载链接
        if repo_name:
            self._sed_replace(
                self.rustdesk_dir,
                "https://rustdesk.com/download",
                f"https://github.com/{repo_name}/releases/latest"
            )
            self._sed_replace(
                self.rustdesk_dir,
                "https://api.github.com/repos/rustdesk/rustdesk/releases/latest",
                f"https://api.github.com/repos/{repo_name}/releases/latest"
            )
            self._sed_replace(
                self.rustdesk_dir,
                "https://github.com/rustdesk/rustdesk/releases/download/fdroid-version/rustdesk-version.txt",
                f"https://github.com/{repo_name}/releases/download/fdroid-version/rustdesk-version.txt"
            )

        # 修改其他配置
        self._sed_replace(self.rustdesk_dir, "prerelease: true", "prerelease: false")
        self._sed_replace(self.rustdesk_dir, "submodules: recursive", "submodules: false")

        # 修改dart文件
        dialog_path = os.path.join(self.rustdesk_dir, "flutter", "lib", "common", "widgets", "dialog.dart")
        if os.path.exists(dialog_path):
            with open(dialog_path, 'r', encoding='utf-8') as f:
                content = f.read()
            content = content.replace(
                "RegexValidationRule('starts with a letter', RegExp(r'^[a-zA-Z]'))",
                "RegexValidationRule('starts with a letter', RegExp(r'^[\\\\w]'))"
            )
            with open(dialog_path, 'w', encoding='utf-8') as f:
                f.write(content)

        # 只删除.gitignore
        gitignore_path = os.path.join(self.rustdesk_dir, ".gitignore")
        if os.path.exists(gitignore_path):
            os.remove(gitignore_path)

    def push_to_new_repo(self, target_repo_url: str, repo_exists: bool = False):
        """推送到新仓库"""
        print("[+] 推送到新仓库...")

        # 配置git用户信息
        subprocess.run(["git", "config", "user.email", "action@github.com"], cwd=self.rustdesk_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "GitHub Action"], cwd=self.rustdesk_dir, check=True, capture_output=True)

        if repo_exists:
            # 新仓库已存在，先克隆新仓库
            print("[+] 克隆新仓库...")
            temp_dir = self.rustdesk_dir + "_temp"
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            
            # 克隆新仓库到临时目录
            subprocess.run(["git", "clone", target_repo_url, temp_dir], check=True, capture_output=True)
            
            # 清空rustdesk_dir的.git目录
            git_dir = os.path.join(self.rustdesk_dir, ".git")
            if os.path.exists(git_dir):
                shutil.rmtree(git_dir)
            
            # 将新仓库的.git目录复制到rustdesk_dir
            shutil.copytree(
                os.path.join(temp_dir, ".git"),
                git_dir
            )
            
            # 清理临时目录
            shutil.rmtree(temp_dir)
            
            # 配置remote
            subprocess.run(["git", "remote", "remove", "origin"], cwd=self.rustdesk_dir, check=False, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", target_repo_url], cwd=self.rustdesk_dir, check=True, capture_output=True)
            
            # 切换到master分支
            subprocess.run(["git", "checkout", "-B", "master"], cwd=self.rustdesk_dir, check=True, capture_output=True)
        else:
            # 新仓库不存在，初始化新仓库
            subprocess.run(["git", "init"], cwd=self.rustdesk_dir, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", target_repo_url], cwd=self.rustdesk_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "master"], cwd=self.rustdesk_dir, check=True, capture_output=True)

        # 添加所有文件
        subprocess.run(["git", "add", "."], cwd=self.rustdesk_dir, check=True, capture_output=True)
        
        # 检查是否有文件需要提交
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.rustdesk_dir,
            capture_output=True,
            text=True
        )
        if not result.stdout.strip():
            print("[!] 没有文件需要提交，跳过 commit")
            return
        
        subprocess.run(
            ["git", "commit", "-m", "Update rustdesk configuration"],
            cwd=self.rustdesk_dir,
            check=True,
            capture_output=True
        )
        subprocess.run(["git", "push", "-u", "origin", "master"], cwd=self.rustdesk_dir, check=True, capture_output=True)
        print("[+] 代码已推送到新仓库")

    def _sed_replace(self, directory: str, old_str: str, new_str: str):
        """在目录中所有文件替换字符串"""
        for root, dirs, files in os.walk(directory):
            # 跳过.git目录
            if '.git' in dirs:
                dirs.remove('.git')
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if old_str in content:
                        content = content.replace(old_str, new_str)
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                except (UnicodeDecodeError, PermissionError):
                    pass

    def _sed_replace_config(self, directory: str, pattern: str, replacement: str):
        """使用正则表达式在目录中所有文件替换字符串"""
        for root, dirs, files in os.walk(directory):
            # 跳过.git目录
            if '.git' in dirs:
                dirs.remove('.git')
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    new_content = re.sub(pattern, replacement, content)
                    if new_content != content:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                except (UnicodeDecodeError, PermissionError):
                    pass

    def _copy_files(self, src_dir: str, dst_dir: str):
        """复制目录内容"""
        for item in os.listdir(src_dir):
            if item == '.git':
                continue
            src_path = os.path.join(src_dir, item)
            dst_path = os.path.join(dst_dir, item)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns('.git'))
            else:
                shutil.copy2(src_path, dst_path)


def main():
    token = os.environ.get("NEW_GITHUB_TOKEN")
    current_repo = os.environ.get("CURRENT_REPO")  # 当前仓库，格式: owner/repo
    new_repo_name = os.environ.get("NEWREPO")
    repo_owner = os.environ.get("REPO_OWNER")

    missing = []
    if not token:
        missing.append("NEW_GITHUB_TOKEN")
    if not current_repo:
        missing.append("CURRENT_REPO")
    if not new_repo_name:
        missing.append("NEWREPO")
    if not repo_owner:
        missing.append("REPO_OWNER")

    if missing:
        print(f"缺少环境变量: {', '.join(missing)}")
        print("请在仓库 Settings → Secrets and variables → Actions 中配置")
        return

    # 解析当前仓库的owner和repo
    current_owner, current_repo_name = current_repo.split("/", 1)

    try:
        manager = GitHubRepoManager(token)

        # 1. 检查新仓库是否存在
        print("\n[=] 步骤1: 检查新仓库是否存在")
        repo_exists = manager.repo_exists(repo_owner, new_repo_name)
        
        if repo_exists:
            print(f"[!] 仓库 '{repo_owner}/{new_repo_name}' 已存在，跳过创建和配置步骤")
        else:
            # 2. 创建新仓库
            print("\n[=] 步骤2: 创建新仓库")
            repo_info = manager.create_repo(
                name=new_repo_name,
                description="由 GitHub Actions 创建的 RustDesk 修改版仓库",
                private=False,
                auto_init=True,
            )

            # 3. 设置 Workflow permissions 为 Read and write permissions
            print("\n[=] 步骤3: 设置 Workflow permissions")
            manager.set_workflow_permissions(repo_owner, new_repo_name, permission="write")

            # 4. 从当前仓库获取secrets并配置到新仓库
            print("\n[=] 步骤4: 从当前仓库复制 Repository secrets 到新仓库")
            current_secrets = manager.list_secrets(current_owner, current_repo_name)
            for secret in current_secrets:
                secret_name = secret["name"]
                secret_value = manager.get_secret(current_owner, current_repo_name, secret_name)
                if secret_value:
                    manager.create_or_update_secret(repo_owner, new_repo_name, secret_name, secret_value)
                    print(f"    复制 secret: {secret_name}")

            # 验证
            print("\n[=] 当前新仓库 secrets:")
            for s in manager.list_secrets(repo_owner, new_repo_name):
                print(f"    - {s['name']}")

        # 5. 读取secrets用于修改rustdesk配置
        secrets = {
            "RENDEZVOUS_SERVERS": os.environ.get("RENDEZVOUS_SERVERS", "rs-ny.rustdesk.com"),
            "PUBLIC_RS_PUB_KEY": os.environ.get("PUBLIC_RS_PUB_KEY", "OeVuKk5nlHiXp+APNn0Y3pC1Iwpwn44JGqrQCsWqmBw="),
            "HARD_SETTINGS": os.environ.get("HARD_SETTINGS", "123456"),
            "API_SERVER": os.environ.get("API_SERVER", "api.rustdesk.com"),
            "ADMIN_SERVER": os.environ.get("ADMIN_SERVER", "admin.rustdesk.com"),
            "PORT21114": os.environ.get("PORT21114", "21114"),
            "PORT21115": os.environ.get("PORT21115", "21115"),
            "PORT21116": os.environ.get("PORT21116", "21116"),
            "PORT21117": os.environ.get("PORT21117", "21117"),
            "PORT21118": os.environ.get("PORT21118", "21118"),
            "PORT21119": os.environ.get("PORT21119", "21119"),
            "REPO_NAME": f"{repo_owner}/{new_repo_name}",
        }

        # 6. 克隆rustdesk仓库并修改
        print("\n[=] 步骤6: 克隆并修改rustdesk仓库")
        rustdesk_manager = RustDeskManager(manager, "rustdesk", "rustdesk")
        rustdesk_manager.clean_work_dir()
        rustdesk_manager.clone_repos()

        # 获取commit id
        main_commit_id, sub_commit_id = rustdesk_manager.get_commit_ids("1.4.9")

        # 修改hbb_common
        rustdesk_manager.modify_hbb_common(sub_commit_id, secrets)

        # 修改rustdesk
        rustdesk_manager.modify_rustdesk(main_commit_id, secrets)

        # 7. 推送到新仓库
        print("\n[=] 步骤7: 推送到新仓库")
        target_repo_url = f"https://{token}@github.com/{repo_owner}/{new_repo_name}.git"
        rustdesk_manager.push_to_new_repo(target_repo_url, repo_exists)

        print("\n[+] 所有操作完成!")

    except requests.exceptions.RequestException as e:
        print(f"API请求错误: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"响应内容: {e.response.text}")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
