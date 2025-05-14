import paramiko
import sys

# Configuración SSH
raspberry_ip = "192.168.0.35"
username = "cbpi"
password = "Fulleravive.13"

# Comando para seguir logs del servicio CraftBeerPi
journalctl_cmd = "journalctl -u craftbeerpi.service -f --no-pager"

# Palabras clave por defecto
DEFAULT_FILTER_KEYWORDS = ["[CHILLER]", "[FERMENTER]"]

def seguir_logs(use_filter=True, custom_filters=None):
    try:
        print("🔌 Conectando a Raspberry Pi...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(raspberry_ip, username=username, password=password)

        print("📡 Escuchando logs de CraftBeerPi...\n")

        if not use_filter:
            print("🔎 Modo sin filtro activado: mostrando todos los logs\n")
        else:
            filters_to_use = custom_filters if custom_filters else DEFAULT_FILTER_KEYWORDS
            print(f"🔎 Filtrando por: {', '.join(filters_to_use)}\n")

        stdin, stdout, stderr = ssh.exec_command(journalctl_cmd)

        for line in iter(stdout.readline, ""):
            if not use_filter or any(keyword in line for keyword in (custom_filters or DEFAULT_FILTER_KEYWORDS)):
                print(line.strip())

    except KeyboardInterrupt:
        print("\n🛑 Finalizado por el usuario.")
    except Exception as e:
        print("❌ Error:", str(e))

if __name__ == "__main__":
    use_filter = True
    custom_filters = None

    if "--noFilter" in sys.argv:
        use_filter = False
    elif "--filter" in sys.argv:
        try:
            idx = sys.argv.index("--filter")
            keyword = sys.argv[idx + 1]
            custom_filters = [keyword]
        except IndexError:
            print("❌ Error: Debes especificar una palabra clave tras '--filter'")
            sys.exit(1)

    seguir_logs(use_filter=use_filter, custom_filters=custom_filters)

