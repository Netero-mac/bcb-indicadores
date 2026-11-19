# bcb-indicadores

[![CI](https://github.com/Netero-mac/bcb-indicadores/actions/workflows/ci.yml/badge.svg)](https://github.com/Netero-mac/bcb-indicadores/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Ruff](https://img.shields.io/badge/lint-ruff-261230)

Ferramenta de linha de comando que coleta indicadores econômicos da
[API SGS do Banco Central do Brasil](https://dadosabertos.bcb.gov.br/), guarda
os dados em SQLite e mostra relatórios no terminal, numa interface no navegador
ou num [painel publicado no GitHub Pages](https://netero-mac.github.io/bcb-indicadores/).

| Série            | Código SGS | Apelido na CLI | Unidade | Periodicidade |
| ---------------- | ---------: | -------------- | ------- | ------------- |
| Selic meta       |        432 | `selic`        | % a.a.  | diária        |
| IPCA mensal      |        433 | `ipca`         | % a.m.  | mensal        |
| Dólar PTAX venda |          1 | `dolar`        | R$      | diária        |
| CDI diário       |         12 | `cdi`          | % a.d.  | diária        |
| IGP-M mensal     |        189 | `igpm`         | % a.m.  | mensal        |
| Euro PTAX venda  |      21620 | `euro`         | R$      | diária        |
| Poupança         |        195 | `poupanca`     | % a.m.  | diária        |
| IBC-Br dessaz.   |      24364 | `ibcbr`        | índice  | mensal        |

Além dessas, o projeto calcula três **indicadores derivados** a partir das séries gravadas:

| Indicador      | Apelido na CLI | Como é calculado                                           |
| -------------- | -------------- | ---------------------------------------------------------- |
| IPCA 12 meses  | `ipca12m`      | IPCA dos 12 últimos meses, composto                        |
| IGP-M 12 meses | `igpm12m`      | IGP-M dos 12 últimos meses, composto (reajuste de aluguel) |
| Juro real      | `juroreal`     | Selic descontada do IPCA 12 meses (equação de Fisher)      |

**Destaques**

- Coleta **idempotente e incremental**: rodar `coletar` de novo só busca o que é novo e nunca duplica linhas.
- Respeita o limite da API de **10 anos por consulta** em séries diárias, dividindo o período em janelas.
- Cliente HTTP com **timeout e retentativas** com espera crescente (backoff).
- Valores tratados com `Decimal`, sem erros de arredondamento de ponto flutuante.
- **Interface web** com cartões, gráficos interativos, comparação de séries, simulador de
  rendimento e CSV por período; roda no seu computador ou como **site estático no GitHub Pages**,
  atualizado sozinho todo dia.
- **Coleta agendada** (`agendar`) e **atalho no menu de aplicativos** (`atalho`), para usar sem terminal.
- Uma única dependência externa (`requests`); banco com `sqlite3` da biblioteca padrão.
- **164 testes** com pytest, que rodam **sem internet**, usando respostas reais gravadas da API.
- CI no GitHub Actions: ruff + pytest no Linux e no Windows a cada push.

## Instalação

Requer **Python 3.12** ou mais recente e Git.

### Linux / macOS

```bash
git clone https://github.com/Netero-mac/bcb-indicadores.git
cd bcb-indicadores
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # sem os extras de desenvolvimento: pip install -e .
```

### Windows (PowerShell)

```powershell
git clone https://github.com/Netero-mac/bcb-indicadores.git
cd bcb-indicadores
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

> Se o PowerShell bloquear a ativação do ambiente virtual, rode uma vez
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. No Prompt de Comando
> (cmd), use `.venv\Scripts\activate.bat`.

Depois da instalação, o comando `bcb-indicadores` fica disponível enquanto o
ambiente virtual estiver ativo. Também é possível usar `python -m bcb_indicadores`.

## Uso

```text
bcb-indicadores [--banco ARQUIVO] [-v] COMANDO

  coletar   [--series SÉRIE ...] [--desde DD/MM/AAAA] [--ate DD/MM/AAAA]
  ultimos   SÉRIE [--n 10]
  resumo
  exportar  SÉRIE --csv ARQUIVO [--desde DD/MM/AAAA] [--ate DD/MM/AAAA]
  web       [--porta 8000] [--nao-abrir]
  site      --saida PASTA
  agendar   [--hora HH:MM] [--remover]
  atalho    [--remover]
```

A série pode ser informada pelo apelido (`selic`) ou pelo código (`432`). Em `ultimos`
e `exportar` também valem os derivados (`ipca12m`, `igpm12m`, `juroreal`). O banco
padrão é `bcb_indicadores.db` na pasta atual; dá para trocar com `--banco` ou com
a variável de ambiente `BCB_INDICADORES_DB`.

Os exemplos abaixo são saídas reais, de 19/11/2026.

### `coletar`: baixa e grava

Na primeira execução, baixa o histórico desde 01/01/2000:

```console
$ bcb-indicadores coletar
Coletando Selic meta (432)...
  01/01/2000 a 19/11/2026: 9820 recebidos, 9820 novos, 0 atualizados
Coletando IPCA mensal (433)...
  01/01/2000 a 19/11/2026: 322 recebidos, 322 novos, 0 atualizados
Coletando Dólar PTAX venda (1)...
WARNING bcb_indicadores.coleta: Série 1: resposta 304 vazia para 2000-01-01 a 2009-12-31; dividindo a janela.
  01/01/2000 a 19/11/2026: 6752 recebidos, 6752 novos, 0 atualizados
Coletando CDI diário (12)...
  01/01/2000 a 19/11/2026: 6751 recebidos, 6751 novos, 0 atualizados
Banco: bcb_indicadores.db
```

Nas execuções seguintes, continua de onde parou e não duplica nada:

```console
$ bcb-indicadores coletar
Coletando Selic meta (432)...
  19/11/2026 a 19/11/2026: 1 recebidos, 0 novos, 0 atualizados
Coletando IPCA mensal (433)...
  01/10/2026 a 19/11/2026: 1 recebidos, 0 novos, 0 atualizados
Coletando Dólar PTAX venda (1)...
  19/11/2026 a 19/11/2026: 0 recebidos, 0 novos, 0 atualizados
Coletando CDI diário (12)...
  18/11/2026 a 19/11/2026: 1 recebidos, 0 novos, 0 atualizados
Banco: bcb_indicadores.db
```

Também é possível escolher séries e período:

```console
$ bcb-indicadores coletar --series selic ipca --desde 01/01/2020 --ate 31/12/2024
```

### `ultimos`: últimas observações de uma série

```console
$ bcb-indicadores ultimos ipca --n 6
IPCA mensal (código 433) — em % a.m.

Data        Valor
----------  -----
01/05/2026   0,58
01/06/2026   0,16
01/07/2026   0,07
01/08/2026  -0,32
01/09/2026   0,82
01/10/2026   0,14
```

### `resumo`: visão geral das séries

```console
$ bcb-indicadores resumo
Série                   Unidade   Data          Último           Var. 12m  Média 12m
----------------------  --------  ----------  --------  -----------------  ---------
Selic meta              % a.a.    19/11/2026     13,75         -1,25 p.p.      14,47
IPCA mensal             % a.m.    01/10/2026      0,14         +0,05 p.p.       0,38
Dólar PTAX venda        R$        19/11/2026    5,8889  +0,5543 (+10,39%)     5,2035
CDI diário              % a.d.    18/11/2026  0,050788     -0,004343 p.p.   0,053285
IGP-M mensal            % a.m.    01/10/2026      1,47         +1,83 p.p.       0,43
Euro PTAX venda         R$        19/11/2026    6,4659   +0,3073 (+4,99%)     6,0105
Poupança                % a.m.    18/11/2026    0,6701       -0,0036 p.p.     0,6619
IBC-Br dessazonalizado  índice    01/09/2026    109,78     +0,94 (+0,86%)     110,14
IPCA 12 meses           % em 12m  01/10/2026      4,63         -0,05 p.p.       4,40
IGP-M 12 meses          % em 12m  01/10/2026      5,25         +4,32 p.p.       1,07
Juro real               % a.a.    19/11/2026      8,72         -1,37 p.p.       9,55
```

Como cada coluna é calculada (sempre em relação à data da última observação, D):

- **Último**: valor mais recente gravado.
- **Var. 12m**: último valor menos o valor vigente em D − 12 meses. Se essa data
  cair em fim de semana ou feriado, usa o dia útil anterior. Para taxas, a
  diferença é em pontos percentuais (p.p.); para o dólar, em reais e em %.
- **Média 12m**: média simples das observações no intervalo (D − 12 meses, D].

E os indicadores derivados:

- **IPCA 12 meses**: inflação composta dos 12 últimos meses, ∏(1 + IPCA/100) − 1.
  É composta, não somada: doze meses de 1% dão 12,68%, não 12%. Se faltar algum
  mês no meio, a janela não é calculada.
- **Juro real**: Selic meta descontada do IPCA 12 meses pela equação de Fisher,
  (1 + Selic/100) / (1 + IPCA12m/100) − 1, mais correta que a subtração simples
  (13,75% e 4,63% dão 8,72%, não 9,12%). É um juro real *ex-post*: compara a taxa
  de hoje com a inflação que já aconteceu. No histórico, cada mês usa a Selic
  vigente no fim do mês; o último ponto usa a Selic mais recente.

### `exportar`: série para CSV

```console
$ bcb-indicadores exportar dolar --csv dolar.csv
6752 linhas de Dólar PTAX venda exportadas para dolar.csv
$ head -3 dolar.csv
data,valor
2000-01-03,1.8011
2000-01-04,1.8337
```

Com `--desde` e `--ate`, exporta só um período:

```console
$ bcb-indicadores exportar ipca12m --csv ipca12m.csv --desde 01/01/2026
10 linhas de IPCA 12 meses exportadas para ipca12m.csv
```

### `web`: interface no navegador

Para ver os dados sem usar o terminal:

```console
$ bcb-indicadores web
Interface disponível em http://127.0.0.1:8000/
Pressione Ctrl+C para encerrar.
```

O navegador abre sozinho com:

- **abas** para ver cada área separadamente (Juros, Inflação, Câmbio e atividade, Simulador);
  a aba fica no endereço (`#inflacao`), então dá para compartilhar o link direto;
- **cartões** com último valor, variação de 12 meses e um minigráfico dos últimos 12 meses
  (os mesmos números do `resumo`), e uma faixa de destaques no topo;
- **painel de detalhe** com o valor em destaque e o resumo do período: início, fim,
  variação, mínimo, máximo e média;
- **gráfico** do indicador escolhido, com períodos de 1, 5 ou 10 anos, o histórico todo ou
  datas escolhidas (de/até), e data e valor ao passar o mouse;
- **comparação** com outro indicador no mesmo gráfico, com um eixo para cada um (ex.: Selic × IPCA 12 meses);
- tabela com as **últimas observações** e download em CSV do **período escolhido** ou da série completa;
- **simulador**: quanto rendeu um valor aplicado a um percentual do CDI entre duas datas,
  comparado com a poupança e com a inflação (ganho real). Valores brutos, sem IR;
- botão **Atualizar dados**, que faz a mesma coisa que o `coletar` (com o banco vazio, baixa o histórico desde 2000).

O servidor usa só a biblioteca padrão (`http.server`), escuta apenas em `127.0.0.1`
(não fica acessível para outros computadores da rede) e funciona sem internet para
consultar o que já está no banco. O gráfico é desenhado em SVG, sem bibliotecas
externas. Se a porta 8000 estiver ocupada, use `--porta 8080`; se a própria interface
já estiver aberta nessa porta, o comando só abre o navegador.

### `agendar` e `atalho`: usar sem terminal

```console
$ bcb-indicadores agendar
Coleta agendada todos os dias às 19:00 (timer do systemd bcb-indicadores-coleta.timer). Se o computador estiver desligado no horário, ela roda quando ele ligar.
Histórico: journalctl --user -u bcb-indicadores-coleta

$ bcb-indicadores atalho
Atalho criado: procure por "Indicadores BCB" no menu de aplicativos.
```

- `agendar` usa um timer do systemd do usuário no Linux (não pede senha de administrador),
  o crontab em outros sistemas Unix e o Agendador de Tarefas no Windows. Troque o horário
  com `--hora 08:30` e desfaça com `--remover`.
- `atalho` cria a entrada "Indicadores BCB" no menu de aplicativos (Linux) ou no Menu Iniciar
  (Windows), que abre a interface sem janela de terminal. Desfaça com `--remover`.
- Os dois gravam o caminho absoluto do banco e do Python do ambiente virtual atual, então
  continuam funcionando de qualquer pasta.

### `site`: painel online no GitHub Pages

`bcb-indicadores site --saida _site` gera a mesma interface como site estático: um
`index.html` e os dados em `dados/*.json` e `dados/*.csv`, sem o botão de coleta e sem
o caminho do banco local.

O workflow `.github/workflows/pages.yml` faz isso sozinho no GitHub Actions, todo dia às
19h30 (horário de Brasília), quando a interface muda na `main` ou manualmente pela aba
**Actions → Publicar painel → Run workflow**. O banco fica no cache do Actions, então
cada execução só baixa o que é novo. Se o banco estiver vazio, a publicação é cancelada,
para nunca trocar um painel com dados por um vazio.

Para ativar num fork: **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### Erros amigáveis

```console
$ bcb-indicadores ultimos libra
usage: bcb-indicadores ultimos [-h] [--n N] SÉRIE
bcb-indicadores ultimos: error: argument SÉRIE: Série desconhecida: 'libra'. Opções: selic (432), ipca (433), dolar (1), cdi (12), igpm (189), euro (21620), poupanca (195), ibcbr (24364); derivados: ipca12m, igpm12m, juroreal
```

## Testes e qualidade

```bash
pytest               # 164 testes, sem acesso à internet
ruff check .         # lint
ruff format --check .
```

Os testes não dependem da rede: as respostas da API em `tests/fixtures/` foram
gravadas da API real (inclusive os casos de erro), e o cliente HTTP é simulado
com `unittest.mock` ou substituído por um cliente falso. Eles cobrem:

- **Parse**: datas `dd/mm/aaaa`, valores com ponto decimal, valores negativos, registros inválidos;
  respostas 404 (sem dados), 406 (janela > 10 anos), 304, 503, JSON inválido e falha de rede.
- **Idempotência**: gravar o mesmo lote duas vezes, lotes sobrepostos, valores revisados pelo BCB,
  coleta incremental e rollback em caso de erro.
- **Cálculos do resumo**: variação e média de 12 meses, referência em fim de semana,
  29 de fevereiro, histórico menor que 12 meses, divisão por zero.
- **Janelas**: cobertura completa do período sem buracos nem sobreposição.
- **CLI** de ponta a ponta, com banco temporário.
- **Indicadores derivados**: juros compostos, janela de 12 meses na virada do ano, meses faltando,
  Selic do fim do mês, ano bissexto e equação de Fisher.
- **Interface web**: todas as rotas com um servidor real numa porta livre, CSV, coleta pelo botão,
  bloqueio de coletas simultâneas e de requisições de outros sites, site estático.
- **Agendamento e atalho**: arquivos do systemd, crontab (sem apagar outras linhas), `schtasks`
  e atalho do Windows, com caminhos com espaço, sempre com um executor falso, sem mexer no sistema.

## Decisões técnicas

- **Datas em ISO 8601 no banco.** A API usa `dd/mm/aaaa`, mas nesse formato a ordem
  alfabética não é a cronológica (`"02/01/2026" < "31/12/2025"`). Gravando como
  `AAAA-MM-DD`, `ORDER BY` e `MAX(data)` funcionam direto no SQL. A conversão
  acontece só nas bordas: ao ler da API e ao exibir.
- **`Decimal` em vez de `float`.** A API devolve valores como texto (`"5.1570"`).
  Converter para `Decimal` preserva as casas publicadas e evita erros como
  `0.1 + 0.2 != 0.3`. No SQLite, os valores são gravados como `TEXT`.
- **Idempotência pela chave primária.** `PRIMARY KEY (codigo, data)` com
  `INSERT ... ON CONFLICT DO UPDATE ... WHERE valor <> excluded.valor`: dado
  repetido é ignorado, e valor revisado pelo BCB é atualizado.
- **Coleta incremental.** Cada série recomeça na última data já gravada. O último dia
  é baixado de novo de propósito: não gera duplicata e captura eventuais revisões.
- **Janelas de 10 anos.** Testei na API: `01/01/2016 a 31/12/2025` é aceito e
  `01/01/2016 a 02/01/2026` é recusado (HTTP 406). Por isso cada janela vai de
  `início` até `início + 10 anos − 1 dia`.
- **Tratamento de um comportamento real da API.** Durante o desenvolvimento, a
  consulta do dólar de 01/01/2000 a 31/12/2009 passou a responder sempre
  `HTTP 304 Not Modified` sem corpo, sem que nenhum cabeçalho de cache tivesse sido
  enviado, enquanto períodos menores funcionavam. A coleta trata esse caso
  dividindo a janela ao meio (até 4 vezes). Outros erros não provocam divisão,
  para não multiplicar requisições quando a API está fora do ar.
- **Retentativas na camada HTTP.** `urllib3.Retry` repete automaticamente erros
  temporários (429, 500, 502, 503, 504 e falhas de conexão) com backoff exponencial
  e respeita o cabeçalho `Retry-After`. O `404` de "período sem dados" não é repetido:
  vira uma lista vazia. Toda requisição tem timeout (5 s para conectar, 30 s para ler).
- **Commit por série.** Se uma série falhar, as demais continuam, e o que já foi
  baixado fica salvo. O comando termina com código de saída 1 para sinalizar a falha.
- **Injeção de dependência para testar.** `main()` e `coletar_serie()` recebem o
  cliente como parâmetro (tipado por um `Protocol`), então os testes usam um cliente
  falso sem precisar de mock global.
- **CSV no formato "universal".** Separador vírgula, ponto decimal e data ISO,
  fácil de ler em pandas, R, bancos de dados e planilhas. Os relatórios no terminal,
  por outro lado, usam o padrão brasileiro (`5,1149`, `19/11/2026`).
- **Sem bibliotecas de tabela.** A formatação das tabelas no terminal é feita à mão
  (`formatacao.py`), mantendo `requests` como única dependência.

## Estrutura de pastas

```text
bcb-indicadores/
├── .github/workflows/
│   ├── ci.yml                  # ruff + pytest (Linux e Windows) a cada push
│   └── pages.yml               # coleta diária e publicação no GitHub Pages
├── src/bcb_indicadores/
│   ├── __init__.py
│   ├── __main__.py             # permite `python -m bcb_indicadores`
│   ├── series.py               # catálogo das séries (código, apelido, unidade)
│   ├── cliente.py              # cliente da API SGS: HTTP, timeout, retentativas e parse
│   ├── banco.py                # SQLite: schema, gravação idempotente e consultas
│   ├── coleta.py               # janelas de 10 anos e coleta incremental
│   ├── relatorios.py           # cálculos do resumo e indicadores derivados (funções puras)
│   ├── formatacao.py           # números/datas no padrão brasileiro e tabelas
│   ├── web.py                  # servidor da interface web (http.server)
│   ├── web_index.html          # página da interface (HTML, CSS e JS sem dependências)
│   ├── sistema.py              # agendamento da coleta e atalho no menu (Linux/Windows)
│   ├── icone.svg               # ícone do atalho
│   └── datas.py                # aritmética de datas (ex.: 29/02 menos 1 ano)
├── tests/
│   ├── fixtures/               # respostas reais gravadas da API
│   ├── conftest.py             # cliente falso e utilitários
│   ├── test_cliente.py
│   ├── test_banco.py
│   ├── test_coleta.py
│   ├── test_relatorios.py
│   ├── test_derivados.py
│   ├── test_formatacao.py
│   ├── test_cli.py
│   ├── test_web.py
│   └── test_sistema.py
├── pyproject.toml              # metadados, dependências, pytest e ruff
└── README.md
```

## Possíveis melhorias

- Opção `--formato excel` no CSV (ponto e vírgula e vírgula decimal).
- Exportação para Parquet.
- Atalho e agendamento com "recuperar execução perdida" também no macOS (launchd).

## Fonte dos dados

Banco Central do Brasil, Sistema Gerenciador de Séries Temporais (SGS).
Dados públicos, acessados pela API em `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados`.
