"""Testes do agendamento e do atalho, sem tocar no sistema de verdade.

Os comandos externos (systemctl, crontab, schtasks, powershell) são registrados
por um executor falso, e a pasta "home" é temporária.
"""

import subprocess
from pathlib import Path

import pytest

from bcb_indicadores import sistema
from bcb_indicadores.cli import main
from bcb_indicadores.sistema import Ambiente, ErroSistema


class Executor:
    """Substitui subprocess.run: guarda os comandos e simula o crontab."""

    def __init__(self, crontab: str | None = None, falhar: str | None = None):
        self.comandos: list[list[str]] = []
        self.crontab = crontab
        self.falhar = falhar

    def __call__(self, comando, entrada=None):
        self.comandos.append(list(comando))
        if self.falhar and comando[0] == self.falhar:
            return subprocess.CompletedProcess(comando, 1, "", "permissão negada")
        if comando[:2] == ["crontab", "-l"]:
            if self.crontab is None:
                return subprocess.CompletedProcess(comando, 1, "", "no crontab for usuario")
            return subprocess.CompletedProcess(comando, 0, self.crontab, "")
        if comando[:2] == ["crontab", "-"]:
            self.crontab = entrada
        return subprocess.CompletedProcess(comando, 0, "", "")


def ambiente(tmp_path, sistema_op="Linux", systemd=True, executor=None):
    return Ambiente(
        sistema=sistema_op,
        home=tmp_path / "home",
        python="/opt/py 3/bin/python",  # com espaço, para testar as aspas
        executar=executor or Executor(),
        tem_systemd=systemd,
        appdata=tmp_path / "appdata",
    )


BANCO = Path("dados") / "meu banco.db"  # relativo: o programa grava o caminho absoluto


def absoluto(caminho: Path) -> str:
    """Como o caminho aparece nos arquivos gerados (entre aspas, por ter espaço)."""
    return sistema._aspas([str(caminho.resolve())])


# --- agendamento: systemd ----------------------------------------------------


def test_agendar_com_systemd(tmp_path):
    amb = ambiente(tmp_path)
    mensagem = sistema.agendar(BANCO, "07:30", amb)

    pasta = amb.home / ".config" / "systemd" / "user"
    servico = (pasta / "bcb-indicadores-coleta.service").read_text()
    timer = (pasta / "bcb-indicadores-coleta.timer").read_text()
    assert 'ExecStart="/opt/py 3/bin/python" -m bcb_indicadores --banco' in servico
    assert f"{absoluto(BANCO)} coletar" in servico
    assert "OnCalendar=*-*-* 07:30:00" in timer
    assert "Persistent=true" in timer
    assert amb.executar.comandos == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "bcb-indicadores-coleta.timer"],
    ]
    assert "07:30" in mensagem


def test_desagendar_com_systemd(tmp_path):
    amb = ambiente(tmp_path)
    assert sistema.desagendar(amb) == "Não havia coleta agendada."
    sistema.agendar(BANCO, "19:00", amb)
    assert sistema.desagendar(amb) == "Agendamento removido."
    assert not list((amb.home / ".config" / "systemd" / "user").iterdir())
    assert ["systemctl", "--user", "disable", "--now", "bcb-indicadores-coleta.timer"] in (
        amb.executar.comandos
    )


@pytest.mark.parametrize("hora", ["25:00", "7h", "12:60", ""])
def test_hora_invalida(tmp_path, hora):
    with pytest.raises(ErroSistema, match="Hora inválida"):
        sistema.agendar(BANCO, hora, ambiente(tmp_path))


def test_falha_do_systemctl_vira_mensagem(tmp_path):
    amb = ambiente(tmp_path, executor=Executor(falhar="systemctl"))
    with pytest.raises(ErroSistema, match="permissão negada"):
        sistema.agendar(BANCO, "19:00", amb)


# --- agendamento: crontab ----------------------------------------------------


