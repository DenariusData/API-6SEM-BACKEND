"""
Valida o formato da mensagem de commit.

Formato esperado (código AKA-XX sempre obrigatório, sem exceção):
    <tipo>: AKA-<numero> <descrição>

Exemplos válidos:
    feat: AKA-12 Add document upload endpoint
    fix: AKA-28 corrige expiração de token JWT

Exemplos INVÁLIDOS (rejeitados de propósito):
    fix(auth): corrige expiração de token JWT   # sem AKA-XX, tem escopo
    chore: Add PR title check workflow          # sem AKA-XX

Sem o AKA-XX, o Jira não linka o commit/PR com a tarefa e o histórico
de desenvolvimento se perde no board.
"""

import re
import sys

TIPOS_VALIDOS = (
    "feat",
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

# tipo com código AKA-XX obrigatório logo após os dois-pontos
PADRAO_COM_TASK = re.compile(
    rf"^(?P<tipo>{'|'.join(TIPOS_VALIDOS)}): AKA-\d+ (?P<descricao>.{{3,}})$"
)


def main():
    if len(sys.argv) < 2:
        print("Erro interno: caminho do arquivo de commit message não informado.")
        return 1

    caminho_msg = sys.argv[1]
    with open(caminho_msg, "r", encoding="utf-8") as f:
        primeira_linha = f.readline().strip()

    # ignora commits de merge, que têm formato próprio do Git
    if primeira_linha.startswith("Merge "):
        return 0

    if not PADRAO_COM_TASK.match(primeira_linha):
        print("\n[ERRO] Mensagem de commit fora do padrão.\n")
        print(f'   Recebido: "{primeira_linha}"\n')
        print("   Formato esperado: <tipo>: AKA-<numero> <descrição>")
        print(f"   Tipos aceitos: {', '.join(TIPOS_VALIDOS)}\n")
        print("   O código AKA-XX é obrigatório para o Jira linkar a tarefa.\n")
        print("   Exemplo válido:")
        print("     feat: AKA-12 Add document upload endpoint\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
