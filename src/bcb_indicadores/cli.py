"""Interface de linha de comando (argparse).

Uso:
    bcb-indicadores coletar [--series selic ipca] [--desde dd/mm/aaaa] [--ate dd/mm/aaaa]
    bcb-indicadores ultimos <série> [--n 10]
    bcb-indicadores resumo
    bcb-indicadores exportar <série> --csv arquivo.csv
    bcb-indicadores web [--porta 8000] [--nao-abrir]
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from bcb_indicadores import __version__, banco
from bcb_indicadores.cliente import ClienteSGS, ErroAPI, parse_data
from bcb_indicadores.coleta import Cliente, coletar_serie
from bcb_indicadores.formatacao import TRACO, data_br, numero_br, tabela
from bcb_indicadores.relatorios import ResumoSerie, resumo_do_banco
from bcb_indicadores.series import SERIES, Serie, buscar_serie

BANCO_PADRAO = "bcb_indicadores.db"
VARIAVEL_BANCO = "BCB_INDICADORES_DB"

# Códigos de saída
OK, ERRO = 0, 1


# ---------------------------------------------------------------------------
# Conversores usados pelo argparse (erros viram mensagens amigáveis)
# ---------------------------------------------------------------------------


def _tipo_serie(texto: str) -> Serie:
    try:
        return buscar_serie(texto)
    except KeyError as erro:
        raise argparse.ArgumentTypeError(erro.args[0]) from erro


def _tipo_data(texto: str) -> date:
    try:
        return parse_data(texto)
    except ValueError as erro:
        raise argparse.ArgumentTypeError(f"data inválida {texto!r}; use dd/mm/aaaa") from erro


def _tipo_positivo(texto: str) -> int:
    try:
        numero = int(texto)
    except ValueError:
        numero = 0
    if numero < 1:
        raise argparse.ArgumentTypeError(f"esperado um inteiro positivo, recebido {texto!r}")
    return numero


def _tipo_porta(texto: str) -> int:
    try:
        porta = int(texto)
    except ValueError:
        porta = -1
    if not 0 <= porta <= 65535:
        raise argparse.ArgumentTypeError(f"porta inválida {texto!r}; use um número de 0 a 65535")
    return porta


def criar_parser() -> argparse.ArgumentParser:
    apelidos = ", ".join(f"{s.apelido} ({s.codigo})" for s in SERIES)
    parser = argparse.ArgumentParser(
        prog="bcb-indicadores",
        description="Indicadores econômicos do Banco Central (API SGS) no terminal.",
        epilog=f"Séries disponíveis: {apelidos}.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--banco",
        type=Path,
        default=Path(os.environ.get(VARIAVEL_BANCO, BANCO_PADRAO)),
        help=f"arquivo SQLite (padrão: ${VARIAVEL_BANCO} ou {BANCO_PADRAO})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="mostra detalhes das requisições"
    )
    sub = parser.add_subparsers(dest="comando", required=True, metavar="COMANDO")

    p = sub.add_parser("coletar", help="baixa as séries da API e grava no banco")
    p.add_argument(
        "--series",
        nargs="+",
        type=_tipo_serie,
        default=list(SERIES),
        metavar="SÉRIE",
        help="séries a coletar (padrão: todas)",
    )
    p.add_argument(
        "--desde",
        type=_tipo_data,
        metavar="DD/MM/AAAA",
        help="data inicial (padrão: continua de onde parou ou 01/01/2000)",
    )
    p.add_argument("--ate", type=_tipo_data, metavar="DD/MM/AAAA", help="data final (padrão: hoje)")
    p.set_defaults(funcao=cmd_coletar)

    p = sub.add_parser("ultimos", help="mostra as últimas observações de uma série")
    p.add_argument("serie", type=_tipo_serie, metavar="SÉRIE", help="apelido ou código da série")
    p.add_argument("--n", type=_tipo_positivo, default=10, help="quantidade (padrão: 10)")
    p.set_defaults(funcao=cmd_ultimos)

    p = sub.add_parser("resumo", help="último valor, variação e média de 12 meses de cada série")
    p.set_defaults(funcao=cmd_resumo)

    p = sub.add_parser("exportar", help="exporta uma série para CSV")
    p.add_argument("serie", type=_tipo_serie, metavar="SÉRIE", help="apelido ou código da série")
    p.add_argument("--csv", type=Path, required=True, metavar="ARQUIVO", help="arquivo de saída")
    p.set_defaults(funcao=cmd_exportar)

    p = sub.add_parser("web", help="abre uma interface no navegador para ver os dados")
    p.add_argument(
        "--porta", type=_tipo_porta, default=8000, help="porta local do servidor (padrão: 8000)"
    )
    p.add_argument("--nao-abrir", action="store_true", help="não abre o navegador automaticamente")
    p.set_defaults(funcao=cmd_web)

    return parser


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def cmd_coletar(args: argparse.Namespace, cliente: Cliente | None) -> int:
    cliente = cliente or ClienteSGS()
    falhas = 0
    with banco.abrir_banco(args.banco) as conexao:
        for serie in args.series:
            print(f"Coletando {serie.nome} ({serie.codigo})...", flush=True)
            try:
                r = coletar_serie(conexao, cliente, serie, desde=args.desde, ate=args.ate)
            except (ErroAPI, ValueError) as erro:
                falhas += 1
                print(f"  ERRO: {erro}", file=sys.stderr)
                continue
            print(
                f"  {data_br(r.inicio)} a {data_br(r.fim)}: {r.recebidos} recebidos, "
                f"{r.inseridos} novos, {r.atualizados} atualizados"
            )
    print(f"Banco: {args.banco}")
    return ERRO if falhas else OK


def cmd_ultimos(args: argparse.Namespace, _cliente: Cliente | None) -> int:
    serie: Serie = args.serie
    with banco.abrir_banco(args.banco) as conexao:
        observacoes = banco.ultimos(conexao, serie.codigo, args.n)
    if not observacoes:
        return _sem_dados(serie)

    print(f"{serie.nome} (código {serie.codigo}) — em {serie.unidade}\n")
    linhas = [[data_br(o.data), numero_br(o.valor, serie.casas)] for o in observacoes]
    print(tabela(["Data", "Valor"], linhas, direita={1}))
    return OK


def cmd_resumo(args: argparse.Namespace, _cliente: Cliente | None) -> int:
    with banco.abrir_banco(args.banco) as conexao:
        resumos = [resumo_do_banco(conexao, serie) for serie in SERIES]
    if all(r.ultima is None for r in resumos):
        print("Banco vazio. Rode primeiro: bcb-indicadores coletar", file=sys.stderr)
        return ERRO

    cabecalho = ["Série", "Unidade", "Data", "Último", "Var. 12m", "Média 12m"]
    print(tabela(cabecalho, [_linha_resumo(r) for r in resumos], direita={3, 4, 5}))
    return OK


def _linha_resumo(r: ResumoSerie) -> list[str]:
    s = r.serie
    if r.ultima is None:
        return [s.nome, s.unidade, TRACO, TRACO, TRACO, TRACO]

    if r.variacao_12m is None:
        variacao = TRACO
    elif s.eh_taxa:
        # Para taxas, a diferença está em pontos percentuais (p.p.).
        variacao = f"{numero_br(r.variacao_12m, s.casas, sinal=True)} p.p."
    else:
        relativa = numero_br(r.variacao_12m_pct, 2, sinal=True)
        variacao = f"{numero_br(r.variacao_12m, s.casas, sinal=True)} ({relativa}%)"

    return [
        s.nome,
        s.unidade,
        data_br(r.ultima.data),
        numero_br(r.ultima.valor, s.casas),
        variacao,
        numero_br(r.media_12m, s.casas),
    ]


def cmd_exportar(args: argparse.Namespace, _cliente: Cliente | None) -> int:
    serie: Serie = args.serie
    with banco.abrir_banco(args.banco) as conexao:
        observacoes = banco.listar(conexao, serie.codigo)
    if not observacoes:
        return _sem_dados(serie)

    # CSV "universal": separador vírgula, ponto decimal e data ISO, fácil de
    # ler no pandas, R, planilhas e bancos de dados.
    with args.csv.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(["data", "valor"])
        escritor.writerows((o.data.isoformat(), str(o.valor)) for o in observacoes)

    print(f"{len(observacoes)} linhas de {serie.nome} exportadas para {args.csv}")
    return OK


def cmd_web(args: argparse.Namespace, cliente: Cliente | None) -> int:
    from bcb_indicadores import web  # importado só aqui: os outros comandos não precisam

    try:
        web.servir(args.banco, args.porta, abrir_navegador=not args.nao_abrir, cliente=cliente)
    except OSError as erro:
        print(f"Não foi possível iniciar o servidor na porta {args.porta}: {erro}", file=sys.stderr)
        print("Tente outra porta, por exemplo: bcb-indicadores web --porta 8080", file=sys.stderr)
        return ERRO
    return OK


def _sem_dados(serie: Serie) -> int:
    print(
        f"Nenhum dado de {serie.nome} no banco. "
        f"Rode primeiro: bcb-indicadores coletar --series {serie.apelido}",
        file=sys.stderr,
    )
    return ERRO


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, cliente: Cliente | None = None) -> int:
    """Executa a CLI. ``cliente`` permite injetar um cliente falso nos testes."""
    args = criar_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.funcao(args, cliente)
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
