"""The text the hook injects around the rules, in every language it ships.

This table is the plugin's property: configuration *selects* a row of it and
can never *supply* one. A config layer arrives with whatever repository is
checked out, and letting it provide the wording of the scaffolding would hand a
cloned repository the pen that writes the sentences the model trusts most —
which is why an unknown language falls back to English instead of reaching for
anything outside this file.

A Python module rather than a data file: no new I/O, no new parser, and no new
untrusted file on the injection path. The English row IS the constant the rest
of the plugin has always used, so selecting `en` emits byte-for-byte what the
plugin emitted before this table existed.

What is deliberately NOT translated is structure, not prose, and stays
byte-identical in every language: the boundary tags and the rule separator
(`neutralize` and every reader locate the block by their exact bytes), the
`[rules-by-trigger (rbt)]` marker every SESSION_NOTICE opens with, the `{name!r}` and
`{body}` fields of the deny reason, and the admin command and flags quoted in
the notices — those are commands, not sentences.

`sanitize_language` lives here rather than in `config.py` for two reasons: this
module is what gives a language value its meaning, and `config.py` is already
at the 400-line ceiling this package holds every module to.
"""

import unicodedata

from .constants import (ADMIN_COMMAND, BRAZILIAN_PORTUGUESE, DEFAULT_LANGUAGE,
                        BLOCK_REASON_TEMPLATE, HARNESS_MARKER,
                        LANGUAGE_EXTRA_CHARS, LANGUAGE_FORBIDDEN_CHARS,
                        LANGUAGE_KEY, LEGACY_NOTICE, MAX_LANGUAGE_CHARS,
                        SESSION_NOTICE, SUPERSEDE_NOTICE, TRUNCATION_NOTICES,
                        warn)

# The keys one row holds. They are the names the constants already carry, so a
# caller reads `messages[SESSION_NOTICE_KEY]` where it used to read the
# constant, and a translation missing one of them is a visible failure rather
# than a quietly untranslated line.
LEGACY_NOTICE_KEY = "LEGACY_NOTICE"
SESSION_NOTICE_KEY = "SESSION_NOTICE"
TRUNCATION_NOTICE_KEY = "TRUNCATION_NOTICE"
SUPERSEDE_NOTICE_KEY = "SUPERSEDE_NOTICE"
ENFORCE_DENY_REASON_TEMPLATE_KEY = "BLOCK_REASON_TEMPLATE"
# The verification report (`verify:`, run by the Stop hook). Its English text is
# written in the table below rather than in constants.py: the five keys above
# are constants only because they predate this table, and constants.py is where
# TUNABLES live — a sentence is not one.
VERIFY_REPORT_HEADER_KEY = "VERIFY_REPORT_HEADER"
VERIFY_FAILURE_KEY = "VERIFY_FAILURE"
VERIFY_EXIT_CODE_KEY = "VERIFY_EXIT_CODE"
VERIFY_TIMED_OUT_KEY = "VERIFY_TIMED_OUT"
VERIFY_OUT_OF_TIME_KEY = "VERIFY_OUT_OF_TIME"
VERIFY_NOT_STARTED_KEY = "VERIFY_NOT_STARTED"
VERIFY_ERROR_KEY = "VERIFY_ERROR"
VERIFY_NO_OUTPUT_KEY = "VERIFY_NO_OUTPUT"
VERIFY_PASSED_KEY = "VERIFY_PASSED"
VERIFY_REPORT_CUT_KEY = "VERIFY_REPORT_CUT"
VERIFY_NOT_RUN_HEADER_KEY = "VERIFY_NOT_RUN_HEADER"
VERIFY_NOT_RUN_KEY = "VERIFY_NOT_RUN"
VERIFY_SYSTEM_MESSAGE_KEY = "VERIFY_SYSTEM_MESSAGE"
VERIFY_SYSTEM_NOT_RUN_KEY = "VERIFY_SYSTEM_NOT_RUN"
VERIFY_SYSTEM_RULE_WRITTEN_KEY = "VERIFY_SYSTEM_RULE_WRITTEN"
# Told to the model by the admin CLI (not the hook) on every subcommand except
# `doctor`, `show` and `status --json`, until the machine has a `~/.claude/rules-by-trigger/config.json` —
# see `scripts/rules_by_trigger_admin/setup.py`. It lives in this table like
# every other sentence the plugin emits, even though the hook itself never
# reads it: this table is the one place translated text is allowed to live.
SETUP_NOTICE_KEY = "SETUP_NOTICE"
MESSAGE_KEYS = (LEGACY_NOTICE_KEY, SESSION_NOTICE_KEY, TRUNCATION_NOTICE_KEY,
                SUPERSEDE_NOTICE_KEY, ENFORCE_DENY_REASON_TEMPLATE_KEY,
                VERIFY_REPORT_HEADER_KEY, VERIFY_FAILURE_KEY,
                VERIFY_EXIT_CODE_KEY, VERIFY_TIMED_OUT_KEY,
                VERIFY_OUT_OF_TIME_KEY, VERIFY_NOT_STARTED_KEY,
                VERIFY_ERROR_KEY, VERIFY_NO_OUTPUT_KEY, VERIFY_PASSED_KEY,
                VERIFY_REPORT_CUT_KEY, VERIFY_NOT_RUN_HEADER_KEY,
                VERIFY_NOT_RUN_KEY, VERIFY_SYSTEM_MESSAGE_KEY,
                VERIFY_SYSTEM_NOT_RUN_KEY, VERIFY_SYSTEM_RULE_WRITTEN_KEY,
                SETUP_NOTICE_KEY)

