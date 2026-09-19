"""
Valida o formato do nome da branch atual.

Formato esperado (obrigatório incluir o código da task do Jira):
    <tipo>/AKA-<numero>-<descricao-curta>

Tipos aceitos são os mesmos usados no padrão de commit (ver
validate_commit_msg.py) — mantidos idênticos de propósito, para que
o tipo escolhido na branch já anuncie o tipo esperado no commit.

O código AKA-XX é o único trecho que o Jira realmente usa para linkar
a branch à task — a capitalização do restante do nome é livre (pode
manter o título da task como o Jira gera automaticamente ao criar a
branch pelo card).

Exemplos válidos:
    feat/AKA-12-nome-curto-da-tarefa
    fix/AKA-45-corrige-token-expirado
    chore/AKA-70-Atualiza-Dependencias
    feature/AKA-92-Requeriments-Track-Back

Sem o código AKA-XX, o Jira não linka o commit/PR com a tarefa e o
histórico de desenvolvimento se perde no board.
"""

import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIPOS_VALIDOS = (
    "feature",  # alias aceito além do padrão de commit, já em uso no repo
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "chore",
    "ci",
    "build",
    "revert",
)

# branches que nunca precisam seguir o padrão de feature branch
BRANCHES_ISENTAS = (
    "main",
    "master",
    "develop",
    "HEAD",
    "chore/add-pr-title-check",  # legado, pré-padrão AKA-XX — não renomear
    "docs/update-readme",  # idem
)

PADRAO = re.compile(
    rf"^(?P<tipo>{'|'.join(TIPOS_VALIDOS)})\/AKA-\d+-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$"
)


def obter_branch_atual():
    resultado = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return resultado.stdout.strip()


def main():
    branch = obter_branch_atual()

    if not branch or branch in BRANCHES_ISENTAS:
        return 0

    if not PADRAO.match(branch):
        print("\n[ERRO] Nome de branch fora do padrao.\n")
        print(f'   Branch atual: "{branch}"\n')
        print("   Formato esperado: <tipo>/AKA-<numero>-descricao-curta")
        print(f"   Tipos aceitos: {', '.join(TIPOS_VALIDOS)}\n")
        print("   O codigo AKA-XX e obrigatorio para o Jira linkar a tarefa.\n")
        print("   Exemplos validos:")
        print("     feat/AKA-12-nome-curto-da-tarefa")
        print("     fix/AKA-45-corrige-token-expirado\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
