"""Catálogo das séries do SGS acompanhadas pelo projeto."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Serie:
    """Metadados de uma série temporal do SGS."""

    codigo: int
    apelido: str  # nome curto usado na linha de comando
    nome: str
    unidade: str
    periodicidade: str  # "diária" ou "mensal"
    casas: int  # casas decimais publicadas pelo BCB, usadas na exibição

    @property
    def eh_taxa(self) -> bool:
        """Séries em % (taxas) variam em pontos percentuais; o dólar, em reais."""
        return self.unidade.startswith("%")


SERIES: tuple[Serie, ...] = (
    Serie(432, "selic", "Selic meta", "% a.a.", "diária", 2),
    Serie(433, "ipca", "IPCA mensal", "% a.m.", "mensal", 2),
    Serie(1, "dolar", "Dólar PTAX venda", "R$", "diária", 4),
    Serie(12, "cdi", "CDI diário", "% a.d.", "diária", 6),
)


def buscar_serie(identificador: str | int) -> Serie:
    """Encontra uma série pelo apelido (ex.: "selic") ou pelo código (ex.: 432).

    Levanta ``KeyError`` com mensagem amigável se a série não existir.
    """
    texto = str(identificador).strip().lower()
    for serie in SERIES:
        if texto in (serie.apelido, str(serie.codigo)):
            return serie
    opcoes = ", ".join(f"{s.apelido} ({s.codigo})" for s in SERIES)
    raise KeyError(f"Série desconhecida: {identificador!r}. Opções: {opcoes}")