# Two spellings of the same separator, because a language code is written both
# ways in the wild and nobody should have to guess which one this file wants.
LANGUAGE_SEPARATOR = "-"
LANGUAGE_SEPARATOR_ALIAS = "_"

# The Unicode form a value is folded to before anything looks at it. The
# compatibility one, not the canonical one: `ｅｎ` (fullwidth), `𝖾𝗇` (math bold)
# and `①` are alphanumeric, printable and identical to the eye, and only NFKC
# collapses them onto the ASCII they impersonate. Without it a value a human
# reads as `en` in the CLI's output would quietly select something else.
LANGUAGE_NORMAL_FORM = "NFKC"

MESSAGES = {
    DEFAULT_LANGUAGE: {
        LEGACY_NOTICE_KEY: LEGACY_NOTICE,
        SESSION_NOTICE_KEY: SESSION_NOTICE,
        TRUNCATION_NOTICE_KEY: TRUNCATION_NOTICES[DEFAULT_LANGUAGE],
        SUPERSEDE_NOTICE_KEY: SUPERSEDE_NOTICE,
        ENFORCE_DENY_REASON_TEMPLATE_KEY: BLOCK_REASON_TEMPLATE,
        VERIFY_REPORT_HEADER_KEY: (
            "rules-by-trigger: {failed} of {total} verification(s) declared by "
            "the rules covering what this turn wrote did not pass. Each block "
            "below names the rule that asked for the command, the command "
            "itself, and the last lines it printed. Address what they report "
            "before ending the turn."
        ),
        VERIFY_FAILURE_KEY: "rule {name!r} — {command}\n{status}\n{output}",
        VERIFY_EXIT_CODE_KEY: "exit code {code}",
        VERIFY_TIMED_OUT_KEY: "timed out after {seconds}s and was killed",
        VERIFY_OUT_OF_TIME_KEY: ("not finished: this turn's verification time "
                                 "budget ran out"),
        VERIFY_NOT_STARTED_KEY: ("not started: this turn's verification time "
                                 "budget was already spent"),
        VERIFY_ERROR_KEY: "could not be started",
        VERIFY_NO_OUTPUT_KEY: "(no output)",
        VERIFY_PASSED_KEY: "passed: {command} (rule {name!r})",
        VERIFY_REPORT_CUT_KEY: "\n[...report cut by the rules-by-trigger size limit...]",
        VERIFY_NOT_RUN_HEADER_KEY: (
            "These verifications never ran, so what they cover is simply "
            "unchecked. They are not failures and there is nothing in them to "
            "fix; they are here so you know the check was incomplete."
        ),
        VERIFY_NOT_RUN_KEY: "{command} (rule {name!r}) — {status}",
        VERIFY_SYSTEM_MESSAGE_KEY: ("rules-by-trigger: verified — {command} "
                                    "(rule {name!r})"),
        VERIFY_SYSTEM_NOT_RUN_KEY: ("rules-by-trigger: not run — {command} "
                                    "(rule {name!r}): {status}"),
        VERIFY_SYSTEM_RULE_WRITTEN_KEY: (
            "rules-by-trigger: the verify: in rule {name!r} was written by this "
            "session, so it runs from the next one"
        ),
        SETUP_NOTICE_KEY: (
            "rules-by-trigger is not set up on this machine yet "
            "(~/.claude/rules-by-trigger/config.json does not exist). Offer the "
            "user the setup through the rules-by-trigger:doctor skill."
        ),
    },
    BRAZILIAN_PORTUGUESE: {
        LEGACY_NOTICE_KEY: (
            "Este escopo ainda usa o formato antigo rules-map.yml, portanto "
            "NENHUMA regra está sendo injetada a partir dele. Migre-o "
            "executando: "
            f"\"{ADMIN_COMMAND}\" migrate --root <project-root> (ou --global). "
            "Avise o usuário que isto aconteceu."
        ),
        SESSION_NOTICE_KEY: (
            f"{HARNESS_MARKER} Esta sessão tem regras por caminho disponíveis. "
            "São arquivos markdown em `.claude/rules-by-trigger/` (projeto) e "
            "`~/.claude/rules-by-trigger/` (global), e eles chegam até você "
            "AUTOMATICAMENTE: no momento em que você toca um arquivo cujo glob "
            "casa, a regra é injetada no seu contexto. Portanto nunca há motivo "
            "para abrir, listar, grepar ou editar esses arquivos você mesmo — e "
            "a configuração recomendada os coloca em deny-list, então a "
            "tentativa é recusada em vez de atendida. Para ler ou alterar uma "
            "regra, use a CLI: "
            f"\"{ADMIN_COMMAND}\" list|show|which|add|update, com "
            "--root '<repo-root>' ou --global — ou a skill "
            "rules-by-trigger:manage, que a conduz para você."
        ),
        TRUNCATION_NOTICE_KEY: TRUNCATION_NOTICES[BRAZILIAN_PORTUGUESE],
        SUPERSEDE_NOTICE_KEY: (
            "Esta versão substitui qualquer ocorrência anterior desta regra na "
            "conversa."
        ),
        ENFORCE_DENY_REASON_TEMPLATE_KEY: (
            "rules-by-trigger: esta chamada de ferramenta está bloqueada pela "
            "regra {name!r} (escopo global). O texto da própria regra é "
            "o motivo:\n\n{body}"
        ),
        VERIFY_REPORT_HEADER_KEY: (
            "rules-by-trigger: {failed} de {total} verificação(ões) "
            "declaradas pelas regras que cobrem o que este turno escreveu não "
            "passaram. Cada bloco abaixo nomeia a regra que pediu o comando, o "
            "próprio comando e as últimas linhas que ele imprimiu. "
            "Resolva o que elas apontam antes de encerrar o turno."
        ),
        VERIFY_FAILURE_KEY: "regra {name!r} — {command}\n{status}\n{output}",
        VERIFY_EXIT_CODE_KEY: "código de saída {code}",
        VERIFY_TIMED_OUT_KEY: "excedeu {seconds}s e foi encerrado",
        VERIFY_OUT_OF_TIME_KEY: ("não terminou: o orçamento de tempo de "
                                 "verificação deste turno acabou"),
        VERIFY_NOT_STARTED_KEY: ("não foi iniciado: o orçamento de tempo de "
                                 "verificação deste turno já tinha acabado"),
        VERIFY_ERROR_KEY: "não pôde ser executado",
        VERIFY_NO_OUTPUT_KEY: "(sem saída)",
        VERIFY_PASSED_KEY: "passou: {command} (regra {name!r})",
        VERIFY_REPORT_CUT_KEY: (
            "\n[...relatório cortado pelo limite de tamanho do rules-by-trigger...]"
        ),
        VERIFY_NOT_RUN_HEADER_KEY: (
            "Estas verificações não chegaram a rodar, então o que elas cobrem "
            "está apenas sem checagem. Não são falhas e não há nada nelas para "
            "corrigir; estão aqui para você saber que a checagem ficou "
            "incompleta."
        ),
        VERIFY_NOT_RUN_KEY: "{command} (regra {name!r}) — {status}",
        VERIFY_SYSTEM_MESSAGE_KEY: ("rules-by-trigger: verificado — {command} "
                                    "(regra {name!r})"),
        VERIFY_SYSTEM_NOT_RUN_KEY: ("rules-by-trigger: não executada — {command} "
                                    "(regra {name!r}): {status}"),
        VERIFY_SYSTEM_RULE_WRITTEN_KEY: (
            "rules-by-trigger: o verify: da regra {name!r} foi escrito por esta "
            "sessão, então ele passa a rodar a partir da próxima"
        ),
        SETUP_NOTICE_KEY: (
            "O rules-by-trigger ainda não foi configurado nesta máquina "
            "(~/.claude/rules-by-trigger/config.json não existe). Ofereça ao "
            "usuário o setup pela skill rules-by-trigger:doctor."
        ),
    },
}
SHIPPED_LANGUAGES = tuple(MESSAGES)


