import os
import re
import paramiko
from scp import SCPClient

# CONFIGURACIÓN
raspberry_ip = "192.168.0.35"
username = "cbpi"
password = "Fulleravive.13"
project_root = os.getcwd()
remote_tmp_path = "/tmp/cbpi_plugin_deploy"
setup_path = os.path.join(project_root, "setup.py")

# INCREMENTA VERSIÓN EN setup.py
def bump_version(setup_file):
    with open(setup_file, "r") as f:
        content = f.read()

    match = re.search(r"version=['\"](\d+)\.(\d+)\.(\d+)['\"]", content)
    if not match:
        raise ValueError("No se encontró la versión en setup.py")

    major, minor, patch = map(int, match.groups())
    patch += 1
    new_version = f"{major}.{minor}.{patch}"
    new_content = re.sub(r"version=['\"]\d+\.\d+\.\d+['\"]", f"version='{new_version}'", content)

    with open(setup_file, "w") as f:
        f.write(new_content)

    print(f"📦 Nueva versión del plugin: {new_version}")
    return new_version

# CREA CONEXIÓN SSH
def create_ssh_client(ip, user, passwd):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username=user, password=passwd)
    return ssh

# DESPLIEGA EL PLUGIN
def deploy():
    version = bump_version(setup_path)
    ssh = create_ssh_client(raspberry_ip, username, password)

    print(f"🧹 Borrando carpeta temporal previa: {remote_tmp_path}")
    ssh.exec_command(f"rm -rf {remote_tmp_path} && mkdir -p {remote_tmp_path}")

    print("📤 Subiendo el plugin completo a la Raspberry Pi...")
    with SCPClient(ssh.get_transport()) as scp:
        for item in os.listdir(project_root):
            if item.startswith(".") or item == "deploy_plugin.py":
                continue
            scp.put(os.path.join(project_root, item), remote_path=remote_tmp_path, recursive=True)

    print("💾 Instalando plugin con pip del entorno CBPi...")
    pip_cmd = f"~/.local/pipx/venvs/cbpi4/bin/python -m pip install --force-reinstall {remote_tmp_path}"
    stdin, stdout, stderr = ssh.exec_command(pip_cmd)
    print(stdout.read().decode())
    print(stderr.read().decode())

    print("🔁 Reiniciando CraftBeerPi...")
    ssh.exec_command("sudo systemctl restart cbpi.service")

    print(f"✅ Plugin desplegado correctamente. Versión instalada: {version}")
    ssh.close()

if __name__ == "__main__":
    deploy()

