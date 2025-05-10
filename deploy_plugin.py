import os
import re
import paramiko
from scp import SCPClient

# CONFIGURACIÓN
raspberry_ip = "192.168.0.35"
username = "cbpi"
password = "Fulleravive.13"
project_root = os.getcwd()
plugin_dir = "cbpi4_GlycolChillerWithDependantTargetTemperature"
plugin_file_path = os.path.join(project_root, plugin_dir, "__init__.py")
setup_path = os.path.join(project_root, "setup.py")
remote_tmp_path = "/tmp/cbpi_plugin_deploy"
original_class_name = "GlycolChillerWithDependantTargetTemperature"

# INCREMENTA LA VERSIÓN EN setup.py
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

# MODIFICA NOMBRE DE CLASE Y REGISTRO
def patch_plugin_class_and_register(version):
    version_suffix = f"v{version.replace('.', '_')}"
    new_class_name = f"{original_class_name}_{version_suffix}"
    plugin_register_name = f"ChillerDepTemp_{version_suffix}"

    with open(plugin_file_path, "r") as f:
        content = f.read()

    # Cambia cualquier clase CBPiFermenterLogic que empiece por GlycolChiller...
    content, class_count = re.subn(
        r'class GlycolChillerWithDependantTargetTemperature(?:_v\d+_\d+_\d+)?\s*\(CBPiFermenterLogic\):',
        f'class {new_class_name}(CBPiFermenterLogic):',
        content
    )

    # Cambia cualquier registro cbpi.plugin.register con esa clase
    content, register_count = re.subn(
        r'cbpi\.plugin\.register\(\s*["\'].*?["\']\s*,\s*GlycolChillerWithDependantTargetTemperature(?:_v\d+_\d+_\d+)?\s*\)',
        f'cbpi.plugin.register("{plugin_register_name}", {new_class_name})',
        content
    )

    with open(plugin_file_path, "w") as f:
        f.write(content)

    if class_count == 0 or register_count == 0:
        print("⚠️  Advertencia: No se pudieron modificar clase o registro. ¿El patrón original ha cambiado?")
    else:
        print(f"🔤 Clase renombrada: {new_class_name}")
        print(f"🆕 Plugin registrado como: {plugin_register_name}")

    return new_class_name, plugin_register_name


# CREA CLIENTE SSH
def create_ssh_client(ip, user, passwd):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username=user, password=passwd)
    return ssh

# DESPLIEGA EL PLUGIN
def deploy():
    version = bump_version(setup_path)
    new_class, plugin_name = patch_plugin_class_and_register(version)
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

    print("🔁 Reiniciando completamente la Raspberry Pi...")
    ssh.exec_command("sudo reboot")


    print(f"✅ Plugin desplegado correctamente: {plugin_name} (v{version})")

if __name__ == "__main__":
    deploy()
