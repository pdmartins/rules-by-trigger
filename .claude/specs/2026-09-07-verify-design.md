# `verify:`: uma verificação por path, rodada no fim do turno — design

Data: 2026-09-07
Fechado em sessão de grilling (continuação de 04/09). Cada decisão abaixo foi
posta ao usuário como pergunta com opções e escolhida por ele; o que não foi
perguntado está marcado como premissa.

---

## 1. Motivação

O conteúdo de uma regra compra aderência a convenções. A alavanca com mais
efeito sobre correctness é um loop de verificação que o agente não consegue
pular: teste ou lint rodando sozinho e a falha voltando para ele. O Claude Code
tem hooks nativos para isso, mas sem seleção por glob. `verify:` reaproveita o
mapeamento path → regra que o plugin já tem e acrescenta o verbo: "quando
escrever em `src/api/**`, rode isto antes de encerrar".

O vocabulário (turn, write, verification, path-scoped policy) está em
`CONTEXT.md`.

## 2. Decisões

| # | Decisão | Escolha |
|---|---|---|
| Q1 | Hook que dispara | Só `Stop`. Dos 33 eventos, apenas `PreToolUse`, `PostToolUse` e `PostToolBatch` sabem de arquivo e devolvem texto ao modelo; `Stop` não sabe de arquivo, mas é o único que fecha o turno, e o plugin lhe dá a lista de escritas pelo estado. `PostToolUse` fica como extensão aditiva futura. |
| Q2 | Gatilho | Um arquivo casando o glob da regra foi **escrito** neste turno (Write/Edit/MultiEdit/NotebookEdit). Leitura não dispara. Edição via Bash é invisível, como já é para a injeção. |
| Q3 | Confiança | Projeto e global executam, sem portão. Hooks nativos em `.claude/settings.json` de projeto já executam após o trust do diretório; `verify:` não abre porta nova. Diferente de `block:`, que segue inerte em projeto (ver ADR). |
| Q4 | Falha | Segura o encerramento do turno (`decision: block`); o Claude recebe a saída e continua. |
| Q5 | Várias regras casam | União: cada comando distinto roda uma vez, global primeiro, depois projetos do mais externo ao mais interno. Todos rodam mesmo com falha anterior; falhas voltam juntas. |
| Q6 | Diretório | Regra de projeto: raiz do projeto dono da regra. Regra global: raiz do projeto do arquivo escrito (o mais interno com `.claude/`), ou o cwd da sessão. |
| Q7 | Forma da chave | `verify:` aceita string ou lista; cada item é um comando separado. |
| Q9 | README | Claim continua "aderência + tokens". `verify:` é o terceiro verbo, "roda a verificação que você declarou"; nenhuma promessa de correctness. |
| Q10 | Re-verificação | Roda de novo só se houve escrita nova casando desde a última verificação. Sem escrita nova, o hook sai e o turno encerra. `stop_hook_active` e o teto nativo de oito bloqueios são rede de segurança, não critério. |
| Q11 | Lista de escritas | `PreToolUse` anota o caminho no estado da sessão a cada write. Uma escrita negada por permissão ainda conta (falso positivo aceito). |
| Q12 | Relato ao Claude | Nome da regra, comando, código de saída e as últimas N linhas de stdout+stderr. O corpo da regra não vai: já foi injetado. |
| Q13 | Tempo | Orçamento fixo por comando (constante); o hook mata o comando e reporta como falha. Timeout folgado do `Stop` no `hooks.json`. Sem chave de frontmatter. |
| Q14 | `tool: read` + `verify:` | `validate` aponta o conflito. O hook ignora o filtro `tool:` para a verificação: o gatilho é escrita. |
| Q15 | Passou | Uma linha ao **usuário** via `systemMessage`; nada ao Claude. |
| Q16 | Estatísticas | Contar execuções e falhas por regra; `status` mostra. `improve` não muda nesta versão. |
| Q17 | Skill `manage` | Só escreve `verify:` quando o usuário pede. Documenta a chave e o critério (comando existe, é determinístico, roda rápido); não sugere. |

## 3. Premissas (decididas sem pergunta)

- `exclude:` vale para a verificação como para a injeção: mesmo matcher.
- Deduplicação por par comando + diretório: regra global tocando dois
  repositórios roda uma vez em cada.
- O comando roda pelo shell do sistema. Comando inexistente é falha reportada.
- Constantes: 120 s por comando, últimas 60 linhas de saída, `Stop` com
  timeout de 600 s no `hooks.json`.
- `SubagentStop` fora: verificação só no turno principal.
- A lista de escritas é limpa a cada verificação executada e no reset de
  estado (`SessionStart` compact/clear).
- CLI: `--verify` repetível em `add` e `update`. `validate` checa string ou
  lista de strings não vazias, e o conflito com `tool: read`.
- O hook `Stop` roda para todo usuário, com ou sem regra `verify:`; sem
  escrita registrada, sai antes de ler qualquer regra ou config.
- CHANGELOG sob `## Unreleased`; o próximo release é `minor`.

## 4. Fora desta versão

`PostToolUse`/`PostToolBatch` como gatilho opt-in, `verify_timeout:` por regra,
allowlist de comandos, `SubagentStop`, uso das estatísticas de verificação
pelo `improve`.