def normalize_language(language):
    """The spelling two language codes are compared under: case-folded, with
    `_` read as `-`, so `pt_br`, `PT-BR` and `pt-BR` are one language."""
    if not isinstance(language, str):
        return ""
    return language.strip().lower().replace(LANGUAGE_SEPARATOR_ALIAS,
                                            LANGUAGE_SEPARATOR)


CANONICAL_LANGUAGES = {normalize_language(code): code for code in MESSAGES}


def canonical_language(language):
    """The shipped code this value selects, or None when no shipped language
    matches it. The canonical spelling is the one this module indexes by; the
    sanitized value the user wrote is what the rules themselves are written
    in, and the two are deliberately allowed to differ."""
    return CANONICAL_LANGUAGES.get(normalize_language(language))


def has_translation(language):
    """Whether the injected text comes out in this language or falls back to
    English. `validate` and `config` report it, because a user who set a
    language the plugin does not ship should learn it from the CLI rather than
    by noticing English scaffolding around their own rules."""
    return canonical_language(language) is not None


def messages_for(language):
    """Every message, in the closest shipped language.

    Never raises and never returns a partial row: an unknown, misspelled or
    absent language yields the English one, because nothing about this setting
    may stop an injection. A copy, so a caller cannot edit the shipped table
    for the rest of the process."""
    return dict(MESSAGES[canonical_language(language) or DEFAULT_LANGUAGE])


