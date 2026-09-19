"""
Valida o formato do nome da branch atual.

Formato esperado (obrigatório incluir o código da task do Jira):
    feature/AKA-<numero>-<descricao-curta-em-kebab-case>

Exemplos válidos:
    feature/AKA-12-nome-curto-da-tarefa
    feature/AKA-92-requeriments-track-back

Sem o código AKA-XX, o Jira não linka o commit/PR com a tarefa e o
histórico de desenvolvimento se perde no board.
"""

import re
import subprocess
import sys

# branches que nunca precisam seguir o padrão de feature branch
BRANCHES_ISENTAS = (
    "main",
    "master",
    "develop",
    "HEAD",
    "chore/add-pr-title-check",  # legado, pré-padrão AKA-XX — não renomear
    "docs/update-readme",  # idem
)

PADRAO = re.compile(r"^feature\/AKA-\d+-[a-z0-9]+(-[a-z0-9]+)*$")


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
        print("\n❌ Nome de branch fora do padrão.\n")
        print(f'   Branch atual: "{branch}"\n')
        print("   Formato esperado: feature/AKA-<numero>-descricao-curta")
        print("   O código AKA-XX é obrigatório para o Jira linkar a tarefa.\n")
        print("   Exemplos válidos:")
        print("     feature/AKA-12-nome-curto-da-tarefa")
        print("     feature/AKA-92-requeriments-track-back\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
