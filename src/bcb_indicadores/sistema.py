"""Integração com o sistema operacional: coleta diária agendada e atalho no menu.

- Linux com systemd: timer do usuário (não precisa de administrador). Com
  ``Persistent=true``, se o computador estiver desligado no horário, a coleta
  roda assim que ele for ligado.
- Outros Unix (macOS, Linux sem systemd): crontab do usuário.
- Windows: Agendador de Tarefas (``schtasks``) e atalho no Menu Iniciar.

Todas as chamadas externas passam por ``Ambiente.executar``, o que permite
testar sem mexer no sistema de verdade.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

NOME = "bcb-indicadores"
UNIDADE = f"{NOME}-coleta"  # nome do serviço/timer do systemd
TAREFA_WINDOWS = "bcb-indicadores coleta"
MARCADOR_CRON = f"# {NOME}: coleta diária"


class ErroSistema(Exception):
    """Falha ao configurar o sistema operacional (mensagem pronta para o usuário)."""


def _executar(comando: Sequence[str], entrada: str | None = None) -> subprocess.CompletedProcess:
    # Lista de argumentos, sem shell: nada é interpretado como comando.
    return subprocess.run(list(comando), input=entrada, capture_output=True, text=True, check=False)


@dataclass
class Ambiente:
    sistema: str = field(default_factory=platform.system)  # "Linux", "Windows", "Darwin"
    home: Path = field(default_factory=Path.home)
    python: str = sys.executable
    executar: Callable[..., subprocess.CompletedProcess] = _executar
    tem_systemd: bool | None = None
    appdata: Path | None = None  # Windows: pasta "Roaming" (onde fica o Menu Iniciar)

    def __post_init__(self) -> None:
        if self.tem_systemd is None:
            self.tem_systemd = self.sistema == "Linux" and shutil.which("systemctl") is not None
        if self.appdata is None:
            padrao = self.home / "AppData" / "Roaming"
            self.appdata = Path(os.environ.get("APPDATA") or padrao)

    def rodar(self, comando: Sequence[str], entrada: str | None = None) -> str:
        """Executa e devolve a saída; levanta ``ErroSistema`` se o comando falhar."""
        try:
            r = self.executar(comando, entrada)
        except FileNotFoundError as erro:
            raise ErroSistema(f"Comando não encontrado: {comando[0]}") from erro
        if r.returncode != 0:
            detalhe = (r.stderr or r.stdout or "").strip()
            raise ErroSistema(f"Falha ao executar {' '.join(comando[:3])}...: {detalhe}")
        return r.stdout


def comando_cli(
    ambiente: Ambiente, banco: Path, *args: str, sem_console: bool = False
) -> list[str]:
    """Linha de comando que roda o próprio programa com o banco em caminho absoluto."""
    python = ambiente.python
    if sem_console and ambiente.sistema == "Windows":
        # pythonw.exe roda sem abrir uma janela de terminal.
        candidato = Path(python).with_name("pythonw.exe")
        python = str(candidato) if candidato.exists() else python
    return [python, "-m", "bcb_indicadores", "--banco", str(Path(banco).resolve()), *args]


def _aspas(partes: Sequence[str]) -> str:
    """Junta argumentos com aspas duplas onde preciso (formato aceito por systemd e .desktop)."""
    saida = []
    for p in partes:
        if any(c in p for c in ' \t"\\$'):
            p = '"' + p.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'
        saida.append(p)
    return " ".join(saida)


def _validar_hora(hora: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in hora.split(":"))
    except ValueError:
        h = m = -1
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ErroSistema(f"Hora inválida {hora!r}; use HH:MM, por exemplo 19:00")
    return h, m


# ---------------------------------------------------------------------------
# Agendamento da coleta
# ---------------------------------------------------------------------------


def agendar(banco: Path, hora: str = "19:00", ambiente: Ambiente | None = None) -> str:
    """Agenda a coleta diária. Devolve uma mensagem explicando o que foi feito."""
    ambiente = ambiente or Ambiente()
    h, m = _validar_hora(hora)
    comando = comando_cli(ambiente, banco, "coletar", sem_console=True)

    if ambiente.sistema == "Windows":
        ambiente.rodar(
            [
                "schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", f"{h:02d}:{m:02d}",
                "/TN", TAREFA_WINDOWS, "/TR", subprocess.list2cmdline(comando),
            ]
        )  # fmt: skip
        return (
            f"Coleta agendada todos os dias às {h:02d}:{m:02d} no Agendador de Tarefas "
            f'("{TAREFA_WINDOWS}").'
        )

    if ambiente.tem_systemd:
        pasta = ambiente.home / ".config" / "systemd" / "user"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / f"{UNIDADE}.service").write_text(
            "[Unit]\n"
            "Description=Coleta diária dos indicadores do Banco Central (bcb-indicadores)\n"
            "Wants=network-online.target\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart={_aspas(comando)}\n",
            encoding="utf-8",
        )
        (pasta / f"{UNIDADE}.timer").write_text(
            "[Unit]\n"
            "Description=Agenda a coleta diária do bcb-indicadores\n\n"
            "[Timer]\n"
            f"OnCalendar=*-*-* {h:02d}:{m:02d}:00\n"
            "Persistent=true\n"
            "RandomizedDelaySec=5min\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n",
            encoding="utf-8",
        )
        ambiente.rodar(["systemctl", "--user", "daemon-reload"])
        ambiente.rodar(["systemctl", "--user", "enable", "--now", f"{UNIDADE}.timer"])
        return (
            f"Coleta agendada todos os dias às {h:02d}:{m:02d} (timer do systemd "
            f"{UNIDADE}.timer). Se o computador estiver desligado no horário, ela roda "
            f"quando ele ligar.\nHistórico: journalctl --user -u {UNIDADE}"
        )

    log = Path(banco).resolve().with_suffix(".log")
    linha = f"{m} {h} * * * {_aspas(comando)} >> {_aspas([str(log)])} 2>&1 {MARCADOR_CRON}"
    ambiente.rodar(["crontab", "-"], _crontab_sem_marcador(ambiente) + linha + "\n")
    return f"Coleta agendada todos os dias às {h:02d}:{m:02d} (crontab). Registro em {log}"


def desagendar(ambiente: Ambiente | None = None) -> str:
    ambiente = ambiente or Ambiente()
    if ambiente.sistema == "Windows":
        ambiente.rodar(["schtasks", "/Delete", "/F", "/TN", TAREFA_WINDOWS])
        return "Agendamento removido do Agendador de Tarefas."

    if ambiente.tem_systemd:
        pasta = ambiente.home / ".config" / "systemd" / "user"
        timer = pasta / f"{UNIDADE}.timer"
        if not timer.exists():
            return "Não havia coleta agendada."
        ambiente.rodar(["systemctl", "--user", "disable", "--now", f"{UNIDADE}.timer"])
        for sufixo in (".timer", ".service"):
            (pasta / f"{UNIDADE}{sufixo}").unlink(missing_ok=True)
        ambiente.rodar(["systemctl", "--user", "daemon-reload"])
        return "Agendamento removido."

    atual = _crontab_atual(ambiente)
    restante = _crontab_sem_marcador(ambiente, atual)
    if restante == atual:
        return "Não havia coleta agendada."
    ambiente.rodar(["crontab", "-"], restante)
    return "Agendamento removido do crontab."


def _crontab_atual(ambiente: Ambiente) -> str:
    r = ambiente.executar(["crontab", "-l"], None)
    # Sem crontab ainda, "crontab -l" termina com erro: tratamos como vazio.
    return r.stdout if r.returncode == 0 else ""


def _crontab_sem_marcador(ambiente: Ambiente, atual: str | None = None) -> str:
    atual = _crontab_atual(ambiente) if atual is None else atual
    linhas = [linha for linha in atual.splitlines() if MARCADOR_CRON not in linha]
    return "".join(f"{linha}\n" for linha in linhas)


# ---------------------------------------------------------------------------
# Atalho no menu de aplicativos
# ---------------------------------------------------------------------------


def criar_atalho(banco: Path, ambiente: Ambiente | None = None) -> str:
    ambiente = ambiente or Ambiente()
    comando = comando_cli(ambiente, banco, "web", sem_console=True)
    icone_origem = resources.files("bcb_indicadores").joinpath("icone.svg")

    if ambiente.sistema == "Windows":
        menu = _menu_iniciar(ambiente)
        menu.mkdir(parents=True, exist_ok=True)
        atalho = menu / "Indicadores BCB.lnk"
        script = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps(str(atalho))}); "
            f"$s.TargetPath = {_ps(comando[0])}; "
            f"$s.Arguments = {_ps(subprocess.list2cmdline(comando[1:]))}; "
            "$s.Description = 'Indicadores econômicos do Banco Central'; $s.Save()"
        )
        ambiente.rodar(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
        return f"Atalho criado no Menu Iniciar: {atalho}"

    if ambiente.sistema != "Linux":
        raise ErroSistema(
            "Atalho automático disponível só para Linux e Windows. "
            f"Use o comando: {_aspas(comando_cli(ambiente, banco, 'web'))}"
        )

    icones = ambiente.home / ".local" / "share" / "icons"
    icones.mkdir(parents=True, exist_ok=True)
    icone = icones / f"{NOME}.svg"
    icone.write_bytes(icone_origem.read_bytes())

    aplicativos = ambiente.home / ".local" / "share" / "applications"
    aplicativos.mkdir(parents=True, exist_ok=True)
    arquivo = aplicativos / f"{NOME}.desktop"
    arquivo.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Indicadores BCB\n"
        "GenericName=Indicadores econômicos\n"
        "Comment=Selic, IPCA, dólar e outros indicadores do Banco Central\n"
        f"Exec={_aspas(comando)}\n"
        f"Icon={icone}\n"
        "Terminal=false\n"
        "Categories=Office;Finance;\n"
        "Keywords=selic;ipca;cdi;dolar;banco central;\n",
        encoding="utf-8",
    )
    arquivo.chmod(0o755)
    return f'Atalho criado: procure por "Indicadores BCB" no menu de aplicativos.\n({arquivo})'


def remover_atalho(ambiente: Ambiente | None = None) -> str:
    ambiente = ambiente or Ambiente()
    if ambiente.sistema == "Windows":
        alvos = [_menu_iniciar(ambiente) / "Indicadores BCB.lnk"]
    else:
        alvos = [
            ambiente.home / ".local" / "share" / "applications" / f"{NOME}.desktop",
            ambiente.home / ".local" / "share" / "icons" / f"{NOME}.svg",
        ]
    existiam = [a for a in alvos if a.exists()]
    for alvo in existiam:
        alvo.unlink()
    return "Atalho removido." if existiam else "Não havia atalho criado."


def _menu_iniciar(ambiente: Ambiente) -> Path:
    return ambiente.appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _ps(texto: str) -> str:
    """Literal de texto do PowerShell entre aspas simples (aspas internas são dobradas)."""
    return "'" + texto.replace("'", "''") + "'"