def test_agendar_com_crontab_preserva_outras_linhas(tmp_path):
    executor = Executor(crontab="0 3 * * * backup.sh\n")
    amb = ambiente(tmp_path, systemd=False, executor=executor)

    sistema.agendar(BANCO, "19:05", amb)
    sistema.agendar(BANCO, "20:00", amb)  # reagendar troca a linha, não duplica

    linhas = executor.crontab.splitlines()
    assert linhas[0] == "0 3 * * * backup.sh"
    assert len(linhas) == 2
    assert linhas[1].startswith("0 20 * * * ")
    assert linhas[1].endswith("# bcb-indicadores: coleta diária")
    assert f">> {absoluto(BANCO.with_suffix('.log'))} 2>&1" in linhas[1]

    assert sistema.desagendar(amb) == "Agendamento removido do crontab."
    assert executor.crontab == "0 3 * * * backup.sh\n"
    assert sistema.desagendar(amb) == "Não havia coleta agendada."


def test_agendar_com_crontab_vazio(tmp_path):
    executor = Executor(crontab=None)
    sistema.agendar(BANCO, "19:00", ambiente(tmp_path, systemd=False, executor=executor))
    assert executor.crontab.count("\n") == 1


# --- agendamento: Windows ----------------------------------------------------


def test_agendar_no_windows(tmp_path):
    amb = ambiente(tmp_path, sistema_op="Windows", systemd=False)
    sistema.agendar(BANCO, "08:00", amb)
    (comando,) = amb.executar.comandos
    assert comando[:5] == ["schtasks", "/Create", "/F", "/SC", "DAILY"]
    assert comando[comando.index("/ST") + 1] == "08:00"
    assert comando[comando.index("/TN") + 1] == "bcb-indicadores coleta"
    assert comando[comando.index("/TR") + 1].endswith("coletar")

    sistema.desagendar(amb)
    assert amb.executar.comandos[-1] == [
        "schtasks", "/Delete", "/F", "/TN", "bcb-indicadores coleta",
    ]  # fmt: skip


# --- atalho ------------------------------------------------------------------


def test_atalho_no_linux(tmp_path):
    amb = ambiente(tmp_path)
    sistema.criar_atalho(BANCO, amb)

    desktop = amb.home / ".local" / "share" / "applications" / "bcb-indicadores.desktop"
    conteudo = desktop.read_text()
    assert "Name=Indicadores BCB" in conteudo
    assert "Terminal=false" in conteudo
    assert 'Exec="/opt/py 3/bin/python" -m bcb_indicadores --banco' in conteudo
    icone = amb.home / ".local" / "share" / "icons" / "bcb-indicadores.svg"
    assert f"Icon={icone}" in conteudo
    assert icone.read_text().startswith("<svg")

    assert sistema.remover_atalho(amb) == "Atalho removido."
    assert not desktop.exists()
    assert not icone.exists()
    assert sistema.remover_atalho(amb) == "Não havia atalho criado."


def test_atalho_no_windows(tmp_path):
    amb = ambiente(tmp_path, sistema_op="Windows", systemd=False)
    mensagem = sistema.criar_atalho(BANCO, amb)
    (comando,) = amb.executar.comandos
    assert comando[0] == "powershell"
    script = comando[-1]
    assert "CreateShortcut(" in script
    assert "Indicadores BCB.lnk" in script
    assert "'/opt/py 3/bin/python'" in script
    assert "Menu Iniciar" in mensagem


def test_aspas_do_powershell_sao_escapadas():
    assert sistema._ps("C:\\Users\\D'Ávila") == "'C:\\Users\\D''Ávila'"


def test_atalho_em_sistema_nao_suportado(tmp_path):
    with pytest.raises(ErroSistema, match="só para Linux e Windows"):
        sistema.criar_atalho(BANCO, ambiente(tmp_path, sistema_op="Darwin", systemd=False))


def test_aspas_no_estilo_systemd():
    assert sistema._aspas(["/bin/py", "a b", 'x"y', "$HOME"]) == ('/bin/py "a b" "x\\"y" "$$HOME"')


# --- CLI ---------------------------------------------------------------------


def test_cli_agendar_mostra_erro_amigavel(tmp_path, capsys, monkeypatch):
    def falhar(*_a, **_k):
        raise ErroSistema("systemctl indisponível")

    monkeypatch.setattr(sistema, "agendar", falhar)
    assert main(["--banco", str(tmp_path / "x.db"), "agendar"]) == 1
    assert "systemctl indisponível" in capsys.readouterr().err


def test_cli_atalho_remover(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sistema, "remover_atalho", lambda: "Atalho removido.")
    assert main(["atalho", "--remover"]) == 0
    assert "Atalho removido." in capsys.readouterr().out
