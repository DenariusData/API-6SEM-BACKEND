"""
Valida o filtro de permissão com os usuários demo (S2-26): faz a MESMA pergunta
como Engenharia e como Qualidade, compara as fontes e grava um relatório para
a Sprint Review (um arquivo Markdown).

Precisa do Ollama no ar (bge-m3 e o LLM) e de documentos reais disponíveis em
áreas diferentes. Não inventa nada: usa o banco e o Ollama de quem roda.

Uso:
    python manage.py seed_taxonomia
    python manage.py seed_usuarios_demo
    python manage.py validar_permissao_demo --pergunta "Como condicionar corpos de prova?"

Termina com erro (código 1) se algum critério reprovar. Resultado
inconclusivo (acervo sem documento do sistema nas fontes) sai com aviso.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core_api.services import validacao_permissao as validacao
from core_api.services.rag.pergunta import ErroIA


class Command(BaseCommand):
    help = "Faz a mesma pergunta com dois usuários demo e compara as fontes."

    def add_arguments(self, parser):
        parser.add_argument("--pergunta", required=True)
        parser.add_argument("--idioma", default="pt-BR", choices=["pt-BR", "en"])
        parser.add_argument(
            "--usuarios",
            nargs=2,
            metavar=("EMAIL_A", "EMAIL_B"),
            default=list(validacao.EMAILS_DEMO),
            help="Padrão: engenharia@akaer.com.br qualidade@akaer.com.br",
        )
        parser.add_argument(
            "--saida",
            default=str(Path(settings.BASE_DIR) / "docs" / "validacoes"),
            help="Pasta do relatório (padrão: docs/validacoes).",
        )

    def handle(self, *args, **opcoes):
        pergunta = opcoes["pergunta"].strip()
        if not pergunta:
            raise CommandError("Informe a pergunta.")
        try:
            usuarios = validacao.resolver_usuarios(tuple(opcoes["usuarios"]))
            dados = validacao.coletar(pergunta, opcoes["idioma"], usuarios)
        except validacao.UsuarioDemoInexistente as e:
            raise CommandError(validacao.ascii_seguro(e)) from e
        except ErroIA as e:
            raise CommandError(validacao.ascii_seguro(f"IA indisponivel: {e}")) from e

        caminho = validacao.gravar(dados, opcoes["saida"])
        self._resumo(dados, caminho)

        if validacao.reprovado(dados):
            raise CommandError("Validacao REPROVADA: veja o relatorio.")
        if validacao.inconclusivo(dados):
            self.stderr.write(
                self.style.WARNING(
                    "Validacao INCONCLUSIVA: o acervo nao permitiu demonstrar "
                    "a diferenca entre os perfis (veja o relatorio)."
                )
            )

    def _resumo(self, dados, caminho):
        escrever = self.stdout.write
        escrever(validacao.ascii_seguro(f"Pergunta: {dados['pergunta']}"))
        for u in dados["usuarios"]:
            sistema = [f for f in u["fontes"] if f["documento_id"]]
            publicas = len(u["fontes"]) - len(sistema)
            escrever(
                validacao.ascii_seguro(
                    f"- {u['nome']}: {len(sistema)} fonte(s) do sistema, "
                    f"{publicas} da base publica, "
                    f"{len(u['bloqueados'])} trecho(s) bloqueado(s) pelo filtro"
                )
            )
        for c in dados["criterios"]:
            estilo = {
                True: self.style.SUCCESS,
                False: self.style.ERROR,
                None: self.style.WARNING,
            }[c["ok"]]
            escrever(
                estilo(
                    validacao.ascii_seguro(
                        f"[{validacao.selo(c['ok'])}] {c['criterio']}"
                    )
                )
            )
        escrever(validacao.ascii_seguro(f"Relatorio gravado em: {caminho}"))
