"""
Configuração do gunicorn na VM (servidor de aplicação do Django).

Usado pelo serviço deploy/akavision-backend.service:
    gunicorn api.wsgi:application --config deploy/gunicorn.conf.py

Escolhas (VM com 4 vCPU e 8 GB, Ollama na mesma máquina):
- workers=3 processos x threads=4 = 12 requisições ao mesmo tempo. A maior
  parte do tempo de uma requisição é espera (Postgres, Ollama), e thread
  espera sem gastar CPU; poucos processos deixam CPU e RAM para o Ollama.
- timeout=620: a pergunta para a IA pode levar minutos na CPU; o pergunta.py
  espera o Ollama até 600 s, então o gunicorn espera um pouco mais.
- max_requests: reinicia cada worker depois de ~1000 requisições, para
  devolver memória que a biblioteca de PDF possa ter acumulado.
"""

bind = "127.0.0.1:8000"  # só o nginx (na mesma VM) acessa; a rede não
workers = 3
threads = 4
worker_class = "gthread"
timeout = 620
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
worker_tmp_dir = "/dev/shm"

# Logs no journal do systemd: journalctl -u akavision-backend
accesslog = "-"
errorlog = "-"
