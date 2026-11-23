"""Termos iniciais (AKA-109): 2 obrigatórios e 1 opcional.

O texto do Termo de Uso é o mesmo que a tela mostrava antes (versão 2.1). O
administrador cadastra versões novas pela tela de Termos.
"""

from django.db import migrations

TERMOS = [
    {
        "titulo": "Termo de Uso",
        "versao": "2.1",
        "obrigatorio": True,
        "texto": (
            "1. Confidencialidade e segurança (ITAR/EAR). O usuário declara-se ciente de "
            "que os arquivos, projetos e documentos técnicos gerenciados pela Akaer "
            "Engenharia S.A. estão protegidos por protocolos de segurança e controle de "
            "exportação de tecnologia aeroespacial (ITAR e EAR). Qualquer vazamento "
            "sujeita o responsável às sanções cabíveis.\n\n"
            "2. Uso do sistema. O acesso é pessoal e intransferível. Os documentos só "
            "podem ser consultados para as atividades de trabalho, sem cópia, impressão "
            "ou envio para fora do sistema.\n\n"
            "3. Auditoria e rastreabilidade. As ações no sistema (acessos, envios, "
            "aprovações e perguntas à IA) são registradas de forma que não pode ser "
            "alterada, para fins de auditoria e conformidade com a ANAC e o Comando da "
            "Aeronáutica."
        ),
    },
    {
        "titulo": "Aviso de Privacidade",
        "versao": "1.0",
        "obrigatorio": True,
        "texto": (
            "Para o funcionamento do AkaVision tratamos nome, e-mail corporativo, "
            "matrícula, perfil de acesso e o registro das suas ações no sistema, com "
            "base na execução do contrato de trabalho e no legítimo interesse da "
            "empresa em proteger informações técnicas (LGPD, art. 7º).\n\n"
            "Os dados pessoais ficam em um banco separado, com acesso restrito. Os "
            "registros de auditoria guardam apenas um identificador, sem nome, e-mail "
            "ou IP.\n\n"
            "Você pode, a qualquer momento, consultar e corrigir seus dados, baixar uma "
            "cópia deles e pedir a exclusão da conta, pela tela Meus dados (LGPD, art. 18)."
        ),
    },
    {
        "titulo": "Comunicações por e-mail",
        "versao": "1.0",
        "obrigatorio": False,
        "texto": (
            "Aceito receber por e-mail avisos sobre novidades e atualizações do "
            "AkaVision. Este consentimento é opcional e pode ser revogado a qualquer "
            "momento em Meus dados, sem afetar o acesso ao sistema."
        ),
    },
]


def criar(apps, schema_editor):
    Termo = apps.get_model("core_api", "Termo")
    for dados in TERMOS:
        if not Termo.objects.filter(titulo=dados["titulo"], ativo=True).exists():
            Termo.objects.create(ativo=True, **dados)


def remover(apps, schema_editor):
    Termo = apps.get_model("core_api", "Termo")
    AceiteTermo = apps.get_model("core_api", "AceiteTermo")
    titulos = [t["titulo"] for t in TERMOS]
    AceiteTermo.objects.filter(termo__titulo__in=titulos).delete()
    Termo.objects.filter(titulo__in=titulos).delete()


class Migration(migrations.Migration):
    dependencies = [("core_api", "0014_termos_consentimento")]

    operations = [migrations.RunPython(criar, remover)]