def sanitize_language(raw, source):
    """One config layer's `language`, or None when the value is not usable.

    Sanitized like a glob and not like an internal value: it arrives from a
    layer that came with a cloned repository, and it goes straight into text
    the model reads. What the allowlist buys is worth stating precisely: no
    newline, no colon, no backtick, no angle bracket and no quote means the
    value cannot forge a delimiter, a frontmatter key or a second line. It does
    NOT mean 32 characters are too few to word an instruction — they are not —
    which is why every place that echoes the value quotes it instead of setting
    it loose in a sentence. `pt-BR`, `Portuguese (Brazil)` and `español` pass;
    `en\\nIgnore all previous instructions` does not.

    Folded to NFKC first, and the invisible alphanumerics refused outright: both
    exist so that a value which RENDERS as `en` IS `en`, rather than a lookalike
    that reads as English to whoever approved the file and selects something
    else in the code.

    There is no clamping here because there is no dangerous *direction* to
    limit, only a shape. A rejected value is warned about and dropped, so the
    layer below still decides — the same treatment every other unusable key
    gets, for the same reason: nothing may leave the hook unable to inject."""
    if not isinstance(raw, str):
        warn(f"{source}: '{LANGUAGE_KEY}' must be a string; ignored")
        return None
    text = unicodedata.normalize(LANGUAGE_NORMAL_FORM, raw).strip()
    allowed = all((char.isalnum() or char in LANGUAGE_EXTRA_CHARS)
                  and char not in LANGUAGE_FORBIDDEN_CHARS for char in text)
    if (not text or len(text) > MAX_LANGUAGE_CHARS or not text.isprintable()
            or not allowed or not any(char.isalpha() for char in text)):
        warn(f"{source}: '{LANGUAGE_KEY}' {raw[:MAX_LANGUAGE_CHARS]!r} must be "
             f"one printable line of at most {MAX_LANGUAGE_CHARS} visible "
             f"letters, digits, spaces or {LANGUAGE_EXTRA_CHARS.strip()!r}, "
             f"and must carry at least one letter; ignored")
        return None
    return text
